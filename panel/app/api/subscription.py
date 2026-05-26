from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Node, NodeStatus, Subscription, SubscriptionStatus
from app.services.subscription_builder import (
    build_singbox_subscription,
    build_v2ray_subscription,
)

router = APIRouter(tags=["subscription"])


@router.get("/sub/{token}", response_class=PlainTextResponse)
async def subscription(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    fmt: Annotated[str, Query(pattern="^(v2ray|singbox)$")] = "v2ray",
) -> Response:
    sub = (
        await db.execute(select(Subscription).where(Subscription.sub_token == token))
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Unknown subscription token")
    if sub.status != SubscriptionStatus.active:
        raise HTTPException(status_code=403, detail=f"Subscription {sub.status.value}")

    nodes = list(
        (
            await db.execute(
                select(Node).where(Node.status == NodeStatus.active).order_by(Node.id)
            )
        )
        .scalars()
        .all()
    )
    settings = get_settings()

    if fmt == "singbox":
        body = build_singbox_subscription(sub, nodes, settings)
        return Response(content=body, media_type="application/json")

    body = build_v2ray_subscription(sub, nodes, settings)
    return PlainTextResponse(
        content=body,
        headers={
            "Profile-Update-Interval": "12",
            "Profile-Title": f"VPN ({len(nodes)} locations)",
        },
    )
