"""The per-picture quality loop: generate -> grade -> regenerate (max N) -> pick the best.

Pure control flow; the callers (``app.pipeline.book``) supply the generate / check
callables and record the provider calls. Rules:

* Up to ``max_attempts`` generations in total (default 3, ``QUALITY_MAX_ATTEMPTS``).
* The loop stops at the first attempt that passes the check.
* A failed check feeds the grader's issues into the next prompt as corrections; a
  text-placement failure (every text zone would cover a face or a key object) also
  switches the next prompt to a stronger quiet-zone rule naming that zone and item.
* A failed generation (provider error / safety block) uses up an attempt; it is retried
  unless the error is permanent (bad request / auth). If no attempt produced a picture,
  the last error is raised.
* A definite contradiction of the description (the grader's ``contradictions``: wrong
  count or colour, a missing must-be-visible item, the wrong character, a prop that does
  not match its Props text) fails the picture whatever the scores; the contradictions
  lead the next attempt's corrections, ahead of everything else. Softer ``issues`` do
  not fail a picture on their own.
* An identity mismatch with the ORIGINAL PHOTO (skin tone, hair colour or hair type)
  fails the picture whatever the likeness score, with a concrete correction built from
  the photo traits ("the hero's hair must be straight and black like in the photo").
  The same goes for the hero's hair vs. the HERO REFERENCE (``hero_hair_match``: length,
  shape, style, colour - wind never lengthens or restyles it) and for family members vs.
  the family reference sheet (``family_match``: apparent age, hair colour, hairstyle);
  the family correction names each family member's expected age and hair.
* The hero look checks are definite identity errors too: the hero must be a CHILD (no
  adult face, beard or wrinkles - an adult photo is drawn as a child version), wear the
  personal items kept from the photo (glasses, headwear) and wear the story's hero outfit
  (never the photo's adult clothes or another character's clothes).
* A supporting character that does not match its reference sheet / description (look,
  outfit for the page, size relative to the hero: ``cast_match``) or a hero expression
  that clearly differs from the requested emotion (``expression_match``) also fails the
  picture, with a concrete correction (``cast_hint`` / ``expression_hint``).
* The rendering style (``style_match``: a flat 2D painting in a low-poly 3D book) and the
  other characters' faces on this page (``cast_expression_match``: the wolf grinning when
  he must look dazed) are definite errors too, with corrections naming the technique
  (``style_hint``) and the expected faces (``cast_expression_hint``).
* Cost cap (``can_afford(definite)``, see ``app.pipeline.book``): before every further
  generation once a picture exists, the loop asks whether the book can afford one more
  generation + grade. ``definite`` says whether the last check failed for a definite
  error (those may use the whole cap; a retry for soft issues only must leave a reserve).
  If not, the best attempt is kept and the result is marked ``cost_stopped`` (phase
  ``stopped``).
* Time budget (``budget_exhausted``, see ``app.pipeline.book``): before attempt n > 1 the
  loop asks whether the book's quality-time budget has run out. If it has, and at least
  ``min_attempts`` generations were made and one of them produced a picture, no further
  generation starts: the best attempt so far is kept and the result is marked
  ``budget_stopped`` (phase ``stopped``). A generation already running always finishes
  and is checked. Without a budget the loop makes up to ``max_attempts`` generations.
* Budget exception for definite errors (``within_hard_cap``): when the budget has run
  out but the best attempt so far has a DEFINITE error (``Check.definite``: a
  contradiction such as a duplicate or a wrong count, a blank/blurred band, an identity
  mismatch incl. hero hair, family or supporting character), the picture may make ONE
  more generation, but only while ``within_hard_cap()`` (the book's elapsed time is under
  ``QUALITY_HARD_CAP_S``) and ``max_attempts`` is not reached. The result records it
  (``extra_retry``); if that generation fails too, the loop stops as ``budget_stopped``.
* Hard cap (``can_finish``): once a picture has an image, no further generation starts
  unless it is expected to end (generation + grade) before ``QUALITY_HARD_CAP_S`` - also
  before the budget has run out and also for the hero's ``min_attempts``. The loop then
  stops as ``budget_stopped`` with the best attempt.
* A check may fail before any grading (``Check.local``, e.g. a blank band along an edge
  of the picture): it counts as a failed attempt with that issue and ranks below every
  graded attempt.
* Retry admission (``admit``, the book's retry phase, see ``app.pipeline.book``): before
  every further generation once a picture exists, the loop first waits for its turn
  (``await admit(n, definite, best)`` returns a ticket); the time / cost / budget rules
  above are then decided, the ticket is told whether the generation started
  (``ticket.decided(started)``) and, once that generation is checked (or failed),
  ``ticket.finished()``. Without ``admit`` the loop retries at once (the old behaviour).
* A check may replace the picture (``Check.image``, e.g. a free strip rescue: the padded
  strip cropped away and graded, no new image call); the attempt then keeps that picture
  and is marked ``rescued``.
* If nothing passes, the attempt with the best score wins:
  ``0.5 * likeness + 0.5 * fidelity`` minus 0.25 for text artifacts, minus 0.3 for an
  identity mismatch, minus 0.15 per contradiction (at most 0.45) and minus 0.2 when the
  text would cover a face or key object. An unchecked picture (grader failed) is
  accepted as is.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from app.pipeline.family import (
    hair_colour_phrase,
    hair_texture_phrase,
    kept_items_phrase,
    skin_phrase,
)
from app.pipeline.placement import Placement
from app.providers.base import GradeResult, ImageResult, PhotoTraits, ProviderError

log = logging.getLogger("app.pipeline.quality")

PERMANENT_STATUSES = frozenset({400, 401, 403, 404})
PLACEMENT_PENALTY = 0.2
MAX_ISSUES = 6

GENERATING, CHECKING, RETRYING = "generating", "checking", "retrying"
STOPPED = "stopped"  # the time budget ended the loop before another generation
LOCAL_FAIL_SCORE = -1.0  # a picture rejected by a local check (no grade) ranks last
HERO_HAIR_FIX = ("the hero's hair must have exactly the same length, shape, style and colour "
                 "as in the hero reference image - wind or motion never makes it longer, "
                 "looser or differently styled")
FAMILY_FIX = ("every family member must look exactly like the family reference sheet: the "
              "same apparent age, hair colour, hairstyle, face and clothes")
CAST_FIX = ("every supporting character must look exactly like their reference sheet and "
            "description, wear what this page says, and be the stated size next to the hero "
            "child")
HERO_CHILD_FIX = ("the hero must be the CHILD from the hero reference, about 7 to 9 years old - "
                  "a child's face and body, no beard, moustache or wrinkles, never a grown-up "
                  "or elderly person")
EXPRESSION_FIX = ("the hero's expression must clearly show the emotion this page asks for - "
                  "the hero reference sets who the hero is, not the expression")
STYLE_FIX = ("render the whole picture in exactly the same art style as the hero reference "
             "image - never a different technique such as a flat 2D painting")
HOOD_FIX = ("the hero's hood must stay DOWN on the shoulders, never pulled up over the head, "
            "so the hero's own headwear and glasses are fully visible")
CAST_EXPRESSION_FIX = ("every other character's face must show this page's expression, not "
                       "their reference sheet's")


@dataclass
class Check:
    passed: bool
    grade: GradeResult | None = None  # None = not graded (no grader, or grader failed)
    issues: list[str] = field(default_factory=list)
    placement: Placement | None = None
    local: bool = False  # failed a local (no-API) check before grading, e.g. a blank band
    # A definite, objective error (not a low score): a local band failure, a contradiction
    # (duplicate, wrong count / colour, missing item ...), an identity mismatch (hero vs.
    # photo or reference, family, supporting character). Allows the budget exception.
    definite: bool = False
    # A replacement picture for this attempt (a strip rescue: the same generation with its
    # padded strip cropped away), used instead of the generated one; ``rescued`` marks it.
    image: ImageResult | None = None
    rescued: bool = False

    @property
    def unchecked(self) -> bool:
        return self.grade is None and not self.local


@dataclass
class Attempt:
    n: int
    image: ImageResult | None = None
    check: Check | None = None
    error: BaseException | None = None

    @property
    def png(self) -> bytes | None:
        return self.image.png if self.image else None

    @property
    def passed(self) -> bool:
        return bool(self.check and self.check.passed)

    @property
    def rescued(self) -> bool:
        return bool(self.check and self.check.rescued)

    @property
    def score(self) -> float:
        if self.image is None:
            return float("-inf")
        if self.check is not None and self.check.local:
            return LOCAL_FAIL_SCORE
        if self.check is None or self.check.grade is None:
            return 0.0
        s = self.check.grade.score
        if self.check.placement is not None and self.check.placement.blocked:
            s -= PLACEMENT_PENALTY
        return s


@dataclass
class LoopResult:
    chosen: Attempt
    attempts: list[Attempt]
    budget_stopped: bool = False  # the time budget stopped further generations
    extra_retry: bool = False  # one generation was allowed past the budget (definite error)
    cost_stopped: bool = False  # the book's cost cap stopped further generations

    @property
    def count(self) -> int:
        return len(self.attempts)


def is_retryable(exc: BaseException) -> bool:
    """Retry transient failures; not bad requests / auth, nor a 429 that means the key has
    no quota at all (billing not enabled), which fails the same way every time."""
    return (isinstance(exc, ProviderError) and exc.status not in PERMANENT_STATUSES
            and not exc.quota_exhausted)


_HAIR_WORDS = ("hair must be", "hair should be", "hair is not", "hair colour", "hair color",
               "hair type", "hair texture")


def tolerate_headwear(grade: GradeResult, traits: PhotoTraits | None) -> GradeResult:
    """When headwear hides most of the hair in the photo, the photo-based hair checks
    (colour, type) cannot be judged: a 'hair must be curly and grey like in the photo'
    fail on a hero whose skullcap covers the hair is a false alarm. Clears them (and
    their identity note, and grader contradictions that only ask for the photo's hair)
    in place; the hero-reference hair check (``hero_hair_match``) still applies."""
    if traits is None or not traits.hair_covered:
        return grade
    if not (grade.hair_colour_match and grade.hair_texture_match):
        grade.hair_colour_match = grade.hair_texture_match = True
        if any(w in grade.identity_notes.lower() for w in _HAIR_WORDS):
            grade.identity_notes = ""
    grade.contradictions = [c for c in grade.contradictions
                            if not ("photo" in c.lower()
                                    and any(w in c.lower() for w in _HAIR_WORDS))]
    return grade


def hero_look_issues(grade: GradeResult, traits: PhotoTraits | None = None,
                     hero_outfit: str = "") -> list[str]:
    """Corrections for failed hero look checks (child, personal items, outfit)."""
    out: list[str] = []
    if not grade.hero_child_match:
        out.append(HERO_CHILD_FIX)
    if not grade.hero_items_match:
        items = kept_items_phrase(traits)
        out.append(f"the hero must wear {items} exactly like in the hero reference - never "
                   "leave them out" if items else
                   "the hero must wear the same glasses and headwear as in the hero reference")
        if "hood" in grade.hero_look_notes.lower():
            out.append(HOOD_FIX)
    if not grade.hero_outfit_match:
        outfit = " ".join((hero_outfit or "").split())
        short = outfit[:220].rsplit(" ", 1)[0] + "..." if len(outfit) > 220 else outfit
        out.append("the hero must wear the hero outfit" + (f" ({short})" if short else "")
                   + " - never adult clothes such as a suit, and never another character's "
                   "clothes")
    return out


def identity_issues(grade: GradeResult, traits: PhotoTraits | None = None,
                    family_hint: str = "", hero_outfit: str = "") -> list[str]:
    """Concrete corrections for the identity checks that failed (empty if all match).
    ``family_hint`` (``app.pipeline.prompts.family_correction``) names what each family
    member on the page must look like; used when ``family_match`` failed. The hero look
    checks (child, personal items, outfit) are definite identity errors too."""
    out: list[str] = []
    if not grade.skin_tone_match:
        skin = skin_phrase(traits) if traits else ""
        out.append(f"the hero's skin must be {skin} like in the photo" if skin else
                   "the hero's skin tone must match the child in the photo exactly")
    if not (grade.hair_colour_match and grade.hair_texture_match):
        texture = hair_texture_phrase(traits) if traits else ""
        colour = hair_colour_phrase(traits) if traits else ""
        if not grade.hair_texture_match and not texture:
            texture = "the same type (straight, wavy, curly or coily)"
        if not grade.hair_colour_match and not colour:
            colour = "the same colour"
        hair = " and ".join(p for p in (texture, colour) if p)
        out.append(f"the hero's hair must be {hair} like in the photo")
    if not grade.hero_hair_match:
        out.append(HERO_HAIR_FIX)
    if out and grade.identity_notes.strip():
        out.append(grade.identity_notes.strip()[:200])
    look = hero_look_issues(grade, traits, hero_outfit)
    out += look
    if look and grade.hero_look_notes.strip():
        out.append(grade.hero_look_notes.strip()[:200])
    if not grade.family_match:
        out.append(family_hint.strip() or FAMILY_FIX)
        if grade.family_notes.strip():
            out.append(grade.family_notes.strip()[:200])
    return out


def evaluate(
    grade: GradeResult,
    *,
    min_likeness: float,
    min_fidelity: float,
    placement: Placement | None = None,
    traits: PhotoTraits | None = None,
    family_hint: str = "",
    cast_hint: str = "",
    expression_hint: str = "",
    hero_outfit: str = "",
    style_hint: str = "",
    cast_expression_hint: str = "",
) -> Check:
    """Pass/fail for one graded picture, with corrective hints for the next attempt.
    ``traits`` (the photo's measured skin tone / hair) make identity corrections concrete;
    ``family_hint`` does the same for a family mismatch, ``cast_hint`` for a supporting
    character mismatch and ``expression_hint`` for a wrong hero expression. When the photo's
    hair is covered by headwear (``traits.hair_covered``) the photo-based hair checks are
    not used (``tolerate_headwear``); ``hero_outfit`` makes an outfit correction concrete."""
    tolerate_headwear(grade, traits)
    contra = [c.strip() for c in grade.contradictions if c and c.strip()]
    identity = [i for i in identity_issues(grade, traits, family_hint, hero_outfit)
                if i not in contra]
    if not grade.style_match:
        identity.insert(0, style_hint.strip() or STYLE_FIX)
        if grade.style_notes.strip():
            identity.insert(1, grade.style_notes.strip()[:200])
    if not grade.cast_match:
        identity.append(cast_hint.strip() or CAST_FIX)
        if grade.cast_notes.strip():
            identity.append(grade.cast_notes.strip()[:300])
    if not grade.cast_expression_match:
        identity.append(cast_expression_hint.strip() or CAST_EXPRESSION_FIX)
        if grade.cast_expression_notes.strip():
            identity.append(grade.cast_expression_notes.strip()[:300])
    definite = bool(contra or identity)
    if not grade.expression_match:
        identity.append(expression_hint.strip() or EXPRESSION_FIX)
        if grade.expression_notes.strip():
            identity.append(grade.expression_notes.strip()[:200])
    found = [i for i in grade.issues if i not in identity and i not in contra]
    tail: list[str] = []  # our own hints; never crowded out by the grader's issues
    ok = not identity and not contra
    if grade.likeness < min_likeness:
        ok = False
        if not grade.issues and not identity and not contra:
            tail.append("the hero child must look like the child in the reference images: "
                        "same face shape, skin tone, eye colour, hair colour and hair style")
    if grade.fidelity < min_fidelity:
        ok = False
        if not grade.issues and not contra:
            tail.append("follow the scene description exactly and show every item that "
                        "must be clearly visible")
    if grade.has_text_artifacts:
        ok = False
        tail.append("remove every letter, word, number, sign and speech bubble")
    if placement is not None and placement.blocked:
        ok = False
        tail.append(placement.retarget.issue())
    # Order: contradictions first (definite errors), then identity, the grader's softer
    # notes (only as many as fit) and our own hints.
    room = max(0, MAX_ISSUES - len(contra) - len(identity) - len(tail))
    issues = (contra + identity + found[:room] + tail)[:MAX_ISSUES]
    return Check(passed=ok, grade=grade, issues=issues, placement=placement,
                 definite=definite)


def pick_best(attempts: Sequence[Attempt]) -> Attempt:
    with_image = [a for a in attempts if a.image is not None]
    passed = [a for a in with_image if a.passed]
    if passed:
        return passed[0]
    # max() keeps the first of equal scores -> earliest attempt wins ties
    return max(with_image, key=lambda a: a.score)


async def run_quality_loop(
    *,
    generate: Callable[[int, Sequence[str], Placement | None], Awaitable[ImageResult]],
    check: Callable[[int, ImageResult], Awaitable[Check]],
    max_attempts: int,
    on_phase: Callable[[int, str, Check | None], Awaitable[None]] | None = None,
    on_attempt: Callable[[Attempt], Awaitable[None]] | None = None,
    budget_exhausted: Callable[[], bool] | None = None,
    min_attempts: int = 1,
    within_hard_cap: Callable[[], bool] | None = None,
    can_finish: Callable[[], bool] | None = None,
    can_afford: Callable[[bool], bool] | None = None,
    admit: Callable[[int, bool, Attempt], Awaitable[object]] | None = None,
) -> LoopResult:
    """``generate(n, corrections, quiet)`` makes attempt ``n`` (1-based); ``quiet`` is the
    last blocked ``Placement`` (zone + blocking item) or None. ``check(n, image)`` grades
    it. ``budget_exhausted()`` (optional) is asked before every further generation; it
    cannot stop the loop before ``min_attempts`` generations nor before a first picture.
    ``within_hard_cap()`` (optional) allows the one extra generation past the budget for
    a definite error. ``can_finish()`` (optional) is asked before every further generation
    once a picture exists: False (the generation and its grade would end past the hard
    cap) stops the loop whatever ``min_attempts`` says. ``can_afford(definite)``
    (optional) is asked the same way: False (the book's cost cap) stops the loop and marks
    the result ``cost_stopped``. ``admit(n, definite, best)`` (optional) waits for the
    retry's turn before those checks and returns a ticket (``decided(started)``,
    ``finished()``, both async). See the module docstring."""
    max_attempts = max(1, max_attempts)
    stopped = False
    extra_used = False
    cost_stopped = False
    corrections: Sequence[str] = ()
    quiet_zone: Placement | None = None
    attempts: list[Attempt] = []

    async def phase(n: int, name: str, chk: Check | None = None) -> None:
        if on_phase is not None:
            await on_phase(n, name, chk)

    async def done(a: Attempt) -> None:
        attempts.append(a)
        if on_attempt is not None:
            await on_attempt(a)

    def out_of_time(made: int, definite: bool = False) -> bool:
        nonlocal extra_used, cost_stopped
        has_picture = any(a.image is not None for a in attempts)
        if has_picture and can_finish is not None and not can_finish():
            log.info("no further generation after attempt %d: it would end past the hard "
                     "cap - keeping the best so far", made)
            return True
        if has_picture and can_afford is not None and not can_afford(definite):
            log.info("no further generation after attempt %d: the book's cost cap - keeping "
                     "the best so far", made)
            cost_stopped = True
            return True
        if not (budget_exhausted is not None and made >= min_attempts
                and any(a.image is not None for a in attempts) and budget_exhausted()):
            return False
        if extra_used or within_hard_cap is None:
            return True
        best = pick_best(attempts)
        if best.check is not None and best.check.definite and within_hard_cap():
            extra_used = True
            log.info("time budget used up after attempt %d, but the best attempt has a "
                     "definite error: one extra generation (hard cap not reached)", made)
            return False
        return True

    ticket = None  # the admitted retry in flight (retry phase), finished after its check

    async def finish_ticket() -> None:
        nonlocal ticket
        t, ticket = ticket, None
        if t is not None:
            await t.finished()

    async def stop(made: int, definite: bool = False) -> bool:
        """True = no further generation. With ``admit``, waits for the retry's turn first
        (only once a picture exists: a picture without one always retries at once)."""
        nonlocal ticket
        has_picture = any(a.image is not None for a in attempts)
        if admit is None or not has_picture:
            return out_of_time(made, definite)
        t = await admit(made + 1, definite, pick_best(attempts))
        try:
            no = out_of_time(made, definite)
        except BaseException:
            await t.decided(False)
            raise
        await t.decided(not no)
        if not no:
            ticket = t
        return no

    try:
        for n in range(1, max_attempts + 1):
            await phase(n, GENERATING)
            try:
                image = await generate(n, corrections, quiet_zone)
            except asyncio.CancelledError:
                raise
            except ProviderError as exc:
                await done(Attempt(n, error=exc))
                await finish_ticket()
                if not is_retryable(exc) or n == max_attempts:
                    break
                if await stop(n):
                    stopped = True
                    await phase(n, STOPPED)
                    break
                await phase(n, RETRYING)
                continue
            await phase(n, CHECKING)
            chk = await check(n, image)
            await done(Attempt(n, image=chk.image or image, check=chk))
            await finish_ticket()
            if chk.passed:
                break
            if n < max_attempts:
                if await stop(n, chk.definite):
                    stopped = True
                    log.info("no further generation after attempt %d: keeping the best so "
                             "far", n)
                    await phase(n, STOPPED, chk)
                    break
                await phase(n, RETRYING, chk)
                corrections = chk.issues
                if chk.placement is not None and chk.placement.blocked:
                    quiet_zone = chk.placement.retarget
    finally:
        await finish_ticket()

    if not any(a.image is not None for a in attempts):
        raise attempts[-1].error  # type: ignore[misc]
    return LoopResult(chosen=pick_best(attempts), attempts=attempts,
                      budget_stopped=stopped and not cost_stopped, extra_retry=extra_used,
                      cost_stopped=cost_stopped)
