"""Prompt builders. ``style`` is always a locked string from
``app.styles.presets.build_style_string`` and is inserted verbatim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.pipeline.family import HeroLook
from app.pipeline.placement import zone_where
from app.stories.loader import Character, Costume, FamilyVariant, Page, Story, get_layout
from app.styles.presets import CONSTRAINTS, outfit_note, style_reminder

DEFAULT_OUTFIT = (
    "Simple, comfortable everyday children's clothes in colours similar to the photo."
)


@dataclass(frozen=True)
class RefSpec:
    """One reference image sent with a page call, in order (image 1, image 2, ...)."""

    role: str  # hero | photo | plate | family_sheet | character | cast | style
    name: str = ""  # character name ("character", "cast"), or who to copy ("family_sheet")
    character_id: str = ""
    costume: str = ""  # "cast" only: the costume shown on the sheet ("" = plain)


# Pages send the hero reference, not the photo: the hero reference is the identity anchor
# (a child version of an adult photo), and the photo pulled pages back to the adult.
DEFAULT_REFS = (RefSpec("hero"),)
# Added right after the reference list of every page prompt: cheaper image models
# composite the plain-background hero / family-sheet figures into the scene as cut-out
# stickers (white outline or halo) unless told to re-draw them.
INTEGRATION_RULE = (
    "Re-draw every character from the reference images as part of this scene - same "
    "lighting, shading and style as the background, standing on the ground with matching "
    "shadows. Never paste them in: no white, light or coloured outline, border, halo, glow "
    "or sticker edge around any figure."
)
# Appended to every layout quiet-zone rule: "plain sky" / "plain background" wording made
# cheaper models leave an unpainted cream strip across the top of the page.
PAINT_EDGE_TO_EDGE = ("Paint that area as part of the scene, right to the edge of the "
                      "picture - never a blank, empty or plain-coloured strip or border.")
# Always in a page prompt: the hero's hair drifted on a "hair blown upward" page (long,
# loose hair on a child with short tight curls) and still passed the grader.
HERO_LOOK_RULE = (
    "The hero's hairstyle, hair length, hair colour and face stay exactly as in the hero "
    "reference on every page - wind, speed or motion never makes the hair longer, looser "
    "or differently styled."
)
# Always in a page prompt: with an adult photo the pages alternated between the adult in
# his own suit and random children (Red), and a Jack page put the hero in the mother's
# blouse and apron. Identity comes from the hero REFERENCE (the child version), never the
# photo; the outfit is always the story's hero outfit.
HERO_CHILD_RULE = (
    "The hero is always the CHILD from the hero reference (about 7 to 9 years old) - never "
    "a grown-up or elderly face, never a beard, moustache or wrinkles - and always wears "
    "the hero outfit: never adult clothes such as a suit, and never another character's "
    "clothes (no dress, blouse, apron, shawl or cloak belonging to someone else)."
)
# A teenage hero (entered age 13-17, or a photo detected as a teen) keeps the teen's own
# face and hair on every page - no child version.
def hero_age_rule(look: HeroLook | None = None) -> str:
    """The per-page rule on the hero's age and clothes: the CHILD rule, or - for a teen
    hero - 'teenager about N' wording."""
    if look is None or not look.teen:
        return HERO_CHILD_RULE
    return (f"The hero is always the TEENAGER from the hero reference (about {look.age} "
            "years old) - a teenager's face and body, never a small child, never a grown-up "
            "or elderly face, never a beard, moustache or wrinkles - with the same face and "
            "the same hair length, hairstyle and hair colour as the hero reference on every "
            "page, and always wears the hero outfit: never adult clothes such as a suit, and "
            "never another character's clothes (no dress, blouse, apron, shawl or cloak "
            "belonging to someone else).")


def hero_items_rule(items: str) -> str:
    """Per-book page rule for the personal items kept from the photo ('' = none):
    'The hero always wears round glasses and a dark skullcap on every page ...'."""
    items = (items or "").strip()
    if not items:
        return ""
    return (f"The hero always wears {items} on every page, exactly as in the hero "
            "reference - never leave them out. A hood always stays DOWN on the shoulders - "
            "never pulled up over the head - so these items stay fully visible; a hat or "
            "cap goes over or around the headwear and never replaces it.")
# On pages with a family member: a family sheet that drew the young mother as an elderly,
# grey-haired woman was copied on some pages while others followed the text.
FAMILY_LOOK_RULE = (
    "Family members match their reference sheet exactly - the same apparent age, hair "
    "colour, hairstyle, face and outfit on every page; never older, greyer or restyled."
)
# Always in a page prompt: the hero was drawn with the reference's calm smile on pages
# where the scene is scary (Red p7 / p11) or a chase (p13).
HERO_EXPRESSION_RULE = (
    "The hero reference fixes WHO the hero is - face shape, hair, skin tone, body "
    "proportions and outfit - NOT the facial expression or the pose. Never copy the "
    "reference's calm face: the hero's expression and body language come from this page's "
    "scene and its 'Hero expression' line."
)
# Always in a page prompt: two baskets (Red p1), a second nightcap in mid-air (p11) and
# two wolves (p13, "tripped and flopped") were drawn.
ONE_EACH_RULE = (
    "Every character appears exactly once in the picture: never draw the same character "
    "twice - no second copy, no before-and-after or motion-sequence duplicates, no "
    "reflection or picture of them. Show one single moment in time. Every single object "
    "(a basket, a whistle, a nightcap, an axe, a broom) appears exactly once."
)
# Always in a page prompt (and the task line names the tall shape first): the stored pages
# of three real books (all 896x1200) showed the model painting a squarer picture inside
# the 3:4 canvas and padding the top / bottom with a blurred copy, a flat wall or an extra
# sky strip behind a straight seam (``app.pipeline.bands.find_seam_band``).
FULL_FRAME_RULE = (
    "Compose the scene for this tall portrait (3:4) frame from the start and fill it edge "
    "to edge with crisp, fully painted scenery in the same style: never paint a wider "
    "picture and then pad, stretch, blur, smear or letterbox the top or bottom to fit - no "
    "blurred, smeared, empty or plain-coloured strip along any edge."
)
# After an attempt came back padded (a seam / blank / blurred band): real runs kept the
# padded picture because the retry padded too (close-ups, wide interiors). The rescue
# names a vertical arrangement explicitly.
VERTICAL_RESCUE_RULE = (
    "VERTICAL COMPOSITION (previous attempts were padded): arrange everything in a tall "
    "column. Put the floor, ground or table top in the bottom third and the ceiling, wall "
    "top, sky or treetops in the top third, both painted with full detail; place the main "
    "subject in the middle and let tall things (doors, cupboards, characters, stalks, "
    "trees) run from the lower part of the frame up toward the top. Show more of what is "
    "above and below the action instead of more of what is to the sides. Do not frame a "
    "wide or square picture."
)
# On pages with another character: Grandma put a finger to her lips (Red p8) and the hero
# copied the gesture although the page gave the hero another action.
NO_MIRROR_RULE = (
    "Every character has their own pose, gesture and expression: the hero child never "
    "copies another character's specific gesture (a finger to the lips, a wave, a pointing "
    "hand, a raised fist, hands on cheeks) unless this page says the hero does it too."
)
# The faces of supporting characters come from the page, not from their reference sheet
# (the wolf's sheet grin was copied onto p13, where he has just tripped).
OTHER_EXPRESSION_RULE = (
    "Every other character's facial expression comes from this page ('Expression on this "
    "page'), never from their reference sheet."
)
HERO_CAPTION = ("The hero child alone, full body from head to feet, front view, neutral "
                "standing pose, on a plain light background, wearing the story outfit. The "
                "hero must be a CHILD of about 7 to 9 years old who looks like the person in "
                "the photo (a child version when the photo shows an adult).")


def hero_caption(look: HeroLook | None = None) -> str:
    """The hero sheet's description for the grader."""
    if look is None or not look.teen:
        return HERO_CAPTION
    return ("The hero teenager alone, full body from head to feet, front view, neutral "
            "standing pose, on a plain light background, wearing the story outfit. The "
            f"hero must be a teenager about {look.age} years old with the same face and "
            "the same hair length, hairstyle and colour as the person in the photo.")


PROPS_HEADING = "Props (draw exactly as described, same on every page):"


def page_props(story: Story, page: Page) -> list[str]:
    """The canonical prop lines this page shows ("beans: exactly five small magic beans
    ..."), from the story-level ``props``, plus the prop's state on this page if the
    story gives one ("On this page: ..."). The image prompt and the grader get the same
    lines, so every page draws the same objects and the grader can check them."""
    out = []
    for pid in page.props:
        if pid not in story.props:
            continue
        line = f"{pid.replace('_', ' ')}: {story.props[pid]}"
        state = story.prop_state(pid, page.page)
        if state:
            line += f". On this page: {state}"
        out.append(line)
    return out


def props_block(story: Story, page: Page) -> str:
    lines = page_props(story, page)
    if not lines:
        return ""
    return PROPS_HEADING + "\n" + "\n".join(f"- {line}" for line in lines)


def costume_line(c: Character, page: int | None) -> str:
    """What ``c`` wears on ``page`` - only for characters with page-dependent costumes:
    'On this page the wolf wears: <costume>' or 'On this page the wolf wears no costume:
    <plain>'. "" for characters without costumes or when no page is given."""
    if not c.costumes or page is None:
        return ""
    cos = c.costume_for(page)
    if cos is not None:
        return f"On this page {c.name} wears: {cos.description}"
    return (f"On this page {c.name} wears no costume: "
            f"{c.plain or 'no clothes, hat or costume of any kind'}.")


def sheet_outfit_line(c: Character, costume: Costume | None) -> str:
    """The outfit line of a supporting character's reference sheet ("" = no costumes)."""
    if not c.costumes:
        return ""
    if costume is not None:
        return f"Outfit on this sheet: {costume.description}"
    return f"Outfit on this sheet: {c.plain or 'no clothes, hat or costume of any kind'}."


def clothing_line(style: str) -> str:
    """The preset's clothing-rendering note (goes right after an outfit line), or ""."""
    note = outfit_note(style)
    return f"Clothing rendering: {note}" if note else ""


def corrections_block(corrections: Sequence[str]) -> str:
    items = [c.strip() for c in corrections if c and c.strip()]
    if not items:
        return ""
    return ("Corrections - a previous attempt at this picture was rejected for these "
            "problems; make sure this one fixes them:\n" + "\n".join(f"- {c}" for c in items))


def hero_prompt(style: str, hero_outfit: str | None = None,
                corrections: Sequence[str] = (), identity: str = "",
                look: HeroLook | None = None) -> str:
    """``identity`` is ``app.pipeline.family.identity_block(traits)`` (child version when
    the photo is not a child, the photo's measured skin tone and hair, the personal items
    kept on every page), or "" when not known yet. The outfit is always the story's hero
    outfit, never the clothes in the photo."""
    outfit = hero_outfit.strip() if hero_outfit else DEFAULT_OUTFIT
    clothing = clothing_line(style)
    fix = corrections_block(corrections)
    teen = look is not None and look.teen
    task_age = (f"The hero is always a TEENAGER of about {look.age} years old who clearly "
                "looks like the person in the photo (same face, same hair length, style "
                "and colour)." if teen else
                "The hero is always a CHILD of about 7 to 9 years old who clearly looks "
                "like the person in the photo.")
    return "\n\n".join(
        [
            "Task: turn the person in the attached photo into a stylised hero character "
            "reference image for a picture book. " + task_age,
            style,
            f"Composition: exactly one character - the {'teenager' if teen else 'child'} - "
            "shown full body from the top of the head to the feet, standing in a relaxed neutral "
            "pose, front view facing the "
            "viewer, arms slightly away from the body, a relaxed, neutral expression with "
            "the mouth closed (each story page sets its own expression), centred with a "
            "small margin around the figure, on a plain, light, evenly lit background with "
            "nothing else in it.",
            f"Outfit (always - it replaces whatever the person wears in the photo): {outfit}",
            *([clothing] if clothing else []),
            "Use the photo only for the person's identity (face shape, eyes, nose, skin tone, "
            "hair, glasses and headwear). Do not copy the photo's clothes, background, "
            "lighting, camera angle or photographic look.",
            *([identity] if identity else []),
            *([fix] if fix else []),
            *([style_reminder(style, like="")] if style_reminder(style) else []),
            CONSTRAINTS,
        ]
    )


def age_phrase(c: Character) -> str:
    """'about 30 years old (a young adult: no grey or white hair, no wrinkles)', or "" when
    the story gives no ``age``."""
    if c.age is None:
        return ""
    if c.elderly:
        return f"about {c.age} years old (elderly)"
    if c.age < 18:
        return f"about {c.age} years old (a child)"
    return f"about {c.age} years old (a young adult: no grey or white hair, no wrinkles)"


def family_expectations(characters: Sequence[Character],
                        family_variant: str | None) -> list[str]:
    """One line per family member with what must be checked on a sheet or page:
    'Mother: about 30 years old (a young adult: ...); skin tone: warm medium brown; hair:
    curly black hair gathered in a loose bun'. Shared by the family sheet prompt, the sheet
    check and the page grader."""
    out = []
    for c in characters:
        variant = c.variants.get(family_variant) if family_variant else None
        bits = [age_phrase(c)]
        if variant is not None:
            bits += [f"skin tone: {variant.skin_tone}" if variant.skin_tone else "",
                     f"hair: {variant.hair}" if variant.hair else ""]
        bits = [b for b in bits if b]
        out.append(f"{c.name}: " + "; ".join(bits) if bits else c.name)
    return out


def family_correction(characters: Sequence[Character], family_variant: str | None) -> str:
    """Concrete correction for a failed ``family_match`` on a page."""
    if not characters:
        return ""
    return ("each family member must look exactly like the family reference sheet - "
            + "; ".join(family_expectations(characters, family_variant)))


def character_line(c: Character, family_variant: str | None = None,
                   page: int | None = None, expression: str = "",
                   size_here: str = "") -> str:
    """One bullet for the cast list. Family members get the book's chosen variant's
    skin tone and hair (overriding any hair detail in the base description); a character
    with an ``age`` gets it spelled out, one with a ``size`` its size relative to the hero
    child, and one with costumes what it wears on ``page`` (or that it wears none)."""
    line = f"- {c.name}: {c.description}"
    if c.size:
        line += f" Size: {c.size}"
    age = age_phrase(c)
    if age:
        line += f" Apparent age: {age}."
    variant = c.variants.get(family_variant) if (c.family and family_variant) else None
    if variant is not None:
        line += (
            f" Same family as the hero child. Skin tone: {variant.skin_tone}. "
            f"Hair: {variant.hair} (this replaces any other hair colour or style above)."
        )
    look = costume_line(c, page)
    if look:
        line += f" {look}"
    if size_here:
        line += f" Size on this page: {size_here.rstrip('.')}."
    if expression:
        line += f" Expression on this page: {expression.rstrip('.')}."
    return line


def cast_expectations(characters: Sequence[Character], page: int | None,
                      sizes: dict[str, str] | None = None) -> list[str]:
    """One line per supporting (non-family) character for the page grader: description,
    size relative to the hero child (and the page's size anchor) and what they wear on
    ``page``. Expressions are checked separately (``cast_expression_lines``)."""
    sizes = sizes or {}
    return [character_line(c, None, page, size_here=sizes.get(c.id, ""))[2:]
            for c in characters if not c.family]


def cast_expression_lines(story: Story, page: Page) -> list[str]:
    """'the wolf: dazed and flustered ...' for every character the page gives a face."""
    return [f"{story.characters[cid].name}: {expr}" for cid, expr in page.expressions.items()
            if cid in story.characters]


def cast_expression_correction(lines: Sequence[str]) -> str:
    """Concrete correction for a failed ``cast_expression_match``."""
    if not lines:
        return ""
    return ("the other characters' faces must show exactly this page's expressions, not "
            "their reference sheet's: " + "; ".join(lines))


def cast_correction(characters: Sequence[Character], page: int | None,
                    sizes: dict[str, str] | None = None) -> str:
    """Concrete correction for a failed ``cast_match``: each supporting character's sheet,
    clothes, size (and this page's size anchor) and outfit for this page."""
    sizes = sizes or {}
    bits = []
    for c in characters:
        if c.family:
            continue
        parts = [f"{c.name} must look exactly like their reference sheet and wear the same "
                 "clothes as on every other page"]
        if sizes.get(c.id):
            parts.append(f"size on this page: {sizes[c.id].rstrip('.')}")
        elif c.size:
            parts.append(f"size: {c.size}")
        look = costume_line(c, page)
        if look:
            parts.append(look)
        bits.append(" - ".join(parts))
    return "; ".join(bits)


def expression_correction(expression: str) -> str:
    """Correction for a failed ``expression_match``."""
    return (f"the hero's expression must clearly be {expression.strip().rstrip('.')} - the "
            "hero reference sets who the hero is, not the expression")


def page_characters(story: Story, page: Page) -> list[Character]:
    return [story.characters[c] for c in page.characters if c in story.characters]


def _ref_line(i: int, r: RefSpec) -> str:
    if r.role == "hero":
        return (f"Image {i} is the stylised hero character reference - the ONLY source of "
                "the hero's identity and age - a reference image for the hero's look only, "
                "not a cut-out to paste in: copy the hero's identity exactly from it - face "
                "shape, age, hair length and style, skin tone, glasses and headwear (if any), body "
                "proportions, outfit and rendering style - but not its expression, its pose "
                "or its plain light background (never a plain light strip or border in the "
                "scene).")
    if r.role == "photo":
        return (f"Image {i} is the original photo of the child - use it only to keep the "
                "likeness (face shape, eye colour, skin tone, hair); do not copy its "
                "background, clothes or photographic look.")
    if r.role == "plate":
        return (f"Image {i} is the background plate for this page: keep its scene, setting, "
                "camera angle, composition, lighting and everything already in it. Insert "
                "the hero child (and every character listed under 'Add to the scene') into "
                "the open space left for them, at a matching scale and lighting. Do not "
                "redraw, crop or move the background.")
    if r.role == "family_sheet":
        return (f"Image {i} is the family reference sheet - {r.name} must look exactly like "
                "the matching figure on it (face, hair, skin tone, body shape, clothes and "
                "colours). It is a reference image for their look only, not a cut-out to "
                "paste in: do not copy its plain background, poses or side-by-side layout.")
    if r.role == "style":
        return (f"Image {i} is a finished, approved page of this same book - the STYLE "
                "ANCHOR. Copy only its rendering technique (the same 3D / pixel / painted "
                "look, textures, shading and lighting, on every character, prop and the "
                "background); never copy its scene, composition, poses or characters.")
    if r.role == "cast":
        outfit = f" ({r.costume})" if r.costume else ""
        return (f"Image {i} is the reference sheet for {r.name}{outfit} - draw {r.name} with "
                "exactly this look (shape, face, colours and clothes) and at the size it shows "
                "next to the plain grey height bar, which marks the hero child's height. It "
                "is a reference for look, clothes and size only: never draw the grey bar, and "
                "do not copy the sheet's plain background, pose or facial expression.")
    return (f"Image {i} is the character reference for {r.name} - draw {r.name} with exactly "
            "this face, hair, skin tone, clothes and colours.")


def fixed_zone_name(page: Page) -> str | None:
    """Zone name ('top' | 'bottom' | 'middle') of the page's FIXED text zone - the layout's
    first allowed zone, the same for every story - or None without a layout."""
    layout = get_layout(page.layout)
    if layout is None or not layout.allowed_text_zones:
        return None
    return str(layout.allowed_text_zones[0].get("zone") or "") or None


def quiet_zone_rule(page: Page, zone: str | None = None, strong: bool = False,
                    item: str | None = None, text_zone: str | None = None) -> str:
    """The layout's quiet-zone instruction; ``strong`` (after a placement failure) adds an
    explicit rule for ``zone``, naming the blocking ``item`` ("the golden harp").
    ``text_zone`` (the page's fixed text zone) is named in every prompt: the text is always
    printed there, so that area must stay free of faces, characters and key objects."""
    layout = get_layout(page.layout)
    base = layout.quiet_zone_instruction if layout else ""
    if base:
        base = f"{base} {PAINT_EDGE_TO_EDGE}"
    if text_zone and not strong:
        where = zone_where(text_zone)
        base = (f"The page text is always printed in the {where} of the picture: keep every "
                f"face, every character and every important object out of the {where}, and "
                f"paint the scene there normally. {base}").strip()
    if not strong:
        return base
    where = zone_where(zone)
    named = f"Keep {item} out of the {where}. " if item and item != "every face" else ""
    return (f"IMPORTANT: the {where} of the picture will be covered by the page text. "
            f"{named}Keep every face, every character and every important object completely "
            f"out of the {where}: keep that area free of faces and important objects, but "
            "paint the scene there normally (sky, grass, wall, floor) - never a blank strip "
            f"or border. {base}").strip()


def scene_prompt(
    style: str,
    page: Page,
    story: Story,
    family_variant: str | None = None,
    *,
    refs: Sequence[RefSpec] | None = None,
    corrections: Sequence[str] = (),
    quiet_zone: str | None = None,
    quiet_item: str | None = None,
    hero_items: str = "",
    vertical_rescue: bool = False,
    hero_look: HeroLook | None = None,
    text_zone: str | None = None,
) -> str:
    """Compose a story-page prompt.

    ``hero_look`` (``app.pipeline.family.resolve_hero_look``) makes the age rule say
    'teenager about N' for a teen hero; ``text_zone`` names the page's fixed text zone in
    the quiet-zone instruction.

    ``hero_items`` names the personal items kept from the photo ("round glasses and a dark
    skullcap", ``app.pipeline.family.kept_items_phrase``); ``vertical_rescue`` (after
    padded / seamed attempts) adds an explicit tall-composition instruction.

    ``refs`` lists the reference images in the order they are sent (default: hero,
    photo). With a ``plate`` ref, characters that are not family are already painted in
    the plate; family members never are (their look depends on the chosen variant), so
    they are listed under "Add to the scene". ``family_variant`` (e.g. ``"fam_b"``) sets
    skin tone and hair for every family character. ``quiet_zone`` (a zone name) switches
    to the stronger quiet-zone rule used after a text-placement failure; ``quiet_item``
    names what blocked it ("the golden harp")."""
    refs = tuple(refs) if refs is not None else DEFAULT_REFS
    has_plate = any(r.role == "plate" for r in refs)
    hero_instruction = page.hero_instruction.replace("hero_outfit", "the hero outfit")
    hero_outfit = story.hero_outfit_for(page.page)

    def line(c: Character) -> str:
        return character_line(c, family_variant, page.page, page.expressions.get(c.id, ""),
                              page.sizes.get(c.id, ""))

    parts = [
        "Task: illustrate one picture-book page starring the hero child, as ONE tall "
        "portrait picture (3 wide by 4 high) composed for that shape and filling the whole "
        "frame from the top edge to the bottom edge.",
        "Reference images: " + " ".join(_ref_line(i, r) for i, r in enumerate(refs, 1)),
        INTEGRATION_RULE,
        *([style_reminder(style)] if style_reminder(style) else []),
        HERO_LOOK_RULE,
        hero_age_rule(hero_look),
        *([hero_items_rule(hero_items)] if hero_items_rule(hero_items) else []),
        HERO_EXPRESSION_RULE,
        ONE_EACH_RULE,
    ]
    others = page_characters(story, page)
    if any(c.family for c in others):
        parts.append(FAMILY_LOOK_RULE)
    if others:
        parts.append(NO_MIRROR_RULE)
    if page.expressions:
        parts.append(OTHER_EXPRESSION_RULE)
    parts += [
        style,
        f"Scene: {page.scene}",
    ]
    if hero_instruction:
        parts.append(f"Hero: {hero_instruction}")
    if page.expression:
        parts.append(f"Hero expression: {page.expression}")
    if hero_outfit:
        parts.append(f"Hero outfit: {hero_outfit}")
    clothing = clothing_line(style)
    if clothing:
        parts.append(clothing)
    if has_plate:
        in_plate = [c for c in others if not c.family]
        to_add = [c for c in others if c.family]
        if in_plate:
            parts.append("Already in the background plate (keep them exactly as drawn): "
                         + ", ".join(c.name for c in in_plate) + ".")
        if to_add:
            parts.append("Add to the scene (render in the same art style):\n"
                         + "\n".join(line(c) for c in to_add))
        expr = [c for c in in_plate if c.id in page.expressions]
        if expr:
            parts.append("Expressions on this page: " + "; ".join(
                f"{c.name}: {page.expressions[c.id].rstrip('.')}" for c in expr) + ".")
    elif others:
        parts.append(
            "Other characters (render them in the same art style):\n"
            + "\n".join(line(c) for c in others)
        )
    props = props_block(story, page)
    if props:
        parts.append(props)
    if page.must_be_visible:
        parts.append("Clearly visible in the picture: " + "; ".join(page.must_be_visible) + ".")
    if page.palette_note:
        parts.append(f"Lighting and palette: {page.palette_note}")
    rule = quiet_zone_rule(page, quiet_zone, strong=quiet_zone is not None, item=quiet_item,
                           text_zone=text_zone)
    if rule:
        parts.append(f"Composition: {rule}")
    parts.append(FULL_FRAME_RULE)
    if vertical_rescue:
        parts.append(VERTICAL_RESCUE_RULE)
    fix = corrections_block(corrections)
    if fix:
        parts.append(fix)
    reminder = style_reminder(style)
    if reminder:
        parts.append(reminder)
    parts.append(CONSTRAINTS)
    return "\n\n".join(parts)


# --- pre-generated assets (scripts/pregenerate_assets.py) ----------------------------


def bible_prompt(style: str, c: Character, variant: FamilyVariant | None = None) -> str:
    """Character reference sheet: the character alone, whole body, plain background."""
    desc = c.description
    clothing = clothing_line(style)
    if age_phrase(c):
        desc += f" Apparent age: {age_phrase(c)}."
    if variant is not None:
        desc += (f" Skin tone: {variant.skin_tone}. Hair: {variant.hair} (this replaces any "
                 "other hair colour or style above).")
    return "\n\n".join([
        f"Task: draw a character reference sheet for {c.name}, a character in a "
        "children's picture book.",
        style,
        f"Character: {desc}",
        *([clothing] if clothing else []),
        "Composition: exactly one character - this one - shown whole, from the top of the "
        "head (or ears) to the feet, in a relaxed neutral pose, front three-quarter view, "
        "friendly expression, centred with a small margin, on a plain, light, evenly lit "
        "background with nothing else in it.",
        CONSTRAINTS,
    ])


HEIGHT_BAR = (
    "Beside the character, standing on the same ground line, draw ONE plain vertical grey "
    "bar with square ends as a height scale: the bar is exactly as tall as the hero child "
    "(a child of about six). It is a plain grey rectangle - no marks, numbers, labels, face "
    "or figure."
)


# Added to a supporting-character sheet prompt on the retry after a safety refusal
# (the plain wolf sheet came back with finish_reason=PROHIBITED_CONTENT).
GENTLE_SHEET_RULE = (
    "Keep it gentle and friendly for very young children: a soft, rounded, cuddly cartoon "
    "storybook character with a kind, silly face, mouth closed in a small smile, no teeth, "
    "claws or anything sharp or scary shown."
)


def cast_sheet_prompt(style: str, c: Character, costume: Costume | None = None,
                      corrections: Sequence[str] = (), gentle: bool = False) -> str:
    """Reference sheet for a supporting (non-family) character, per story x style x
    costume: the character alone, whole body, plain background, next to a plain grey
    height bar that marks the hero child's height (no child is drawn - a child figure
    could be mistaken for the hero). Pages get it as a reference for look AND size.
    ``c.sheet_description`` (if given) replaces the description; ``gentle`` (the retry
    after a safety refusal) adds ``GENTLE_SHEET_RULE``."""
    desc = c.sheet_description or c.description
    if age_phrase(c):
        desc += f" Apparent age: {age_phrase(c)}."
    outfit = sheet_outfit_line(c, costume)
    clothing = clothing_line(style)
    size = c.size or "as described above"
    fix = corrections_block(corrections)
    return "\n\n".join([
        f"Task: draw a character reference sheet for {c.name}, a supporting character in a "
        "children's picture book.",
        style,
        f"Character: {desc}",
        *([outfit] if outfit else []),
        *([clothing] if clothing else []),
        f"Size relative to the hero child: {size}",
        f"Composition: exactly one character - {c.name} - shown whole, from the top of the "
        "head (or ears) to the feet (and tail), standing in a relaxed neutral pose, front "
        "three-quarter view, centred, on a plain, light, evenly lit background with nothing "
        f"else in it. {HEIGHT_BAR} Draw {c.name} at the right size next to the bar. No "
        "child, no other people or animals, no scenery.",
        *([GENTLE_SHEET_RULE] if gentle else []),
        *([fix] if fix else []),
        CONSTRAINTS,
    ])


def _join(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def age_rule(characters: Sequence[Character]) -> str:
    """'Mother must look young (Mother about 30): no grey, silver or white hair, no
    wrinkles, no stooped or elderly posture. Only Grandma is elderly.' - from the ``age``
    data, so no character name is hard-coded. "" when no adult under ELDERLY_AGE."""
    young = [c for c in characters if c.age is not None and not c.elderly and c.age >= 18]
    old = [c for c in characters if c.elderly]
    parts = []
    if young:
        ages = ", ".join(f"{c.name} about {c.age}" for c in young)
        parts.append(f"{_join([c.name for c in young])} must look young ({ages}): no grey, "
                     "silver or white hair, no wrinkles, no stooped or elderly posture.")
        if old:
            parts.append(f"Only {_join([c.name for c in old])} "
                         f"{'is' if len(old) == 1 else 'are'} elderly.")
    return " ".join(parts)


def family_sheet_prompt(style: str, characters: Sequence[Character],
                        variant: FamilyVariant | str | None,
                        corrections: Sequence[str] = ()) -> str:
    """One per-book reference sheet with every family member of the story side by side in
    the book's family variant (no hero). Each figure's apparent age and variant hair are
    spelled out, and characters who are not elderly are forbidden grey hair and wrinkles.
    ``corrections`` are the sheet check's notes when the first sheet failed it."""
    variant_id = variant.id if isinstance(variant, FamilyVariant) else variant
    names = [c.name for c in characters]
    clothing = clothing_line(style)
    who = _join(names)
    expect = family_expectations(characters, variant_id)
    ages = age_rule(characters)
    fix = corrections_block(corrections)
    count = ("exactly one character" if len(names) == 1
             else f"exactly {len(names)} characters, side by side, not overlapping")
    return "\n\n".join([
        f"Task: draw a character reference sheet for the family in a children's picture "
        f"book: {who}.",
        style,
        "Characters:\n" + "\n".join(character_line(c, variant_id) for c in characters),
        "Apparent age and hair (most important - get these exactly right for every "
        "figure):\n" + "\n".join(f"- {e}" for e in expect),
        *([ages] if ages else []),
        *([clothing] if clothing else []),
        f"Composition: {count}, each shown whole from the top of the head to the feet, "
        "standing in a relaxed neutral pose, front three-quarter view, friendly expression, "
        "on a plain, light, evenly lit background with nothing else in it. No child, no "
        "other people, no scenery.",
        *([fix] if fix else []),
        CONSTRAINTS,
    ])


def plate_prompt(style: str, page: Page, story: Story,
                 refs: Sequence[RefSpec] = ()) -> str:
    """Background plate for one page: the scene WITHOUT the hero and WITHOUT family members
    (their look depends on the book's family variant, so pages insert them from the bible).
    Supporting characters that are not family ARE painted in, from their bible refs."""
    others = page_characters(story, page)
    leave_out = ["the hero child"] + [c.name for c in others if c.family]
    keep = [c for c in others if not c.family]
    parts = [
        "Task: paint the background plate for one picture-book page. A plate is the "
        "finished scene with some characters left out; they are painted in later.",
    ]
    if refs:
        parts.append("Reference images: " + " ".join(_ref_line(i, r)
                                                      for i, r in enumerate(refs, 1)))
    parts += [
        style,
        f"Scene: {page.plate_prompt or page.scene}",
        "Leave out completely: " + ", ".join(leave_out) + ". Every empty space the scene "
        "asks for stays open and empty at the right scale - no figure, no silhouette, no "
        "shadow of a person, no clothes or props held in mid-air.",
    ]
    if keep:
        parts.append("Characters to include (render them in the same art style):\n"
                     + "\n".join(character_line(c, None, page.page) for c in keep))
        clothing = clothing_line(style)
        if clothing:
            parts.append(clothing)
    props = props_block(story, page)
    if props:
        parts.append(props)
    if page.palette_note:
        parts.append(f"Lighting and palette: {page.palette_note}")
    rule = quiet_zone_rule(page)
    if rule:
        parts.append(f"Composition: {rule}")
    parts.append(CONSTRAINTS)
    return "\n\n".join(parts)
