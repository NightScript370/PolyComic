import base64
import io
from contextlib import asynccontextmanager

import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models import ApiCall
from app.db.session import get_sessionmaker
from app.providers.base import NoImageError
from app.providers.fake import FakeProvider
from tests.conftest import client, login, make_image
from tests.wallet_helpers import balance, entries, login_funded


@asynccontextmanager
async def _client(funded: bool = True):
    async with client() as c:
        if funded:
            await login_funded(c, cents=500)
        else:
            await login(c)
        yield c


async def _calls() -> list[ApiCall]:
    async with get_sessionmaker()() as s:
        return list((await s.execute(select(ApiCall).order_by(ApiCall.id))).scalars())


async def test_generate_requires_login(photo_jpeg):
    fake = FakeProvider()
    async with client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
            data={"style": "n64", "detail": "2"},
        )
    assert r.status_code == 401
    assert r.json() == {"error": "Please sign in."}
    assert fake.calls == []


def _png(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))


async def test_styles_endpoint():
    async with _client() as c:
        r = await c.get("/api/styles")
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == ["n64", "ps1", "sprite16"]


async def test_generate_happy_path(photo_jpeg, monkeypatch):
    fake = FakeProvider()
    import app.api.hw1 as api

    monkeypatch.setattr(api, "get_image_provider", lambda name=None, model=None: fake)
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
            data={"style": "sprite16", "detail": "3"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["style"] == "sprite16" and body["detail"] == 3
    assert _png(body["hero"]["data_url"]).format == "PNG"
    assert [s["page"] for s in body["scenes"]] == [5, 19]
    for s in body["scenes"]:
        assert s["title"] and s["caption"]
        _png(s["data_url"])
    assert body["est_cost_usd"] == 0.0
    # 4 image calls: hero (photo only), family sheet (no refs), the beanstalk scene
    # [hero] and the planting scene with the mother [hero, family sheet] - no photo on
    # scenes (the hero reference is the identity anchor)
    assert sorted(c["n_images"] for c in fake.calls) == [0, 1, 1, 2]
    by_n = {c["n_images"]: c["prompt"] for c in fake.calls}
    assert "reference sheet for the family" in by_n[0]
    assert "Image 2 is the family reference sheet - Mother" in by_n[2]
    assert "Planting" in by_n[2] or "purple bean" in by_n[2]
    calls = await _calls()
    assert sorted((c.kind, c.page or 0, c.ok) for c in calls) == [
        ("family_match", 0, True), ("family_sheet", 0, True),
        ("family_sheet_check", 0, True), ("hw1_hero", 0, True),
        ("hw1_scene", 5, True), ("hw1_scene", 19, True)]
    assert all(c.user_id is not None and c.book_id is None for c in calls)


async def test_generate_uses_env_fake_provider(photo_jpeg):
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.png", make_image("PNG"), "image/png")},
            data={"style": "n64", "detail": "1"},
        )
    assert r.status_code == 200, r.text


async def test_generate_rejects_non_image():
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.txt", b"definitely not an image", "text/plain")},
            data={"style": "n64", "detail": "2"},
        )
    assert r.status_code == 400
    assert "error" in r.json()


async def test_generate_rejects_gif():
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.gif", make_image("GIF"), "image/gif")},
            data={"style": "n64"},
        )
    assert r.status_code == 400
    assert "JPEG" in r.json()["error"]


@pytest.mark.parametrize(("style", "detail"), [("mario", "2"), ("n64", "7"), ("n64", "x")])
async def test_generate_rejects_bad_params(photo_jpeg, style, detail):
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
            data={"style": style, "detail": detail},
        )
    assert r.status_code == 400
    assert "error" in r.json()


async def test_safety_block_maps_to_422(photo_jpeg, monkeypatch):
    class Blocked:
        name = "blocked"

        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            raise NoImageError("no image (finish_reason=IMAGE_SAFETY)",
                               finish_reason="IMAGE_SAFETY")

    import app.api.hw1 as api

    monkeypatch.setattr(api, "get_image_provider", lambda name=None, model=None: Blocked())
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
            data={"style": "ps1", "detail": "2"},
        )
    assert r.status_code == 422
    assert "safety" in r.json()["error"]
    calls = await _calls()
    # the hero now waits for the photo traits, so the family match (ok) is recorded
    # before the run's failure row; the family sheet (blocked or cancelled) may be too
    assert (calls[0].kind, calls[0].ok) == ("family_match", True)
    assert (calls[-1].kind, calls[-1].ok) == ("hw1", False)
    assert "IMAGE_SAFETY" in calls[-1].error
    assert all(c.kind == "family_sheet" and not c.ok for c in calls[1:-1])
    # charged 99 cents, then re-credited because the run failed
    uid = calls[-1].user_id
    assert await balance(uid) == 500
    kinds = [(e.kind, e.amount_cents) for e in await entries(uid)]
    assert kinds == [("topup", 500), ("charge", -99), ("recredit", 99)]


async def test_quick_scenes_charged_per_run(photo_jpeg, monkeypatch):
    fake = FakeProvider()
    import app.api.hw1 as api

    monkeypatch.setattr(api, "get_image_provider", lambda name=None, model=None: fake)
    async with _client() as c:
        for _ in range(2):
            r = await c.post("/api/hw1/generate",
                             files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
                             data={"style": "ps1", "detail": "2"})
            assert r.status_code == 200
        uid = (await c.get("/api/me")).json()["user"]["id"]
    assert await balance(uid) == 500 - 2 * 99


async def test_quick_scenes_insufficient_funds(photo_jpeg, monkeypatch):
    fake = FakeProvider()
    import app.api.hw1 as api

    monkeypatch.setattr(api, "get_image_provider", lambda name=None, model=None: fake)
    async with _client(funded=False) as c:
        r = await c.post("/api/hw1/generate",
                         files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
                         data={"style": "ps1", "detail": "2"})
    assert r.status_code == 402
    assert r.json()["price_cents"] == 99 and r.json()["balance_cents"] == 0
    assert fake.calls == []


async def test_missing_key_gives_503(photo_jpeg, monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "gemini")
    from app.config import get_settings

    get_settings.cache_clear()
    async with _client() as c:
        r = await c.post(
            "/api/hw1/generate",
            files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")},
            data={"style": "ps1", "detail": "2"},
        )
    assert r.status_code == 503
    assert "error" in r.json()


async def test_photo_is_downscaled_and_transposed():
    from app.pipeline.photo import normalise_photo

    buf = io.BytesIO()
    im = Image.new("RGB", (3000, 2000), (10, 20, 30))
    exif = im.getexif()
    exif[0x0112] = 6  # rotate 90 on display
    im.save(buf, format="JPEG", exif=exif.tobytes())
    out = Image.open(io.BytesIO(normalise_photo(buf.getvalue())))
    assert max(out.size) <= 1536
    assert out.size[1] > out.size[0]  # portrait after EXIF transpose


async def test_fake_provider_is_deterministic():
    a = await FakeProvider().edit("hello", [b"x"])
    b = await FakeProvider().edit("hello", [b"x"])
    c = await FakeProvider().edit("hello!", [b"x"])
    assert a.png == b.png != c.png
