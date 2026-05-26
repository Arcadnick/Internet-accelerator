"""Server-rendered HTML for the user dashboard (no SPA).

Uses a cookie-based session: on POST /login we set an HttpOnly cookie with
the JWT; subsequent requests reuse the cookie.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.i18n import COOKIE_NAME as LANG_COOKIE
from app.i18n import LOCALES, get_locale, translator
from app.models import Subscription, User, UserRole
from app.security import create_access_token, verify_password

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(f) < 1024:
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} {unit}"
        f /= 1024
    return f"{f:.1f} PB"


def _happ_deeplink(sub_url: str) -> str:
    from urllib.parse import quote

    return f"happ://add/{quote(sub_url, safe='')}"


templates.env.globals["format_bytes"] = _format_bytes
templates.env.globals["happ_deeplink"] = _happ_deeplink


def render(
    request: Request,
    template: str,
    context: dict | None = None,
    status_code: int = 200,
) -> Response:
    """Render a template with `t()` translator + `locale` injected."""
    locale = get_locale(request)
    ctx = {
        "t": translator(locale),
        "locale": locale,
        "locales": LOCALES,
        **(context or {}),
    }
    return templates.TemplateResponse(
        request, template, ctx, status_code=status_code
    )


router = APIRouter(tags=["web"])

SESSION_COOKIE = "session"


async def _user_from_cookie(request: Request, db: AsyncSession) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
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


# Backwards-compat alias used by other modules.
COOKIE_NAME = SESSION_COOKIE


@router.get("/", response_class=HTMLResponse)
async def root(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    user = await _user_from_cookie(request, db)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.role == UserRole.admin:
        return RedirectResponse("/admin", status_code=302)
    subs = list(
        (
            await db.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    base = str(request.base_url).rstrip("/")
    for s in subs:
        s.sub_url = f"{base}/sub/{s.sub_token}"  # type: ignore[attr-defined]
    return render(request, "dashboard.html", {"user": user, "subs": subs})


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Response:
    return render(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return render(
            request,
            "login.html",
            {"error": translator(get_locale(request))("login.error.invalid")},
            status_code=401,
        )
    if not user.is_active:
        return render(
            request,
            "login.html",
            {"error": translator(get_locale(request))("login.error.disabled")},
            status_code=403,
        )
    token = create_access_token(user.id, user.role.value)
    redirect_to = "/admin" if user.role == UserRole.admin else "/"
    response = RedirectResponse(redirect_to, status_code=302)
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=(settings.env != "dev"),  # require HTTPS in prod
        max_age=settings.jwt_ttl_minutes * 60,
    )
    return response


@router.get("/logout")
async def logout() -> Response:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/lang/{code}")
async def switch_lang(code: str, request: Request) -> Response:
    """Set the language cookie and bounce back to the referring page."""
    target = request.headers.get("referer") or "/"
    response = RedirectResponse(target, status_code=302)
    if code in LOCALES:
        response.set_cookie(
            LANG_COOKIE,
            code,
            httponly=False,  # readable by JS for client-side niceties
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
        )
    return response
