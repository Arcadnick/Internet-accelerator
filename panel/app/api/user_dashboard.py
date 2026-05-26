from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Subscription, User
from app.schemas.subscription import SubscriptionPublic
from app.security import get_current_user

router = APIRouter(prefix="/api/me", tags=["dashboard"])


@router.get("/subscriptions", response_model=list[SubscriptionPublic])
async def my_subscriptions(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[SubscriptionPublic]:
    rows = list(
        (
            await db.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    base = str(request.base_url).rstrip("/")
    return [
        SubscriptionPublic(
            expires_at=s.expires_at,
            traffic_used_bytes=s.traffic_used_bytes,
            traffic_limit_bytes=s.traffic_limit_bytes,
            status=s.status,
            subscription_url=f"{base}/sub/{s.sub_token}",
        )
        for s in rows
    ]


@router.get("/subscriptions/{sub_id}/qr.png")
async def subscription_qr(
    sub_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    sub = await db.get(Subscription, sub_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    import io

    import qrcode
    from fastapi.responses import Response

    base = str(request.base_url).rstrip("/")
    url = f"{base}/sub/{sub.sub_token}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
