"""Locked art-style strings - the prompt-engineering heart of PolyComic.

Rules:
* A style string is built only from fixed constants, so it is byte-identical on
  every call for a given (preset, detail). Never format user data into it.
* Describe TECHNIQUE only. Never name a game, console, company or character in
  prompt text. Preset ids (``n64``, ``ps1``, ``sprite16``) are internal keys and
  must never appear in the text sent to a model (unit-tested).
"""

from __future__ import annotations

from dataclasses import dataclass

DETAIL_LEVELS = (1, 2, 3)
DEFAULT_DETAIL = 2


@dataclass(frozen=True)
class StylePreset:
    id: str
    label: str  # shown in the UI
    description: str  # one-line UI blurb
    technique: str  # the locked core of the style string
    detail: dict[int, str]  # detail-level clause, keyed 1..3
    likeness: str = ""  # preset-specific likeness traits ("" = the shared LIKENESS_TRAITS)
    # How clothing is rendered in this technique; appended after the outfit line of the
    # hero, family sheet and page prompts ("" = no note). Story outfits ask for patches
    # and seams that a low-detail technique must not draw as stitched fabric.
    outfit_note: str = ""
    # The key technique words, repeated near the END of page / hero / portrait prompts
    # (``style_reminder``) and named in a style correction (``style_correction``): two Jack
    # pages in a 3D preset came back as flat 2D paintings when the style string sat only in
    # the middle of a long prompt.
    key_technique: str = ""
    # What this style is clearly NOT (a different rendering, e.g. a flat 2D painting).
    not_style: str = ""


# Shared likeness traits blended into every preset.
LIKENESS_TRAITS = (
    "Character design: the child becomes a charming hero character with large expressive "
    "eyes and a rounded, clearly readable face, lit by the scene's own light like "
    "everything else. Keep the child's recognisable facial features: face shape, eye "
    "shape and colour, skin tone, hair colour and style, hairline, and freckles or glasses "
    "if present. The face must stay recognisable as the same child in every picture."
)

# Global negative/constraints block appended to every image call.
CONSTRAINTS = (
    "Strict rules: no text, letters, numbers, words, signage, labels, speech bubbles, "
    "symbols, sound-effect shapes, watermarks, logos, frames or user-interface elements "
    "anywhere in the picture. One single coherent full-bleed image - not a collage, grid, "
    "split screen or comic panels. Child-appropriate picture-book content for ages 4 to 8, "
    "gentle in tone: no gore, blood, injuries, violence or horror imagery (no menacing "
    "monsters, bared dripping fangs or horror lighting). Characters may still show ordinary "
    "feelings in a child-friendly way - a child can look clearly scared, startled or "
    "worried when the scene calls for it."
)

PRESETS: dict[str, StylePreset] = {
    "n64": StylePreset(
        id="n64",
        label="Polygon Adventure",
        description=(
            "Chunky late-90s 3D: faceted shapes, soft blurry textures, bright primary colours."
        ),
        technique=(
            "Art style: a screenshot from a late-1990s home console 3D game, rendered in real "
            "time on very limited hardware - not a modern animated film and not a modern game. "
            "Low polygon count everywhere, characters included: each character is a few hundred "
            "polygons at most, with a blocky, faceted head whose flat facets are clearly "
            "visible, arms and legs made of simple cylinders and boxes, and simple mitten-like "
            "hands with no finger detail. Surfaces are large flat-coloured polygons with simple "
            "per-vertex shading: each facet is one colour or a soft blend between its corners, "
            "with hard creases where facets meet. No photorealistic lighting, no subsurface "
            "skin scattering, no ambient-occlusion softness, no depth of field, no film "
            "grain. Clothes and hair are solid shapes painted with small, blurry, "
            "low-resolution textures stretched over big surfaces - no fabric weave, stitching, "
            "seams, wrinkles, individual hair strands or skin pores. Distance fog fades far "
            "objects into a pale sky colour. A bold, limited palette of saturated primary "
            "reds, blues, yellows and greens. Backgrounds are a simple painted skybox behind "
            "plain, chunky, gently rolling geometry."
        ),
        detail={
            1: (
                "Detail level: very low polygon count - huge flat facets everywhere, a head of "
                "only a few dozen faces, block-like body parts, mitten hands, tiny smeared "
                "textures, very few colours."
            ),
            2: (
                "Detail level: classic low polygon count - clearly visible facets on the head, "
                "body and limbs, rounded but faceted silhouettes, mitten hands, small soft "
                "textures."
            ),
            3: (
                "Detail level: the most detailed low-poly - somewhat more facets for rounder "
                "silhouettes and slightly sharper textures, but still clearly low-poly: facet "
                "edges visible on the head and limbs, mitten hands, no fine surface detail."
            ),
        },
        likeness=(
            "Character design: the child becomes a charming low-poly hero character with large "
            "expressive eyes painted onto the face texture and a rounded, clearly readable "
            "face built from flat facets, lit by the scene's own light like everything else. "
            "Keep the child's recognisable features in this simple form: face shape, eye "
            "shape and colour, skin tone, hair colour and style, hairline, and freckles or "
            "glasses if present. The face must stay recognisable as the same child in every "
            "picture."
        ),
        outfit_note=(
            "Render clothing as simple solid shapes; any patches are flat colour shapes "
            "painted on, with no stitching, weave or texture detail."
        ),
        key_technique=(
            "a real-time low-polygon 3D render: chunky faceted 3D models with visible flat "
            "facets, simple per-vertex shading, blurry low-resolution textures and distance "
            "fog"
        ),
        not_style=(
            "never a flat 2D illustration, painting, drawing or cartoon, and never a smooth "
            "modern animated-film 3D look"
        ),
    ),
    "ps1": StylePreset(
        id="ps1",
        label="Wobbly Low-Poly",
        description="Moody late-90s 3D: pixelated textures, dithering, hard-edged polygons.",
        technique=(
            "Art style: a late-1990s real-time 3D render with no texture filtering. Low-polygon "
            "models with hard-edged, angular polygon silhouettes. Textures are pixelated with "
            "nearest-neighbour sampling, and they warp and swim slightly across large surfaces "
            "from affine texture mapping. Slight vertex jitter, so straight edges are a little "
            "wobbly and not perfectly aligned. Visible ordered dithering in colour gradients and "
            "shadows from a reduced colour depth. A slightly darker, moodier palette with muted, "
            "earthy colours and deep shadows, lit by simple vertex lighting."
        ),
        detail={
            1: (
                "Detail level: very low polygon count - blocky, angular shapes, very coarse "
                "pixelated textures with large texels, strong dithering."
            ),
            2: (
                "Detail level: classic low polygon count - angular but readable shapes, coarse "
                "pixelated textures, visible dithering."
            ),
            3: (
                "Detail level: refined low-poly - more polygons and finer textures with subtler "
                "dithering, while keeping the pixelated, slightly wobbly look."
            ),
        },
        outfit_note=(
            "Render clothing as simple angular shapes with coarse pixelated textures; any "
            "patches are blocky flat-colour areas in the texture, with no fine stitching, "
            "seams or weave."
        ),
        key_technique=(
            "a real-time low-polygon 3D render: hard-edged angular 3D models, pixelated "
            "nearest-neighbour textures, ordered dithering and simple vertex lighting"
        ),
        not_style=(
            "never a flat 2D illustration, painting, drawing or cartoon, and never a smooth "
            "high-polygon 3D look"
        ),
    ),
    "sprite16": StylePreset(
        id="sprite16",
        label="16-bit Sprite",
        description=(
            "Modern retro pixel art: crisp hand-pixelled sprites, bold outlines, bright gradients."
        ),
        technique=(
            "Art style: modern retro 16-bit sprite pixel art. Crisp, hand-placed pixels on a "
            "strict pixel grid, every pixel square and sharp, with no anti-aliasing, blur or "
            "smoothing. Each character sprite uses a limited palette with clean dark outlines "
            "(always dark, never white or light-coloured) and careful pixel shading. Bright, "
            "saturated colours with banded pixel gradients. Backgrounds are built from "
            "flat parallax layers - near, middle and far - like a side-scrolling game scene, "
            "with tiled pixel patterns for ground and foliage."
        ),
        detail={
            1: (
                "Detail level: big chunky pixels - the hero is only about 32 pixels tall before "
                "scaling up, very few colours per sprite, bold simple shapes."
            ),
            2: (
                "Detail level: classic 16-bit resolution - the hero is about 64 pixels tall before "
                "scaling up, around 16 colours per sprite."
            ),
            3: (
                "Detail level: refined pixel art - the hero is about 128 pixels tall before "
                "scaling up, more shading steps and finer detail, every pixel still crisp and "
                "visible."
            ),
        },
        outfit_note=(
            "Render clothing with a few flat pixel colours and clean dark outlines; any "
            "patches are small blocks of one colour, with no stitching or weave detail."
        ),
        key_technique=(
            "crisp 16-bit pixel art on a strict pixel grid: square, sharp pixels with clean "
            "dark outlines, banded pixel shading and flat parallax background layers"
        ),
        not_style=(
            "never a smooth painting, a soft illustration or a 3D render, and never blurred "
            "or anti-aliased"
        ),
    ),
}


def get_preset(preset: str) -> StylePreset:
    try:
        return PRESETS[preset]
    except KeyError:
        raise ValueError(f"unknown style preset: {preset!r}") from None


def build_style_string(preset: str, detail: int = DEFAULT_DETAIL) -> str:
    """The locked style string for (preset, detail). Identical bytes on every call."""
    p = get_preset(preset)
    if detail not in DETAIL_LEVELS:
        raise ValueError(f"detail must be one of {DETAIL_LEVELS}, got {detail!r}")
    return " ".join((p.technique, p.detail[detail], p.likeness or LIKENESS_TRAITS))


def outfit_note(style: str) -> str:
    """The clothing-rendering note of the preset whose locked style string ``style`` is
    ("" if none or not a preset style). Prompt builders only get the style string, so the
    preset is recognised by its technique, which every style string starts with."""
    for p in PRESETS.values():
        if style.startswith(p.technique):
            return p.outfit_note
    return ""


def _preset_of(style: str) -> StylePreset | None:
    for p in PRESETS.values():
        if style.startswith(p.technique):
            return p
    return None


def style_reminder(style: str, like: str = "the hero reference image") -> str:
    """The closing style line of a prompt ("" if ``style`` is not a preset style):
    'Rendering style (most important, for the WHOLE picture - characters, props and
    background): <key technique>, exactly like the hero reference image - <not style>.'"""
    p = _preset_of(style)
    if p is None or not p.key_technique:
        return ""
    same = f", exactly like {like}" if like else ""
    return ("Rendering style (most important - the WHOLE picture, every character, prop and "
            f"background, in one consistent technique): {p.key_technique}{same}; "
            f"{p.not_style}.")


def style_summary(style: str) -> str:
    """The key technique words of the preset (the style string itself when unknown)."""
    p = _preset_of(style)
    return p.key_technique if p is not None and p.key_technique else style


def style_correction(style: str) -> str:
    """Concrete correction for a failed ``style_match``, naming the technique."""
    p = _preset_of(style)
    if p is None or not p.key_technique:
        return ("render the whole picture in exactly the same art style as the hero "
                "reference image")
    return (f"render the WHOLE picture as {p.key_technique}, exactly like the hero reference "
            f"image - {p.not_style}")


def styles_for_ui() -> list[dict]:
    return [
        {"id": p.id, "label": p.label, "description": p.description,
         "detail_levels": list(DETAIL_LEVELS)}
        for p in PRESETS.values()
    ]
