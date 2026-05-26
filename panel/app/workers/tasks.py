"""ARQ background worker.

Runs two cron jobs:
  * `collect_traffic_task` every minute: pulls per-user stats from Panel XRay
    and updates traffic counters.
  * `enforce_limits_task` every minute (offset 30s): expires subscriptions
    that hit the time/traffic limit and rewrites the XRay config to drop them.
"""

import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.db import SessionLocal
from app.services.billing import enforce_limits
from app.services.traffic_collector import collect_traffic

logger = logging.getLogger(__name__)


async def collect_traffic_task(ctx: dict) -> None:
    async with SessionLocal() as db:
        updated = await collect_traffic(db)
        if updated:
            logger.info("traffic_collector: updated %d subscription(s)", updated)


async def enforce_limits_task(ctx: dict) -> None:
    async with SessionLocal() as db:
        await enforce_limits(db)


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    redis_settings = _redis_settings()
    cron_jobs = [
        cron(collect_traffic_task, second={0}),
        cron(enforce_limits_task, second={30}),
    ]
    keep_result_forever = False
