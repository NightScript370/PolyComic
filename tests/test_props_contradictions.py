"""Canonical story props (Jack's five beans), grader contradictions that fail a picture,
the per-preset clothing note, and the props line given to the grader."""

import re

import pytest

from app.pipeline import quality
from app.pipeline.family_sheet import family_characters
from app.pipeline.prompts import (
    PROPS_HEADING,
    family_sheet_prompt,
    hero_prompt,
    page_props,
    plate_prompt,
    scene_prompt,
)
from app.providers.base import CONTRADICTION_PENALTY_CAP, GradeResult, ImageResult
from app.providers.fake_grader import FakeGrader
from app.providers.gemini_vision import GRADE_INSTRUCTIONS, _Grade, props_note
from app.stories.loader import _parse_story, load_story
from app.styles.presets import PRESETS, build_style_string, outfit_note
from tests.test_quality import HERO, RecordingFake, _photo
from tests.test_styles import _assert_no_brands

FIVE_BEANS = ("exactly five small magic beans, each about the size of a fingertip - one red, "
              "one yellow, one green, one blue, one purple; five beans in total, never more")

# --- 1. props: loader, validation, story audit ------------------------------------------


@pytest.mark.parametrize("story_id", ["jack", "red"])
def test_every_page_prop_id_is_known(story_id):
    story = load_story(story_id)  # unknown ids would already raise here
    used = {pid for p in story.pages for pid in p.props}
    assert used and used <= set(story.props)
    for p in story.pages:
        assert len(p.props) == len(set(p.props)), (story_id, p.page)
        assert all(story.props[pid] for pid in p.props)


def test_unknown_prop_id_is_an_error():
    data = {"id": "x", "props": {"beans": "five beans"},
            "pages": [{"page": 1, "props": ["beans", "harp"]}]}
    with pytest.raises(ValueError, match=r"page 1: unknown prop id.*harp"):
        _parse_story(data, "x")
    ok = _parse_story({"id": "x", "pages": [{"page": 1}]}, "x")
    assert ok.props == {} and ok.pages[0].props == []


def test_jack_beans_are_canonical_and_consistent():
    story = load_story("jack")
    assert story.props["beans"] == FIVE_BEANS
    assert "purple" in story.props["last_bean"] and "exactly one" in story.props["last_bean"]
    pages = {p.page: p for p in story.pages}
    assert pages[2].props == ["beans"] and pages[3].props == ["beans", "cottage"]
    assert pages[19].props == ["last_bean"]
    # every page whose art shows a bean (not the beanstalk) references a bean prop
    for p in story.pages:
        art = " ".join([p.caption, p.plate_prompt, p.hero_instruction, *p.must_be_visible])
        shows_bean = re.search(r"\bbeans?\b", art.replace("beans fell", ""))
        if shows_bean:
            assert {"beans", "last_bean"} & set(p.props), p.page
    # captions / must-be-visible agree with the prop: five on 2 and 3, the purple one on 19
    assert "exactly five small magic beans" in pages[2].caption
    assert any("exactly five" in m for m in pages[2].must_be_visible)
    assert "the five beans scatter" in pages[3].caption
    assert "the purple one" in pages[3].caption
    assert any("purple bean" in m for m in pages[3].must_be_visible)
    assert "purple" in pages[3].hero_instruction
    assert "purple bean" in pages[19].caption
    assert any("purple bean" in m for m in pages[19].must_be_visible)
    for n in (2, 3):  # no bare "beans scatter" / "flying beans" without a count
        assert "small flying beans" not in pages[n].must_be_visible
    # approved text and titles unchanged
    assert pages[3].text == "Beans! she cried. And out the window they went."
    assert pages[3].title == "Out the window"


def test_red_props_are_conservative():
    story = load_story("red")
    by_prop: dict[str, list[int]] = {}
    for p in story.pages:
        for pid in p.props:
            by_prop.setdefault(pid, []).append(p.page)
    # only objects that recur across pages
    assert all(len(v) >= 2 for v in by_prop.values()), by_prop
    assert {1, 2, 8, 13} <= set(by_prop["basket"])
    assert {1, 11, 12} <= set(by_prop["whistle"])
    assert "about the length of a finger" in story.props["whistle"]


def test_scene_and_plate_prompts_carry_the_props_block():
    story = load_story("jack")
    style = build_style_string("n64", 2)
    p2 = story.page(2)
    sp = scene_prompt(style, p2, story)
    assert f"{PROPS_HEADING}\n- beans: {FIVE_BEANS}" in sp
    assert sp.index(PROPS_HEADING) < sp.index("Clearly visible in the picture")
    assert f"- beans: {FIVE_BEANS}" in plate_prompt(style, p2, story)
    assert PROPS_HEADING not in scene_prompt(style, story.page(1), story)
    assert page_props(story, story.page(19))[0].startswith("last bean: exactly one small "
                                                           "purple")


async def test_grader_gets_the_same_prop_lines():
    from app.pipeline.book import run_book

    fake, photo, grader = RecordingFake(), await _photo(), FakeGrader()
    await run_book(fake, photo, "n64", 2, story_id="jack", pages=[2, 4], hero=HERO,
                   grader=grader)
    by_caption = {c.caption[:20]: c for c in grader.calls}
    story = load_story("jack")
    assert by_caption[story.page(2).caption[:20]].props == [
        f"beans: {FIVE_BEANS}. On this page: all five beans lie together in the old man's "
        "open palm - one of each colour, none anywhere else"]
    assert all(not c.props for c in grader.calls if c.subject == "hero")
    note = props_note([f"beans: {FIVE_BEANS}"])
    assert "contradiction" in note and f"- beans: {FIVE_BEANS}" in note
    assert props_note([]) == ""


# --- 2. contradictions --------------------------------------------------------------------


def test_grader_schema_and_instructions_define_contradictions():
    assert "contradictions" in _Grade.model_fields and "issues" in _Grade.model_fields
    text = GRADE_INSTRUCTIONS
    assert "contradictions: DEFINITE violations" in text
    assert "a must-be-visible item or a required character that is missing" in text
    assert "These do NOT count" in text
    assert "framing or cropping choices" in text
    # hair texture is judged on the type category, also for short curly hair
    assert "straight, wavy, curly or coily" in text
    assert "short, tight curls" in text and "bumpy, lumpy or clumped" in text


def test_contradiction_fails_even_with_high_scores_and_leads_corrections():
    g = GradeResult(0.95, 0.95, issues=["the hero could smile more"],
                    contradictions=["show exactly five beans, not about ten"],
                    hair_texture_match=False)
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    assert not chk.passed
    assert chk.issues[0] == "show exactly five beans, not about ten"
    assert chk.issues[1].startswith("the hero's hair must be")
    assert "the hero could smile more" in chk.issues
    # the same grade without contradictions or identity problems passes
    assert quality.evaluate(GradeResult(0.95, 0.95, issues=["the hero could smile more"]),
                            min_likeness=0.6, min_fidelity=0.6).passed


def test_contradictions_cost_score_capped():
    base = GradeResult(0.8, 0.8)
    one = GradeResult(0.8, 0.8, contradictions=["a"])
    many = GradeResult(0.8, 0.8, contradictions=list("abcdefg"))
    assert one.score == pytest.approx(base.score - 0.15)
    assert many.score == pytest.approx(base.score - CONTRADICTION_PENALTY_CAP)


async def test_loop_feeds_contradictions_and_best_of_three_prefers_fewer():
    # equal likeness/fidelity everywhere: only the number of contradictions differs
    grades = [
        GradeResult(0.9, 0.9, contradictions=["show exactly five beans, not about ten",
                                              "the beans must be five colours, not all "
                                              "purple"]),
        GradeResult(0.9, 0.9, contradictions=["one bean is missing"]),
        GradeResult(0.9, 0.9, contradictions=["a", "b", "c"]),
    ]
    seen: list[list[str]] = []

    async def generate(n, corrections, quiet):
        seen.append(list(corrections))
        return ImageResult(png=f"a{n}".encode(), model="m")

    async def check(n, image):
        return quality.evaluate(grades[n - 1], min_likeness=0.6, min_fidelity=0.6)

    res = await quality.run_quality_loop(generate=generate, check=check, max_attempts=3)
    assert res.count == 3 and not any(a.passed for a in res.attempts)
    assert seen[1][:2] == ["show exactly five beans, not about ten",
                           "the beans must be five colours, not all purple"]
    assert seen[2][0] == "one bean is missing"
    # 0.9-0.30=0.60 ; 0.9-0.15=0.75 ; 0.9-0.45=0.45 -> attempt 2 (fewest contradictions)
    assert res.chosen.n == 2
    assert GradeResult(0.7, 0.7).score > GradeResult(0.8, 0.8, contradictions=["a", "b"]).score


async def test_fake_grader_contradictions_default_empty_and_override():
    ok = await FakeGrader().grade(b"x", hero=None, photo=b"p", caption="c", must_be_visible=[])
    bad = await FakeGrader(contradictions=["wrong count"]).grade(
        b"x", hero=None, photo=b"p", caption="c", must_be_visible=[])
    assert ok.contradictions == [] and bad.contradictions == ["wrong count"]
    assert not quality.evaluate(bad, min_likeness=0.6, min_fidelity=0.6).passed


# --- 3. clothing note per preset ------------------------------------------------------------


def test_outfit_note_per_preset_and_placement():
    jack, red = load_story("jack"), load_story("red")
    n64 = build_style_string("n64", 2)
    assert outfit_note(n64) == PRESETS["n64"].outfit_note
    assert "no stitching, weave or texture detail" in outfit_note(n64)
    assert outfit_note("some other style") == ""
    for preset in PRESETS:
        for detail in (1, 2, 3):
            assert outfit_note(build_style_string(preset, detail)) == PRESETS[preset].outfit_note
        _assert_no_brands(PRESETS[preset].outfit_note)
    note = outfit_note(n64)
    hp = hero_prompt(n64, jack.hero_outfit)
    assert hp.index("Outfit (always") < hp.index(note)
    sp = scene_prompt(n64, jack.page(2), jack)
    assert sp.index("Hero outfit: ") < sp.index(note)
    fp = family_sheet_prompt(n64, family_characters(red), "fam_a")
    assert fp.index("Characters:") < fp.index(note) < fp.index("Composition:")
    # the YAML outfit itself is untouched
    assert "patched" in jack.hero_outfit.lower()


# --- cut-out / sticker look --------------------------------------------------------------

STICKER = "re-draw the hero as part of the scene with no white outline"


def test_grader_instructions_make_a_sticker_look_a_contradiction():
    text = GRADE_INSTRUCTIONS
    contra = text[text.index("contradictions: DEFINITE"):text.index("These do NOT count")]
    assert "cut-out/sticker look" in contra and "visibly pasted in" in contra
    assert "halo or glow around a figure" in contra
    assert STICKER in contra
    assert "If you are not sure, put it in issues instead." in " ".join(text.split())
    assert "cut-out/sticker look" in _Grade.model_fields["contradictions"].description


async def test_fake_grader_sticker_contradiction_fails_the_page():
    grader = FakeGrader(contradictions=[STICKER])
    grade = await grader.grade(b"png", hero=b"h", photo=b"p", caption="c",
                               must_be_visible=[])
    assert grade.contradictions == [STICKER]
    chk = quality.evaluate(grade, min_likeness=0.6, min_fidelity=0.6)
    assert not chk.passed and chk.issues[0] == STICKER
