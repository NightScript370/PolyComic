"""Speaker portraits: a small head-and-shoulders picture of the speaking character inside
the dialogue box (RPG style). Two reasons (``portrait:`` on the story page):

* ``offscreen`` (``true``): the speaker is not in the page's picture (grader-gated, below);
* ``moment``: the line is spoken before/after the action the picture shows, so the portrait
  shows the speaker's face while speaking EVEN IF the speaker is in the picture (RPG
  convention, intentional - not a duplicate). No visibility gate and no presence question;
  the portrait is started early (see ``Book.start_moment_portraits``) and a missing one
  just leaves the box without it.

Rules (user-approved design):

1. Candidates: story pages with a single ``speaker`` and ``portrait``
   (``app.stories.loader``): the speaker may be out of the picture, or the text describes
   another moment than the picture.
2. Final gate (no doubling): the page's grade call gets ``presence`` (the speaker's name)
   and reports ``visible_characters`` - every asked name it sees in the picture in ANY
   form (partly hidden, in disguise, only ears or eyes). The portrait is drawn only when
   the CHOSEN attempt's grade says the speaker is not visible and no detection box names
   them. No grade (no grader, grader failed) or no presence answer = unknown = no
   portrait. Every decision is logged per page (``portrait_gate``).
3. Expressive portraits: one per (character, costume, expression), ``portrait_expression``
   from the page (``neutral`` | ``happy`` | ``scared`` | ``sly`` | ``angry``), drawn from the
   character's reference as the reference image:
   * supporting characters: their reference sheet in the page's costume; made ONCE per
     story x style x costume x expression (lazily - only the expression a book needs, the
     first time a book needs it) and stored in ``character_portraits`` for every later
     book (``AssetSource.portrait`` / ``save_portrait``);
   * the hero: the book's hero reference (per book, never cached across books);
   * family members: the book's family sheet (or their bible sheet), per book.
   One image call (kind ``portrait``, 1:1) + one vision check (kind ``portrait_check``:
   right character, one head-and-shoulders figure, expression). A failed check
   regenerates ONCE with the check's notes (only if ``may_retry``), then the portrait
   with fewer failed checks is kept. A failed check call keeps the portrait unchecked; a
   failed image call means no portrait (the page keeps its text box without one).
4. Time: the portrait starts only after the gate passes and only when an image call +
   check are expected to end before the hard cap; the image call goes through the same
   token limiter / image slots / rate-limit rules as a retry generation
   (``must_finish=False``). A stored portrait costs nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from app.pipeline.calls import CallCost, Record, short_error
from app.pipeline.prompts import corrections_block
from app.providers.base import GradeResult, ImageResult, NoImageError, PortraitCheck
from app.stories.loader import HERO, PORTRAIT_MOMENT, Page, Story
from app.styles.presets import CONSTRAINTS, style_reminder

log = logging.getLogger("app.pipeline.portraits")

PORTRAIT_KIND = "portrait"
PORTRAIT_CHECK_KIND = "portrait_check"
PORTRAIT_ASPECT = "1:1"
HERO_PRESENCE_NAME = "the hero child"

# What each ``portrait_expression`` looks like (child-appropriate, never menacing).
EXPRESSION_FACES: dict[str, str] = {
    "neutral": "a calm, friendly neutral face, mouth closed",
    "happy": "a big warm smile and bright, happy eyes",
    "scared": ("scared the way a picture-book character is scared, never horror: eyes "
               "wide open, eyebrows raised high and worried, mouth open in a small O - "
               "clearly not calm and not smiling"),
    "sly": ("a sly, cheeky look: eyes narrowed a little, one eyebrow raised, a crooked "
            "closed-mouth grin - mischievous and silly, never menacing, no teeth shown"),
    "angry": ("cross and demanding the way a picture-book character is: eyebrows drawn "
              "down, mouth in a grumpy frown, a comical huff - never menacing or "
              "frightening, no teeth shown"),
}

ImageCall = Callable[[str, int | None, str, list[bytes]], Awaitable[ImageResult]]
PortraitKey = tuple[str, str, str]  # (character id or "hero", costume id, expression)


def portrait_key(story: Story, page: Page) -> PortraitKey | None:
    """The portrait a candidate page would show, or None for a non-candidate."""
    if not page.portrait or not page.speaker:
        return None
    if page.speaker == HERO:
        return (HERO, "", page.portrait_expression)
    c = story.characters.get(page.speaker)
    if c is None:
        return None
    costume = "" if c.family else c.sheet_costume_id(page.page)
    return (c.id, costume, page.portrait_expression)


def presence_name(story: Story, who: str) -> str:
    """The name the grader is asked about ("the wolf", "Grandma", "the hero child")."""
    if who == HERO:
        return HERO_PRESENCE_NAME
    c = story.characters.get(who)
    return c.name if c is not None else who


def presence_names(story: Story, page: Page) -> list[str]:
    """``presence`` for the page's grade call: the speaker of an ``offscreen`` candidate
    page, else [] (``moment`` pages are never gated, so nobody is asked)."""
    key = portrait_key(story, page)
    if not key or page.portrait_when == PORTRAIT_MOMENT:
        return []
    return [presence_name(story, key[0])]


def _box_words(story: Story, who: str) -> list[str]:
    """Lower-case words a detection box label uses for the speaker ("wolf", "hero")."""
    if who == HERO:
        return ["hero"]
    name = presence_name(story, who).lower()
    for article in ("the ", "a "):
        if name.startswith(article):
            name = name[len(article):]
    c = story.characters.get(who)
    words = {name, who.replace("_", " ").lower()}
    if c is not None and c.tab_label:
        words.add(c.tab_label.lower())
    return sorted(w for w in words if w)


def portrait_gate(story: Story, page: Page, grade: GradeResult | None) -> tuple[bool, str]:
    """(draw the portrait?, reason). Only a grade that answered the presence question and
    does not see the speaker (nor boxes them) lets the portrait through."""
    key = portrait_key(story, page)
    if key is None:
        return False, "not a portrait page"
    if page.portrait_when == PORTRAIT_MOMENT:
        return True, "spoken at another moment than the picture (shown even if visible)"
    if grade is None:
        return False, "presence unknown (picture not graded)"
    if grade.visible_characters is None:
        return False, "presence unknown (grader gave no answer)"
    name = presence_name(story, key[0])
    seen = {" ".join(v.lower().split()) for v in grade.visible_characters}
    if " ".join(name.lower().split()) in seen:
        return False, f"{name} is in the picture"
    words = _box_words(story, key[0])
    for b in grade.boxes:
        label = b.label.lower()
        if any(w in label for w in words):
            return False, f"{name} is in the picture (box {b.label!r})"
    return True, f"{name} is not in the picture"


def portrait_prompt(style: str, name: str, description: str, expression: str,
                    ref_label: str, corrections: Sequence[str] = (),
                    gentle: bool = False) -> str:
    face = EXPRESSION_FACES.get(expression, EXPRESSION_FACES["neutral"])
    fix = corrections_block(corrections)
    reminder = style_reminder(style, like="the reference image")
    return "\n\n".join([
        f"Task: draw a small square CLOSE-UP speaker portrait of {name} for a children's "
        "picture book - shown next to their words in a dialogue box.",
        style,
        f"Image 1 is {ref_label}: copy {name}'s look exactly from it - face, colours, "
        "clothes or costume and rendering style - but not its pose, framing, facial "
        "expression, size bar or background (it may be a full-body figure with a calm "
        "face: this portrait is a close-up with the expression below).",
        f"Character: {description}",
        f"Expression (most important): {face}.",
        f"Composition: a CLOSE-UP of exactly one character - {name} - head and shoulders "
        "only, cropped at the chest, facing the viewer (a slight three-quarter turn is "
        "fine); the face is large and centred and fills most of the square (the head is at "
        "least half the picture's height). Never a full body, never a small or distant "
        "figure. A plain, soft, evenly lit single-colour background. No other people or "
        "animals, no scenery, no frame or border, no letters.",
        *([reminder] if reminder else []),
        *(["Keep it gentle and friendly for very young children: soft, rounded cartoon "
           "shapes, a kind silly face, no teeth, claws or anything sharp or scary."]
          if gentle else []),
        *([fix] if fix else []),
        CONSTRAINTS,
    ])


async def check_portrait(grader, png: bytes, *, name: str, description: str,
                         expression: str, record: Record,
                         page: int | None = None) -> PortraitCheck | None:
    """One recorded vision call; None if it failed (portrait kept unchecked) or the grader
    cannot check portraits."""
    check = getattr(grader, "check_portrait", None)
    if check is None:
        return None
    t0 = time.perf_counter()
    try:
        result = await check(png, name=name, description=description,
                             expression=EXPRESSION_FACES.get(expression, expression))
    except asyncio.CancelledError:
        try:
            await record(CallCost.failed(PORTRAIT_CHECK_KIND, page, grader.model, t0,
                                         grader.name, "cancelled"))
        except Exception:
            log.exception("could not record cancelled portrait check")
        raise
    except Exception as exc:
        log.warning("portrait check failed for %s (portrait used unchecked): %s", name,
                    short_error(exc))
        await record(CallCost.failed(PORTRAIT_CHECK_KIND, page, grader.model, t0,
                                     grader.name, short_error(exc)))
        return None
    await record(CallCost.of_meta(PORTRAIT_CHECK_KIND, page, result.meta, grader.name))
    return result


def _fallback_fixes(name: str, chk: PortraitCheck, expression: str) -> list[str]:
    out = []
    if not chk.character_ok:
        out.append(f"{name} must match the reference image and description exactly")
    if not chk.single_ok:
        out.append(f"show only {name}, head and shoulders, on a plain background")
    if not chk.closeup_ok:
        out.append(f"a close-up: only {name}'s head and shoulders, cropped at the chest, the "
                   "face filling most of the square - never a full body or a small figure")
    if not chk.expression_ok:
        out.append(f"the face must clearly show: {EXPRESSION_FACES.get(expression, '')}")
    return out


async def make_portrait(image_call: ImageCall, style: str, *, name: str, description: str,
                        expression: str, ref: bytes, ref_label: str, grader=None,
                        record: Record | None = None,
                        may_retry: Callable[[], bool] | None = None,
                        page: int | None = None) -> tuple[ImageResult, str] | None:
    """(image, prompt) of the kept portrait, or None if the image call failed (recorded by
    ``image_call``). Checked once; a failed check regenerates once (if ``may_retry()``),
    then the portrait with fewer failed checks is kept. A safety refusal is retried ONCE
    with gentler wording."""
    t0 = time.perf_counter()

    async def draw(corrections: Sequence[str] = ()) -> tuple[ImageResult, str] | None:
        prompt = portrait_prompt(style, name, description, expression, ref_label,
                                 corrections)
        try:
            return await image_call(PORTRAIT_KIND, page, prompt, [ref]), prompt
        except asyncio.CancelledError:
            raise
        except NoImageError as exc:
            if not exc.is_safety:
                log.error("portrait of %s failed: %s", name, short_error(exc))
                return None
            log.warning("portrait of %s refused (finish_reason=%s) - retrying once gently",
                        name, exc.finish_reason)
            prompt = portrait_prompt(style, name, description, expression, ref_label,
                                     corrections, gentle=True)
            try:
                return await image_call(PORTRAIT_KIND, page, prompt, [ref]), prompt
            except asyncio.CancelledError:
                raise
            except Exception as exc2:
                log.error("portrait of %s failed after the gentle retry: %s", name,
                          short_error(exc2))
                return None
        except Exception as exc:
            log.error("portrait of %s failed: %s", name, short_error(exc))
            return None

    first = await draw()
    if first is None:
        return None
    chosen, note = first, "unchecked"
    if grader is not None and record is not None:
        chk1 = await check_portrait(grader, first[0].png, name=name, description=description,
                                    expression=expression, record=record, page=page)
        if chk1 is not None and chk1.passed:
            note = "check passed"
        elif chk1 is not None:
            log.warning("portrait of %s failed its check: %s", name, chk1.issues)
            if may_retry is not None and not may_retry():
                note = "check failed, kept (no time)"
            else:
                second = await draw(chk1.issues or _fallback_fixes(name, chk1, expression))
                if second is None:
                    note = "check failed, regeneration failed - kept the first"
                else:
                    chk2 = await check_portrait(grader, second[0].png, name=name,
                                                description=description,
                                                expression=expression, record=record,
                                                page=page)
                    if chk2 is None or chk2.passed:
                        chosen = second
                        note = "regenerated, " + ("check passed" if chk2 else "unchecked")
                    else:
                        chosen = second if chk2.failures <= chk1.failures else first
                        note = (f"failed its check twice - kept attempt "
                                f"{2 if chosen is second else 1}")
    log.info("portrait done %s expression=%s (%s) duration=%.2fs", name, expression, note,
             time.perf_counter() - t0)
    return chosen
