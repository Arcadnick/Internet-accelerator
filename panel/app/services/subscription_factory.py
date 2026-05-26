"""Creates Subscription rows with all generated identifiers (UUID, sub_token, email)."""

import secrets
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, Subscription, SubscriptionStatus, User


def _random_token() -> str:
    return secrets.token_urlsafe(32)


async def create_subscription_for(
    db: AsyncSession, user: User, plan: Plan
) -> Subscription:
    # duration_days == 0 means "never expires" (infinite subscription).
    expires_at = (
        None
        if plan.duration_days == 0
        else datetime.now(UTC) + timedelta(days=plan.duration_days)
    )
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        xray_uuid=str(uuid_mod.uuid4()),
        xray_email=f"u{user.id}-{secrets.token_hex(4)}@panel",
        sub_token=_random_token(),
        expires_at=expires_at,
        traffic_limit_bytes=plan.traffic_bytes,  # 0 means unlimited
        traffic_used_bytes=0,
        status=SubscriptionStatus.active,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub
