"""Offline vision grader (IMAGE_PROVIDER=fake and tests). No network, no cost.

By default every image passes with a face box in the middle of the picture (clear of the
top and bottom text zones), every identity check matching and no contradictions. Tests
pass ``fn`` to script grades per call, ``identity`` to set the default identity fields
(``skin_tone_match``, ``hair_colour_match``, ``hair_texture_match``, ``identity_notes``,
``hero_hair_match``, ``family_match``, ``family_notes``) or ``contradictions`` to set the
default contradictions (empty). Family sheet checks pass by default; ``sheet_fn`` scripts
them per call. Supporting-character sheet checks pass by default; ``cast_sheet_fn``
scripts them. Speaker portrait checks pass by default; ``portrait_fn`` scripts them.
Presence (``visible_characters``, asked via ``presence``) defaults to every asked name -
the conservative answer (no portrait); ``visible`` sets the names it reports instead.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageStat

from app.providers.base import (
    Box,
    CallMeta,
    CastSheetCheck,
    GradeResult,
    PhotoTraits,
    PortraitCheck,
    SheetCheck,
)

DEFAULT_FACE = Box("hero face", 0.38, 0.30, 0.24, 0.16, "face")


@dataclass
class GradeCall:
    subject: str  # hero | page
    caption: str
    must_be_visible: list[str]
    image: bytes
    has_hero_ref: bool
    traits: PhotoTraits | None = None
    props: list[str] = field(default_factory=list)
    family_ref: bytes | None = None
    family: list[str] = field(default_factory=list)
    cast_refs: list[bytes] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    expression: str = ""
    hero_items: list[str] = field(default_factory=list)
    hero_outfit: str = ""
    presence: list[str] = field(default_factory=list)
    style: str = ""
    cast_expressions: list[str] = field(default_factory=list)
    hero_age: str = ""


@dataclass
class PortraitCall:
    image: bytes
    name: str
    description: str
    expression: str


@dataclass
class SheetCall:
    image: bytes
    characters: list[str]


@dataclass
class CastSheetCall:
    image: bytes
    name: str
    description: str
    size: str


class FakeGrader:
    name = "fake"
    model = "fake-vision"

    def __init__(
        self,
        fn: Callable[[GradeCall, int], GradeResult] | None = None,
        traits: PhotoTraits | None = None,
        identity: dict | None = None,
        contradictions: list[str] | None = None,
        sheet_fn: Callable[[SheetCall, int], SheetCheck] | None = None,
        cast_sheet_fn: Callable[[CastSheetCall, int], CastSheetCheck] | None = None,
        portrait_fn: Callable[[PortraitCall, int], PortraitCheck] | None = None,
        visible: list[str] | None = None,
    ) -> None:
        """``fn(call, n)`` returns the grade for the n-th call (0-based) of this grader;
        ``sheet_fn(call, n)`` the n-th family sheet check."""
        self.fn = fn
        self.traits = traits
        self.identity = dict(identity or {})
        self.contradictions = list(contradictions or [])
        self.sheet_fn = sheet_fn
        self.cast_sheet_fn = cast_sheet_fn
        self.calls: list[GradeCall] = []
        self.sheet_calls: list[SheetCall] = []
        self.cast_sheet_calls: list[CastSheetCall] = []
        self.portrait_fn = portrait_fn
        self.portrait_calls: list[PortraitCall] = []
        self.visible = visible
        self.trait_calls = 0

    async def grade(self, image, *, hero, photo, caption, must_be_visible, subject="page",
                    traits=None, props=(), family_ref=None, family=(), cast_refs=(), cast=(),
                    expression="", hero_items=(), hero_outfit="", presence=(), style="",
                    cast_expressions=(), hero_age=""):
        call = GradeCall(subject, caption, list(must_be_visible), image, hero is not None,
                         traits, list(props), family_ref, list(family), list(cast_refs),
                         list(cast), expression, list(hero_items), hero_outfit,
                         list(presence), style, list(cast_expressions), hero_age)
        n = len(self.calls)
        self.calls.append(call)
        if self.fn is not None:
            return self.fn(call, n)
        seen = None
        if call.presence:
            seen = (list(call.presence) if self.visible is None
                    else [x for x in call.presence if x in self.visible])
        return GradeResult(likeness=0.9, fidelity=0.9, boxes=[DEFAULT_FACE],
                           meta=CallMeta(model=self.model),
                           contradictions=list(self.contradictions),
                           visible_characters=seen, **self.identity)

    async def check_family_sheet(self, image, *, characters) -> SheetCheck:
        call = SheetCall(image, list(characters))
        n = len(self.sheet_calls)
        self.sheet_calls.append(call)
        if self.sheet_fn is not None:
            return self.sheet_fn(call, n)
        return SheetCheck(meta=CallMeta(model=self.model))

    async def check_cast_sheet(self, image, *, name, description, size) -> CastSheetCheck:
        call = CastSheetCall(image, name, description, size)
        n = len(self.cast_sheet_calls)
        self.cast_sheet_calls.append(call)
        if self.cast_sheet_fn is not None:
            return self.cast_sheet_fn(call, n)
        return CastSheetCheck(meta=CallMeta(model=self.model))

    async def check_portrait(self, image, *, name, description, expression) -> PortraitCheck:
        call = PortraitCall(image, name, description, expression)
        n = len(self.portrait_calls)
        self.portrait_calls.append(call)
        if self.portrait_fn is not None:
            return self.portrait_fn(call, n)
        return PortraitCheck(meta=CallMeta(model=self.model))

    async def photo_traits(self, photo: bytes) -> PhotoTraits:
        self.trait_calls += 1
        if self.traits is not None:
            return self.traits
        return PhotoTraits(skin_tone=tone_from_pixels(photo), hair_colour="dark_brown",
                           hair_texture="wavy", meta=CallMeta(model=self.model))


def tone_from_pixels(photo: bytes) -> int:
    """Crude, deterministic stand-in: mean luminance of the photo -> 1..6."""
    with Image.open(io.BytesIO(photo)) as im:
        lum = ImageStat.Stat(im.convert("L").resize((16, 16))).mean[0]
    return max(1, min(6, 6 - int(lum // 43)))
