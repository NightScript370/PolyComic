"""Plates are painted without the hero and without family members (they are inserted per
book from the bible), so the YAML plate prompts must describe empty spots, never them."""

import re

import pytest

from app.stories.loader import load_story

HERO_WORDS = ("hero", "child", "children", "kid", "jack", "red riding hood")


@pytest.mark.parametrize("story_id", ["jack", "red"])
def test_plate_prompts_never_name_hero_or_family(story_id):
    story = load_story(story_id)
    family = [c for c in story.characters.values() if c.family]
    words = [*HERO_WORDS, *(c.id for c in family), *(c.name for c in family)]
    words += ["grandma", "grandmother", "granny", "mum", "mom", "mother"]
    for page in story.pages:
        assert page.plate_prompt, page.page
        for w in words:
            assert not re.search(rf"\b{re.escape(w)}('s)?\b", page.plate_prompt, re.I), (
                story_id, page.page, w)


@pytest.mark.parametrize("story_id", ["jack", "red"])
def test_plate_prompts_leave_an_empty_spot(story_id):
    for page in load_story(story_id).pages:
        assert "empty" in page.plate_prompt.lower(), (story_id, page.page)
