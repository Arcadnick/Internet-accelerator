import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    over_limit = "over_limit"
    disabled = "disabled"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="SET NULL")
    )

    # Used as both XRay client UUID and stats key
    xray_uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # XRay email field (used in StatsService pattern "user>>>{email}>>>traffic")
    xray_email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # URL token for subscription endpoint
    sub_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 0 = unlim

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.active,
    )
    # Bumped by traffic_collector whenever this user's counter increased; used to
    # show "online now" indicators in the admin UI.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")  # noqa: F821
