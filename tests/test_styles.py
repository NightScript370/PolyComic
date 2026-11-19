import re

import pytest

from app.pipeline.hw1 import hw1_pages
from app.pipeline.prompts import hero_prompt, scene_prompt
from app.stories.loader import load_story
from app.styles.presets import (
    CONSTRAINTS,
    DETAIL_LEVELS,
    PRESETS,
    build_style_string,
    styles_for_ui,
)

BANNED = [
    "nintendo", "mario", "luigi", "zelda", "kirby", "donkey kong", "banjo", "pokemon",
    "sony", "playstation", "psx", "ps1", "ps one", "crash bandicoot", "spyro",
    "sega", "sonic", "mania", "genesis", "mega drive", "dreamcast", "saturn",
    "n64", "snes", "super famicom", "famicom", "nes", "game boy", "gameboy",
    "capcom", "konami", "squaresoft", "square enix", "final fantasy", "atari", "xbox",
    "naughty dog", "sprite16", "ultra 64",
]


def _assert_no_brands(text: str) -> None:
    low = text.lower()
    for word in BANNED:
        assert not re.search(rf"\b{re.escape(word)}\b", low), f"banned word {word!r} in prompt"


@pytest.mark.parametrize("preset", list(PRESETS))
@pytest.mark.parametrize("detail", DETAIL_LEVELS)
def test_style_string_is_locked_and_clean(preset, detail):
    a = build_style_string(preset, detail)
    b = build_style_string(preset, detail)
    assert a == b and a.encode() == b.encode()
    _assert_no_brands(a)
    _assert_no_brands(CONSTRAINTS)
    assert "large expressive eyes" in a
    assert "hairline" in a


def test_detail_levels_differ():
    for preset in PRESETS:
        assert len({build_style_string(preset, d) for d in DETAIL_LEVELS}) == 3


def test_bad_inputs():
    with pytest.raises(ValueError):
        build_style_string("nope", 2)
    with pytest.raises(ValueError):
        build_style_string("n64", 4)


def test_styles_for_ui():
    ids = [s["id"] for s in styles_for_ui()]
    assert ids == ["n64", "ps1", "sprite16"]
    assert all(s["detail_levels"] == [1, 2, 3] for s in styles_for_ui())


def test_prompts_contain_style_verbatim_and_no_brands():
    story = load_story("jack")
    for preset in PRESETS:
        style = build_style_string(preset, 2)
        hp = hero_prompt(style, story.hero_outfit)
        assert style in hp and CONSTRAINTS in hp
        _assert_no_brands(hp)
        for page in hw1_pages():
            sp = scene_prompt(style, page, story)
            assert style in sp and CONSTRAINTS in sp
            assert page.caption in sp
            assert "hero_outfit" not in sp
            _assert_no_brands(sp)


# Words that pull the look toward modern 3D film rendering or name a studio / engine.
BANNED += ["nintendo 64", "goldeneye", "rareware", "pixar", "dreamworks", "unreal", "unity"]


@pytest.mark.parametrize("detail", DETAIL_LEVELS)
def test_n64_reads_as_low_poly_at_every_level(detail):
    s = build_style_string("n64", detail)
    _assert_no_brands(s)
    low = s.lower()
    for must in ("low polygon count", "mitten", "no finger detail", "per-vertex shading",
                 "blurry, low-resolution textures", "distance fog", "skybox",
                 "not a modern animated film", "no subsurface", "no ambient-occlusion",
                 "stitching", "lit by the scene's own light", "large expressive eyes"):
        assert must in low, must
    # the old contradiction and film-style lighting are gone
    for gone in ("gouraud", "smooth colour gradients", "soft rim light", "separate fingers",
                 "edge highlight"):
        assert gone not in low, gone
    assert "low-poly" in PRESETS["n64"].detail[detail].lower() or "low polygon" in (
        PRESETS["n64"].detail[detail].lower())


def test_other_presets_keep_shared_likeness_traits():
    from app.styles.presets import LIKENESS_TRAITS

    for preset in ("ps1", "sprite16"):
        assert build_style_string(preset, 2).endswith(LIKENESS_TRAITS)
        assert PRESETS[preset].likeness == ""


# --- cut-out / sticker look -----------------------------------------------------------
# Cheaper image models draw "rim light" / "edge highlight" as a white outline or halo
# around the figure, so no preset may ask for one (sprite16's dark outlines are fine).
HALO_WORDS = ("rim light", "edge highlight", "halo", "glow")


@pytest.mark.parametrize("preset", list(PRESETS))
def test_no_preset_asks_for_rim_light_or_halo(preset):
    from app.styles.presets import LIKENESS_TRAITS

    p = PRESETS[preset]
    for text in (p.technique, p.likeness or LIKENESS_TRAITS, *p.detail.values()):
        low = text.lower()
        for word in HALO_WORDS:
            assert word not in low, (preset, word)
    likeness = (p.likeness or LIKENESS_TRAITS).lower()
    assert "large expressive eyes" in likeness and "readable face" in likeness
    assert "lit by the scene's own light like everything else" in likeness


def test_sprite_outlines_are_dark_never_light():
    low = PRESETS["sprite16"].technique.lower()
    assert "clean dark outlines" in low and "never white or light-coloured" in low


def test_scene_prompts_carry_the_integration_rule():
    from app.pipeline.prompts import INTEGRATION_RULE, RefSpec

    story = load_story("jack")
    refs = (RefSpec("hero"), RefSpec("photo"), RefSpec("family_sheet", name="Mother"))
    for preset in PRESETS:
        style = build_style_string(preset, 2)
        for page in hw1_pages():
            for r in (None, refs):
                sp = scene_prompt(style, page, story, refs=r)
                assert INTEGRATION_RULE in sp
                # right after the reference list, before the style
                assert sp.index("Reference images:") < sp.index(INTEGRATION_RULE) < (
                    sp.index(style))
        sp = scene_prompt(style, story.page(1), story, refs=refs)
        assert "hero's look only, not a cut-out to paste in" in sp
        assert "Mother must look exactly like" in sp
        assert "their look only, not a cut-out to paste in" in sp
    assert "no white, light or coloured outline, border, halo, glow or sticker edge" in (
        INTEGRATION_RULE)
