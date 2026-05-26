"""Expires subscriptions and rebuilds the XRay config when status changes.

Two conditions disable a subscription:
  * traffic_used_bytes >= traffic_limit_bytes (when limit > 0)
  * expires_at < now()

Disabled subs are removed from the XRay config on next rebuild — clients
lose connectivity immediately when xray restarts.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, SubscriptionStatus
from app.services.xray_local import rebuild_and_apply

logger = logging.getLogger(__name__)


async def enforce_limits(db: AsyncSession) -> int:
    """Returns number of subscriptions transitioned to expired/over_limit."""
    now = datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(Subscription).where(
                    Subscription.status == SubscriptionStatus.active,
                    or_(
                        Subscription.expires_at < now,
                        # over_limit: limit > 0 AND used >= limit
                        # SQL: traffic_limit_bytes > 0 AND traffic_used_bytes >= traffic_limit_bytes
                        (Subscription.traffic_limit_bytes > 0)
                        & (
                            Subscription.traffic_used_bytes
                            >= Subscription.traffic_limit_bytes
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return 0

    changed = 0
    for sub in rows:
        if sub.expires_at is not None and sub.expires_at < now:
            sub.status = SubscriptionStatus.expired
        elif (
            sub.traffic_limit_bytes > 0
            and sub.traffic_used_bytes >= sub.traffic_limit_bytes
        ):
            sub.status = SubscriptionStatus.over_limit
        changed += 1

    await db.commit()
    if changed:
        await rebuild_and_apply(db)
        logger.info("billing: disabled %d subscription(s)", changed)
    return changed
