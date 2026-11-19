"""Structural validation of the story YAML files against the shared page template.

Works on the raw YAML (not the tolerant loader) so a typo in a field name fails
here instead of silently becoming an empty string.
"""

from __future__ import annotations

import re
from functools import cache

import pytest
import yaml

from app.stories.loader import LAYOUTS_PATH, STORIES_DIR, render_text
from tests.test_styles import BANNED

STORIES = ["jack", "red"]
N_PAGES = 20
ALLOWED_PLACEHOLDERS = {
    "name", "they", "them", "their", "theirs", "themself", "They", "Their",
    "is", "was", "has", "does",
}
PAGE_FIELDS = [
    "page", "layout", "title", "text", "caption", "plate_prompt", "hero_instruction",
    "characters", "palette_note", "must_be_visible",
]
ART_FIELDS = ["caption", "plate_prompt", "hero_instruction", "palette_note"]
OPTIONAL_ART_FIELDS = ["art_caption", "expression"]
# Art fields must call the hero "the hero child" (role names are case-sensitive so the
# colour "red" is fine) and must not ask for drawn text / sound-effect shapes.
ART_FORBIDDEN = re.compile(r"\b(Jack|Red)\b")
ART_FORBIDDEN_CI = re.compile(
    r"\b(snore|sound effect|musical note|note shape|ring shape|shake lines?|letters|signage)\b",
    re.I,
)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@cache
def _story(story_id: str) -> dict:
    with (STORIES_DIR / f"{story_id}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@cache
def _layouts() -> dict:
    with LAYOUTS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _text_strings(s: dict) -> list[str]:
    return [s["subtitle"], s["about_me"]["heading"]] + [p["text"] for p in s["pages"]]


def _assert_no_brands(text: str, where: str) -> None:
    low = text.lower()
    for word in BANNED:
        assert not re.search(rf"\b{re.escape(word)}\b", low), f"{where}: banned word {word!r}"


def test_template_has_20_slots_in_order():
    slots = _layouts()["slots"]
    assert [s["slot"] for s in slots] == list(range(1, N_PAGES + 1))
    assert len({s["id"] for s in slots}) == N_PAGES


def test_template_text_zones_in_unit_box():
    data = _layouts()
    for s in [*data["slots"], data["about_me"]]:
        assert s["allowed_text_zones"], s["id"]
        boxes = [z["box"] for z in s["allowed_text_zones"]]
        if "portrait_box" in s:
            boxes.append(s["portrait_box"])
        for b in boxes:
            assert b["w"] > 0 and b["h"] > 0, s["id"]
            assert 0 <= b["x"] and b["x"] + b["w"] <= 1 + 1e-9, (s["id"], b)
            assert 0 <= b["y"] and b["y"] + b["h"] <= 1 + 1e-9, (s["id"], b)


@pytest.mark.parametrize("story_id", STORIES)
def test_story_parses_with_20_pages_on_matching_slots(story_id):
    s = _story(story_id)
    for key in ["id", "title", "subtitle", "hero_outfit", "family_variant_ids", "characters",
                "pages", "about_me"]:
        assert s.get(key), f"{story_id}: missing {key}"
    assert s["about_me"].get("heading")
    slots = [x["id"] for x in _layouts()["slots"]]
    assert [p["page"] for p in s["pages"]] == list(range(1, N_PAGES + 1))
    for p in s["pages"]:
        assert p["layout"] == slots[p["page"] - 1], f"{story_id} p{p['page']} wrong slot"
        for f in PAGE_FIELDS:
            assert f in p, f"{story_id} p{p['page']} missing {f}"
        for f in ["title", "text", "caption", "plate_prompt", "hero_instruction"]:
            assert str(p[f]).strip(), f"{story_id} p{p['page']} empty {f}"
        assert p["must_be_visible"], f"{story_id} p{p['page']} empty must_be_visible"


@pytest.mark.parametrize("story_id", STORIES)
def test_text_placeholders_are_allowed(story_id):
    for t in _text_strings(_story(story_id)):
        for ph in PLACEHOLDER.findall(t):
            assert ph in ALLOWED_PLACEHOLDERS, f"{story_id}: bad placeholder {{{ph}}} in {t!r}"
        stripped = PLACEHOLDER.sub("", t)
        assert "{" not in stripped and "}" not in stripped, f"{story_id}: stray brace in {t!r}"


@pytest.mark.parametrize("story_id", STORIES)
def test_character_page_lists_consistent(story_id):
    s = _story(story_id)
    chars = {c["id"]: c for c in s["characters"]}
    pages = {p["page"]: p for p in s["pages"]}
    for p in s["pages"]:
        for cid in p["characters"]:
            assert cid in chars, f"{story_id} p{p['page']}: unknown character {cid}"
            assert p["page"] in chars[cid]["pages"], f"{story_id}: {cid} missing page {p['page']}"
    for c in chars.values():
        for n in c["pages"]:
            assert c["id"] in pages[n]["characters"], f"{story_id}: p{n} does not list {c['id']}"


@pytest.mark.parametrize("story_id", STORIES)
def test_family_variants_match_story_ids(story_id):
    s = _story(story_id)
    ids = s["family_variant_ids"]
    assert ids and len(set(ids)) == len(ids)
    family = [c for c in s["characters"] if c.get("family")]
    assert family, f"{story_id}: no family characters"
    for c in family:
        assert [v["id"] for v in c["variants"]] == ids, c["id"]
        for v in c["variants"]:
            assert v.get("skin_tone") and v.get("hair"), (c["id"], v["id"])


@pytest.mark.parametrize("story_id", STORIES)
def test_art_fields_have_no_brands_or_role_names(story_id):
    s = _story(story_id)
    for p in s["pages"]:
        for f in ART_FIELDS:
            where = f"{story_id} p{p['page']} {f}"
            text = " ".join(str(p[f]).split())
            _assert_no_brands(text, where)
            assert not ART_FORBIDDEN.search(text), f"{where}: names the role"
            assert not ART_FORBIDDEN_CI.search(text), f"{where}: asks for drawn text/effects"
        for f in OPTIONAL_ART_FIELDS:  # art-only scene / expression overrides
            if p.get(f):
                where = f"{story_id} p{p['page']} {f}"
                text = " ".join(str(p[f]).split())
                _assert_no_brands(text, where)
                assert not ART_FORBIDDEN.search(text), f"{where}: names the role"
                assert not ART_FORBIDDEN_CI.search(text), f"{where}: asks for drawn text"
        for m in p["must_be_visible"]:
            _assert_no_brands(str(m), f"{story_id} p{p['page']} must_be_visible")
    for c in s["characters"]:
        _assert_no_brands(" ".join(str(c["description"]).split()), f"{story_id} {c['id']}")
    outfit = s["hero_outfit"]
    _assert_no_brands(" ".join(str(v) for v in outfit.values()) if isinstance(outfit, dict)
                      else str(outfit), f"{story_id} hero_outfit")
    _assert_no_brands(str(s["about_me"].get("hero_instruction", "")), f"{story_id} about_me")


@pytest.mark.parametrize("story_id", STORIES)
@pytest.mark.parametrize("pronouns", ["he", "she", "they"])
def test_every_text_renders_without_leftover_braces(story_id, pronouns):
    for t in _text_strings(_story(story_id)):
        out = render_text(" ".join(t.split()), "Mia", pronouns)
        assert "{" not in out and "}" not in out, f"{story_id}/{pronouns}: {out!r}"
        assert out.strip()
