from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Plan, Subscription, User
from app.schemas.subscription import SubscriptionCreate, SubscriptionOut
from app.security import get_current_admin
from app.services.subscription_factory import create_subscription_for
from app.services.xray_local import rebuild_and_apply

router = APIRouter(prefix="/api/admin/subscriptions", tags=["admin:subscriptions"])


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> list[Subscription]:
    return list(
        (await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    )


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> Subscription:
    user = await db.get(User, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    plan = await db.get(Plan, body.plan_id)
    if plan is None or not plan.is_active:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    sub = await create_subscription_for(db, user, plan)
    await rebuild_and_apply(db)
    return sub


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    sub_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> None:
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    await db.delete(sub)
    await db.commit()
    await rebuild_and_apply(db)
