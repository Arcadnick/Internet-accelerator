"""Auto-provisioning endpoint: admin posts host+root creds, panel SSHes in,
runs bootstrap_node.sh, and registers the node. Logs stream over Server-Sent
Events while bootstrap runs (1-2 minutes typical)."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_db
from app.models import Node, NodeEvent, NodeStatus, User
from app.schemas.provision import ProvisionNodeRequest
from app.security import get_current_admin
from app.services.node_allocator import (
    allocate_inbound_port,
    make_tags,
    random_short_id,
)
from app.services.provisioner import (
    ProvisionRequest,
    ProvisionResult,
    provision_node,
)
from app.services.xray_local import rebuild_and_apply

router = APIRouter(prefix="/api/admin/provision", tags=["admin:provision"])


def _sse(event: str, data: dict | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/node")
async def provision_node_endpoint(
    body: ProvisionNodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_admin)],
) -> StreamingResponse:
    if not body.ssh_password and not body.ssh_private_key:
        raise HTTPException(
            status_code=400, detail="ssh_password or ssh_private_key required"
        )

    req = ProvisionRequest(
        host=body.host,
        ssh_port=body.ssh_port,
        ssh_user=body.ssh_user,
        ssh_password=body.ssh_password,
        ssh_private_key=body.ssh_private_key,
        admin_ssh_key=body.admin_ssh_key,
    )

    async def stream():
        result: ProvisionResult | None = None
        error: str | None = None
        async for event, payload in provision_node(req, panel_ip=body.panel_ip):
            if event == "log":
                yield _sse("log", {"line": payload})
            elif event == "result":
                result = payload  # type: ignore[assignment]
                yield _sse("log", {"line": "bootstrap finished, registering node..."})
            elif event == "error":
                error = payload  # type: ignore[assignment]
                yield _sse("error", {"message": payload})

        if error is not None:
            yield _sse("done", {"status": "error", "message": error})
            return

        if result is None:
            yield _sse("done", {"status": "error", "message": "no result returned"})
            return

        # Register the node in a fresh session — the request-scoped one may be
        # past its useful lifetime after a long-running SSH bootstrap.
        try:
            async with SessionLocal() as fresh_db:
                in_tag, out_tag = make_tags(body.country_code)
                port = await allocate_inbound_port(fresh_db)
                node = Node(
                    country_code=body.country_code.upper(),
                    label=body.label,
                    host=result.host,
                    ssh_port=body.ssh_port,
                    s2s_password=result.s2s_password,
                    s2s_sni=result.host,  # self-signed cert: SNI matches host
                    s2s_allow_insecure=True,  # self-signed; tighten later
                    panel_inbound_tag=in_tag,
                    panel_outbound_tag=out_tag,
                    panel_inbound_port=port,
                    reality_short_id=random_short_id(),
                    status=NodeStatus.active,
                )
                fresh_db.add(node)
                await fresh_db.flush()
                fresh_db.add(
                    NodeEvent(node_id=node.id, level="info", message="provisioned via SSH")
                )
                await fresh_db.commit()
                await rebuild_and_apply(fresh_db)
                yield _sse(
                    "done",
                    {
                        "status": "ok",
                        "node_id": node.id,
                        "panel_inbound_port": node.panel_inbound_port,
                    },
                )
        except Exception as e:
            yield _sse("done", {"status": "error", "message": f"db error: {e!r}"})

    return StreamingResponse(stream(), media_type="text/event-stream")
