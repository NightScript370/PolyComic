"""Second real-run fixes: blurred bands, page-dependent costumes and prop states, "exactly
one" counts and the one-moment rule, supporting-character reference sheets + size, hero
expressions, the budget exception for definite errors, and 429 handling (rate limit vs.
out of credit)."""

import asyncio
import io
import logging
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter
from sqlalchemy import select

from app.config import get_settings
from app.db import repo_assets
from app.db.models import CharacterBible
from app.db.session import get_sessionmaker
from app.pipeline import quality
from app.pipeline.bands import (
    _looks_unpainted,
    blurred_band_issue,
    find_blank_band,
    seam_issue,
)
from app.pipeline.book import (
    MAX_CAST_REFS,
    BookGenerator,
    MemoryAssets,
    MinuteLimiter,
    plan_book,
    rate_limit_delay,
    run_book,
)
from app.pipeline.calls import CALL_KINDS
from app.pipeline.cast_sheets import CAST_SHEET_CHECK_KIND, CAST_SHEET_KIND, needed_sheets
from app.pipeline.prompts import (
    FULL_FRAME_RULE,
    HERO_EXPRESSION_RULE,
    ONE_EACH_RULE,
    cast_correction,
    cast_expectations,
    cast_sheet_prompt,
    character_line,
    hero_prompt,
    page_props,
    scene_prompt,
)
from app.providers.base import (
    CastSheetCheck,
    GradeResult,
    ImageResult,
    ProviderError,
    parse_google_error,
    provider_error_from_google,
)
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini import log_output_size
from app.providers.gemini_vision import GRADE_INSTRUCTIONS, _Grade
from app.stories.loader import _parse_story, load_story
from app.styles.presets import CONSTRAINTS, build_style_string
from tests.test_hair_family_drift import _gemini, _png
from tests.test_quality import HERO, RecordingFake, _photo

SPRITE = build_style_string("sprite16", 2)
INSTR = " ".join(GRADE_INSTRUCTIONS.split())


def png(im: Image.Image, fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    im.save(out, format=fmt)
    return out.getvalue()


# --- 1. blurred bands ---------------------------------------------------------------------


def scene(size=(480, 640), seed=1, pixel=False) -> Image.Image:
    """A painted-looking picture: outlined shapes of many colours plus a little grain."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (90, 140, 80))
    d = ImageDraw.Draw(im)
    w, h = size
    for _ in range(160):
        x, y = rnd.randrange(w), rnd.randrange(h)
        sw, sh = rnd.randrange(15, 110), rnd.randrange(15, 110)
        col = tuple(rnd.randrange(30, 230) for _ in range(3))
        shape = d.rectangle if rnd.random() < 0.5 else d.ellipse
        shape([x, y, x + sw, y + sh], fill=col, outline=(20, 20, 20), width=2)
    im = Image.blend(im, Image.effect_noise(size, 18).convert("RGB"), 0.15)
    if pixel:
        im = im.resize((w // 6, h // 6), Image.Resampling.NEAREST).resize(
            size, Image.Resampling.NEAREST)
    return im


def blurred(edge="top", frac=0.15, radius=12, **kw) -> Image.Image:
    im = scene(**kw)
    w, h = im.size
    bh = int(h * frac)
    box = (0, 0, w, bh) if edge == "top" else (0, h - bh, w, h)
    im.paste(im.filter(ImageFilter.GaussianBlur(radius)).crop(box), box)
    return im


def smeared(frac=0.18) -> Image.Image:
    """The first sharp row stretched up to the top edge (outpainting smear)."""
    im = scene()
    w, h = im.size
    bh = int(h * frac)
    im.paste(im.crop((0, bh, w, bh + 1)).resize((w, bh)).filter(ImageFilter.GaussianBlur(3)),
             (0, 0))
    return im


def sky(frac=0.35, straight=True, clouds=False) -> Image.Image:
    """A smooth vertical-gradient sky over the scene; ragged = a hilly / treetop horizon."""
    im = scene(seed=3)
    w, h = im.size
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / h
        gd.line([(0, y), (w, y)], fill=(int(120 + 100 * t), int(170 + 65 * t),
                                         int(230 + 20 * t)))
    if clouds:
        cl = Image.new("L", (w, h), 0)
        cd = ImageDraw.Draw(cl)
        rnd = random.Random(7)
        for _ in range(8):
            x, y = rnd.randrange(w), rnd.randrange(int(h * 0.25))
            cd.ellipse([x - 60, y - 20, x + 60, y + 20], fill=200)
        grad.paste((255, 255, 255), (0, 0), cl.filter(ImageFilter.GaussianBlur(14)))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    if straight:
        md.rectangle([0, 0, w, int(h * frac)], fill=255)
    else:
        import math
        pts = [(x, int(h * frac + 0.08 * h * math.sin(x / 37) + 0.05 * h * math.sin(x / 11)))
               for x in range(0, w + 1, 4)]
        md.polygon([(0, 0), (w, 0)] + pts[::-1], fill=255)
    im.paste(grad, (0, 0), mask)
    return im


@pytest.mark.parametrize(("im", "edge"), [
    (blurred("top", 0.15), "top"),
    (blurred("bottom", 0.12), "bottom"),
    (blurred("top", 0.20, pixel=True), "top"),
    (smeared(), "top"),
])
def test_blurred_band_detected(im, edge):
    """A blurred strip behind a sharp straight boundary is a padding seam; the issue text
    matches the kind found."""
    for data in (png(im), png(im, "JPEG")):
        band = find_blank_band(data)
        assert band is not None and band.kind in ("seam", "blurred") and band.edge == edge
        assert band.issue == (seam_issue(edge) if band.kind == "seam"
                              else blurred_band_issue(edge))
    assert "no blurred, smeared or out-of-focus strip at the top" in blurred_band_issue("top")
    assert "no separate strip at the top joined by a straight seam" in seam_issue("top")


def padded(kind: str, size=(480, 640)) -> Image.Image:
    """What the real pages showed (all the right 3:4 size): a squarer picture painted
    inside the canvas and the leftover top / bottom filled behind a straight seam."""
    w, h = size
    pic = scene((w, int(w * 1.0)), seed=11)  # a square picture ...
    canvas = Image.new("RGB", size)
    top = (h - pic.size[1]) // 2
    canvas.paste(pic, (0, top))
    if kind == "blurred_copy":  # red p10: zoomed, blurred copy above and below
        zoom = pic.resize((w * 2, pic.size[1] * 2)).filter(ImageFilter.GaussianBlur(10))
        canvas.paste(zoom.crop((w // 2, 0, w // 2 + w, top)), (0, 0))
        canvas.paste(zoom.crop((w // 2, zoom.size[1] - (h - top - pic.size[1]),
                                w // 2 + w, zoom.size[1])), (0, top + pic.size[1]))
    elif kind == "wall_and_white":  # red p11: flat grey wall on top, white strip below
        canvas.paste((112, 108, 104), (0, 0, w, top))
        canvas.paste((242, 241, 236), (0, top + pic.size[1], w, h))
    elif kind == "sky_strip":  # red p2: an unrelated soft sky strip above a hard seam
        sky_strip = Image.new("RGB", (w, top))
        d = ImageDraw.Draw(sky_strip)
        for y in range(top):
            d.line([(0, y), (w, y)], fill=(150 + y // 2, 200 + y // 4, 235))
        canvas.paste(sky_strip, (0, 0))
        canvas = canvas.crop((0, 0, w, h - (h - top - pic.size[1])))
        canvas = canvas.resize(size)  # the picture reaches the bottom edge
    return canvas


@pytest.mark.parametrize("kind", ["blurred_copy", "wall_and_white", "sky_strip"])
def test_padding_seams_like_the_real_pages_are_found(kind):
    band = find_blank_band(png(padded(kind)))
    assert band is not None and band.edge in ("top", "bottom") and band.fraction >= 0.05


def legit_horizontal(kind: str, size=(480, 640)) -> Image.Image:
    """Legit full-width horizontals the real set had: a ceiling beam with planks above it,
    a tablecloth hem over a textured floor."""
    im = scene(size, seed=13)
    w, h = size
    d = ImageDraw.Draw(im)
    if kind == "beam":  # textured planks above a dark beam line at 12%
        planks = scene((w, int(h * 0.12)), seed=21)
        im.paste(planks, (0, 0))
        d.rectangle([0, int(h * 0.12), w, int(h * 0.12) + 10], fill=(60, 40, 25))
    else:  # tablecloth hem: detailed floor below a straight hem at 88%
        im.paste(scene((w, int(h * 0.12)), seed=22), (0, int(h * 0.88)))
        d.line([(0, int(h * 0.88)), (w, int(h * 0.88))], fill=(240, 220, 200), width=3)
    return im


@pytest.mark.parametrize("kind", ["beam", "hem"])
def test_legit_straight_horizontals_are_not_seams(kind):
    assert find_blank_band(png(legit_horizontal(kind))) is None


def test_pale_blue_sky_is_not_an_unpainted_colour():
    """A real Jack page: a flat pale-blue sky (162, 214, 227; saturation 0.29) blending into
    clouds was flagged as unpainted before PAPER_MAX_SATURATION went down to 0.22."""
    assert not _looks_unpainted((162, 214, 227))
    assert _looks_unpainted((245, 235, 215)) and _looks_unpainted((242, 241, 236))


@pytest.mark.parametrize("im", [
    scene(), scene(pixel=True),
    sky(straight=False),  # gradient sky above a ragged treeline
    sky(straight=False, clouds=True),  # soft clouds above a ragged horizon
    scene().filter(ImageFilter.GaussianBlur(6)),  # a softly painted picture as a whole
], ids=["scene", "pixel", "ragged-sky", "cloudy-ragged-sky", "soft-all"])
def test_legit_soft_areas_are_not_blurred_bands(im):
    assert find_blank_band(png(im)) is None


def test_known_limit_smooth_sky_above_a_dead_straight_cut():
    """A smooth sky strip above a perfectly straight full-width cut cannot be told apart
    from the real red p2 padding (a sky strip above a seam): it is flagged."""
    band = find_blank_band(png(sky()))
    assert band is not None and band.kind == "seam" and band.edge == "top"


def test_gradual_depth_of_field_is_not_a_band():
    im = scene(seed=5)
    w, h = im.size
    out = im.copy()
    for i, r in enumerate([10, 8, 6, 4, 3, 2, 1]):
        y0, y1 = int(h * i * 0.04), int(h * (i + 1) * 0.04)
        out.paste(im.filter(ImageFilter.GaussianBlur(r)).crop((0, y0, w, y1)), (0, y0))
    assert find_blank_band(png(out)) is None


def test_grader_makes_blurred_padded_bands_and_duplicates_contradictions():
    contra = INSTR[INSTR.index("contradictions: DEFINITE"):INSTR.index("These do NOT count")]
    assert "blurred-out, smeared, padded or letterboxed band" in contra
    assert "always a contradiction, never just an issue" in contra
    assert "the same character drawn more than once" in contra
    assert "draw the wolf only once" in contra and "only one nightcap" in contra
    desc = _Grade.model_fields["contradictions"].description
    assert "blurred-out, smeared, padded or letterboxed" in desc and "drawn twice" in desc


def test_page_prompt_composes_for_the_frame_and_quiet_zones_ask_for_no_blur():
    red = load_story("red")
    p = scene_prompt(SPRITE, red.page(12), red)
    assert FULL_FRAME_RULE in p and "letterbox" in FULL_FRAME_RULE
    for sid in ("red", "jack"):
        story = load_story(sid)
        for n in (10, 12):
            page = story.page(n)
            prompt = scene_prompt(SPRITE, page, story)
            assert "out-of-focus" not in prompt and "out of focus" not in prompt
            assert "softly blurred" not in prompt and "blurry" not in prompt
            # the reader-facing caption is unchanged; the model gets art_caption if set
            assert page.scene in prompt


def test_log_output_size_warns_on_another_aspect(caplog):
    square = _png((512, 512))
    with caplog.at_level(logging.DEBUG, logger="app.providers.gemini"):
        assert log_output_size(square, "3:4", [_png((640, 480))])
        assert not log_output_size(_png((768, 1024)), "3:4", [])
    assert "returned 512x512 for requested aspect 3:4 (refs 640x480)" in caplog.text


# --- 2. costumes per page -----------------------------------------------------------------


def test_wolf_costume_page_range():
    red = load_story("red")
    wolf = red.characters["wolf"]
    assert "nightcap" not in wolf.description and "shawl" not in wolf.description
    disguise = {p.page for p in red.pages if "wolf" in p.characters
                and wolf.sheet_costume_id(p.page) == "grandma_disguise"}
    assert disguise == {7, 8, 9, 10, 11, 13, 14, 15}
    assert wolf.costume_for(17).id == "cap_flying" and wolf.sheet_costume_id(17) == ""
    assert wolf.costume_for(3) is None and wolf.costume_for(18) is None
    assert [c.id if c else "" for c in wolf.sheet_costumes()] == ["", "grandma_disguise"]
    grandma = red.characters["grandmother"]
    assert "never wears a nightcap" in grandma.description


def test_character_line_says_what_is_worn_on_each_page():
    wolf = load_story("red").characters["wolf"]
    p3, p9, p17 = (character_line(wolf, None, n) for n in (3, 9, 17))
    assert "wears no costume: just his own grey fur: no nightcap" in p3
    assert "On this page the wolf wears: Grandma's frilly white nightcap" in p9
    assert "flying off his head" in p17 and "none on it" in p17
    assert "Size: Standing upright he is about one and a half times" in p9
    assert "wears" not in character_line(wolf)  # no page: no costume line


def test_costume_validation():
    def story(costumes):
        return {"id": "x", "pages": [{"page": 1}], "characters": [
            {"id": "fox", "description": "a fox", "costumes": costumes}]}
    with pytest.raises(ValueError, match="two costumes"):
        _parse_story(story([{"id": "a", "pages": [1, 2], "description": "hat"},
                            {"id": "b", "pages": [2], "description": "coat"}]), "x")
    with pytest.raises(ValueError, match="needs an `id`"):
        _parse_story(story([{"pages": [1], "description": "hat"}]), "x")


async def test_grader_gets_the_wolf_outfit_for_the_page():
    red = load_story("red")
    grader = FakeGrader()
    await run_book(RecordingFake(), await _photo(), "sprite16", 2, story_id="red",
                   pages=[3, 9, 17], hero=HERO, grader=grader)
    by = {c.caption: c for c in grader.calls if c.subject == "page"}
    assert "wears no costume" in by[red.page(3).scene].cast[0]
    assert "wears: Grandma's frilly white nightcap" in by[red.page(9).scene].cast[0]
    assert "flying off his head" in by[red.page(17).scene].cast[1]


# --- 3. exactly one / one moment / prop states -----------------------------------------------


def test_prop_counts():
    red = load_story("red")
    for pid in ("basket", "whistle", "nightcap", "broom", "axe", "wardrobe", "cottage"):
        assert "exactly one in the whole picture, never two" in red.props[pid], pid
    jack = load_story("jack")
    assert "exactly one in the whole picture" in jack.props["axe"]
    assert "axe" in jack.page(17).props
    s = _parse_story({"id": "x", "props": {"cups": {"description": "cups", "count": 3}},
                      "pages": [{"page": 1, "props": ["cups"]}]}, "x")
    assert s.props["cups"] == "cups - exactly three in the whole picture, never more"
    with pytest.raises(ValueError, match="count"):
        _parse_story({"id": "x", "props": {"cups": {"description": "c", "count": 0}},
                      "pages": [{"page": 1}]}, "x")


def test_every_page_prompt_has_the_one_moment_and_expression_rules():
    for sid in ("red", "jack"):
        story = load_story(sid)
        for page in story.pages:
            p = scene_prompt(SPRITE, page, story)
            assert ONE_EACH_RULE in p and HERO_EXPRESSION_RULE in p
    assert "no before-and-after or motion-sequence duplicates" in ONE_EACH_RULE


def test_red_one_moment_pages():
    red = load_story("red")
    p13, p11, p1 = red.page(13), red.page(11), red.page(1)
    assert "already lies flopped" in p13.art_caption and "only once" in p13.art_caption
    assert "only wolf in the picture" in p13.plate_prompt
    assert "exactly one nightcap - on his head" in p11.art_caption
    assert "basket on the left arm" not in p1.hero_instruction
    assert "only basket in the picture" in p1.hero_instruction
    # the reader-facing captions are untouched
    assert p13.caption.startswith("The hero child runs around the little table.")
    assert p11.caption.startswith("The wolf springs out of bed")


def test_prop_states_per_page():
    jack = load_story("jack")
    p17, p18 = page_props(jack, jack.page(17)), page_props(jack, jack.page(18))
    assert "On this page: being chopped down: a deep wedge-shaped notch" in p17[0]
    assert "ONE STRAIGHT line" in p18[0] and "chopped stump" in p18[0]
    assert "An arched, bent, curved-up or still-standing beanstalk is wrong" in p18[0]
    assert "On this page" not in page_props(jack, jack.page(4))[0]
    p3 = page_props(jack, jack.page(3))[0]
    assert "ONE clear arc" in p3 and "none on the windowsill" in p3
    assert "purple bean lies on the doorstep right by the hero child's boot" in p3
    assert "mother holds the handle" in jack.page(17).art_caption
    assert "ONE STRAIGHT line" in jack.page(18).art_caption
    assert any("flat and straight" in m for m in jack.page(18).must_be_visible)
    with pytest.raises(ValueError, match="overlapping"):
        _parse_story({"id": "x", "props": {"tree": {"description": "a tree", "states": [
            {"pages": [1], "description": "a"}, {"pages": [1], "description": "b"}]}},
            "pages": [{"page": 1}]}, "x")


def test_grader_treats_a_wrong_prop_state_as_a_contradiction():
    contra = INSTR[INSTR.index("contradictions: DEFINITE"):INSTR.index("These do NOT count")]
    assert "a prop in the wrong state for THIS page" in contra
    assert "bends in an arch over the stump" in contra
    assert "or a Props line states it (then it IS a contradiction)" in INSTR


# --- 4. supporting-character sheets + size ----------------------------------------------------


def test_every_supporting_character_states_its_size():
    for sid in ("red", "jack"):
        for c in load_story(sid).characters.values():
            assert "child" in c.size, (sid, c.id)
    giant = load_story("jack").characters["giant"].size
    assert "at least about two and a half times the child's height" in giant
    assert "cut off by the frame" in giant and "never about the child's size" in giant


def test_cast_sheet_prompt_has_a_height_bar_and_no_child():
    wolf = load_story("red").characters["wolf"]
    p = cast_sheet_prompt(SPRITE, wolf, wolf.costume("grandma_disguise"))
    assert "plain vertical grey bar" in p and "as tall as the hero child" in p
    assert "No child" in p and "Outfit on this sheet: Grandma's frilly white nightcap" in p
    plain = cast_sheet_prompt(SPRITE, wolf)
    assert "Outfit on this sheet: just his own grey fur" in plain


def test_needed_sheets():
    red = load_story("red")
    keys = needed_sheets(red, red.pages)
    assert keys == [("wolf", ""), ("wolf", "grandma_disguise"), ("woodcutter", "")]
    assert set(CALL_KINDS) >= {CAST_SHEET_KIND, CAST_SHEET_CHECK_KIND}
    assert all(len(k) <= 32 for k in CALL_KINDS)


async def test_first_book_makes_the_sheet_and_later_books_reuse_it():
    assets = MemoryAssets()
    fake, grader = RecordingFake(), FakeGrader()
    photo = await _photo()
    book = await run_book(fake, photo, "sprite16", 2, story_id="red", pages=[3, 9],
                          hero=HERO, grader=grader, assets=assets)
    assert set(assets.sheets) == {("wolf", ""), ("wolf", "grandma_disguise")}
    kinds = [c.kind for c in book.costs]
    assert kinds.count(CAST_SHEET_KIND) == 2 and kinds.count(CAST_SHEET_CHECK_KIND) == 2
    assert {c.name for c in grader.cast_sheet_calls} == {"the wolf"}
    pages = [(c, imgs) for c, imgs in zip(fake.calls, fake.images, strict=True)
             if c["prompt"].startswith("Task: illustrate")]
    by = {("p9" if "nightcap" in c["prompt"].split("Scene:")[1][:300] else "p3"): imgs
          for c, imgs in pages}
    assert assets.sheets[("wolf", "grandma_disguise")] in by["p9"]
    assert assets.sheets[("wolf", "")] in by["p3"]
    page_grades = [c for c in grader.calls if c.subject == "page"]
    assert all(len(c.cast_refs) == 1 and c.cast for c in page_grades)

    again, grader2 = RecordingFake(), FakeGrader()
    book2 = await run_book(again, photo, "sprite16", 2, story_id="red", pages=[3, 9],
                           hero=HERO, grader=grader2, assets=assets)
    assert CAST_SHEET_KIND not in [c.kind for c in book2.costs]
    assert grader2.cast_sheet_calls == []


async def test_two_concurrent_books_make_one_sheet():
    assets, photo = MemoryAssets(), await _photo()
    fakes = [RecordingFake(), RecordingFake()]
    books = await asyncio.gather(*(run_book(f, photo, "n64", 2, story_id="red", pages=[18],
                                            hero=HERO, grader=FakeGrader(), assets=assets)
                                   for f in fakes))
    made = sum(c.kind == CAST_SHEET_KIND for b in books for c in b.costs)
    assert made == 2  # wolf + woodcutter, once each - not once per book
    assert set(assets.sheets) == {("wolf", ""), ("woodcutter", "")}


async def test_failed_sheet_check_regenerates_once_and_keeps_the_better():
    def sheet_fn(call, n):
        return CastSheetCheck(size_ok=n > 0, issues=["draw the wolf 1.5x the bar"])
    grader = FakeGrader(cast_sheet_fn=sheet_fn)
    fake = RecordingFake()
    book = await run_book(fake, await _photo(), "n64", 2, story_id="red", pages=[3],
                          hero=HERO, grader=grader, assets=MemoryAssets())
    sheet_prompts = [c["prompt"] for c in fake.calls
                     if c["prompt"].startswith("Task: draw a character reference sheet for the "
                                               "wolf")]
    assert len(sheet_prompts) == 2 and "draw the wolf 1.5x the bar" in sheet_prompts[1]
    assert [c.kind for c in book.costs].count(CAST_SHEET_CHECK_KIND) == 2


async def test_no_sheet_generation_without_a_grader():
    fake = RecordingFake()
    await run_book(fake, await _photo(), "n64", 2, story_id="red", pages=[3], hero=HERO)
    assert not any("reference sheet for the wolf" in c["prompt"] for c in fake.calls)


async def test_supporting_refs_are_capped():
    assets = MemoryAssets(sheets={("harp", ""): b"HARP", ("hen", ""): b"HEN",
                                  ("giant", ""): b"GIANT"})
    fake = RecordingFake()
    await run_book(fake, await _photo(), "n64", 2, story_id="jack", pages=[14], hero=HERO,
                   grader=FakeGrader(), assets=assets)
    (call, refs), = [(c, i) for c, i in zip(fake.calls, fake.images, strict=True)
                     if c["prompt"].startswith("Task: illustrate")]
    # ranked by ref_priority: the giant first (his clothes changed on p14 without a sheet)
    assert refs[1:] == [b"GIANT", b"HARP", b"HEN"] and MAX_CAST_REFS == 3
    assert "plain grey height bar" in call["prompt"] and "never draw the grey bar" in call[
        "prompt"]


async def test_insert_sheet_is_idempotent_on_the_unique_key():
    async with get_sessionmaker()() as s:
        first = await repo_assets.insert_sheet(s, story="red", character_id="wolf",
                                               costume_id="grandma_disguise", preset="n64",
                                               png=b"A", prompt="p", model="m")
        second = await repo_assets.insert_sheet(s, story="red", character_id="wolf",
                                                costume_id="grandma_disguise", preset="n64",
                                                png=b"B", prompt="p", model="m")
        plain = await repo_assets.insert_sheet(s, story="red", character_id="wolf",
                                               costume_id="", preset="n64", png=b"C",
                                               prompt="p", model="m")
        assert (first, second, plain) == (b"A", b"A", b"C")
        rows = (await s.execute(select(CharacterBible))).scalars().all()
        assert len(rows) == 2
        assert await repo_assets.bible_keys(s, "red", "n64") == {("wolf", "")}
        assert await repo_assets.sheet_keys(s, "red", "n64") == {
            ("wolf", "", ""), ("wolf", "", "grandma_disguise")}


def test_cast_mismatch_fails_with_a_concrete_correction():
    red = load_story("red")
    wolf = [red.characters["wolf"]]
    hint = cast_correction(wolf, 9)
    assert "the wolf must look exactly like their reference sheet" in hint
    assert "size: Standing upright" in hint and "nightcap" in hint
    g = GradeResult(0.9, 0.9, cast_match=False, cast_notes="the wolf is 3x the child")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6, cast_hint=hint)
    assert not chk.passed and chk.definite and chk.issues[:2] == [hint,
                                                                  "the wolf is 3x the child"]
    assert g.score < GradeResult(0.9, 0.9).score
    assert cast_expectations(wolf, 3)[0].startswith("the wolf: A tall, lanky grey wolf")


async def test_gemini_grade_sends_cast_sheets_lines_and_expression():
    g, models = _gemini({"likeness": 0.8, "fidelity": 0.8, "has_text_artifacts": False,
                         "skin_tone_match": True, "hair_colour_match": True,
                         "hair_texture_match": True, "identity_notes": "",
                         "hero_hair_match": True, "family_match": True, "family_notes": "",
                         "cast_match": False, "cast_notes": "wolf too big",
                         "expression_match": False, "expression_notes": "smiling",
                         "hero_child_ok": True, "hero_items_ok": True, "hero_outfit_ok": True,
                         "hero_look_notes": "",
                         "contradictions": [], "issues": [], "boxes": []})
    res = await g.grade(_png(), hero=_png(), photo=_png(), caption="c", must_be_visible=[],
                        cast_refs=[_png(), _png()], cast=["the wolf: x", "the woodcutter: y"],
                        expression="scared: wide eyes")
    text, *parts = models.calls[0]
    assert len(parts) == 5
    assert "Images 4-5 are the supporting characters' reference sheet(s)" in text
    assert "- the wolf: x" in text
    assert "Requested hero expression (expression_match): scared" in text
    assert not res.cast_match and res.cast_notes == "wolf too big"
    assert not res.expression_match and res.expression_notes == "smiling"
    res = await g.grade(_png(), hero=_png(), photo=_png(), caption="c", must_be_visible=[])
    assert res.cast_match and res.expression_match  # nothing asked: never a failure


async def test_gemini_cast_sheet_check():
    g, models = _gemini({"figure_ok": True, "looks_ok": True, "size_ok": False,
                         "issues": ["draw the bar"]})
    wolf = load_story("red").characters["wolf"]
    chk = await g.check_cast_sheet(_png(), name="the wolf", description=wolf.description,
                                   size=wolf.size)
    text = models.calls[0][0]
    assert "next to ONE plain vertical grey height bar" in text and "one and a half" in text
    assert not chk.passed and chk.failures == 1


# --- 5. expressions ---------------------------------------------------------------------------


def test_every_page_has_an_expression_and_red_7_11_are_scared():
    for sid in ("red", "jack"):
        for p in load_story(sid).pages:
            assert p.expression, (sid, p.page)
    red = load_story("red")
    for n in (7, 11):
        p = red.page(n)
        assert p.expression.startswith("scared") and "no smile" in p.expression
        assert "clearly scared" in p.hero_instruction and "gasp" in p.hero_instruction
    assert "giggling" not in red.page(13).hero_instruction
    assert "no smile" in red.page(13).expression
    jack = load_story("jack")
    assert jack.page(9).expression.startswith("frightened")
    assert "alarmed" in jack.page(15).expression and "no smile" in jack.page(15).expression


def test_expression_reaches_prompt_and_grader_rules():
    red = load_story("red")
    p = scene_prompt(SPRITE, red.page(7), red)
    assert f"Hero expression: {red.page(7).expression}" in p
    assert "not its expression, its pose" in p  # hero ref line
    assert "calm friendly" not in hero_prompt(SPRITE)
    assert "Likeness is about identity only, never the expression" in INSTR
    assert "expression_match - only when a requested hero expression is given" in INSTR
    assert "child can look clearly scared" in CONSTRAINTS and "horror" in CONSTRAINTS
    assert "truly frightening" not in CONSTRAINTS


def test_wrong_expression_fails_but_is_not_a_definite_error():
    g = GradeResult(0.9, 0.9, expression_match=False, expression_notes="calm smile")
    chk = quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6,
                           expression_hint="the hero's expression must clearly be scared")
    assert not chk.passed and not chk.definite
    assert chk.issues[:2] == ["the hero's expression must clearly be scared", "calm smile"]


async def test_book_passes_the_requested_expression_to_the_grader():
    red = load_story("red")
    grader = FakeGrader()
    await run_book(RecordingFake(), await _photo(), "n64", 2, story_id="red", pages=[7],
                   hero=HERO, grader=grader)
    page = next(c for c in grader.calls if c.subject == "page")
    assert page.expression == red.page(7).expression and page.caption == red.page(7).scene
    hero = next(c for c in grader.calls if c.subject == "hero")
    assert hero.expression == "" and hero.cast == []


# --- 6. budget exception for definite errors ------------------------------------------------


def _img(tag):
    return ImageResult(png=tag.encode(), model="m")


async def _gen(n, corrections, quiet):
    return _img(f"a{n}")


def _check(definite: bool):
    async def check(n, image):
        g = GradeResult(0.9, 0.9, contradictions=["two wolves"] if definite else [])
        if not definite:
            g.likeness = 0.3
        return quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    return check


async def test_one_extra_retry_after_the_budget_for_a_definite_error():
    res = await quality.run_quality_loop(generate=_gen, check=_check(True), max_attempts=3,
                                         budget_exhausted=lambda: True,
                                         within_hard_cap=lambda: True)
    assert res.count == 2 and res.extra_retry and res.budget_stopped


async def test_no_extra_retry_for_soft_failures_or_past_the_hard_cap():
    res = await quality.run_quality_loop(generate=_gen, check=_check(False), max_attempts=3,
                                         budget_exhausted=lambda: True,
                                         within_hard_cap=lambda: True)
    assert res.count == 1 and not res.extra_retry and res.budget_stopped
    res = await quality.run_quality_loop(generate=_gen, check=_check(True), max_attempts=3,
                                         budget_exhausted=lambda: True,
                                         within_hard_cap=lambda: False)
    assert res.count == 1 and not res.extra_retry and res.budget_stopped


async def test_extra_retry_that_passes_is_not_budget_stopped():
    async def check(n, image):
        g = GradeResult(0.9, 0.9, contradictions=["two baskets"] if n == 1 else [],
                        boxes=[DEFAULT_FACE])
        return quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6)
    res = await quality.run_quality_loop(generate=_gen, check=check, max_attempts=3,
                                         budget_exhausted=lambda: True,
                                         within_hard_cap=lambda: True)
    assert res.count == 2 and res.chosen.passed and res.extra_retry
    assert not res.budget_stopped


def test_hard_cap_setting_and_definite_kinds(monkeypatch):
    assert get_settings().quality_hard_cap_s == 70.0
    monkeypatch.setenv("QUALITY_HARD_CAP_S", "0")
    get_settings.cache_clear()
    plan = plan_book("red", [1], "n64", 2)
    gen = BookGenerator(plan, HERO, b"", hero_provider=FakeProvider(), grader=FakeGrader())
    assert not gen.within_hard_cap()
    for g in (GradeResult(0.9, 0.9, skin_tone_match=False),
              GradeResult(0.9, 0.9, family_match=False),
              GradeResult(0.9, 0.9, contradictions=["two nightcaps"])):
        assert quality.evaluate(g, min_likeness=0.6, min_fidelity=0.6).definite
    assert not quality.evaluate(GradeResult(0.3, 0.3), min_likeness=0.6,
                                min_fidelity=0.6).definite


async def test_book_band_failure_gets_the_extra_retry(monkeypatch):
    """A page whose only attempt has a blank band (a definite error) gets one more
    generation although the budget is used up, while under the hard cap."""
    from tests.test_budget_bands import with_band

    class BandFirst(FakeProvider):
        n = 0

        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            res = await super().edit(prompt, images, aspect_ratio=aspect_ratio)
            if prompt.startswith("Task: illustrate"):
                BandFirst.n += 1
                if BandFirst.n == 1:
                    res.png = with_band("top", 0.30)  # too deep for the free rescue
            return res

    plan = plan_book("jack", [4], "n64", 2)
    t = {"now": 0.0}
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=BandFirst(),
                        grader=FakeGrader(), time_budget_s=1, hard_cap_s=100,
                        clock=lambda: t["now"], started_at=0.0)
    t["now"] = 50.0  # budget (1 s) used up, hard cap (100 s) not
    book = await gen.run()
    assert book.pages[0].attempts == 2 and book.pages[0].scores["passed"]


# --- 7. 429: rate limit vs. out of credit ------------------------------------------------------

OUT_OF_CREDIT = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": (
    "You exceeded your current quota, please check your plan and billing details. For more "
    "information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits."
), "details": [{"@type": "type.googleapis.com/google.rpc.Help", "links": [
    {"description": "Learn more about Gemini API quotas",
     "url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]}]}}
RATE_LIMIT = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": (
    "You exceeded your current quota, please check your plan and billing details."),
    "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_requests",
            "quotaId": "GenerateRequestsPerMinutePerProjectPerModel",
            "quotaDimensions": {"location": "global", "model": "gemini-3.1-flash-lite-image"},
            "quotaValue": "20"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}


class _ApiError(Exception):
    def __init__(self, body):
        self.details = body
        self.code = body["error"]["code"]
        self.status = body["error"]["status"]
        self.message = body["error"]["message"]


def test_out_of_credit_429_is_permanent_and_not_retried():
    exc = provider_error_from_google(_ApiError(OUT_OF_CREDIT), "Gemini API error")
    assert exc.status == 429 and exc.quota_exhausted and not exc.rate_limited
    assert exc.retry_after is None and not quality.is_retryable(exc)


def test_rate_limit_429_is_retried_after_the_server_delay():
    info = parse_google_error(RATE_LIMIT)
    assert info == {"quota_metric": "generativelanguage.googleapis.com/generate_content_"
                                    "requests",
                    "quota_id": "GenerateRequestsPerMinutePerProjectPerModel",
                    "quota_value": "20", "retry_after": 7.0}
    exc = provider_error_from_google(_ApiError(RATE_LIMIT), "Gemini API error")
    assert exc.rate_limited and not exc.quota_exhausted and quality.is_retryable(exc)
    assert "id=GenerateRequestsPerMinutePerProjectPerModel" in exc.quota_text
    assert "(retry after 7s)" in str(exc)
    assert 7 * 0.85 <= rate_limit_delay(exc, 1) <= 7 * 1.25
    no_delay = ProviderError("Gemini API error 429: quota per minute", status=429)
    assert 2 * 0.85 <= rate_limit_delay(no_delay, 1) <= 2 * 1.25
    assert rate_limit_delay(no_delay, 9) <= 20 * 1.25
    # a per-day quota is not worth waiting for; parse never raises on odd bodies
    day = ProviderError("Gemini API error 429: x", status=429,
                        quota_id="GenerateRequestsPerDayPerProjectPerModel")
    assert day.quota_exhausted and parse_google_error(["odd"]) == {}


class _Scripted429(RecordingFake):
    """Page calls: the first raises ``err``; everything else works."""

    def __init__(self, err):
        super().__init__()
        self.err, self.raised = err, 0

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        if prompt.startswith("Task: illustrate") and not self.raised:
            self.raised += 1
            raise self.err
        return await super().edit(prompt, images, aspect_ratio=aspect_ratio)


async def test_book_retries_a_rate_limited_call_after_the_delay(monkeypatch):
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    import app.pipeline.book as book_mod
    monkeypatch.setattr(book_mod.asyncio, "sleep", fake_sleep)
    exc = provider_error_from_google(_ApiError(RATE_LIMIT), "Gemini API error")
    fake = _Scripted429(exc)
    book = await run_book(fake, await _photo(), "n64", 2, story_id="jack", pages=[4],
                          hero=HERO, grader=FakeGrader())
    pages = [c for c in book.costs if c.kind == "page"]
    assert [c.ok for c in pages] == [False, True]  # both tries recorded
    assert book.pages[0].attempts == 1  # the wait did not use up a quality attempt
    waits = [d for d in slept if d > 1]
    assert len(waits) == 1 and 5 <= waits[0] <= 9


async def test_out_of_credit_stops_further_calls_and_keeps_best(caplog):
    exc = provider_error_from_google(_ApiError(OUT_OF_CREDIT), "Gemini API error")

    class CreditRunsOut(RecordingFake):
        pages = 0

        async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
            if prompt.startswith("Task: illustrate"):
                CreditRunsOut.pages += 1
                if CreditRunsOut.pages > 2:
                    raise exc
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)

    def fn(call, n):  # every page fails its check once -> wants a retry
        return GradeResult(0.3, 0.9, boxes=[DEFAULT_FACE])

    fake = CreditRunsOut()
    with caplog.at_level(logging.ERROR, logger="app.pipeline.book"):
        book = await run_book(fake, await _photo(), "n64", 2, story_id="jack", pages=[4, 5],
                              hero=HERO, grader=FakeGrader(fn=fn), max_attempts=3)
    # each page keeps its first picture; after the 429 no further page call is made, and
    # a picture graded after it is accepted unchecked (no grade call either)
    assert [p.chosen_attempt for p in book.pages] == [1, 1]
    assert CreditRunsOut.pages == 3
    page_calls = [c for c in book.costs if c.kind == "page"]
    assert sum(not c.ok for c in page_calls) == 1  # one 429 seen, then no further calls
    assert "OUT OF CREDIT" in caplog.text


async def test_minute_limiter():
    t = {"now": 0.0}
    lim = MinuteLimiter(2, clock=lambda: t["now"])
    await lim.acquire()
    await lim.acquire()
    waiter = asyncio.create_task(lim.acquire())
    await asyncio.sleep(0.02)
    assert not waiter.done()
    t["now"] = 61.0
    await asyncio.wait_for(waiter, 2)


def test_band_checks_raise_no_deprecation_warnings():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert find_blank_band(png(padded("blurred_copy"))) is not None
        find_blank_band(png(scene()))
