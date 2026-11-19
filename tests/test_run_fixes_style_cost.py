"""Third real-run fixes (Jack ps1 / Red n64) and the per-book cost cap: style drift,
the fallen beanstalk, giant sizes, supporting-sheet ranking, supporting-character
expressions, no mirrored gestures, the canonical quilt / cottages / castle, hero-sheet
trivia, close-up portraits, the page-dependent whistle, the grader's JSON retry and media
resolution, and the cost cap."""

import io

import pytest
from google.genai import errors as genai_errors
from PIL import Image

from app.pipeline import quality
from app.pipeline.book import (
    MAX_CAST_REFS,
    PORTRAIT_RESERVE,
    SOFT_RESERVE,
    BookGenerator,
    MemoryAssets,
    plan_book,
    run_book,
)
from app.pipeline.calls import CallCost
from app.pipeline.portraits import _fallback_fixes, portrait_prompt
from app.pipeline.prompts import (
    NO_MIRROR_RULE,
    OTHER_EXPRESSION_RULE,
    cast_expectations,
    cast_expression_lines,
    hero_prompt,
    page_props,
    scene_prompt,
)
from app.providers.base import CallMeta, GradeResult, PortraitCheck
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import (
    GRADE_INSTRUCTIONS,
    GeminiVisionGrader,
    outfit_trivia,
)
from app.stories.loader import _parse_story, load_story
from app.styles.presets import build_style_string, style_correction, style_summary
from app.tiers import Tier, tier_config
from tests.test_quality import HERO, _photo

PS1 = build_style_string("ps1", 2)
N64 = build_style_string("n64", 2)
INSTR = " ".join(GRADE_INSTRUCTIONS.split())


# --- 1. style drift -------------------------------------------------------------------------


def test_page_prompt_repeats_the_style_technique_near_the_end():
    jack = load_story("jack")
    prompt = scene_prompt(PS1, jack.page(3), jack)
    tail = prompt[prompt.index("Corrections") if "Corrections" in prompt else -1200:]
    assert "Rendering style (most important" in tail
    assert "pixelated nearest-neighbour textures" in tail
    assert "never a flat 2D illustration" in tail
    # ... after the composition rules, right before the strict rules
    assert prompt.index("Rendering style") > prompt.index("Compose the scene")
    assert prompt.index("Rendering style") < prompt.index("Strict rules")
    # the hero reference (the style anchor) gets it too, without "like the reference"
    hero = hero_prompt(N64, jack.hero_outfit)
    assert "low-polygon 3D render" in hero and "exactly like" not in hero.split(
        "Rendering style")[1].split(";")[0]


def test_style_mismatch_is_a_definite_error_naming_the_technique():
    g = GradeResult(likeness=0.9, fidelity=0.9, style_match=False,
                    style_notes="flat 2D painting; the book is low-poly 3D")
    hint = style_correction(PS1)
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, style_hint=hint)
    assert not chk.passed and chk.definite
    assert chk.issues[0].startswith("render the WHOLE picture as a real-time low-polygon 3D")
    assert "flat 2D painting" in chk.issues[1]
    assert g.hard_failures == 1


async def test_pages_and_hero_send_the_style_to_the_grader():
    grader = FakeGrader()
    await run_book(FakeProvider(), await _photo(), "ps1", 2, story_id="jack", pages=[4],
                   hero=HERO, grader=grader)
    assert {c.subject for c in grader.calls} == {"hero", "page"}
    assert all(c.style == style_summary(PS1) for c in grader.calls)
    assert "style_match" in INSTR and "flat 2D" in INSTR


# --- 2./3. beanstalk and giant sizes ----------------------------------------------------------


def test_fallen_beanstalk_is_straight_and_flat_on_p18():
    jack = load_story("jack")
    line = [x for x in page_props(jack, jack.page(18)) if x.startswith("beanstalk")][0]
    assert "ONE STRAIGHT line" in line and "next to the short chopped stump" in line
    assert "An arched, bent, curved-up or still-standing beanstalk is wrong" in line
    assert "STRAIGHT" in jack.page(18).art_caption and "cottage" in jack.page(18).props


def test_every_giant_page_anchors_his_size():
    jack = load_story("jack")
    for n in jack.characters["giant"].pages:
        assert jack.page(n).sizes.get("giant"), n
    assert "four times" in jack.page(15).sizes["giant"]
    assert "right behind" in jack.page(15).sizes["giant"]
    assert "as big as the cottage" in jack.page(18).sizes["giant"]
    line = cast_expectations([jack.characters["giant"]], 15, jack.page(15).sizes)[0]
    assert "Size on this page: towering right behind the hero child" in line
    assert "Size on this page" in scene_prompt(PS1, jack.page(15), jack)


# --- 4. supporting sheets ranked by importance ------------------------------------------------


async def test_the_giant_sheet_is_sent_first_and_three_fit():
    assets = MemoryAssets(sheets={("harp", ""): b"HARP", ("hen", ""): b"HEN",
                                  ("giant", ""): b"GIANT"})
    grader = FakeGrader()

    class Rec(FakeProvider):
        def __init__(self):
            super().__init__()
            self.images = []

        async def edit(self, prompt, images, **kw):
            self.images.append((prompt, list(images)))
            return await super().edit(prompt, images, **kw)

    fake = Rec()
    await run_book(fake, await _photo(), "n64", 2, story_id="jack", pages=[14], hero=HERO,
                   grader=grader, assets=assets)
    (prompt, refs), = [x for x in fake.images if x[0].startswith("Task: illustrate")]
    assert MAX_CAST_REFS == 3 and refs[1:] == [b"GIANT", b"HARP", b"HEN"]
    page = [c for c in grader.calls if c.subject == "page"][0]
    assert page.cast_refs == [b"GIANT", b"HARP", b"HEN"]
    assert page.cast[0].startswith("the giant:")
    jack = load_story("jack")
    assert [c.ref_priority for c in (jack.characters[x] for x in ("giant", "hen", "harp"))] \
        == [0, 2, 2]
    for cid in ("giant", "giants_wife", "old_man", "mother"):
        assert any(w in jack.characters[cid].description for w in ("wearing", "dress")), cid


def test_ref_priority_defaults_from_size():
    s = _parse_story({"id": "t", "characters": [
        {"id": "a", "size": "Small: knee high"}, {"id": "b", "size": "A tall man"},
        {"id": "c", "ref_priority": 0}]}, "t")
    assert [s.characters[k].ref_priority for k in "abc"] == [2, 1, 0]


# --- 5. supporting-character expressions -------------------------------------------------------


def test_wolf_has_page_expressions_and_no_default_grin():
    red = load_story("red")
    wolf = red.characters["wolf"]
    assert "grin" not in wolf.description and "grin" not in wolf.sheet_description
    for n in (2, 3, 7, 8, 9, 10, 11, 13, 14, 15, 17):
        assert red.page(n).expressions.get("wolf"), n
    assert "dazed and flustered" in red.page(13).expressions["wolf"]
    prompt = scene_prompt(N64, red.page(13), red)
    assert "Expression on this page: dazed and flustered" in prompt
    assert OTHER_EXPRESSION_RULE in prompt
    jack = load_story("jack")
    for n in (9, 11, 12, 13, 14, 15, 18):
        assert jack.page(n).expressions.get("giant"), n


def test_expressions_must_name_page_characters():
    with pytest.raises(ValueError, match="not in the page's characters"):
        _parse_story({"id": "t", "characters": [{"id": "wolf"}], "pages": [
            {"page": 1, "characters": [], "expressions": {"wolf": "dazed"}}]}, "t")


async def test_wrong_supporting_expression_fails_definitely():
    red = load_story("red")
    lines = cast_expression_lines(red, red.page(13))
    assert lines == [f"the wolf: {red.page(13).expressions['wolf']}"]
    g = GradeResult(likeness=0.9, fidelity=0.9, cast_expression_match=False,
                    cast_expression_notes="the wolf grins; he must look dazed")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6,
                           cast_expression_hint="the wolf must look dazed")
    assert not chk.passed and chk.definite and "the wolf must look dazed" in chk.issues
    grader = FakeGrader()
    await run_book(FakeProvider(), await _photo(), "n64", 2, story_id="red", pages=[13],
                   hero=HERO, grader=grader)
    page = [c for c in grader.calls if c.subject == "page"][0]
    assert page.cast_expressions == lines


# --- 6. no mirrored gestures --------------------------------------------------------------------


def test_red_p8_hero_does_not_copy_grandmas_gesture():
    red = load_story("red")
    p8 = red.page(8)
    assert "does NOT copy" in p8.hero_instruction and "chest" in p8.hero_instruction
    assert "no finger at the lips" in p8.expression
    assert NO_MIRROR_RULE in scene_prompt(N64, p8, red)
    assert NO_MIRROR_RULE not in scene_prompt(N64, red.page(4), red)  # nobody to copy
    assert "copying another character's specific gesture" in INSTR


# --- 7. quilt, cottages, castle -------------------------------------------------------------------


def test_canonical_places_and_quilt():
    red = load_story("red")
    assert "rose pink, butter yellow and sage green" in red.props["quilt"]
    for n in (7, 8, 9, 10, 11, 13):
        assert "quilt" in red.page(n).props, n
    assert "golden-straw thatched roof" in red.props["cottage"]
    for n in (3, 5, 6, 7, 15, 16, 18, 19):
        assert "cottage" in red.page(n).props, n
    jack = load_story("jack")
    for n in (3, 4, 5, 16, 18):
        assert "cottage" in jack.page(n).props, n
    assert "three round towers" in jack.props["castle"]
    assert "Grandma's cottage must have white walls" in INSTR


# --- 8a. hero sheet trivia ------------------------------------------------------------------------


async def test_hero_sheet_is_not_failed_for_patch_or_satchel_side():
    assert outfit_trivia("the patch must be on the left elbow")
    assert outfit_trivia("the satchel should be on the left hip")
    assert not outfit_trivia("remove the second figure")
    grader = FakeGrader()
    await run_book(FakeProvider(), await _photo(), "ps1", 2, story_id="jack", pages=[4],
                   hero=HERO, grader=grader)
    hero, page = grader.calls[0], grader.calls[1]
    assert hero.subject == "hero" and "left elbow" not in hero.hero_outfit
    assert "oatmeal-coloured linen tunic" in hero.hero_outfit
    assert "left elbow" in page.hero_outfit  # pages keep the continuity text


async def test_gemini_hero_grade_drops_trivia_contradictions():
    g, models = _grader(answers=[_answer(contradictions=[
        "the patch should be on the left elbow", "remove the second figure"])])
    res = await g.grade(_png(), hero=None, photo=_png(), caption="c", must_be_visible=[],
                        subject="hero")
    assert res.contradictions == ["remove the second figure"]
    assert "which elbow or knee a patch is on" in models.calls[0]["text"]


# --- 8b. close-up portraits -----------------------------------------------------------------------


def test_portrait_prompt_and_check_demand_a_close_up():
    p = portrait_prompt(N64, "the hero child", "desc", "scared", "the hero reference")
    assert "CLOSE-UP" in p and "Never a full body" in p and "clearly not calm" in p
    assert "Rendering style" in p
    chk = PortraitCheck(closeup_ok=False)
    assert chk.failures == 1 and not chk.passed
    assert any("close-up" in f for f in _fallback_fixes("the hero child", chk, "scared"))


# --- 8c. one whistle ------------------------------------------------------------------------------


def test_whistle_is_handed_over_on_p1_and_worn_from_p2():
    red = load_story("red")
    assert "whistle" not in red.hero_outfit
    assert "whistle" not in red.hero_outfit_for(1)
    assert "whistle" in red.hero_outfit_for(2) and "whistle" in red.hero_outfit_for(12)
    assert "whistle" not in hero_prompt(N64, red.hero_outfit)
    p1 = scene_prompt(N64, red.page(1), red)
    assert "NOT wearing a whistle yet" in p1
    p12 = [x for x in page_props(red, red.page(12)) if x.startswith("whistle")][0]
    assert "still on its red ribbon" in p12 and "lifted to the lips" in p12


# --- 8d./8e. grader JSON retry and media resolution -----------------------------------------------


def _png(size=(64, 64)) -> bytes:
    out = io.BytesIO()
    Image.effect_noise(size, 40).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _answer(**kw) -> str:
    import json

    base = {"likeness": 0.9, "fidelity": 0.9, "has_text_artifacts": False,
            "skin_tone_match": True, "hair_colour_match": True, "hair_texture_match": True,
            "identity_notes": "", "hero_hair_match": True, "family_match": True,
            "family_notes": "", "cast_match": True, "cast_notes": "",
            "expression_match": True, "expression_notes": "", "hero_child_ok": True,
            "hero_items_ok": True, "hero_outfit_ok": True, "hero_look_notes": "",
            "contradictions": [], "issues": [], "boxes": []}
    base.update(kw)
    return json.dumps(base)


class _Usage:
    prompt_token_count = 1000
    candidates_token_count = 100
    thoughts_token_count = 0


class _Models:
    def __init__(self, answers, reject_media=False):
        self.answers = list(answers)
        self.calls = []
        self.reject_media = reject_media

    async def generate_content(self, *, model, contents, config):
        levels = [getattr(getattr(p, "media_resolution", None), "level", None)
                  for p in contents[1:]]
        self.calls.append({"text": contents[0], "levels": levels})
        if self.reject_media and any(levels):
            raise genai_errors.APIError(400, {"error": {
                "code": 400, "status": "INVALID_ARGUMENT",
                "message": "media_resolution is not supported"}})
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return type("R", (), {"text": text, "usage_metadata": _Usage(),
                              "model_version": "m"})()


def _grader(answers, **kw):
    from app.providers import gemini_vision

    gemini_vision._NO_MEDIA_RESOLUTION.clear()
    g = GeminiVisionGrader(api_key="k", model="gemini-3.8-flash", **kw)
    models = _Models(answers)
    g._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    return g, models


async def test_invalid_json_is_asked_again_once():
    g, models = _grader(answers=["{not json", _answer()])
    res = await g.grade(_png(), hero=None, photo=_png(), caption="c", must_be_visible=[])
    assert len(models.calls) == 2 and res.likeness == 0.9
    assert res.meta.input_tokens == 2000  # both tries counted
    g2, models2 = _grader(answers=["{bad", "{still bad"])
    with pytest.raises(Exception, match="invalid JSON"):
        await g2.grade(_png(), hero=None, photo=_png(), caption="c", must_be_visible=[])
    assert len(models2.calls) == 2


async def test_grader_images_use_lower_media_resolution_with_fallback():
    from google.genai import types

    from app.providers import gemini_vision

    g, models = _grader(answers=[_answer()], media_level="low", media_level_first="medium")
    await g.grade(_png(), hero=_png(), photo=_png(), caption="c", must_be_visible=[])
    assert models.calls[0]["levels"] == [types.PartMediaResolutionLevel.MEDIA_RESOLUTION_MEDIUM,
                                         types.PartMediaResolutionLevel.MEDIA_RESOLUTION_LOW,
                                         types.PartMediaResolutionLevel.MEDIA_RESOLUTION_LOW]
    g2, models2 = _grader(answers=[_answer()], media_level="low", media_level_first="medium")
    models2.reject_media = True
    await g2.grade(_png(), hero=None, photo=_png(), caption="c", must_be_visible=[])
    assert [any(c["levels"]) for c in models2.calls] == [True, False]
    assert "gemini-3.8-flash" in gemini_vision._NO_MEDIA_RESOLUTION
    gemini_vision._NO_MEDIA_RESOLUTION.clear()


# --- cost cap -------------------------------------------------------------------------------------


class _PricedFake(FakeProvider):
    async def edit(self, prompt, images, **kw):
        r = await super().edit(prompt, images, **kw)
        r.est_cost_usd = 0.03
        return r


def _failing_pages(definite: bool):
    def fn(call, n):
        meta = CallMeta(model="fake-vision", est_cost_usd=0.005)
        if call.subject == "hero":
            return GradeResult(likeness=0.9, fidelity=0.9, boxes=[DEFAULT_FACE], meta=meta)
        if definite:
            return GradeResult(likeness=0.9, fidelity=0.9, boxes=[DEFAULT_FACE], meta=meta,
                               contradictions=["show exactly five beans"])
        return GradeResult(likeness=0.9, fidelity=0.3, boxes=[DEFAULT_FACE], meta=meta,
                           issues=["could be brighter"])
    return fn


async def _book(cap: float, definite: bool = True):
    return await run_book(_PricedFake(), await _photo(), "n64", 2, story_id="jack",
                          pages=[4], hero=HERO, grader=FakeGrader(fn=_failing_pages(definite)),
                          time_budget_s=0, cost_cap_usd=cap)


async def test_without_a_cap_a_failing_page_gets_every_attempt():
    book = await _book(0.0)
    assert book.pages[0].attempts == 3 and not book.pages[0].cost_stopped
    assert book.cost_skips == []


async def test_the_cap_skips_retries_and_keeps_the_best_attempt():
    book = await _book(0.08)
    page = book.pages[0]
    assert page.attempts == 1 and page.cost_stopped and not page.budget_stopped
    assert book.cost_skips == ["page 4 retry (definite error)"]
    # required calls still ran: hero + page picture + both grades
    assert [c.kind for c in book.costs if c.kind in ("hero", "page")] == ["hero", "page"]


def _gen(cap: float) -> BookGenerator:
    plan = plan_book("jack", [4], "n64", 2)
    gen = BookGenerator(plan, HERO, b"x", hero_provider=FakeProvider(), grader=FakeGrader(),
                        cost_cap_usd=cap)
    gen.image_cost_est, gen.grade_cost_est = 0.03, 0.01
    gen._pending = set()
    return gen


def test_afford_orders_definite_before_soft_before_portraits():
    gen = _gen(1.0)
    gen.costs = [CallCost("page", 4, "m", 0.95, 1.0, 0, 0)]
    # 0.95 + 0.04 = 0.99: fits only without a reserve
    assert gen.afford(0.04, "definite", definite=True)
    assert not gen.afford(0.04, "soft")  # leaves less than SOFT_RESERVE
    assert not gen.afford(0.04, "portrait", portrait=True)
    assert gen.cost_skips == ["soft", "portrait"]
    gen.costs = [CallCost("page", 4, "m", 1.0 - 0.04 - SOFT_RESERVE - 0.001, 1.0, 0, 0)]
    assert gen.afford(0.04, "soft") and not gen.afford(0.04, "portrait", portrait=True)
    assert PORTRAIT_RESERVE > SOFT_RESERVE


def test_afford_reserves_pending_first_pictures_and_ignores_shared_sheets():
    gen = _gen(0.5)
    gen.costs = [CallCost("cast_sheet", None, "m", 5.0, 1.0, 0, 0),
                 CallCost("cast_sheet_check", None, "m", 1.0, 1.0, 0, 0),
                 CallCost("page", 4, "m", 0.1, 1.0, 0, 0)]
    assert gen.spent_usd() == pytest.approx(0.1)
    assert gen.afford(0.04, "x", definite=True)
    gen._pending = set(range(5, 14))  # 9 x 0.04 = 0.36 reserved: 0.1 + 0.36 + 0.04 = 0.5
    assert gen.afford(0.04, "x", definite=True)
    gen._pending = set(range(5, 15))
    assert not gen.afford(0.04, "x", definite=True)
    assert _gen(0.0).afford(99.0, "no cap")
    # a picture whose first image call is already recorded only reserves the rest
    gen.costs.append(CallCost("page", 14, "m", 0.03, 1.0, 0, 0))
    assert gen.pending_usd() == pytest.approx(9 * 0.04 + 0.01)


def test_tiers_carry_their_cost_cap():
    assert tier_config(Tier.COST_EFFICIENT).cost_cap_usd == pytest.approx(0.97)
    assert tier_config(Tier.PROFESSIONAL).cost_cap_usd == pytest.approx(3.70)
