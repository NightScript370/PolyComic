"""Pick the story's family variant closest to the child in the photo.

The vision model estimates ``PhotoTraits`` (skin tone 1..6, hair colour, hair texture)
in one call. This module maps each variant's free-text description in the story YAML
(``skin_tone`` / ``hair`` of the first family character, i.e. the parent) onto the same
scale with a fixed keyword table and picks the nearest one. Deterministic and offline,
so it is unit-tested; ties go to the earlier variant, and anything unusable falls back
to the story's first variant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.providers.base import PhotoTraits
from app.stories.loader import FamilyVariant, Story

# Checked in order; first keyword found wins ("warm medium brown" -> medium, not brown).
_TONE_WORDS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("deep", "dark"), 6.0),
    (("medium",), 4.0),
    (("olive", "golden", "tan"), 3.0),
    (("brown",), 5.0),
    (("very fair", "pale"), 1.0),
    (("fair",), 1.5),
    (("light",), 2.0),
)
_COLOUR_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("silver", "grey", "gray", "white"), "grey"),
    (("auburn", "ginger", "red"), "red"),
    (("blonde", "blond", "honey", "golden"), "blonde"),
    (("dark-brown", "dark brown"), "dark_brown"),
    (("light-brown", "light brown", "brown"), "light_brown"),
    (("black",), "black"),
)
_TEXTURES = ("straight", "wavy", "curly", "coily")
# Hair colours that are close to each other cost less to mismatch.
_NEAR_COLOURS = {frozenset(p) for p in (("dark_brown", "black"), ("light_brown", "dark_brown"),
                                        ("blonde", "light_brown"), ("red", "light_brown"))}

TONE_WEIGHT = 1.0
COLOUR_MISMATCH = 0.75
COLOUR_NEAR = 0.35
TEXTURE_STEP = 0.25  # per step on straight -> wavy -> curly -> coily


@dataclass(frozen=True)
class VariantTraits:
    id: str
    skin_tone: float
    hair_colour: str
    hair_texture: str


def _has(text: str, word: str) -> bool:
    """Whole-word match ("red" must not match "gathered"; "brown" matches "dark-brown")."""
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None


def _first(text: str, table) -> object | None:
    low = text.lower()
    for words, value in table:
        if any(_has(low, w) for w in words):
            return value
    return None


def variant_traits(v: FamilyVariant) -> VariantTraits:
    tone = _first(v.skin_tone, _TONE_WORDS)
    colour = _first(v.hair, _COLOUR_WORDS)
    texture = next((t for t in _TEXTURES if _has(v.hair.lower(), t)), "unknown")
    return VariantTraits(v.id, float(tone) if tone is not None else 3.5,
                         str(colour or "unknown"), texture)


def distance(photo: PhotoTraits, v: VariantTraits) -> float:
    d = TONE_WEIGHT * abs(float(photo.skin_tone) - v.skin_tone)
    if "unknown" not in (photo.hair_colour, v.hair_colour) and photo.hair_colour != v.hair_colour:
        near = frozenset((photo.hair_colour, v.hair_colour)) in _NEAR_COLOURS
        d += COLOUR_NEAR if near else COLOUR_MISMATCH
    if photo.hair_texture in _TEXTURES and v.hair_texture in _TEXTURES:
        d += TEXTURE_STEP * abs(_TEXTURES.index(photo.hair_texture)
                                - _TEXTURES.index(v.hair_texture))
    return d


def family_variants(story: Story) -> list[FamilyVariant]:
    """Variants of the first family character that has them, in ``family_variant_ids`` order."""
    for c in story.characters.values():
        if c.family and c.variants:
            return [c.variants[i] for i in story.family_variant_ids if i in c.variants]
    return []


def default_family_variant(story: Story) -> str:
    return story.family_variant_ids[0] if story.family_variant_ids else ""


def choose_family_variant(story: Story, traits: PhotoTraits | None) -> str:
    """Closest variant id for ``traits``; the first variant when there is nothing to match."""
    variants = family_variants(story)
    if traits is None or not variants:
        return default_family_variant(story)
    scored = [(distance(traits, variant_traits(v)), i, v.id) for i, v in enumerate(variants)]
    return min(scored)[2]


# --- plain-words description of the photo traits (prompts, grader, corrections) --------

SKIN_PHRASES = {1: "very fair", 2: "fair", 3: "light olive", 4: "medium tan", 5: "brown",
                6: "deep brown"}


def skin_phrase(traits: PhotoTraits) -> str:
    return SKIN_PHRASES.get(int(traits.skin_tone), "")


def hair_colour_phrase(traits: PhotoTraits) -> str:
    c = traits.hair_colour
    return "" if c in ("", "unknown") else c.replace("_", " ")


def hair_texture_phrase(traits: PhotoTraits) -> str:
    return traits.hair_texture if traits.hair_texture in _TEXTURES else ""


def hair_phrase(traits: PhotoTraits) -> str:
    """'straight black' / 'curly' / 'dark brown' / '' (unknown)."""
    return " ".join(p for p in (hair_texture_phrase(traits), hair_colour_phrase(traits)) if p)


CHILD_AGE = "about 7 to 9 years old"
# Never drawn on the child version, whatever the photo shows.
DROPPED_FEATURES = ("a beard", "a moustache or stubble", "wrinkles or an aged face",
                    "the clothes from the photo (adult clothes such as a suit, jacket, tie "
                    "or blouse)")


def kept_items(traits: PhotoTraits | None) -> list[str]:
    """Personal items from the photo the hero wears on EVERY page: glasses and headwear
    (kippah / skullcap, hijab, turban, cap, headscarf ...). [] when none or unknown."""
    if traits is None:
        return []
    out = []
    for item in (traits.glasses, traits.headwear):
        item = (item or "").strip().rstrip(".")
        if item:
            out.append(item)
    return out


def kept_items_phrase(traits: PhotoTraits | None) -> str:
    """'round black-rimmed glasses and a dark navy skullcap (kippah)' ('' = none)."""
    items = kept_items(traits)
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def child_version_needed(traits: PhotoTraits | None) -> bool:
    """True when the photo shows a teen, an adult or an elderly person."""
    return traits is not None and not traits.is_child


def _hair_words(traits: PhotoTraits) -> str:
    hair = hair_phrase(traits)
    if not hair:
        return ""
    if traits.hair_colour == "grey":
        return f"{hair} hair (grey stays grey, exactly as photographed)"
    return f"{hair} hair"


def identity_line(traits: PhotoTraits | None) -> str:
    """'The child has light olive skin and curly black hair - keep exactly that. ...' for
    the hero prompt (every attempt, the first included); '' when nothing usable is known.
    Curly / coily hair gets an extra rule, because low-detail styles tend to flatten it
    into one smooth swept shape. Hair hidden under headwear in the photo is not asserted
    (only what shows)."""
    if traits is None:
        return ""
    skin = skin_phrase(traits)
    hair = "" if traits.hair_covered else _hair_words(traits)
    bits = ([f"{skin} skin"] if skin else []) + ([hair] if hair else [])
    if not bits:
        return ""
    line = ("The child has " + " and ".join(bits) + " - keep exactly that. Stylise the "
            "shapes, never the skin tone, hair colour or hair type (straight, wavy, curly or "
            "coily).")
    if hair and hair_texture_phrase(traits) in ("curly", "coily"):
        line += (f" Even drawn as one simple solid shape, the hair must clearly read as "
                 f"{hair_texture_phrase(traits)}: a bumpy, clumped silhouette, never smooth, "
                 "sleek or swept.")
    return line


def child_version_block(traits: PhotoTraits | None) -> str:
    """The 'draw a child version' rule for a hero prompt when the photo shows a teen,
    adult or elderly person ('' for a child photo or unknown traits)."""
    if not child_version_needed(traits):
        return ""
    assert traits is not None
    who = {"teen": "a teenager", "adult": "an adult",
           "elderly": "an elderly person"}.get(traits.age_group, "an adult")
    hair = "" if traits.hair_covered else _hair_words(traits)
    same = ["face shape", "eye shape", "nose", "skin tone"]
    if hair:
        same.append(f"hair colour and hair texture ({hair_phrase(traits)}, as photographed)")
    else:
        same.append("hair colour and hair texture as far as they show")
    glasses = traits.glasses.strip()
    if glasses:
        same.append(f"the same {glasses}" if not glasses.lower().startswith(("a ", "the "))
                    else f"the same {glasses.split(' ', 1)[1]}")
    note = " Grey hair stays grey." if traits.hair_colour == "grey" and hair else ""
    return ("CHILD VERSION: the photo shows " + who + ". Draw the hero as a CHILD VERSION "
            f"of this person, {CHILD_AGE}, who clearly looks like them: the same "
            + ", ".join(same) + f".{note} Child proportions: a child's height and a big "
            "head, a round young face with smooth skin. Leave out: "
            + ", ".join(DROPPED_FEATURES) + ". The hero is a child on every picture, "
            "never a grown-up.")


def kept_items_rule(traits: PhotoTraits | None) -> str:
    """'Personal items - always worn on every page: round glasses and a dark skullcap. ...'
    for the hero prompt and every page prompt ('' when the photo shows none)."""
    items = kept_items_phrase(traits)
    if not items:
        return ""
    rule = (f"Personal items - the hero ALWAYS wears {items}, on every picture, exactly as "
            "in the hero reference.")
    if traits is not None and traits.headwear.strip():
        rule += (" A hood of the story outfit always stays DOWN on the shoulders, never "
                 "up over the head, so the headwear always shows; a hat or cap goes over or "
                 "around the headwear and never replaces or hides it.")
    return rule


def identity_block(traits: PhotoTraits | None) -> str:
    """Everything the hero prompt needs from the photo traits: child version (if the
    photo is not a child), identity line, kept personal items. '' when unknown."""
    return "\n\n".join(b for b in (child_version_block(traits), identity_line(traits),
                                    kept_items_rule(traits)) if b)
