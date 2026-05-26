from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    traffic_bytes: int = Field(ge=0)  # 0 = unlimited
    duration_days: int = Field(ge=1)
    price: Decimal = Decimal(0)


class PlanOut(BaseModel):
    id: int
    name: str
    traffic_bytes: int
    duration_days: int
    price: Decimal
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
