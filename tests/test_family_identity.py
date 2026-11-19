"""Identity checks (skin tone / hair vs. the photo), the per-book family sheet, HW1 family
matching, region boxes in text placement, bean sizes and quota errors."""

import asyncio
import logging
import time

import pytest

from app.api.common import BUSY_MESSAGE, UNAVAILABLE_MESSAGE, provider_error
from app.config import get_settings
from app.pipeline import quality
from app.pipeline.book import BookGenerator, MemoryAssets, plan_book, run_book
from app.pipeline.family import identity_line
from app.pipeline.family_sheet import family_characters, page_family
from app.pipeline.hw1 import run_hw1
from app.pipeline.placement import OBJECT_MAX_AREA, OBJECT_MAX_WIDTH, place_text
from app.pipeline.prompts import family_sheet_prompt
from app.providers.base import Box, GradeResult, PhotoTraits, ProviderError
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import GRADE_INSTRUCTIONS, _Grade
from app.stories.loader import get_layout, load_story
from app.styles.presets import build_style_string
from tests.test_quality import HERO, SHEET_TASK, RecordingFake, _page_calls, _photo

TRAITS = PhotoTraits(skin_tone=2, hair_colour="black", hair_texture="straight")
HERO_TASK = "Task: turn the person"


def _hero_prompts(fake) -> list[str]:
    return [c["prompt"] for c in fake.calls if c["prompt"].startswith(HERO_TASK)]


def _sheet_calls(fake):
    return [c for c in fake.calls if c["prompt"].startswith(SHEET_TASK)]


# --- 1. identity checks ------------------------------------------------------------------


def test_grade_schema_has_identity_fields():
    fields = set(_Grade.model_fields)
    assert {"skin_tone_match", "hair_colour_match", "hair_texture_match",
            "identity_notes"} <= fields
    text = GRADE_INSTRUCTIONS.lower()
    assert "original photo" in text and "coily" in text
    assert "a teen, an adult or" in text and "lighting" in text
    assert "never box ground" in text and "a bean, a harp, a basket" in text


def test_identity_mismatch_fails_even_at_high_likeness():
    g = GradeResult(0.9, 0.9, hair_texture_match=False, identity_notes="hair is curly")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=TRAITS)
    assert not chk.passed
    assert chk.issues[0] == "the hero's hair must be straight and black like in the photo"
    assert "hair is curly" in chk.issues
    g = GradeResult(0.95, 0.95, skin_tone_match=False)
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=TRAITS)
    assert not chk.passed and chk.issues[0] == "the hero's skin must be fair like in the photo"
    # matching identity at the same scores passes
    assert quality.evaluate(GradeResult(0.9, 0.9), min_likeness=0.6, min_fidelity=0.6,
                            traits=TRAITS).passed


def test_identity_corrections_without_traits_and_score_penalty():
    g = GradeResult(0.9, 0.9, hair_colour_match=False)
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    assert chk.issues[0] == "the hero's hair must be the same colour like in the photo"
    assert GradeResult(0.9, 0.9, skin_tone_match=False).score < GradeResult(0.7, 0.7).score


def test_identity_issues_are_kept_within_the_cap():
    g = GradeResult(0.9, 0.9, has_text_artifacts=True, skin_tone_match=False,
                    hair_texture_match=False, issues=[f"issue {i}" for i in range(5)])
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, traits=TRAITS)
    assert len(chk.issues) == quality.MAX_ISSUES
    assert chk.issues[:2] == ["the hero's skin must be fair like in the photo",
                              "the hero's hair must be straight and black like in the photo"]
    assert chk.issues[-1] == "remove every letter, word, number, sign and speech bubble"


def test_identity_line():
    assert identity_line(TRAITS).startswith(
        "The child has fair skin and straight black hair - keep exactly that.")
    assert "read as" not in identity_line(TRAITS)
    assert identity_line(None) == ""
    assert "dark brown hair" in identity_line(PhotoTraits(4, "dark_brown", "unknown"))
    curly = identity_line(PhotoTraits(5, "black", "curly"))
    assert curly.startswith("The child has brown skin and curly black hair - keep exactly "
                            "that.")
    assert "must clearly read as curly" in curly and "never smooth, sleek or swept" in curly


def test_fake_grader_identity_defaults_and_override():
    async def go():
        ok = await FakeGrader().grade(b"x", hero=None, photo=b"p", caption="c",
                                      must_be_visible=[])
        bad = await FakeGrader(identity={"hair_texture_match": False}).grade(
            b"x", hero=None, photo=b"p", caption="c", must_be_visible=[])
        return ok, bad

    ok, bad = asyncio.run(go())
    assert ok.identity_ok and not bad.identity_ok and bad.skin_tone_match


def test_likeness_thresholds_default_and_config(monkeypatch):
    s = get_settings()
    assert (s.quality_min_likeness, s.quality_min_likeness_hero, s.quality_min_fidelity) == (
        0.6, 0.6, 0.6)
    monkeypatch.setenv("QUALITY_MIN_LIKENESS_HERO", "0.75")
    monkeypatch.setenv("QUALITY_MIN_LIKENESS", "0.7")
    get_settings.cache_clear()
    gen = BookGenerator(plan_book("jack", [1], "n64", 2), HERO, b"p",
                        hero_provider=FakeProvider(), grader=FakeGrader())
    assert (gen.min_likeness_hero, gen.min_likeness, gen.min_fidelity) == (0.75, 0.7, 0.6)


class IdentityGrader(FakeGrader):
    """Hero attempt 1: hair type wrong (likeness 0.9); page 4 attempt 1: skin wrong."""

    def __init__(self, likeness=0.9):
        super().__init__(traits=TRAITS)
        self.likeness = likeness
        self.n = {"hero": 0, "page": 0}

    async def photo_traits(self, photo):
        await asyncio.sleep(0.05)  # like the real call, slower than starting the hero
        return await super().photo_traits(photo)

    async def grade(self, image, *, hero, photo, caption, must_be_visible, subject="page",
                    traits=None, props=(), **kw):
        await super().grade(image, hero=hero, photo=photo, caption=caption,
                            must_be_visible=must_be_visible, subject=subject, traits=traits,
                            props=props, **kw)
        self.n[subject] += 1
        if subject == "hero" and self.n["hero"] == 1:
            return GradeResult(self.likeness, 0.9, boxes=[DEFAULT_FACE],
                               hair_texture_match=False)
        if subject == "page" and self.n["page"] == 1:
            return GradeResult(self.likeness, 0.9, boxes=[DEFAULT_FACE], skin_tone_match=False)
        return GradeResult(self.likeness, 0.9, boxes=[DEFAULT_FACE])


async def test_book_identity_mismatch_regenerates_hero_and_page_with_traits():
    fake, photo, grader = RecordingFake(), await _photo(), IdentityGrader()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[4], hero=HERO,
                          grader=grader)
    assert book.hero_attempts == 2 and book.hero_scores["passed"]
    first, second = _hero_prompts(fake)
    # attempt 1 waits for the traits and spells them out; so does the retry
    assert "The child has fair skin and straight black hair - keep exactly that." in first
    assert "Corrections" not in first
    assert "fair skin and straight black hair" in second
    assert "the hero's hair must be straight and black like in the photo" in second
    assert book.pages[0].attempts == 2 and book.pages[0].scores["passed"]
    page_prompts = [c["prompt"] for c, _ in _page_calls(fake)]
    assert "the hero's skin must be fair like in the photo" in page_prompts[1]
    # every grade got the photo traits (pages are compared with hero AND photo)
    assert all(c.traits == TRAITS for c in grader.calls)
    assert all(c.has_hero_ref for c in grader.calls if c.subject == "page")


async def test_hero_threshold_from_config(monkeypatch):
    monkeypatch.setenv("QUALITY_MIN_LIKENESS_HERO", "0.95")
    get_settings.cache_clear()
    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[4], hero=HERO,
                          grader=FakeGrader())  # likeness 0.9 everywhere
    assert book.hero_attempts == 3 and not book.hero_scores["passed"]
    assert book.pages[0].attempts == 1  # pages use QUALITY_MIN_LIKENESS (0.6)


# --- 2. family sheet ---------------------------------------------------------------------


def test_family_sheet_prompt_lists_every_family_member():
    story = load_story("red")
    chars = family_characters(story)
    assert [c.id for c in chars] == ["mother", "grandmother"]
    p = family_sheet_prompt(build_style_string("n64", 2), chars, "fam_c")
    assert "Mother and Grandma" in p and "exactly 2 characters, side by side" in p
    assert "warm medium brown" in p and "No child" in p
    assert [c.id for c in family_characters(load_story("jack"))] == ["mother"]


async def test_family_sheet_once_per_book_and_only_on_family_pages():
    fake, photo = RecordingFake(), await _photo()
    grader = FakeGrader(traits=PhotoTraits(6, "black", "coily"))
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1, 3, 4, 5],
                          hero=HERO, grader=grader)
    sheets = _sheet_calls(fake)
    assert len(sheets) == 1 and "deep brown" in sheets[0]["prompt"]
    assert sheets[0]["n_images"] == 0
    assert book.family_sheet_png and book.family_variant == "fam_d"
    story = load_story("jack")
    for call, refs in _page_calls(fake):
        if call["prompt"].startswith("Task: draw a small square"):
            continue  # a speaker portrait, not a page
        page = next(p for p in story.pages if p.scene in call["prompt"])
        if page_family(story, page):
            assert refs[1] == book.family_sheet_png
            assert "Image 2 is the family reference sheet - Mother must look exactly" in (
                call["prompt"])
        else:
            assert book.family_sheet_png not in refs
            assert "family reference sheet" not in call["prompt"]
    kinds = [c.kind for c in book.costs]
    assert kinds.count("family_sheet") == 1
    assert next(c for c in book.costs if c.kind == "family_sheet").ok


async def test_no_family_sheet_without_family_pages_or_grader():
    fake, photo = RecordingFake(), await _photo()
    await run_book(fake, photo, "n64", 2, story_id="jack", pages=[4, 5], hero=HERO,
                   grader=FakeGrader())
    assert _sheet_calls(fake) == []
    fake = RecordingFake()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO)
    assert _sheet_calls(fake) == [] and book.family_sheet_png is None


async def test_family_sheet_skipped_when_bible_sheets_exist():
    fake, photo = RecordingFake(), await _photo()
    grader = FakeGrader(traits=PhotoTraits(6, "black", "coily"))
    assets = MemoryAssets(bible={("mother", "fam_d"): b"MOTHER_D"})
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=grader, assets=assets)
    assert _sheet_calls(fake) == [] and book.family_sheet_png is None
    (call, refs), = _page_calls(fake)
    # Mother's bible sheet, then the cow's supporting sheet (made by this book)
    assert refs[1:] == [b"MOTHER_D", assets.sheets[("milky_white", "")]]
    assert "family reference sheet" not in call["prompt"]


async def test_family_sheet_fills_only_missing_bible_sheets():
    """Red: Mother has a bible sheet, Grandma not -> sheet made; Mother-only pages use the
    bible sheet, Grandma pages the family sheet."""
    story = load_story("red")
    mother_only = next(p.page for p in story.pages
                       if [c.id for c in page_family(story, p)] == ["mother"])
    grandma = next(p.page for p in story.pages
                   if "grandmother" in [c.id for c in page_family(story, p)]
                   and "mother" not in p.characters)
    fake, photo = RecordingFake(), await _photo()
    grader = FakeGrader(traits=PhotoTraits(1, "red", "curly"))  # fam_a
    assets = MemoryAssets(bible={("mother", "fam_a"): b"MOTHER_A"})
    book = await run_book(fake, photo, "ps1", 2, story_id="red",
                          pages=[mother_only, grandma], hero=HERO, grader=grader,
                          assets=assets)
    assert len(_sheet_calls(fake)) == 1
    by_page = {}
    for call, refs in _page_calls(fake):
        n = next(p.page for p in story.pages if p.scene in call["prompt"])
        by_page[n] = (call, refs)
    assert by_page[mother_only][1][1] == b"MOTHER_A"
    assert book.family_sheet_png not in by_page[mother_only][1]
    assert by_page[grandma][1][1] == book.family_sheet_png
    assert "family reference sheet - Grandma must look" in by_page[grandma][0]["prompt"]


class SheetFails(RecordingFake):
    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        if prompt.startswith(SHEET_TASK):
            self.calls.append({"prompt": prompt, "n_images": len(images)})
            self.images.append(list(images))
            raise ProviderError("sheet boom", status=500)
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


async def test_family_sheet_failure_is_recorded_and_tolerated(caplog):
    fake, photo = SheetFails(), await _photo()
    with caplog.at_level(logging.ERROR, logger="app.pipeline.family_sheet"):
        book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                              grader=FakeGrader())
    assert book.family_sheet_png is None and len(book.pages) == 1
    failed = [c for c in book.costs if c.kind == "family_sheet"]
    assert len(failed) == 1 and not failed[0].ok and "sheet boom" in failed[0].error
    (call, refs), = _page_calls(fake)
    # hero and Milky-White's supporting sheet - no family sheet, no photo
    assert len(refs) == 2 and "family reference sheet" not in call["prompt"]
    assert "Image 2 is the reference sheet for Milky-White" in call["prompt"]
    assert "family sheet failed" in caplog.text


class Timed(FakeProvider):
    """Every image call sleeps; the family sheet ``sheet_s``, others ``image_s``."""

    def __init__(self, image_s=0.3, sheet_s=0.3):
        super().__init__()
        self.image_s, self.sheet_s = image_s, sheet_s
        self.spans: dict[str, list[tuple[float, float]]] = {}

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        kind = ("sheet" if prompt.startswith(SHEET_TASK) else
                "hero" if prompt.startswith(HERO_TASK) else prompt.split("Scene: ")[1][:20])
        t0 = time.perf_counter()
        await asyncio.sleep(self.sheet_s if kind == "sheet" else self.image_s)
        self.spans.setdefault(kind, []).append((t0, time.perf_counter()))
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


class SlowGrader(FakeGrader):
    traits_done = 0.0

    async def photo_traits(self, photo):
        await asyncio.sleep(0.05)
        self.traits_done = time.perf_counter()
        return await super().photo_traits(photo)

    async def grade(self, *a, **k):
        await asyncio.sleep(0.05)
        return await super().grade(*a, **k)


async def test_family_sheet_overlaps_hero_and_stays_off_the_critical_path():
    prov, photo, grader = Timed(), await _photo(), SlowGrader()
    t0 = time.perf_counter()
    await run_book(prov, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                   grader=grader)
    total = time.perf_counter() - t0
    (sheet_start, sheet_end), = prov.spans["sheet"]
    (hero_start, hero_end), = prov.spans["hero"]
    page_start = min(s for k, v in prov.spans.items() if k not in ("sheet", "hero")
                     for s, _ in v)
    # the hero waits for the photo traits (~2 s for real, .05 here) ...
    assert hero_start >= grader.traits_done
    # ... and the family sheet still runs beside it
    assert sheet_start < hero_end
    assert page_start >= sheet_end  # ready before the family page starts
    # traits .05 + hero .3 + grade .05 + page .3 + grade .05 = .75; sequential with the
    # sheet would add another .3
    assert total < 0.95


async def test_hero_starts_without_traits_when_the_traits_call_fails():
    class TraitsFail(FakeGrader):
        async def photo_traits(self, photo):
            raise ProviderError("vision down", status=500)

    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[4], hero=HERO,
                          grader=TraitsFail())
    first = _hero_prompts(fake)[0]
    assert "The child has" not in first and book.hero_attempts == 1
    assert [c for c in book.costs if c.kind == "family_match"][0].error


async def test_non_family_page_does_not_wait_for_a_slow_sheet():
    story = load_story("jack")
    prov, photo = Timed(image_s=0.05, sheet_s=0.6), await _photo()
    await run_book(prov, photo, "n64", 2, story_id="jack", pages=[1, 4], hero=HERO,
                   grader=FakeGrader())
    (sheet_start, sheet_end), = prov.spans["sheet"]
    p4 = story.page(4).caption[:20]
    p1 = story.page(1).caption[:20]
    assert prov.spans[p4][0][0] < sheet_end  # page 4 (no family) started before the sheet
    assert prov.spans[p1][0][0] >= sheet_end  # page 1 (Mother) waited for it


# --- 3. HW1 -------------------------------------------------------------------------------


async def test_hw1_family_match_and_sheet_with_grader():
    fake, photo = RecordingFake(), await _photo()
    recorded = []

    async def record(c):
        recorded.append(c)

    res = await run_hw1(fake, photo, "n64", 2,
                        grader=FakeGrader(traits=PhotoTraits(6, "black", "coily")),
                        record=record)
    assert res.family_variant == "fam_d" and res.family_sheet_png
    assert sorted(c.kind for c in recorded) == ["family_match", "family_sheet",
                                                "family_sheet_check"]
    assert [c.kind for c in res.extra_calls] == [c.kind for c in recorded]
    assert len(_sheet_calls(fake)) == 1 and len(fake.calls) == 4
    scenes = {c["prompt"].split("Scene: ")[1][:25]: (c, imgs) for c, imgs in
              zip(fake.calls, fake.images, strict=True) if "Scene: " in c["prompt"]}
    (beanstalk, b_refs), (planting, p_refs) = scenes.values()
    assert len(b_refs) == 1 and "family reference sheet" not in beanstalk["prompt"]
    assert p_refs[1] == res.family_sheet_png and "deep brown" in planting["prompt"]


async def test_hw1_without_grader_uses_default_variant():
    fake, photo = RecordingFake(), await _photo()
    res = await run_hw1(fake, photo, "n64", 2)
    assert res.family_variant == "fam_a" and res.family_sheet_png is None
    assert len(fake.calls) == 3 and res.extra_calls == []
    assert sorted(c["n_images"] for c in fake.calls) == [1, 1, 1]


async def test_hw1_hero_failure_still_raises_provider_error():
    class HeroFails(FakeProvider):
        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            if prompt.startswith(HERO_TASK):
                raise ProviderError("hero boom", status=500)
            await asyncio.sleep(0.05)
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)

    with pytest.raises(ProviderError, match="hero boom"):
        await run_hw1(HeroFails(), await _photo(), "n64", 2, grader=FakeGrader())


# --- 4. region boxes in text placement ------------------------------------------------------

S01 = get_layout("s01_home_two_shot")  # zones: bottom, then top


def test_region_boxes_are_ignored_but_faces_never_are():
    assert (OBJECT_MAX_AREA, OBJECT_MAX_WIDTH) == (0.30, 0.85)
    garden = Box("the garden below the window", 0.0, 0.55, 0.9, 0.45, "key_object")
    assert place_text(S01, [garden]).zone.zone == "bottom"
    # narrow but very wide strip (only 0.09 of the area): also a region
    ground = Box("grass", 0.05, 0.8, 0.9, 0.1, "key_object")
    assert place_text(S01, [ground]).zone.zone == "bottom"
    # a real object in the bottom zone still moves the text
    bean = Box("purple bean", 0.45, 0.8, 0.05, 0.04, "key_object")
    assert place_text(S01, [bean]).zone.zone == "top"
    # a huge face is still a face
    big_face = Box("giant face", 0.0, 0.5, 1.0, 0.5, "face")
    assert place_text(S01, [big_face]).zone.zone == "top"


def test_region_area_threshold_from_config(monkeypatch):
    box = Box("big chest", 0.2, 0.6, 0.6, 0.4, "key_object")  # 0.24 of the image
    assert place_text(S01, [box]).zone.zone == "top"
    monkeypatch.setenv("OBJECT_MAX_AREA", "0.2")
    get_settings.cache_clear()
    assert place_text(S01, [box]).zone.zone == "bottom"


# --- 5. bean sizes ---------------------------------------------------------------------------


def test_beans_are_small_in_jack_art_fields():
    story = load_story("jack")
    for n in (2, 3, 19):
        p = story.page(n)
        art = " ".join([p.caption, p.plate_prompt, p.hero_instruction, *p.must_be_visible])
        assert "about the size of a fingertip" in art, n
        assert "small" in p.caption, n
    assert "about the size of a fingertip" in " ".join(story.page(19).must_be_visible)
    # approved text and titles unchanged
    assert story.page(19).title == "Planting the last bean"
    assert story.page(2).text == "Five magic beans, said the old man. For one cow."


# --- 7. quota errors ---------------------------------------------------------------------------

QUOTA_0 = ("Gemini API error 429: You exceeded your current quota, please check your plan "
           "and billing details. Quota exceeded for metric: generativelanguage.googleapis.com/"
           "generate_content_free_tier_requests, limit: 0, model: gemini-3-pro-image")
RATE = ("Gemini API error 429: You exceeded your current quota, please check your plan and "
        "billing details. Quota exceeded for metric: generativelanguage.googleapis.com/"
        "generate_requests_per_model_per_minute, limit: 20. Please retry in 7s.")


@pytest.mark.parametrize(("message", "exhausted"), [
    (QUOTA_0, True),
    ("Gemini API error 429: You exceeded your current quota.", True),
    # out of credit (prepaid balance used up): the exact real message, no delay / metric
    ("Gemini API error 429: You exceeded your current quota, please check your plan and "
     "billing details.", True),
    ("Gemini API error 429: limit:0 for this project", True),
    (RATE, False),
    ("Gemini API error 429: Resource has been exhausted (e.g. check quota).", False),
    ("Gemini API error 429: too many requests, limit: 10", False),
])
def test_quota_exhausted_detection(message, exhausted):
    assert ProviderError(message, status=429).quota_exhausted is exhausted
    assert not ProviderError(message, status=500).quota_exhausted


def test_quota_messages_and_logging(caplog):
    with caplog.at_level(logging.ERROR, logger="app.api"):
        err = provider_error(ProviderError(QUOTA_0, status=429))
    assert (err.status, err.message) == (503, UNAVAILABLE_MESSAGE)
    assert UNAVAILABLE_MESSAGE == "Image generation isn't available right now."
    assert any(r.levelno == logging.ERROR and "out of credit" in r.getMessage()
               for r in caplog.records)
    err = provider_error(ProviderError(RATE, status=429))
    assert (err.status, err.message) == (503, BUSY_MESSAGE)


def test_quota_exhausted_is_not_retried():
    assert not quality.is_retryable(ProviderError(QUOTA_0, status=429))
    assert quality.is_retryable(ProviderError(RATE, status=429))
