import asyncio
import io
import json

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select

import app.api.books as books_api
from app.db import repo, repo_assets
from app.db.models import Book, PageAttempt, User
from app.db.session import get_sessionmaker
from app.jobs.runner import registry
from app.main import recover_stale_jobs
from app.providers.base import NoImageError, ProviderError
from app.providers.fake import FakeProvider
from app.stories.loader import load_story
from tests.conftest import client
from tests.wallet_helpers import balance, entries
from tests.wallet_helpers import login_funded as login

FORM = {"story": "jack", "style": "sprite16", "detail": "2", "name": "Mia", "pronouns": "she",
        "age": "7", "fav_color": "green", "fav_food": "", "fav_game": "tag", "fav_animal": "",
        "about": "Mia likes climbing trees."}


@pytest.fixture(autouse=True)
def three_pages(monkeypatch):
    """Most API tests make a short book (hero + pages 1-3); the full book has its own test."""
    from app.config import get_settings

    monkeypatch.setenv("BOOK_PAGES", "1,2,3")
    get_settings.cache_clear()


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(books_api, "get_image_provider", lambda name=None, model=None: provider)
    return provider


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(books_api, "get_image_provider", lambda name=None, model=None: provider)


async def _post(c, photo, **overrides):
    data = {k: v for k, v in {**FORM, **overrides}.items() if v is not None}
    return await c.post("/api/books", data=data,
                        files={"photo": ("kid.jpg", photo, "image/jpeg")})


async def _create(c, photo, **overrides) -> str:
    r = await _post(c, photo, **overrides)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["events_url"] == f"/api/books/{body['book_id']}/events"
    await registry.wait(body["book_id"])
    return body["book_id"]


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        event, data = None, []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if event and data:
            out.append((event, json.loads("\n".join(data))))
    return out


async def test_book_job_end_to_end(photo_jpeg, fake):
    async with client() as c:
        await login(c)
        book_id = await _create(c, photo_jpeg)
        # family sheet + 2 supporting sheets (Milky-White, the old man) + hero + pages 1-3
        # + the mother's speaker portrait (page 3 is a "different moment" line: always)
        assert len(fake.calls) == 8
        # sheets: no refs; hero: photo; p1: hero, family sheet, cow; p2: hero, old man,
        # cow; p3 (Mother): hero, family sheet (pages never get the photo)
        assert sorted(x["n_images"] for x in fake.calls) == [0, 0, 0, 1, 1, 2, 3, 3]

        events = parse_sse((await c.get(f"/api/books/{book_id}/events")).text)
        names = [e for e, _ in events]
        assert names[0] == "stage" and events[0][1] == {
            "stage": "done", "label": "Done", "done": 4, "total": 4}
        assert names.count("hero") == 1 and names.count("page") == 3
        assert names[-1] == "done"
        pages = [d for e, d in events if e == "page"]
        assert [p["page"] for p in pages] == [1, 2, 3]
        p1 = pages[0]
        assert p1["text"] == "Mia and her mother had nothing left to sell but Milky-White."
        assert set(p1["zone"]) == {"zone", "x", "y", "w", "h"}
        assert p1["attempts"] == 1
        assert p1["scores"] == {"likeness": 0.9, "fidelity": 0.9, "passed": True}
        hero_ev = [d for e, d in events if e == "hero"][0]
        assert hero_ev["attempts"] == 1 and hero_ev["scores"]["passed"] is True
        pdf_url = events[-1][1]["pdf_url"]
        assert pdf_url == f"/api/books/{book_id}/JackAndTheBeanstalk.pdf"

        r = await c.get(pdf_url)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"] == 'attachment; filename="JackAndTheBeanstalk.pdf"'
        assert "private" in r.headers["cache-control"]
        assert len(PdfReader(io.BytesIO(r.content)).pages) == 5
        assert (await c.get(f"/api/books/{book_id}/Other.pdf")).status_code == 404

        thumb = await c.get(p1["thumb_url"])
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(thumb.content)).width == 320
        full = await c.get(p1["image_url"])
        assert full.content.startswith(b"\x89PNG")
        hero = [d for e, d in events if e == "hero"][0]
        assert (await c.get(hero["thumb_url"])).status_code == 200
        assert (await c.get(hero["image_url"])).content.startswith(b"\x89PNG")
        assert (await c.get(f"/api/books/{book_id}/pages/9.png")).status_code == 404

        listing = (await c.get("/api/books")).json()
        assert len(listing) == 1
        item = listing[0]
        assert item["book_id"] == book_id and item["status"] == "done"
        assert item["title"] == "Jack and the Beanstalk" and item["hero_name"] == "Mia"
        assert item["pdf_url"] == pdf_url and item["cover_thumb_url"] == hero["thumb_url"]
        assert item["est_cost_usd"] == 0.0 and item["created_at"]

        detail = (await c.get(f"/api/books/{book_id}")).json()
        assert detail["stage"] == "done" and detail["page_numbers"] == [1, 2, 3]
        assert [p["page"] for p in detail["pages"]] == [1, 2, 3]
        assert detail["favourites"]["colour"] == "green"

    async with get_sessionmaker()() as s:
        book = await repo.get_book(s, book_id)
        assert book.status == "done" and book.finished_at is not None
        assert await repo.get_photo(s, book_id)  # kept for regeneration
        calls = await repo.list_calls(s, book_id)
        assert sorted((x.kind, x.page or 0) for x in calls) == [
            ("cast_sheet", 0), ("cast_sheet", 0), ("cast_sheet_check", 0),
            ("cast_sheet_check", 0),  # Milky-White and the old man, first jack/n64 book
            ("family_match", 0), ("family_sheet", 0), ("family_sheet_check", 0),
            ("grade", 0), ("grade", 1),
            ("grade", 2), ("grade", 3), ("hero", 0), ("page", 1), ("page", 2), ("page", 3),
            ("portrait", 3), ("portrait_check", 3)]  # the mother, spoken at another moment
        assert all(x.ok and x.user_id == book.user_id for x in calls)
        # fake grader: photo (200,150,120) -> tone 3, dark-brown wavy hair -> fam_b
        assert book.family_variant == "fam_b"
        attempts = await repo_assets.list_attempts(s, book_id)
        assert [(a.page, a.attempt, a.chosen, a.passed) for a in attempts] == [
            (0, 1, True, True), (1, 1, True, True), (2, 1, True, True), (3, 1, True, True)]
        thumbs = (await s.execute(select(PageAttempt.thumb_jpeg).where(
            PageAttempt.book_id == book_id))).scalars().all()
        assert len(thumbs) == 4 and all(t[:2] == bytes([0xFF, 0xD8]) for t in thumbs)
    # charged once, not re-credited
    assert [(e.kind, e.amount_cents) for e in await entries(book.user_id)] == [
        ("topup", 10_000), ("charge", -99)]


async def test_red_story_and_book_pages_env(photo_jpeg, fake, monkeypatch):
    monkeypatch.setenv("BOOK_PAGES", "2,5")
    from app.config import get_settings

    get_settings.cache_clear()
    async with client() as c:
        await login(c)
        book_id = await _create(c, photo_jpeg, story="red", pronouns="they")
        detail = (await c.get(f"/api/books/{book_id}")).json()
    assert detail["story"] == "red" and detail["title"] == "Little Red Riding Hood"
    assert [p["page"] for p in detail["pages"]] == [2, 5]
    assert detail["pdf_url"].endswith("/LittleRedRidingHood.pdf")


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [({"story": "cinderella"}, "story"), ({"name": ""}, "name"), ({"name": "x" * 41}, "name"),
     ({"name": "Mi\x07a"}, "name"), ({"pronouns": "it"}, "pronouns"), ({"age": "18"}, "age"),
     ({"about": "z" * 501}, "about"), ({"style": "mario"}, "style"), ({"detail": "9"}, "detail")],
)
async def test_validation(photo_jpeg, fake, overrides, needle):
    async with client() as c:
        await login(c)
        r = await _post(c, photo_jpeg, **overrides)
    assert r.status_code == 400, r.text
    assert needle in r.json()["error"]
    assert fake.calls == []


async def test_bad_photo_and_missing_key(photo_jpeg, monkeypatch):
    async with client() as c:
        await login(c)
        assert (await _post(c, b"not an image")).status_code == 400
        monkeypatch.setenv("IMAGE_PROVIDER", "gemini")
        from app.config import get_settings

        get_settings.cache_clear()
        assert (await _post(c, photo_jpeg)).status_code == 503
        assert (await c.get("/api/books")).json() == []


async def test_other_users_books_are_404(photo_jpeg, fake):
    async with client() as a:
        await login(a, email="a@example.test")
        book_id = await _create(a, photo_jpeg)
    async with client() as b:
        await login(b, email="b@example.test")
        base = f"/api/books/{book_id}"
        for path in ["", "/events", "/hero.png", "/hero/thumb.jpg", "/pages/1.png",
                     "/pages/1/thumb.jpg", "/JackAndTheBeanstalk.pdf"]:
            assert (await b.get(base + path)).status_code == 404, path
        assert (await b.delete(base)).status_code == 404
        assert (await b.get("/api/books")).json() == []
    async with client() as a:
        await login(a, email="a@example.test")
        assert (await a.get(f"/api/books/{book_id}")).status_code == 200
        assert (await a.delete(f"/api/books/{book_id}")).json() == {"ok": True}
        assert (await a.get(f"/api/books/{book_id}")).status_code == 404
        assert (await a.get("/api/books")).json() == []


class GatedFake(FakeProvider):
    """Blocks every call until ``gate`` is set."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        await self.gate.wait()
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


async def test_one_active_job_per_user(photo_jpeg, monkeypatch):
    gated = GatedFake()
    use_provider(monkeypatch, gated)
    async with client() as c:
        await login(c)
        r1 = await _post(c, photo_jpeg)
        assert r1.status_code == 202
        r2 = await _post(c, photo_jpeg)
        assert r2.status_code == 409
        assert "already have a book" in r2.json()["error"]
        # a different user is not blocked
        async with client() as other:
            await login(other, email="other@example.test")
            r3 = await _post(other, photo_jpeg)
            assert r3.status_code == 202
        gated.gate.set()
        await registry.wait(r1.json()["book_id"])
        await registry.wait(r3.json()["book_id"])
        assert (await _post(c, photo_jpeg)).status_code == 202  # free again


PAGE2 = load_story("jack").page(2).caption[:40]


class FailingPageFake(FakeProvider):
    """Hero ok; every page-2 generation fails at once; other pages hang until cancelled."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc
        self.cancelled = 0
        self.page2_calls = 0

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        if len(images) <= 1:  # hero (photo) or family sheet (no refs)
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        if PAGE2 in prompt:
            self.page2_calls += 1
            await asyncio.sleep(0.01)
            raise self.exc
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


@pytest.mark.parametrize(
    ("exc", "message"),
    [(NoImageError("blocked", finish_reason="IMAGE_SAFETY"), "safety"),
     (ProviderError("boom", status=500), "image service failed")],
)
async def test_failure_cancels_remaining_pages(photo_jpeg, monkeypatch, exc, message):
    """Page 2 never produces a picture: 3 generations (the quality-loop maximum), then the
    book fails, the other pages' calls are cancelled and the charge is re-credited."""
    prov = FailingPageFake(exc)
    use_provider(monkeypatch, prov)
    async with client() as c:
        user = await login(c)
        r = await _post(c, photo_jpeg)
        book_id = r.json()["book_id"]
        await registry.wait(book_id, timeout=5)  # far less than the 30 s hang
        assert prov.cancelled == 2
        assert prov.page2_calls == 3

        events = parse_sse((await c.get(f"/api/books/{book_id}/events")).text)
        assert events[-1][0] == "error" and message in events[-1][1]["message"]
        assert [e for e, _ in events].count("page") == 0
        listing = (await c.get("/api/books")).json()
        assert listing[0]["status"] == "failed" and listing[0]["pdf_url"] is None

    async with get_sessionmaker()() as s:
        book = await repo.get_book(s, book_id)
        assert book.status == "failed" and message in book.error
        calls = await repo.list_calls(s, book_id)
        attempts = await repo_assets.list_attempts(s, book_id)
    kinds = sorted((x.kind, x.ok, (x.error or "")[:9]) for x in calls if x.kind == "hero")
    assert kinds == [("hero", True, "")]
    page_calls = [x for x in calls if x.kind == "page"]
    assert len(page_calls) == 5 and not any(x.ok for x in page_calls)
    assert sum(x.error == "cancelled" for x in page_calls) == 2
    assert sorted(x.page for x in page_calls if x.error != "cancelled") == [2, 2, 2]
    assert [(a.attempt, a.error is not None) for a in attempts if a.page == 2] == [
        (1, True), (2, True), (3, True)]
    assert await balance(user["id"]) == 10_000
    assert [e.kind for e in await entries(user["id"])] == ["topup", "charge", "recredit"]


async def test_stale_jobs_marked_failed_on_startup():
    async with get_sessionmaker()() as s:
        user, _ = await repo.upsert_google_user(s, sub="dev:x", email="x@e.test", name="X",
                                                picture=None)
        for bid, status in [("q1", "queued"), ("r1", "running"), ("d1", "done")]:
            s.add(Book(id=bid, user_id=user.id, story="jack", title="T", preset="n64",
                       detail=2, hero_name="A", pronouns="he", favourites={}, about="",
                       family_variant="", page_numbers=[1], status=status, stage="hero",
                       est_cost_usd=0.0, pdf_filename=""))
        await s.commit()
    assert await recover_stale_jobs() == 2
    async with get_sessionmaker()() as s:
        assert (await repo.get_book(s, "q1")).status == "failed"
        assert (await repo.get_book(s, "r1")).error == "Server restarted - please try again."
        assert (await repo.get_book(s, "d1")).status == "done"
        assert await repo.count_active_books(s, user.id) == 0
        assert isinstance(await s.get(User, user.id), User)


async def test_live_events_stream_while_running(photo_jpeg, monkeypatch):
    """Subscribe while the job is blocked, then release it: live events arrive in order."""
    gated = GatedFake()
    use_provider(monkeypatch, gated)
    async with client() as c:
        await login(c)
        book_id = (await _post(c, photo_jpeg)).json()["book_id"]
        get = asyncio.create_task(c.get(f"/api/books/{book_id}/events"))
        for _ in range(100):  # wait until the SSE handler has subscribed
            await asyncio.sleep(0.01)
            if books_api.broker.subscriber_count(book_id):
                break
        gated.gate.set()
        events = parse_sse((await asyncio.wait_for(get, 10)).text)
    names = [e for e, _ in events]
    assert names[0] == "stage"
    assert names.index("hero") < names.index("page") < names.index("done")
    assert names.count("page") == 3 and names[-1] == "done"
    stages = [d["stage"] for e, d in events if e == "stage"]
    assert stages.index("hero") < stages.index("pages") < stages.index("binding") < stages.index(
        "done")
    labels = {d["stage"]: d["label"] for e, d in events if e == "stage"}
    assert labels == {"hero": "Building hero", "pages": "Painting pages",
                      "binding": "Binding the book", "done": "Done"}
    # one progress bar over all pictures: hero + 3 pages
    progress = [(d["done"], d["total"]) for e, d in events if e == "stage"]
    assert all(t == 4 for _, t in progress)
    assert [d for d, _ in progress] == sorted(d for d, _ in progress)
    assert progress[0][0] <= 1 and progress[-1][0] == 4  # snapshot may already see the hero


async def test_orphaned_active_book_does_not_block(photo_jpeg, fake):
    async with client() as c:
        user = await login(c)
        async with get_sessionmaker()() as s:
            s.add(Book(id="orphan", user_id=user["id"], story="jack", title="T", preset="n64",
                       detail=2, hero_name="A", pronouns="he", favourites={}, about="",
                       family_variant="", page_numbers=[1], status="running", stage="pages",
                       est_cost_usd=0.0, pdf_filename=""))
            await s.commit()
        await _create(c, photo_jpeg)
        statuses = {b["book_id"]: b["status"] for b in (await c.get("/api/books")).json()}
    assert statuses["orphan"] == "failed" and list(statuses.values()).count("done") == 1


async def test_delete_running_book_cancels_job(photo_jpeg, monkeypatch):
    gated = GatedFake()
    use_provider(monkeypatch, gated)
    async with client() as c:
        await login(c)
        book_id = (await _post(c, photo_jpeg)).json()["book_id"]
        await asyncio.sleep(0.05)
        assert book_id in registry.tasks
        assert (await c.delete(f"/api/books/{book_id}")).json() == {"ok": True}
        assert book_id not in registry.tasks
        assert (await c.get(f"/api/books/{book_id}")).status_code == 404
        assert gated.calls == []  # the hero call never got past the gate
        user_id = (await c.get("/api/me")).json()["user"]["id"]
        assert await balance(user_id) == 10_000  # deleted while running -> re-credited
        assert (await _post(c, photo_jpeg)).status_code == 202  # user is free again
        gated.gate.set()


async def test_full_book_has_20_pages(photo_jpeg, fake, monkeypatch):
    monkeypatch.delenv("BOOK_PAGES", raising=False)  # default: every page of the story
    from app.config import get_settings

    get_settings.cache_clear()
    async with client() as c:
        await login(c)
        book_id = await _create(c, photo_jpeg, story="red")
        detail = (await c.get(f"/api/books/{book_id}")).json()
        assert detail["page_numbers"] == list(range(1, 21))
        assert [p["page"] for p in detail["pages"]] == list(range(1, 21))
        assert detail["hero_attempts"] == 1 and detail["hero_scores"]["passed"]
        pdf = (await c.get(detail["pdf_url"])).content
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 22  # cover + 20 pages + About Me
    # hero + family sheet (Mother + Grandma) + 3 supporting sheets (wolf plain, wolf in
    # Grandma's disguise, woodcutter) + 20 pages + the wolf's page-3 speaker portrait
    assert len(fake.calls) == 26
    sheet_prompts = [x["prompt"] for x in fake.calls if "family in a children's" in x["prompt"]]
    assert len(sheet_prompts) == 1 and "Mother and Grandma" in sheet_prompts[0]
    async with get_sessionmaker()() as s:
        stored = (await s.execute(select(Book.family_sheet_png).where(
            Book.id == book_id))).scalar_one()
    assert stored and stored.startswith(b"\x89PNG")


async def test_insufficient_funds_is_402_and_starts_nothing(photo_jpeg, fake):
    from tests.conftest import login as plain_login

    async with client() as c:
        await plain_login(c)
        r = await _post(c, photo_jpeg)
        assert r.status_code == 402
        assert r.json()["price_cents"] == 99
        assert (await c.get("/api/books")).json() == []
        assert fake.calls == []
        await login(c)  # funds the same user
        assert (await _post(c, photo_jpeg)).status_code == 202  # not left marked busy


async def test_quality_loop_events_and_persisted_attempts(photo_jpeg, monkeypatch):
    from app.providers.base import GradeResult
    from app.providers.fake_grader import DEFAULT_FACE
    from tests.test_quality import ScriptedGrader

    story = load_story("jack")
    p2 = story.page(2).caption[:30]
    grader = ScriptedGrader({
        p2: [GradeResult(0.4, 0.9, issues=["hair too light"]), GradeResult(0.5, 0.5),
             GradeResult(0.3, 0.3)],
        "hero": [GradeResult(0.2, 0.9), GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])],
    })
    gated = GatedFake()
    use_provider(monkeypatch, gated)
    monkeypatch.setattr(books_api, "get_vision_grader", lambda name=None: grader)
    async with client() as c:
        await login(c)
        book_id = (await _post(c, photo_jpeg)).json()["book_id"]
        with books_api.broker.subscribe(book_id) as q:
            gated.gate.set()
            await registry.wait(book_id)
            live = []
            while not q.empty():
                live.append(q.get_nowait())
        snap = parse_sse((await c.get(f"/api/books/{book_id}/events")).text)

    attempts = [d for e, d in live if e == "attempt"]
    hero_att = [d for d in attempts if d["page"] == 0]
    assert [(d["attempt"], d["phase"]) for d in hero_att] == [
        (1, "generating"), (1, "checking"), (1, "retrying"), (2, "generating"),
        (2, "checking")]
    assert hero_att[2]["likeness"] == 0.2 and hero_att[2]["max_attempts"] == 3
    p2_retry = [d for d in attempts if d["page"] == 2 and d["phase"] == "retrying"]
    assert [d["attempt"] for d in p2_retry] == [1, 2]
    assert p2_retry[0] == {"page": 2, "attempt": 1, "max_attempts": 3, "phase": "retrying",
                           "likeness": 0.4, "fidelity": 0.9, "issues": ["hair too light"]}
    hero_ev = [d for e, d in live if e == "hero"][0]
    assert hero_ev["attempts"] == 2 and hero_ev["scores"] == {
        "likeness": 0.9, "fidelity": 0.9, "passed": True}
    pages = {d["page"]: d for e, d in live if e == "page"}
    assert pages[1]["attempts"] == 1 and pages[3]["attempts"] == 1
    assert pages[2]["attempts"] == 3
    # best of three: 0.65, 0.5, 0.3 -> attempt 1
    assert pages[2]["scores"] == {"likeness": 0.4, "fidelity": 0.9, "passed": False}
    # the snapshot (reconnect) reports the same outcome
    snap_pages = {d["page"]: d for e, d in snap if e == "page"}
    assert snap_pages[2]["attempts"] == 3 and snap_pages[2]["scores"] == pages[2]["scores"]
    assert [d for e, d in snap if e == "hero"][0]["attempts"] == 2

    async with get_sessionmaker()() as s:
        rows = await repo_assets.list_attempts(s, book_id)
        calls = await repo.list_calls(s, book_id)
        page_rows = {p.page: p for p in await repo.list_pages(s, book_id)}
    p2_rows = [(r.attempt, r.chosen, r.passed, r.issues[:1]) for r in rows if r.page == 2]
    assert p2_rows[0] == (1, True, False, ["hair too light"])
    assert [r[:2] for r in p2_rows] == [(1, True), (2, False), (3, False)]
    assert [(r.attempt, r.chosen) for r in rows if r.page == 0] == [(1, False), (2, True)]
    assert page_rows[2].attempts == 3 and page_rows[1].attempts == 1
    assert sum(c.kind == "page" and c.page == 2 for c in calls) == 3
    assert sum(c.kind == "grade" for c in calls) == 2 + 1 + 3 + 1


async def test_restart_sweep_recredits_crashed_books_once():
    from app.billing import wallet
    from app.jobs.runner import recredit_failed_books
    from tests.wallet_helpers import fund

    async with get_sessionmaker()() as s:
        user, _ = await repo.upsert_google_user(s, sub="dev:crash", email="c@e.test",
                                                name="C", picture=None)
        uid = user.id
    await fund(uid, 500)
    for bid, status in [("crashed", "running"), ("finished", "done")]:
        async with get_sessionmaker()() as s:
            s.add(Book(id=bid, user_id=uid, story="jack", title="T", preset="n64", detail=2,
                       hero_name="A", pronouns="he", favourites={}, about="",
                       family_variant="", page_numbers=[1], status=status, stage="pages",
                       est_cost_usd=0.0, pdf_filename=""))
            await s.commit()
            await wallet.charge_document(s, uid, book_id=bid, description="Book")
    assert await balance(uid) == 500 - 2 * 99

    assert await recover_stale_jobs() == 1
    assert await recredit_failed_books() == 1
    async with get_sessionmaker()() as s:
        assert (await repo.get_book(s, "crashed")).status == "failed"
    assert await balance(uid) == 500 - 99  # only the crashed book's charge came back
    assert await recredit_failed_books() == 0  # second run: nothing to do
    assert await balance(uid) == 500 - 99
    kinds = [(e.kind, e.book_id) for e in await entries(uid)]
    assert kinds.count(("recredit", "crashed")) == 1 and ("recredit", "finished") not in kinds
