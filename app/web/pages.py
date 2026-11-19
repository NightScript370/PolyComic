"""HTML pages added in M4: /wallet, /admin, unlisted /hw1, and robots.txt.

Templates are written by the UI work; until one exists a minimal fallback page is served.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound

from app.auth.deps import OptionalUser
from app.billing.refunds import is_admin
from app.tiers import tier_options
from app.web.assets import register_static_url

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
register_static_url(templates.env)

ROBOTS = "User-agent: *\nDisallow: /admin\nDisallow: /wallet\nDisallow: /api/\n"


def render(request: Request, name: str, title: str, *, status: int = 200, **ctx):
    try:
        templates.env.get_template(name)
    except TemplateNotFound:
        meta = '<meta name="robots" content="noindex">' if ctx.get("noindex") else ""
        return HTMLResponse(
            f"<!doctype html><html><head><meta charset='utf-8'>{meta}<title>{title}</title>"
            f"</head><body><h1>{title}</h1></body></html>", status_code=status)
    return templates.TemplateResponse(request, name, ctx, status_code=status)


@router.get("/wallet", response_class=HTMLResponse)
async def wallet_page(request: Request, user: OptionalUser):
    return render(request, "wallet.html", "Wallet", noindex=True)


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: OptionalUser):
    if not is_admin(user):
        return HTMLResponse("<!doctype html><title>Not Found</title><h1>404 Not Found</h1>",
                            status_code=404)
    return render(request, "admin.html", "Admin", noindex=True)


@router.get("/hw1", response_class=HTMLResponse)
async def hw1_page(request: Request):
    return render(request, "hw1.html", "Quick Scenes", noindex=True, tiers=tier_options())


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return ROBOTS
