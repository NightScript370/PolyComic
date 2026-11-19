import pytest

from app.pipeline.hw1 import hw1_pages
from app.stories.loader import (
    find_page,
    get_layout,
    load_layouts,
    load_story,
    render_text,
)


@pytest.mark.parametrize("story_id", ["jack", "red"])
def test_story_loads(story_id):
    story = load_story(story_id)
    assert story.title
    assert story.hero_outfit
    assert story.pages
    layouts = load_layouts()
    for page in story.pages:
        assert page.caption, f"page {page.page} has no caption"
        assert page.layout in layouts, f"page {page.page} layout {page.layout} missing"
        for cid in page.characters:
            assert cid in story.characters, f"page {page.page} unknown character {cid}"


def test_layouts_have_quiet_zones():
    layouts = load_layouts()
    assert len([k for k in layouts if k != "about_me"]) == 20
    assert all(lay.quiet_zone_instruction for lay in layouts.values())
    assert get_layout("s05_vertical_ascent").allowed_text_zones


def test_hw1_pages_found_by_title():
    climb, plant = hw1_pages()
    assert climb.page == 5 and "climb" in climb.title.lower()
    assert plant.page == 19 and "plant" in plant.title.lower()


def test_find_page_fallback_and_missing():
    story = load_story("jack")
    assert find_page(story, title="no such title", page=19).page == 19
    with pytest.raises(LookupError):
        find_page(story, title="no such title")


@pytest.mark.parametrize(
    ("pronouns", "expected"),
    [
        ("he", "Sam said he is brave. Sam has his map. He was ready, by himself. It is his."),
        ("she", "Sam said she is brave. Sam has her map. She was ready, by herself. It is hers."),
        ("they", "Sam said they are brave. Sam have their map. They were ready, by themself. "
                 "It is theirs."),
    ],
)
def test_render_text(pronouns, expected):
    text = ("{name} said {they} {is} brave. {name} {has} {their} map. {They} {was} ready, "
            "by {themself}. It is {theirs}.")
    assert render_text(text, "Sam", pronouns) == expected


def test_render_text_more():
    assert render_text("Give it to {them}. {Their} turn. {does} it?", "Ali", "she") == (
        "Give it to her. Her turn. does it?"
    )
    assert render_text("{They} {does}", "Ali", "they/them") == "They do"
    assert render_text("{unknown} {name}", "Ali", "he") == "{unknown} Ali"
    with pytest.raises(ValueError):
        render_text("{name}", "Ali", "it")


def test_real_page_text_renders():
    story = load_story("jack")
    for page in story.pages:
        for pr in ("he", "she", "they"):
            out = render_text(page.text, "Mia", pr)
            assert "{name}" not in out and "{they}" not in out
