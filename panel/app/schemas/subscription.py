from datetime import datetime

from pydantic import BaseModel

from app.models.subscription import SubscriptionStatus


class SubscriptionCreate(BaseModel):
    user_id: int
    plan_id: int


class SubscriptionOut(BaseModel):
    id: int
    user_id: int
    plan_id: int | None
    xray_uuid: str
    xray_email: str
    sub_token: str
    expires_at: datetime | None
    traffic_used_bytes: int
    traffic_limit_bytes: int
    status: SubscriptionStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionPublic(BaseModel):
    """Returned to end users (no admin fields)."""

    expires_at: datetime | None
    traffic_used_bytes: int
    traffic_limit_bytes: int
    status: SubscriptionStatus
    subscription_url: str
