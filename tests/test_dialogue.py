"""Dialogue-window page text: per-preset looks, speaker tab, fitting, fallback."""

import io

import pytest
from PIL import Image
from pypdf import PdfReader

from app.pipeline import dialogue
from app.pipeline.book import BookPage, BookResult, HeroDetails
from app.pipeline.pdf import BODY, build_book_pdf, register_fonts, zone_rect
from app.pipeline.placement import TextZone
from app.stories.loader import load_story

ZONE = TextZone("bottom", 0.06, 0.75, 0.88, 0.19)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 320), (60, 120, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _book(preset="sprite16", story_id="red", num=2, text="Good morning, said a wolf.",
          name="Mia", zone=ZONE):
    story = load_story(story_id)
    pg = story.page(num)
    page = BookPage(page=num, title="t", caption="c", text=text, layout=pg.layout,
                    zone=zone, png=_png())
    return BookResult(story=story, preset=preset, detail=2,
                      hero_details=HeroDetails(name=name, pronouns="she"),
                      family_variant="fam_a", style="s", hero_png=_png(), pages=[page],
                      costs=[])


def _story_page_text(book) -> str:
    return " ".join(PdfReader(io.BytesIO(build_book_pdf(book))).pages[1].extract_text().split())


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16", "mystery"])
def test_every_preset_renders_with_text(preset):
    assert "Good morning" in _story_page_text(_book(preset))


@pytest.mark.parametrize("preset", ["n64", "ps1", "sprite16"])
def test_tab_only_when_speaker_set(preset):
    assert "Wolf" in _story_page_text(_book(preset, num=2))
    assert "Wolf" not in _story_page_text(_book(preset, num=4, text="A meadow of flowers."))


def test_unknown_preset_falls_back_to_plain_box():
    assert dialogue.style_for("mystery") is None and dialogue.style_for(None) is None
    # no tab in the fallback box
    assert "Wolf" not in _story_page_text(_book("mystery", num=2))


def test_hero_speaker_uses_child_name():
    text = _story_page_text(_book("n64", "jack", 17, "Mother, the axe! cried Zed.", name="Zed"))
    assert "Zed" in text and text.count("Zed") == 2  # tab + text


def test_long_name_and_text_stay_inside_zone():
    register_fonts()
    book = _book("sprite16", "jack", 17, "word " * 60, name="Bartholomew" * 4)
    pdf = build_book_pdf(book)
    assert pdf.startswith(b"%PDF")
    x, y, w, h = zone_rect(ZONE)
    seen = []

    def visit(t, cm, tm, fd, fs):
        if t.strip():
            seen.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]))

    PdfReader(io.BytesIO(pdf)).pages[1].extract_text(visitor_text=visit)
    assert seen
    assert all(x - 1 <= px <= x + w + 1 and y - 1 <= py <= y + h + 1 for px, py in seen)


def test_tab_label_fit_truncates():
    register_fonts()
    label, size = dialogue.tab_label_fit("A" * 40, BODY, 9, 60)
    assert len(label) < 40 or size < 9
    from reportlab.pdfbase import pdfmetrics
    assert pdfmetrics.stringWidth(label, BODY, size) <= 60 + 0.01


def test_speaker_labels_in_stories():
    red, jack = load_story("red"), load_story("jack")
    assert red.speaker_label(2, "Mia") == "Wolf"
    assert red.speaker_label(1, "Mia") == "Mother"
    assert red.speaker_label(14, "Mia") == "Grandma"
    assert red.speaker_label(9, "Mia") == ""  # two speakers
    assert red.speaker_label(4, "Mia") == "Mia"  # the hero's one line
    assert red.speaker_label(7, "Mia") == "???"  # page-level override
    assert red.speaker_label(8, "Mia") == "Grandma"
    assert red.speaker_label(5, "Mia") == ""  # narration
    assert jack.speaker_label(2, "Mia") == "Old man"
    assert jack.speaker_label(7, "Mia") == "Giant's wife"
    assert jack.speaker_label(14, "Mia") == "Harp"
    assert jack.speaker_label(17, "Mia") == "Mia"


def test_progress_speaker_for_and_unknown():
    from app.jobs.progress import speaker_for

    assert speaker_for("red", 2, "Mia") == "Wolf"
    assert speaker_for("jack", 17, "Zed") == "Zed"
    assert speaker_for("red", 4, "Mia") == "Mia"
    assert speaker_for("red", 5, "Mia") == ""
    assert speaker_for("nope_story", 1, "Mia") == ""
    assert speaker_for("red", 99, "Mia") == ""
