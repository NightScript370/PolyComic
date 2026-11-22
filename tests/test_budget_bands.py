"""Quality-loop time budget, blank-band check, figure boxes / middle-zone rule, calibrated
contradictions, grader input downscaling and thinking level, and the p3/p10 page fixes."""

import io

import pytest
from google.genai import errors as genai_errors
from PIL import Image

from app.config import Settings, get_settings
from app.pipeline import quality
from app.pipeline.bands import MIN_FRACTION, band_issue, find_blank_band
from app.pipeline.book import HERO_MIN_ATTEMPTS, BookGenerator, Hooks, plan_book, run_book
from app.pipeline.placement import is_region, place_text
from app.pipeline.prompts import PAINT_EDGE_TO_EDGE, quiet_zone_rule, scene_prompt
from app.providers.base import Box, GradeResult, ImageResult, ProviderError
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import (
    GRADE_INSTRUCTIONS,
    GeminiVisionGrader,
    _Box,
    _boxes,
    _Grade,
    shrink_for_grader,
)
from app.stories.loader import get_layout, load_story
from app.styles.presets import build_style_string
from tests.test_quality import HERO, ScriptedGrader, _photo

# --- helpers --------------------------------------------------------------------------------


def png(im: Image.Image, fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    im.save(out, format=fmt)
    return out.getvalue()


def noisy(size=(480, 640)) -> Image.Image:
    """A 'painted' picture: noise everywhere, so no edge line is flat."""
    return Image.effect_noise(size, 40).convert("RGB")


def with_band(edge: str, fraction: float, colour=(245, 235, 215), size=(480, 640)) -> bytes:
    im = noisy(size)
    w, h = size
    box = {"top": (0, 0, w, int(h * fraction)), "bottom": (0, h - int(h * fraction), w, h),
           "left": (0, 0, int(w * fraction), h), "right": (w - int(w * fraction), 0, w, h)}
    im.paste(colour, box[edge])
    return png(im)


def figure(x, y, w, h, label="hero body"):
    return Box(label, x, y, w, h, "figure")


def face(x, y, w, h, label="hero face"):
    return Box(label, x, y, w, h, "face")


def img(tag: str) -> ImageResult:
    return ImageResult(png=tag.encode(), model="m")


async def fail_check(n, image):
    return quality.evaluate(GradeResult(0.3, 0.3 + n / 100), min_likeness=0.6,
                            min_fidelity=0.6)


# --- (e) time budget: the loop ----------------------------------------------------------------


async def test_budget_stops_new_retries_and_keeps_best():
    phases, made = [], []

    async def generate(n, corrections, quiet):
        made.append(n)
        return img(f"a{n}")

    async def on_phase(n, phase, chk):
        phases.append((n, phase))

    res = await quality.run_quality_loop(generate=generate, check=fail_check, max_attempts=3,
                                         on_phase=on_phase, budget_exhausted=lambda: True)
    assert made == [1] and res.count == 1 and res.budget_stopped
    assert res.chosen.n == 1 and not res.chosen.passed
    assert phases == [(1, "generating"), (1, "checking"), (1, quality.STOPPED)]


async def test_running_generation_finishes_after_the_deadline():
    state = {"out": False}

    async def generate(n, corrections, quiet):
        if n == 2:
            state["out"] = True  # the budget runs out while attempt 2 is being generated
        return img(f"a{n}")

    checked = []

    async def check(n, image):
        checked.append(n)
        return await fail_check(n, image)

    res = await quality.run_quality_loop(generate=generate, check=check, max_attempts=3,
                                         budget_exhausted=lambda: state["out"])
    assert res.count == 2 and checked == [1, 2] and res.budget_stopped
    assert res.chosen.n == 2  # best of the two made (higher fidelity)


async def test_no_budget_keeps_the_three_generation_spec():
    res = await quality.run_quality_loop(
        generate=lambda n, c, q: _ret(img(f"a{n}")), check=fail_check, max_attempts=3,
        budget_exhausted=lambda: False)
    assert res.count == 3 and not res.budget_stopped
    res = await quality.run_quality_loop(
        generate=lambda n, c, q: _ret(img(f"a{n}")), check=fail_check, max_attempts=3)
    assert res.count == 3 and not res.budget_stopped


async def _ret(v):
    return v


async def test_budget_min_attempts_and_needs_a_picture_first():
    res = await quality.run_quality_loop(
        generate=lambda n, c, q: _ret(img(f"a{n}")), check=fail_check, max_attempts=3,
        budget_exhausted=lambda: True, min_attempts=2)
    assert res.count == 2 and res.budget_stopped

    async def first_fails(n, corrections, quiet):
        if n == 1:
            raise ProviderError("boom", status=500)
        return img(f"a{n}")

    # no picture yet: the budget does not stop the retry (the book would fail otherwise)
    res = await quality.run_quality_loop(generate=first_fails, check=fail_check,
                                         max_attempts=3, budget_exhausted=lambda: True)
    assert res.count == 2 and res.chosen.n == 2 and res.budget_stopped


# --- (e) time budget: the book (fake clock) -----------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class SlowFake(FakeProvider):
    """Every image call takes 10 s of fake time."""

    def __init__(self, clock: Clock):
        super().__init__()
        self.clock = clock

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        self.clock.t += 10.0
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


class BudgetHooks(Hooks):
    def __init__(self):
        self.stopped: list[tuple[int, int]] = []
        self.phases: list[tuple[int, int, str]] = []

    async def attempt_phase(self, page, attempt, phase, check):
        self.phases.append((page, attempt, phase))

    async def budget_stopped(self, page, chosen):
        self.stopped.append((page, chosen.n))


async def test_book_budget_hero_gets_two_attempts_pages_stop_after_one():
    clock = Clock()
    provider = SlowFake(clock)
    always_fail = GradeResult(0.2, 0.2, boxes=[DEFAULT_FACE])
    story = load_story("jack")
    grader = ScriptedGrader({"hero": [always_fail], story.page(1).caption[:30]: [always_fail],
                             story.page(2).caption[:30]: [always_fail]})
    hooks = BudgetHooks()
    plan = plan_book("jack", [1, 2, 4], "n64", 2)
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=provider, grader=grader,
                        hooks=hooks, time_budget_s=5.0, hard_cap_s=70, clock=clock)
    book = await gen.run()
    assert HERO_MIN_ATTEMPTS == 2
    assert book.hero_attempts == 2  # budget gone after attempt 1, the hero still retries once
    pages = {p.page: p for p in book.pages}
    assert pages[1].attempts == 1 and pages[1].budget_stopped
    assert pages[2].attempts == 1 and pages[2].budget_stopped
    assert pages[4].attempts == 1 and not pages[4].budget_stopped  # passed first time
    assert sorted(hooks.stopped) == [(0, 1), (1, 1), (2, 1)]  # (picture, kept attempt)
    assert (0, 2, quality.STOPPED) in hooks.phases and (1, 1, quality.STOPPED) in hooks.phases
    # the attempt count is honest: image calls made == attempts reported
    assert sum(c.kind == "page" for c in book.costs) == 3
    assert sum(c.kind == "hero" for c in book.costs) == 2


async def test_book_budget_not_reached_gives_three_attempts():
    clock = Clock()
    story = load_story("jack")
    always_fail = GradeResult(0.2, 0.2, boxes=[DEFAULT_FACE])
    grader = ScriptedGrader({story.page(1).caption[:30]: [always_fail]})
    plan = plan_book("jack", [1], "n64", 2)
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=SlowFake(clock),
                        grader=grader, time_budget_s=1000.0, hard_cap_s=0, clock=clock)
    book = await gen.run()
    assert book.pages[0].attempts == 3 and not book.pages[0].budget_stopped


def test_budget_settings_defaults(monkeypatch):
    s = Settings()
    assert s.quality_time_budget_s == 36.0 and s.book_time_target_s == 55.0
    assert s.quality_max_attempts == 3
    assert s.grader_thinking_level == "low" and s.grader_image_max_side == 768
    assert s.gemini_vision_model == "gemini-3.8-flash"
    monkeypatch.setenv("GRADER_THINKING_LEVEL", "LOW")
    assert Settings().grader_thinking_level == "low"
    monkeypatch.setenv("GRADER_THINKING_LEVEL", "turbo")
    with pytest.raises(ValueError):
        Settings()


# --- (e) budget fields in SSE events and page_attempts ------------------------------------------


async def test_budget_fields_in_events_and_attempt_rows(photo_jpeg, monkeypatch):
    import app.api.books as books_api
    from app.db import repo_assets
    from app.db.session import get_sessionmaker
    from app.jobs.runner import registry
    from tests.test_books_api import GatedFake, _post, parse_sse, use_provider
    from tests.wallet_helpers import login_funded as login

    monkeypatch.setenv("BOOK_PAGES", "1,2")
    monkeypatch.setenv("QUALITY_TIME_BUDGET_S", "0.001")
    get_settings.cache_clear()
    story = load_story("jack")
    grader = ScriptedGrader({story.page(2).caption[:30]: [GradeResult(0.3, 0.3)],
                             "hero": [GradeResult(0.2, 0.9)]})
    gated = GatedFake()
    use_provider(monkeypatch, gated)
    monkeypatch.setattr(books_api, "get_vision_grader", lambda name=None: grader)
    from tests.conftest import client

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

    stopped = [d for e, d in live if e == "attempt" and d["phase"] == "stopped"]
    assert [(d["page"], d["attempt"]) for d in stopped] == [(0, 2), (2, 1)]
    assert all(d["budget_stopped"] is True for d in stopped)
    assert stopped[1]["likeness"] == 0.3  # the failed check's scores ride along
    hero = [d for e, d in live if e == "hero"][0]
    assert hero["attempts"] == 2 and hero["budget_stopped"] is True
    pages = {d["page"]: d for e, d in live if e == "page"}
    assert pages[2]["attempts"] == 1 and pages[2]["budget_stopped"] is True
    assert pages[1]["budget_stopped"] is False
    snap_pages = {d["page"]: d for e, d in snap if e == "page"}
    assert snap_pages[2]["budget_stopped"] is True and snap_pages[1]["budget_stopped"] is False
    assert [d for e, d in snap if e == "hero"][0]["budget_stopped"] is True
    async with get_sessionmaker()() as s:
        rows = await repo_assets.list_attempts(s, book_id)
    flagged = [(r.page, r.attempt, r.chosen) for r in rows if r.budget_stopped]
    assert flagged == [(0, 1, True), (2, 1, True)]  # the kept attempt (equal scores: first)


# --- (a) calibrated contradictions -------------------------------------------------------------


def _section(start: str, end: str) -> str:
    text = " ".join(GRADE_INSTRUCTIONS.split())
    return text[text.index(start):text.index(end)]


def test_contradiction_guidance_lists_only_objective_errors():
    contra = _section("contradictions: DEFINITE", "These do NOT count")
    for needle in ("wrong count", "wrong colour of something whose colour is specified",
                   "must-be-visible item or a required character that is missing",
                   "an extra character", "cut-out/sticker look",
                   "a blank, empty, unpainted, blurred-out, smeared, padded or letterboxed "
                   "band, strip",
                   "inset panels, comic borders, split panels or more than one image",
                   "letters, words or other text",
                   "show exactly five beans in total; currently seven",
                   "remove the reference inset panels in the top-left corner",
                   "no empty band at the top"):
        assert needle in contra, needle
    not_contra = _section("These do NOT count", "- issues:")
    for needle in ("framing or cropping choices",
                   "beanstalk tip should end above the clouds rather than off-panel",
                   "as long as it is recognisable",
                   "giant should be fully visible rather than just a blurry boot",
                   "exact pose, gesture, grip or expression",
                   "holding the axe near the head rather than swinging from the handle",
                   "off by more than about 2x", "golden egg is somewhat large",
                   "bean is slightly large", "motion state",
                   "unless that motion is the page's main event", "lighting",
                   "background details"):
        assert needle in not_contra, needle
    desc = _Grade.model_fields["contradictions"].description
    assert "objective, checkable errors only" in desc and "2x" in desc


def test_nitpicks_as_issues_pass_but_real_errors_fail():
    nitpicks = GradeResult(0.85, 0.8, issues=[
        "beanstalk tip should end above the clouds rather than off-panel",
        "holding the axe near the head rather than swinging from the handle"])
    assert quality.evaluate(nitpicks, min_likeness=0.6, min_fidelity=0.6).passed
    real = GradeResult(0.85, 0.8, contradictions=[
        "remove extraneous reference inset panels in top-left corner"])
    chk = quality.evaluate(real, min_likeness=0.6, min_fidelity=0.6)
    assert not chk.passed and chk.issues[0].startswith("remove extraneous reference inset")


async def test_fake_grader_scripted_calibration_in_the_loop():
    seq = [GradeResult(0.9, 0.9, contradictions=["show exactly five beans total; currently "
                                                 "seven"]),
           GradeResult(0.9, 0.9, issues=["bean slightly large"])]
    grader = FakeGrader(fn=lambda call, n: seq[min(n, 1)])
    seen = []

    async def generate(n, corrections, quiet):
        seen.append(list(corrections))
        return img(f"a{n}")

    async def check(n, image):
        g = await grader.grade(image.png, hero=None, photo=b"p", caption="c",
                               must_be_visible=[])
        return quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)

    res = await quality.run_quality_loop(generate=generate, check=check, max_attempts=3)
    assert res.count == 2 and res.chosen.n == 2 and res.chosen.passed
    assert seen[1] == ["show exactly five beans total; currently seven"]


# --- (b) figure boxes and the middle-zone rule ---------------------------------------------------

S01 = get_layout("s01_home_two_shot")  # bottom, top (+ middle last)


def test_grader_parses_figure_boxes():
    boxes = _boxes([_Box(label="hero body", kind="Figure", box_2d=[300, 100, 900, 400]),
                    _Box(label="hero face", kind="face", box_2d=[300, 150, 420, 300]),
                    _Box(label="harp", kind="object", box_2d=[0, 0, 100, 100])])
    assert [b.kind for b in boxes] == ["figure", "face", "key_object"]
    assert boxes[0].y == pytest.approx(0.3) and boxes[0].h == pytest.approx(0.6)
    assert "'figure'" in _Box.model_fields["kind"].description
    assert 'kind "figure"' in " ".join(GRADE_INSTRUCTIONS.split())


def test_middle_zone_blocked_by_any_body_not_just_faces():
    # faces in both top and bottom zones, a body (no face) across the middle band
    boxes = [face(0.4, 0.8, 0.1, 0.1), face(0.4, 0.1, 0.1, 0.1),
             figure(0.6, 0.35, 0.2, 0.4)]
    p = place_text(S01, boxes)
    assert p.blocked  # the middle is no longer a clear way out
    # without the body the middle band would be clear
    assert not place_text(S01, boxes[:2]).blocked
    assert place_text(S01, boxes[:2]).zone.zone == "middle"


def test_top_and_bottom_ignore_figures_and_big_figures_are_not_regions():
    tall = figure(0.3, 0.1, 0.4, 0.88)  # head to feet, crosses both bands
    assert not is_region(tall)
    p = place_text(S01, [tall, face(0.4, 0.3, 0.2, 0.12)])
    assert p.zone.zone == "bottom" and not p.blocked and p.overlap == 0


def test_blocked_middle_retargets_the_corrective_prompt():
    # every zone blocked; the middle (a body) is the least bad but the next prompt must
    # ask to clear the layout's first zone, not the middle of the picture
    boxes = [face(0.1, 0.78, 0.3, 0.15), face(0.1, 0.06, 0.3, 0.15),
             figure(0.85, 0.5, 0.05, 0.06, "hen body")]
    p = place_text(S01, boxes)
    assert p.zone.zone == "middle" and p.blocked and p.blocker_phrase == "every character's body"
    assert p.retarget.zone.zone == "bottom" and p.retarget.blocker_phrase == "every face"
    chk = quality.evaluate(GradeResult(0.9, 0.9, boxes=boxes), min_likeness=0.6,
                           min_fidelity=0.6, placement=p)
    assert not chk.passed and "bottom fifth" in chk.issues[-1]


# --- (c) blank band detector -------------------------------------------------------------------


@pytest.mark.parametrize(("edge", "fraction"), [("top", 0.15), ("bottom", 0.12),
                                                ("left", 0.10), ("right", 0.10)])
def test_band_detected_on_every_edge(edge, fraction):
    band = find_blank_band(with_band(edge, fraction))
    assert band is not None and band.edge == edge
    assert band.fraction == pytest.approx(fraction, abs=0.02)
    assert band.issue == band_issue(edge)


def test_band_issue_wording():
    assert band_issue("top").startswith("paint the whole picture edge to edge; no empty band "
                                        "at the top")


def test_no_band_on_painted_pictures():
    assert MIN_FRACTION == 0.08
    assert find_blank_band(png(noisy())) is None
    assert find_blank_band(with_band("top", 0.05)) is None  # thinner than 8%
    # a flat, saturated painted sky is not an unpainted band
    assert find_blank_band(with_band("top", 0.2, colour=(70, 130, 220))) is None
    # a vertical gradient (flat rows that drift) is not a band
    grad = Image.new("RGB", (480, 640))
    grad.putdata([(240 - y // 4, 235 - y // 4, 225 - y // 4) for y in range(640)
                  for _ in range(480)])
    assert find_blank_band(png(grad)) is None
    assert find_blank_band(b"not an image") is None


async def test_fake_provider_pictures_have_no_band():
    for prompt in ("a", "b", "c"):
        assert find_blank_band((await FakeProvider().edit(prompt, [])).png) is None


def test_black_border_and_jpeg_band():
    assert find_blank_band(with_band("right", 0.1, colour=(5, 5, 5))).edge == "right"
    im = noisy()
    im.paste((250, 248, 240), (0, 0, 480, 100))
    band = find_blank_band(png(im, "JPEG"))
    assert band is not None and band.edge == "top"


class BandFirstFake(FakeProvider):
    """The first page picture has a cream band across the top (30%: too deep for the free
    strip rescue); later ones are fine."""

    def __init__(self):
        super().__init__()
        self.page_prompts: list[str] = []

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        res = await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        if prompt.startswith("Task: illustrate one picture-book page"):
            self.page_prompts.append(prompt)
            if len(self.page_prompts) == 1:
                res.png = with_band("top", 0.30)
        return res


async def test_band_fails_the_attempt_without_a_grade_call():
    provider, grader = BandFirstFake(), FakeGrader()
    book = await run_book(provider, await _photo(), "n64", 2, story_id="jack", pages=[4],
                          hero=HERO, grader=grader)
    page = book.pages[0]
    assert page.attempts == 2 and page.chosen_attempt == 2
    assert sum(c.subject == "page" for c in grader.calls) == 1  # attempt 1 not graded
    assert sum(c.kind == "grade" and c.page == 4 for c in book.costs) == 1
    assert band_issue("top") in provider.page_prompts[1]


def test_local_fail_ranks_below_graded_attempts():
    local = quality.Attempt(1, image=img("a"), check=quality.Check(passed=False, local=True))
    graded = quality.Attempt(2, image=img("b"), check=quality.evaluate(
        GradeResult(0.1, 0.1, contradictions=list("abc"), has_text_artifacts=True),
        min_likeness=0.6, min_fidelity=0.6))
    assert quality.pick_best([local, graded]).n == 2
    assert not local.check.unchecked


def test_quiet_zone_wording_paints_the_area():
    story = load_story("jack")
    page = story.page(15)
    base = quiet_zone_rule(page)
    assert base.endswith(PAINT_EDGE_TO_EDGE)
    strong = quiet_zone_rule(page, "top", strong=True, item="the golden harp")
    assert "fill it only with plain, calm background" not in strong
    assert ("keep that area free of faces and important objects, but paint the scene there "
            "normally (sky, grass, wall, floor) - never a blank strip or border") in strong
    assert "plain sky" not in get_layout("s15_chase_action").quiet_zone_instruction
    assert PAINT_EDGE_TO_EDGE in scene_prompt(build_style_string("n64", 2), page, story)


# --- (d) grader input downscaling and thinking level ------------------------------------------


def test_shrink_for_grader():
    big = png(Image.linear_gradient("L").resize((1536, 2048)).convert("RGB"))
    small = shrink_for_grader(big)
    assert small[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(small)) as im:
        assert im.size == (576, 768)
    tiny = png(noisy((300, 400)))
    assert shrink_for_grader(tiny) is tiny
    assert shrink_for_grader(big, max_side=0) is big
    assert shrink_for_grader(b"garbage") == b"garbage"
    rgba = Image.new("RGBA", (1000, 1000), (10, 20, 30, 128))
    assert shrink_for_grader(png(rgba))[:3] == b"\xff\xd8\xff"


class _Resp:
    text = ('{"likeness":0.9,"fidelity":0.9,"has_text_artifacts":false,"skin_tone_match":true,'
            '"hair_colour_match":true,"hair_texture_match":true,"identity_notes":"",'
            '"hero_hair_match":true,"family_match":true,"family_notes":"",'
            '"cast_match":true,"cast_notes":"","expression_match":true,'
            '"expression_notes":"","hero_child_ok":true,"hero_items_ok":true,'
            '"hero_outfit_ok":true,"hero_look_notes":"",'
            '"contradictions":[],"issues":[],"boxes":[{"label":"hero body","kind":"figure",'
            '"box_2d":[100,100,900,500]}]}')
    usage_metadata = None
    model_version = "m"


class _Models:
    def __init__(self, reject_minimal: bool):
        self.reject_minimal = reject_minimal
        self.calls: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        level = config.thinking_config.thinking_level.value
        self.calls.append({"level": level, "contents": contents})
        if self.reject_minimal and level == "MINIMAL":
            raise genai_errors.APIError(400, {"error": {
                "code": 400, "status": "INVALID_ARGUMENT",
                "message": "Thinking level MINIMAL is not supported for this model."}})
        return _Resp()


@pytest.fixture(autouse=True)
def _forget_rejected_levels():
    from app.providers import gemini_vision

    gemini_vision._REJECTED_LEVELS.clear()
    yield
    gemini_vision._REJECTED_LEVELS.clear()


def _grader(reject_minimal=False, **kw) -> tuple[GeminiVisionGrader, _Models]:
    g = GeminiVisionGrader(api_key="test-key", model="gemini-3.8-flash", **kw)
    models = _Models(reject_minimal)
    g._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    return g, models


async def test_grader_sends_downscaled_images_and_configured_thinking():
    g, models = _grader(thinking_level="minimal")
    big = png(Image.linear_gradient("L").resize((1536, 2048)).convert("RGB"))
    res = await g.grade(big, hero=big, photo=big, caption="c", must_be_visible=[])
    assert models.calls[0]["level"] == "MINIMAL"
    parts = models.calls[0]["contents"][1:]
    assert len(parts) == 3
    for part in parts:
        assert part.inline_data.mime_type == "image/jpeg"
        with Image.open(io.BytesIO(part.inline_data.data)) as im:
            assert max(im.size) == 768
    assert res.boxes[0].kind == "figure"


async def test_grader_falls_back_to_low_when_minimal_is_rejected():
    g, models = _grader(reject_minimal=True, thinking_level="minimal")
    await g.grade(b"\x89PNG\r\n\x1a\n" + b"0" * 10, hero=None, photo=png(noisy((64, 64))),
                  caption="c", must_be_visible=[])
    assert [c["level"] for c in models.calls] == ["MINIMAL", "LOW"]
    assert g.thinking_level == "low"
    g2, models2 = _grader(thinking_level="low")
    models2.reject_minimal = True
    await g2.grade(png(noisy((64, 64))), hero=None, photo=png(noisy((64, 64))), caption="c",
                   must_be_visible=[])
    assert [c["level"] for c in models2.calls] == ["LOW"]
    with pytest.raises(ValueError):
        GeminiVisionGrader(api_key="k", model="m", thinking_level="turbo")


async def test_default_level_is_low_and_the_fallback_is_remembered_per_process():
    g0, models0 = _grader()
    await g0.grade(png(noisy((64, 64))), hero=None, photo=png(noisy((64, 64))), caption="c",
                   must_be_visible=[])
    assert [c["level"] for c in models0.calls] == ["LOW"]
    # a grader asked for MINIMAL learns it is rejected once ...
    g1, models1 = _grader(reject_minimal=True, thinking_level="minimal")
    args = dict(hero=None, photo=png(noisy((64, 64))), caption="c", must_be_visible=[])
    await g1.grade(png(noisy((64, 64))), **args)
    assert [c["level"] for c in models1.calls] == ["MINIMAL", "LOW"]
    # ... and the next grader (next book) does not repeat the rejected call
    g2, models2 = _grader(reject_minimal=True, thinking_level="minimal")
    assert g2.thinking_level == "low"
    await g2.grade(png(noisy((64, 64))), **args)
    assert [c["level"] for c in models2.calls] == ["LOW"]


async def test_other_400s_are_not_retried():
    g, models = _grader()

    async def bad(*, model, contents, config):
        models.calls.append(1)
        raise genai_errors.APIError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                    "message": "bad image"}})

    models.generate_content = bad
    with pytest.raises(ProviderError):
        await g.grade(png(noisy((64, 64))), hero=None, photo=png(noisy((64, 64))),
                      caption="c", must_be_visible=[])
    assert len(models.calls) == 1


# --- (b) page fixes: Jack p3 beans, p10 close-up (shared slot with Red p10) ---------------------


def test_jack_p3_bean_count_is_unambiguous():
    story = load_story("jack")
    p3 = story.page(3)
    assert "exactly four small beans (red, yellow, green, blue)" in " ".join(p3.caption.split())
    assert "exactly one small purple bean on the doorstep" in " ".join(p3.caption.split())
    assert "Five beans in total, no others" in " ".join(p3.caption.split())
    assert any("exactly four small beans" in m for m in p3.must_be_visible)
    assert any("five beans in total, no others" in m for m in p3.must_be_visible)
    assert "Five beans in total, no others anywhere" in " ".join(p3.plate_prompt.split())
    assert "five beans in total" in story.props["beans"]
    assert p3.text == "Beans! she cried. And out the window they went."


@pytest.mark.parametrize("story_id", ["jack", "red"])
def test_closeup_slot_prefers_top_and_keeps_face_in_middle(story_id):
    story = load_story(story_id)
    p10 = story.page(10)
    lay = get_layout(p10.layout)
    assert [z["zone"] for z in lay.allowed_text_zones] == ["top", "bottom"]
    assert "middle band" in p10.hero_instruction and "bottom fifth" in p10.hero_instruction
    # a close-up face in the middle band (forehead 0.28 .. chin 0.66) leaves the top clear
    p = place_text(lay, [face(0.3, 0.28, 0.4, 0.38), figure(0.15, 0.25, 0.7, 0.75)])
    assert p.zone.zone == "top" and not p.blocked
    # the old failure: a face reaching into the bottom fifth no longer matters on top
    p = place_text(lay, [face(0.25, 0.3, 0.5, 0.55)])
    assert p.zone.zone == "top" and not p.blocked
    # Red: the wolf's eyes now sit below the top fifth, so they do not block the top zone
    if story_id == "red":
        assert "below the top fifth" in " ".join(p10.plate_prompt.split())
