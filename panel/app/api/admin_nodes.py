from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Node, NodeStatus, User
from app.schemas.node import NodeCreate, NodeOut, NodeUpdate
from app.security import get_current_admin
from app.services.node_allocator import (
    allocate_inbound_port,
    make_tags,
    random_short_id,
)
from app.services.xray_local import rebuild_and_apply

router = APIRouter(prefix="/api/admin/nodes", tags=["admin:nodes"])


@router.get("", response_model=list[NodeOut])
async def list_nodes(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> list[Node]:
    return list((await db.execute(select(Node).order_by(Node.id))).scalars().all())


@router.post("", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    body: NodeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> Node:
    in_tag, out_tag = make_tags(body.country_code)
    port = await allocate_inbound_port(db)
    node = Node(
        country_code=body.country_code.upper(),
        label=body.label,
        host=body.host,
        ssh_port=body.ssh_port,
        s2s_password=body.s2s_password,
        s2s_sni=body.s2s_sni,
        s2s_allow_insecure=body.s2s_allow_insecure,
        panel_inbound_tag=in_tag,
        panel_outbound_tag=out_tag,
        panel_inbound_port=port,
        reality_short_id=random_short_id(),
        status=NodeStatus.active,  # manual add: assume node is already ready
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    await rebuild_and_apply(db)
    return node


@router.patch("/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: int,
    body: NodeUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> Node:
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(node, field, value)
    await db.commit()
    await db.refresh(node)
    await rebuild_and_apply(db)
    return node


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> None:
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    await db.delete(node)
    await db.commit()
    await rebuild_and_apply(db)
