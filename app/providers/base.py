"""Provider interfaces for image generation/editing and (later) vision grading.

Rule from the spec: every ``edit`` call makes exactly ONE upstream API call.
Providers never retry; a failure surfaces to the caller as a ``ProviderError``.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from PIL import Image

from app.config import get_settings

log = logging.getLogger("app.providers")


@dataclass
class ImageResult:
    png: bytes
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 1
    est_cost_usd: float = 0.0
    duration_s: float = 0.0


# Markers of a 429 that is NOT a short-lived rate limit: the key has no quota at all
# (billing not enabled / free tier with limit 0). Retrying cannot help.
# ("check your plan and billing details" is in every Gemini 429, so plain "billing" is not
# a marker.)
_QUOTA_MARKERS = ("free_tier", "free tier", "billing is not enabled", "billing not enabled",
                  "enable billing", "billing to be enabled")
_LIMIT_ZERO = re.compile(r"limit:\s*0(?![\d.])")
_RATE_MARKERS = ("per_minute", "perminute", "per minute", "per_second", "persecond")
_DAY_MARKERS = ("per_day", "perday", "per day")
_RETRY_IN = re.compile(r"retry in\s*([\d.]+)\s*s", re.I)


def _seconds(value) -> float | None:
    """'23s' / '1.5s' / 23 -> seconds; None if unreadable."""
    try:
        return max(0.0, float(str(value).strip().rstrip("s")))
    except (TypeError, ValueError):
        return None


def parse_google_error(details) -> dict:
    """Quota metric / id / limit and the server's retry delay from a Google API error body
    (``google.rpc.QuotaFailure`` and ``google.rpc.RetryInfo``). Never raises; never
    contains credentials (the body has none)."""
    out: dict = {}
    try:
        body = details.get("error", details) if isinstance(details, dict) else {}
        for d in body.get("details") or []:
            if not isinstance(d, dict):
                continue
            kind = str(d.get("@type", ""))
            if kind.endswith("QuotaFailure"):
                for v in d.get("violations") or []:
                    if isinstance(v, dict):
                        out.setdefault("quota_metric", str(v.get("quotaMetric") or ""))
                        out.setdefault("quota_id", str(v.get("quotaId") or ""))
                        if v.get("quotaValue") is not None:
                            out.setdefault("quota_value", str(v.get("quotaValue")))
            elif kind.endswith("RetryInfo"):
                delay = _seconds(d.get("retryDelay"))
                if delay is not None:
                    out["retry_after"] = delay
    except Exception:  # an odd body never breaks error handling
        return out
    return out


class ProviderError(RuntimeError):
    """Upstream call failed or returned something unusable.

    For a 429 the provider may pass what the error body says: the quota metric / id / limit
    (``quota_metric``, ``quota_id``, ``quota_value``) and the server's retry delay
    (``retry_after`` seconds)."""

    def __init__(self, message: str, *, status: int | None = None, quota_metric: str = "",
                 quota_id: str = "", quota_value: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.quota_metric = quota_metric
        self.quota_id = quota_id
        self.quota_value = quota_value
        if retry_after is None:
            m = _RETRY_IN.search(message)
            retry_after = float(m.group(1)) if m else None
        self.retry_after = retry_after

    @property
    def quota_text(self) -> str:
        """'metric=... id=... limit=...' for logs ("" if the body named none)."""
        bits = [f"metric={self.quota_metric}" if self.quota_metric else "",
                f"id={self.quota_id}" if self.quota_id else "",
                f"limit={self.quota_value}" if self.quota_value else ""]
        return " ".join(b for b in bits if b)

    @property
    def rate_evidence(self) -> bool:
        """The 429 says it is a short-lived rate limit: a server retry delay (RetryInfo /
        "retry in Ns") or a per-minute / per-second quota metric."""
        msg = f"{self} {self.quota_metric} {self.quota_id}".lower()
        return self.retry_after is not None or any(m in msg for m in _RATE_MARKERS)

    @property
    def quota_exhausted(self) -> bool:
        """A 429 that retrying cannot fix: out of credit / billing (prepaid balance used up,
        billing not enabled, free tier), a quota of 0 or a per-day quota used up.

        Gemini answers all 429s with RESOURCE_EXHAUSTED. "You exceeded your current quota,
        please check your plan and billing details" WITHOUT a retry delay or a per-minute /
        per-second metric was a real out-of-credit case (a prepaid balance ran out mid-book;
        5 retries all failed), so it counts as exhausted. "limit: 0" / quotaValue 0, "free
        tier", a billing hint or a per-day metric always count. A 429 with rate evidence
        (``rate_evidence``) does not; neither does a 429 without any quota wording (e.g.
        "Resource has been exhausted"), which is retried like any transient error."""
        if self.status != 429:
            return False
        msg = f"{self} {self.quota_metric} {self.quota_id}".lower()
        if (_LIMIT_ZERO.search(msg) or self.quota_value.strip() == "0"
                or any(m in msg for m in _QUOTA_MARKERS)):
            return True
        if self.rate_evidence:
            return False
        return (any(m in msg for m in _DAY_MARKERS)
                or "exceeded your current quota" in msg)

    @property
    def rate_limited(self) -> bool:
        """A 429 rate limit worth waiting for (retry after the delay / with backoff): it
        carries rate evidence and is not an exhausted quota."""
        return self.status == 429 and self.rate_evidence and not self.quota_exhausted


def provider_error_from_google(exc, prefix: str) -> ProviderError:
    """``ProviderError`` from a google-genai ``APIError``: message plus, for a 429, the
    quota metric / id / limit and the server's retry delay from the error body."""
    info = parse_google_error(getattr(exc, "details", None))
    msg = f"{prefix} {exc.code}: {exc.message or exc.status}"
    quota = " ".join(f"{k}={info[k]}" for k in ("quota_metric", "quota_id", "quota_value")
                     if info.get(k))
    if quota:
        msg += f" [{quota}]"
    if info.get("retry_after") is not None:
        msg += f" (retry after {info['retry_after']:g}s)"
    return ProviderError(msg, status=exc.code, quota_metric=info.get("quota_metric", ""),
                         quota_id=info.get("quota_id", ""),
                         quota_value=info.get("quota_value", ""),
                         retry_after=info.get("retry_after"))


class NoImageError(ProviderError):
    """The model answered but produced no image (safety block, refusal, ...)."""

    SAFETY_REASONS = frozenset(
        {
            "SAFETY",
            "IMAGE_SAFETY",
            "PROHIBITED_CONTENT",
            "IMAGE_PROHIBITED_CONTENT",
            "BLOCKLIST",
            "SPII",
            "moderation_blocked",
        }
    )

    def __init__(self, message: str, *, finish_reason: str | None = None):
        super().__init__(message)
        self.finish_reason = finish_reason

    @property
    def is_safety(self) -> bool:
        return (self.finish_reason or "") in self.SAFETY_REASONS


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    async def edit(
        self,
        prompt: str,
        images: list[bytes],
        *,
        aspect_ratio: str = "3:4",
        size_hint: str | None = None,
    ) -> ImageResult:
        """Generate one image from ``prompt`` conditioned on reference ``images``."""
        ...


# --- Vision grading -----------------------------------------------------------------


@dataclass
class Box:
    """A detected region, normalised 0..1, origin top-left.

    ``kind`` is ``face`` (any face, hero or not), ``figure`` (a character's whole visible
    body; text must never sit over one in the middle band) or ``key_object`` (something
    the page needs, e.g. a ``must_be_visible`` item)."""

    label: str
    x: float
    y: float
    w: float
    h: float
    kind: str = "key_object"


@dataclass
class CallMeta:
    """Cost/latency of one vision call (recorded in ``api_calls``)."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    duration_s: float = 0.0


IDENTITY_PENALTY = 0.3
CONTRADICTION_PENALTY = 0.15  # per definite contradiction of the description
CONTRADICTION_PENALTY_CAP = 0.45


@dataclass
class GradeResult:
    likeness: float  # 0..1, how recognisably the hero matches the reference child
    fidelity: float  # 0..1, how well the image matches the caption/spec
    has_text_artifacts: bool = False  # letters, signs, speech bubbles, watermarks
    issues: list[str] = field(default_factory=list)  # short, actionable problems
    boxes: list[Box] = field(default_factory=list)  # faces / key objects
    meta: CallMeta = field(default_factory=CallMeta)
    # Identity checks of the hero vs. the ORIGINAL PHOTO. Stylisation is fine; a
    # different skin tone, hair colour or hair type (straight/wavy/curly/coily) is a
    # different child and fails the picture whatever the likeness score.
    skin_tone_match: bool = True
    hair_colour_match: bool = True
    hair_texture_match: bool = True
    identity_notes: str = ""
    # Pages only: the hero's hair vs. the HERO REFERENCE image (length, shape/silhouette,
    # style, colour - not just the type category), and every family member on the page
    # vs. the family reference sheet (apparent age, hair colour, hairstyle). Both are True
    # when there is nothing to compare (no hero reference / no family member or sheet) and
    # fail the picture like the identity checks above.
    hero_hair_match: bool = True
    family_match: bool = True
    family_notes: str = ""
    # Definite violations of the description (wrong count or colour, a missing
    # must-be-visible item, the wrong character, a prop that does not match its Props
    # text). Any one fails the picture; ``issues`` are softer improvement notes.
    contradictions: list[str] = field(default_factory=list)
    # Pages only: every supporting (non-family) character vs. its reference sheet and
    # description - look, outfit/costume for this page and size relative to the hero
    # child. True when there is nothing to compare (no supporting character listed).
    cast_match: bool = True
    cast_notes: str = ""
    # Pages only: does the hero's expression show the emotion the page asks for? True
    # when no expression is requested or the face is not visible.
    expression_match: bool = True
    expression_notes: str = ""
    # Pages only: other characters' faces vs. this page's ``expressions`` (the wolf dazed
    # after tripping, not grinning). True when none is given. A definite error when false.
    cast_expression_match: bool = True
    cast_expression_notes: str = ""
    # Rendering style vs. the hero reference image (pages) and the style text: a clearly
    # different rendering (a flat 2D painting in a low-poly 3D book) is a definite error.
    style_match: bool = True
    style_notes: str = ""
    # The hero's look rules (hero reference and pages), all definite errors when false:
    # ``hero_child_match`` - the hero is a child of about 7-9 (never an adult or elderly
    # face, beard, moustache or wrinkles, whatever age the photo shows);
    # ``hero_items_match`` - the personal items kept from the photo (glasses, headwear such
    # as a kippah, hijab, turban or cap) are drawn; ``hero_outfit_match`` - the hero wears
    # the story's hero outfit (never the photo's adult clothes such as a suit, nor another
    # character's clothes). True when there is nothing to check.
    hero_child_match: bool = True
    hero_items_match: bool = True
    hero_outfit_match: bool = True
    hero_look_notes: str = ""
    # Speaker-portrait gate (pages with ``presence`` only): which of the asked character
    # names the grader sees in the picture in ANY form (partly hidden, in disguise, only
    # ears or eyes). None = not asked or unknown - a portrait is then never drawn.
    visible_characters: list[str] | None = None

    @property
    def hero_look_ok(self) -> bool:
        return self.hero_child_match and self.hero_items_match and self.hero_outfit_match

    @property
    def identity_ok(self) -> bool:
        return (self.skin_tone_match and self.hair_colour_match and self.hair_texture_match
                and self.hero_hair_match and self.family_match and self.hero_look_ok)

    @property
    def hard_failures(self) -> int:
        """Contradictions plus a failed cast, expression or style check (each fails a
        picture)."""
        return (len(self.contradictions) + (not self.cast_match)
                + (not self.expression_match) + (not self.cast_expression_match)
                + (not self.style_match))

    @property
    def score(self) -> float:
        """Ranking score used to pick the best attempt (text artifacts, identity
        mismatches, contradictions and cast / expression mismatches penalised)."""
        s = 0.5 * self.likeness + 0.5 * self.fidelity
        s -= min(CONTRADICTION_PENALTY * self.hard_failures, CONTRADICTION_PENALTY_CAP)
        if self.has_text_artifacts:
            s -= 0.25
        if not self.identity_ok:
            s -= IDENTITY_PENALTY
        return s


AGE_GROUPS = ("child", "teen", "adult", "elderly", "unknown")


@dataclass
class PhotoTraits:
    """What the vision model estimated from the uploaded photo (family matching, the hero
    prompt and the identity checks).

    The photo may show anyone (a parent or grandparent uploads their own picture); the hero
    is always drawn as a CHILD VERSION of that person (``app.pipeline.family``). Glasses and
    headwear are personal items kept on every page; facial hair is never drawn on the
    child."""

    skin_tone: int  # 1 (very fair) .. 6 (very deep)
    hair_colour: str  # blonde | red | light_brown | dark_brown | black | grey | unknown
    hair_texture: str  # straight | wavy | curly | coily | unknown
    meta: CallMeta = field(default_factory=CallMeta)
    age_group: str = "unknown"  # child | teen | adult | elderly | unknown
    glasses: str = ""  # e.g. "round black-rimmed glasses"; "" = none
    headwear: str = ""  # e.g. "a dark navy skullcap (kippah)"; "" = none
    facial_hair: bool = False  # beard / moustache in the photo (never drawn on the child)
    hair_covered: bool = False  # headwear hides most or all of the hair in the photo
    hair_style: str = ""  # length + cut as photographed, e.g. "short, tousled, side parting"

    @property
    def is_child(self) -> bool:
        """A child photo (or an unknown age): drawn as is, no 'child version' wording."""
        return self.age_group in ("child", "unknown")


@dataclass
class SheetCheck:
    """The vision check of a per-book family reference sheet (one call)."""

    count_ok: bool = True  # exactly the expected number of figures, no extra people
    no_child: bool = True  # no child / hero figure on the sheet
    age_ok: bool = True  # every figure looks its expected apparent age
    hair_ok: bool = True  # every figure's hair colour and hairstyle match the variant
    issues: list[str] = field(default_factory=list)  # concrete fixes for a regeneration
    meta: CallMeta = field(default_factory=CallMeta)

    @property
    def failures(self) -> int:
        return sum(not ok for ok in (self.count_ok, self.no_child, self.age_ok, self.hair_ok))

    @property
    def passed(self) -> bool:
        return self.failures == 0


@dataclass
class CastSheetCheck:
    """The vision check of a supporting character's reference sheet (one call)."""

    figure_ok: bool = True  # exactly one figure (the height bar is not one), no scenery
    looks_ok: bool = True  # clearly this character, matching description and outfit
    size_ok: bool = True  # height bar drawn and the character about the stated size
    issues: list[str] = field(default_factory=list)  # concrete fixes for a regeneration
    meta: CallMeta = field(default_factory=CallMeta)

    @property
    def failures(self) -> int:
        return sum(not ok for ok in (self.figure_ok, self.looks_ok, self.size_ok))

    @property
    def passed(self) -> bool:
        return self.failures == 0


@dataclass
class PortraitCheck:
    """The vision check of a speaker portrait (one call)."""

    character_ok: bool = True  # clearly this character (look, costume)
    single_ok: bool = True  # exactly one head-and-shoulders figure, plain background
    expression_ok: bool = True  # shows the requested expression
    issues: list[str] = field(default_factory=list)  # concrete fixes for a regeneration
    meta: CallMeta = field(default_factory=CallMeta)
    # A close-up: head and shoulders, the face filling most of the frame (a real Jack
    # portrait came back as a small full-body figure and passed).
    closeup_ok: bool = True

    @property
    def failures(self) -> int:
        return sum(not ok for ok in (self.character_ok, self.single_ok, self.expression_ok,
                                     self.closeup_ok))

    @property
    def passed(self) -> bool:
        return self.failures == 0


@runtime_checkable
class VisionGrader(Protocol):
    name: str
    model: str

    async def grade(
        self,
        image: bytes,
        *,
        hero: bytes | None,
        photo: bytes,
        caption: str,
        must_be_visible: list[str],
        subject: str = "page",
        traits: PhotoTraits | None = None,
        props: Sequence[str] = (),
        family_ref: bytes | None = None,
        family: Sequence[str] = (),
        cast_refs: Sequence[bytes] = (),
        cast: Sequence[str] = (),
        expression: str = "",
        hero_items: Sequence[str] = (),
        hero_outfit: str = "",
        presence: Sequence[str] = (),
        style: str = "",
        cast_expressions: Sequence[str] = (),
        hero_age: str = "",
    ) -> GradeResult:
        """One call. ``subject`` is ``hero`` (character reference sheet) or ``page``.
        ``traits`` (the photo's measured skin tone / hair, if known) make the identity
        checks and corrections concrete. ``props`` are the page's canonical prop lines
        ("beans: exactly five small magic beans ..."), checked like the description.
        ``family_ref`` (the family reference sheet, pages with a family member only) and
        ``family`` (what each family member on the page must look like, from
        ``app.pipeline.prompts.family_expectations``) drive ``family_match``.
        ``cast`` (one line per supporting character: description, size, outfit on this
        page) and ``cast_refs`` (their reference sheets) drive ``cast_match``;
        ``expression`` (the hero's requested emotion) drives ``expression_match``.
        ``hero_items`` (glasses / headwear kept from the photo) and ``hero_outfit`` (the
        story's hero outfit) drive ``hero_items_match`` / ``hero_outfit_match``;
        ``hero_child_match`` is always checked. ``presence`` (character names, pages
        with a possible speaker portrait only) fills ``visible_characters``. ``style`` (the
        key technique words of the book's style) drives ``style_match``;
        ``cast_expressions`` ('the wolf: dazed ...', one per character the page gives a face)
        drives ``cast_expression_match``. ``hero_age`` ("a teenager about 15 years old", only
        passed for a teen hero) replaces the child-age expectation of ``hero_child_match``
        and the likeness note."""
        ...

    async def check_family_sheet(self, image: bytes, *,
                                 characters: Sequence[str]) -> SheetCheck:
        """One call: does the family sheet show exactly these characters (one line each:
        name, apparent age, skin tone, hair), no child, each with the right apparent age
        and hair?"""
        ...

    async def photo_traits(self, photo: bytes) -> PhotoTraits:
        """One call: estimated skin tone and hair of the child in ``photo``."""
        ...

    # Optional (callers use getattr): one call checking a supporting character's sheet.
    # async def check_cast_sheet(self, image: bytes, *, name: str, description: str,
    #                            size: str) -> CastSheetCheck
    # Optional (callers use getattr): one call checking a speaker portrait.
    # async def check_portrait(self, image: bytes, *, name: str, description: str,
    #                          expression: str) -> PortraitCheck


# --- image size (aspect-ratio debugging) ------------------------------------------------


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the image header, or None if it cannot be read."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def aspect_off(size: tuple[int, int] | None, ratio: str, tolerance: float = 0.02) -> bool:
    """True if ``size`` (w, h) differs from ``ratio`` ("3:4") by more than ``tolerance``
    (relative). False when either is unknown."""
    try:
        a, b = (float(x) for x in ratio.split(":"))
    except ValueError:
        return False
    if size is None or not size[1] or not b:
        return False
    want, got = a / b, size[0] / size[1]
    return abs(got - want) / want > tolerance


# --- helpers -------------------------------------------------------------------


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ProviderError("unsupported reference image format")


def ensure_png(data: bytes) -> bytes:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()


def log_call(provider: str, result: ImageResult) -> None:
    size = image_size(result.png)
    log.info(
        "image call provider=%s model=%s duration=%.2fs in_tok=%d out_tok=%d est_cost=$%.4f "
        "size=%s",
        provider,
        result.model,
        result.duration_s,
        result.input_tokens,
        result.output_tokens,
        result.est_cost_usd,
        "x".join(map(str, size)) if size else "?",
    )


# Reference images per request. Gemini 3 image models (Nano Banana Pro / 2) accept up to
# 14 input images per request (Google's image-generation docs, Nov 2026; not re-verified
# against the SDK, which does not enforce it). Older 2.5 Flash Image works best with <= 3.
# OpenAI's edit endpoint accepts up to 16. IMAGE_MAX_REFS overrides all of these.
DEFAULT_MAX_REFS = 14
_MAX_REFS_BY_MODEL = {"gemini-2.5-flash-image": 3}


def max_reference_images(provider: ImageProvider) -> int:
    configured = get_settings().image_max_refs
    if configured > 0:
        return configured
    own = getattr(provider, "max_reference_images", None)
    if isinstance(own, int) and own > 0:
        return own
    return _MAX_REFS_BY_MODEL.get(str(getattr(provider, "model", "")), DEFAULT_MAX_REFS)


def get_image_provider(name: str | None = None, *, model: str | None = None) -> ImageProvider:
    """Build the provider selected by IMAGE_PROVIDER (gemini | openai | fake).

    ``model`` overrides the configured model id (PAGE_IMAGE_MODEL, PREGEN_IMAGE_MODEL)."""
    settings = get_settings()
    name = (name or settings.image_provider or "gemini").strip().lower()
    if name == "fake":
        from app.providers.fake import FakeProvider

        # The fake draws placeholders for free but reports the requested model id, so the
        # tier's model shows up in api_calls during offline runs and tests.
        return FakeProvider(model=model) if model else FakeProvider()
    if name == "gemini":
        from app.providers.gemini import GeminiProvider

        return GeminiProvider(api_key=settings.gemini_api_key,
                              model=model or settings.gemini_image_model,
                              image_size=settings.gemini_image_size,
                              ref_max_side=settings.image_ref_max_side,
                              ref_level=settings.image_ref_media_resolution,
                              ref_level_first=settings.image_ref_media_resolution_first)
    if name == "openai":
        from app.providers.openai_image import OpenAIImageProvider

        return OpenAIImageProvider(api_key=settings.openai_api_key,
                                   model=model or settings.openai_image_model)
    raise ValueError(f"unknown IMAGE_PROVIDER: {name!r}")


def get_vision_grader(name: str | None = None) -> VisionGrader:
    """Vision grader for the quality loop and family matching. IMAGE_PROVIDER=fake -> the
    offline fake grader; otherwise Gemini (GEMINI_VISION_MODEL) - also for the OpenAI image
    provider, which then needs GEMINI_API_KEY too."""
    settings = get_settings()
    name = (name or settings.image_provider or "gemini").strip().lower()
    if name == "fake":
        from app.providers.fake_grader import FakeGrader

        return FakeGrader()
    from app.providers.gemini_vision import GeminiVisionGrader

    return GeminiVisionGrader(api_key=settings.gemini_api_key, model=settings.gemini_vision_model,
                              thinking_level=settings.grader_thinking_level,
                              image_max_side=settings.grader_image_max_side,
                              media_level=settings.grader_media_resolution,
                              media_level_first=settings.grader_media_resolution_first)
