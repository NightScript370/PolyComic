"""Retry phase after the first pass, the free strip rescue, the hood-down rule and the
fleeing-direction wording (real Cost-Efficient runs: Jack $0.87 / Red $0.88 with zero
retries although both ended ~$0.10 under the $0.97 cap)."""

import asyncio
import io

import pytest
from PIL import Image

from app.config import get_settings
from app.db.models import PageAttempt
from app.pipeline import quality
from app.pipeline.bands import Band, find_blank_band
from app.pipeline.book import (
    TIER_DEFINITE,
    TIER_PORTRAIT,
    TIER_SOFT,
    BookGenerator,
    Hooks,
    RetryRequest,
    RetryScheduler,
    plan_book,
    run_book,
)
from app.pipeline.calls import CALL_KINDS, CallCost
from app.pipeline.family import kept_items_rule
from app.pipeline.prompts import hero_items_rule, scene_prompt
from app.pipeline.rescue import plan_rescue, rescue_strips
from app.providers.base import Box, CallMeta, GradeResult, PhotoTraits
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import GRADE_INSTRUCTIONS, _Grade
from app.stories.loader import load_story
from app.styles.presets import build_style_string
from tests.test_budget_bands import png, with_band
from tests.test_cast_costume_expression import padded
from tests.test_quality import HERO, _photo

N64 = build_style_string("n64", 2)
INSTR = " ".join(GRADE_INSTRUCTIONS.split())
PAGE_TASK = "Task: illustrate one picture-book page"


# --- 2. free strip rescue (synthetic images) -------------------------------------------------


@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_rescue_crops_the_strip_and_keeps_the_page_size(edge):
    src = with_band(edge, 0.15)
    band = find_blank_band(src)
    assert band is not None and band.edge == edge
    res = rescue_strips(src, band, find_blank_band)
    assert res.png is not None and res.why == ""
    assert find_blank_band(res.png) is None
    assert Image.open(io.BytesIO(res.png)).size == (480, 640)
    step = res.steps[0]
    assert step.edge == edge and 0.15 < step.cut < 0.18 and step.crop == pytest.approx(
        step.cut, abs=0.01)


@pytest.mark.parametrize("kind", ["blurred_copy", "wall_and_white", "sky_strip"])
def test_rescue_handles_real_padding_incl_both_edges(kind):
    src = png(padded(kind))
    band = find_blank_band(src)
    res = rescue_strips(src, band, find_blank_band)
    assert res.png is not None and find_blank_band(res.png) is None
    if kind == "wall_and_white":  # top AND bottom padded: two passes
        assert {s.edge for s in res.steps} == {"top", "bottom"}


def test_rescue_limits_fall_back_to_a_paid_retry():
    deep = with_band("top", 0.25)
    res = rescue_strips(deep, find_blank_band(deep), find_blank_band)
    assert res.png is None and "deeper than 22%" in res.why
    ok = with_band("top", 0.18)
    res = rescue_strips(ok, find_blank_band(ok), find_blank_band, max_crop=0.10)
    assert res.png is None and "aspect crop" in res.why
    assert get_settings().strip_rescue_max_depth == 0.22
    assert get_settings().strip_rescue_max_crop == 0.30


def test_rescue_crop_centres_on_the_faces():
    band = Band("top", 0.15, (0, 0, 0), "seam")
    centred = plan_rescue((480, 640), band)
    x0, _, x1, _ = centred.box
    assert abs(x0 - (480 - x1)) <= 1  # no boxes: centred
    right = plan_rescue((480, 640), band, [Box("hero face", 0.80, 0.4, 0.15, 0.1, "face")])
    assert right.box[2] == 480  # the face at the right edge stays in
    left = plan_rescue((480, 640), band, [Box("hero body", 0.0, 0.3, 0.2, 0.6, "figure")])
    assert left.box[0] == 0
    assert plan_rescue((480, 640), Band("left", 0.30, (0, 0, 0))) is None


class BandFirst(FakeProvider):
    """The first page picture has a padded strip at the top (``fraction`` deep)."""

    def __init__(self, fraction=0.15):
        super().__init__()
        self.fraction = fraction
        self.page_prompts: list[str] = []

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        res = await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        if prompt.startswith(PAGE_TASK):
            self.page_prompts.append(prompt)
            if len(self.page_prompts) == 1:
                res.png = with_band("top", self.fraction)
        return res


class AttemptHooks(Hooks):
    def __init__(self):
        self.attempts: list[quality.Attempt] = []

    async def attempt_done(self, page, attempt):
        if page:
            self.attempts.append(attempt)


async def test_book_rescues_a_padded_page_with_one_grade_and_no_image_call():
    provider, grader, hooks = BandFirst(), FakeGrader(), AttemptHooks()
    book = await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4],
                          hero=HERO, grader=grader, hooks=hooks)
    page = book.pages[0]
    assert page.attempts == 1 and page.scores["passed"]
    assert len(provider.page_prompts) == 1  # no new image call
    kinds = [c.kind for c in book.costs if c.page == 4]
    assert kinds == ["page", "rescue_grade"]
    assert hooks.attempts[0].rescued and find_blank_band(page.png) is None
    assert book.rescues and book.rescues[0].startswith("p4 a1 blank strip top")
    assert "rescue_grade" in CALL_KINDS


async def test_failed_rescue_falls_back_to_a_paid_retry():
    def fn(call, n):
        meta = CallMeta(model="fake-vision")
        if call.subject == "page" and n == 1:  # the rescue's grade: a face cut off
            return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], meta=meta, contradictions=[
                "show the hero's whole face inside the frame - it is cut off at the left"])
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], meta=meta)

    provider, hooks = BandFirst(), AttemptHooks()
    book = await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4],
                          hero=HERO, grader=FakeGrader(fn=fn), hooks=hooks)
    page = book.pages[0]
    assert page.attempts == 2 and page.chosen_attempt == 2
    assert hooks.attempts[0].rescued and not hooks.attempts[0].passed
    # the paid retry is told not to pad again
    assert "no empty band at the top" in provider.page_prompts[1]


async def test_too_deep_strip_is_not_rescued():
    provider = BandFirst(0.30)
    book = await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4],
                          hero=HERO, grader=FakeGrader())
    assert book.pages[0].attempts == 2
    assert not any(c.kind == "rescue_grade" for c in book.costs)
    assert "not rescued" in book.rescues[0]


def test_rescued_attempt_column_and_migration():
    import importlib.util
    from pathlib import Path

    assert "rescued" in PageAttempt.__table__.columns
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0012_attempt_rescued.py"
    spec = importlib.util.spec_from_file_location("m0012", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0012" and mod.down_revision == "0011"


# --- 1. retry phase -------------------------------------------------------------------------


class _Priced(FakeProvider):
    """Every image call costs $0.03; optionally the second page picture is slow."""

    def __init__(self, log=None, slow_second=False):
        super().__init__()
        self.log = log if log is not None else []
        self.slow_second = slow_second
        self.n = 0

    async def edit(self, prompt, images, **kw):
        if prompt.startswith(PAGE_TASK):
            self.n += 1
            n = self.n
            if self.slow_second and n == 2:
                await asyncio.sleep(0.15)
            self.log.append(("image", n))
        r = await super().edit(prompt, images, **kw)
        r.est_cost_usd = 0.03
        return r


def _grades(failing: dict[str, list[GradeResult]], log=None):
    """Grade per page caption prefix (a list consumed in order; the last repeats); hero
    and other pages pass. Every grade costs $0.005."""
    def fn(call, n):
        meta = CallMeta(model="fake-vision", est_cost_usd=0.005)
        for key, seq in failing.items():
            if call.subject == "page" and call.caption.startswith(key):
                g = seq.pop(0) if len(seq) > 1 else seq[0]
                g.meta = meta
                if log is not None:
                    log.append(("grade", key))
                return g
        if log is not None and call.subject == "page":
            log.append(("grade", call.caption[:20]))
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], meta=meta)
    return fn


def _definite(n=1):
    return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE],
                       contradictions=[f"show exactly five beans {i}" for i in range(n)])


async def _cap_book(cap: float, seed: float = 0.10):
    story = load_story("jack")
    key = story.page(4).scene[:40]
    plan = plan_book("jack", [4, 5, 6], "n64", 2)  # no family sheet, no cast sheets
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=_Priced(),
                        grader=FakeGrader(fn=_grades({key: [_definite()]})),
                        time_budget_s=0, cost_cap_usd=cap)
    gen.image_cost_est = seed  # a conservative pricing seed (actual: $0.03)
    return gen, await gen.run()


async def test_retry_phase_uses_actual_costs_and_fills_the_cap():
    # required: hero 0.035 + 3 pages x 0.035 = 0.14; two retries of page 4: +0.07 = 0.21.
    # Before: the retry was decided while two pages were still reserved at the seed
    # (2 x 0.105) and skipped although the book ended far under the cap.
    gen, book = await _cap_book(0.21)
    page4 = {p.page: p for p in book.pages}[4]
    assert page4.attempts == 3 and not page4.cost_stopped
    assert book.cost_skips == []
    assert gen.image_cost_est == pytest.approx(0.03)  # the seed is replaced by actuals
    assert book.est_cost_usd <= 0.21 + 1e-9


async def test_retry_phase_never_passes_the_cap_and_reports_the_skip():
    gen, book = await _cap_book(0.20)
    page4 = {p.page: p for p in book.pages}[4]
    assert page4.attempts == 2 and page4.cost_stopped and not page4.budget_stopped
    assert book.cost_skips == ["page 4 retry (definite error)"]
    assert book.est_cost_usd <= 0.20 + 1e-9


async def test_retries_wait_for_every_first_picture():
    story = load_story("jack")
    key = story.page(4).scene[:40]
    events: list = []
    provider = _Priced(log=events, slow_second=True)
    await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4, 5],
                   hero=HERO, grader=FakeGrader(fn=_grades(
                       {key: [_definite(), GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])]},
                       log=events)))
    images = [i for i, e in enumerate(events) if e[0] == "image"]
    grades = [i for i, e in enumerate(events) if e[0] == "grade"]
    assert len(images) == 3 and len(grades) == 3
    # the retry (3rd image) starts only after BOTH first grades
    assert images[2] > grades[1]


async def test_early_band_retry_starts_during_the_first_pass(monkeypatch):
    class Slow2Band1(_Priced):
        async def edit(self, prompt, images, **kw):
            if prompt.startswith(PAGE_TASK):
                self.n += 1
                n = self.n
                if n == 2:
                    await asyncio.sleep(0.3)  # page 2's first picture is slow
                self.log.append(("image", n))
                r = await FakeProvider.edit(self, prompt, images, **kw)
                if n == 1:
                    r.png = with_band("top", 0.30)  # not rescuable: needs a new picture
                r.est_cost_usd = 0.03
                return r
            return await super().edit(prompt, images, **kw)

    async def run(early: bool):
        monkeypatch.setenv("QUALITY_EARLY_BAND_RETRY", "true" if early else "false")
        get_settings.cache_clear()
        events: list = []
        provider = Slow2Band1(log=events)
        await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4, 5],
                       hero=HERO, grader=FakeGrader(fn=_grades({}, log=events)))
        return events

    try:
        events = await run(True)
        # image 3 (page 1's retry) is made before page 2's (slow) first picture is graded
        assert events.index(("image", 3)) < events.index(("image", 2))
        events = await run(False)
        assert events.index(("image", 3)) > events.index(("image", 2))
    finally:
        monkeypatch.delenv("QUALITY_EARLY_BAND_RETRY", raising=False)
        get_settings.cache_clear()


def _gen(cap: float = 0.0) -> BookGenerator:
    plan = plan_book("jack", [4], "n64", 2)
    gen = BookGenerator(plan, HERO, b"x", hero_provider=FakeProvider(), grader=FakeGrader(),
                        cost_cap_usd=cap)
    gen.image_cost_est, gen.grade_cost_est = 0.03, 0.01
    return gen


async def test_scheduler_order_definite_worst_first_then_portraits_then_soft():
    gen = _gen()
    sched = gen.scheduler
    order: list[int] = []

    async def ask(req: RetryRequest):
        t = await sched.admit(req)
        order.append(req.page)
        await t.decided(False)

    reqs = [RetryRequest(1, 2, TIER_SOFT, score=0.1),
            RetryRequest(2, 1, TIER_PORTRAIT),
            RetryRequest(3, 2, TIER_DEFINITE, score=0.6, severity=1),
            RetryRequest(4, 2, TIER_DEFINITE, score=0.7, severity=3),
            RetryRequest(5, 3, TIER_DEFINITE, score=-1.0, severity=3),
            RetryRequest(6, 2, TIER_DEFINITE, score=0.2, severity=1)]
    tasks = [asyncio.create_task(ask(r)) for r in reqs]
    await asyncio.sleep(0.01)
    assert order == []  # the first pass (hero + page 4) is not done
    gen.first_done(0)
    gen.first_done(4)
    await asyncio.gather(*tasks)
    # definite: 2nd attempts before 3rd, most failures then lowest score; then portraits
    assert order == [4, 6, 3, 5, 2, 1]


async def test_head_waits_for_running_retries_instead_of_being_skipped():
    gen = _gen(0.10)
    gen._pending = set()
    gen.costs = [CallCost("page", 4, "m", 0.03, 1.0, 0, 0)]
    sched: RetryScheduler = gen.scheduler
    a = await sched.admit(RetryRequest(4, 2, TIER_DEFINITE))
    await a.decided(True)  # running: 0.04 reserved
    b_task = asyncio.create_task(sched.admit(RetryRequest(7, 2, TIER_DEFINITE)))
    await asyncio.sleep(0.01)
    assert not b_task.done()  # 0.03 + 0.04 + 0.04 > 0.10: wait, do not skip
    gen.costs += [CallCost("page", 4, "m", 0.02, 1.0, 0, 0),
                  CallCost("grade", 4, "m", 0.005, 1.0, 0, 0)]  # cheaper than reserved
    await a.finished()
    b = await asyncio.wait_for(b_task, 1)
    assert gen.afford(gen.cycle_cost(), "b", definite=True)  # 0.055 + 0.04 <= 0.10
    await b.decided(True)
    assert gen.cost_skips == []


# --- 3. hood down -----------------------------------------------------------------------------


def test_red_hood_stays_down_on_every_page():
    red = load_story("red")
    assert "hood always DOWN on the shoulders (never up over the head)" in red.hero_outfit_core
    assert "never pulled up over the head" in red.hero_outfit
    for page in red.pages:
        assert "hood DOWN on the shoulders, never up over the head" in " ".join(
            page.hero_instruction.split()), page.page
    p11 = scene_prompt(N64, red.page(11), red, hero_items="round glasses and a red cap")
    assert "A hood always stays DOWN on the shoulders" in p11
    assert "never pulled up over the head" in hero_items_rule("a red cap")
    rule = kept_items_rule(PhotoTraits(3, "black", "curly", headwear="a red cap"))
    assert "always stays DOWN on the shoulders, never up over the head" in rule


def test_hood_up_hiding_the_headwear_is_a_definite_identity_error():
    assert "a HOOD pulled UP over the head so that the listed headwear" in INSTR
    assert "hood pulled up over the head hides" in _Grade.model_fields["hero_items_ok"].description
    g = GradeResult(0.8, 0.8, boxes=[DEFAULT_FACE], hero_items_match=False,
                    hero_look_notes="the hood is up and hides the red cap")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    assert not chk.passed and chk.definite
    assert quality.HOOD_FIX in chk.issues


# --- 4. fleeing direction ---------------------------------------------------------------------


def test_wolf_runs_away_from_the_cottage_on_red_17_and_18():
    red = load_story("red")
    p18 = " ".join(red.page(18).scene.split())
    assert "TINY figure near the horizon" in p18 and "seen from behind" in p18
    assert "AWAY from the cottage" in p18 and "never on the near hill" in p18
    assert "seen from behind" in " ".join(red.page(18).plate_prompt.split())
    p17 = " ".join(red.page(17).scene.split())
    assert "dashes AWAY from them" in p17 and "never toward them" in p17
    assert "back and tail toward them" in " ".join(red.page(17).plate_prompt.split())
    # the texts the reader sees are unchanged
    assert red.page(18).text.startswith("Over the hills and far away ran the wolf")
    jack = load_story("jack")
    assert "fleeing AWAY from the giant and the castle" in " ".join(
        jack.page(15).scene.split())
    assert "no giant on the beanstalk" in " ".join(jack.page(18).scene.split())


def test_grader_contradictions_for_cut_faces_and_fleeing_direction():
    contra = INSTR[INSTR.index("contradictions: DEFINITE"):INSTR.index("These do NOT count")]
    assert "cut off by the edge of the picture so that their FACE is lost" in contra
    assert "flees or runs AWAY drawn facing or heading TOWARD what they flee from" in contra
    assert "close up / large in the foreground when the description says they are small" \
        in contra
    desc = _Grade.model_fields["contradictions"].description
    assert "face is cut off by the frame edge" in desc and "fleeing" in desc
