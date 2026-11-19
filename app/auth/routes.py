"""Auth routes: Google OIDC login/callback, logout, /api/me and an opt-in dev login.

Logs say who logged in by user id only (no emails, no tokens).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.common import error
from app.auth.deps import SESSION_KEY, DbSession, OptionalUser, user_json
from app.billing.refunds import is_admin
from app.config import get_settings
from app.db import repo
from app.logging_setup import user_id_var

log = logging.getLogger("app.auth")
router = APIRouter()

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
DEV_SUB_PREFIX = "dev:"


@lru_cache
def _oauth(client_id: str, client_secret: str) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def google_client():
    s = get_settings()
    if not (s.google_client_id and s.google_client_secret):
        return None
    return _oauth(s.google_client_id, s.google_client_secret).google


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + "/auth/callback"


def _sign_in(request: Request, user_id: int) -> None:
    request.session.clear()  # drop pre-login state (OAuth state, any old uid)
    request.session[SESSION_KEY] = user_id
    user_id_var.set(user_id)


@router.get("/auth/login")
async def login(request: Request):
    client = google_client()
    if client is None:
        return error(503, "Google sign-in is not configured on this server.")
    return await client.authorize_redirect(request, redirect_uri())


@router.get("/auth/callback")
async def callback(request: Request, session: DbSession):
    client = google_client()
    if client is None:
        return error(503, "Google sign-in is not configured on this server.")
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("login failed: oauth error=%s", exc.error)
        return RedirectResponse("/?login_error=1", status_code=303)
    info = token.get("userinfo") or {}
    sub = str(info.get("sub") or "")
    email = str(info.get("email") or "")
    if not sub or not email or info.get("email_verified") is not True:
        log.warning("login rejected: missing sub/email or email not verified")
        return RedirectResponse("/?login_error=1", status_code=303)
    user, created = await repo.upsert_google_user(
        session, sub=sub, email=email, name=str(info.get("name") or "")[:255],
        picture=(str(info["picture"])[:2048] if info.get("picture") else None),
    )
    _sign_in(request, user.id)
    log.info("login ok user_id=%s new_user=%s", user.id, created)
    return RedirectResponse("/", status_code=303)


@router.post("/auth/logout")
async def logout(request: Request):
    uid = request.session.get(SESSION_KEY)
    request.session.clear()
    if uid is not None:
        log.info("logout user_id=%s", uid)
    return Response(status_code=204)


@router.get("/api/me")
async def me(user: OptionalUser) -> JSONResponse:
    body = user_json(user)
    if body is not None:
        body["is_admin"] = is_admin(user)
    return JSONResponse({"user": body}, headers={"Cache-Control": "private, no-store"})


@router.post("/auth/dev-login")
async def dev_login(
    request: Request,
    session: DbSession,
    email: Annotated[str | None, Form()] = None,
    name: Annotated[str | None, Form()] = None,
):
    """Local testing without Google. Only when DEV_LOGIN=1 and PUBLIC_BASE_URL is not https;
    otherwise this route answers 404 as if it did not exist."""
    if not get_settings().dev_login_enabled:
        return error(404, "Not Found")
    email = (email or "dev@example.test").strip()[:320]
    name = (name or "Dev User").strip()[:255]
    user, created = await repo.upsert_google_user(
        session, sub=DEV_SUB_PREFIX + email.lower(), email=email, name=name, picture=None
    )
    _sign_in(request, user.id)
    log.warning("DEV login user_id=%s new_user=%s", user.id, created)
    return {"user": user_json(user)}
