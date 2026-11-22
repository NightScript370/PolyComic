"""Teen hero wording, the fixed text zone per page number, off-style as a definite error
with a style anchor, the end-to-end clock (request arrival) and early preview events."""

import asyncio
import time

import pytest

import app.api.books as books_api
from app.config import get_settings
from app.jobs import runner
from app.jobs.runner import JobHooks, JobInput
from app.pipeline import quality
from app.pipeline.book import BookGenerator, HeroDetails, plan_book, run_book
from app.pipeline.family import child_version_block, identity_block, resolve_hero_look
from app.pipeline.family_sheet import hero_identity_prompt
from app.pipeline.placement import candidates, place_text
from app.pipeline.prompts import hero_caption, scene_prompt
from app.providers.base import Box, GradeResult, ImageResult, PhotoTraits
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import hero_age_note, traits_note
from app.stories.loader import get_layout, load_story
from app.styles.presets import build_style_string, style_correction
from tests.conftest import client
from tests.test_books_api import FORM, fake, login, three_pages  # noqa: F401
from tests.test_quality import _photo

N64 = build_style_string("n64", 2)
TEEN = PhotoTraits(3, "dark_brown", "straight", age_group="teen",
                   hair_style="short, tousled, side parting")
ADULT = PhotoTraits(3, "black", "curly", age_group="adult")
CHILD = PhotoTraits(4, "black", "straight", age_group="child")
MIA = HeroDetails("Mia", "she")


# --- 1. teens drawn at their age -----------------------------------------------------------


def test_resolve_hero_look_entered_age_wins():
    assert resolve_hero_look(ADULT, 15).teen and resolve_hero_look(ADULT, 15).age == 15
    assert not resolve_hero_look(TEEN, 9).teen  # entered 9 beats a teen-looking photo
    assert resolve_hero_look(TEEN, None).teen  # no age: the detected group decides
    assert not resolve_hero_look(ADULT, None).teen  # adults get the child version
    assert not resolve_hero_look(None, None).teen
    assert resolve_hero_look(None, 13).teen and not resolve_hero_look(None, 12).teen


def test_teen_hero_prompt_says_teenager_and_keeps_hair():
    look = resolve_hero_look(TEEN, 15)
    p = hero_identity_prompt(N64, load_story("jack"), TEEN, look=look)
    assert "TEENAGER of about 15" in p and "TEEN VERSION" in p
    assert "about 7 to 9" not in p and "CHILD VERSION" not in p
    assert "short, tousled, side parting" in p  # hair length / style from the photo
    assert "Hair length and style exactly as photographed" in p
    assert "teenager about 15" in hero_caption(look)
    assert "7 to 9" not in hero_caption(look)


def test_adult_photo_still_gets_the_child_version_and_entered_teen_age_overrides():
    block = child_version_block(ADULT, resolve_hero_look(ADULT, None))
    assert "CHILD VERSION" in block and "7 to 9" in block
    assert "TEEN VERSION" in child_version_block(ADULT, resolve_hero_look(ADULT, 16))
    assert child_version_block(CHILD, resolve_hero_look(CHILD, None)) == ""
    assert "The teenager has" in identity_block(TEEN, resolve_hero_look(TEEN, 15))


def test_teen_page_prompt_and_grader_wording():
    story = load_story("jack")
    page = story.pages[3]
    look = resolve_hero_look(TEEN, 15)
    p = scene_prompt(N64, page, story, hero_look=look)
    assert "TEENAGER from the hero reference (about 15" in p
    assert "about 7 to 9 years old" not in p
    assert "about 7 to 9 years old" in scene_prompt(N64, page, story)
    note = hero_age_note(look.phrase)
    assert "teenager about 15" in note and "hair mismatch" in note
    assert "teenager about 15" in traits_note(TEEN, look.phrase)
    assert "child version" not in traits_note(TEEN, look.phrase)
    assert hero_age_note("") == ""


def test_teen_check_correction_names_teenager():
    g = GradeResult(0.9, 0.9, hero_child_match=False)
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=TEEN,
                           hero_look=resolve_hero_look(TEEN, 15))
    assert not chk.passed and chk.definite
    assert any("TEENAGER" in i and "about 15" in i for i in chk.issues)


async def test_book_sends_teen_wording_to_provider_and_grader():
    seen: list[str] = []

    class Rec(FakeProvider):
        async def edit(self, prompt, images, **kw):
            seen.append(prompt)
            return await super().edit(prompt, images, **kw)

    grader = FakeGrader(traits=TEEN)
    await run_book(Rec(), await _photo(), "n64", 2, story_id="jack", pages=[4],
                   hero=HeroDetails("Mia", "she", age=15), grader=grader)
    joined = "\n".join(seen)
    assert "TEENAGER" in joined and "about 7 to 9 years old" not in joined
    graded = [c for c in grader.calls if c.subject in ("hero", "page")]
    assert graded and all(c.hero_age == "a teenager about 15 years old" for c in graded)


# --- 2. fixed text zone per page number ------------------------------------------------------


def test_every_page_number_has_one_shared_layout_and_first_zone():
    jack, red = load_story("jack"), load_story("red")
    for pj, pr in zip(jack.pages, red.pages, strict=True):
        assert pj.page == pr.page and pj.layout == pr.layout
        lay = get_layout(pj.layout)
        assert candidates(lay, fixed=True) == [candidates(lay)[0]]
        assert place_text(lay, fixed=True).zone == place_text(
            get_layout(pr.layout), fixed=True).zone


def test_fixed_zone_never_moves_and_blocked_names_the_blocker():
    s01 = get_layout("s01_home_two_shot")  # bottom first, then top
    assert place_text(s01).zone.zone == "bottom"
    face_bottom = Box("hero face", 0.4, 0.8, 0.1, 0.1, "face")
    assert place_text(s01, [face_bottom]).zone.zone == "top"  # legacy: moves
    p = place_text(s01, [face_bottom], fixed=True)
    assert p.zone.zone == "bottom" and p.blocked and p.blocker_phrase == "every face"
    assert "bottom fifth" in p.issue()
    harp = Box("golden harp", 0.3, 0.78, 0.2, 0.1, "key_object")
    p = place_text(s01, [harp], fixed=True)
    assert p.zone.zone == "bottom" and p.blocker_phrase == "the golden harp"
    assert not place_text(s01, [Box("hero face", 0.4, 0.3, 0.2, 0.2, "face")],
                          fixed=True).blocked


def test_prompt_names_the_fixed_zone_every_time():
    story = load_story("red")
    assert "always printed in the bottom fifth" in scene_prompt(
        N64, story.pages[0], story, text_zone="bottom")
    assert "always printed in the top fifth" in scene_prompt(
        N64, story.pages[3], story, text_zone="top")


async def test_blocked_fixed_zone_is_regenerated_then_kept(caplog):
    blocked = Box("hero face", 0.4, 0.8, 0.1, 0.1, "face")  # inside the bottom zone
    seen: list[str] = []

    def fn(call, n):
        if call.subject == "hero":
            return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])
        return GradeResult(0.9, 0.9, boxes=[blocked])

    class Rec(FakeProvider):
        async def edit(self, prompt, images, **kw):
            seen.append(prompt)
            return await super().edit(prompt, images, **kw)

    caplog.set_level("WARNING")
    book = await run_book(Rec(), await _photo(), "n64", 2, story_id="jack", pages=[1],
                          hero=MIA, grader=FakeGrader(fn=fn), time_budget_s=0)
    pg = book.pages[0]
    assert pg.zone.zone == "bottom"  # never moved to "top"
    assert pg.attempts == 3
    page_prompts = [p for p in seen if "Task: illustrate one picture-book page" in p]
    assert "always printed in the bottom fifth" in page_prompts[0]
    assert "IMPORTANT: the bottom fifth" in page_prompts[1]  # corrective retry
    assert "FIXED text zone" in caplog.text


# --- 3. off-style = definite error + style anchor ------------------------------------------


def test_off_style_is_a_definite_error_with_the_preset_restated():
    g = GradeResult(0.9, 0.9, style_match=False,
                    style_notes="flat 2D painted illustration; the book is low-poly 3D")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6,
                           style_hint=style_correction(N64))
    assert not chk.passed and chk.definite
    assert chk.issues[0] == style_correction(N64)
    assert "WHOLE picture" in chk.issues[0]
    assert g.hard_failures >= 1


async def test_off_style_page_retries_with_a_style_anchor():
    seen: list[str] = []
    graded: dict[str, int] = {}
    story = load_story("jack")
    marker = story.page(5).scene

    class Rec(FakeProvider):
        async def edit(self, prompt, images, **kw):
            seen.append(prompt)
            return await super().edit(prompt, images, **kw)

    def fn(call, n):
        if call.subject == "hero" or marker not in call.caption:
            return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])
        graded[marker] = graded.get(marker, 0) + 1  # page 5: off-style on attempt 1 only
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], style_match=graded[marker] > 1)

    plan = plan_book("jack", [4, 5], "n64", 2)
    gen = BookGenerator(plan, MIA, await _photo(), hero_provider=Rec(),
                        grader=FakeGrader(fn=fn), time_budget_s=0, hard_cap_s=0)
    book = await gen.run()
    assert book.pages[1].attempts == 2
    anchored = [p for p in seen if "STYLE ANCHOR" in p]
    assert len(anchored) == 1 and marker in anchored[0]
    assert gen._style_anchor is not None


# --- 4. end-to-end clock + cap ---------------------------------------------------------------


def test_default_caps_leave_room_for_the_pdf():
    s = get_settings()
    assert s.quality_hard_cap_s <= 47 and s.quality_time_budget_s < s.quality_hard_cap_s
    assert s.book_time_target_s <= 55
    assert s.book_time_target_s - s.quality_hard_cap_s >= 8  # PDF + persist + 'done'


async def test_job_clock_starts_at_the_request_not_the_job(monkeypatch):
    got: dict = {}

    class Stub:
        max_attempts = 3

        def __init__(self, plan, hero, photo, **kw):
            got.update(kw)
            self.hooks = None

        async def run(self):
            raise asyncio.CancelledError

    monkeypatch.setattr(runner, "BookGenerator", Stub)
    plan = plan_book("jack", [1], "n64", 2)
    requested = time.monotonic() - 12.5  # the request came in 12.5 s before the job ran
    job = JobInput(book_id="b1", user_id=1, plan=plan, hero=MIA, photo=b"x",
                   requested_at=requested)
    with pytest.raises(asyncio.CancelledError):
        await runner.BookJob(job, FakeProvider()).run()
    assert got["started_at"] == requested
    job2 = JobInput(book_id="b2", user_id=1, plan=plan, hero=MIA, photo=b"x")
    before = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await runner.BookJob(job2, FakeProvider()).run()
    assert got["started_at"] >= before


async def test_post_books_captures_the_request_time(monkeypatch, photo_jpeg, fake):  # noqa: F811
    jobs: list[JobInput] = []

    def grab(job, providers):
        jobs.append(job)
        return asyncio.ensure_future(asyncio.sleep(0))

    monkeypatch.setattr(books_api, "start_book_job", grab)
    t0 = time.monotonic()
    async with client() as c:
        await login(c)
        r = await c.post("/api/books", data=FORM,
                         files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")})
    assert r.status_code == 202
    assert t0 <= jobs[0].requested_at <= time.monotonic()
    runner.registry.by_user.clear()  # the patched start never registered a task


async def test_retries_stop_before_the_end_to_end_cap():
    t = {"now": 0.0}

    def fn(call, n):
        return GradeResult(0.3, 0.9, boxes=[DEFAULT_FACE])  # always a (soft) failure

    plan = plan_book("jack", [4], "n64", 2)
    gen = BookGenerator(plan, MIA, await _photo(), hero_provider=FakeProvider(),
                        grader=FakeGrader(fn=fn), time_budget_s=1000, hard_cap_s=46,
                        clock=lambda: t["now"], started_at=0.0)
    t["now"] = 44.0  # the request arrived 44 s ago: one more cycle would end past 46 s
    book = await gen.run()
    assert book.pages[0].attempts == 1 and book.pages[0].budget_stopped


# --- 5. early thumbnails -----------------------------------------------------------------------


def _attempt(n, passed, likeness=0.9):
    chk = quality.Check(passed=passed, grade=GradeResult(likeness, 0.9, boxes=[DEFAULT_FACE]))
    return quality.Attempt(n, image=ImageResult(png=b"x", model="m"), check=chk)


def test_preview_events_first_attempt_then_only_when_replaced(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(runner.broker, "publish", lambda b, e, d: events.append((e, d)))
    plan = plan_book("jack", [4], "n64", 2)
    hooks = JobHooks(JobInput("bk", 1, plan, MIA, b"x"), 2, 3)
    hooks.preview(4, _attempt(1, False, 0.5))  # failed, but the best so far: preview
    hooks.preview(4, _attempt(2, False, 0.3))  # worse: nothing new
    hooks.preview(4, _attempt(3, True))  # passes: replaces the preview
    hooks.preview(0, _attempt(1, True))  # the hero has its own event
    assert [(e, d["attempt"], d["passed"]) for e, d in events] == [
        ("preview", 1, False), ("preview", 3, True)]
    assert events[0][1]["thumb_url"] == "/api/books/bk/pages/4/attempts/1/thumb.jpg"


async def test_attempt_thumb_endpoint(photo_jpeg, fake):  # noqa: F811
    async with client() as c:
        await login(c)
        r = await c.post("/api/books", data=FORM,
                         files={"photo": ("kid.jpg", photo_jpeg, "image/jpeg")})
        assert r.status_code == 202, r.text
        book_id = r.json()["book_id"]
        await runner.registry.wait(book_id)
        r = await c.get(f"/api/books/{book_id}/pages/1/attempts/1/thumb.jpg")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        r = await c.get(f"/api/books/{book_id}/pages/1/attempts/9/thumb.jpg")
        assert r.status_code == 404
