import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app.api import (
    admin_nodes,
    admin_plans,
    admin_provision,
    admin_subscriptions,
    admin_users,
    auth as auth_api,
    subscription as subscription_api,
    user_dashboard,
)
from app.seed import ensure_default_admin
from app.web import router as web_router
from app.web_admin import AuthRedirect, router as web_admin_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_default_admin()
    yield


app = FastAPI(title="VPN Panel", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AuthRedirect)
async def _auth_redirect(request: Request, exc: AuthRedirect) -> RedirectResponse:
    return RedirectResponse(exc.location, status_code=302)


app.include_router(auth_api.router)
app.include_router(admin_nodes.router)
app.include_router(admin_provision.router)
app.include_router(admin_plans.router)
app.include_router(admin_subscriptions.router)
app.include_router(admin_users.router)
app.include_router(user_dashboard.router)
app.include_router(subscription_api.router)
app.include_router(web_admin_router)
app.include_router(web_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
