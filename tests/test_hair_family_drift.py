"""Hero hair drift and family drift: story wording, apparent ages, the always-on look
rules, the family sheet check (one regeneration) and the page grader's ``hero_hair_match``
/ ``family_match`` checks (book and HW1)."""

import io
import json
import logging
import re

import pytest
from PIL import Image

from app.pipeline import quality
from app.pipeline.book import run_book
from app.pipeline.calls import CallCost
from app.pipeline.family_sheet import (
    FAMILY_SHEET_CHECK_KIND,
    family_characters,
    make_family_sheet,
)
from app.pipeline.hw1 import run_hw1
from app.pipeline.prompts import (
    FAMILY_LOOK_RULE,
    HERO_LOOK_RULE,
    RefSpec,
    family_correction,
    family_expectations,
    family_sheet_prompt,
    scene_prompt,
)
from app.providers.base import GradeResult, ProviderError, SheetCheck
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import GRADE_INSTRUCTIONS, GeminiVisionGrader, _Grade
from app.stories.loader import _parse_story, load_story
from app.styles.presets import build_style_string
from tests.test_quality import HERO, SHEET_TASK, RecordingFake, _page_calls, _photo

N64 = build_style_string("n64", 2)
# wording that changes a character's hair length / style or age between pages
DRIFT = re.compile(r"hair (blown|flying|streaming|whipping|flowing|loose|windswept)|"
                   r"windswept|wind-blown|tired|grey-haired|elderly mother", re.I)


# --- story data ---------------------------------------------------------------------------


def test_no_art_field_restyles_hair_or_ages_a_character():
    for sid in ("jack", "red"):
        story = load_story(sid)
        for p in story.pages:
            for text in (p.caption, p.plate_prompt, p.hero_instruction):
                assert not DRIFT.search(text), (sid, p.page, text)
        for c in family_characters(story):
            assert not DRIFT.search(c.description), (sid, c.id)
    p16 = load_story("jack").page(16).hero_instruction
    assert "hair blown" not in p16 and "satchel strap" in p16
    assert "usual length and style" in p16


def test_family_characters_state_an_apparent_age():
    jack, red = load_story("jack"), load_story("red")
    mother = jack.characters["mother"]
    assert mother.age == 30 and not mother.elderly
    assert "around 30" in mother.description and "no grey hair" in mother.description
    assert "headscarf" in mother.description and "well-worn" in mother.description
    assert red.characters["mother"].age == 30
    grandma = red.characters["grandmother"]
    assert grandma.age == 70 and grandma.elderly and "around 70" in grandma.description
    # Grandma's hair is old in every variant (consistent with her age)
    assert all("silver" in v.hair for v in grandma.variants.values())
    assert jack.characters["old_man"].age is None  # optional outside the family


@pytest.mark.parametrize("age", [None, 0, 200, "thirty", True, 30.5])
def test_family_character_needs_a_valid_age(age):
    char = {"id": "mum", "name": "Mum", "family": True, "description": "a mum"}
    if age is not None:
        char["age"] = age
    with pytest.raises(ValueError, match="age"):
        _parse_story({"id": "x", "characters": [char], "pages": [{"page": 1}]}, "x")
    ok = _parse_story({"id": "x", "characters": [{"id": "dog", "description": "a dog"}],
                       "pages": [{"page": 1}]}, "x")
    assert ok.characters["dog"].age is None


# --- prompts ------------------------------------------------------------------------------


def test_page_prompt_always_locks_the_hero_look_and_family_only_on_family_pages():
    jack = load_story("jack")
    p4 = scene_prompt(N64, jack.page(4), jack, "fam_c")
    p16 = scene_prompt(N64, jack.page(16), jack, "fam_c")
    assert HERO_LOOK_RULE in p4 and HERO_LOOK_RULE in p16
    assert "wind, speed or motion never makes the hair longer" in HERO_LOOK_RULE
    assert FAMILY_LOOK_RULE not in p4 and FAMILY_LOOK_RULE in p16
    assert "same apparent age, hair colour, hairstyle, face and outfit" in FAMILY_LOOK_RULE
    assert "hair blown" not in p16
    assert "Apparent age: about 30 years old (a young adult: no grey or white hair" in p16
    assert "curly black hair gathered in a loose bun" in p16


def test_family_sheet_prompt_spells_out_age_and_variant_hair():
    jack = load_story("jack")
    p = family_sheet_prompt(N64, family_characters(jack), "fam_c")
    assert ("- Mother: about 30 years old (a young adult: no grey or white hair, no "
            "wrinkles); skin tone: warm medium brown; hair: curly black hair gathered in a "
            "loose bun") in p
    assert ("Mother must look young (Mother about 30): no grey, silver or white hair, no "
            "wrinkles, no stooped or elderly posture.") in p
    assert "elderly." not in p  # no elderly character in Jack
    red = load_story("red")
    p = family_sheet_prompt(N64, family_characters(red), "fam_b")
    assert "Grandma: about 70 years old (elderly)" in p
    assert "straight silver-white hair in a neat bun" in p
    assert "Mother must look young (Mother about 30)" in p and "Only Grandma is elderly." in p
    fixed = family_sheet_prompt(N64, family_characters(jack), "fam_c",
                                ["Mother must look about 30, no grey hair"])
    assert "Corrections - a previous attempt" in fixed
    assert "- Mother must look about 30, no grey hair" in fixed


def test_family_expectations_and_correction():
    red = load_story("red")
    lines = family_expectations(family_characters(red), "fam_a")
    assert lines[0].startswith("Mother: about 30 years old") and "curly auburn hair" in lines[0]
    assert lines[1].startswith("Grandma: about 70 years old (elderly)")
    fix = family_correction(family_characters(red)[:1], "fam_a")
    assert fix.startswith("each family member must look exactly like the family reference "
                          "sheet - Mother: about 30")
    assert family_correction([], "fam_a") == ""


# --- grader schema / instructions ------------------------------------------------------------


def test_grade_schema_and_instructions_cover_hero_hair_and_family():
    assert {"hero_hair_match", "family_match", "family_notes"} <= set(_Grade.model_fields)
    text = " ".join(GRADE_INSTRUCTIONS.split())
    assert "hero_hair_match - compare the hero's hair in image 1 with the HERO REFERENCE" in text
    assert "A little movement is fine" in text and "clearly longer, loose and flowing" in text
    assert "family_match - only when a family reference sheet is given" in text
    assert "grey or white hair and wrinkles on a mother who is young on the sheet" in text


class _Resp:
    usage_metadata = None
    model_version = "m"

    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[list] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append(contents)
        return _Resp(json.dumps(self.payload))


def _gemini(payload: dict):
    g = GeminiVisionGrader(api_key="test-key", model="gemini-3.8-flash")
    models = _Models(payload)
    g._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    return g, models


def _png(size=(64, 64), colour=(120, 90, 60)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


GRADE_FAILS = {"likeness": 0.8, "fidelity": 0.8, "has_text_artifacts": False,
               "skin_tone_match": True, "hair_colour_match": True, "hair_texture_match": True,
               "identity_notes": "hair is long and loose", "hero_hair_match": False,
               "family_match": False, "family_notes": "Mother has grey hair",
               "cast_match": False, "cast_notes": "the wolf is too big",
               "expression_match": False, "expression_notes": "smiling",
               "hero_child_ok": True, "hero_items_ok": True, "hero_outfit_ok": True,
               "hero_look_notes": "",
               "contradictions": [], "issues": [], "boxes": []}


async def test_gemini_grade_sends_family_sheet_and_lines():
    g, models = _gemini(GRADE_FAILS)
    big = _png((1536, 2048))
    res = await g.grade(big, hero=big, photo=big, caption="c", must_be_visible=[],
                        family_ref=big, family=["Mother: about 30 years old; hair: black curls"])
    text, *parts = models.calls[0]
    assert len(parts) == 4  # page, hero, photo, family sheet - each downscaled
    for part in parts:
        with Image.open(io.BytesIO(part.inline_data.data)) as im:
            assert max(im.size) == 768
    assert "(the HERO REFERENCE" in text
    assert "Image 4 is the family reference sheet" in text
    assert "- Mother: about 30 years old; hair: black curls" in text
    assert not res.hero_hair_match and not res.family_match and not res.identity_ok
    assert res.family_notes == "Mother has grey hair"


async def test_gemini_grade_without_references_never_fails_the_new_checks():
    g, models = _gemini(GRADE_FAILS)
    res = await g.grade(_png(), hero=None, photo=_png(), caption="c", must_be_visible=[],
                        family=["Mother: about 30"])  # lines without a sheet are ignored
    text, *parts = models.calls[0]
    assert len(parts) == 2 and "Image 3 is the family reference sheet" not in text
    assert "Mother: about 30" not in text
    assert res.hero_hair_match and res.family_match and res.family_notes == ""


async def test_gemini_family_sheet_check():
    g, models = _gemini({"count_ok": True, "no_child": True, "age_ok": False,
                         "hair_ok": True, "issues": ["Mother must look about 30"]})
    chk = await g.check_family_sheet(_png((1536, 2048)),
                                     characters=["Mother: about 30 years old"])
    text, part = models.calls[0]
    assert "exactly 1 adult family member(s)" in text and "- Mother: about 30 years old" in text
    assert "grey, silver or white hair, wrinkles" in text
    with Image.open(io.BytesIO(part.inline_data.data)) as im:
        assert max(im.size) == 768
    assert not chk.passed and chk.failures == 1 and chk.issues == ["Mother must look about 30"]


# --- quality ----------------------------------------------------------------------------------


def test_hero_hair_and_family_mismatch_fail_like_identity():
    g = GradeResult(0.95, 0.95, hero_hair_match=False, identity_notes="long loose hair")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    assert not chk.passed and chk.issues[:2] == [quality.HERO_HAIR_FIX, "long loose hair"]
    hint = "each family member must look exactly like the family reference sheet - Mother: x"
    g = GradeResult(0.95, 0.95, family_match=False, family_notes="Mother has grey hair")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, family_hint=hint)
    assert not chk.passed and chk.issues[:2] == [hint, "Mother has grey hair"]
    chk = quality.evaluate(GradeResult(0.9, 0.9, family_match=False), min_likeness=0.6,
                           min_fidelity=0.6)
    assert chk.issues == [quality.FAMILY_FIX]
    assert GradeResult(0.9, 0.9, family_match=False).score < GradeResult(0.7, 0.7).score
    assert GradeResult(0.9, 0.9, hero_hair_match=False).score < GradeResult(0.7, 0.7).score
    assert quality.evaluate(GradeResult(0.9, 0.9), min_likeness=0.6, min_fidelity=0.6).passed


# --- book: page grading -----------------------------------------------------------------------


async def test_book_grader_gets_hero_ref_and_family_sheet_only_on_family_pages():
    jack = load_story("jack")
    fake, photo, grader = RecordingFake(), await _photo(), FakeGrader()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1, 4], hero=HERO,
                          grader=grader)
    by = {c.caption: c for c in grader.calls if c.subject == "page"}
    p1, p4 = by[jack.page(1).caption], by[jack.page(4).caption]
    assert p1.has_hero_ref and p4.has_hero_ref
    assert p1.family_ref == book.family_sheet_png
    assert p1.family == family_expectations([jack.characters["mother"]], book.family_variant)
    assert p4.family_ref is None and p4.family == []
    hero = next(c for c in grader.calls if c.subject == "hero")
    assert hero.family_ref is None and not hero.has_hero_ref


async def test_book_family_mismatch_regenerates_with_a_concrete_correction():
    jack = load_story("jack")
    seen = {"n": 0}

    def fn(call, n):
        if call.subject == "page":
            seen["n"] += 1
            if seen["n"] == 1:
                return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], family_match=False,
                                   family_notes="Mother looks elderly with grey hair")
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])

    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=FakeGrader(fn=fn), family_variant="fam_c")
    assert book.pages[0].attempts == 2 and book.pages[0].scores["passed"]
    second = _page_calls(fake)[1][0]["prompt"]
    assert family_correction([jack.characters["mother"]], "fam_c") in second
    assert "curly black hair gathered in a loose bun" in second
    assert "Mother looks elderly with grey hair" in second


async def test_book_hero_hair_drift_regenerates_the_page():
    def fn(call, n):
        if call.subject == "page" and n == 1:  # call 0 is the hero
            return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], hero_hair_match=False)
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])

    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[16], hero=HERO,
                          grader=FakeGrader(fn=fn))
    assert book.pages[0].attempts == 2
    assert quality.HERO_HAIR_FIX in _page_calls(fake)[1][0]["prompt"]


# --- family sheet check -------------------------------------------------------------------------


class SheetRecorder(RecordingFake):
    """Keeps every family sheet PNG it returns, in order."""

    def __init__(self):
        super().__init__()
        self.sheets: list[bytes] = []

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        img = await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        if prompt.startswith(SHEET_TASK):
            self.sheets.append(img.png)
        return img


OLD_MOTHER = SheetCheck(age_ok=False, hair_ok=False,
                        issues=["Mother must look about 30: no grey hair, no wrinkles"])


def _kinds(costs):
    return sorted(c.kind for c in costs if c.kind.startswith("family_sheet"))


async def test_failed_sheet_check_regenerates_once_with_the_notes():
    fake, photo = SheetRecorder(), await _photo()
    grader = FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER if n == 0 else SheetCheck())
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=grader, family_variant="fam_c")
    assert len(fake.sheets) == 2 and book.family_sheet_png == fake.sheets[1]
    second = [c["prompt"] for c in fake.calls if c["prompt"].startswith(SHEET_TASK)][1]
    assert "- Mother must look about 30: no grey hair, no wrinkles" in second
    assert len(grader.sheet_calls) == 2
    assert grader.sheet_calls[0].characters == family_expectations(
        family_characters(load_story("jack")), "fam_c")
    assert _kinds(book.costs) == ["family_sheet", "family_sheet", "family_sheet_check",
                                  "family_sheet_check"]
    (_, refs), = _page_calls(fake)
    assert refs[1] == fake.sheets[1]  # the page uses the regenerated sheet


async def test_passing_sheet_check_costs_one_call_and_no_regeneration():
    fake, photo, grader = SheetRecorder(), await _photo(), FakeGrader()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=grader)
    assert len(fake.sheets) == 1 and len(grader.sheet_calls) == 1
    check = next(c for c in book.costs if c.kind == FAMILY_SHEET_CHECK_KIND)
    assert check.ok and check.model == "fake-vision" and check.page is None


async def test_two_failed_checks_keep_the_better_sheet(caplog):
    worse = SheetCheck(count_ok=False, age_ok=False, hair_ok=False, issues=["x"])
    fake, photo = SheetRecorder(), await _photo()
    grader = FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER if n == 0 else worse)
    with caplog.at_level(logging.WARNING, logger="app.pipeline.family_sheet"):
        book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                              grader=grader)
    assert len(fake.sheets) == 2 and book.family_sheet_png == fake.sheets[0]
    assert "failed its check twice" in caplog.text and "keeping attempt 1" in caplog.text
    # a tie keeps the second (it had the first one's notes as corrections)
    fake, grader = SheetRecorder(), FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER)
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=grader)
    assert book.family_sheet_png == fake.sheets[1]
    assert len(fake.sheets) == 2 and len(grader.sheet_calls) == 2  # never a third


async def _direct(grader, may_retry=None):
    fake, calls = SheetRecorder(), []

    async def image_call(kind, page, prompt, refs):
        return await fake.edit(prompt, refs)

    async def record(c: CallCost):
        calls.append(c)

    png = await make_family_sheet(image_call, N64, load_story("jack"), "fam_c",
                                  grader=grader, record=record, may_retry=may_retry)
    return png, fake, calls


async def test_no_regeneration_when_the_time_budget_is_used_up():
    grader = FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER)
    png, fake, calls = await _direct(grader, may_retry=lambda: False)
    assert png == fake.sheets[0] and len(fake.sheets) == 1
    assert [c.kind for c in calls] == [FAMILY_SHEET_CHECK_KIND]


async def test_failed_check_call_accepts_the_sheet_unchecked():
    class CheckDown(FakeGrader):
        async def check_family_sheet(self, image, *, characters):
            raise ProviderError("vision down", status=503)

    png, fake, calls = await _direct(CheckDown())
    assert png == fake.sheets[0] and len(fake.sheets) == 1
    (c,), = [calls]
    assert c.kind == FAMILY_SHEET_CHECK_KIND and not c.ok and "vision down" in c.error


async def test_sheet_without_grader_is_not_checked():
    png, fake, calls = await _direct(None)
    assert png == fake.sheets[0] and calls == []


async def test_book_sheet_regeneration_respects_the_time_budget():
    """Budget 0.0001 s: used up by the time the first check fails -> no regeneration."""
    fake, photo = SheetRecorder(), await _photo()
    grader = FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER)
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=grader, time_budget_s=0.0001)
    assert len(fake.sheets) == 1 and book.family_sheet_png == fake.sheets[0]


# --- HW1 ----------------------------------------------------------------------------------------


async def test_hw1_sheet_check_regenerates_once_and_scene_uses_the_new_sheet():
    fake, photo = SheetRecorder(), await _photo()
    grader = FakeGrader(sheet_fn=lambda call, n: OLD_MOTHER if n == 0 else SheetCheck())
    recorded = []

    async def record(c):
        recorded.append(c)

    res = await run_hw1(fake, photo, "n64", 2, grader=grader, record=record)
    assert len(fake.sheets) == 2 and res.family_sheet_png == fake.sheets[1]
    assert sorted(c.kind for c in recorded) == ["family_match", "family_sheet", "family_sheet",
                                                "family_sheet_check", "family_sheet_check"]
    scene = [imgs for c, imgs in zip(fake.calls, fake.images, strict=True)
             if "Image 2 is the family reference sheet" in c["prompt"]]
    assert scene == [[res.hero.png, fake.sheets[1]]]  # no photo on scenes
    assert grader.calls == []  # HW1 scenes stay ungraded


def test_scene_prompt_family_rule_with_plate_refs():
    red = load_story("red")
    page = next(p for p in red.pages if "grandmother" in p.characters)
    refs = (RefSpec("hero"), RefSpec("photo"), RefSpec("plate"),
            RefSpec("family_sheet", name="Grandma"))
    p = scene_prompt(N64, page, red, "fam_a", refs=refs)
    assert HERO_LOOK_RULE in p and FAMILY_LOOK_RULE in p
    assert "Apparent age: about 70 years old (elderly)." in p
