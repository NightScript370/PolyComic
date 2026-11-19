import asyncio
import io

import pytest
from pypdf import PdfReader

from app.pipeline.book import HeroDetails, default_family_variant, run_book
from app.pipeline.pdf import (
    BODY,
    PAGE_H,
    PAGE_W,
    Para,
    build_book_pdf,
    clean_text,
    fit_block,
    pdf_filename,
    register_fonts,
    wrap,
)
from app.pipeline.placement import choose_zone
from app.pipeline.prompts import scene_prompt
from app.providers.fake import FakeProvider
from app.stories.loader import get_layout, load_story
from app.styles.presets import build_style_string
from tests.test_styles import _assert_no_brands

MM = 72 / 25.4


class SlowFake(FakeProvider):
    """Fake provider that sleeps so we can observe concurrency."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0

    async def edit(self, prompt, images, *, aspect_ratio="3:4", size_hint=None):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return await super().edit(prompt, images, aspect_ratio=aspect_ratio)
        finally:
            self.in_flight -= 1


def _hero(**kw) -> HeroDetails:
    base = dict(name="Mia", pronouns="she", age=7,
                favourites={"colour": "green", "animal": "otters"}, about="Mia loves maps.")
    base.update(kw)
    return HeroDetails(**base)


async def _book(provider=None, pages=(1, 2, 3), **kw):
    provider = provider or FakeProvider()
    photo = (await FakeProvider().edit("photo", [])).png
    book = await run_book(provider, photo, "n64", 2, story_id="jack", pages=pages,
                          hero=_hero(**kw))
    return provider, book


async def test_pipeline_one_hero_then_pages_concurrently():
    fake, book = await _book(SlowFake())
    assert len(fake.calls) == 4
    assert [c["n_images"] for c in fake.calls] == [1, 1, 1, 1]  # hero: photo; pages: hero
    assert "hero character reference" in fake.calls[0]["prompt"]
    assert fake.max_in_flight == 3  # the three page calls overlapped
    assert [p.page for p in book.pages] == [1, 2, 3]
    assert [c.kind for c in book.costs] == ["hero", "page", "page", "page"]
    assert [c.page for c in book.costs] == [None, 1, 2, 3]
    assert book.est_cost_usd == 0.0
    for p in book.pages:
        assert p.title and p.caption and p.text and p.png.startswith(b"\x89PNG")
        assert p.zone == choose_zone(None, get_layout(p.layout), None)


async def test_style_string_identical_in_every_call():
    fake, book = await _book()
    style = build_style_string("n64", 2)
    assert book.style == style
    for call in fake.calls:
        assert call["prompt"].count(style) == 1
        _assert_no_brands(call["prompt"])


async def test_any_pages_and_other_story_work():
    fake = FakeProvider()
    photo = (await FakeProvider().edit("photo", [])).png
    book = await run_book(fake, photo, "ps1", 1, story_id="red", pages=[20, 4],
                          hero=_hero(pronouns="they"))
    assert [p.page for p in book.pages] == [20, 4]
    assert len(fake.calls) == 3
    with pytest.raises(LookupError):
        await run_book(fake, photo, "ps1", 1, story_id="jack", pages=[21], hero=_hero())
    assert len(fake.calls) == 3  # bad page fails before any provider call


async def test_page_text_rendered_with_pronouns():
    _, he = await _book(name="Leo", pronouns="he", pages=(1,))
    _, they = await _book(name="Kai", pronouns="they", pages=(1,))
    assert he.pages[0].text == "Leo and his mother had nothing left to sell but Milky-White."
    assert they.pages[0].text == "Kai and their mother had nothing left to sell but Milky-White."
    assert he.subtitle == "starring Leo as Jack"


def test_scene_prompt_family_variant():
    story = load_story("jack")
    style = build_style_string("sprite16", 3)
    p1, p2 = story.page(1), story.page(2)  # page 1 has Mother, page 2 has no family
    mother = story.characters["mother"]
    for vid in story.family_variant_ids:
        v = mother.variants[vid]
        prompt = scene_prompt(style, p1, story, vid)
        assert v.skin_tone in prompt and v.hair in prompt
        assert prompt.count(style) == 1
        assert "Skin tone:" not in scene_prompt(style, p2, story, vid)
    assert "Skin tone:" not in scene_prompt(style, p1, story)  # no variant -> base text only
    assert default_family_variant(story) == story.family_variant_ids[0]


async def test_unknown_family_variant_rejected():
    photo = (await FakeProvider().edit("photo", [])).png
    with pytest.raises(ValueError):
        await run_book(FakeProvider(), photo, "n64", 2, story_id="jack", pages=[1],
                       hero=_hero(), family_variant="fam_z")


def test_hero_details_pronouns_validated():
    assert HeroDetails(name="A", pronouns="They/Them").pronouns == "they"
    with pytest.raises(ValueError):
        HeroDetails(name="A", pronouns="it")


async def test_pdf_cover_pages_about_me():
    _, book = await _book()
    pdf = build_book_pdf(book)
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 5  # cover + 3 story pages + About Me
    first = reader.pages[0]
    assert float(first.mediabox.width) == pytest.approx(216 * MM, abs=0.01)
    assert float(first.mediabox.height) == pytest.approx(286 * MM, abs=0.01)
    assert float(first.trimbox.width) == pytest.approx(210 * MM, abs=0.01)
    assert float(first.trimbox.left) == pytest.approx(3 * MM, abs=0.01)
    assert (PAGE_W, PAGE_H) == pytest.approx((216 * MM, 286 * MM))
    assert reader.metadata.title == "Jack and the Beanstalk - starring Mia as Jack"

    texts = [" ".join(pg.extract_text().split()) for pg in reader.pages]
    assert "starring Mia as Jack" in texts[0]
    assert "nothing left to sell" in texts[1]
    about = texts[4]
    for expected in ["My name is Mia", "7 years old", "Colour: green", "Animal: otters",
                     "Mia loves maps."]:
        assert expected in about, (expected, about)
    assert "Food" not in about and "Game" not in about  # only filled favourites


async def test_pdf_handles_hostile_and_long_text():
    _, book = await _book(name="<b>Zoë</b> %PDF \\) (", age=None, favourites={},
                          about="x" * 500)
    reader = PdfReader(io.BytesIO(build_book_pdf(book)))
    assert len(reader.pages) == 5
    about = reader.pages[4].extract_text()
    assert "years old" not in about and "favourite" not in about


def test_text_helpers():
    register_fonts()
    assert clean_text("a\x00b​  c\n\td") == "ab c d"
    lines = wrap("supercalifragilistic " * 3 + "z" * 200, BODY, 20, 200)
    assert all(len(line) > 0 for line in lines) and "".join(lines).count("z") == 200
    size, _ = fit_block([Para("short")], 400, 100)
    assert size == 26
    size, _ = fit_block([Para("word " * 400)], 300, 60)
    assert size == 14  # clamps at the minimum
    assert pdf_filename("Jack and the Beanstalk") == "JackAndTheBeanstalk.pdf"
    assert pdf_filename("Little Red Riding Hood") == "LittleRedRidingHood.pdf"
