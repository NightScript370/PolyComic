import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import router as admin_router
from app.api.billing import router as billing_router
from app.api.books import router as books_router
from app.api.common import ApiError
from app.api.hw1 import router as hw1_router
from app.auth.routes import router as auth_router
from app.billing.stripe_client import billing_status
from app.config import Settings, get_settings
from app.db import repo
from app.db.session import dispose_engine, get_sessionmaker
from app.jobs.runner import recredit_failed_books, registry
from app.logging_setup import RequestIdMiddleware, setup_logging
from app.tiers import tier_options
from app.web.assets import CachedStaticFiles
from app.web.pages import router as pages_router
from app.web.pages import templates

WEB_DIR = Path(__file__).parent / "web"
SESSION_COOKIE = "polycomic_session"
SESSION_MAX_AGE_S = 14 * 24 * 3600

log = logging.getLogger("app.main")


def session_secret(settings: Settings) -> str:
    """SESSION_SECRET is required, except for offline/dev runs (fake provider or DEV_LOGIN),
    which get a random per-process secret (sessions end on restart)."""
    if settings.session_secret:
        return settings.session_secret
    if settings.dev_mode:
        log.warning("SESSION_SECRET is empty: using a random dev secret (dev/fake mode only)")
        return secrets.token_urlsafe(48)
    raise RuntimeError(
        "SESSION_SECRET is not set. Generate one with: "
        'python -c "import secrets;print(secrets.token_urlsafe(48))"'
    )


async def recover_stale_jobs() -> int:
    """Jobs run in this process; any queued/running book left over from before a restart
    can never finish, so mark it failed."""
    async with get_sessionmaker()() as s:
        n = await repo.fail_stale_books(s)
    if n:
        log.warning("marked %d stale book job(s) failed after restart", n)
    return n


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await recover_stale_jobs()
    except Exception:
        log.exception("stale job recovery failed (is the database reachable?)")
    try:
        await recredit_failed_books()
    except Exception:
        log.exception("re-credit sweep for failed books failed")
    yield
    await registry.shutdown()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.dev_login_enabled:
        log.warning("DEV_LOGIN is enabled: anyone can sign in via POST /auth/dev-login")

    app = FastAPI(title="PolyComic", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(settings),
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE_S,
        same_site="lax",
        https_only=settings.is_https,
    )
    app.add_middleware(RequestIdMiddleware)
    app.mount("/static", CachedStaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return exc.response()

    app.include_router(auth_router)
    app.include_router(hw1_router)
    app.include_router(books_router)
    app.include_router(billing_router)
    app.include_router(admin_router)
    app.include_router(pages_router)
    billing_status(settings)  # logs an error once if Stripe is missing/not a test key

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"tiers": tier_options()})

    return app


app = create_app()
