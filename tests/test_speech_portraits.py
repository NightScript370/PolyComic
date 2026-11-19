"""Mixed narration + speech in one dialogue box (quotes + highlight colour) and speaker
portraits that appear ONLY when the grader confirms the speaker is not in the picture."""

import dataclasses
import io

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.colors import HexColor

from app.pipeline import dialogue
from app.pipeline.book import BookGenerator, BookPage, BookResult, MemoryAssets, plan_book
from app.pipeline.calls import CALL_KINDS
from app.pipeline.pdf import (
    DIALOGUE_MIN,
    PORTRAIT_MIN,
    SPEECH_INK,
    Para,
    build_book_pdf,
    clean_runs,
    layout_dialogue,
    page_paragraph,
    register_fonts,
    wrap,
    wrap_offsets,
    zone_rect,
)
from app.pipeline.placement import TextZone
from app.pipeline.portraits import (
    EXPRESSION_FACES,
    PORTRAIT_CHECK_KIND,
    PORTRAIT_KIND,
    make_portrait,
    portrait_gate,
    portrait_key,
    presence_names,
)
from app.providers.base import Box, CallMeta, GradeResult, PortraitCheck
from app.providers.fake import FakeProvider
from app.providers.fake_grader import DEFAULT_FACE, FakeGrader
from app.providers.gemini_vision import presence_note, visible_from
from app.stories.loader import (
    CLOSE_QUOTE,
    OPEN_QUOTE,
    PORTRAIT_EXPRESSIONS,
    _parse_story,
    display_runs,
    load_story,
    render_text,
    split_speech,
)
from tests.test_quality import HERO, _photo

ZONE = TextZone("bottom", 0.06, 0.75, 0.88, 0.19)

# Every page with speech, both stories: (who, says) in reading order.
EXPECTED = {
    "red": {
        1: [("mother", "Take this basket to Grandma,"),
            ("mother", "and this little whistle too. And remember, keep to the path!")],
        2: [("wolf", "Good morning,"), ("wolf", "And where are you going, little one?")],
        3: [("wolf", "Why not pick some flowers for Grandma?")],
        4: [("hero", "Grandma will love these!")],
        7: [("wolf", "Come in, dear!")],
        8: [("grandmother", "Shh!")],
        9: [("hero", "Grandma, what big ears you have!"),
            ("wolf", "All the better to hear you with,")],
        10: [("hero", "Grandma, what big eyes you have!"),
             ("wolf", "All the better to see you with, my dear.")],
        11: [("hero", "And Grandma, what big teeth you have!"),
             ("wolf", "All the better to gobble you up!")],
        14: [("grandmother", "Shoo, you rascal! Shoo!")],
        16: [("woodcutter", "I heard your whistle!")],
    },
    "jack": {
        2: [("old_man", "Five magic beans,"), ("old_man", "For one cow.")],
        3: [("mother", "Beans!")],
        7: [("giants_wife", "You look hungry,"), ("giants_wife", "Come in, quick!")],
        8: [("giants_wife", "Hide!")],
        9: [("giant", "Fee-fi-fo-fum!"), ("giant", "I smell somebody new!")],
        11: [("giant", "Wife, bring me my hen!")],
        14: [("harp", "Master! Master! Wake up!")],
        17: [("hero", "Mother, the axe!")],
    },
}


def _png(size=(240, 320), color=(60, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# --- story metadata ---------------------------------------------------------------------


@pytest.mark.parametrize("story_id", ["red", "jack"])
def test_speech_spans_in_stories(story_id):
    story = load_story(story_id)
    got = {p.page: [(s.who, s.says) for s in p.speech] for p in story.pages if p.speech}
    assert got == EXPECTED[story_id]


@pytest.mark.parametrize("story_id", ["red", "jack"])
@pytest.mark.parametrize("name,pronouns", [("Mia", "she"), ("Zed", "he"), ("Ash", "they"),
                                           ("Grandma Jo", "she")])
def test_every_span_matches_the_rendered_text(story_id, name, pronouns):
    story = load_story(story_id)
    for p in story.pages:
        text = render_text(p.text, name, pronouns)
        runs = story.speech_runs(p.page, text, name, pronouns)
        assert "".join(t for t, _ in runs) == text  # wording never changes
        spoken = [(who, t) for t, who in runs if who]
        assert spoken == [(s.who, render_text(s.says, name, pronouns)) for s in p.speech]


def _cand(page: int = 7, **kw):
    """Red page ``page`` made a portrait candidate (no story page is one at the moment)."""
    kw = {"portrait": True, "portrait_expression": "sly", **kw}
    return dataclasses.replace(load_story("red").page(page), **kw)


def _cand_plan(preset="sprite16", page: int = 7, **kw):
    return dataclasses.replace(plan_book("red", [page], preset, 2),
                               pages=(_cand(page, **kw),))


def test_portrait_pages_and_keys():
    red, jack = load_story("red"), load_story("jack")
    # Only the "different moment" pages have portraits (red p7 keeps the speaker a mystery).
    assert [(p.page, p.portrait_when, p.portrait_expression)
            for p in red.pages if p.portrait] == [(3, "moment", "sly")]
    assert [(p.page, p.speaker, p.portrait_when, p.portrait_expression)
            for p in jack.pages if p.portrait] == [
        (3, "mother", "moment", "angry"), (11, "giant", "moment", "angry"),
        (17, "hero", "moment", "scared")]
    # Mother = family sheet (per book), hero = hero reference, wolf p3 = PLAIN costume.
    assert portrait_key(jack, jack.page(3)) == ("mother", "", "angry")
    assert portrait_key(jack, jack.page(17)) == ("hero", "", "scared")
    assert portrait_key(jack, jack.page(11))[0] == "giant"
    assert portrait_key(red, red.page(3)) == ("wolf", "", "sly")
    assert presence_names(red, red.page(3)) == []  # moment pages ask no presence question
    p7 = red.page(7)
    assert p7.speaker == "wolf" and red.speaker_label(7, "Mia") == "???"
    assert portrait_key(red, p7) is None and presence_names(red, p7) == []
    assert "portrait_expression" not in p7.raw and "portrait" not in p7.raw
    cand = _cand()  # synthetic: the machinery still works for future pages
    assert portrait_key(red, cand) == ("wolf", "grandma_disguise", "sly")
    assert presence_names(red, cand) == ["the wolf"]
    assert presence_names(red, red.page(2)) == []  # not a candidate: no presence question


def test_portrait_values_parse_and_stay_backward_compatible():
    for val, on, when in [(True, True, "offscreen"), ("offscreen", True, "offscreen"),
                          ("moment", True, "moment"), (False, False, "offscreen"),
                          (None, False, "offscreen")]:
        story = _parse_story(_story({"speaker": "wolf", "portrait": val}), "t")
        pg = story.pages[0]
        assert (pg.portrait, pg.portrait_when) == (on, when)
    assert "angry" in EXPRESSION_FACES and set(EXPRESSION_FACES) == set(PORTRAIT_EXPRESSIONS)


def test_page_speaker_label_override_and_new_tabs():
    red = load_story("red")
    assert red.page(7).speaker == "wolf" and red.page(7).speaker_label == "???"
    assert red.page(7).speech[0].who == "wolf"  # speech semantics unchanged
    assert red.characters["wolf"].tab_label == "Wolf"  # other wolf pages keep their tab
    assert red.speaker_label(2, "Mia") == "Wolf"
    assert red.speaker_label(4, "Mia") == "Mia" and red.page(4).speaker == "hero"
    assert red.speaker_label(8, "Mia") == "Grandma" and red.page(8).speaker == "grandmother"
    from app.jobs.progress import speaker_for
    assert speaker_for("red", 7, "Mia") == "???"
    assert speaker_for("red", 4, "Zed") == "Zed" and speaker_for("red", 8, "Zed") == "Grandma"


@pytest.mark.parametrize("label,ok", [("???", True), ("Voice", True), ("", False),
                                      ("   ", False), ("x" * 40, False), (5, False)])
def test_page_speaker_label_validation(label, ok):
    from app.stories.loader import _parse_story
    data = _story({"speaker": "wolf", "speaker_label": label})
    if ok:
        story = _parse_story(data, "t")
        assert story.speaker_label(1) == str(label).strip()
    else:
        with pytest.raises(ValueError, match="speaker_label"):
            _parse_story(data, "t")
    with pytest.raises(ValueError, match="needs a `speaker`"):
        _parse_story(_story({"speaker_label": "???"}), "t")


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16"])
def test_mystery_tab_renders_in_pixel_font(preset):
    from app.pipeline.pdf import DISPLAY, register_fonts, tab_font_for
    register_fonts()
    assert tab_font_for("???") == DISPLAY  # the pixel font has the glyph: no fallback
    reader = PdfReader(io.BytesIO(build_book_pdf(_book(preset, num=7))))
    text = reader.pages[1].extract_text()
    assert "???" in text and "Come in, dear!" in text
    assert len(reader.pages[1].images) == 1  # never a portrait


def _story(page: dict, chars=None) -> dict:
    return {"id": "t", "characters": chars or [{"id": "wolf", "name": "the wolf"}],
            "pages": [{"page": 1, "text": "Hello {name}, said the wolf. Bye.", **page}]}


@pytest.mark.parametrize("page,needle", [
    ({"speech": [{"who": "wolf", "says": "Goodbye"}]}, "exact substring"),
    ({"speech": [{"who": "wolf", "says": "Bye."}, {"who": "wolf", "says": "Hello"}]},
     "reading order"),
    ({"speech": [{"who": "wolf", "says": "Hello {na"}]}, "placeholder"),
    ({"speech": [{"who": "wolf", "says": " Bye."}]}, "space"),
    ({"speech": [{"who": "owl", "says": "Bye."}]}, "unknown speech speaker"),
    ({"speech": [{"who": "wolf"}]}, "needs `who` and `says`"),
    ({"speaker": "hero", "speech": [{"who": "wolf", "says": "Bye."}]}, "several speak"),
    ({"portrait": True}, "needs a single `speaker`"),
    ({"speaker": "wolf", "portrait": True, "portrait_expression": "furious"},
     "portrait_expression"),
    ({"speaker": "wolf", "portrait": "sometimes"}, "must be true, false"),
    ({"portrait": "moment"}, "needs a single `speaker`"),
])
def test_bad_speech_metadata_fails_at_load(page, needle):
    with pytest.raises(ValueError, match=needle):
        _parse_story(_story(page), "t")


def test_quotes_in_says_rejected_and_placeholder_spans_render():
    with pytest.raises(ValueError, match="quotation"):
        _parse_story({"id": "t", "characters": [{"id": "wolf", "name": "w"}],
                      "pages": [{"page": 1, "text": 'He said "hi".',
                                 "speech": [{"who": "wolf", "says": '"hi"'}]}]}, "t")
    story = _parse_story(_story({"speech": [{"who": "wolf", "says": "Hello {name},"}]}), "t")
    runs = story.speech_runs(1, "Hello Mia, said the wolf. Bye.", "Mia", "she")
    assert runs == [("Hello Mia,", "wolf"), (" said the wolf. Bye.", None)]


def test_split_and_display_runs():
    runs = split_speech("A b, said c. D e!", [("w", "A b,"), ("w", "D e!")])
    assert runs == [("A b,", "w"), (" said c. ", None), ("D e!", "w")]
    shown = display_runs(runs)
    assert shown[0] == (f"{OPEN_QUOTE}A b,{CLOSE_QUOTE}", True) and shown[1][1] is False
    # an edited text that lost a span: the whole page stays narration
    assert split_speech("Something else.", [("w", "A b,")]) == [("Something else.", None)]


# --- PDF: mixed runs --------------------------------------------------------------------


def test_clean_runs_keeps_mask_and_spaces_between_runs_plain():
    text, mask = clean_runs([("“Hi,”", True), ("  said\tWolf. ", False),
                             ("“Bye!”", True)])
    assert text == "“Hi,” said Wolf. “Bye!”"
    assert len(mask) == len(text)
    assert mask[text.index("said")] is False and mask[0] is True
    assert mask[text.index(" said")] is False  # the joining space is narration
    assert all(mask[text.index("“Bye"):])


@pytest.mark.parametrize("text", ["a b c", "word " * 40, "x" * 300 + " tail",
                                  "Hello there, little one? said the wolf."])
def test_wrap_offsets_match_word_wrap(text):
    register_fonts()
    single = " ".join(text.split())
    lines = wrap_offsets(single, "PC-Andika-Bold", 18, 120)
    assert [single[a:b] for a, b in lines] == wrap(text, "PC-Andika-Bold", 18, 120)


def _book(preset="sprite16", story_id="red", num=2, text=None, name="Mia", portrait=None,
          zone=ZONE):
    story = load_story(story_id)
    pg = story.page(num)
    page = BookPage(page=num, title="t", caption="c",
                    text=text if text is not None else render_text(pg.text, name, "she"),
                    layout=pg.layout, zone=zone, png=_png(), portrait_png=portrait)
    return BookResult(story=story, preset=preset, detail=2,
                      hero_details=dataclasses.replace(HERO, name=name),
                      family_variant="fam_a", style="s", hero_png=_png(), pages=[page],
                      costs=[])


def _rg(color) -> bytes:
    def num(v: float) -> str:  # ReportLab's style: 6 decimals, no leading zero
        t = f"{v:.6f}".rstrip("0").rstrip(".")
        return t[1:] if t.startswith("0.") else t
    return f"{num(color.red)} {num(color.green)} {num(color.blue)} rg".encode()


def _content(pdf: bytes, page=1) -> bytes:
    return PdfReader(io.BytesIO(pdf)).pages[page].get_contents().get_data()


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16"])
def test_pdf_quotes_and_highlights_speech(preset):
    pdf = build_book_pdf(_book(preset, num=2))
    text = " ".join(PdfReader(io.BytesIO(pdf)).pages[1].extract_text().split())
    assert "“Good morning,” said a wolf" in text
    assert "“And where are you going, little one?”" in text
    style = dialogue.style_for(preset)
    data = _content(pdf)
    assert _rg(style.speech) in data and _rg(style.text_color) in data


def test_pdf_narration_page_has_no_highlight_and_fallback_box_uses_dark_speech():
    pdf = build_book_pdf(_book("sprite16", num=5))
    assert _rg(dialogue.style_for("sprite16").speech) not in _content(pdf)
    plain = build_book_pdf(_book("mystery", num=2))
    assert _rg(SPEECH_INK) in _content(plain)


def test_two_speaker_page_has_quotes_but_no_tab():
    pdf = build_book_pdf(_book("n64", num=9))
    text = " ".join(PdfReader(io.BytesIO(pdf)).pages[1].extract_text().split())
    assert "“Grandma, what big ears you have!” said Mia." in text
    assert "“All the better to hear you with,” said the wolf." in text
    assert "Wolf" not in text


def test_text_that_lost_its_spans_is_plain_narration():
    book = _book("sprite16", num=2, text="Good day, said a wolf.")
    para = page_paragraph(book, book.pages[0])
    assert para.spoken is None and para.text == "Good day, said a wolf."


def test_highlight_contrast_and_colour_blind_safety():
    for preset, fills in dialogue.FILLS.items():
        st = dialogue.style_for(preset)
        for fill in fills:
            assert dialogue.contrast(st.speech, HexColor(fill)) >= 4.5, (preset, fill)
            assert dialogue.contrast(st.text_color, HexColor(fill)) >= 4.5
    assert dialogue.contrast(SPEECH_INK, HexColor("#ffffff")) >= 4.5
    # quotes are always added, so speech never relies on colour alone
    assert display_runs([("Hi", "w")])[0][0].startswith(OPEN_QUOTE)


# --- PDF: portrait layout ---------------------------------------------------------------


def _inside(inner, outer, tol=0.01):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return x >= ox - tol and y >= oy - tol and x + w <= ox + ow + tol and y + h <= oy + oh + tol


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16"])
@pytest.mark.parametrize("portrait", [False, True])
@pytest.mark.parametrize("text", ["Come in, dear! called a voice from the bed.", "word " * 90])
def test_dialogue_fits_zone_with_and_without_portrait(preset, portrait, text):
    register_fonts()
    style = dialogue.style_for(preset)
    rect = zone_rect(ZONE)
    lay = layout_dialogue(Para(" ".join(text.split())), rect, style, tab=True,
                          portrait=portrait)
    assert _inside(lay.box, rect)
    lines = [i for i in lay.items if not isinstance(i, float)]
    block_h = sum(i.height for i in lines)
    assert lay.text_top - block_h >= lay.box[1] + style.pad_bottom - 0.5
    assert all(i.width <= lay.text_w + 0.01 for i in lines)
    assert lay.size >= DIALOGUE_MIN
    if portrait:
        px, py, side = lay.portrait
        assert side >= PORTRAIT_MIN and _inside((px, py, side, side), lay.box)
        box_top = lay.box[1] + lay.box[3]
        assert py + side <= box_top - style.tab_h / 2  # clear of the name tab
        assert lay.text_x >= px + side  # text never overlaps the portrait
        assert lay.text_x + lay.text_w <= lay.box[0] + lay.box[2] + 0.01
    else:
        assert lay.portrait is None and lay.centred


def test_low_zone_drops_portrait():
    register_fonts()
    rect = zone_rect(TextZone("bottom", 0.06, 0.9, 0.88, 0.05))
    lay = layout_dialogue("Hi there.", rect, dialogue.style_for("n64"), portrait=True)
    assert lay.portrait is None


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16"])
def test_portrait_page_renders(preset):
    pdf = build_book_pdf(_book(preset, num=7, portrait=_png((300, 300), (200, 80, 40))))
    reader = PdfReader(io.BytesIO(pdf))
    assert "Come in, dear!" in reader.pages[1].extract_text()
    assert len(reader.pages[1].images) == 2  # the page art + the portrait
    plain = PdfReader(io.BytesIO(build_book_pdf(_book(preset, num=7))))
    assert len(plain.pages[1].images) == 1


# --- the gate ---------------------------------------------------------------------------


def _grade(visible, boxes=()):
    return GradeResult(0.9, 0.9, boxes=list(boxes) or [DEFAULT_FACE],
                       visible_characters=visible)


def test_moment_portrait_gate_ignores_visibility():
    red = load_story("red")
    p3 = red.page(3)
    for grade in (None, _grade(None), _grade(["the wolf"]), _grade([])):
        assert portrait_gate(red, p3, grade)[0] is True
    # offscreen pages keep the gate
    off = dataclasses.replace(p3, portrait_when="offscreen")
    assert portrait_gate(red, off, None)[0] is False
    assert portrait_gate(red, off, _grade(["the wolf"]))[0] is False
    assert portrait_gate(red, off, _grade([]))[0] is True
    assert presence_names(red, off) == ["the wolf"]


def test_portrait_gate_cases():
    red = load_story("red")
    p7 = _cand()
    assert portrait_gate(red, p7, None) == (False, "presence unknown (picture not graded)")
    assert portrait_gate(red, p7, _grade(None))[0] is False  # no answer = unknown
    assert portrait_gate(red, p7, _grade(["the wolf"]))[0] is False
    assert portrait_gate(red, p7, _grade(["The Wolf"]))[0] is False
    boxed = _grade([], [Box("wolf face", 0.5, 0.2, 0.1, 0.1, "face")])
    ok, why = portrait_gate(red, p7, boxed)
    assert not ok and "box" in why
    assert portrait_gate(red, p7, _grade([])) == (True, "the wolf is not in the picture")
    assert portrait_gate(red, red.page(2), _grade([]))[0] is False  # not a candidate


def test_grader_presence_prompt_and_parsing():
    assert presence_note([]) == ""
    note = presence_note(["the wolf"])
    assert "visible_characters" in note and "- the wolf" in note and "disguise" in note
    assert visible_from(["The wolf in a nightcap", "hero"], ["the wolf", "Grandma"]) == [
        "the wolf"]
    assert visible_from([], ["the wolf"]) == []


def test_call_kinds():
    assert {PORTRAIT_KIND, PORTRAIT_CHECK_KIND} <= set(CALL_KINDS)
    assert all(len(k) <= 32 for k in CALL_KINDS)


# --- the pipeline -----------------------------------------------------------------------


class Recorder(FakeProvider):
    pass


def _portrait_calls(fake):
    return [c for c in fake.calls if c["prompt"].startswith("Task: draw a small square")]


async def test_portrait_made_when_speaker_not_visible_and_reused():
    assets, fake = MemoryAssets(), Recorder()
    grader = FakeGrader(visible=[])
    plan = _cand_plan("sprite16")
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=fake, grader=grader,
                        assets=assets, time_budget_s=0, hard_cap_s=0)
    book = await gen.run()
    page = book.pages[0]
    assert page.portrait_png is not None and page.visible == []
    assert page.portrait == {"character": "wolf", "costume": "grandma_disguise",
                             "expression": "sly"}
    calls = _portrait_calls(fake)
    assert len(calls) == 1 and calls[0]["aspect_ratio"] == "1:1"
    assert calls[0]["n_images"] == 1  # the wolf's disguise sheet is the reference
    assert "sly" in calls[0]["prompt"] or "cheeky" in calls[0]["prompt"]
    kinds = [c.kind for c in book.costs]
    assert kinds.count(PORTRAIT_KIND) == 1 and kinds.count(PORTRAIT_CHECK_KIND) == 1
    assert [c.presence for c in grader.calls if c.subject == "page"] == [["the wolf"]]
    assert set(assets.portraits) == {("wolf", "grandma_disguise", "sly")}
    pdf = build_book_pdf(book)
    assert len(PdfReader(io.BytesIO(pdf)).pages[1].images) == 2

    fake2 = Recorder()
    book2 = await BookGenerator(plan, HERO, await _photo(), hero_provider=fake2,
                                grader=FakeGrader(visible=[]), assets=assets,
                                time_budget_s=0, hard_cap_s=0).run()
    assert book2.pages[0].portrait_png == assets.portraits[("wolf", "grandma_disguise", "sly")]
    assert _portrait_calls(fake2) == []
    assert PORTRAIT_KIND not in [c.kind for c in book2.costs]


async def test_no_portrait_when_speaker_visible_or_presence_unknown():
    for grader in (FakeGrader(),  # default: every asked name visible
                   FakeGrader(fn=lambda call, n: GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE],
                                                             meta=CallMeta()))):
        fake = Recorder()
        book = await BookGenerator(_cand_plan("n64"), HERO, await _photo(),
                                   hero_provider=fake, grader=grader, assets=MemoryAssets(),
                                   time_budget_s=0, hard_cap_s=0).run()
        assert book.pages[0].portrait_png is None and book.pages[0].portrait is None
        assert _portrait_calls(fake) == []
    # no grader at all: unknown presence, no portrait, no presence question
    fake = Recorder()
    book = await BookGenerator(_cand_plan("n64"), HERO, await _photo(),
                               hero_provider=fake).run()
    assert book.pages[0].portrait_png is None and _portrait_calls(fake) == []


async def test_non_candidate_pages_never_ask_presence():
    grader = FakeGrader(visible=[])
    book = await BookGenerator(plan_book("red", [2], "n64", 2), HERO, await _photo(),
                               hero_provider=Recorder(), grader=grader,
                               time_budget_s=0, hard_cap_s=0).run()
    assert [c.presence for c in grader.calls if c.subject == "page"] == [[]]
    assert book.pages[0].portrait_png is None and book.pages[0].visible is None


async def test_failed_portrait_check_regenerates_once():
    grader = FakeGrader(visible=[], portrait_fn=lambda call, n: PortraitCheck(
        expression_ok=n > 0, issues=["make the grin slyer"]))
    fake = Recorder()
    book = await BookGenerator(_cand_plan("ps1"), HERO, await _photo(),
                               hero_provider=fake, grader=grader, assets=MemoryAssets(),
                               time_budget_s=0, hard_cap_s=0).run()
    calls = _portrait_calls(fake)
    assert len(calls) == 2 and "make the grin slyer" in calls[1]["prompt"]
    assert len(grader.portrait_calls) == 2 and book.pages[0].portrait_png is not None


async def test_portrait_skipped_past_the_hard_cap():
    fake = Recorder()
    gen = BookGenerator(_cand_plan("ps1"), HERO, await _photo(),
                        hero_provider=fake, grader=FakeGrader(visible=[]),
                        assets=MemoryAssets(), time_budget_s=0, hard_cap_s=70)
    gen.image_est_s = 1000.0  # one more image call cannot end before the cap
    book = await gen.run()
    assert book.pages[0].portrait_png is None and _portrait_calls(fake) == []


async def test_hero_and_family_portraits_use_book_references():
    story = load_story("jack")
    hero_page = dataclasses.replace(story.page(17), portrait=True,
                                    portrait_expression="happy")
    plan = plan_book("jack", [17], "n64", 2)
    plan = dataclasses.replace(plan, pages=(hero_page,))
    fake = Recorder()
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=fake,
                        grader=FakeGrader(visible=[], fn=None), assets=MemoryAssets(),
                        time_budget_s=0, hard_cap_s=0)
    # hero speaker: boxes label the hero, so make the grade box-free for this test
    gen.grader.fn = lambda call, n: GradeResult(
        0.9, 0.9, boxes=[Box("cupboard", 0.1, 0.1, 0.1, 0.1)], meta=CallMeta(),
        visible_characters=[] if call.presence else None)
    book = await gen.run()
    assert book.pages[0].portrait == {"character": "hero", "costume": "",
                                      "expression": "happy"}
    assert _portrait_calls(fake)[0]["n_images"] == 1

    red = load_story("red")
    mum = dataclasses.replace(red.page(1), portrait=True)
    plan = dataclasses.replace(plan_book("red", [1], "n64", 2), pages=(mum,))
    fake = Recorder()
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=fake,
                        grader=FakeGrader(visible=[]), assets=MemoryAssets(),
                        time_budget_s=0, hard_cap_s=0)
    book = await gen.run()
    assert book.pages[0].portrait["character"] == "mother"
    prompt = _portrait_calls(fake)[0]["prompt"]
    assert "family reference sheet" in prompt
    assert gen.family_sheet_png is not None


async def test_make_portrait_failed_image_call_means_no_portrait():
    async def boom(kind, page, prompt, refs):
        raise RuntimeError("down")

    assert await make_portrait(boom, "s", name="the wolf", description="d",
                               expression="sly", ref=b"x", ref_label="sheet") is None


# --- payloads ---------------------------------------------------------------------------


def test_progress_speech_for():
    from app.jobs.progress import speech_for

    runs = speech_for("red", 2, render_text(load_story("red").page(2).text, "Mia", "she"),
                      "Mia", "she")
    assert runs[0] == [f"{OPEN_QUOTE}Good morning,{CLOSE_QUOTE}", True]
    assert runs[1][1] is False
    assert speech_for("red", 5, "anything", "Mia", "she") == []
    assert speech_for("nope", 1, "x", "Mia", "she") == []


async def test_api_portrait_payload_and_endpoint(photo_jpeg, monkeypatch):
    import app.api.books as books_api
    from app.config import get_settings
    from app.db import repo_assets
    from app.db.session import get_sessionmaker
    from app.jobs.runner import registry
    from tests.conftest import client
    from tests.test_books_api import FORM, _post, parse_sse
    from tests.wallet_helpers import login_funded as login

    monkeypatch.setenv("BOOK_PAGES", "2,7")
    get_settings.cache_clear()
    # No story page is a portrait candidate now: make red p7 one for this test only.
    p7 = load_story("red").page(7)
    monkeypatch.setattr(p7, "portrait", True)
    monkeypatch.setattr(p7, "portrait_expression", "sly")
    provider = FakeProvider()
    monkeypatch.setattr(books_api, "get_image_provider", lambda name=None, model=None: provider)
    monkeypatch.setattr(books_api, "get_vision_grader",
                        lambda name=None: FakeGrader(visible=[]))
    async with client() as c:
        await login(c)
        r = await _post(c, photo_jpeg, **{**FORM, "story": "red"})
        book_id = r.json()["book_id"]
        await registry.wait(book_id)
        events = parse_sse((await c.get(f"/api/books/{book_id}/events")).text)
        pages = {d["page"]: d for e, d in events if e == "page"}
        assert pages[2]["portrait_url"] is None
        assert pages[2]["speech"][0] == [f"{OPEN_QUOTE}Good morning,{CLOSE_QUOTE}", True]
        assert pages[7]["portrait_url"] == f"/api/books/{book_id}/pages/7/portrait.png"
        img = await c.get(pages[7]["portrait_url"])
        assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
        assert (await c.get(f"/api/books/{book_id}/pages/2/portrait.png")).status_code == 404
        detail = (await c.get(f"/api/books/{book_id}")).json()
        assert {p["page"]: bool(p["portrait_url"]) for p in detail["pages"]} == {
            2: False, 7: True}
    async with get_sessionmaker()() as s:
        rows = await repo_assets.list_attempts(s, book_id)
        assert {r.page: r.visible_characters for r in rows} == {0: None, 2: None, 7: []}
        assert await repo_assets.get_portrait_png(s, "red", "sprite16", "wolf",
                                                  "grandma_disguise", "sly")


# --- "different moment" portraits ---------------------------------------------------------


async def _moment_book(story_id, page, preset="n64", assets=None, grader=None, fake=None,
                       **gen_kw):
    plan = plan_book(story_id, [page], preset, 2)
    fake = fake or Recorder()
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=fake,
                        grader=grader or FakeGrader(), assets=assets or MemoryAssets(),
                        time_budget_s=0, hard_cap_s=0, **gen_kw)
    return gen, fake, await gen.run()


async def test_moment_portrait_shown_even_when_speaker_is_visible():
    # The default FakeGrader sees every asked name; a moment page asks none and still shows
    # the portrait (the plain wolf on red p3).
    grader = FakeGrader()
    assets = MemoryAssets()
    _, fake, book = await _moment_book("red", 3, assets=assets, grader=grader)
    assert book.pages[0].portrait == {"character": "wolf", "costume": "", "expression": "sly"}
    assert book.pages[0].portrait_png is not None
    assert [c.presence for c in grader.calls if c.subject == "page"] == [[]]
    assert set(assets.portraits) == {("wolf", "", "sly")}
    assert len(_portrait_calls(fake)) == 1
    # no grader at all: still shown
    _, fake2, book2 = await _moment_book("red", 3, assets=MemoryAssets(),
                                         grader=None)
    assert book2.pages[0].portrait_png is not None


async def test_moment_portraits_hero_family_and_cast_sources():
    gen, fake, book = await _moment_book("jack", 17)
    assert book.pages[0].portrait == {"character": "hero", "costume": "",
                                      "expression": "scared"}
    assert _portrait_calls(fake)[0]["n_images"] == 1
    gen, fake, book = await _moment_book("jack", 3)
    assert book.pages[0].portrait["character"] == "mother"
    assert "family reference sheet" in _portrait_calls(fake)[0]["prompt"]
    assert "huff" in _portrait_calls(fake)[0]["prompt"]  # the angry face
    gen, fake, book = await _moment_book("jack", 11)
    assert book.pages[0].portrait["character"] == "giant"
    assert set(gen.assets.portraits) == {("giant", gen.plan.story.characters[
        "giant"].sheet_costume_id(11), "angry")}


async def test_moment_portraits_start_before_the_page_grade():
    order = []

    def grade_fn(call, n):
        if call.subject == "page":
            order.append("grade")
        return GradeResult(0.9, 0.9, boxes=[DEFAULT_FACE], meta=CallMeta())

    class Spy(Recorder):
        async def edit(self, prompt, refs, **kw):
            order.append("portrait" if prompt.startswith("Task: draw a small square")
                         else "image")
            return await super().edit(prompt, refs, **kw)

    await _moment_book("red", 3, grader=FakeGrader(fn=grade_fn), fake=Spy())
    assert order.index("portrait") < order.index("grade")


async def test_failed_moment_portrait_leaves_the_box_without_one():
    class Boom(Recorder):
        async def edit(self, prompt, refs, **kw):
            if prompt.startswith("Task: draw a small square"):
                raise RuntimeError("down")
            return await super().edit(prompt, refs, **kw)

    _, _, book = await _moment_book("red", 3, fake=Boom())
    assert book.pages[0].portrait_png is None and book.pages[0].portrait is None
    assert build_book_pdf(book)  # still renders


async def test_moment_portrait_skipped_past_the_hard_cap():
    plan = plan_book("red", [3], "ps1", 2)
    fake = Recorder()
    gen = BookGenerator(plan, HERO, await _photo(), hero_provider=fake,
                        grader=FakeGrader(), assets=MemoryAssets(), time_budget_s=0,
                        hard_cap_s=70)
    gen.image_est_s = 1000.0
    book = await gen.run()
    assert book.pages[0].portrait_png is None and _portrait_calls(fake) == []
