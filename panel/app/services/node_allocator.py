"""Allocates panel-side resources (inbound port, reality short_id, tags) for new nodes."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Node


def random_short_id() -> str:
    # Reality short_id is hex, 0-8 bytes. 8 hex chars (4 bytes) is plenty.
    return secrets.token_hex(4)


def random_s2s_password() -> str:
    return secrets.token_urlsafe(32)


async def allocate_inbound_port(db: AsyncSession) -> int:
    settings = get_settings()
    used = set(
        (await db.execute(select(Node.panel_inbound_port))).scalars().all()
    )
    for port in range(settings.panel_inbound_port_start, settings.panel_inbound_port_end + 1):
        if port not in used:
            return port
    raise RuntimeError("No free panel inbound ports in configured range")


def make_tags(country_code: str, node_id_hint: int | None = None) -> tuple[str, str]:
    suffix = secrets.token_hex(2)
    cc = country_code.lower()
    return f"in-{cc}-{suffix}", f"out-{cc}-{suffix}"
