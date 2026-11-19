"""Child version of any photo + kept personal items + story outfit (hero and pages), the
hero look grader checks, headwear-tolerant hair checks, reference downscaling / media
resolution, the token-aware image limiter, the hard cap that bounds the book, seam rescue,
the gentle cast-sheet retry and the Jack size / stump / bean fixes."""

import asyncio
import io

import pytest
from google.genai import errors as genai_errors
from PIL import Image

from app.config import get_settings
from app.pipeline import quality
from app.pipeline.book import (
    BookGenerator,
    HardCapReached,
    TokenLimiter,
    plan_book,
    run_book,
)
from app.pipeline.cast_sheets import make_cast_sheet
from app.pipeline.family import (
    child_version_block,
    identity_block,
    identity_line,
    kept_items,
    kept_items_phrase,
)
from app.pipeline.prompts import (
    DEFAULT_REFS,
    GENTLE_SHEET_RULE,
    HERO_CHILD_RULE,
    VERTICAL_RESCUE_RULE,
    cast_sheet_prompt,
    hero_prompt,
    scene_prompt,
)
from app.providers.base import (
    GradeResult,
    ImageResult,
    NoImageError,
    PhotoTraits,
    ProviderError,
)
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini import GeminiProvider
from app.providers.gemini_vision import (
    CAST_SHEET_CHECK_INSTRUCTIONS,
    GRADE_INSTRUCTIONS,
    TRAITS_INSTRUCTIONS,
    GeminiVisionGrader,
    _Traits,
    traits_note,
)
from app.providers.refs import (
    estimate_input_tokens,
    image_tokens,
    shrink_ref,
    tile_tokens,
)
from app.stories.loader import load_story
from app.styles.presets import build_style_string
from tests.test_quality import HERO, RecordingFake, _photo

N64 = build_style_string("n64", 2)
ELDER = PhotoTraits(2, "grey", "curly", age_group="elderly",
                    glasses="round black-rimmed glasses",
                    headwear="a dark navy skullcap (kippah)", facial_hair=True)
KID = PhotoTraits(4, "black", "straight", age_group="child")


def _png(size=(896, 1200), mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.effect_noise(size, 40).convert(mode).save(out, format="PNG")
    return out.getvalue()


# --- 1. child version, kept items, outfit ----------------------------------------------------


def test_identity_block_for_an_elderly_photo():
    block = identity_block(ELDER)
    assert block.startswith("CHILD VERSION: the photo shows an elderly person.")
    assert "about 7 to 9 years old" in block and "Grey hair stays grey" in block
    assert "Leave out: a beard, a moustache or stubble, wrinkles" in block
    assert "adult clothes such as a suit" in block
    assert "the same round black-rimmed glasses" in block
    assert ("the hero ALWAYS wears round black-rimmed glasses and a dark navy skullcap "
            "(kippah), on every picture") in block
    assert "goes over or around the headwear and never replaces or hides it" in block
    assert kept_items(ELDER) == ["round black-rimmed glasses", "a dark navy skullcap (kippah)"]


def test_identity_block_for_a_child_photo_has_no_child_version():
    assert child_version_block(KID) == "" and child_version_block(None) == ""
    block = identity_block(KID)
    assert "CHILD VERSION" not in block and "Personal items" not in block
    assert block == identity_line(KID)
    assert kept_items_phrase(KID) == "" and kept_items(None) == []


def test_hair_covered_by_headwear_is_not_asserted():
    covered = PhotoTraits(3, "black", "curly", age_group="adult", headwear="a white hijab",
                          hair_covered=True)
    assert "curly black" not in identity_line(covered)
    assert "hair colour and hair texture as far as they show" in child_version_block(covered)


def test_hero_prompt_forces_the_story_outfit_and_a_child():
    jack = load_story("jack")
    p = hero_prompt(N64, jack.hero_outfit, identity=identity_block(ELDER))
    assert "The hero is always a CHILD of about 7 to 9 years old" in p
    assert "Outfit (always - it replaces whatever the person wears in the photo)" in p
    assert "Do not copy the photo's clothes" in p and "CHILD VERSION" in p


def test_page_prompt_rules_and_no_photo_ref():
    red = load_story("red")
    assert [r.role for r in DEFAULT_REFS] == ["hero"]
    p = scene_prompt(N64, red.page(2), red, "fam_a", hero_items=kept_items_phrase(ELDER))
    assert HERO_CHILD_RULE in p and "original photo" not in p
    assert ("The hero always wears round black-rimmed glasses and a dark navy skullcap "
            "(kippah) on every page") in p
    assert "goes over or around the headwear and never replaces it" in p
    plain = scene_prompt(N64, red.page(2), red, "fam_a")
    assert HERO_CHILD_RULE in plain and "always wears round" not in plain
    assert VERTICAL_RESCUE_RULE not in plain
    assert VERTICAL_RESCUE_RULE in scene_prompt(N64, red.page(10), red, vertical_rescue=True)


async def test_book_with_an_elderly_photo_uses_child_version_items_and_outfit():
    fake, grader = RecordingFake(), FakeGrader(traits=ELDER)
    photo = await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="red", pages=[2], hero=HERO,
                          grader=grader)
    hero_prompts = [c["prompt"] for c in fake.calls if c["prompt"].startswith("Task: turn")]
    assert "CHILD VERSION" in hero_prompts[0] and "skullcap" in hero_prompts[0]
    (call, refs), = [(c, i) for c, i in zip(fake.calls, fake.images, strict=True)
                     if c["prompt"].startswith("Task: illustrate")]
    assert photo not in refs and refs[0] == book.hero_png
    assert "always wears round black-rimmed glasses and a dark navy skullcap" in call["prompt"]
    for g in grader.calls:  # hero and page grades both check the hero look
        assert g.hero_items == kept_items(ELDER)
        assert g.hero_outfit.startswith("A bright red hooded cape")


# --- 2. hero look checks in the quality gate ------------------------------------------------


def test_hero_look_failures_are_definite_with_concrete_corrections():
    outfit = load_story("red").hero_outfit
    g = GradeResult(0.9, 0.9, hero_child_match=False, hero_items_match=False,
                    hero_outfit_match=False, hero_look_notes="old man in a suit")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=ELDER,
                           hero_outfit=outfit)
    assert not chk.passed and chk.definite and not g.identity_ok
    text = " | ".join(chk.issues)
    assert quality.HERO_CHILD_FIX in text
    assert "the hero must wear round black-rimmed glasses and a dark navy skullcap" in text
    assert "the hero must wear the hero outfit (A bright red hooded cape" in text
    assert "never adult clothes such as a suit" in text and "old man in a suit" in text
    ok = quality.evaluate(GradeResult(0.9, 0.9), min_likeness=0.6, min_fidelity=0.6)
    assert ok.passed


def test_hair_checks_tolerate_headwear_covering_the_hair():
    covered = PhotoTraits(2, "grey", "curly", age_group="elderly", headwear="a skullcap",
                          hair_covered=True)
    g = GradeResult(0.9, 0.9, hair_colour_match=False, hair_texture_match=False,
                    identity_notes="hair must be curly and grey like in the photo",
                    contradictions=["the hair must be curly and grey like in the photo"])
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=covered)
    assert chk.passed and g.identity_ok and g.contradictions == []
    # visible hair (not covered) still fails
    g2 = GradeResult(0.9, 0.9, hair_colour_match=False)
    assert not quality.evaluate(g2, min_likeness=0.6, min_fidelity=0.6, traits=ELDER).passed


async def test_hero_reference_regenerates_when_it_shows_an_adult_in_a_suit():
    def fn(call, n):
        if call.subject == "hero" and n == 0:
            return GradeResult(0.9, 0.9, hero_child_match=False, hero_outfit_match=False,
                               hero_look_notes="an old man with a beard in a dark suit",
                               boxes=[DEFAULT_FACE])
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])

    fake = RecordingFake()
    book = await run_book(fake, await _photo(), "n64", 2, story_id="red", pages=[2],
                          hero=HERO, grader=FakeGrader(fn=fn, traits=ELDER))
    assert book.hero_attempts == 2 and book.hero_scores["passed"]
    second = [c["prompt"] for c in fake.calls if c["prompt"].startswith("Task: turn")][1]
    assert quality.HERO_CHILD_FIX in second and "dark suit" in second


# --- 3. grader texts and parsing ----------------------------------------------------------------


def test_grader_instructions_judge_pages_against_the_hero_reference():
    text = GRADE_INSTRUCTIONS
    assert "recognisably the same character as the\n  HERO REFERENCE" in text
    assert "hero_child_ok: the hero must be a CHILD of about 6-10" in text
    assert "hero_items_ok" in text and "hero_outfit_ok" in text
    assert "another character's\n    clothes" in text
    assert "if the hair is fully covered in either, set" in text
    assert "A giant only about\n  2-3x the child's height indoors is FINE" in text
    assert "age_group" in TRAITS_INSTRUCTIONS and "headwear" in TRAITS_INSTRUCTIONS
    assert {"age_group", "glasses", "headwear", "facial_hair",
            "hair_covered"} <= set(_Traits.model_fields)
    assert "child version" in traits_note(ELDER) and "elderly person" in traits_note(ELDER)
    assert "{name}" in CAST_SHEET_CHECK_INSTRUCTIONS


class _Resp:
    usage_metadata = None
    model_version = "m"

    def __init__(self, text):
        self.text = text


def _vision(text):
    g = GeminiVisionGrader(api_key="test-key", model="gemini-3.8-flash")
    calls = []

    class Models:
        async def generate_content(self, *, model, contents, config):
            calls.append(contents)
            return _Resp(text)

    g._client = type("C", (), {"aio": type("A", (), {"models": Models()})()})()
    return g, calls


async def test_gemini_photo_traits_parse_age_items_and_none_words():
    g, _ = _vision('{"skin_tone":2,"hair_colour":"grey","hair_texture":"curly",'
                   '"age_group":"Elderly","glasses":"round glasses","headwear":"none",'
                   '"facial_hair":true,"hair_covered":false}')
    t = await g.photo_traits(_png((64, 64)))
    assert (t.age_group, t.glasses, t.headwear, t.facial_hair) == (
        "elderly", "round glasses", "", True)
    assert not t.is_child


async def test_gemini_grade_sends_hero_items_and_outfit_and_parses_the_look_checks():
    body = ('{"likeness":0.9,"fidelity":0.9,"has_text_artifacts":false,'
            '"skin_tone_match":true,"hair_colour_match":true,"hair_texture_match":true,'
            '"identity_notes":"","hero_hair_match":true,"family_match":true,'
            '"family_notes":"","cast_match":true,"cast_notes":"","expression_match":true,'
            '"expression_notes":"","hero_child_ok":false,"hero_items_ok":false,'
            '"hero_outfit_ok":false,"hero_look_notes":"adult in a suit",'
            '"contradictions":[],"issues":[],"boxes":[]}')
    g, calls = _vision(body)
    small = _png((64, 64))
    res = await g.grade(small, hero=small, photo=small, caption="c", must_be_visible=[],
                        hero_items=["round glasses"], hero_outfit="a red cape")
    text = calls[0][0]
    assert "Hero personal items (hero_items_ok - always worn): round glasses" in text
    assert "Hero outfit (hero_outfit_ok): a red cape" in text
    assert not (res.hero_child_match or res.hero_items_match or res.hero_outfit_match)
    # nothing listed -> items / outfit never fail, the child check always applies
    res2 = await g.grade(small, hero=None, photo=small, caption="c", must_be_visible=[])
    assert res2.hero_items_match and res2.hero_outfit_match and not res2.hero_child_match


# --- 4. reference downscaling, media resolution, token estimates -----------------------------


def test_reference_downscaling_and_token_estimates():
    big = _png()
    small = shrink_ref(big, 768)
    assert small[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(small)) as im:
        assert im.size == (573, 768)
    assert shrink_ref(small, 768) is small and shrink_ref(big, 0) is big
    rgba = shrink_ref(_png(mode="RGBA"), 768)
    assert rgba[:3] == b"\xff\xd8\xff"
    assert tile_tokens((896, 1200)) == 4 * 258 and tile_tokens((573, 768)) == 258
    assert tile_tokens((300, 300)) == 258
    assert image_tokens(big) == 1032 and image_tokens(big, 768) == 258
    assert image_tokens(big, 768, "high") == 1120 and image_tokens(big, 0, "medium") == 560
    assert estimate_input_tokens("x" * 400, [big, big], max_side=768, first_level="high",
                                 level="medium") == 100 + 1120 + 560


def test_ref_settings_defaults_and_validation(monkeypatch):
    s = get_settings()
    assert s.image_input_tokens_per_minute == 90000 and s.image_ref_max_side == 768
    assert (s.image_ref_media_resolution, s.image_ref_media_resolution_first) == (
        "medium", "high")
    monkeypatch.setenv("IMAGE_REF_MEDIA_RESOLUTION", "huge")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()


class _ImgResp:
    prompt_feedback = None
    usage_metadata = None
    model_version = "m"

    def __init__(self, png):
        part = type("P", (), {"inline_data": type("B", (), {"data": png,
                                                             "mime_type": "image/png"})(),
                              "text": None})()
        self.candidates = [type("C", (), {"content": type("X", (), {"parts": [part]})(),
                                          "finish_reason": "STOP", "finish_message": None})()]


def _gemini(reject_levels=False):
    from app.providers import gemini

    gemini._NO_MEDIA_RESOLUTION.discard("gemini-3.1-flash-lite-image")
    p = GeminiProvider(api_key="test-not-a-key", model="gemini-3.1-flash-lite-image",
                       ref_max_side=768, ref_level="medium", ref_level_first="high")
    calls = []
    out = _png((96, 128))

    class Models:
        async def generate_content(self, *, model, contents, config):
            calls.append(contents)
            levelled = any(getattr(c, "media_resolution", None) for c in contents[1:])
            if reject_levels and levelled:
                raise genai_errors.APIError(400, {"error": {
                    "code": 400, "status": "INVALID_ARGUMENT",
                    "message": "media_resolution is not supported for this model"}})
            return _ImgResp(out)

    p._client = type("C", (), {"aio": type("A", (), {"models": Models()})()})()
    return p, calls


async def test_gemini_provider_shrinks_refs_and_sets_media_resolution():
    p, calls = _gemini()
    await p.edit("prompt", [_png(), _png()])
    parts = calls[0][1:]
    assert [x.media_resolution.level.value for x in parts] == [
        "MEDIA_RESOLUTION_HIGH", "MEDIA_RESOLUTION_MEDIUM"]
    for x in parts:
        assert x.inline_data.mime_type == "image/jpeg"
        with Image.open(io.BytesIO(x.inline_data.data)) as im:
            assert max(im.size) == 768
    assert p.estimate_input_tokens("x" * 40, [_png(), _png()]) == 10 + 1120 + 560


async def test_gemini_provider_drops_media_resolution_when_rejected():
    from app.providers import gemini

    p, calls = _gemini(reject_levels=True)
    await p.edit("prompt", [_png()])
    assert len(calls) == 2 and calls[1][1].media_resolution is None
    assert p.model in gemini._NO_MEDIA_RESOLUTION
    await p.edit("prompt", [_png()])  # remembered: one call, no level
    assert len(calls) == 3 and calls[2][1].media_resolution is None
    gemini._NO_MEDIA_RESOLUTION.discard(p.model)


# --- 5. token-aware limiter -----------------------------------------------------------------


async def test_token_limiter_queues_learns_and_lets_a_big_call_through():
    t = {"now": 0.0}
    lim = TokenLimiter(1000, clock=lambda: t["now"])
    a = await lim.acquire(600)
    assert lim.wait_estimate(600) == pytest.approx(60.0)
    waiter = asyncio.create_task(lim.acquire(600))
    await asyncio.sleep(0.02)
    assert not waiter.done()
    lim.settle(a, 300)  # the API reported fewer tokens: the booking shrinks, ratio learns
    assert lim.ratio < 1.0
    await asyncio.wait_for(waiter, 2)
    assert lim.used() <= 1000
    t["now"] = 200.0
    big = await lim.acquire(5000)  # larger than the budget: proceeds on an empty window
    assert lim.used() >= 1000
    lim.release(big)
    assert lim.used() == 0


async def test_book_logs_estimated_and_actual_input_tokens(caplog, monkeypatch):
    monkeypatch.setenv("IMAGE_INPUT_TOKENS_PER_MINUTE", "50000")
    get_settings.cache_clear()
    with caplog.at_level("INFO", logger="app.pipeline.book"):
        await run_book(RecordingFake(), await _photo(), "n64", 2, story_id="jack",
                       pages=[4], hero=HERO, grader=FakeGrader())
    assert "image tokens kind=page page=4 refs=1 est_in_tok=" in caplog.text
    assert "window=" in caplog.text and "/50000" in caplog.text


# --- 6. hard cap ------------------------------------------------------------------------------


async def _gen(n, corrections, quiet):
    return ImageResult(png=b"x", model="m")


async def test_loop_never_starts_a_generation_that_would_end_past_the_cap():
    async def failing(n, image):
        return quality.Check(passed=False, issues=["meh"])

    res = await quality.run_quality_loop(generate=_gen, check=failing, max_attempts=3,
                                         budget_exhausted=lambda: False, min_attempts=2,
                                         can_finish=lambda: False)
    assert res.count == 1 and res.budget_stopped
    res = await quality.run_quality_loop(generate=_gen, check=failing, max_attempts=3,
                                         can_finish=lambda: True)
    assert res.count == 3


async def test_book_retry_is_skipped_when_it_cannot_end_before_the_cap():
    t = {"now": 0.0}

    def fn(call, n):  # every picture fails softly -> would want 3 generations
        return GradeResult(0.3, 0.9, boxes=[DEFAULT_FACE])

    plan = plan_book("jack", [4], "n64", 2)
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=FakeProvider(),
                        grader=FakeGrader(fn=fn), time_budget_s=1000, hard_cap_s=70,
                        clock=lambda: t["now"], started_at=0.0, max_attempts=3)
    t["now"] = 66.0  # 66 + (image + grade, learned: >5 s) > 70: only the first pictures
    book = await gen.run()
    assert book.hero_attempts == 1 and book.pages[0].attempts == 1
    assert book.pages[0].budget_stopped
    assert gen.can_finish(3) and not gen.can_finish(5)


async def test_rate_limit_wait_and_token_queue_respect_the_cap(monkeypatch):
    t = {"now": 0.0}
    plan = plan_book("jack", [4], "n64", 2)
    exc = ProviderError("429 per minute (retry after 10s)", status=429)

    class Always429(FakeProvider):
        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            raise exc

    slept = []

    async def fake_sleep(d):
        slept.append(d)

    import app.pipeline.book as book_mod
    monkeypatch.setattr(book_mod.asyncio, "sleep", fake_sleep)
    gen = BookGenerator(plan, HERO, b"", hero_provider=Always429(), grader=FakeGrader(),
                        hard_cap_s=70, clock=lambda: t["now"], started_at=0.0)
    t["now"] = 50.0
    with pytest.raises(ProviderError):  # a retry generation: no wait past the cap
        await gen._image(gen.hero_provider, "page", 4, "p", [], must_finish=False)
    assert not [d for d in slept if d > 1]
    # token queue: a non-must call that would wait past the cap is not started at all
    monkeypatch.setenv("IMAGE_INPUT_TOKENS_PER_MINUTE", "100")
    get_settings.cache_clear()
    lim = book_mod.image_token_rate()
    lim.clock = lambda: t["now"]
    await lim.acquire(100)
    with pytest.raises(HardCapReached):
        await gen._image(FakeProvider(), "page", 4, "p" * 400, [], must_finish=False)


# --- 7. seam rescue ---------------------------------------------------------------------------


async def test_padded_attempt_gets_the_vertical_rescue_on_the_last_attempt():
    from tests.test_budget_bands import with_band

    class BandFirst(RecordingFake):
        pages = 0

        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            res = await super().edit(prompt, images, aspect_ratio=aspect_ratio)
            if prompt.startswith("Task: illustrate"):
                BandFirst.pages += 1
                if BandFirst.pages == 1:
                    res.png = with_band("top", 0.30)  # too deep for the free rescue
            return res

    fake = BandFirst()
    book = await run_book(fake, await _photo(), "n64", 2, story_id="red", pages=[10],
                          hero=HERO, grader=FakeGrader(), max_attempts=2)
    prompts = [c["prompt"] for c in fake.calls if c["prompt"].startswith("Task: illustrate")]
    assert len(prompts) == 2 and book.pages[0].attempts == 2
    assert VERTICAL_RESCUE_RULE not in prompts[0] and VERTICAL_RESCUE_RULE in prompts[1]


def test_vertical_rescue_rule():
    plan = plan_book("red", [10], "n64", 2)
    gen = BookGenerator(plan, HERO, b"", hero_provider=FakeProvider(), grader=FakeGrader(),
                        max_attempts=3, time_budget_s=0)
    assert not gen.vertical_rescue(2, 0) and not gen.vertical_rescue(2, 1)
    assert gen.vertical_rescue(3, 1) and gen.vertical_rescue(3, 2)


def test_seam_pages_are_written_as_tall_compositions():
    red, jack = load_story("red"), load_story("jack")
    from app.stories.loader import get_layout

    assert "TALL portrait close-up, not a wide face crop" in get_layout(
        "s10_closeup_reaction").quiet_zone_instruction
    for lid in ("s11_tense_two_shot", "s12_treasure_close", "s14_second_marvel",
                "s15_chase_action"):
        assert get_layout(lid).quiet_zone_instruction.startswith("Frame it tall"), lid
    assert red.page(10).scene.startswith("A tall portrait close-up")
    for story, n in ((red, 11), (red, 14), (red, 15), (jack, 11), (jack, 12), (jack, 14)):
        assert "tall" in story.page(n).scene.lower(), (story.id, n)
    assert "chest-up" not in jack.page(12).hero_instruction


# --- 8. gentle cast-sheet retry, Jack fixes ----------------------------------------------------


async def test_cast_sheet_safety_refusal_retries_once_gently():
    red = load_story("red")
    wolf = red.characters["wolf"]
    prompts = []

    async def call(kind, page, prompt, refs):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise NoImageError("no image (finish_reason=PROHIBITED_CONTENT)",
                               finish_reason="PROHIBITED_CONTENT")
        return ImageResult(png=_png((64, 64)), model="m")

    made = await make_cast_sheet(call, N64, wolf, None)
    assert made is not None and len(prompts) == 2
    assert GENTLE_SHEET_RULE not in prompts[0] and GENTLE_SHEET_RULE in prompts[1]
    assert "friendly, silly cartoon storybook wolf" in prompts[0]
    assert "yellow-green eyes and a long snout. Sly" not in prompts[0]

    async def other(kind, page, prompt, refs):
        prompts.append(prompt)
        raise NoImageError("no image (finish_reason=OTHER)", finish_reason="OTHER")

    prompts.clear()
    assert await make_cast_sheet(other, N64, wolf, None) is None and len(prompts) == 1


def test_cast_sheet_prompt_uses_sheet_description_only_on_sheets():
    red = load_story("red")
    wolf = red.characters["wolf"]
    assert wolf.sheet_description.startswith("A friendly, silly cartoon storybook wolf")
    assert wolf.sheet_description in cast_sheet_prompt(N64, wolf)
    assert GENTLE_SHEET_RULE in cast_sheet_prompt(N64, wolf, gentle=True)
    page = scene_prompt(N64, red.page(2), red, "fam_a")
    assert wolf.description in page and wolf.sheet_description not in page


def test_jack_giant_sizes_stump_and_beans():
    jack = load_story("jack")
    for cid in ("giant", "giants_wife"):
        size = jack.characters[cid].size
        assert "at least about two and a half times the child's height" in size, cid
        assert "six times" not in size and "seven times" not in size
    assert "exactly one in the whole picture" in jack.props["stump"]
    assert jack.page(18).props == ["beanstalk", "stump", "cottage"]
    assert "exactly ONE short, clean chopped stump" in jack.page(18).scene
    p3 = jack.page(3)
    assert p3.text == "Beans! she cried. And out the window they went."
    assert "fly in ONE\n clear arc" not in p3.scene and "ONE clear arc" in p3.scene
    assert "none on the windowsill" in p3.scene
    assert p3.caption.startswith("The cottage at dusk.")  # reader caption unchanged
