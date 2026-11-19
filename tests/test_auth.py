import pytest
from sqlalchemy import select

import app.auth.routes as auth_routes
from app.config import get_settings
from app.db.models import User
from app.db.session import get_sessionmaker
from app.main import create_app
from tests.conftest import client, login


async def test_me_anonymous_then_dev_login_then_logout():
    async with client() as c:
        r = await c.get("/api/me")
        assert r.status_code == 200 and r.json() == {"user": None}
        user = await login(c, email="a@example.test", name="Ann")
        assert user["email"] == "a@example.test" and user["name"] == "Ann"
        me = (await c.get("/api/me")).json()["user"]
        assert me == {"id": user["id"], "name": "Ann", "email": "a@example.test",
                      "picture": None, "is_admin": False}
        assert (await c.post("/auth/logout")).status_code == 204
        assert (await c.get("/api/me")).json() == {"user": None}
        # same dev email -> same user row
        assert (await login(c, email="a@example.test"))["id"] == user["id"]


@pytest.mark.parametrize(
    "path,method",
    [("/api/books", "GET"), ("/api/books", "POST"), ("/api/books/x", "GET"),
     ("/api/books/x/events", "GET"), ("/api/books/x/hero.png", "GET"),
     ("/api/books/x/pages/1.png", "GET"), ("/api/books/x/pages/1/thumb.jpg", "GET"),
     ("/api/books/x/JackAndTheBeanstalk.pdf", "GET"), ("/api/books/x", "DELETE"),
     ("/api/hw1/generate", "POST")],
)
async def test_api_requires_login(path, method):
    async with client() as c:
        r = await c.request(method, path)
    assert r.status_code == 401
    assert r.json() == {"error": "Please sign in."}


async def test_dev_login_off_by_default(monkeypatch):
    monkeypatch.delenv("DEV_LOGIN")
    get_settings.cache_clear()
    async with client() as c:
        assert (await c.post("/auth/dev-login")).status_code == 404


async def test_dev_login_refused_on_https(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://polycomic.example")
    get_settings.cache_clear()
    assert not get_settings().dev_login_enabled
    async with client() as c:
        assert (await c.post("/auth/dev-login")).status_code == 404


def test_session_secret_required_outside_dev(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "gemini")
    monkeypatch.delenv("DEV_LOGIN")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app()
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    get_settings.cache_clear()
    create_app()


async def test_login_not_configured():
    async with client() as c:
        r = await c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 503


class FakeGoogle:
    def __init__(self, userinfo):
        self.userinfo = userinfo

    async def authorize_access_token(self, request):
        return {"access_token": "not-a-real-token", "userinfo": self.userinfo}


async def test_callback_creates_and_updates_user(monkeypatch):
    info = {"sub": "g-123", "email": "parent@gmail.example", "email_verified": True,
            "name": "Pat", "picture": "https://example.test/p.png"}
    monkeypatch.setattr(auth_routes, "google_client", lambda: FakeGoogle(info))
    async with client() as c:
        r = await c.get("/auth/callback", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        me = (await c.get("/api/me")).json()["user"]
        assert me["name"] == "Pat" and me["picture"] == "https://example.test/p.png"

        info.update(name="Pat B")
        await c.get("/auth/callback", follow_redirects=False)
        assert (await c.get("/api/me")).json()["user"]["name"] == "Pat B"

    async with get_sessionmaker()() as s:
        users = list((await s.execute(select(User))).scalars())
    assert len(users) == 1 and users[0].google_sub == "g-123"
    assert users[0].last_login_at is not None


async def test_callback_requires_verified_email(monkeypatch):
    info = {"sub": "g-9", "email": "x@example.test", "email_verified": False}
    monkeypatch.setattr(auth_routes, "google_client", lambda: FakeGoogle(info))
    async with client() as c:
        r = await c.get("/auth/callback", follow_redirects=False)
        assert r.status_code == 303 and "login_error" in r.headers["location"]
        assert (await c.get("/api/me")).json() == {"user": None}


def test_session_cookie_flags(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://polycomic.example")
    monkeypatch.setenv("SESSION_SECRET", "y" * 40)
    get_settings.cache_clear()
    app = create_app()
    mw = [m for m in app.user_middleware if m.cls.__name__ == "SessionMiddleware"][0]
    assert mw.kwargs["https_only"] is True and mw.kwargs["same_site"] == "lax"
    assert auth_routes.redirect_uri() == "https://polycomic.example/auth/callback"
