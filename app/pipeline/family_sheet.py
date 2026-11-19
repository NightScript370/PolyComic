"""Family match + per-book family reference sheet, shared by the book pipeline and HW1.

* ``match_family``: one vision call on the photo -> ``PhotoTraits`` -> closest family
  variant (``app.pipeline.family``). A failed call falls back to the story's first variant
  (or the fixed one) and is recorded; it never raises (except cancellation).
* ``make_family_sheet``: ONE image call that draws every family member of the story side
  by side in the book's style and chosen variant (no hero, plain background). Pages that
  show a family member get it as a reference so Mother / Grandma look the same on every
  page. A failed image call is logged, recorded and returns ``None``. With a grader the
  sheet then gets ONE vision check (kind ``family_sheet_check``: figure count, no child,
  apparent age, hair per character - a real run drew the ~30-year-old mother as an
  elderly grey-haired woman and pages copied it). A failed check regenerates the sheet
  ONCE with the check's notes as corrections (if ``may_retry()`` allows - the book stops
  that once its quality time budget is used up) and checks it again; if that fails too,
  the sheet with fewer failed checks is kept (the second on a tie) and a warning logged.
  A failed check CALL accepts the sheet unchecked.
  Latency: the check is one vision call on one downscaled image (~2-5 s). The sheet runs
  beside the hero (traits ~2 s + hero generation + hero grade, often with a retry), so a
  passing sheet + check is normally ready before the hero and adds no delay; a
  regeneration (~15-25 s more) can delay only pages that show a family member.
* ``hero_identity_prompt``: the hero prompt with the identity line built from the photo
  traits (``match_family``'s result). Both pipelines wait for the traits before the
  first hero call and build every hero prompt through it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from app.pipeline.calls import CallCost, Record, short_error
from app.pipeline.family import choose_family_variant, default_family_variant, identity_block
from app.pipeline.prompts import family_expectations, family_sheet_prompt, hero_prompt
from app.providers.base import ImageResult, PhotoTraits, SheetCheck, VisionGrader
from app.stories.loader import Character, Page, Story

log = logging.getLogger("app.pipeline.family_sheet")

FAMILY_SHEET_KIND = "family_sheet"
FAMILY_SHEET_CHECK_KIND = "family_sheet_check"
# image_call(kind, page, prompt, refs) -> ImageResult; records the call, raises on failure.
ImageCall = Callable[[str, int | None, str, list[bytes]], Awaitable[ImageResult]]


def family_characters(story: Story) -> list[Character]:
    """Every character flagged ``family: true`` in the story YAML, in YAML order."""
    return [c for c in story.characters.values() if c.family]


def page_family(story: Story, page: Page) -> list[Character]:
    return [story.characters[c] for c in page.characters
            if c in story.characters and story.characters[c].family]


def sheet_names(chars: list[Character]) -> str:
    """'Mother' / 'Mother and Grandma' (who must match the sheet, for the ref line)."""
    names = [c.name for c in chars]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def hero_identity_prompt(style: str, story: Story, traits: PhotoTraits | None,
                         corrections: Sequence[str] = ()) -> str:
    """Hero prompt for ``story`` with the photo's identity block: a CHILD VERSION rule
    when the photo shows a teen or adult, the identity line ("The child has brown skin and
    curly black hair - keep exactly that.") and the personal items kept on every page
    (glasses, headwear) - or without it when the traits are unknown (no grader, or the
    traits call failed). The outfit is always the story's hero outfit."""
    return hero_prompt(style, story.hero_outfit, corrections, identity=identity_block(traits))


async def match_family(grader: VisionGrader | None, photo: bytes, story: Story, record: Record,
                       fixed_variant: str | None = None) -> tuple[PhotoTraits | None, str]:
    """(traits, variant). Traits are measured whenever a grader exists (the hero identity
    checks use them too); ``fixed_variant`` skips only the variant choice."""
    traits: PhotoTraits | None = None
    if grader is not None:
        t0 = time.perf_counter()
        try:
            traits = await grader.photo_traits(photo)
        except asyncio.CancelledError:
            try:
                await record(CallCost.failed("family_match", None, grader.model, t0,
                                             grader.name, "cancelled"))
            except Exception:
                log.exception("could not record cancelled family match")
            raise
        except Exception as exc:
            log.warning("family match failed, using the default variant: %s", short_error(exc))
            await record(CallCost.failed("family_match", None, grader.model, t0, grader.name,
                                         short_error(exc)))
        else:
            await record(CallCost.of_meta("family_match", None, traits.meta, grader.name))
    if fixed_variant is not None:
        return traits, fixed_variant
    if grader is None or not story.family_variant_ids:
        return traits, default_family_variant(story)
    return traits, choose_family_variant(story, traits)


async def check_family_sheet(grader: VisionGrader, png: bytes, characters: list[str],
                             record: Record) -> SheetCheck | None:
    """One recorded vision call (kind ``family_sheet_check``); None if it failed (the sheet
    is then accepted unchecked) or the grader cannot check sheets."""
    check = getattr(grader, "check_family_sheet", None)
    if check is None:
        return None
    t0 = time.perf_counter()
    try:
        result = await check(png, characters=characters)
    except asyncio.CancelledError:
        try:
            await record(CallCost.failed(FAMILY_SHEET_CHECK_KIND, None, grader.model, t0,
                                         grader.name, "cancelled"))
        except Exception:
            log.exception("could not record cancelled family sheet check")
        raise
    except Exception as exc:
        log.warning("family sheet check failed (sheet used unchecked): %s", short_error(exc))
        await record(CallCost.failed(FAMILY_SHEET_CHECK_KIND, None, grader.model, t0,
                                     grader.name, short_error(exc)))
        return None
    await record(CallCost.of_meta(FAMILY_SHEET_CHECK_KIND, None, result.meta, grader.name))
    return result


async def _draw(image_call: ImageCall, style: str, story: Story, chars: list[Character],
                variant: str, corrections: Sequence[str] = ()) -> bytes | None:
    prompt = family_sheet_prompt(style, chars, variant, corrections)
    try:
        img = await image_call(FAMILY_SHEET_KIND, None, prompt, [])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("family sheet failed (pages continue without it) story=%s variant=%s: %s",
                  story.id, variant, short_error(exc))
        return None
    return img.png


async def make_family_sheet(image_call: ImageCall, style: str, story: Story,
                            variant: str, *, grader: VisionGrader | None = None,
                            record: Record | None = None,
                            may_retry: Callable[[], bool] | None = None) -> bytes | None:
    """Image call (kind ``family_sheet``) -> PNG, or None if the story has no family or the
    call failed (already recorded by ``image_call``). With ``grader`` and ``record`` the
    sheet is checked once and, if the check fails and ``may_retry()`` (default: yes)
    allows, regenerated once with the check's notes (see the module docstring)."""
    chars = family_characters(story)
    if not chars:
        return None
    t0 = time.perf_counter()
    first = await _draw(image_call, style, story, chars, variant)
    if first is None:
        return None
    chosen, note = first, "unchecked"
    if grader is not None and record is not None:
        expected = family_expectations(chars, variant)
        chk1 = await check_family_sheet(grader, first, expected, record)
        if chk1 is not None and chk1.passed:
            note = "check passed"
        elif chk1 is not None:
            log.warning("family sheet failed its check story=%s variant=%s: %s", story.id,
                        variant, chk1.issues)
            if may_retry is not None and not may_retry():
                log.warning("family sheet: no time left for a regeneration - keeping the "
                            "sheet that failed its check")
                note = "check failed, kept (no time)"
            else:
                second = await _draw(image_call, style, story, chars, variant,
                                     chk1.issues or _fallback_fixes(chk1))
                if second is None:
                    note = "check failed, regeneration failed - kept the first"
                else:
                    chk2 = await check_family_sheet(grader, second, expected, record)
                    if chk2 is None or chk2.passed:
                        chosen = second
                        note = "regenerated, " + ("check passed" if chk2 else "unchecked")
                    else:
                        chosen = second if chk2.failures <= chk1.failures else first
                        note = (f"failed its check twice - kept attempt "
                                f"{2 if chosen is second else 1}")
                        log.warning("family sheet failed its check twice story=%s "
                                    "variant=%s: %s - keeping attempt %d", story.id, variant,
                                    chk2.issues, 2 if chosen is second else 1)
    log.info("family sheet done story=%s variant=%s characters=%s (%s) duration=%.2fs",
             story.id, variant, [c.id for c in chars], note, time.perf_counter() - t0)
    return chosen


def _fallback_fixes(chk: SheetCheck) -> list[str]:
    """Corrections when the check failed without notes."""
    out = []
    if not chk.count_ok:
        out.append("draw exactly the listed family members, no extra people")
    if not chk.no_child:
        out.append("no child on the sheet")
    if not chk.age_ok:
        out.append("every figure must look exactly the apparent age given above")
    if not chk.hair_ok:
        out.append("every figure's hair colour and hairstyle must match the hair given above")
    return out
