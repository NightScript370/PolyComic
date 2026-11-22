"""M4b pipeline: family match, text placement, quality loop, plates/bible refs."""

import asyncio

import pytest

from app.config import get_settings
from app.pipeline import quality
from app.pipeline.book import (
    BookGenerator,
    HeroDetails,
    Hooks,
    MemoryAssets,
    plan_book,
    run_book,
    select_refs,
)
from app.pipeline.family import choose_family_variant, family_variants, variant_traits
from app.pipeline.placement import OBJECT_MAX_COVER, candidates, place_text
from app.pipeline.prompts import RefSpec, scene_prompt
from app.providers.base import (
    Box,
    GradeResult,
    ImageResult,
    NoImageError,
    PhotoTraits,
    ProviderError,
    max_reference_images,
)
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.stories.loader import Layout, get_layout, load_story
from app.styles.presets import build_style_string

# --- family match ---------------------------------------------------------------------


def test_variant_traits_parse_story_yaml():
    jack = {v.id: variant_traits(v) for v in family_variants(load_story("jack"))}
    assert [(t.skin_tone, t.hair_colour, t.hair_texture) for t in jack.values()] == [
        (1.5, "blonde", "straight"), (3.0, "dark_brown", "wavy"), (4.0, "black", "curly"),
        (6.0, "black", "coily")]
    red = {v.id: variant_traits(v) for v in family_variants(load_story("red"))}
    assert [(t.skin_tone, t.hair_colour, t.hair_texture) for t in red.values()] == [
        (1.5, "red", "curly"), (3.0, "black", "straight"), (4.0, "dark_brown", "wavy"),
        (6.0, "black", "coily")]


@pytest.mark.parametrize(
    ("story", "tone", "colour", "texture", "expected"),
    [("jack", 1, "blonde", "straight", "fam_a"),
     ("jack", 2, "light_brown", "straight", "fam_a"),
     ("jack", 3, "dark_brown", "wavy", "fam_b"),
     ("jack", 4, "black", "curly", "fam_c"),
     ("jack", 5, "black", "coily", "fam_d"),
     ("jack", 6, "black", "coily", "fam_d"),
     ("red", 1, "red", "curly", "fam_a"),
     ("red", 3, "black", "straight", "fam_b"),
     ("red", 4, "dark_brown", "wavy", "fam_c"),
     ("red", 6, "unknown", "unknown", "fam_d")],
)
def test_choose_family_variant(story, tone, colour, texture, expected):
    traits = PhotoTraits(skin_tone=tone, hair_colour=colour, hair_texture=texture)
    assert choose_family_variant(load_story(story), traits) == expected


def test_family_variant_fallback_is_first():
    assert choose_family_variant(load_story("jack"), None) == "fam_a"


# --- text placement ----------------------------------------------------------------------

S01 = get_layout("s01_home_two_shot")  # zones: bottom, then top


def face(x, y, w, h, label="hero face"):
    return Box(label, x, y, w, h, "face")


def obj(x, y, w, h, label="golden egg"):
    return Box(label, x, y, w, h, "key_object")


def test_placement_first_zone_when_clear():
    p = place_text(S01, [DEFAULT_FACE])
    assert p.zone.zone == "bottom" and p.overlap == 0 and not p.blocked
    assert place_text(S01).zone.zone == "bottom"


def test_placement_skips_zone_with_face_or_key_object():
    assert place_text(S01, [face(0.4, 0.8, 0.1, 0.1)]).zone.zone == "top"
    assert place_text(S01, [obj(0.1, 0.8, 0.2, 0.1)]).zone.zone == "top"


def test_placement_padding_counts_near_misses():
    # face ends 0.01 above the bottom zone (y=0.75): inside the 0.02 padding
    assert place_text(S01, [face(0.4, 0.6, 0.1, 0.14)]).zone.zone == "top"
    # 0.05 away: clear
    assert place_text(S01, [face(0.4, 0.6, 0.1, 0.10)]).zone.zone == "bottom"


MID_FACE = face(0.45, 0.45, 0.1, 0.1, "giant face")  # blocks the middle band


def test_placement_faces_weighted_when_everything_is_blocked():
    # bottom: face 0.09^2 padded (x3 = 0.024); top: whole object 0.14^2 padded (0.020);
    # middle: a big face. All blocked -> least weighted overlap = top, although its raw
    # overlap is larger than the bottom one.
    boxes = [face(0.45, 0.85, 0.05, 0.05), obj(0.1, 0.08, 0.1, 0.1), MID_FACE]
    p = place_text(S01, boxes)
    assert p.zone.zone == "top" and p.blocked and not p.face_overlap
    assert p.blocker_phrase == "the golden egg"
    assert p.issue() == ("keep the golden egg out of the top fifth of the picture - the page "
                         "text is printed there")
    # faces everywhere -> least overlap, blocked by a face
    boxes = [face(0.45, 0.85, 0.05, 0.05), face(0.1, 0.05, 0.3, 0.15), MID_FACE]
    p = place_text(S01, boxes)
    assert p.zone.zone == "bottom" and p.blocked and p.blocker_phrase == "every face"


def test_placement_all_blocked_never_covers_face_to_spare_object():
    # Jack page 5: cottage box in the bottom fifth (>20% covered), hero face small in the
    # top zone, hero figure in the middle. Covering the face must never win.
    s05 = get_layout("s05_vertical_ascent")  # zones: bottom, then top
    cottage = obj(0.3, 0.70, 0.4, 0.25, "tiny cottage")
    boxes = [cottage, face(0.45, 0.05, 0.05, 0.05),
             Box("hero", 0.35, 0.35, 0.3, 0.3, "figure")]
    p = place_text(s05, boxes)
    assert p.zone.zone == "bottom" and p.blocked and not p.face_overlap
    assert p.blocker is cottage
    # without the face the top zone is clear, so the bottom is still not preferred
    assert place_text(s05, boxes[:1]).zone.zone == "top"


def test_placement_middle_when_top_and_bottom_covered():
    both = [face(0.4, 0.8, 0.1, 0.1), face(0.4, 0.1, 0.1, 0.1)]
    p = place_text(S01, both)
    assert p.zone.zone == "middle" and not p.blocked
    assert (p.zone.y, p.zone.h) == (0.41, 0.18)
    # an object in the bottom zone and a face on top -> middle as well
    p = place_text(S01, [obj(0.3, 0.8, 0.3, 0.1, "golden harp"), face(0.4, 0.1, 0.1, 0.1)])
    assert p.zone.zone == "middle"
    # a layout that lists its own middle zone is not given a second one
    zones = [{"zone": "bottom", "box": {"x": 0.06, "y": 0.75, "w": 0.88, "h": 0.19}},
             {"zone": "middle", "box": {"x": 0.10, "y": 0.41, "w": 0.80, "h": 0.16}}]
    lay = Layout("x", 1, "", zones, "")
    assert [z.zone for z in candidates(lay)] == ["bottom", "middle"]
    assert [z.zone for z in candidates(S01)] == ["bottom", "top", "middle"]


def test_placement_object_cover_threshold():
    # everything else blocked by faces, so the bottom zone's object coverage decides
    faces = [face(0.4, 0.1, 0.1, 0.1), MID_FACE]
    # the object pokes 0.01 of its 0.1 height into the bottom zone (10% <= 20%): acceptable
    p = place_text(S01, [obj(0.3, 0.66, 0.2, 0.1, "golden harp"), *faces])
    assert p.zone.zone == "bottom" and not p.blocked
    # 40% inside: blocked, and the issue names it
    p = place_text(S01, [obj(0.3, 0.71, 0.2, 0.1, "golden harp"), *faces])
    assert p.blocked and p.blocker.label == "golden harp"
    assert OBJECT_MAX_COVER == 0.20


def test_layouts_do_not_list_middle_themselves():
    for lay in load_story("jack").pages + load_story("red").pages:
        names = [z["zone"] for z in get_layout(lay.layout).allowed_text_zones]
        assert "middle" not in names


# --- quality loop ----------------------------------------------------------------------


def img(tag: str) -> ImageResult:
    return ImageResult(png=tag.encode(), model="m")


def grade(lk, fd, text=False, issues=()):
    return GradeResult(likeness=lk, fidelity=fd, has_text_artifacts=text, issues=list(issues))


def check_for(grades):
    async def check(n, image):
        return quality.evaluate(grades[n - 1], min_likeness=0.6, min_fidelity=0.6)
    return check


async def test_loop_stops_at_first_pass():
    calls = []

    async def generate(n, corrections, quiet):
        calls.append((n, list(corrections), quiet))
        return img(f"a{n}")

    res = await quality.run_quality_loop(
        generate=generate, check=check_for([grade(0.9, 0.9)] * 3), max_attempts=3)
    assert calls == [(1, [], None)] and res.count == 1 and res.chosen.n == 1


async def test_loop_regenerates_with_corrections_then_picks_best():
    calls, phases = [], []
    grades = [grade(0.5, 0.9, issues=["hair should be curly"]), grade(0.55, 0.8, text=True),
              grade(0.4, 0.95)]

    async def generate(n, corrections, quiet):
        calls.append(list(corrections))
        return img(f"a{n}")

    async def on_phase(n, phase, chk):
        phases.append((n, phase, None if chk is None else round(chk.grade.likeness, 2)))

    res = await quality.run_quality_loop(generate=generate, check=check_for(grades),
                                         max_attempts=3, on_phase=on_phase)
    assert res.count == 3
    # 0.5*0.5+0.5*0.9=0.70 ; 0.675-0.25 (text) ; 0.675 -> attempt 1 wins
    assert res.chosen.n == 1 and res.chosen.png == b"a1"
    assert calls[0] == [] and calls[1] == ["hair should be curly"]
    assert "remove every letter, word, number, sign and speech bubble" in calls[2]
    assert phases == [(1, "generating", None), (1, "checking", None), (1, "retrying", 0.5),
                      (2, "generating", None), (2, "checking", None), (2, "retrying", 0.55),
                      (3, "generating", None), (3, "checking", None)]


async def test_loop_placement_failure_requests_quiet_zone():
    quiet_seen = []
    layout = S01

    async def generate(n, corrections, quiet):
        quiet_seen.append(quiet)
        return img(f"a{n}")

    async def check(n, image):
        boxes = ([face(0.4, 0.8, 0.1, 0.1), face(0.4, 0.1, 0.1, 0.1), MID_FACE] if n == 1
                 else [obj(0.3, 0.6, 0.1, 0.1)])
        p = place_text(layout, boxes)
        return quality.evaluate(GradeResult(0.9, 0.9, boxes=boxes), min_likeness=0.6,
                                min_fidelity=0.6, placement=p)

    res = await quality.run_quality_loop(generate=generate, check=check, max_attempts=3)
    assert res.count == 2 and res.chosen.n == 2
    assert quiet_seen[0] is None and quiet_seen[1].zone.zone == "bottom"
    assert quiet_seen[1].blocker_phrase == "every face"


async def test_loop_retries_failed_generation_but_not_permanent_errors():
    seq = [NoImageError("blocked", finish_reason="IMAGE_SAFETY"), img("ok")]

    async def generate(n, corrections, quiet):
        item = seq[n - 1]
        if isinstance(item, Exception):
            raise item
        return item

    res = await quality.run_quality_loop(generate=generate, check=check_for([None, grade(1, 1)]),
                                         max_attempts=3)
    assert res.count == 2 and res.chosen.n == 2 and res.attempts[0].error is not None

    async def bad_request(n, corrections, quiet):
        raise ProviderError("bad", status=400)

    with pytest.raises(ProviderError):
        await quality.run_quality_loop(generate=bad_request, check=check_for([]), max_attempts=3)

    count = 0

    async def always_500(n, corrections, quiet):
        nonlocal count
        count += 1
        raise ProviderError("boom", status=500)

    with pytest.raises(ProviderError):
        await quality.run_quality_loop(generate=always_500, check=check_for([]), max_attempts=3)
    assert count == 3


# --- refs: plates + bible ----------------------------------------------------------------


def test_select_refs_priority_and_order():
    chars = [(RefSpec("character", "Mother", "mother"), b"M"),
             (RefSpec("character", "Hen", "hen"), b"H")]
    refs = select_refs(b"hero", b"photo", b"plate", chars, cap=14)
    assert [r.role for r, _ in refs] == ["hero", "photo", "plate", "character", "character"]
    refs = select_refs(b"hero", b"photo", b"plate", chars, cap=3)
    assert [b for _, b in refs] == [b"hero", b"photo", b"plate"]
    refs = select_refs(b"hero", b"photo", b"plate", chars, cap=2)  # plate beats photo
    assert [b for _, b in refs] == [b"hero", b"plate"]
    refs = select_refs(b"hero", b"photo", None, chars, cap=3)
    assert [b for _, b in refs] == [b"hero", b"photo", b"M"]


def test_max_reference_images(monkeypatch):
    fake = FakeProvider()
    assert max_reference_images(fake) == 14
    fake.model = "gemini-2.5-flash-image"
    assert max_reference_images(fake) == 3
    monkeypatch.setenv("IMAGE_MAX_REFS", "5")
    get_settings.cache_clear()
    assert max_reference_images(fake) == 5


class RecordingFake(FakeProvider):
    def __init__(self):
        super().__init__()
        self.images: list[list[bytes]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        self.images.append(list(images))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        finally:
            self.in_flight -= 1


HERO = HeroDetails(name="Mia", pronouns="she")
SHEET_TASK = "Task: draw a character reference sheet for the family"
CAST_TASK = "Task: draw a character reference sheet for "  # family + supporting sheets


async def _photo() -> bytes:
    return (await FakeProvider().edit("photo", [])).png


def _page_calls(fake: RecordingFake):
    return [(c, imgs) for c, imgs in zip(fake.calls, fake.images, strict=True)
            if not c["prompt"].startswith(("Task: turn the person", CAST_TASK))]


async def test_plate_and_bible_used_as_refs():
    fake, photo = RecordingFake(), await _photo()
    assets = MemoryAssets(
        plates={1: b"PLATE1"},
        bible={("mother", "fam_d"): b"MOTHER_D", ("mother", "fam_a"): b"MOTHER_A",
               ("milky_white", ""): b"COW"},
    )
    grader = FakeGrader(traits=PhotoTraits(6, "black", "coily"))
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1, 2], hero=HERO,
                          grader=grader, assets=assets)
    assert book.family_variant == "fam_d" and grader.trait_calls == 1
    (c1, refs1), (c2, refs2) = sorted(_page_calls(fake), key=lambda x: "Milky-White in the"
                                      not in x[0]["prompt"])
    # page 1: hero, plate, Mother (variant fam_d); the cow is already in the plate; no
    # photo - the hero reference is the identity anchor
    assert refs1[0] == book.hero_png and photo not in refs1
    assert refs1[1:] == [b"PLATE1", b"MOTHER_D"]
    assert "Image 2 is the background plate" in c1["prompt"]
    assert "Image 3 is the character reference for Mother" in c1["prompt"]
    assert "Already in the background plate (keep them exactly as drawn): Milky-White." in (
        c1["prompt"])
    assert "deep brown" in c1["prompt"]  # fam_d skin tone in the cast line
    assert book.pages[0].used_plate
    # page 2: no plate -> supporting sheets: the stored cow sheet, and the old man's sheet
    # made by this book (first book of jack/n64 to need it) and stored for later books
    assert refs2[:1] == [book.hero_png] and photo not in refs2
    assert refs2[1:] == [assets.sheets[("old_man", "")], b"COW"]
    assert "Image 2 is the reference sheet for the old man" in c2["prompt"]
    assert "background plate" not in c2["prompt"] and not book.pages[1].used_plate


async def test_fallback_without_assets():
    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=FakeGrader())
    (call, refs), = _page_calls(fake)
    # hero + the per-book family sheet (Mother is on page 1) + Milky-White's supporting
    # sheet, made by this book (no photo)
    assert refs[:2] == [book.hero_png, book.family_sheet_png] and len(refs) == 3
    assert "Image 3 is the reference sheet for Milky-White" in call["prompt"]
    assert "Other characters" in call["prompt"]
    assert "Image 2 is the family reference sheet - Mother must look exactly" in call["prompt"]
    assert book.pages[0].attempts == 1


async def test_refs_capped(monkeypatch):
    monkeypatch.setenv("IMAGE_MAX_REFS", "3")
    get_settings.cache_clear()
    fake, photo = RecordingFake(), await _photo()
    assets = MemoryAssets(plates={20: b"P20"},
                          bible={("mother", "fam_a"): b"M", ("hen", ""): b"H"})
    await run_book(fake, photo, "n64", 2, story_id="jack", pages=[20], hero=HERO,
                   assets=assets, family_variant="fam_a")
    (_, refs), = _page_calls(fake)
    assert refs[1] == b"P20" and len(refs) == 3 and photo not in refs


class ScriptedGrader(FakeGrader):
    """Per-picture grade scripts keyed by caption prefix; default passes."""

    def __init__(self, scripts: dict[str, list[GradeResult]]):
        super().__init__()
        self.scripts = scripts
        self.seen: dict[str, int] = {}

    async def grade(self, image, *, hero, photo, caption, must_be_visible, subject="page",
                    traits=None, props=(), **kw):
        await super().grade(image, hero=hero, photo=photo, caption=caption,
                            must_be_visible=must_be_visible, subject=subject, traits=traits,
                            props=props, **kw)
        key = "hero" if subject == "hero" else next(
            (k for k in self.scripts if caption.startswith(k)), None)
        if key is None or key not in self.scripts:
            return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])
        n = self.seen.get(key, 0)
        self.seen[key] = n + 1
        script = self.scripts[key]
        return script[min(n, len(script) - 1)]


class EventHooks(Hooks):
    def __init__(self):
        self.phases: list[tuple[int, int, str]] = []
        self.done: list[tuple[int, int, bool]] = []

    async def attempt_phase(self, page, attempt, phase, check):
        self.phases.append((page, attempt, phase))

    async def attempt_done(self, page, a):
        self.done.append((page, a.n, a.passed))


async def test_book_quality_loop_regenerates_and_picks_best():
    story = load_story("jack")
    p2, p3 = story.page(2).scene[:30], story.page(3).scene[:30]
    grader = ScriptedGrader({
        "hero": [GradeResult(0.3, 0.9, issues=["face shape differs"]),
                 GradeResult(0.8, 0.9)],
        p2: [GradeResult(0.5, 0.5), GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])],
        p3: [GradeResult(0.5, 0.7), GradeResult(0.55, 0.75), GradeResult(0.2, 0.2)],
    })
    fake, photo, hooks = RecordingFake(), await _photo(), EventHooks()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1, 2, 3], hero=HERO,
                          grader=grader, hooks=hooks)
    assert book.hero_attempts == 2 and book.hero_scores["passed"]
    by_page = {p.page: p for p in book.pages}
    assert by_page[1].attempts == 1
    assert by_page[2].attempts == 2 and by_page[2].scores["passed"]
    assert by_page[3].attempts == 3 and by_page[3].chosen_attempt == 2
    assert by_page[3].scores == {"likeness": 0.55, "fidelity": 0.75, "passed": False}
    # hero 2, family sheet 1 (Mother on pages 1 and 3), supporting sheets 2 (Milky-White,
    # the old man), pages 1 + 2 + 3 generations, the mother's page-3 speaker portrait
    assert len(fake.calls) == 2 + 1 + 2 + 1 + 2 + 3 + 1
    assert [c.kind for c in book.costs].count("grade") == 2 + 1 + 2 + 3
    retry_prompts = [c["prompt"] for c in fake.calls if "Corrections" in c["prompt"]]
    assert any("face shape differs" in p for p in retry_prompts)
    assert (3, 2, "retrying") in hooks.phases and (3, 3, "retrying") not in hooks.phases
    assert sorted(d for d in hooks.done if d[0] == 3) == [(3, 1, False), (3, 2, False),
                                                         (3, 3, False)]
    assert len([c for c in book.costs if c.kind == "page" and c.page == 3]) == 3


async def test_book_text_zone_avoids_face_and_regenerates_when_blocked():
    story = load_story("jack")
    p1 = story.page(1).caption[:30]  # s01: bottom first, then top
    p4 = story.page(4).caption[:30]
    p5 = story.page(5).caption[:30]
    blocked = [Box("hero face", 0.4, 0.8, 0.1, 0.1, "face"),
               Box("giant face", 0.4, 0.1, 0.1, 0.1, "face"), MID_FACE]
    grader = ScriptedGrader({
        p1: [GradeResult(0.9, 0.9, boxes=[Box("hero face", 0.4, 0.8, 0.1, 0.1, "face")])],
        p4: [GradeResult(0.9, 0.9, boxes=blocked)],  # every attempt blocked by faces
        # top + bottom covered by faces -> middle, no regeneration
        p5: [GradeResult(0.9, 0.9, boxes=blocked[:2])],
    })
    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1, 4, 5], hero=HERO,
                          grader=grader)
    by_page = {p.page: p for p in book.pages}
    assert by_page[1].zone.zone == "top" and by_page[1].attempts == 1
    assert by_page[5].zone.zone == "middle" and by_page[5].attempts == 1
    assert by_page[4].attempts == 3 and not by_page[4].scores["passed"]
    p4_prompts = [c["prompt"] for c in fake.calls if p4 in c["prompt"]]
    assert "IMPORTANT: the" not in p4_prompts[0]
    assert all("IMPORTANT: the" in p and "page text" in p for p in p4_prompts[1:])


async def test_book_object_under_text_triggers_regeneration():
    story = load_story("jack")
    p14 = story.page(14).scene[:30]
    harp_everywhere = [Box("golden harp", 0.3, 0.78, 0.3, 0.12, "key_object"),
                       Box("golden egg", 0.3, 0.08, 0.3, 0.12, "key_object"),
                       Box("hen", 0.3, 0.44, 0.3, 0.12, "key_object")]
    grader = ScriptedGrader({p14: [GradeResult(0.9, 0.9, boxes=harp_everywhere),
                                   GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE])]})
    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[14], hero=HERO,
                          grader=grader)
    page = book.pages[0]
    assert page.attempts == 2 and page.scores["passed"]
    prompts = [c["prompt"] for c in fake.calls if p14 in c["prompt"]]
    assert "IMPORTANT: the" not in prompts[0]
    first_zone = get_layout(story.page(14).layout).allowed_text_zones[0]["zone"]
    where = {"top": "top fifth", "bottom": "bottom fifth"}[first_zone]
    blocker = {"top": "golden egg", "bottom": "golden harp"}[first_zone]
    assert f"Keep the {blocker} out of the {where}." in prompts[1]
    assert f"keep the {blocker} out of the {where} of the picture" in prompts[1]


def test_pdf_draws_text_in_middle_zone():
    import io

    from pypdf import PdfReader

    from app.pipeline.book import BookPage, BookResult
    from app.pipeline.pdf import PAGE_H, build_book_pdf, zone_rect
    from app.pipeline.placement import MIDDLE_ZONE

    story = load_story("jack")
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (480, 640), (40, 90, 160)).save(buf, format="PNG")
    pic = buf.getvalue()
    page = BookPage(page=1, title="t", caption="c", text="Middle words here.",
                    layout=story.page(1).layout, zone=MIDDLE_ZONE, png=pic)
    book = BookResult(story=story, preset="n64", detail=2, hero_details=HERO,
                      family_variant="fam_a", style="s", hero_png=pic, pages=[page], costs=[])
    reader = PdfReader(io.BytesIO(build_book_pdf(book)))
    ys = []
    reader.pages[1].extract_text(visitor_text=lambda t, cm, tm, fd, fs: ys.append(
        (t, tm[5] * cm[3] + cm[5])) if "Middle" in t else None)
    assert ys, "middle text not drawn"
    _, y, _, h = zone_rect(MIDDLE_ZONE)
    assert y <= ys[0][1] <= y + h <= PAGE_H


async def test_grader_failure_accepts_picture_unchecked():
    class Broken(FakeGrader):
        async def grade(self, *a, **k):
            raise ProviderError("vision down", status=503)

        async def photo_traits(self, photo):
            raise ProviderError("vision down", status=503)

    fake, photo = RecordingFake(), await _photo()
    book = await run_book(fake, photo, "n64", 2, story_id="jack", pages=[1], hero=HERO,
                          grader=Broken())
    assert book.family_variant == "fam_a"  # fallback
    assert book.pages[0].attempts == 1 and book.pages[0].scores is None
    failed = [c for c in book.costs if not c.ok]
    assert sorted(c.kind for c in failed) == ["family_match", "grade", "grade"]


async def test_image_concurrency_limit(monkeypatch):
    monkeypatch.setenv("IMAGE_CONCURRENCY", "2")
    get_settings.cache_clear()
    fake, photo = RecordingFake(), await _photo()
    plan = plan_book("jack", [1, 2, 3, 4, 5], "n64", 2)
    gen = BookGenerator(plan, HERO, photo, hero_provider=fake, grader=FakeGrader())
    await gen.run()
    assert fake.max_in_flight == 2


def test_scene_prompt_plate_lists_family_to_add():
    story = load_story("red")
    page = story.page(20)  # mother + grandma (family) + woodcutter
    style = build_style_string("ps1", 2)
    refs = [RefSpec("hero"), RefSpec("photo"), RefSpec("plate"),
            RefSpec("character", "Mother", "mother")]
    p = scene_prompt(style, page, story, "fam_c", refs=refs, corrections=["fix the hood"])
    assert "Already in the background plate (keep them exactly as drawn): the woodcutter." in p
    assert "Add to the scene" in p and "- Mother:" in p and "- Grandma:" in p
    assert "warm medium brown" in p and "fix the hood" in p
    assert p.count(style) == 1
