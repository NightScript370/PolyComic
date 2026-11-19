"""Gemini vision grader (GEMINI_VISION_MODEL) - structured JSON output, one call each.

* ``grade``: [generated image, hero reference (pages only), original photo, family
  reference sheet (pages with a family member only)] + caption + must-be-visible list +
  the page's props (+ the photo's measured traits, if known, + what each family member
  on the page must look like) -> likeness, fidelity, identity checks vs. the ORIGINAL
  PHOTO (skin tone, hair colour, hair type), the hero's hair vs. the HERO REFERENCE
  (length, shape, style, colour: ``hero_hair_match``), the family members vs. the family
  sheet (apparent age, hair colour, hairstyle: ``family_match``), text artifacts,
  contradictions (objective, checkable errors only, which fail the picture), softer
  issues and boxes for faces, character bodies (``figure``) and discrete key objects.
  At most 4 images, each downscaled (see below).
  Pages also get the supporting characters' reference sheets (``cast_refs``) and one
  line per supporting character (``cast``: description, size relative to the hero child,
  outfit on this page) -> ``cast_match``, and the hero's requested emotion
  (``expression``) -> ``expression_match``. Both fail the picture when false. Pages and
  the hero also get the book's key style words (``style``) -> ``style_match`` (rendering
  vs. the hero reference: a flat 2D painting in a 3D book is a definite error), and pages
  the other characters' faces (``cast_expressions``) -> ``cast_expression_match``. The
  hero sheet (``HERO_SHEET_RULES``) never fails on outfit trivia (patch side, bag side).
  Images go at a per-part media_resolution (``media_level_first`` for image 1,
  ``media_level`` for the rest; dropped once per process if the model rejects it), and an
  answer that is not valid JSON is asked again once (``JSON_TRIES``).
* ``check_family_sheet``: [family sheet] + one expectation line per family member ->
  count, no child, apparent age, hair (one call, used by ``app.pipeline.family_sheet``).
* ``check_cast_sheet``: [supporting character sheet] + name, description, size -> one
  figure, right character / outfit, height bar and size (``app.pipeline.cast_sheets``).
* ``check_portrait``: [speaker portrait] + name, description, expression -> right
  character, one head-and-shoulders figure, expression (``app.pipeline.portraits``).
* Speaker-portrait gate: pages that may show a portrait pass ``presence`` (character
  names) and get ``visible_characters`` - every one of them seen in the picture in any
  form (partly hidden, in disguise, only ears or eyes); unknown names are dropped.
* ``photo_traits``: [photo] -> skin tone (1..6), hair colour/texture, apparent age group,
  glasses, headwear, facial hair; used to pick the closest family variant and to draw the
  hero as a CHILD VERSION of the person with their glasses / headwear
  (``app.pipeline.family``).
* Hero look checks (``grade``, hero and pages): ``hero_child_ok`` (a child, no beard or
  adult face), ``hero_items_ok`` (the kept glasses / headwear, ``hero_items``) and
  ``hero_outfit_ok`` (the story's hero outfit, ``hero_outfit``; never the photo's adult
  clothes or another character's clothes). Likeness on pages is judged against the HERO
  REFERENCE; the photo is kept for skin tone and hair colour / type.

Boxes are requested in Gemini's native detection format (``box_2d`` = [ymin, xmin, ymax,
xmax] on a 0..1000 grid, which the model is trained on) and converted here to normalised
x/y/w/h with origin top-left. No retries (``HttpRetryOptions(attempts=1)``); a failed call
raises ``ProviderError`` and the caller decides what to do.

Latency: the thinking level is ``GRADER_THINKING_LEVEL`` (google-genai 3.0's
``ThinkingLevel`` has MINIMAL, LOW, MEDIUM, HIGH; default "low", because gemini-3.8-flash
rejects "minimal"). The SDK does
not say which levels a model accepts; if the model rejects the level with a 400 that names
thinking, the call is repeated once at LOW and LOW is kept for this model for the rest of
the process (``_REJECTED_LEVELS``). Images are
sent downscaled to ``GRADER_IMAGE_MAX_SIDE`` px (JPEG q85) when larger - grading does not
need full resolution, and smaller inputs upload and tokenise faster. Boxes are
normalised, so downscaling does not change them.
"""

from __future__ import annotations

import io
import logging
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

from app.providers.base import (
    AGE_GROUPS,
    Box,
    CallMeta,
    CastSheetCheck,
    GradeResult,
    PhotoTraits,
    PortraitCheck,
    ProviderError,
    SheetCheck,
    provider_error_from_google,
    sniff_mime,
)
from app.providers.pricing import estimate_cost

log = logging.getLogger("app.providers.gemini_vision")

_TIMEOUT_MS = 60_000
GRADER_MAX_SIDE = 768
GRADER_JPEG_QUALITY = 85
_LEVELS = {"minimal": types.ThinkingLevel.MINIMAL, "low": types.ThinkingLevel.LOW,
           "medium": types.ThinkingLevel.MEDIUM, "high": types.ThinkingLevel.HIGH}

HAIR_COLOURS = ("blonde", "red", "light_brown", "dark_brown", "black", "grey", "unknown")
HAIR_TEXTURES = ("straight", "wavy", "curly", "coily", "unknown")


class _Box(BaseModel):
    label: str = Field(description="what this is, e.g. 'hero face', 'mother face', 'golden egg'")
    kind: str = Field(description="'face' for any face, 'figure' for a character's whole "
                                  "body, 'key_object' for anything else")
    box_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax], each 0..1000")


class _Grade(BaseModel):
    likeness: float = Field(description="0..1: is the hero recognisably the child?")
    fidelity: float = Field(description="0..1: does the picture match the description?")
    has_text_artifacts: bool = Field(
        description="true if any letters, words, numbers, signs, speech bubbles or "
                    "watermarks are drawn")
    skin_tone_match: bool = Field(
        description="true if the hero's skin tone matches the child in the ORIGINAL PHOTO")
    hair_colour_match: bool = Field(
        description="true if the hero's hair colour matches the child in the ORIGINAL PHOTO")
    hair_texture_match: bool = Field(
        description="true if the hero's hair type category (straight / wavy / curly / coily) "
                    "matches the child in the ORIGINAL PHOTO; short tight curls count as "
                    "curly, and a smooth swept shape on a curly-haired child is false")
    identity_notes: str = Field(
        description="one short sentence on any identity mismatch, e.g. 'hero has curly "
                    "brown hair, photo shows straight black hair'; empty if all match")
    hero_hair_match: bool = Field(
        description="true if the hero's hair in image 1 has the same length, overall shape "
                    "and silhouette, hairstyle and colour as in the HERO REFERENCE image; a "
                    "little movement from wind or a jump is fine, clearly longer, loose or "
                    "flowing hair on a short-haired hero, or a different style, is false; "
                    "true if there is no hero reference or the hair is not visible")
    family_match: bool = Field(
        description="true if every family member in image 1 matches the FAMILY REFERENCE "
                    "SHEET and the family description in apparent age, hair colour and "
                    "hairstyle; true if no family member is in image 1 or no family sheet "
                    "is given")
    family_notes: str = Field(
        description="one short sentence on any family mismatch, e.g. 'Mother has grey hair "
                    "and wrinkles; the sheet shows a young mother with black curls'; empty "
                    "if all match")
    cast_match: bool = Field(
        description="true if every listed supporting character in image 1 matches its "
                    "reference sheet (if given) and description: look, the outfit or "
                    "costume for this page, and size relative to the hero child; true if "
                    "no supporting character is listed")
    cast_notes: str = Field(
        description="one short, concrete correction per mismatching supporting character, "
                    "e.g. 'the wolf must be about 1.5x the child's height; now about 3x'; "
                    "empty if all match")
    expression_match: bool = Field(
        description="true if the hero's face and body language clearly show the requested "
                    "expression; true if none is requested or the face is not visible")
    expression_notes: str = Field(
        description="one short sentence on a wrong expression, e.g. 'hero smiles calmly; "
                    "should look scared: wide eyes, open mouth'; empty if it matches")
    cast_expression_match: bool = Field(
        default=True,
        description="true if every character listed under 'Other characters' expressions' "
                    "shows that expression; true if none is listed or their face is not "
                    "visible")
    cast_expression_notes: str = Field(
        default="",
        description="one short correction per character with a clearly wrong face, e.g. "
                    "'the wolf grins; he must look dazed: eyes spinning, mouth open in an "
                    "oof'; empty if all match")
    style_match: bool = Field(
        default=True,
        description="true if image 1 is rendered in the same art style / rendering "
                    "technique as the hero reference and the style description (e.g. both "
                    "low-poly 3D); false for a clearly different rendering such as a flat 2D "
                    "painting or drawing in a 3D book")
    style_notes: str = Field(
        default="",
        description="one short sentence on a style mismatch, e.g. 'flat 2D painted "
                    "illustration; the book is a low-poly 3D render'; empty if it matches")
    hero_child_ok: bool = Field(
        description="true if the hero in image 1 is clearly a child of about 6-10: a "
                    "child's face and body, no beard, moustache, stubble or wrinkles, never a "
                    "teen, grown-up or elderly person; true if the hero is not visible")
    hero_items_ok: bool = Field(
        description="true if the hero wears every listed personal item (glasses, headwear); "
                    "false if a hood pulled up over the head hides the listed headwear or "
                    "glasses; true if none is listed or the hero's head is not visible")
    hero_outfit_ok: bool = Field(
        description="true if the hero wears the hero outfit described below - never adult "
                    "clothes from the photo (suit, jacket, tie) and never another "
                    "character's clothes (e.g. the mother's dress, blouse or apron); true if "
                    "no hero outfit is given or the hero's body is not visible")
    hero_look_notes: str = Field(
        description="one short sentence per failed hero look check, e.g. 'the hero is an "
                    "old man with a beard and a suit'; empty if all pass")
    contradictions: list[str] = Field(
        description="objective, checkable errors only: a wrong count, a wrong colour of "
                    "something whose colour is specified, a missing must-be-visible item or "
                    "required character, the wrong or an extra character, a cut-out/sticker "
                    "look (outline, border, halo or glow around a figure not from the "
                    "scene's lighting, or a figure visibly pasted in), a blank, unpainted, "
                    "blurred-out, smeared, padded or letterboxed band or border along an "
                    "edge, the same character drawn twice or a single object drawn twice, "
                    "inset panels / comic borders / split panels / several images, text, a "
                    "main character whose face is cut off by the frame edge, or a fleeing "
                    "character facing / heading toward what they flee or drawn close up when "
                    "the description says far away; never other framing or cropping, pose, "
                    "grip, lighting, motion or a size that is off by less than about 2x; "
                    "empty if none")
    issues: list[str] = Field(description="softer, concrete improvement notes; empty if none")
    boxes: list[_Box]
    visible_characters: list[str] = Field(
        default_factory=list,
        description="names from the 'Characters to report' list that appear in image 1 in "
                    "ANY form (even partly, hidden under covers, in disguise, only ears, "
                    "eyes, a tail or a boot); empty if none or no list is given")


class _SheetCheck(BaseModel):
    count_ok: bool = Field(description="exactly the expected number of figures")
    no_child: bool = Field(description="true if no child is drawn")
    age_ok: bool = Field(description="every figure looks its expected apparent age")
    hair_ok: bool = Field(description="every figure's hair colour and style match")
    issues: list[str] = Field(description="concrete fixes, naming the character; empty if "
                                          "none")


class _CastSheetCheck(BaseModel):
    figure_ok: bool = Field(description="exactly one figure (the grey bar is not a figure), "
                                        "no other people or animals, no scenery")
    looks_ok: bool = Field(description="clearly this character and matching the description "
                                       "and outfit")
    size_ok: bool = Field(description="a plain grey height bar is drawn and the character is "
                                      "about the stated size next to it")
    issues: list[str] = Field(description="concrete fixes; empty if none")


class _PortraitCheck(BaseModel):
    character_ok: bool = Field(description="clearly this character, matching the "
                                           "description and costume")
    single_ok: bool = Field(description="exactly one head-and-shoulders figure on a plain "
                                        "background, no other figures, no text")
    expression_ok: bool = Field(description="the face clearly shows the requested "
                                            "expression")
    closeup_ok: bool = Field(default=True,
                             description="a close-up: head and shoulders cropped at the "
                                         "chest, the face filling most of the frame; false "
                                         "for a full body or a small, distant figure")
    issues: list[str] = Field(description="concrete fixes; empty if none")


class _Traits(BaseModel):
    skin_tone: int = Field(description="1 very fair, 2 fair, 3 light/olive, 4 medium/tan, "
                                       "5 brown, 6 deep brown")
    hair_colour: str = Field(description="one of: " + ", ".join(HAIR_COLOURS))
    hair_texture: str = Field(description="one of: " + ", ".join(HAIR_TEXTURES))
    age_group: str = Field(description="one of: " + ", ".join(AGE_GROUPS))
    glasses: str = Field(description="short description of the glasses, e.g. 'round "
                                     "black-rimmed glasses'; empty if none")
    headwear: str = Field(description="short description of the head covering with its "
                                      "colour, e.g. 'a dark navy skullcap (kippah)', 'a light "
                                      "blue hijab', 'a red baseball cap'; empty if none")
    facial_hair: bool = Field(description="true if the person has a beard or moustache")
    hair_covered: bool = Field(description="true if headwear hides most or all of the hair")


GRADE_INSTRUCTIONS = """You are the quality checker for a children's picture book.
Image 1 is the generated picture to check.{refs}
Score honestly; a typical acceptable picture scores 0.7-0.9.
- likeness (0..1): is the hero child in image 1 recognisably the same character as the
  HERO REFERENCE (face shape, skin tone, hair colour and style, eye colour, glasses and
  headwear)? Without a hero reference, compare with the photo: the hero is drawn as a
  CHILD VERSION of the person in the photo (about 7-9 years old, whatever age the photo
  shows), so judge face shape, eyes, nose, skin tone and hair - youth, smooth skin and
  no beard are expected and never lower likeness. Stylisation is expected and fine. 0
  if the hero is missing or their face is hidden. An adult or elderly face, a beard or
  a different child (different face, hair or skin) is a different character: score it
  low. Likeness is about identity only, never the expression: a scared, surprised,
  crying-out or laughing face is just as like the child as the calm face of the hero
  reference - never score a page higher because it copies the reference's smile.
- Identity checks - compare the hero in image 1 with the child in the ORIGINAL PHOTO:
  skin_tone_match, hair_colour_match, hair_texture_match (hair type: straight, wavy,
  curly or coily). Stylisation is fine: simplified shapes, flat colours, big eyes, a
  game-like or low-detail look, a younger or child-like version of the person, and
  different lighting do NOT count as mismatches. The photo may show a teen, an adult or
  an elderly person, or be taken in warm, dim or coloured light; age and lighting alone
  are never a mismatch - judge the underlying skin tone and hair. Grey hair in the photo
  stays grey on the child version (that is a match). Headwear: when a cap, kippah,
  hijab, turban, headscarf or hood covers the hair in the photo or in image 1, judge
  only the hair that shows; if the hair is fully covered in either, set
  hair_colour_match and hair_texture_match to true - never ask for hair that the
  headwear rightly covers. But a clearly different skin tone
  (e.g. medium brown instead of light), a different hair colour (e.g. brown instead of
  black, blonde instead of brown) or a different hair type (e.g. tight curls instead of
  straight hair) is a different child: set that field to false and say what is wrong
  in identity_notes. If the hero is missing, set all three to true and score likeness 0.
- hair_texture_match - judge this one carefully. Compare the hair TYPE category in the
  photo and in image 1: straight, wavy, curly or coily (ignore length and styling).
  Look closely at short hair: short, tight curls or coils close to the head are curly
  (or coily), not straight, however short they are cut. Stylised hair is often one
  solid shape (low-poly, pixel art) and that is fine, but when the photo shows curly
  or coily hair that shape must still read as curly: a bumpy, lumpy or clumped
  silhouette, curl tufts or a textured rounded mass. A smooth, sleek or swept shape
  (straight strands, a side-swept fringe) on a child whose photo shows curls is a
  mismatch - set false. Equally, curls on a child with straight hair are a mismatch.
- hero_hair_match - compare the hero's hair in image 1 with the HERO REFERENCE image
  (not only the type category): same length, same overall shape and silhouette, same
  hairstyle, same colour. Short hair stays short, tight curls stay tight curls close to
  the head, a bun stays a bun. A little movement is fine: a few strands or curls lifted
  by wind or a jump, hair slightly squashed by a hood or hat. Hair that is clearly
  longer, loose and flowing where the reference is short, straightened, restyled or a
  different colour is a different look - set false and say what differs in
  identity_notes. True if there is no hero reference image or the hair is not visible
  (e.g. covered by headwear).
- Hero look checks (hero_child_ok, hero_items_ok, hero_outfit_ok) - definite errors:
  * hero_child_ok: the hero must be a CHILD of about 6-10 on every picture. A grown-up
    or elderly face, a beard, moustache, stubble, wrinkles or adult body proportions is
    false - even when the photo shows an adult.
  * hero_items_ok: the hero must wear every listed personal item (glasses, headwear)
    exactly like in the hero reference; a missing item is false. A hat worn over or
    around the headwear is fine, but a HOOD pulled UP over the head so that the listed
    headwear (or the glasses) cannot be seen is false - the hero's hood always stays
    DOWN on the shoulders; with the hood up and no visible headwear the hero reads as a
    different child. True if none is listed or the head is not visible.
  * hero_outfit_ok: the hero must wear the hero outfit described below. Adult clothes
    from the photo (a suit, jacket, tie, shirt and trousers) or another character's
    clothes (a mother's dress, blouse or apron, Grandma's nightdress, a cloak belonging
    to someone else) are false. Lighting, simplified detail and the outfit partly
    hidden by pose or framing are fine.
  Say what is wrong in hero_look_notes.
- family_match - only when a family reference sheet is given: every family member in
  image 1 (mother, grandmother, ...) must look like their figure on the sheet and the
  family description: the same apparent age, hair colour and hairstyle (and broadly the
  same face and clothes). A clearly different age (e.g. grey or white hair and wrinkles
  on a mother who is young on the sheet), a different hair colour or a different
  hairstyle is false - name who and what in family_notes. Pose, expression, lighting,
  a slightly different angle or simplified detail are fine. True if no family member is
  in image 1 or no family sheet is given.
- cast_match - only when supporting characters are listed below: each one in image 1
  must match its reference sheet (when given; the plain grey bar on a sheet marks the
  hero child's height and is never drawn in the scene) and its description - the same
  look and colours, the outfit or costume stated for THIS page (e.g. no nightcap when
  the line says he wears none, the nightcap when it says he wears it) - and be about the
  stated size relative to the hero child. Sizes are judged loosely: perspective
  (distance), framing, cropping (a giant cut off by the frame), sitting, bending and
  poses are all fine - judge as if they stood side by side. A size fails ONLY when it is
  clearly wrong: a character described as a giant or as much taller than the child that
  is NOT clearly bigger (less than about 2x the child's height, or about a grown-up's
  size or smaller); a small character or animal (hen, harp, pet) drawn as big as the
  child or bigger; an ordinary grown-up or animal more than about twice or less than
  about half the stated size, or a head that fills a whole bed. A giant only about
  2-3x the child's height indoors is FINE. False -> one concrete correction per
  character in cast_notes ("the wolf must be about 1.5x the child's height standing;
  here about 3x"). True if no supporting character is listed.
- expression_match - only when a requested hero expression is given below: true if the
  hero's face and body language clearly show that emotion. A clearly different emotion
  is false: smiling, laughing or calm/neutral when scared, worried or alarmed is
  requested; scared or crying when happy is requested. Intensity may differ a little.
  Say what is wrong and what to show in expression_notes. True if no expression is
  requested or the hero's face is not visible.
- cast_expression_match - only when "Other characters' expressions" are listed below:
  each listed character's face and body language must show that page's expression (a
  reference sheet's grin or calm face does not count). A clearly different emotion is
  false, e.g. a grinning or smiling wolf when he must look dazed and flustered, an
  awake, roaring giant when he must be asleep. Intensity may differ a little. One
  concrete correction per character in cast_expression_notes. True if none is listed or
  the face is not visible.
- style_match - compare the RENDERING STYLE of the whole of image 1 with the HERO
  REFERENCE image (when given; it is drawn in the book's style) and the style
  description below: the same technique (e.g. low-polygon 3D with facets and pixelated
  textures, or crisp pixel art). A clearly different rendering is false - a flat 2D
  painting, drawing, watercolour or cartoon illustration in a 3D book, a smooth modern
  3D film look in a low-poly book, a smooth painting in a pixel-art book, or characters
  in one technique pasted on a background in another. Lighting, colour mood, camera
  distance and level of detail are NOT style differences. Say what differs in
  style_notes (name both techniques).
- fidelity (0..1): does image 1 show what the description asks for, including every
  item in the must-be-visible list, with no extra limbs, merged bodies or broken faces?
- has_text_artifacts: true if image 1 contains ANY letters, words, numbers, signs,
  speech bubbles, captions, logos or watermarks.
- contradictions: DEFINITE violations only - objective, checkable errors that anyone
  could confirm by looking, never matters of taste or interpretation. Only these count:
  * a wrong count of something the description or Props count (e.g. "show exactly five
    beans in total; currently seven");
  * a wrong colour of something whose colour is specified (e.g. all beans purple
    instead of one red, one yellow, one green, one blue and one purple);
  * a prop in the wrong state for THIS page when its Props line says "On this page: ..."
    (e.g. "the beanstalk must lie fallen, straight and flat across the fields - here it
    bends in an arch over the stump"; "show a deep notch cut into the stalk - the axe only
    touches it"; "only one red bean - there are two"; "show the purple bean on the
    doorstep");
  * a must-be-visible item or a required character that is missing;
  * the wrong character, or an extra character the description does not ask for;
  * the same character drawn more than once (a second copy, a before/after or
    motion-sequence duplicate), or a single object drawn twice (e.g. "show only one
    basket - remove the second basket on the table"; "only one nightcap, on the wolf's
    head - remove the one flying in the air"; "draw the wolf only once");
  * a place or object that does not match its Props line (e.g. "Grandma's cottage must
    have white walls and a golden thatched roof - here it is grey stone with red tiles";
    "the quilt must be the pink, yellow and green patchwork - here it is plain blue");
  * the hero child copying another character's specific gesture (e.g. both with a
    finger to the lips) when the description gives the hero a different action;
  * a cut-out/sticker look: an outline, border, halo or glow around a figure that
    doesn't come from the scene's lighting, or a figure that is visibly pasted in (thin
    dark outlines that are part of a pixel-art style are fine);
  * a blank, empty, unpainted, blurred-out, smeared, padded or letterboxed band, strip
    or border along an edge of the picture (e.g. "paint the whole picture edge to edge;
    no empty band at the top" for a plain cream strip across the top, or "paint the top
    of the picture sharply; no blurred strip" for a smeared, out-of-focus strip). This is
    always a contradiction, never just an issue;
  * inset panels, comic borders, split panels or more than one image in the frame
    (e.g. "remove the reference inset panels in the top-left corner");
  * letters, words or other text (also set has_text_artifacts);
  * a main character (the hero child or a character the description names) cut off by
    the edge of the picture so that their FACE is lost - half a face, or the head sliced
    off by the frame (e.g. "show the hero's whole face inside the frame - it is cut off
    at the left edge"). A body cropped below the shoulders, or a giant's body running
    off the frame while his face shows, is fine;
  * a character who flees or runs AWAY drawn facing or heading TOWARD what they flee
    from - the place, character or people the description says they run away from (e.g.
    "the wolf must run away from the cottage, seen from behind heading over the far hill
    - here he runs toward the cottage") - or drawn close up / large in the foreground
    when the description says they are small, far away or in the distance.
  Write each one as a short, concrete fix, e.g. "show exactly five beans, not about
  ten" or "re-draw the hero as part of the scene with no white outline".
  These do NOT count (put them in issues if they matter at all):
  * framing or cropping choices (e.g. NOT "the beanstalk tip should end above the clouds
    rather than off-panel") - except a main character's face cut off by the frame (above);
  * how much of something is visible, as long as it is recognisable (e.g. NOT "the
    giant should be fully visible rather than just a blurry boot" when the description
    shows only his boot);
  * the exact pose, gesture, grip or expression (e.g. NOT "holding the axe near the head
    rather than swinging from the handle");
  * relative size, unless the description gives a size AND it is off by more than about
    2x (e.g. NOT "the golden egg is somewhat large" or "the bean is slightly large");
  * motion state - toppling or standing, running or walking - unless that motion is the
    page's main event or a Props line states it (then it IS a contradiction), and a
    fleeing character's direction always counts (above);
  * the art style or rendering technique, camera angle, lighting, and background
    details the description does not mention.
  If you are not sure, put it in issues instead. Empty list if there are none.
- issues: at most 5 softer, concrete improvement notes an illustrator could act on
  (e.g. "the hero could face the viewer more"; "the beanstalk leaves could be bigger").
  Do not repeat the contradictions here.
- boxes: every face (kind "face", label who it is); the whole visible body of every
  character, from the top of the head to the feet or to the edge of the picture (kind
  "figure", label who it is, e.g. "hero body", "mother body" - a character gets both a
  face box and a figure box); and every must-be-visible item that is a discrete thing
  you could pick up or point at - a bean, a harp, a basket, a hen, an axe (kind
  "key_object", label the item) - as box_2d [ymin, xmin, ymax, xmax] on a 0-1000 grid
  of image 1. Never box ground, grass, soil, sky, clouds, a garden, a field,
  a forest, a road, a room, walls or any other large area or background, even if the
  must-be-visible list names it."""

HERO_SHEET_RULES = """
This is the HERO REFERENCE SHEET. Fail it only for what matters on every page: identity
(likeness, skin tone, hair), a child (not an adult), the kept personal items (glasses,
headwear), the outfit TYPE and main colours (e.g. tunic, trousers, boots, bag; cape and
tunic), the art style, one full-body figure on a plain background, and text. Small outfit
details never fail it and are NOT contradictions: which elbow or knee a patch is on, how
many patches, which side or hip a bag or satchel sits on, strap length, buttons or laces -
put them in issues at most, and still set hero_outfit_ok to true."""

SHEET_CHECK_INSTRUCTIONS = """You check a character reference sheet for a children's
picture book. It should show exactly {count} adult family member(s) of the hero child,
side by side on a plain background, and NO child. Expected, one line per figure:
{lines}
Stylisation is expected and fine (simplified shapes, flat colours, a game-like or
low-detail look); judge apparent age, hair and the number of figures.
- count_ok: true if there are exactly {count} figure(s) and no extra people.
- no_child: true if no child (no hero, no child-sized figure) is drawn.
- age_ok: true if every figure looks about its expected age. A figure expected to be a
  young adult (about 30) with grey, silver or white hair, wrinkles, a stooped posture or
  an elderly face is false; an elderly figure drawn young is false too.
- hair_ok: true if every figure's visible hair colour and hairstyle match the expected
  hair (a headscarf or ribbon over part of the hair is fine if the visible hair matches).
- issues: one short, concrete fix per problem, naming the character, e.g. "Mother must
  look about 30: curly black hair in a loose bun, no grey hair, no wrinkles". Empty if
  everything matches."""

CAST_SHEET_CHECK_INSTRUCTIONS = """You check a character reference sheet for a children's
picture book. It should show exactly one character - {name} - whole body, on a plain
background, next to ONE plain vertical grey height bar that marks the hero child's
height (the bar is a scale guide, not a figure). Expected:
- {name}: {description}
- Size relative to the hero child (the bar): {size}
Stylisation is expected and fine (simplified shapes, flat colours, pixel art, low poly).
- figure_ok: true if there is exactly one figure (the bar does not count), no child, no
  other people or animals and no scenery.
- looks_ok: true if it is clearly {name} and matches the description, including the
  outfit or the lack of one (e.g. no nightcap when none is expected).
- size_ok: true if the grey bar is drawn and {name} is about the stated size next to it
  (within about 1.5x); false if the bar is missing or the size is clearly wrong.
- issues: one short, concrete fix per problem, e.g. "draw the wolf about 1.5 times as
  tall as the grey bar". Empty if everything matches."""

PORTRAIT_CHECK_INSTRUCTIONS = """You check a small speaker portrait for a children's
picture book (shown next to the character's words in a dialogue box). It should show
exactly one character - {name} - head and shoulders, facing the viewer, on a plain
background. Expected:
- {name}: {description}
- Expression: {expression}
Stylisation is expected and fine (simplified shapes, flat colours, pixel art, low poly).
- character_ok: true if it is clearly {name} and matches the description and costume.
- single_ok: true if there is exactly one figure, no other people or animals, no
  scenery, no letters or frame text.
- closeup_ok: true if it is a CLOSE-UP - head and shoulders only, cropped at the chest,
  the face filling most of the frame (the head at least about half the picture's
  height). A full body, a figure down to the waist or knees, or a small figure with
  lots of empty background is false.
- expression_ok: true if the face clearly shows the requested expression. A calm,
  neutral or smiling face when an emotion is requested is false - e.g. scared needs wide
  eyes, raised eyebrows and an open mouth; angry needs eyebrows drawn down and a frown.
- issues: one short, concrete fix per problem, e.g. "show only the wolf's head and
  shoulders, the face filling most of the square". Empty if everything matches."""

TRAITS_INSTRUCTIONS = """Look at the main person in this photo (a child, a teen or an
adult). A picture book will draw them as a CHILD character, so estimate only what is
needed to keep them recognisable and to match illustrated family members:
- skin_tone on a 1-6 scale (1 very fair, 2 fair, 3 light or olive, 4 medium or tan,
  5 brown, 6 deep brown);
- hair_colour (one of: {colours}) and hair_texture (one of: {textures}) of the hair as
  photographed (grey or white hair is "grey"); if unsure, give your best estimate; use
  "unknown" only if the hair is not visible at all;
- age_group (one of: {ages}): child up to about 12, teen 13-19, adult 20-64, elderly 65+;
- glasses: a short description if they wear glasses (shape and frame colour), else empty;
- headwear: a short description with colour if they wear a head covering (skullcap /
  kippah, hijab, turban, headscarf, cap, hat ...), else empty;
- facial_hair: true if they have a beard or moustache;
- hair_covered: true if the headwear hides most or all of the hair."""


def _boxes(raw: list[_Box]) -> list[Box]:
    out = []
    for b in raw:
        if len(b.box_2d) != 4:
            continue
        ymin, xmin, ymax, xmax = (max(0, min(1000, v)) / 1000 for v in b.box_2d)
        if xmax <= xmin or ymax <= ymin:
            continue
        raw_kind = b.kind.strip().lower()
        kind = raw_kind if raw_kind in ("face", "figure") else "key_object"
        out.append(Box(b.label[:80], xmin, ymin, xmax - xmin, ymax - ymin, kind))
    return out


def traits_note(traits: PhotoTraits | None) -> str:
    """'Measured from the original photo: light olive skin, straight black hair. ...'"""
    if traits is None:
        return ""
    from app.pipeline.family import hair_phrase, skin_phrase

    hair = hair_phrase(traits)
    if hair and traits.hair_covered:
        hair_bit = f"{hair} hair, mostly covered by headwear (judge only the hair that shows)"
    else:
        hair_bit = f"{hair} hair" if hair else ""
    bits = [p for p in (f"{skin_phrase(traits)} skin" if skin_phrase(traits) else "",
                        hair_bit) if p]
    if not bits:
        return ""
    note = ("Measured from the original photo: " + ", ".join(bits) + ". Use this for the "
            "identity checks (the hero should keep the same skin tone, hair colour and hair "
            "type).")
    if not traits.is_child:
        who = {"teen": "a teenager", "elderly": "an elderly person"}.get(
            traits.age_group, "an adult")
        note += (f" The photo shows {who}: the hero is drawn as a "
                 "child version of them (about 7-9, no beard, no wrinkles) - that is "
                 "expected, not a mismatch.")
    return note


def props_note(props) -> str:
    """The page's canonical props for the grader (the same lines the image prompt has)."""
    lines = [p.strip() for p in props if p and p.strip()]
    if not lines:
        return ""
    return ("\nProps (they must match exactly - a different count, colour, size or "
            "state for this page is a contradiction):\n" + "\n".join(f"- {p}" for p in lines))


def cast_note(cast, first_ref: int | None) -> str:
    """The supporting characters on the page (description, size, outfit on this page)."""
    lines = [c.strip() for c in cast if c and c.strip()]
    if not lines:
        return ""
    refs = (f" Their reference sheets are image {first_ref} onward, in this order."
            if first_ref is not None else "")
    return ("\nSupporting characters (cast_match: look, outfit on this page and size "
            f"relative to the hero child must match):{refs}\n"
            + "\n".join(f"- {c}" for c in lines))


def presence_note(names) -> str:
    """The characters whose presence the grader reports (``visible_characters``)."""
    lines = [n.strip() for n in names if n and n.strip()]
    if not lines:
        return ""
    return ("\nCharacters to report (visible_characters): list each of these names, "
            "exactly as written, that appears in image 1 in ANY form - even partly, hidden "
            "under covers, in disguise or as only ears, eyes, a tail or a boot. Leave a "
            "name out only if no part of that character is drawn:\n"
            + "\n".join(f"- {n}" for n in lines))


def visible_from(raw, names) -> list[str]:
    """The asked ``names`` the model reported (case-insensitive; a reported name that
    contains an asked one counts - "the wolf in a nightcap" -> "the wolf")."""
    got = [" ".join(str(r).lower().split()) for r in raw or [] if str(r).strip()]
    out = []
    for n in names:
        key = " ".join(n.lower().split())
        if key and any(key == g or key in g for g in got):
            out.append(n)
    return out


def expression_note(expression: str) -> str:
    e = (expression or "").strip()
    return f"\nRequested hero expression (expression_match): {e}" if e else ""


def cast_expression_note(lines) -> str:
    lines = [x.strip() for x in lines if x and x.strip()]
    if not lines:
        return ""
    return ("\nOther characters' expressions on this page (cast_expression_match):\n"
            + "\n".join(f"- {x}" for x in lines))


def style_note(style: str) -> str:
    st = (style or "").strip()
    return f"\nBook art style (style_match): {st}" if st else ""


def family_note(family) -> str:
    """What each family member on the page must look like (age, skin tone, hair)."""
    lines = [f.strip() for f in family if f and f.strip()]
    if not lines:
        return ""
    return ("\nFamily members (they must match the family reference sheet - a different "
            "apparent age, hair colour or hairstyle is a family mismatch):\n"
            + "\n".join(f"- {f}" for f in lines))


def shrink_for_grader(data: bytes, max_side: int = GRADER_MAX_SIDE,
                      quality: int = GRADER_JPEG_QUALITY) -> bytes:
    """``data`` re-encoded as JPEG with the longest side at most ``max_side`` px, when it
    is larger; otherwise (or if it cannot be read, or ``max_side`` <= 0) unchanged."""
    if max_side <= 0:
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= max_side:
                return data
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=quality)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return data


_NO_ITEM = ("", "none", "no", "n/a", "null", "nothing")


def _item(text: str) -> str:
    """A personal item description from the traits call ('' for none), at most 80 chars."""
    t = " ".join((text or "").split()).strip(" .")
    return "" if t.lower() in _NO_ITEM else t[:80]


def hero_look_note(items, outfit: str) -> str:
    """The personal items and outfit the hero look checks use (pages and the hero)."""
    lines = [i.strip() for i in items if i and i.strip()]
    out = ""
    if lines:
        out += ("\nHero personal items (hero_items_ok - always worn): " + "; ".join(lines))
    if outfit and outfit.strip():
        out += f"\nHero outfit (hero_outfit_ok): {outfit.strip()}"
    return out


_TRIVIA = ("patch", "elbow", "knee", "hip", "strap", "lace", "button")
_SIDES = ("left", "right", "side")


def outfit_trivia(text: str) -> bool:
    """A hero-sheet contradiction about a small outfit detail (which elbow a patch is on,
    which side the satchel sits on) - never a reason to redraw the hero reference."""
    t = text.lower()
    if any(w in t for w in _TRIVIA):
        return True
    return any(w in t for w in ("satchel", "bag")) and any(w in t for w in _SIDES)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


# (model, level) pairs the API rejected with a 400; shared by every grader in the process
# (a new grader is built per book/request), so the failed call is made once, not per book.
_REJECTED_LEVELS: set[tuple[str, str]] = set()
# Vision models that rejected a per-part media_resolution (400) in this process.
_NO_MEDIA_RESOLUTION: set[str] = set()
_LEVEL_ENUM = {"low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM",
               "high": "MEDIA_RESOLUTION_HIGH", "ultra_high": "MEDIA_RESOLUTION_ULTRA_HIGH"}
# Calls per check when the answer is not valid JSON for the schema (one retry): a real
# page once went unchecked after a single validation error.
JSON_TRIES = 2


class _Img:
    """An image for a grader call, built into a ``types.Part`` at call time (with or
    without its media_resolution)."""

    __slots__ = ("data", "level")

    def __init__(self, data: bytes, level: str):
        self.data = data
        self.level = level


def _rejects_media_resolution(exc: genai_errors.APIError) -> bool:
    msg = f"{exc.message or ''} {exc.status or ''}".lower()
    return exc.code == 400 and ("media_resolution" in msg or "media resolution" in msg)


class GeminiVisionGrader:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, thinking_level: str = "low",
                 image_max_side: int = GRADER_MAX_SIDE, media_level: str = "",
                 media_level_first: str = ""):
        """``media_level_first`` / ``media_level``: per-part media_resolution of the
        picture under check (image 1) and of every other image ('' = API default, high:
        1120 tokens per image on Gemini 3; medium 560, low 280)."""
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not set (needed for the vision grader)")
        level = (thinking_level or "").strip().lower()
        if level not in _LEVELS:
            raise ValueError(f"unknown grader thinking level {thinking_level!r}; "
                             f"one of {sorted(_LEVELS)}")
        self.model = model
        # A (model, level) pair a previous grader found unsupported is not tried again.
        self.thinking_level = "low" if (model, level) in _REJECTED_LEVELS else level
        self.image_max_side = image_max_side
        self.media_level = media_level
        self.media_level_first = media_level_first
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)),
        )

    def _config(self, schema: type[BaseModel]) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            # Little thinking keeps a grade call short (book target < 60 s).
            thinking_config=types.ThinkingConfig(thinking_level=_LEVELS[self.thinking_level]),
        )

    def _build(self, contents: list) -> list:
        levels_ok = self.model not in _NO_MEDIA_RESOLUTION
        out = []
        for c in contents:
            if not isinstance(c, _Img):
                out.append(c)
            elif levels_ok and c.level:
                out.append(types.Part.from_bytes(
                    data=c.data, mime_type=sniff_mime(c.data),
                    media_resolution=types.PartMediaResolution(
                        level=types.PartMediaResolutionLevel(_LEVEL_ENUM[c.level]))))
            else:
                out.append(types.Part.from_bytes(data=c.data, mime_type=sniff_mime(c.data)))
        return out

    async def _generate(self, contents: list, schema: type[BaseModel]):
        """One request; a 400 for the thinking level or the media resolution repeats it once
        without that setting (remembered for the model)."""
        for _ in range(3):
            try:
                return await self._client.aio.models.generate_content(
                    model=self.model, contents=self._build(contents),
                    config=self._config(schema))
            except genai_errors.APIError as exc:
                if _rejects_thinking_level(exc) and self.thinking_level != "low":
                    log.warning("model %s rejected thinking level %s (%s) - using low",
                                self.model, self.thinking_level, exc.message or exc.status)
                    _REJECTED_LEVELS.add((self.model, self.thinking_level))
                    self.thinking_level = "low"
                    continue
                if (_rejects_media_resolution(exc) and self.model not in _NO_MEDIA_RESOLUTION
                        and any(isinstance(c, _Img) and c.level for c in contents)):
                    log.warning("model %s rejected media_resolution (%s) - grading without "
                                "it from now on", self.model, exc.message or exc.status)
                    _NO_MEDIA_RESOLUTION.add(self.model)
                    continue
                raise
        raise ProviderError("Gemini vision: request settings rejected repeatedly")

    async def _json_call(self, contents: list, schema: type[BaseModel]):
        """(parsed, meta). An answer that is not valid JSON for ``schema`` is asked again
        once (``JSON_TRIES``); the meta sums the tokens and cost of every try."""
        started = time.perf_counter()
        in_tok = out_tok = 0
        cost = 0.0
        model = self.model
        last: Exception | None = None
        for attempt in range(1, JSON_TRIES + 1):
            try:
                response = await self._generate(contents, schema)
            except genai_errors.APIError as exc:
                raise provider_error_from_google(exc, "Gemini vision API error") from exc
            usage = response.usage_metadata
            i = (usage.prompt_token_count or 0) if usage else 0
            o = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                 if usage else 0)
            in_tok += i
            out_tok += o
            cost += estimate_cost(self.model, input_tokens=i, output_text_tokens=o)
            model = response.model_version or self.model
            try:
                parsed = schema.model_validate_json(response.text or "")
                break
            except ValidationError as exc:
                last = exc
                log.warning("grader answer was not valid JSON (try %d/%d): %s", attempt,
                            JSON_TRIES, str(exc)[:200])
        else:
            raise ProviderError(f"Gemini vision returned invalid JSON: {last}"[:300]) from last
        meta = CallMeta(model=model, input_tokens=in_tok, output_tokens=out_tok,
                        duration_s=time.perf_counter() - started,
                        est_cost_usd=round(cost, 6))
        return parsed, meta

    def _part(self, data: bytes, first: bool = False) -> _Img:
        small = shrink_for_grader(data, self.image_max_side)
        sniff_mime(small)  # unsupported formats fail before any call
        return _Img(small, self.media_level_first if first else self.media_level)

    async def grade(self, image, *, hero, photo, caption, must_be_visible, subject="page",
                    traits: PhotoTraits | None = None, props=(), family_ref=None, family=(),
                    cast_refs=(), cast=(), expression="", hero_items=(), hero_outfit="",
                    presence=(), style="", cast_expressions=()):
        parts = [self._part(image, first=True)]
        if hero is not None:
            refs = ("\nImage 2 is the stylised hero character reference (the HERO "
                    "REFERENCE: the hero as a child - judge likeness against it). Image 3 is "
                    "the original photo of the person (it may show a teen or adult; use it "
                    "only for skin tone and hair colour / type).")
            parts.append(self._part(hero))
        else:
            refs = ("\nImage 2 is the original photo of the person (it may show a teen "
                    "or adult; the hero is drawn as a child version of them).")
        parts.append(self._part(photo))
        if family_ref is not None:
            refs += (f" Image {len(parts) + 1} is the family reference sheet: the hero's "
                     "family members as they must look on every page.")
            parts.append(self._part(family_ref))
        cast_lines = [c for c in cast if c and c.strip()] if subject == "page" else []
        sheets = list(cast_refs) if cast_lines else []
        first_sheet = len(parts) + 1 if sheets else None
        if sheets:
            last = len(parts) + len(sheets)
            refs += (f" Image{'s' if len(sheets) > 1 else ''} {first_sheet}"
                     f"{f'-{last}' if len(sheets) > 1 else ''} "
                     f"{'are' if len(sheets) > 1 else 'is'} the supporting characters' "
                     "reference sheet(s), each next to a grey bar marking the hero child's "
                     "height.")
            parts += [self._part(sh) for sh in sheets]
        expr = expression if subject == "page" else ""
        cast_expr = ([x for x in cast_expressions if x and x.strip()]
                     if subject == "page" else [])
        known = traits_note(traits)
        what = ("a full-body character reference sheet of the hero child alone on a plain "
                "background" if subject == "hero" else "a story page")
        text = (GRADE_INSTRUCTIONS.format(refs=refs)
                + f"\n\nImage 1 should be {what}. Description: {caption}"
                + ("\nMust be visible: " + "; ".join(must_be_visible) if must_be_visible
                   else "")
                + props_note(props)
                + (family_note(family) if family_ref is not None else "")
                + cast_note(cast_lines, first_sheet)
                + expression_note(expr)
                + cast_expression_note(cast_expr)
                + style_note(style)
                + hero_look_note(hero_items, hero_outfit)
                + presence_note(presence)
                + (f"\n{known}" if known else "")
                + (HERO_SHEET_RULES if subject == "hero" else ""))
        parsed, meta = await self._json_call([text, *parts], _Grade)
        contradictions = [c.strip()[:200] for c in parsed.contradictions if c.strip()]
        if subject == "hero":
            contradictions = [c for c in contradictions if not outfit_trivia(c)]
        return GradeResult(
            likeness=_clamp01(parsed.likeness), fidelity=_clamp01(parsed.fidelity),
            has_text_artifacts=bool(parsed.has_text_artifacts),
            issues=[i.strip()[:200] for i in parsed.issues if i.strip()][:5],
            contradictions=contradictions[:5],
            boxes=_boxes(parsed.boxes), meta=meta,
            skin_tone_match=bool(parsed.skin_tone_match),
            hair_colour_match=bool(parsed.hair_colour_match),
            hair_texture_match=bool(parsed.hair_texture_match),
            identity_notes=parsed.identity_notes.strip()[:200],
            # Nothing to compare against -> never a failure, whatever the model said.
            hero_hair_match=bool(parsed.hero_hair_match) if hero is not None else True,
            family_match=bool(parsed.family_match) if family_ref is not None else True,
            family_notes=(parsed.family_notes.strip()[:200] if family_ref is not None
                          else ""),
            cast_match=bool(parsed.cast_match) if cast_lines else True,
            cast_notes=parsed.cast_notes.strip()[:300] if cast_lines else "",
            expression_match=bool(parsed.expression_match) if expr.strip() else True,
            expression_notes=parsed.expression_notes.strip()[:200] if expr.strip() else "",
            hero_child_match=bool(parsed.hero_child_ok),
            hero_items_match=(bool(parsed.hero_items_ok)
                              if any(i and i.strip() for i in hero_items) else True),
            hero_outfit_match=(bool(parsed.hero_outfit_ok)
                               if hero_outfit and hero_outfit.strip() else True),
            hero_look_notes=parsed.hero_look_notes.strip()[:200],
            cast_expression_match=(bool(parsed.cast_expression_match) if cast_expr
                                   else True),
            cast_expression_notes=(parsed.cast_expression_notes.strip()[:300] if cast_expr
                                   else ""),
            style_match=bool(parsed.style_match) if style.strip() else True,
            style_notes=parsed.style_notes.strip()[:200] if style.strip() else "",
            visible_characters=(visible_from(parsed.visible_characters, presence)
                                if any(n and n.strip() for n in presence) else None),
        )

    async def check_portrait(self, image: bytes, *, name: str, description: str,
                             expression: str) -> PortraitCheck:
        text = PORTRAIT_CHECK_INSTRUCTIONS.format(
            name=name, description=description.strip() or name,
            expression=expression.strip() or "neutral")
        parsed, meta = await self._json_call(
            [text, self._part(image, first=True)], _PortraitCheck)
        return PortraitCheck(
            character_ok=bool(parsed.character_ok), single_ok=bool(parsed.single_ok),
            expression_ok=bool(parsed.expression_ok), closeup_ok=bool(parsed.closeup_ok),
            issues=[i.strip()[:200] for i in parsed.issues if i.strip()][:5], meta=meta,
        )

    async def check_cast_sheet(self, image: bytes, *, name: str, description: str,
                               size: str) -> CastSheetCheck:
        text = CAST_SHEET_CHECK_INSTRUCTIONS.format(
            name=name, description=description.strip() or name,
            size=size.strip() or "not stated (only check that the bar is drawn)")
        parsed, meta = await self._json_call(
            [text, self._part(image, first=True)], _CastSheetCheck)
        return CastSheetCheck(
            figure_ok=bool(parsed.figure_ok), looks_ok=bool(parsed.looks_ok),
            size_ok=bool(parsed.size_ok),
            issues=[i.strip()[:200] for i in parsed.issues if i.strip()][:5], meta=meta,
        )

    async def check_family_sheet(self, image: bytes, *, characters) -> SheetCheck:
        lines = [c.strip() for c in characters if c and c.strip()]
        text = SHEET_CHECK_INSTRUCTIONS.format(
            count=len(lines), lines="\n".join(f"- {c}" for c in lines))
        parsed, meta = await self._json_call(
            [text, self._part(image, first=True)], _SheetCheck)
        return SheetCheck(
            count_ok=bool(parsed.count_ok), no_child=bool(parsed.no_child),
            age_ok=bool(parsed.age_ok), hair_ok=bool(parsed.hair_ok),
            issues=[i.strip()[:200] for i in parsed.issues if i.strip()][:5], meta=meta,
        )

    async def photo_traits(self, photo: bytes) -> PhotoTraits:
        text = TRAITS_INSTRUCTIONS.format(colours=", ".join(HAIR_COLOURS),
                                          textures=", ".join(HAIR_TEXTURES),
                                          ages=", ".join(AGE_GROUPS))
        parsed, meta = await self._json_call(
            [text, self._part(photo, first=True)], _Traits)
        colour = parsed.hair_colour.strip().lower().replace(" ", "_")
        texture = parsed.hair_texture.strip().lower()
        age = parsed.age_group.strip().lower()
        return PhotoTraits(
            skin_tone=max(1, min(6, int(parsed.skin_tone))),
            hair_colour=colour if colour in HAIR_COLOURS else "unknown",
            hair_texture=texture if texture in HAIR_TEXTURES else "unknown",
            meta=meta,
            age_group=age if age in AGE_GROUPS else "unknown",
            glasses=_item(parsed.glasses),
            headwear=_item(parsed.headwear),
            facial_hair=bool(parsed.facial_hair),
            hair_covered=bool(parsed.hair_covered),
        )


def _rejects_thinking_level(exc: genai_errors.APIError) -> bool:
    """A 400 that complains about the thinking configuration (unsupported level)."""
    msg = f"{exc.message or ''} {exc.status or ''}".lower()
    return exc.code == 400 and "thinking" in msg
