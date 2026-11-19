"""Book pipeline: photo -> family match + stylised hero -> N story pages, each through the
quality loop (generate -> grade -> regenerate, max 3 generations -> best).

Stages and provider calls (all recorded through ``Hooks.call``):

1. **hero** - concurrently:
   a. family match: one vision call on the photo -> ``PhotoTraits`` -> closest family
      variant (``app.pipeline.family``); falls back to the story's first variant.
   b. family sheet (``app.pipeline.family_sheet``): right after (a), ONE image call that
      draws every family member of the story in the chosen variant - unless pre-generated
      bible sheets exist for all of them, or no planned page shows a family member. No
      quality loop; a failure is recorded and the book continues without it. The sheet
      gets one vision check (``family_sheet_check``: count, no child, apparent age, hair)
      and, if that fails and the time budget is not used up, ONE regeneration with the
      check's notes (then the better of the two is kept).
   c. hero: image call(s) [photo] -> character reference, graded for likeness and
      identity (skin tone, hair colour, hair type) vs. the photo, regenerated up to
      ``max_attempts``. Every attempt, the first included, waits for the photo traits
      from (a) (~2 s) and spells them out ("The child has brown skin and curly black hair
      - keep exactly that."); if the traits call fails the hero starts without them. The
      family sheet still runs beside the hero.
   d. supporting-character sheets (``app.pipeline.cast_sheets``): for every
      (character, costume) sheet the planned pages use, the stored sheet - or, the first
      time a story + style needs it and only with a grader, ONE image call (kind
      ``cast_sheet``) + one vision check (``cast_sheet_check``) and at most one
      regeneration, stored for every later book. Runs beside the hero; a page waits only
      for the sheets it uses.
2. **pages** - every page concurrently (process-wide limit ``IMAGE_CONCURRENCY`` on image
   calls), as soon as the hero is done; a page that shows a family member also waits for
   the family sheet (normally finished before the hero). References per page, in order:
   [hero, background plate (if pre-generated), family sheet (family pages without bible
   sheets), bible refs for family members in the chosen variant, and - without a plate -
   the reference sheets of at most ``MAX_CAST_REFS`` supporting characters in the costume
   for that page], capped to the provider's maximum by priority hero > plate > family
   sheet > characters > supporting sheets. The photo is NOT sent with pages: the hero
   reference is the identity anchor (a CHILD VERSION of the person when the photo shows
   a teen or adult, with their glasses / headwear and the story outfit); page prompts
   repeat the kept personal items and the grader checks the hero look on every page.
   Without a plate (or sheets) the page falls back to the M3 behaviour (characters
   described in text) and logs a warning.
   Each page is graded ([page, hero, photo (+ the family reference - the sheet, or the
   family member's bible sheet - on pages with a family member)] + caption +
   must-be-visible + the page's canonical props + photo traits + each family member's
   expected age and hair + the supporting characters' sheets and lines (look, outfit on
   this page, size relative to the hero) + the hero's requested expression: likeness,
   identity incl. the hero's hair vs. the hero reference and the family vs. their
   reference, cast match, expression match, fidelity, contradictions), its
   text zone is chosen from the grader's boxes
   (``app.pipeline.placement``) and a zone that would cover a face or a key object (or,
   for the middle band, any character's body) counts as a failed check. Before the grade
   call a local check (``app.pipeline.bands``, no API) looks for a blank band along an
   edge; the padded picture is not graded. Instead a FREE STRIP RESCUE
   (``app.pipeline.rescue``) crops the strip away, re-crops to 3:4 and upscales; the
   result is band-checked again and graded (one ``rescue_grade`` call, no image call). A
   rescue that passes is the attempt (``rescued``); a failed or impossible one (strip
   deeper than STRIP_RESCUE_MAX_DEPTH, aspect crop over STRIP_RESCUE_MAX_CROP, band again)
   leads to a normal paid retry.
3. **speaker portraits** (``app.pipeline.portraits``): a page with ``portrait: offscreen``
   asks its grade calls which characters are visible (``presence``); after the page's loop, if
   the CHOSEN attempt's grade confirms the speaker is NOT in the picture, the page gets a
   small portrait of the speaker for its dialogue box - the stored one (supporting
   characters, per story x style x costume x expression), or ONE image call + check (at
   most one regeneration), only when it can end before the hard cap. Unknown presence =
   no portrait. The decision is logged per page. A ``portrait: moment`` page (line spoken
   at another moment than the picture) is not gated: its portrait starts right after the
   hero (``start_moment_portraits``), beside the pages, and is always shown if it exists.

Retry phase (``QUALITY_RETRY_PHASE``, default on, with a grader; ``RetryScheduler``):
every picture's FIRST attempt (image + band check / rescue + grade) runs first, all pages
in parallel. A page retry (and a NEW speaker portrait) waits until the first pass is over,
then the waiting requests are decided one at a time in priority order and run in parallel
(token limiter, image slots): definite errors first (identity / child / items / outfit,
contradictions incl. duplicates and padding, bands, style, cast or expression mismatch),
every page's 2nd attempt before any 3rd, most definite failures and lowest score first;
then speaker portraits; then soft-issue retries. Each decision uses the time / cost rules
below; nothing is "pending" then but the retries in flight, so the cost cap is used fully
but not passed. The head of the queue that does not fit while other retries run waits for
them (they may cost less than reserved) instead of being skipped. Exception: a page whose
attempt failed a LOCAL band check (rescue impossible) may start its retry during the first
pass (``QUALITY_EARLY_BAND_RETRY``) when the projection with this book's actual costs fits
and the token limiter has no queue. Each picture still makes at most ``max_attempts``.

Time budget (``QUALITY_TIME_BUDGET_S``, default 45 s, measured from the start of the
book job): once it has passed, a picture that fails its check starts no further
generation and keeps its best attempt so far (``budget_stopped``); a generation already
running finishes. The hero gets at least 2 generations (everything depends on it) unless
the hard cap forbids it. Until the budget runs out every picture gets up to
``max_attempts``. Exception: a picture whose best attempt has a DEFINITE error
(contradiction / duplicate, blank or blurred band, identity mismatch) may make ONE more
generation after the budget.

Hard cap (``QUALITY_HARD_CAP_S``, default 70 s): it bounds the END of work, not its
start. Before the fix a book could reach ~90 s: a retry started at 44 s (budget not yet
used up) or the definite-error extra started at 69.9 s still ran an image call (~12-20 s)
and a grade (~3-6 s); a rate-limit wait was allowed when it ended before the cap, so the
call after it ran past it. Now no retry generation, definite-error extra, sheet
regeneration, rate-limit wait or token-limiter queue is taken unless it is expected to
end (image call + grade, averaged over this book's calls: ``cycle_estimate``) before the
cap. Only a picture that has no image yet may go past it (the book needs one picture per
page).

Cost cap (``cost_cap_usd``, per tier: COST_CAP_COST_EFFICIENT_USD /
COST_CAP_PROFESSIONAL_USD): like the hard cap, but for money. Required calls always run:
the photo traits, the hero's first picture and grade, every page's first picture and
grade, the family sheet. Every OPTIONAL call - a retry generation (+ its grade), the
definite-error extra, the family sheet regeneration, a speaker portrait or its
regeneration, a rate-limit re-try of an optional call - first asks ``afford``: the book's
running estimated cost (``spent_usd``: every recorded call except the first-time SHARED
supporting-character sheets, which are amortised over all later books) + the remaining
expected cost of the pictures still in their first pass + the remaining expected cost of
retries / portraits already running + the call's own expected cost (image + its check:
the plain AVERAGE of this book's own page and grade calls, the pricing table only until
the first one is seen) must stay under the cap. Order of spending when money is tight: a
retry for a DEFINITE error (identity, duplicate / contradiction, band, style, a wrong
face) may use the whole cap; a portrait must leave ``PORTRAIT_RESERVE`` free and a retry
for soft issues only ``SOFT_RESERVE`` (in the retry phase portraits are decided before
soft retries, but need more room, so they are still dropped first). A skipped call
is logged with its reason, the picture keeps its best attempt (``cost_stopped`` on the page
and its kept attempt) and ``BookResult.cost_skips`` lists everything the cap skipped.

Input tokens: every image call's input tokens are estimated (prompt + downscaled refs,
``app.providers.refs``) and queued through a process-wide per-minute token limiter
(``IMAGE_INPUT_TOKENS_PER_MINUTE``), corrected with the usage the API reports.

A page that produces no picture at all after ``max_attempts`` fails the book; the other
pages' calls are cancelled (TaskGroup) and recorded as ``cancelled``.

``BookGenerator`` is storage-agnostic: the background job (``app.jobs.runner``) passes
hooks that persist and stream every step; ``run_book`` runs it without hooks for scripts
and tests. Without a grader there is no quality loop (one generation per picture), no
family match (first variant) and no family sheet, which is the old M3 behaviour.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import random
import time
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from app.config import get_settings
from app.pipeline import quality
from app.pipeline.bands import Band, find_blank_band
from app.pipeline.calls import CallCost, short_error
from app.pipeline.cast_sheets import (
    costume_label,
    make_cast_sheet,
    needed_sheets,
    sheet_lock,
    supporting,
)
from app.pipeline.family import (  # noqa: F401 - default_family_variant re-exported
    choose_family_variant,
    default_family_variant,
    kept_items,
    kept_items_phrase,
)
from app.pipeline.family_sheet import (
    family_characters,
    hero_identity_prompt,
    make_family_sheet,
    match_family,
    page_family,
    sheet_names,
)
from app.pipeline.placement import Placement, TextZone, place_text
from app.pipeline.portraits import (
    HERO_PRESENCE_NAME,
    PORTRAIT_ASPECT,
    PortraitKey,
    make_portrait,
    portrait_gate,
    portrait_key,
    presence_names,
)
from app.pipeline.prompts import (
    HERO_CAPTION,
    RefSpec,
    cast_correction,
    cast_expectations,
    cast_expression_correction,
    cast_expression_lines,
    expression_correction,
    family_correction,
    family_expectations,
    page_characters,
    page_props,
    scene_prompt,
    sheet_outfit_line,
)
from app.pipeline.rescue import rescue_strips
from app.providers.base import (
    GradeResult,
    ImageProvider,
    ImageResult,
    PhotoTraits,
    ProviderError,
    VisionGrader,
    max_reference_images,
)
from app.providers.pricing import estimate_cost
from app.providers.refs import estimate_input_tokens
from app.stories.loader import (
    HERO,
    PORTRAIT_MOMENT,
    Page,
    Story,
    get_layout,
    load_story,
    normalise_pronouns,
    render_text,
)
from app.styles.presets import (
    DEFAULT_DETAIL,
    build_style_string,
    style_correction,
    style_summary,
)

log = logging.getLogger("app.pipeline.book")

ASPECT_RATIO = "3:4"
HERO_PAGE = 0  # page number used for the hero in attempts/events
# Order and labels used on the About Me page.
FAVOURITE_KEYS: tuple[tuple[str, str], ...] = (
    ("colour", "Colour"),
    ("food", "Food"),
    ("game", "Game"),
    ("animal", "Animal"),
)
HERO_MUST_SEE = ["hero face", "the whole body from head to feet"]
# The time budget never cuts the hero below this many generations (pages: 1).
HERO_MIN_ATTEMPTS = 2
# Supporting-character sheets sent with one page (more refs make calls slower), chosen by
# ``Character.ref_priority`` (large humanoid characters first, small animals / props last):
# with 2 in page order, Jack p14 (harp, hen, giant) sent no giant sheet and his clothes
# changed. References are downscaled and sent at medium resolution (~560 tokens each), so
# a third sheet adds ~560 input tokens to the call.
MAX_CAST_REFS = 3
# Calls that are shared by every later book of the story + style (first-time supporting
# character sheets): recorded, but not counted against a book's cost cap.
SHARED_KINDS = frozenset({"cast_sheet", "cast_sheet_check"})
# Calls of a picture's generation cycle (image + its check, incl. a strip-rescue grade).
CYCLE_KINDS = frozenset({"hero", "page", "grade", "rescue_grade"})
PORTRAIT_KINDS = frozenset({"portrait", "portrait_check"})
# Retry-phase priority tiers (lower first): definite errors, speaker portraits, soft issues.
TIER_DEFINITE, TIER_PORTRAIT, TIER_SOFT = 0, 1, 2
# Share of the cost cap a retry for SOFT issues only, or a speaker portrait, must leave
# free (definite errors may use the whole cap; portraits are dropped first).
SOFT_RESERVE = 0.03
PORTRAIT_RESERVE = 0.06
# Seeds for the expected cost of one page image call and one grade call (input tokens),
# replaced by this book's own averages.
PAGE_INPUT_TOKENS_EST = 5500
GRADE_INPUT_TOKENS_EST = 5000
GRADE_OUTPUT_TOKENS_EST = 900


@dataclass(frozen=True)
class HeroDetails:
    name: str
    pronouns: str  # he | she | they
    age: int | None = None
    favourites: dict[str, str] = field(default_factory=dict)  # keys from FAVOURITE_KEYS
    about: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "pronouns", normalise_pronouns(self.pronouns))

    def filled_favourites(self) -> list[tuple[str, str]]:
        return [(label, self.favourites[k].strip()) for k, label in FAVOURITE_KEYS
                if (self.favourites.get(k) or "").strip()]


@dataclass
class BookPage:
    page: int
    title: str
    caption: str  # scene spec sent to the model
    text: str  # rendered page text (name + pronouns filled in)
    layout: str
    zone: TextZone
    png: bytes
    attempts: int = 1
    scores: dict | None = None  # {likeness, fidelity, passed} of the chosen attempt
    chosen_attempt: int = 1
    used_plate: bool = False
    budget_stopped: bool = False  # the time budget stopped further generations
    cost_stopped: bool = False  # the book's cost cap stopped further generations
    # Speaker portrait for the dialogue box (only when the grader confirmed the speaker
    # is not in the picture) and which one it is: {character, costume, expression}.
    portrait_png: bytes | None = None
    portrait: dict | None = None
    # Characters the grader saw in the chosen picture (portrait pages only; None =
    # not asked / unknown).
    visible: list[str] | None = None


@dataclass
class HeroOutcome:
    png: bytes
    attempts: int
    scores: dict | None
    chosen_attempt: int
    image: ImageResult
    budget_stopped: bool = False
    cost_stopped: bool = False


@dataclass
class BookResult:
    story: Story
    preset: str
    detail: int
    hero_details: HeroDetails
    family_variant: str
    style: str  # the locked style string used for every call
    hero_png: bytes
    pages: list[BookPage]
    costs: list[CallCost]
    hero_attempts: int = 1
    hero_scores: dict | None = None
    timings: dict[str, float] = field(default_factory=dict)
    family_sheet_png: bytes | None = None
    # Optional calls the cost cap skipped ("page 7 retry", "portrait p3 mother/angry").
    cost_skips: list[str] = field(default_factory=list)
    # Free strip rescues ("p6 a1 seam strip top cut 13%, aspect crop 13% -> passed").
    rescues: list[str] = field(default_factory=list)

    @property
    def est_cost_usd(self) -> float:
        return round(sum(c.est_cost_usd for c in self.costs), 4)

    @property
    def subtitle(self) -> str:
        return render_text(self.story.subtitle, self.hero_details.name,
                           self.hero_details.pronouns)


@dataclass(frozen=True)
class BookPlan:
    """Everything decided before the first provider call (validated, no I/O).

    ``family_variant`` is the story's first variant until the family match replaces it,
    unless it was given explicitly (``family_fixed``)."""

    story: Story
    preset: str
    detail: int
    style: str
    pages: tuple[Page, ...]
    family_variant: str
    family_fixed: bool = False


def _consume_result(fut: asyncio.Future) -> None:
    """Done-callback of a portrait nobody awaits (yet): never an unretrieved exception."""
    if not fut.cancelled() and fut.exception() is not None:
        log.warning("portrait task failed (box without portrait): %s", fut.exception())


def plan_book(
    story_id: str,
    pages: Sequence[int],
    preset: str,
    detail: int,
    family_variant: str | None = None,
) -> BookPlan:
    """Raises LookupError (unknown story/page) or ValueError (style/variant) before any call."""
    style = build_style_string(preset, detail)
    story = load_story(story_id)
    wanted = tuple(story.page(n) for n in pages)
    fixed = family_variant is not None
    if family_variant is None:
        family_variant = default_family_variant(story)
    elif family_variant not in story.family_variant_ids:
        raise ValueError(f"unknown family variant {family_variant!r} for {story.id}")
    return BookPlan(story=story, preset=preset, detail=detail, style=style, pages=wanted,
                    family_variant=family_variant, family_fixed=fixed)


# --- assets (plates + character bible) ------------------------------------------------


class AssetSource(Protocol):
    async def plate(self, page: int) -> bytes | None: ...

    async def characters(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], bytes]:
        """PNG per (character_id, variant_id); variant_id is "" for non-family."""
        ...

    # Optional (looked up with getattr): supporting-character sheets per costume.
    # async def sheet(self, character_id: str, costume_id: str) -> bytes | None
    # async def save_sheet(self, character_id: str, costume_id: str, png: bytes,
    #                      prompt: str, model: str) -> bytes   (returns the stored sheet)
    # Optional (getattr): supporting-character speaker portraits per costume x expression.
    # async def portrait(self, character_id, costume_id, expression) -> bytes | None
    # async def save_portrait(self, character_id, costume_id, expression, png, prompt,
    #                         model) -> bytes   (returns the stored portrait)


class NoAssets:
    async def plate(self, page: int) -> bytes | None:
        return None

    async def characters(self, keys):
        return {}

    async def sheet(self, character_id: str, costume_id: str) -> bytes | None:
        return None

    async def save_sheet(self, character_id, costume_id, png, prompt, model) -> bytes:
        return png  # nowhere to keep it: this book uses it, the next one makes its own

    async def portrait(self, character_id, costume_id, expression) -> bytes | None:
        return None

    async def save_portrait(self, character_id, costume_id, expression, png, prompt,
                            model) -> bytes:
        return png


@dataclass
class MemoryAssets:
    plates: dict[int, bytes] = field(default_factory=dict)
    bible: dict[tuple[str, str], bytes] = field(default_factory=dict)
    # supporting-character sheets per (character_id, costume_id); a plain sheet
    # (costume "") falls back to bible[(character_id, "")]
    sheets: dict[tuple[str, str], bytes] = field(default_factory=dict)
    # speaker portraits per (character_id, costume_id, expression)
    portraits: dict[tuple[str, str, str], bytes] = field(default_factory=dict)

    async def plate(self, page: int) -> bytes | None:
        return self.plates.get(page)

    async def portrait(self, character_id, costume_id, expression) -> bytes | None:
        return self.portraits.get((character_id, costume_id, expression))

    async def save_portrait(self, character_id, costume_id, expression, png, prompt,
                            model) -> bytes:
        return self.portraits.setdefault((character_id, costume_id, expression), png)

    async def characters(self, keys):
        return {k: self.bible[k] for k in keys if k in self.bible}

    async def sheet(self, character_id: str, costume_id: str) -> bytes | None:
        got = self.sheets.get((character_id, costume_id))
        if got is None and not costume_id:
            got = self.bible.get((character_id, ""))
        return got

    async def save_sheet(self, character_id, costume_id, png, prompt, model) -> bytes:
        return self.sheets.setdefault((character_id, costume_id), png)


def select_refs(
    hero_png: bytes,
    photo: bytes | None,
    plate: bytes | None,
    characters: Sequence[tuple[RefSpec, bytes]],
    cap: int,
) -> list[tuple[RefSpec, bytes]]:
    """Keep the highest-priority refs within ``cap`` (hero > plate > photo > characters,
    characters in the given order - the family sheet, if any, first) and return them in
    send order: hero, photo, plate, characters. Book pages pass ``photo=None``: the hero
    reference (a child version of the person) is the only identity source on pages."""
    prio: list[tuple[RefSpec, bytes]] = [(RefSpec("hero"), hero_png)]
    if plate is not None:
        prio.append((RefSpec("plate"), plate))
    if photo is not None:
        prio.append((RefSpec("photo"), photo))
    prio += list(characters)
    kept = prio[: max(1, cap)]
    order = {"hero": 0, "photo": 1, "plate": 2, "family_sheet": 3, "character": 3,
             "cast": 4}
    return sorted(kept, key=lambda r: order[r[0].role])  # stable: characters keep order


def family_reference(refs: Sequence[tuple[RefSpec, bytes]],
                     family_ids: Sequence[str]) -> tuple[bytes | None, list[str]]:
    """(image, character ids it shows) the grader compares the page's family members
    with: the family sheet if the page got it (it shows every family member), else the
    first family member's bible sheet (only that member is checked), else (None, [])
    - no family member on the page, or no reference image for them. One image keeps the
    grade call small."""
    if not family_ids:
        return None, []
    for spec, png in refs:
        if spec.role == "family_sheet":
            return png, list(family_ids)
    for spec, png in refs:
        if spec.role == "character" and spec.character_id in family_ids:
            return png, [spec.character_id]
    return None, []


# --- hooks -----------------------------------------------------------------------------


class Hooks:
    """Callbacks for progress and persistence. Defaults do nothing."""

    async def stage(self, stage: str, done: int) -> None: ...

    async def call(self, call: CallCost) -> None: ...

    async def family(self, variant: str, traits: PhotoTraits | None) -> None: ...

    async def family_sheet(self, png: bytes) -> None: ...

    async def attempt_phase(self, page: int, attempt: int, phase: str,
                            check: quality.Check | None) -> None: ...

    async def attempt_done(self, page: int, attempt: quality.Attempt) -> None: ...

    async def budget_stopped(self, page: int, chosen: quality.Attempt) -> None:
        """The time budget ended this picture's loop; ``chosen`` is kept."""

    async def cost_stopped(self, page: int, chosen: quality.Attempt) -> None:
        """The book's cost cap ended this picture's loop; ``chosen`` is kept."""

    async def hero_done(self, hero: HeroOutcome) -> None: ...

    async def page_done(self, page: BookPage) -> None: ...


_SLOTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def image_slots() -> asyncio.Semaphore:
    """Process-wide limit on image calls in flight (IMAGE_CONCURRENCY), one per event loop."""
    loop = asyncio.get_running_loop()
    sem = _SLOTS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, get_settings().image_concurrency))
        _SLOTS[loop] = sem
    return sem


class MinuteLimiter:
    """At most ``per_minute`` acquisitions in any 60 s window (sliding window)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self._starts: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self.clock()
                while self._starts and now - self._starts[0] >= 60.0:
                    self._starts.popleft()
                if len(self._starts) < self.per_minute:
                    self._starts.append(now)
                    return
                # short naps: the window is re-checked at least once a second
                await asyncio.sleep(min(60.0 - (now - self._starts[0]) + 0.01, 1.0))


_LIMITERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def image_rate() -> MinuteLimiter | None:
    """Process-wide IMAGE_MAX_PER_MINUTE limiter (one per event loop), None when off."""
    n = get_settings().image_max_per_minute
    if n <= 0:
        return None
    loop = asyncio.get_running_loop()
    lim = _LIMITERS.get(loop)
    if lim is None or lim.per_minute != n:
        lim = _LIMITERS[loop] = MinuteLimiter(n)
    return lim


class TokenLimiter:
    """At most ``per_minute`` estimated INPUT tokens of image calls in any 60 s window
    (sliding window; Google's ``GenerateContentPaidTierInputTokensPerModelPerMinute``).

    ``acquire(tokens)`` waits until the call fits, books it and returns a handle;
    ``settle(handle, actual)`` replaces the booking with the tokens the API reported and
    learns the ratio actual / estimate (``ratio``, EMA), which scales later estimates. A
    single call larger than the whole budget proceeds once the window is empty."""

    LEARN = 0.3  # weight of the newest actual/estimate ratio

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.ratio = 1.0
        self._booked: collections.deque[list] = collections.deque()  # [t, tokens]
        self._lock = asyncio.Lock()

    def scaled(self, estimate: int) -> int:
        return max(1, round(estimate * self.ratio))

    def _prune(self, now: float) -> None:
        while self._booked and now - self._booked[0][0] >= 60.0:
            self._booked.popleft()

    def used(self) -> int:
        self._prune(self.clock())
        return sum(b[1] for b in self._booked)

    def wait_estimate(self, estimate: int) -> float:
        """Seconds until a call of ``estimate`` (raw) tokens would fit (0 = now)."""
        now = self.clock()
        self._prune(now)
        need = self.scaled(estimate)
        total = sum(b[1] for b in self._booked)
        if total + need <= self.per_minute or not self._booked:
            return 0.0
        for t, tok, *_ in self._booked:
            total -= tok
            if total + need <= self.per_minute or total <= 0:
                return max(0.0, 60.0 - (now - t))
        return 60.0

    async def acquire(self, estimate: int) -> list:
        need = self.scaled(estimate)
        async with self._lock:
            while True:
                now = self.clock()
                self._prune(now)
                total = sum(b[1] for b in self._booked)
                if total + need <= self.per_minute or not self._booked:
                    entry = [now, need, estimate]
                    self._booked.append(entry)
                    return entry
                # short naps: the window is re-checked at least once a second
                await asyncio.sleep(min(60.0 - (now - self._booked[0][0]) + 0.01, 1.0))

    def settle(self, entry: list, actual: int) -> None:
        """Record the real input tokens of a booked call (0 / unknown: keep the estimate)."""
        if actual <= 0:
            return
        entry[1] = actual
        est = entry[2] if len(entry) > 2 else 0
        if est > 0:
            self.ratio = (1 - self.LEARN) * self.ratio + self.LEARN * (actual / est)
            self.ratio = min(4.0, max(0.25, self.ratio))

    def release(self, entry: list) -> None:
        """A booked call that never reached the API (or failed before using tokens)."""
        try:
            self._booked.remove(entry)
        except ValueError:
            pass


_TOKEN_LIMITERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def image_token_rate() -> TokenLimiter | None:
    """Process-wide IMAGE_INPUT_TOKENS_PER_MINUTE limiter (one per event loop), None when
    off."""
    n = get_settings().image_input_tokens_per_minute
    if n <= 0:
        return None
    loop = asyncio.get_running_loop()
    lim = _TOKEN_LIMITERS.get(loop)
    if lim is None or lim.per_minute != n:
        lim = _TOKEN_LIMITERS[loop] = TokenLimiter(n)
    return lim


def call_input_tokens(provider: ImageProvider, prompt: str, refs: list[bytes]) -> int:
    """Estimated input tokens of one image call: the provider's own estimate (Gemini knows
    its downscaling and media resolution), else the classic tile rule."""
    own = getattr(provider, "estimate_input_tokens", None)
    if callable(own):
        try:
            return int(own(prompt, refs))
        except Exception:
            log.debug("provider token estimate failed; using the tile rule", exc_info=True)
    settings = get_settings()
    return estimate_input_tokens(prompt, refs, max_side=settings.image_ref_max_side)


class HardCapReached(ProviderError):
    """A retry generation was not started: it could not end before QUALITY_HARD_CAP_S
    (no provider call was made)."""


# Starting points for the expected duration of one image call and one grade call; each
# book replaces them with its own observed averages (``BookGenerator.cycle_estimate``).
IMAGE_CALL_EST_S = 15.0
GRADE_CALL_EST_S = 5.0
_EST_WEIGHT = 0.3


RATE_LIMIT_BASE_DELAY_S = 2.0
RATE_LIMIT_MAX_DELAY_S = 20.0


def rate_limit_delay(exc: ProviderError, retry: int) -> float:
    """Wait before retry ``retry`` (1-based) of a rate-limited call: the server's retry
    delay if it gave one, else exponential backoff (2, 4, 8 .. 20 s); +-25 % jitter so
    parallel pages do not retry in lock-step."""
    base = exc.retry_after if exc.retry_after is not None else min(
        RATE_LIMIT_BASE_DELAY_S * 2 ** (retry - 1), RATE_LIMIT_MAX_DELAY_S)
    return base * random.uniform(0.85, 1.25)


def scores_of(check: quality.Check | None) -> dict | None:
    if check is None or check.grade is None:
        return None
    g = check.grade
    return {"likeness": round(g.likeness, 3), "fidelity": round(g.fidelity, 3),
            "passed": check.passed}


_short = short_error


def severity(check: quality.Check | None) -> int:
    """How many definite failures a check has (a local band failure counts 3, an identity
    mismatch 2, each contradiction / cast / expression / style failure 1)."""
    if check is None:
        return 0
    n = 3 if check.local else 0
    g = check.grade
    if g is not None:
        n += g.hard_failures + (0 if g.identity_ok else 2)
    return n


@dataclass(eq=False)
class RetryRequest:
    """One optional generation waiting for its turn in the retry phase: a page retry
    (``attempt`` = the attempt it would make) or a speaker portrait."""

    page: int
    attempt: int
    tier: int  # TIER_DEFINITE | TIER_PORTRAIT | TIER_SOFT
    score: float = 0.0  # the picture's best score so far (lower = worse = sooner)
    severity: int = 0  # definite failures of the best attempt (more = sooner)
    early: bool = False  # may start during the first pass (local band failure)
    kinds: frozenset = CYCLE_KINDS  # recorded call kinds this request's work produces

    @property
    def key(self) -> tuple:
        # Definite errors first, then portraits, then soft issues; within a tier every
        # page's 2nd attempt before any 3rd (the money is spread over pages), then the
        # most definite failures, the lowest score, the page order.
        return (self.tier, self.attempt, -self.severity, self.score, self.page)


class RetryTicket:
    """Handed out by ``RetryScheduler.admit``: the holder decides (start or not) and, if
    it started, reports when the work is finished."""

    def __init__(self, scheduler: RetryScheduler, req: RetryRequest):
        self.scheduler = scheduler
        self.req = req
        self.since: int | None = None  # index into BookGenerator.costs at the start
        self._decided = False

    def reserved(self) -> float:
        """Expected USD this started work still has to spend (cycle minus what its calls
        have recorded so far)."""
        gen = self.scheduler.gen
        spent = gen.spent_on(self.req.page, self.req.kinds, since=self.since or 0)
        return max(0.0, gen.cycle_cost() - spent)

    async def decided(self, started: bool) -> None:
        if not self._decided:
            self._decided = True
            self.scheduler.decided(self, started)

    async def finished(self) -> None:
        self.scheduler.finished(self)


class RetryScheduler:
    """The retry phase (``QUALITY_RETRY_PHASE``): page retries and new speaker portraits
    wait until every picture's FIRST attempt (image + band check + grade) is done, then
    are decided one at a time in priority order (``RetryRequest.key``) and run in
    parallel. The decision itself (time budget, hard cap, cost cap) stays with the caller;
    the scheduler only orders it and reserves the expected cost of started work, so the
    cost projection is: spent + in-flight remainder + this call (+ reserve). If the head
    of the queue does not fit while other retries are in flight, it waits for them (their
    real cost may be lower than reserved) instead of being skipped.

    Early start: during the first pass, a request flagged ``early`` (a local band failure
    the free rescue could not fix - no grade needed to know it must be redrawn) may start
    at once when the projection with the pictures still in their first pass (at this
    book's actual average costs) fits and the token limiter has no queue."""

    def __init__(self, gen: BookGenerator):
        self.gen = gen
        self.waiting: list[RetryRequest] = []
        self.inflight: list[RetryTicket] = []
        self.deciding = False
        self._ev = asyncio.Event()

    def kick(self) -> None:
        """Wake every waiter to re-check its turn (state changed)."""
        ev, self._ev = self._ev, asyncio.Event()
        ev.set()

    def reserved_usd(self) -> float:
        return sum(t.reserved() for t in self.inflight)

    def _fits(self, req: RetryRequest) -> bool:
        return self.gen.projection_ok(self.gen.cycle_cost(),
                                      definite=req.tier == TIER_DEFINITE,
                                      portrait=req.tier == TIER_PORTRAIT)

    def _is_turn(self, req: RetryRequest) -> bool:
        if self.deciding:
            return False
        if self.gen.first_pass_done():
            if min(self.waiting, key=lambda r: r.key) is not req:
                return False
            # Not affordable now, but in-flight work may cost less than reserved: wait.
            return not self.inflight or self._fits(req)
        if not req.early:
            return False
        early = [r for r in self.waiting if r.early]
        return (min(early, key=lambda r: r.key) is req and self._fits(req)
                and self.gen.tokens_free())

    async def admit(self, req: RetryRequest) -> RetryTicket:
        self.waiting.append(req)
        try:
            while not self._is_turn(req):
                await self._ev.wait()
        except BaseException:
            self.waiting.remove(req)
            self.kick()
            raise
        self.waiting.remove(req)
        self.deciding = True
        if not self.gen.first_pass_done():
            log.info("early retry page=%d attempt=%d during the first pass (local band "
                     "failure, cost projection fits)", req.page, req.attempt)
        return RetryTicket(self, req)

    def decided(self, ticket: RetryTicket, started: bool) -> None:
        self.deciding = False
        if started:
            ticket.since = len(self.gen.costs)
            self.inflight.append(ticket)
        self.kick()

    def finished(self, ticket: RetryTicket) -> None:
        if ticket in self.inflight:
            self.inflight.remove(ticket)
        self.kick()


class BookGenerator:
    def __init__(
        self,
        plan: BookPlan,
        hero: HeroDetails,
        photo: bytes,
        *,
        hero_provider: ImageProvider,
        page_provider: ImageProvider | None = None,
        grader: VisionGrader | None = None,
        assets: AssetSource | None = None,
        hooks: Hooks | None = None,
        max_attempts: int | None = None,
        time_budget_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
        hard_cap_s: float | None = None,
        cost_cap_usd: float = 0.0,
    ) -> None:
        """``time_budget_s`` overrides QUALITY_TIME_BUDGET_S (<= 0: no budget);
        ``hard_cap_s`` overrides QUALITY_HARD_CAP_S (<= 0: no extra retry after the
        budget); ``started_at`` is the job's start on ``clock`` (default: when ``run``
        starts); ``cost_cap_usd`` is the tier's per-book cost cap (<= 0: none)."""
        settings = get_settings()
        self.plan = plan
        self.hero = hero
        self.photo = photo
        self.hero_provider = hero_provider
        self.page_provider = page_provider or hero_provider
        self.grader = grader
        self.assets = assets or NoAssets()
        self.hooks = hooks or Hooks()
        self.max_attempts = (max_attempts if max_attempts is not None
                             else settings.quality_max_attempts) if grader else 1
        self.min_likeness = settings.quality_min_likeness
        self.min_likeness_hero = settings.quality_min_likeness_hero
        self.min_fidelity = settings.quality_min_fidelity
        self.time_budget_s = (settings.quality_time_budget_s if time_budget_s is None
                              else time_budget_s)
        self.hard_cap_s = (settings.quality_hard_cap_s if hard_cap_s is None
                           else hard_cap_s)
        self.clock = clock
        self.started_at = started_at
        self.costs: list[CallCost] = []
        self.timings: dict[str, float] = {}
        self.traits: PhotoTraits | None = None
        self.family_sheet_png: bytes | None = None
        # Set once the family match / family sheet are settled (ok, failed or skipped).
        self._traits_ready = asyncio.Event()
        self._sheet_ready = asyncio.Event()
        # Set by the first image/grade call that fails with an exhausted quota (out of
        # credit / billing): no further provider call is started for this book.
        self.out_of_credit: ProviderError | None = None
        # Supporting-character sheets being fetched / made, per (character_id, costume).
        self._cast_tasks: dict[tuple[str, str], asyncio.Task] = {}
        # Speaker portraits of this book, per (who, costume, expression); shared by pages.
        self._portrait_tasks: dict[PortraitKey, asyncio.Future] = {}
        self._hero_png: bytes | None = None
        # Expected durations (s) of one image call and one grade call, learned from this
        # book's successful calls; used by the hard cap.
        self.image_est_s = IMAGE_CALL_EST_S
        self.grade_est_s = GRADE_CALL_EST_S
        # Cost cap: expected USD of one page image call / one grade call (seeded from the
        # pricing table, then this book's averages) and the pictures (hero + pages) whose
        # first generation + grade has not finished yet.
        self.cost_cap_usd = max(0.0, float(cost_cap_usd or 0.0))
        page_model = str(getattr(self.page_provider, "model", "") or "")
        self.image_cost_est = estimate_cost(page_model, input_tokens=PAGE_INPUT_TOKENS_EST,
                                            images=1)
        self.grade_cost_est = (estimate_cost(str(getattr(grader, "model", "")),
                                             input_tokens=GRADE_INPUT_TOKENS_EST,
                                             output_text_tokens=GRADE_OUTPUT_TOKENS_EST)
                               if grader is not None else 0.0)
        # Observed costs of this book's page image calls and grade calls: once there is
        # one, the expected cost is their plain average (the pricing seed is dropped).
        self._image_costs: list[float] = []
        self._grade_costs: list[float] = []
        # Pictures (hero = HERO_PAGE, pages) whose FIRST generation + check has not
        # finished: their remaining expected cost is reserved; when none is left the
        # first pass is over and the retry phase starts.
        self._pending: set[int] = {HERO_PAGE, *(p.page for p in plan.pages)}
        self.cost_skips: list[str] = []
        self.scheduler: RetryScheduler | None = (
            RetryScheduler(self) if grader is not None and settings.quality_retry_phase
            else None)
        self.early_band_retry = settings.quality_early_band_retry
        self.strip_rescue = settings.strip_rescue
        self.rescue_max_depth = settings.strip_rescue_max_depth
        self.rescue_max_crop = settings.strip_rescue_max_crop
        # Free strip rescues of this book ("p6 a1 seam top 12% -> passed").
        self.rescues: list[str] = []
        self._page_tokens_est = PAGE_INPUT_TOKENS_EST

    # --- time budget -----------------------------------------------------------------------

    def elapsed(self) -> float:
        if self.started_at is None:
            self.started_at = self.clock()
        return self.clock() - self.started_at

    def budget_exhausted(self) -> bool:
        """True once QUALITY_TIME_BUDGET_S has passed since the book started."""
        return self.time_budget_s > 0 and self.elapsed() >= self.time_budget_s

    def within_hard_cap(self) -> bool:
        """True while one more generation + grade is expected to END before
        QUALITY_HARD_CAP_S (the one extra generation for a definite error after the budget
        is allowed only then). False when there is no cap."""
        return self.hard_cap_s > 0 and self.can_finish(self.cycle_estimate())

    def cycle_estimate(self) -> float:
        """Expected seconds of one more generation: image call + grade (if graded)."""
        return self.image_est_s + (self.grade_est_s if self.grader is not None else 0.0)

    def can_finish(self, seconds: float) -> bool:
        """True if work of ``seconds`` started now would end before the hard cap (always
        True without a cap)."""
        return self.hard_cap_s <= 0 or self.elapsed() + seconds <= self.hard_cap_s

    def may_retry(self) -> bool:
        """A sheet regeneration (family / cast): within the budget and able to finish
        (image + check) before the hard cap."""
        return not self.budget_exhausted() and self.can_finish(self.cycle_estimate())

    def _learn(self, attr: str, seconds: float) -> None:
        if seconds > 0:
            old = getattr(self, attr)
            setattr(self, attr, (1 - _EST_WEIGHT) * old + _EST_WEIGHT * seconds)

    # --- cost cap ---------------------------------------------------------------------------

    def spent_usd(self) -> float:
        """Estimated cost of this book so far, without the shared first-time cast sheets."""
        return sum(c.est_cost_usd for c in self.costs if c.kind not in SHARED_KINDS)

    def cycle_cost(self) -> float:
        """Expected USD of one more generation: image call + grade (if graded)."""
        return self.image_cost_est + (self.grade_cost_est if self.grader is not None else 0.0)

    def spent_on(self, page: int, kinds: frozenset, since: int = 0) -> float:
        """Recorded cost of ``page``'s calls of ``kinds`` (hero: HERO_PAGE) from
        ``costs[since:]``."""
        key = None if page == HERO_PAGE else page
        return sum(c.est_cost_usd for c in self.costs[since:]
                   if c.page == key and c.kind in kinds)

    def pending_usd(self) -> float:
        """Expected cost still to come of the pictures in their first pass: per picture one
        cycle (image + grade, this book's actual averages) minus what it has recorded."""
        cycle = self.cycle_cost()
        return sum(max(0.0, cycle - self.spent_on(p, CYCLE_KINDS)) for p in self._pending)

    def first_pass_done(self) -> bool:
        return not self._pending

    def first_done(self, page: int = HERO_PAGE) -> None:
        """``page``'s first generation + check finished (it is no longer reserved); the
        last one opens the retry phase."""
        if page not in self._pending:
            return
        self._pending.discard(page)
        if not self._pending:
            log.info("first pass done at %.1fs (spent $%.4f of cap $%.2f): retry phase",
                     self.elapsed(), self.spent_usd(), self.cost_cap_usd)
        if self.scheduler is not None:
            self.scheduler.kick()

    def tokens_free(self) -> bool:
        """The token limiter would let a page call through now (no queue)."""
        tokens = image_token_rate()
        return tokens is None or tokens.wait_estimate(self._page_tokens_est) <= 0

    def _projection(self, cost: float, definite: bool, portrait: bool
                    ) -> tuple[bool, float, float, float, float]:
        """(fits, spent, pending first pictures, in-flight retries, reserve)."""
        cap = self.cost_cap_usd
        reserve = 0.0 if definite else cap * (PORTRAIT_RESERVE if portrait else SOFT_RESERVE)
        spent = self.spent_usd()
        pending = self.pending_usd()
        inflight = self.scheduler.reserved_usd() if self.scheduler is not None else 0.0
        return (spent + pending + inflight + cost + reserve <= cap + 1e-9, spent, pending,
                inflight, reserve)

    def projection_ok(self, cost: float, *, definite: bool = False,
                      portrait: bool = False) -> bool:
        """``afford`` without logging or listing a skip (the scheduler's look-ahead)."""
        return self.cost_cap_usd <= 0 or self._projection(cost, definite, portrait)[0]

    def afford(self, cost: float, what: str, *, definite: bool = False,
               portrait: bool = False) -> bool:
        """True if an optional call of ``cost`` USD fits under the cost cap (always True
        without one). Projection: spent + the pictures still in their first pass (their
        remaining cycle at this book's ACTUAL average image / grade cost) + started
        retries / portraits still running (their remaining cycle) + this call; a
        soft-issue retry leaves ``SOFT_RESERVE`` and a portrait ``PORTRAIT_RESERVE`` of the
        cap free. In the retry phase nothing is pending but the retries themselves, so the
        cap is used fully. A refusal is logged and listed in ``cost_skips``."""
        cap = self.cost_cap_usd
        if cap <= 0:
            return True
        ok, spent, pending, inflight, reserve = self._projection(cost, definite, portrait)
        if ok:
            return True
        log.warning("cost cap: skipping %s - spent $%.4f + pending first pictures $%.4f + "
                    "running retries $%.4f + this call $%.4f + reserve $%.4f > cap $%.2f",
                    what, spent, pending, inflight, cost, reserve, cap)
        self.cost_skips.append(what)
        return False

    def _learn_cost(self, call: CallCost) -> None:
        """Expected image / grade cost = the plain average of this book's own calls (the
        pricing-table seed only until the first one is seen)."""
        if call.est_cost_usd <= 0 or call.error:
            return
        if call.kind == "page":
            self._image_costs.append(call.est_cost_usd)
            self.image_cost_est = sum(self._image_costs) / len(self._image_costs)
        elif call.kind in ("grade", "rescue_grade"):
            self._grade_costs.append(call.est_cost_usd)
            self.grade_cost_est = sum(self._grade_costs) / len(self._grade_costs)

    # --- recorded calls ------------------------------------------------------------------

    async def _record(self, call: CallCost) -> None:
        self.costs.append(call)  # in-memory totals stay right even if persisting fails
        self._learn_cost(call)
        try:
            await self.hooks.call(call)
        except Exception:  # telemetry must never fail a book (CancelledError propagates)
            log.exception("could not record provider call kind=%s page=%s", call.kind,
                          call.page)

    async def _image(self, provider: ImageProvider, kind: str, page: int | None, prompt: str,
                     refs: list[bytes], must_finish: bool = True,
                     aspect_ratio: str = ASPECT_RATIO) -> ImageResult:
        """One image call, recorded; a 429 RATE limit (not a zero / daily quota) is retried
        after the server's retry delay or a backoff, at most IMAGE_RATE_LIMIT_RETRIES times.
        ``must_finish`` (the picture has no image yet - the book needs one): the waits may
        go past QUALITY_HARD_CAP_S. Otherwise (a retry generation) a wait - rate-limit
        backoff or the token limiter queue - is only taken when the call is still expected
        to END before the cap; else ``HardCapReached`` / the 429 is raised. Every try is its
        own recorded call (providers never retry by themselves)."""
        retries = max(0, get_settings().image_rate_limit_retries)
        n = 0
        while True:
            if self.out_of_credit is not None:
                raise self.out_of_credit  # no new calls once the key is out of credit
            try:
                return await self._image_once(provider, kind, page, prompt, refs,
                                              must_finish=must_finish,
                                              aspect_ratio=aspect_ratio)
            except ProviderError as exc:
                self._note_quota(exc)
                if not exc.rate_limited or n >= retries:
                    raise
                n += 1
                delay = rate_limit_delay(exc, n)
                if not must_finish and not self.can_finish(delay + self.cycle_estimate()):
                    log.warning("rate limited kind=%s page=%s %s - no retry: the wait (%.1fs) "
                                "+ the call would end past the hard cap", kind, page,
                                exc.quota_text, delay)
                    raise
                if not must_finish and not self.afford(
                        self.image_cost_est, f"rate-limit re-try kind={kind} page={page}",
                        definite=True):
                    raise
                log.warning("rate limited kind=%s page=%s %s - retry %d/%d in %.1fs", kind,
                            page, exc.quota_text or "(no quota details)", n, retries, delay)
                await asyncio.sleep(delay)

    def _note_quota(self, exc: ProviderError) -> None:
        if exc.quota_exhausted and self.out_of_credit is None:
            self.out_of_credit = exc
            log.error("IMAGE GENERATION IS OUT OF CREDIT (quota exhausted / billing: prepaid "
                      "balance used up, billing not enabled or quota 0) %s - no further "
                      "provider calls for this book; pictures keep their best attempt: %s",
                      exc.quota_text, exc)

    async def _image_once(self, provider: ImageProvider, kind: str, page: int | None,
                          prompt: str, refs: list[bytes], must_finish: bool = True,
                          aspect_ratio: str = ASPECT_RATIO) -> ImageResult:
        model = str(getattr(provider, "model", "") or "")
        est = call_input_tokens(provider, prompt, refs)
        tokens = image_token_rate()
        if tokens is not None and not must_finish:
            wait = tokens.wait_estimate(est)
            if wait > 0 and not self.can_finish(wait + self.cycle_estimate()):
                raise HardCapReached(f"no retry kind={kind} page={page}: the token-limit "
                                     f"queue ({wait:.1f}s) + the call would end past the "
                                     f"hard cap")
        limiter = image_rate()
        if limiter is not None:
            await limiter.acquire()
        booking = await tokens.acquire(est) if tokens is not None else None
        async with image_slots():
            t0 = time.perf_counter()
            try:
                img = await provider.edit(prompt, refs, aspect_ratio=aspect_ratio)
            except asyncio.CancelledError:
                await self._record_quietly(CallCost(kind, page, model, 0.0,
                                                    round(time.perf_counter() - t0, 3), 0, 0,
                                                    provider.name, "cancelled"))
                raise
            except Exception as exc:
                if (booking is not None and isinstance(exc, ProviderError)
                        and exc.status == 429):
                    tokens.release(booking)  # rejected before using the quota
                await self._record(CallCost(kind, page, model, 0.0,
                                            round(time.perf_counter() - t0, 3), 0, 0,
                                            provider.name, _short(exc)))
                raise
        if booking is not None:
            tokens.settle(booking, img.input_tokens)
        if kind == "page":
            self._page_tokens_est = est
        self._learn("image_est_s", img.duration_s or (time.perf_counter() - t0))
        log.info("image tokens kind=%s page=%s refs=%d est_in_tok=%d in_tok=%d%s", kind, page,
                 len(refs), est, img.input_tokens,
                 "" if tokens is None else f" window={tokens.used()}/{tokens.per_minute} "
                 f"ratio={tokens.ratio:.2f}")
        await self._record(CallCost.of(kind, page, img, provider.name))
        return img

    async def _record_quietly(self, call: CallCost) -> None:
        try:
            await self._record(call)
        except Exception:
            log.exception("could not record cancelled call kind=%s page=%s", call.kind,
                          call.page)

    async def _grade(self, image: bytes, *, page: int | None, hero_png: bytes | None,
                     caption: str, must: list[str], subject: str,
                     props: Sequence[str] = (), family_ref: bytes | None = None,
                     family: Sequence[str] = (), cast_refs: Sequence[bytes] = (),
                     cast: Sequence[str] = (), expression: str = "",
                     hero_look: bool = True,
                     presence: Sequence[str] = (), hero_outfit: str | None = None,
                     cast_expressions: Sequence[str] = (),
                     kind: str = "grade") -> GradeResult | None:
        """One grader call; None if it failed (the picture is then accepted unchecked).
        Waits for the photo traits first (they are ready long before any picture is).
        ``family_ref`` / ``family`` are passed only on pages with a family reference,
        ``cast_refs`` / ``cast`` / ``expression`` only when there are any."""
        assert self.grader is not None
        await self._traits_ready.wait()
        if self.out_of_credit is not None:
            return None  # no more calls: the picture is accepted unchecked
        extra: dict = ({"family_ref": family_ref, "family": list(family)}
                       if family_ref is not None else {})
        if cast:
            extra.update(cast=list(cast), cast_refs=list(cast_refs))
        if expression:
            extra["expression"] = expression
        if presence:
            extra["presence"] = list(presence)
        if cast_expressions:
            extra["cast_expressions"] = list(cast_expressions)
        style = style_summary(self.plan.style)
        if style:
            extra["style"] = style
        if hero_look:
            items = kept_items(self.traits)
            if items:
                extra["hero_items"] = items
            outfit = self.plan.story.hero_outfit if hero_outfit is None else hero_outfit
            if outfit:
                extra["hero_outfit"] = outfit
        t0 = time.perf_counter()
        try:
            g = await self.grader.grade(image, hero=hero_png, photo=self.photo,
                                        caption=caption, must_be_visible=must, subject=subject,
                                        traits=self.traits, props=list(props), **extra)
        except asyncio.CancelledError:
            await self._record_quietly(CallCost(kind, page, self.grader.model, 0.0,
                                                round(time.perf_counter() - t0, 3), 0, 0,
                                                self.grader.name, "cancelled"))
            raise
        except Exception as exc:
            if isinstance(exc, ProviderError):
                self._note_quota(exc)
            log.warning("grader failed page=%s (picture accepted unchecked): %s", page,
                        _short(exc))
            await self._record(CallCost(kind, page, self.grader.model, 0.0,
                                        round(time.perf_counter() - t0, 3), 0, 0,
                                        self.grader.name, _short(exc)))
            return None
        self._learn("grade_est_s", time.perf_counter() - t0)
        await self._record(CallCost.of_meta(kind, page, g.meta, self.grader.name))
        return g

    # --- family -----------------------------------------------------------------------------

    async def family_step(self) -> str:
        """Photo traits + family variant (shared with HW1: ``match_family``). The traits
        are measured whenever there is a grader - the identity checks use them too."""
        t0 = time.perf_counter()
        try:
            fixed = self.plan.family_variant if self.plan.family_fixed else None
            self.traits, variant = await match_family(self.grader, self.photo,
                                                      self.plan.story, self._record,
                                                      fixed_variant=fixed)
        finally:
            self._traits_ready.set()
        self.plan = replace(self.plan, family_variant=variant)
        self.timings["family_match"] = round(time.perf_counter() - t0, 3)
        log.info("family variant=%s traits=%s", variant,
                 None if self.traits is None else
                 (self.traits.skin_tone, self.traits.hair_colour, self.traits.hair_texture))
        await self.hooks.family(variant, self.traits)
        return variant

    def family_sheet_wanted(self) -> bool:
        """Only with a grader (without one the book stays at one call per picture) and only
        when a planned page shows a family member."""
        story = self.plan.story
        return (self.grader is not None and bool(family_characters(story))
                and any(page_family(story, p) for p in self.plan.pages))

    async def family_sheet_step(self) -> bytes | None:
        """After the family match: one family reference sheet (shared with HW1:
        ``make_family_sheet``), unless bible sheets exist for every family member in the
        chosen variant. Runs beside the hero. Never raises (except cancellation)."""
        try:
            await self._traits_ready.wait()
            if not self.family_sheet_wanted():
                return None
            t0 = time.perf_counter()
            story, variant = self.plan.story, self.plan.family_variant
            keys = [(c.id, variant) for c in family_characters(story)]
            if len(await self.assets.characters(keys)) == len(keys):
                log.info("family sheet skipped: bible sheets exist for %s", keys)
                return None

            async def call(kind, page, prompt, refs):
                return await self._image(self.hero_provider, kind, page, prompt, refs)

            # The check's one regeneration is skipped once the quality time budget is
            # used up (family pages would otherwise wait another ~20 s for it).
            png = await make_family_sheet(
                call, self.plan.style, story, variant, grader=self.grader,
                record=self._record,
                may_retry=lambda: self.may_retry() and self.afford(
                    self.cycle_cost(), "family sheet regeneration", definite=True))
            self.timings["family_sheet"] = round(time.perf_counter() - t0, 3)
            if png is not None:
                self.family_sheet_png = png
                try:
                    await self.hooks.family_sheet(png)
                except Exception:
                    log.exception("could not store the family sheet (pages still use it)")
            return png
        finally:
            self._sheet_ready.set()

    # --- supporting-character sheets ----------------------------------------------------

    async def _stored_sheet(self, character_id: str, costume_id: str) -> bytes | None:
        getter = getattr(self.assets, "sheet", None)
        if getter is not None:
            return await getter(character_id, costume_id)
        if costume_id:
            return None
        found = await self.assets.characters([(character_id, "")])
        return found.get((character_id, ""))

    async def cast_sheet_step(self, character_id: str, costume_id: str) -> bytes | None:
        """The stored sheet, or - with a grader - a new one (made once per process at a
        time, see ``app.pipeline.cast_sheets``). Never raises (except cancellation)."""
        story, preset = self.plan.story, self.plan.preset
        c = story.characters[character_id]
        costume = c.costume(costume_id) if costume_id else None
        try:
            async with sheet_lock((story.id, preset, character_id, costume_id)):
                png = await self._stored_sheet(character_id, costume_id)
                if png is not None or self.grader is None:
                    return png
                t0 = time.perf_counter()

                async def call(kind, page, prompt, refs):
                    return await self._image(self.hero_provider, kind, page, prompt, refs)

                # Shared by every book of this story + style: drawn at the default detail.
                style = build_style_string(preset, DEFAULT_DETAIL)
                made = await make_cast_sheet(call, style, c, costume, grader=self.grader,
                                             record=self._record, may_retry=self.may_retry)
                if made is None:
                    return None
                img, prompt = made
                png = img.png
                saver = getattr(self.assets, "save_sheet", None)
                if saver is not None:
                    try:
                        png = await saver(character_id, costume_id, png, prompt, img.model)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("could not store the %s/%s sheet (this book still "
                                      "uses it)", character_id, costume_id or "plain")
                label = f"{character_id}_{costume_id}" if costume_id else character_id
                self.timings[f"cast_sheet_{label}"] = round(time.perf_counter() - t0, 3)
                return png
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("cast sheet %s/%s failed (described in text only)", character_id,
                          costume_id or "plain")
            return None

    def start_cast_sheets(self, tg: asyncio.TaskGroup) -> None:
        """Start (in ``tg``) every supporting sheet the planned pages use."""
        for key in needed_sheets(self.plan.story, self.plan.pages):
            if key not in self._cast_tasks:
                self._cast_tasks[key] = tg.create_task(self.cast_sheet_step(*key))

    async def _cast_png(self, character_id: str, costume_id: str) -> bytes | None:
        task = self._cast_tasks.get((character_id, costume_id))
        if task is not None:
            # shield: a page that is cancelled must not cancel the sheet other pages share
            return await asyncio.shield(task)
        return await self._stored_sheet(character_id, costume_id)

    # --- hero -------------------------------------------------------------------------------

    async def hero_step(self) -> HeroOutcome:
        t0 = time.perf_counter()
        made: list[bool] = []  # one entry per attempt that produced a picture

        async def generate(n, corrections, _quiet):
            # Every attempt waits for the photo traits (~2 s on the first one; the family
            # sheet still overlaps the hero): without the explicit identity line the first
            # hero lost curly hair texture and still passed. A failed traits call sets the
            # event too, and the hero then starts without the line.
            await self._traits_ready.wait()
            prompt = hero_identity_prompt(self.plan.style, self.plan.story, self.traits,
                                          corrections)
            return await self._image(self.hero_provider, "hero", None, prompt, [self.photo],
                                     must_finish=not made)

        async def check(n, img):
            made.append(True)
            if self.grader is None:
                if len(made) == 1:
                    self.first_done(HERO_PAGE)
                return quality.Check(passed=True)
            # The hero reference is the identity anchor of every page: it is checked for
            # likeness and identity vs. the photo AND the hero look (a child, the kept
            # glasses / headwear, the story outfit - never the photo's suit, no beard).
            # The hero sheet is judged on the outfit's description only (type and colours),
            # not on continuity trivia such as which elbow a patch is on.
            core = self.plan.story.hero_outfit_core or self.plan.story.hero_outfit
            try:
                g = await self._grade(img.png, page=None, hero_png=None,
                                      caption=HERO_CAPTION, must=HERO_MUST_SEE,
                                      subject="hero", hero_outfit=core)
            finally:
                if len(made) == 1:
                    self.first_done(HERO_PAGE)
            if g is None:
                return quality.Check(passed=True)
            return quality.evaluate(g, min_likeness=self.min_likeness_hero,
                                    min_fidelity=self.min_fidelity, traits=self.traits,
                                    hero_outfit=core,
                                    style_hint=style_correction(self.plan.style))

        res = await self._loop(HERO_PAGE, generate, check, min_attempts=HERO_MIN_ATTEMPTS)
        chosen = res.chosen
        out = HeroOutcome(png=chosen.png, attempts=res.count, scores=scores_of(chosen.check),
                          chosen_attempt=chosen.n, image=chosen.image,
                          budget_stopped=res.budget_stopped, cost_stopped=res.cost_stopped)
        self.timings["hero"] = round(time.perf_counter() - t0, 3)
        log.info("hero done attempts=%d chosen=%d scores=%s duration=%.2fs", res.count,
                 chosen.n, out.scores, self.timings["hero"])
        await self.hooks.hero_done(out)
        return out

    async def _loop(self, page: int, generate, check, min_attempts: int = 1,
                    admit=None) -> quality.LoopResult:
        async def on_phase(n, phase, chk):
            await self.hooks.attempt_phase(page, n, phase, chk)

        async def on_attempt(a):
            await self.hooks.attempt_done(page, a)

        res = await quality.run_quality_loop(
            generate=generate, check=check, max_attempts=self.max_attempts,
            on_phase=on_phase, on_attempt=on_attempt,
            budget_exhausted=self.budget_exhausted, min_attempts=min_attempts,
            within_hard_cap=self.within_hard_cap,
            can_finish=lambda: self.can_finish(self.cycle_estimate()),
            can_afford=lambda definite: self.afford(
                self.cycle_cost(),
                f"{'hero' if page == HERO_PAGE else f'page {page}'} retry"
                f" ({'definite error' if definite else 'soft issues'})",
                definite=definite),
            admit=admit)
        if res.extra_retry:
            log.warning("page=%d extra generation after the time budget for a definite "
                        "error (hard cap %.0fs, now %.1fs): kept attempt %d of %d "
                        "(passed=%s)", page, self.hard_cap_s, self.elapsed(), res.chosen.n,
                        res.count, res.chosen.passed)
        if res.budget_stopped:
            why = (f"time budget ({self.time_budget_s:.0f}s) used up"
                   if self.budget_exhausted() else
                   f"hard cap ({self.hard_cap_s:.0f}s) - one more generation "
                   f"(~{self.cycle_estimate():.0f}s) would end past it")
            log.warning("page=%d %s at %.1fs: no further retries, keeping attempt %d of %d "
                        "(passed=%s)", page, why, self.elapsed(), res.chosen.n, res.count,
                        res.chosen.passed)
            await self.hooks.budget_stopped(page, res.chosen)
        if res.cost_stopped:
            log.warning("page=%d cost cap ($%.2f, spent $%.4f): no further retries, keeping "
                        "attempt %d of %d (passed=%s)", page, self.cost_cap_usd,
                        self.spent_usd(), res.chosen.n, res.count, res.chosen.passed)
            await self.hooks.cost_stopped(page, res.chosen)
        return res

    # --- pages ------------------------------------------------------------------------------

    async def page_refs(self, page: Page, hero_png: bytes) -> list[tuple[RefSpec, bytes]]:
        await self._traits_ready.wait()  # the family variant is settled
        story, variant = self.plan.story, self.plan.family_variant
        plate = await self.assets.plate(page.page)
        chars = page_characters(story, page)
        if plate is None:
            log.warning("no background plate story=%s page=%d preset=%s - falling back to "
                        "the prompt + reference sheets (run scripts/pregenerate_assets.py)",
                        story.id, page.page, self.plan.preset)
        # Family members: bible sheet in the chosen variant, else the per-book family sheet.
        need = [c for c in chars if c.family]
        keys = [(c.id, variant) for c in need]
        found = await self.assets.characters(keys) if keys else {}
        lacking = [c for c, k in zip(need, keys, strict=True) if k not in found]
        # Family members without a bible sheet get the per-book family sheet (pages
        # without a family member never wait for it).
        sheet: bytes | None = None
        family_lacking = [c for c in lacking if c.family]
        if family_lacking:
            await self._sheet_ready.wait()
            sheet = self.family_sheet_png
        missing = [c.id for c in lacking if sheet is None]
        char_refs = [(RefSpec("character", c.name, c.id), found[k])
                     for c, k in zip(need, keys, strict=True) if k in found]
        if sheet is not None:
            char_refs.insert(0, (RefSpec("family_sheet", sheet_names(family_lacking)), sheet))
        # Supporting characters are painted into the plate; without one, their reference
        # sheets (in this page's costume), at most MAX_CAST_REFS of them.
        if plate is None:
            ranked = sorted(supporting(story, page), key=lambda c: c.ref_priority)
            for c in ranked:
                costume = c.sheet_costume_id(page.page)
                png = await self._cast_png(c.id, costume)
                if png is None:
                    missing.append(c.id)
                elif sum(s.role == "cast" for s, _ in char_refs) < MAX_CAST_REFS:
                    char_refs.append((RefSpec("cast", c.name, c.id,
                                              costume_label(c, costume)), png))
        if missing:
            log.warning("no reference sheet for %s story=%s preset=%s page=%d - described "
                        "in text only", missing, story.id, self.plan.preset, page.page)
        cap = max_reference_images(self.page_provider)
        # No photo: the hero reference (the child version) is the identity anchor; the
        # photo pulled pages back to an adult person and cost ~1k input tokens per call.
        return select_refs(hero_png, None, plate, char_refs, cap)

    async def page_step(self, page: Page, hero_png: bytes) -> BookPage:
        t0 = time.perf_counter()
        layout = get_layout(page.layout)
        refs = await self.page_refs(page, hero_png)
        specs = [r for r, _ in refs]
        images = [b for _, b in refs]
        story, variant = self.plan.story, self.plan.family_variant
        family_here = [c for c in page_characters(story, page) if c.family]
        family_ref, checked_ids = family_reference(refs, [c.id for c in family_here])
        checked = [c for c in family_here if c.id in checked_ids]
        family_lines = family_expectations(checked, variant)
        family_hint = family_correction(checked, variant)
        cast_chars = supporting(story, page)
        # The grader gets the sheets in the order of the cast lines.
        sent = [spec.character_id for spec, _ in refs if spec.role == "cast"]
        cast_chars = ([c for cid in sent for c in cast_chars if c.id == cid]
                      + [c for c in cast_chars if c.id not in sent])
        by_id = {spec.character_id: b for spec, b in refs if spec.role == "cast"}
        cast_pngs = [by_id[c.id] for c in cast_chars if c.id in by_id]
        cast_lines = cast_expectations(cast_chars, page.page, page.sizes)
        cast_hint = cast_correction(cast_chars, page.page, page.sizes)
        expr_lines = cast_expression_lines(story, page)
        expr_hint = cast_expression_correction(expr_lines)
        page_outfit = story.hero_outfit_for(page.page)
        style_hint = style_correction(self.plan.style)
        expression_hint = expression_correction(page.expression) if page.expression else ""
        # Pages that may show a speaker portrait ask the grader who is visible.
        presence = presence_names(story, page) if self.grader is not None else []
        made: list[bool] = []  # one entry per attempt that produced a picture
        padded: list[int] = []  # attempts rejected for a padded / blank / blurred band

        async def generate(n, corrections, quiet):
            prompt = scene_prompt(self.plan.style, page, self.plan.story,
                                  self.plan.family_variant, refs=specs,
                                  corrections=corrections,
                                  quiet_zone=quiet.zone.zone if quiet else None,
                                  quiet_item=quiet.blocker_phrase if quiet else None,
                                  hero_items=kept_items_phrase(self.traits),
                                  vertical_rescue=self.vertical_rescue(n, len(padded)))
            return await self._image(self.page_provider, "page", page.page, prompt, images,
                                     must_finish=not made)

        async def check(n, img):
            made.append(True)
            first = len(made) == 1
            try:
                return await check_once(n, img)
            finally:
                if first:
                    self.first_done(page.page)

        async def check_once(n, img):
            if self.grader is None:
                return quality.Check(passed=True, placement=place_text(layout))
            band = await asyncio.to_thread(find_blank_band, img.png)
            if band is not None:  # no grade of the padded picture (saves time and money)
                padded.append(n)
                log.warning("page=%d attempt=%d %s band %s %.0f%% - not graded", page.page, n,
                            band.kind, band.edge, band.fraction * 100)
                rescued = await rescue(n, img, band)
                if rescued is not None:
                    return rescued
                return quality.Check(passed=False, issues=[band.issue],
                                     placement=place_text(layout), local=True,
                                     definite=True)
            return await graded(n, img.png)

        async def rescue(n, img, band: Band) -> quality.Check | None:
            """Free strip rescue (``app.pipeline.rescue``): the strip cropped away, re-checked
            and graded (one grade call, kind ``rescue_grade``); None = not possible (too
            deep, too much crop, still a band) - the caller fails the attempt locally."""
            if not self.strip_rescue or self.out_of_credit is not None:
                return None
            how = await asyncio.to_thread(rescue_strips, img.png, band, find_blank_band, (),
                                          max_depth=self.rescue_max_depth,
                                          max_crop=self.rescue_max_crop)
            if how.png is None:
                log.warning("page=%d attempt=%d no strip rescue (%s): %s - paid retry",
                            page.page, n, how.describe(), how.why)
                self.rescues.append(f"p{page.page} a{n} not rescued: {how.why}")
                return None
            png = how.png
            spent0 = self.spent_on(page.page, frozenset({"rescue_grade"}))
            chk = await graded(n, png, kind="rescue_grade")
            chk.image = replace(img, png=png)
            chk.rescued = True
            cost = self.spent_on(page.page, frozenset({"rescue_grade"})) - spent0
            if not chk.passed:
                # the next generation must not pad again, whatever the crop's grade says
                chk.issues = ([band.issue] + [i for i in chk.issues if i != band.issue]
                              )[:quality.MAX_ISSUES]
            log.warning("page=%d attempt=%d strip rescue %s: graded passed=%s (no image "
                        "call, grade $%.4f)%s", page.page, n, how.describe(), chk.passed,
                        cost, "" if chk.passed else f" - issues: {chk.issues[:3]}")
            self.rescues.append(f"p{page.page} a{n} {how.describe()} -> "
                                f"{'passed' if chk.passed else 'failed'}")
            return chk

        async def graded(n, png: bytes, kind: str = "grade") -> quality.Check:
            g = await self._grade(png, page=page.page, hero_png=hero_png,
                                  caption=page.scene, must=page.must_be_visible,
                                  subject="page", props=page_props(self.plan.story, page),
                                  family_ref=family_ref, family=family_lines,
                                  cast_refs=cast_pngs, cast=cast_lines,
                                  expression=page.expression, presence=presence,
                                  hero_outfit=page_outfit, cast_expressions=expr_lines,
                                  kind=kind)
            if g is None:
                return quality.Check(passed=True, placement=place_text(layout))
            if n not in padded and any(
                    k in " ".join(g.contradictions).lower()
                    for k in ("padded", "letterbox", "blurred strip", "empty band", "seam")):
                padded.append(n)
            placement = place_text(layout, g.boxes)
            if placement.blocked:
                log.warning("page=%d attempt=%d text zone %s covers %s (needs attention)",
                            page.page, n, placement.zone.zone, placement.blocker_phrase)
            return quality.evaluate(g, min_likeness=self.min_likeness,
                                    min_fidelity=self.min_fidelity, placement=placement,
                                    traits=self.traits, family_hint=family_hint,
                                    cast_hint=cast_hint, expression_hint=expression_hint,
                                    hero_outfit=page_outfit, style_hint=style_hint,
                                    cast_expression_hint=expr_hint)

        async def admit(n_next: int, definite: bool, best: quality.Attempt) -> RetryTicket:
            assert self.scheduler is not None
            chk = best.check
            definite = definite or bool(chk is not None and chk.definite)
            req = RetryRequest(
                page=page.page, attempt=n_next,
                tier=TIER_DEFINITE if definite else TIER_SOFT, score=best.score,
                severity=severity(chk),
                early=bool(self.early_band_retry and definite and chk is not None
                           and chk.local))
            return await self.scheduler.admit(req)

        res = await self._loop(page.page, generate, check,
                               admit=admit if self.scheduler is not None else None)
        chosen = res.chosen
        placement: Placement = (chosen.check.placement if chosen.check and chosen.check.placement
                                else place_text(layout))
        if placement.blocked:
            log.warning("page=%d final text zone %s still covers %s (needs attention)",
                        page.page, placement.zone.zone, placement.blocker_phrase)
        grade = chosen.check.grade if chosen.check is not None else None
        portrait_png, portrait = await self.portrait_step(page, grade)
        bp = BookPage(
            page=page.page, title=page.title, caption=page.caption,
            text=render_text(page.text, self.hero.name, self.hero.pronouns),
            layout=page.layout, zone=placement.zone, png=chosen.png, attempts=res.count,
            scores=scores_of(chosen.check), chosen_attempt=chosen.n,
            used_plate=any(s.role == "plate" for s in specs),
            budget_stopped=res.budget_stopped, cost_stopped=res.cost_stopped,
            portrait_png=portrait_png, portrait=portrait,
            visible=grade.visible_characters if grade is not None and presence else None,
        )
        log.info("page=%d done attempts=%d chosen=%d zone=%s plate=%s refs=%d duration=%.2fs",
                 page.page, res.count, chosen.n, placement.zone.zone, bp.used_plate,
                 len(images), time.perf_counter() - t0)
        await self.hooks.page_done(bp)
        return bp

    # --- speaker portraits ------------------------------------------------------------------

    def start_moment_portraits(self) -> None:
        """Start the portraits of ``portrait: moment`` pages at once (after the hero, whose
        reference the hero portrait needs), beside the page pictures: they are always
        shown, so nothing has to wait for a grade. Same limiter / hard-cap rules as any
        portrait; a failed or skipped one just leaves ``portrait_step`` without it."""
        story = self.plan.story
        for page in self.plan.pages:
            key = portrait_key(story, page)
            if key is None or page.portrait_when != PORTRAIT_MOMENT:
                continue
            if key not in self._portrait_tasks:
                fut = asyncio.ensure_future(self._portrait(key, page.page, early=True))
                fut.add_done_callback(_consume_result)
                self._portrait_tasks[key] = fut

    async def portrait_step(self, page: Page,
                            grade: GradeResult | None) -> tuple[bytes | None, dict | None]:
        """(portrait PNG, {character, costume, expression}) for a candidate page whose
        chosen picture does NOT show the speaker (``portrait_gate``), else (None, None).
        Never raises (except cancellation): a missing portrait only leaves the box
        without one."""
        story = self.plan.story
        key = portrait_key(story, page)
        if key is None:
            return None, None
        ok, why = portrait_gate(story, page, grade if self.grader is not None else None)
        log.info("portrait page=%d speaker=%s expression=%s draw=%s (%s)", page.page,
                 page.speaker, page.portrait_expression, ok, why)
        if not ok:
            return None, None
        try:
            fut = self._portrait_tasks.get(key)
            if fut is None:
                fut = asyncio.ensure_future(self._portrait(key, page.page))
                self._portrait_tasks[key] = fut
            png = await asyncio.shield(fut)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("portrait page=%d failed (no portrait)", page.page)
            return None, None
        if png is None:
            log.warning("portrait page=%d: none made (box without portrait)", page.page)
            return None, None
        who, costume, expression = key
        return png, {"character": who, "costume": costume, "expression": expression}

    async def _portrait(self, key: PortraitKey, page: int,
                        early: bool = False) -> bytes | None:
        """The stored portrait (supporting characters) or a new one."""
        who, costume_id, expression = key
        story, preset = self.plan.story, self.plan.preset
        c = story.characters.get(who)
        supporting_char = c is not None and not c.family
        if supporting_char:
            async with sheet_lock(("portrait", story.id, preset, who, costume_id, expression)):
                getter = getattr(self.assets, "portrait", None)
                stored = await getter(who, costume_id, expression) if getter else None
                if stored is not None:
                    return stored
                made = await self._make_portrait(key, page, early)
                if made is None:
                    return None
                img, prompt = made
                saver = getattr(self.assets, "save_portrait", None)
                if saver is None:
                    return img.png
                try:
                    return await saver(who, costume_id, expression, img.png, prompt,
                                       img.model)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("could not store the %s portrait (this book still uses "
                                  "it)", key)
                    return img.png
        made = await self._make_portrait(key, page, early)
        return made[0].png if made else None

    async def _make_portrait(self, key: PortraitKey, page: int,
                             early: bool = False) -> tuple[ImageResult, str] | None:
        """Reference + description for ``key``, then ``make_portrait`` - only when one
        image call + check can end before the hard cap."""
        who, costume_id, expression = key
        story = self.plan.story
        style = self.plan.style
        if who == HERO:
            ref, ref_label = self._hero_png, "the hero character reference"
            name = HERO_PRESENCE_NAME
            desc = ("the hero child exactly as in the reference image: same face, skin "
                    "tone, hair, glasses and headwear (if any), wearing the story outfit: "
                    f"{story.hero_outfit_for(page) or 'as in the reference'}")
        else:
            c = story.characters[who]
            name = c.name
            if c.family:
                await self._sheet_ready.wait()
                found = await self.assets.characters([(c.id, self.plan.family_variant)])
                ref = found.get((c.id, self.plan.family_variant)) or self.family_sheet_png
                ref_label = f"the family reference sheet (draw only {c.name} from it)"
                lines = family_expectations([c], self.plan.family_variant)
                desc = f"{c.description} {lines[0] if lines else ''}".strip()
            else:
                ref = await self._cast_png(c.id, costume_id)
                ref_label = f"{c.name}'s character reference sheet"
                costume = c.costume(costume_id) if costume_id else None
                desc = (f"{c.sheet_description or c.description} "
                        f"{sheet_outfit_line(c, costume)}").strip()
                # Shared by every book of this story + style: drawn at the default detail.
                style = build_style_string(self.plan.preset, DEFAULT_DETAIL)
        if ref is None:
            log.warning("portrait %s: no reference image - skipped", key)
            return None
        # Retry phase: a new portrait waits for the first pass and then for its turn -
        # after every queued definite-error retry, before soft-issue retries. A ``moment``
        # portrait (always shown, no grade gate: ``early``) may start during the first pass
        # when the projection with this book's actual costs (+ PORTRAIT_RESERVE) fits and
        # the token limiter has no queue - it then overlaps the first pass (wall time).
        ticket = (await self.scheduler.admit(RetryRequest(page=page, attempt=1,
                                                          tier=TIER_PORTRAIT, early=early,
                                                          kinds=PORTRAIT_KINDS))
                  if self.scheduler is not None else None)
        try:
            if not self.can_finish(self.cycle_estimate()):
                log.warning("portrait %s skipped: one image call + check (~%.0fs) would end "
                            "past the hard cap (%.0fs, now %.1fs)", key,
                            self.cycle_estimate(), self.hard_cap_s, self.elapsed())
                return None
            label = "/".join(x for x in key if x)
            if not self.afford(self.cycle_cost(), f"portrait p{page} {label}",
                               portrait=True):
                return None
            if ticket is not None:
                await ticket.decided(True)

            async def call(kind, pg, prompt, refs):
                return await self._image(self.hero_provider, kind, pg, prompt, refs,
                                         must_finish=False, aspect_ratio=PORTRAIT_ASPECT)

            t0 = time.perf_counter()
            made = await make_portrait(call, style, name=name, description=desc,
                                       expression=expression, ref=ref, ref_label=ref_label,
                                       grader=self.grader, record=self._record,
                                       may_retry=lambda: self.can_finish(self.cycle_estimate())
                                       and self.afford(self.cycle_cost(),
                                                       f"portrait p{page} {label} "
                                                       "regeneration", portrait=True),
                                       page=page)
            label = "_".join(x for x in key if x)
            self.timings[f"portrait_{label}"] = round(time.perf_counter() - t0, 3)
            return made
        finally:
            if ticket is not None:
                await ticket.decided(False)  # no-op once decided
                await ticket.finished()

    def vertical_rescue(self, n: int, padded: int) -> bool:
        """Ask explicitly for a vertical composition on attempt ``n`` when earlier attempts
        came back padded: after two padded attempts, or after one when this is likely the
        last attempt (the max, or the budget is used up)."""
        if padded <= 0 or n <= 1:
            return False
        return padded >= 2 or n >= self.max_attempts or self.budget_exhausted()

    async def pages_step(self, hero_png: bytes) -> list[BookPage]:
        t0 = time.perf_counter()
        await self.hooks.stage("pages", 1)
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(self.page_step(p, hero_png)) for p in self.plan.pages]
        except BaseExceptionGroup as eg:  # surface the first real failure as itself
            real = [e for e in eg.exceptions if not isinstance(e, asyncio.CancelledError)]
            raise (real or list(eg.exceptions))[0] from None
        self.timings["pages"] = round(time.perf_counter() - t0, 3)
        return [t.result() for t in tasks]

    # --- whole book -------------------------------------------------------------------------

    async def run(self) -> BookResult:
        started = time.perf_counter()
        self.elapsed()  # starts the time-budget clock unless the job already did
        await self.hooks.stage("hero", 0)
        try:
            # Family match -> family sheet runs beside the hero; pages start when the hero
            # is done (family pages also wait for the sheet, normally already there).
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.family_step())
                tg.create_task(self.family_sheet_step())
                self.start_cast_sheets(tg)
                hero = await tg.create_task(self.hero_step())
                self._hero_png = hero.png
                self.start_moment_portraits()
                pages = await self.pages_step(hero.png)
        except BaseExceptionGroup as eg:  # surface the first real failure as itself
            real = [e for e in eg.exceptions if not isinstance(e, asyncio.CancelledError)]
            raise (real or list(eg.exceptions))[0] from None
        finally:  # a failed book leaves no portrait call running
            for fut in self._portrait_tasks.values():
                if not fut.done():
                    fut.cancel()
        self.timings["images_total"] = round(time.perf_counter() - started, 3)
        result = BookResult(
            story=self.plan.story, preset=self.plan.preset, detail=self.plan.detail,
            hero_details=self.hero, family_variant=self.plan.family_variant,
            style=self.plan.style, hero_png=hero.png, pages=pages, costs=self.costs,
            hero_attempts=hero.attempts, hero_scores=hero.scores, timings=dict(self.timings),
            family_sheet_png=self.family_sheet_png, cost_skips=list(self.cost_skips),
            rescues=list(self.rescues),
        )
        if self.cost_skips:
            log.warning("cost cap $%.2f skipped %d optional call(s): %s", self.cost_cap_usd,
                        len(self.cost_skips), self.cost_skips)
        if self.rescues:
            log.info("free strip rescues: %s", self.rescues)
        log.info(
            "book images done story=%s pages=%s style=%s detail=%d variant=%s calls=%d "
            "timings=%s est_cost=$%.4f", self.plan.story.id, [p.page for p in pages],
            self.plan.preset, self.plan.detail, self.plan.family_variant, len(self.costs),
            self.timings, result.est_cost_usd,
        )
        return result


async def run_book(
    provider: ImageProvider,
    photo: bytes,
    preset: str,
    detail: int,
    *,
    story_id: str,
    pages: Sequence[int],
    hero: HeroDetails,
    family_variant: str | None = None,
    page_provider: ImageProvider | None = None,
    grader: VisionGrader | None = None,
    assets: AssetSource | None = None,
    hooks: Hooks | None = None,
    max_attempts: int | None = None,
    time_budget_s: float | None = None,
    cost_cap_usd: float = 0.0,
) -> BookResult:
    """Whole book in one call (scripts and tests). The web app uses ``app.jobs`` instead,
    which runs the same ``BookGenerator`` with persistence/streaming hooks."""
    plan = plan_book(story_id, pages, preset, detail, family_variant)
    gen = BookGenerator(plan, hero, photo, hero_provider=provider, page_provider=page_provider,
                        grader=grader, assets=assets, hooks=hooks, max_attempts=max_attempts,
                        time_budget_s=time_budget_s, cost_cap_usd=cost_cap_usd)
    return await gen.run()
