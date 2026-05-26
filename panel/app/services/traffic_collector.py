"""Pulls per-user traffic stats from the Panel XRay and persists them.

Uses the `xray api statsquery` CLI subcommand against the Panel XRay's gRPC
API (typically host.docker.internal:10085 from inside the panel container).
Each invocation atomically reads-and-resets the counters, so we never
double-count even if a task firing overlaps the previous one.

Output of `xray api statsquery` looks like:
    user>>>u1-abcd@panel>>>traffic>>>uplink: 12345
    user>>>u1-abcd@panel>>>traffic>>>downlink: 67890
"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Subscription, TrafficLog

logger = logging.getLogger(__name__)

_STAT_LINE = re.compile(
    r"^user>>>(?P<email>[^>]+)>>>traffic>>>(?P<direction>uplink|downlink):\s*(?P<bytes>\d+)$"
)


async def _query_user_stats(api_addr: str) -> dict[str, dict[str, int]]:
    """Returns {email: {"up": bytes, "down": bytes}} from a single query+reset call."""
    proc = await asyncio.create_subprocess_exec(
        "xray",
        "api",
        "statsquery",
        "--server",
        api_addr,
        "--pattern",
        "user>>>",
        "--reset",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"xray api statsquery failed (rc={proc.returncode}): {stderr.decode().strip()}"
        )

    result: dict[str, dict[str, int]] = defaultdict(lambda: {"up": 0, "down": 0})
    for line in stdout.decode().splitlines():
        m = _STAT_LINE.match(line.strip())
        if not m:
            continue
        email = m.group("email")
        direction = "up" if m.group("direction") == "uplink" else "down"
        result[email][direction] = int(m.group("bytes"))
    return dict(result)


async def collect_traffic(db: AsyncSession) -> int:
    """Runs one collection cycle. Returns number of subscriptions updated."""
    settings = get_settings()
    try:
        stats = await _query_user_stats(settings.panel_xray_api_addr)
    except FileNotFoundError:
        logger.warning("xray CLI not available; skipping stats collection")
        return 0
    except RuntimeError as e:
        logger.warning("stats query failed: %s", e)
        return 0

    if not stats:
        return 0

    rows = list(
        (
            await db.execute(
                select(Subscription).where(Subscription.xray_email.in_(stats.keys()))
            )
        )
        .scalars()
        .all()
    )

    now = datetime.now(UTC)
    updated = 0
    for sub in rows:
        delta = stats.get(sub.xray_email)
        if not delta:
            continue
        up = delta["up"]
        down = delta["down"]
        if up == 0 and down == 0:
            continue
        sub.traffic_used_bytes += up + down
        sub.last_seen_at = now  # marks the subscription as currently online
        db.add(
            TrafficLog(
                subscription_id=sub.id,
                node_id=None,
                bytes_up=up,
                bytes_down=down,
            )
        )
        updated += 1

    await db.commit()
    return updated
