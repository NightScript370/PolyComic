import io
import os

# Before any app import: app.main builds the app at import time and requires a
# SESSION_SECRET unless the provider is fake. Tests never touch a real image API.
os.environ["IMAGE_PROVIDER"] = "fake"
os.environ.pop("SESSION_SECRET", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import dispose_engine, get_engine  # noqa: E402


@pytest.fixture(autouse=True)
def fake_provider_env(monkeypatch):
    """Never touch a real image API in tests."""
    monkeypatch.setenv("IMAGE_PROVIDER", "fake")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("BOOK_PAGES", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    monkeypatch.setenv("DEV_LOGIN", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def db(tmp_path, monkeypatch, fake_provider_env):
    """Fresh SQLite database per test (aiosqlite; no Postgres needed)."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    get_settings.cache_clear()
    await dispose_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    from app.jobs.runner import registry

    await registry.shutdown()
    await dispose_engine()


def make_image(fmt: str = "JPEG", size=(800, 1000), color=(200, 150, 120)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def photo_jpeg() -> bytes:
    return make_image("JPEG")


def client(app=None) -> httpx.AsyncClient:
    from app.main import create_app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app or create_app()),
                             base_url="http://test")


async def login(c: httpx.AsyncClient, email: str = "kid-parent@example.test",
                name: str = "Parent") -> dict:
    r = await c.post("/auth/dev-login", data={"email": email, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["user"]
