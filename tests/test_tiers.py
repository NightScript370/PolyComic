"""Quality tiers (app/tiers.py): settings, prices, models per tier, the BOOK_PRICE_CENTS
override, the books.tier migration default, per-tier costs, and HW1 with a tier."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from app.config import Settings, get_settings
from app.db import repo
from app.db.models import ApiCall, Book
from app.db.session import get_sessionmaker
from app.jobs.runner import registry
from app.pipeline.hw1 import run_hw1
from app.providers.base import PhotoTraits
from app.providers.fake import FakeProvider
from app.providers.fake_grader import FakeGrader
from app.providers.pricing import PRICES
from app.tiers import DEFAULT_TIER, Tier, parse_tier, tier_config
from tests.conftest import client, login
from tests.test_quality import _photo
from tests.wallet_helpers import balance, entries, fund

PRO_MODEL = "gemini-3-pro-image"
CHEAP_MODEL = "gemini-3.1-flash-lite-image"
FORM = {"story": "jack", "style": "n64", "detail": "2", "name": "Mia", "pronouns": "she"}


@pytest.fixture(autouse=True)
def short_books(monkeypatch):
    monkeypatch.setenv("BOOK_PAGES", "1,2")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.test")
    get_settings.cache_clear()


def _env(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


# --- settings / resolver ----------------------------------------------------------------


def test_tier_defaults():
    pro, cheap = tier_config(Tier.PROFESSIONAL), tier_config("cost_efficient")
    assert (pro.image_model, pro.page_model, pro.price_cents, pro.label) == (
        PRO_MODEL, PRO_MODEL, 399, "Professional")
    assert (cheap.image_model, cheap.page_model, cheap.price_cents, cheap.label) == (
        CHEAP_MODEL, CHEAP_MODEL, 99, "Cost-Efficient")
    assert DEFAULT_TIER is Tier.COST_EFFICIENT
    # both A/B candidates for the cheap tier are priced (not the pessimistic fallback)
    assert {CHEAP_MODEL, "gemini-nano-banana-2.1"} <= set(PRICES)


def test_tier_model_settings(monkeypatch):
    _env(monkeypatch, PAGE_IMAGE_MODEL="gemini-3.1-flash-image",
         COST_EFFICIENT_IMAGE_MODEL="gemini-3.1-flash-lite-image",
         PRICE_PROFESSIONAL_CENTS="450", PRICE_COST_EFFICIENT_CENTS="120")
    pro, cheap = tier_config(Tier.PROFESSIONAL), tier_config(Tier.COST_EFFICIENT)
    # Professional keeps the old GEMINI_IMAGE_MODEL / PAGE_IMAGE_MODEL behaviour
    assert (pro.image_model, pro.page_model, pro.price_cents) == (
        PRO_MODEL, "gemini-3.1-flash-image", 450)
    # Cost-Efficient: one model everywhere, PAGE_IMAGE_MODEL does not apply
    assert (cheap.image_model, cheap.page_model, cheap.price_cents) == (
        "gemini-3.1-flash-lite-image", "gemini-3.1-flash-lite-image", 120)
    _env(monkeypatch, PROFESSIONAL_IMAGE_MODEL="gemini-3.1-flash-image")
    pro = tier_config(Tier.PROFESSIONAL)
    assert pro.image_model == pro.page_model == "gemini-3.1-flash-image"


def test_book_price_cents_overrides_both_tiers_only_when_set(monkeypatch):
    assert Settings().book_price_cents is None
    _env(monkeypatch, BOOK_PRICE_CENTS="")  # empty line in .env = not set
    assert [tier_config(t).price_cents for t in Tier] == [399, 99]
    _env(monkeypatch, BOOK_PRICE_CENTS="0")
    assert [tier_config(t).price_cents for t in Tier] == [0, 0]
    _env(monkeypatch, BOOK_PRICE_CENTS="150")
    assert [tier_config(t).price_cents for t in Tier] == [150, 150]


def test_parse_tier():
    assert parse_tier(None) is parse_tier("") is Tier.COST_EFFICIENT
    assert parse_tier(" Professional ") is Tier.PROFESSIONAL
    for bad in ("pro", "gold", "cost-efficient"):
        with pytest.raises(ValueError, match="tier must be one of"):
            parse_tier(bad)


# --- books API ---------------------------------------------------------------------------


async def _post(c, photo, **extra):
    return await c.post("/api/books", data={**FORM, **extra},
                        files={"photo": ("kid.jpg", photo, "image/jpeg")})


async def _book_calls(book_id: str) -> list[ApiCall]:
    async with get_sessionmaker()() as s:
        return await repo.list_calls(s, book_id)


@pytest.mark.parametrize(("tier", "model", "price"), [
    ("professional", PRO_MODEL, 399),
    ("cost_efficient", CHEAP_MODEL, 99),
    (None, CHEAP_MODEL, 99),  # default tier
])
async def test_book_uses_tier_model_and_price(photo_jpeg, tier, model, price):
    async with client() as c:
        user = await login(c)
        await fund(user["id"], 1000)
        extra = {"tier": tier} if tier else {}
        r = await _post(c, photo_jpeg, **extra)
        assert r.status_code == 202, r.text
        book_id = r.json()["book_id"]
        await registry.wait(book_id)
        listing = (await c.get("/api/books")).json()
        detail = (await c.get(f"/api/books/{book_id}")).json()
    want_tier = tier or "cost_efficient"
    assert listing[0]["tier"] == detail["tier"] == want_tier
    assert detail["status"] == "done"
    calls = await _book_calls(book_id)
    image_calls = [c for c in calls if c.kind in ("hero", "family_sheet", "page")]
    assert {c.kind for c in image_calls} == {"hero", "family_sheet", "page"}
    assert {c.model for c in image_calls} == {model}
    assert {c.model for c in calls if c.kind == "grade"} == {"fake-vision"}  # same grader
    assert await balance(user["id"]) == 1000 - price
    charge = [e for e in await entries(user["id"]) if e.kind == "charge"][0]
    assert charge.amount_cents == -price and charge.book_id == book_id
    async with get_sessionmaker()() as s:
        assert (await repo.get_book(s, book_id)).tier == want_tier


async def test_unknown_tier_is_400_and_charges_nothing(photo_jpeg):
    async with client() as c:
        user = await login(c)
        await fund(user["id"], 1000)
        r = await _post(c, photo_jpeg, tier="gold")
        assert r.status_code == 400 and "tier must be one of" in r.json()["error"]
        assert (await c.get("/api/books")).json() == []
    assert await balance(user["id"]) == 1000


async def test_402_reports_the_tier_price(photo_jpeg):
    async with client() as c:
        user = await login(c)
        await fund(user["id"], 200)  # enough for Cost-Efficient, not for Professional
        r = await _post(c, photo_jpeg, tier="professional")
        assert r.status_code == 402
        assert r.json() == {"error": "Not enough balance. Add funds to make a book.",
                            "balance_cents": 200, "price_cents": 399}
        r = await _post(c, photo_jpeg, tier="cost_efficient")
        assert r.status_code == 202
        await registry.wait(r.json()["book_id"])
    assert await balance(user["id"]) == 101


async def test_book_price_cents_zero_makes_professional_free(photo_jpeg, monkeypatch):
    _env(monkeypatch, BOOK_PRICE_CENTS="0")
    async with client() as c:
        user = await login(c)  # no funds at all
        r = await _post(c, photo_jpeg, tier="professional")
        assert r.status_code == 202, r.text
        await registry.wait(r.json()["book_id"])
        assert (await c.get("/api/wallet")).json()["tiers"][0]["price_cents"] == 0
    assert await balance(user["id"]) == 0


# --- migration default --------------------------------------------------------------------


def test_migration_0006_labels_old_books_professional(tmp_path, monkeypatch):
    db = (tmp_path / "m6.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(f"sqlite:///{db}")
    command.upgrade(cfg, "0005")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, google_sub, email, name) "
                          "VALUES (1, 's', 'a@b.test', 'A')"))
        conn.execute(text(
            "INSERT INTO books (id, user_id, story, title, preset, detail, hero_name, "
            "pronouns, favourites, about, family_variant, page_numbers, status, stage, "
            "est_cost_usd, pdf_filename) VALUES ('old', 1, 'jack', 't', 'n64', 2, 'h', "
            "'she', '{}', '', '', '[]', 'done', 'done', 0.5, '')"))
    command.upgrade(cfg, "0006")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT tier FROM books WHERE id='old'")).scalar_one() == \
            "professional"
    command.downgrade(cfg, "0005")
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(books)"))]
        assert "tier" not in cols
    engine.dispose()


async def test_orm_row_without_tier_gets_professional():
    async with client() as c:
        user = await login(c)
    async with get_sessionmaker()() as s:
        s.add(Book(id="legacy", user_id=user["id"], story="jack", title="t", preset="n64",
                   detail=1, hero_name="h", pronouns="she"))
        await s.commit()
        tier = (await s.execute(select(Book.tier).where(Book.id == "legacy"))).scalar_one()
    assert tier == "professional"


# --- per-tier costs -------------------------------------------------------------------------


async def _done_books(user_id: int, books: list[tuple[str, str | None, float]]) -> None:
    async with get_sessionmaker()() as s:
        for bid, tier, cost in books:
            extra = {"tier": tier} if tier else {}
            s.add(Book(id=bid, user_id=user_id, story="jack", title="t", preset="n64",
                       detail=1, hero_name="h", pronouns="she", status="done", **extra))
            await s.flush()
            s.add(ApiCall(book_id=bid, user_id=user_id, provider="fake", model="m",
                          kind="page", page=1, est_cost_usd=cost))
        await s.commit()


BOOKS = [("p1", "professional", 2.0), ("p2", None, 3.0), ("p3", "professional", 7.0),
         ("c1", "cost_efficient", 0.2), ("c2", "cost_efficient", 0.4)]


async def test_wallet_reports_per_tier_prices_and_averages():
    async with client() as c:
        user = await login(c)
        await _done_books(user["id"], BOOKS)
        body = (await c.get("/api/wallet")).json()
    assert body["book_price_cents"] == 99 and body["default_tier"] == "cost_efficient"
    assert body["tiers"] == [
        {"id": "professional", "label": "Professional", "price_cents": 399,
         "avg_cost_per_book_usd": 4.0, "books_costed": 3},
        {"id": "cost_efficient", "label": "Cost-Efficient", "price_cents": 99,
         "avg_cost_per_book_usd": 0.3, "books_costed": 2}]
    assert body["books_costed"] == 5  # overall, kept for older clients


async def test_admin_costs_per_tier():
    async with client() as admin_c:
        admin = await login(admin_c, email="boss@example.test")
        await _done_books(admin["id"], BOOKS)
        c = (await admin_c.get("/api/admin/costs")).json()
    pro, cheap = c["tiers"]
    assert pro == {"id": "professional", "label": "Professional", "price_cents": 399,
                   "price_usd": 3.99, "books_done": 3, "avg_cost_per_book_usd": 4.0,
                   "median_cost_per_book_usd": 3.0, "margin_per_book_usd": -0.01}
    assert cheap["id"] == "cost_efficient" and cheap["books_done"] == 2
    assert cheap["avg_cost_per_book_usd"] == pytest.approx(0.3)
    assert cheap["median_cost_per_book_usd"] == pytest.approx(0.3)
    assert cheap["margin_per_book_usd"] == pytest.approx(0.69)
    assert {b["book_id"]: b["tier"] for b in c["recent_books"]} == {
        "p1": "professional", "p2": "professional", "p3": "professional",
        "c1": "cost_efficient", "c2": "cost_efficient"}


# --- HW1 -------------------------------------------------------------------------------------


async def _hw1(c, photo, **extra):
    return await c.post("/api/hw1/generate", data={"style": "ps1", "detail": "2", **extra},
                        files={"photo": ("kid.jpg", photo, "image/jpeg")})


@pytest.mark.parametrize(("tier", "model", "price"), [
    ("professional", PRO_MODEL, 399), (None, CHEAP_MODEL, 99)])
async def test_hw1_tier_model_and_price(photo_jpeg, tier, model, price):
    async with client() as c:
        user = await login(c)
        await fund(user["id"], 500)
        r = await _hw1(c, photo_jpeg, **({"tier": tier} if tier else {}))
        assert r.status_code == 200, r.text
        assert r.json()["tier"] == (tier or "cost_efficient")
    async with get_sessionmaker()() as s:
        calls = list((await s.execute(select(ApiCall))).scalars())
    image = [c for c in calls if c.kind in ("hw1_hero", "hw1_scene", "family_sheet")]
    assert {c.kind for c in image} == {"hw1_hero", "hw1_scene", "family_sheet"}
    assert {c.model for c in image} == {model}
    assert await balance(user["id"]) == 500 - price


async def test_hw1_unknown_tier_and_402(photo_jpeg):
    async with client() as c:
        user = await login(c)
        await fund(user["id"], 200)
        r = await _hw1(c, photo_jpeg, tier="deluxe")
        assert r.status_code == 400 and "tier" in r.json()["error"]
        r = await _hw1(c, photo_jpeg, tier="professional")
        assert r.status_code == 402 and r.json()["price_cents"] == 399
    assert await balance(user["id"]) == 200


async def test_hw1_hero_waits_for_traits_and_has_identity_line():
    fake, photo = FakeProvider(), await _photo()
    grader = FakeGrader(traits=PhotoTraits(5, "black", "curly"))
    await run_hw1(fake, photo, "n64", 2, grader=grader)
    hero = [x["prompt"] for x in fake.calls if x["prompt"].startswith("Task: turn the person")]
    assert len(hero) == 1 and grader.trait_calls == 1
    assert "The child has" in hero[0] and "curly black hair" in hero[0]
    assert "must clearly read as curly" in hero[0]


# --- pages ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "step"), [("/", "6. Quality"), ("/hw1", "4. Quality")])
async def test_pages_render_the_tier_picker(path, step, monkeypatch):
    _env(monkeypatch, PRICE_PROFESSIONAL_CENTS="450")
    async with client() as c:
        html = (await c.get(path)).text
    assert step in html
    assert 'name="tier" value="professional">' in html
    assert 'name="tier" value="cost_efficient" checked>' in html
    assert "PROFESSIONAL" in html and "COST-EFFICIENT" in html
    assert 'data-cents="450">$4.50<' in html and 'data-cents="99">$0.99<' in html
    assert "Top-quality art, slower" in html and "Good art, low price" in html
