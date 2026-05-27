"""Server-rendered admin UI.

Mirrors what the JSON /api/admin/* endpoints do, but with HTML forms and
cookie-based auth so a human can manage the system from a browser without
juggling JWT tokens.

Auth model: the same `session` cookie set by /login carries a JWT; we
decode it and require role=admin. Non-admins get a 403 explanation page,
not a generic Unauthorized — that helps confused users sign in with the
right account.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import (
    Node,
    NodeEvent,
    NodeStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
    TrafficLog,
    User,
    UserRole,
)
from app.schemas.provision import ProvisionNodeRequest
from app.security import hash_password
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
from app.services.subscription_factory import create_subscription_for
from app.services.xray_local import rebuild_and_apply
from app.web import COOKIE_NAME, render

router = APIRouter(prefix="/admin", tags=["web:admin"])


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------


async def _user_from_cookie(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        uid = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


class AuthRedirect(Exception):
    """Raised by require_admin when the user must be sent to /login."""

    def __init__(self, location: str = "/login") -> None:
        self.location = location


async def require_admin(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    user = await _user_from_cookie(request, db)
    if user is None:
        raise AuthRedirect("/login")
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


ONLINE_WINDOW_SECONDS = 90  # last_seen_at within this window => "online"


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    now = datetime.now(UTC)
    online_cutoff = now - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    in_24h_cutoff = now - timedelta(hours=24)
    expiring_soon_cutoff = now + timedelta(days=7)

    # top-line counts
    node_count = (await db.execute(select(func.count(Node.id)))).scalar_one()
    active_nodes = (
        await db.execute(
            select(func.count(Node.id)).where(Node.status == NodeStatus.active)
        )
    ).scalar_one()
    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    sub_count = (await db.execute(select(func.count(Subscription.id)))).scalar_one()
    active_subs = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.active
            )
        )
    ).scalar_one()
    plan_count = (await db.execute(select(func.count(Plan.id)))).scalar_one()
    total_traffic = (
        await db.execute(
            select(func.coalesce(func.sum(Subscription.traffic_used_bytes), 0))
        )
    ).scalar_one()

    online_count = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.last_seen_at >= online_cutoff
            )
        )
    ).scalar_one()

    traffic_24h = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(TrafficLog.bytes_up + TrafficLog.bytes_down), 0
                )
            ).where(TrafficLog.collected_at >= in_24h_cutoff)
        )
    ).scalar_one()

    stats = [
        {"label_key": "stats.online_now",    "value": online_count,                     "hint_key": "stats.online_now.hint"},
        {"label_key": "stats.nodes",         "value": f"{active_nodes}/{node_count}",   "hint_key": "stats.active_total"},
        {"label_key": "stats.users",         "value": user_count,                       "hint_key": None},
        {"label_key": "stats.subscriptions", "value": f"{active_subs}/{sub_count}",     "hint_key": "stats.active_total"},
        {"label_key": "stats.plans",         "value": plan_count,                       "hint_key": None},
        {"label_key": "stats.traffic_24h",   "value": _human_bytes(traffic_24h),        "hint_key": None},
        {"label_key": "stats.traffic_total", "value": _human_bytes(total_traffic),      "hint_key": "stats.all_subs"},
    ]

    # detail sections
    top_users_rows = (
        await db.execute(
            select(Subscription, User.email)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.traffic_used_bytes > 0)
            .order_by(Subscription.traffic_used_bytes.desc())
            .limit(5)
        )
    ).all()
    top_users = [
        {
            "email": email,
            "used": s.traffic_used_bytes,
            "used_human": _human_bytes(s.traffic_used_bytes),
            "limit_human": (
                _human_bytes(s.traffic_limit_bytes) if s.traffic_limit_bytes else None
            ),
            "id": s.id,
            "is_online": s.last_seen_at is not None and s.last_seen_at >= online_cutoff,
        }
        for s, email in top_users_rows
    ]

    online_rows = (
        await db.execute(
            select(Subscription, User.email)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.last_seen_at >= online_cutoff)
            .order_by(Subscription.last_seen_at.desc())
            .limit(10)
        )
    ).all()
    online_subs = [
        {"id": s.id, "email": email, "last_seen_at": s.last_seen_at}
        for s, email in online_rows
    ]

    expiring_rows = (
        await db.execute(
            select(Subscription, User.email)
            .join(User, User.id == Subscription.user_id)
            .where(
                Subscription.status == SubscriptionStatus.active,
                Subscription.expires_at.is_not(None),
                Subscription.expires_at <= expiring_soon_cutoff,
            )
            .order_by(Subscription.expires_at)
            .limit(10)
        )
    ).all()
    expiring_soon = [
        {"id": s.id, "email": email, "expires_at": s.expires_at}
        for s, email in expiring_rows
    ]

    recent_events_rows = (
        await db.execute(
            select(NodeEvent, Node.label, Node.country_code)
            .join(Node, Node.id == NodeEvent.node_id)
            .order_by(NodeEvent.created_at.desc())
            .limit(8)
        )
    ).all()
    recent_events = [
        {
            "level": ev.level,
            "message": ev.message,
            "created_at": ev.created_at,
            "node": f"{cc} {label}",
        }
        for ev, label, cc in recent_events_rows
    ]

    recent_subs_rows = (
        await db.execute(
            select(Subscription, User.email)
            .join(User, User.id == Subscription.user_id)
            .order_by(Subscription.created_at.desc())
            .limit(5)
        )
    ).all()
    recent_subs = [
        {
            "id": s.id,
            "email": email,
            "created_at": s.created_at,
            "status": s.status,
        }
        for s, email in recent_subs_rows
    ]

    return render(
        request,
        "admin/dashboard.html",
        {
            "stats": stats,
            "top_users": top_users,
            "online_subs": online_subs,
            "expiring_soon": expiring_soon,
            "recent_events": recent_events,
            "recent_subs": recent_subs,
            "admin": admin,
        },
    )


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------


@router.get("/nodes", response_class=HTMLResponse)
async def nodes_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    flash_kind: str | None = None,
    flash_message: str | None = None,
) -> Response:
    nodes = list((await db.execute(select(Node).order_by(Node.id))).scalars().all())
    flash = (
        {"kind": flash_kind, "message": flash_message}
        if flash_kind and flash_message
        else None
    )
    return render(
        request, "admin/nodes.html", {"nodes": nodes, "flash": flash, "admin": admin}
    )


@router.post("/nodes")
async def create_node_html(
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    country_code: Annotated[str, Form()],
    label: Annotated[str, Form()],
    host: Annotated[str, Form()],
    s2s_password: Annotated[str, Form()],
    s2s_sni: Annotated[str, Form()],
    ssh_port: Annotated[int, Form()] = 22,
    s2s_allow_insecure: Annotated[str | None, Form()] = None,
) -> Response:
    try:
        in_tag, out_tag = make_tags(country_code)
        port = await allocate_inbound_port(db)
        node = Node(
            country_code=country_code.upper(),
            label=label,
            host=host,
            ssh_port=ssh_port,
            s2s_password=s2s_password,
            s2s_sni=s2s_sni,
            s2s_allow_insecure=bool(s2s_allow_insecure),
            panel_inbound_tag=in_tag,
            panel_outbound_tag=out_tag,
            panel_inbound_port=port,
            reality_short_id=random_short_id(),
            status=NodeStatus.active,
        )
        db.add(node)
        await db.commit()
        await rebuild_and_apply(db)
        return RedirectResponse(
            f"/admin/nodes?flash_kind=info&flash_message=Node+{label}+added",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            f"/admin/nodes?flash_kind=error&flash_message={_quote(str(e))}",
            status_code=303,
        )


@router.get("/nodes/{node_id}/edit", response_class=HTMLResponse)
async def node_edit_page(
    node_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return render(request, "admin/node_edit.html", {"node": node, "admin": admin})


@router.post("/nodes/{node_id}/edit")
async def node_edit_submit(
    node_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    label: Annotated[str, Form()],
    host: Annotated[str, Form()],
    s2s_password: Annotated[str, Form()],
    s2s_sni: Annotated[str, Form()],
    ssh_port: Annotated[int, Form()] = 22,
    s2s_allow_insecure: Annotated[str | None, Form()] = None,
    status: Annotated[str, Form()] = "active",
) -> Response:
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    try:
        node.label = label
        node.host = host
        node.ssh_port = ssh_port
        node.s2s_password = s2s_password
        node.s2s_sni = s2s_sni
        node.s2s_allow_insecure = bool(s2s_allow_insecure)
        node.status = NodeStatus(status)
        await db.commit()
        await rebuild_and_apply(db)
        return RedirectResponse(
            f"/admin/nodes?flash_kind=info&flash_message=Node+{label}+updated",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            f"/admin/nodes/{node_id}/edit?flash_kind=error&flash_message={_quote(str(e))}",
            status_code=303,
        )


@router.post("/nodes/{node_id}/delete")
async def delete_node_html(
    node_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    node = await db.get(Node, node_id)
    if node is not None:
        await db.delete(node)
        await db.commit()
        await rebuild_and_apply(db)
        return RedirectResponse(
            f"/admin/nodes?flash_kind=info&flash_message=Node+{node_id}+deleted",
            status_code=303,
        )
    return RedirectResponse("/admin/nodes", status_code=303)


# ---------------------------------------------------------------------------
# provisioning via SSH (UI + SSE stream)
# ---------------------------------------------------------------------------


@router.get("/nodes/provision", response_class=HTMLResponse)
async def provision_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    return render(request, "admin/provision.html", {"admin": admin})


@router.post("/nodes/provision")
async def provision_stream(
    body: ProvisionNodeRequest,
    admin: Annotated[User, Depends(require_admin)],
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

    async def gen() -> AsyncIterator[bytes]:
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
                    s2s_sni=result.host,
                    s2s_allow_insecure=True,
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

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    plans = list((await db.execute(select(Plan).order_by(Plan.id))).scalars().all())
    return render(request, "admin/plans.html", {"plans": plans, "admin": admin})


@router.post("/plans")
async def create_plan_html(
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    name: Annotated[str, Form()],
    traffic_gb: Annotated[int, Form()],
    duration_days: Annotated[int, Form()],
    price: Annotated[Decimal, Form()] = Decimal(0),
) -> Response:
    plan = Plan(
        name=name,
        traffic_bytes=traffic_gb * 1024 * 1024 * 1024,
        duration_days=duration_days,
        price=price,
    )
    db.add(plan)
    await db.commit()
    return RedirectResponse("/admin/plans", status_code=303)


@router.post("/plans/{plan_id}/delete")
async def delete_plan_html(
    plan_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    plan = await db.get(Plan, plan_id)
    if plan is not None:
        await db.delete(plan)
        await db.commit()
    return RedirectResponse("/admin/plans", status_code=303)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    users = list((await db.execute(select(User).order_by(User.id))).scalars().all())
    counts_rows = (
        await db.execute(
            select(Subscription.user_id, func.count(Subscription.id)).group_by(
                Subscription.user_id
            )
        )
    ).all()
    sub_counts = dict(counts_rows)
    # "online" = any subscription of this user is recently active
    online_cutoff = datetime.now(UTC) - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    last_seen_rows = (
        await db.execute(
            select(Subscription.user_id, func.max(Subscription.last_seen_at)).group_by(
                Subscription.user_id
            )
        )
    ).all()
    last_seen = dict(last_seen_rows)
    online_user_ids = {
        uid for uid, ts in last_seen.items() if ts is not None and ts >= online_cutoff
    }
    return render(
        request,
        "admin/users.html",
        {
            "users": users,
            "sub_counts": sub_counts,
            "last_seen": last_seen,
            "online_user_ids": online_user_ids,
            "admin": admin,
        },
    )


@router.post("/users")
async def create_user_html(
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is None:
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=UserRole.user,
        )
        db.add(user)
        await db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_page(
    user_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return render(request, "admin/user_edit.html", {"user": user, "admin": admin})


@router.post("/users/{user_id}/edit")
async def edit_user_submit(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
    password: Annotated[str | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
) -> Response:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.email = email
    user.role = UserRole(role)
    user.is_active = bool(is_active)
    if password:  # blank means "keep current"
        user.password_hash = hash_password(password)
    await db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user_html(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    user = await db.get(User, user_id)
    if user is not None and user.id != admin.id:  # never delete self
        await db.delete(user)
        await db.commit()
        # Subscriptions are cascade-deleted; rebuild XRay config so removed
        # UUIDs disappear from all inbounds.
        await rebuild_and_apply(db)
    return RedirectResponse("/admin/users", status_code=303)


# ---------------------------------------------------------------------------
# subscriptions
# ---------------------------------------------------------------------------


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    subs = list(
        (await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    )
    users = list((await db.execute(select(User).order_by(User.id))).scalars().all())
    active_plans = list(
        (await db.execute(select(Plan).where(Plan.is_active.is_(True)))).scalars().all()
    )
    users_by_id = {u.id: u.email for u in users}
    plans_all = list((await db.execute(select(Plan))).scalars().all())
    plans_by_id = {p.id: p.name for p in plans_all}
    online_cutoff = datetime.now(UTC) - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    online_sub_ids = {
        s.id for s in subs if s.last_seen_at is not None and s.last_seen_at >= online_cutoff
    }
    return render(
        request,
        "admin/subscriptions.html",
        {
            "subs": subs,
            "users": users,
            "active_plans": active_plans,
            "users_by_id": users_by_id,
            "plans_by_id": plans_by_id,
            "online_sub_ids": online_sub_ids,
            "admin": admin,
        },
    )


@router.post("/subscriptions")
async def create_subscription_html(
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    user_id: Annotated[int, Form()],
    plan_id: Annotated[int, Form()],
) -> Response:
    user = await db.get(User, user_id)
    plan = await db.get(Plan, plan_id)
    if user is None or plan is None or not plan.is_active:
        return RedirectResponse("/admin/subscriptions", status_code=303)
    await create_subscription_for(db, user, plan)
    await rebuild_and_apply(db)
    return RedirectResponse("/admin/subscriptions", status_code=303)


@router.get("/subscriptions/{sub_id}/edit", response_class=HTMLResponse)
async def edit_subscription_page(
    sub_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    user = await db.get(User, sub.user_id)
    plans = list(
        (await db.execute(select(Plan).order_by(Plan.id))).scalars().all()
    )
    current_plan = await db.get(Plan, sub.plan_id) if sub.plan_id else None
    return render(
        request,
        "admin/subscription_edit.html",
        {
            "sub": sub,
            "user": user,
            "plans": plans,
            "current_plan": current_plan,
            "admin": admin,
        },
    )


@router.post("/subscriptions/{sub_id}/edit")
async def edit_subscription_submit(
    sub_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    plan_id: Annotated[int, Form()],
    status: Annotated[str, Form()],
    traffic_limit_gb: Annotated[float, Form()],
    expires_at: Annotated[str | None, Form()] = None,
    never_expires: Annotated[str | None, Form()] = None,
    reset_traffic: Annotated[str | None, Form()] = None,
    extend_by_plan: Annotated[str | None, Form()] = None,
) -> Response:
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    plan = await db.get(Plan, plan_id)
    sub.plan_id = plan.id if plan is not None else None

    # Only allow manual transitions to active/disabled — expired/over_limit
    # are derived by the billing job from the actual numbers.
    if status in ("active", "disabled"):
        sub.status = SubscriptionStatus(status)

    sub.traffic_limit_bytes = int(traffic_limit_gb * 1024 * 1024 * 1024)

    if never_expires:
        sub.expires_at = None
    elif extend_by_plan and plan is not None and plan.duration_days > 0:
        base = sub.expires_at or datetime.now(UTC)
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)
        sub.expires_at = base + timedelta(days=plan.duration_days)
    elif expires_at:
        # browser sends "YYYY-MM-DDTHH:MM" from <input type=datetime-local>
        try:
            parsed = datetime.fromisoformat(expires_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            sub.expires_at = parsed
        except ValueError:
            pass

    if reset_traffic:
        sub.traffic_used_bytes = 0

    await db.commit()
    await rebuild_and_apply(db)
    return RedirectResponse("/admin/subscriptions", status_code=303)


@router.post("/subscriptions/{sub_id}/delete")
async def delete_subscription_html(
    sub_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
) -> Response:
    sub = await db.get(Subscription, sub_id)
    if sub is not None:
        await db.delete(sub)
        await db.commit()
        await rebuild_and_apply(db)
    return RedirectResponse("/admin/subscriptions", status_code=303)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _human_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(f) < 1024:
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} {unit}"
        f /= 1024
    return f"{f:.1f} PB"
