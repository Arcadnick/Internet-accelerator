from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Plan, User
from app.schemas.plan import PlanCreate, PlanOut
from app.security import get_current_admin

router = APIRouter(prefix="/api/admin/plans", tags=["admin:plans"])


@router.get("", response_model=list[PlanOut])
async def list_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> list[Plan]:
    return list((await db.execute(select(Plan).order_by(Plan.id))).scalars().all())


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> Plan:
    plan = Plan(
        name=body.name,
        traffic_bytes=body.traffic_bytes,
        duration_days=body.duration_days,
        price=body.price,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> None:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    await db.delete(plan)
    await db.commit()
