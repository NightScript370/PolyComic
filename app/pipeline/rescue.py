"""Free strip rescue: crop a padded strip away instead of paying for a new picture.

When the local band check (``app.pipeline.bands``) finds a padded strip along an edge -
blank, blurred or a padding seam - the picture under it is usually fine: the image model
painted a squarer picture inside the 3:4 canvas. ``rescue_strip``:

1. cuts the strip away (its detected depth plus ``SEAM_MARGIN`` of the side, so the
   soft seam line goes too);
2. crops the other axis to restore the page's aspect ratio (top / bottom strip: the
   sides; left / right strip: top and bottom), centred on the main subjects - the
   grader's face boxes (else figure boxes) when given, else the centre;
3. upscales the result back to the page size (Lanczos).

``rescue_strips`` repeats this while the band check still finds a strip on ANOTHER edge
(models often pad the top AND the bottom; the check reports the deeper one first), at
most twice. The caller grades the result (one grade call, no image call); the grader
treats a main character whose face is cut off by the frame edge as a contradiction, so a
bad crop fails like any picture. A strip deeper than ``max_depth``
(``STRIP_RESCUE_MAX_DEPTH``, default 22% of the side) or an aspect crop that would remove
more than ``max_crop`` (``STRIP_RESCUE_MAX_CROP``, default 30%, all passes together) of the
other side is not rescued: the page gets a normal paid retry.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.pipeline.bands import Band
from app.providers.base import Box

log = logging.getLogger("app.pipeline.rescue")

# Extra share of the side cut beyond the detected depth (the seam / blur boundary row).
SEAM_MARGIN = 0.01
DEFAULT_MAX_DEPTH = 0.22
DEFAULT_MAX_CROP = 0.30


@dataclass(frozen=True)
class Rescue:
    """What was cut: the strip (``edge``, ``cut`` share of that side) and the aspect crop
    (``crop`` share of the other side), and the kept box in source pixels."""

    edge: str
    kind: str
    cut: float
    crop: float
    box: tuple[int, int, int, int]  # (x0, y0, x1, y1) of the kept area in the source

    def describe(self) -> str:
        return (f"{self.kind} strip {self.edge} cut {self.cut:.0%}, aspect crop "
                f"{self.crop:.0%}")


def _subject_span(boxes: Sequence[Box], axis: str) -> tuple[float, float] | None:
    """(start, end) 0..1 of the faces (else figures) along ``axis`` ('x' or 'y')."""
    for kind in ("face", "figure"):
        picked = [b for b in boxes if b.kind == kind]
        if picked:
            if axis == "x":
                return min(b.x for b in picked), max(b.x + b.w for b in picked)
            return min(b.y for b in picked), max(b.y + b.h for b in picked)
    return None


def _offset(total: int, keep: int, span: tuple[float, float] | None) -> int:
    """Start of a ``keep``-long window in ``total`` centred on ``span`` (else centred),
    clamped to the picture."""
    centre = total / 2 if span is None else (span[0] + span[1]) / 2 * total
    return int(round(min(max(centre - keep / 2, 0), total - keep)))


def plan_rescue(size: tuple[int, int], band: Band, boxes: Sequence[Box] = (), *,
                max_depth: float = DEFAULT_MAX_DEPTH,
                max_crop: float = DEFAULT_MAX_CROP) -> Rescue | None:
    """The crop that removes ``band`` and keeps the aspect ratio of ``size``, or None when
    the strip is too deep or the aspect crop too large (then: a paid retry)."""
    w, h = size
    if w < 8 or h < 8 or band.fraction <= 0 or band.fraction > max_depth:
        return None
    vertical = band.edge in ("top", "bottom")
    side = h if vertical else w
    cut = min(side - 1, math.ceil((band.fraction + SEAM_MARGIN) * side))
    if vertical:
        keep_h = h - cut
        keep_w = max(1, round(keep_h * w / h))
        crop = 1 - keep_w / w
        if crop > max_crop:
            return None
        x0 = _offset(w, keep_w, _subject_span(boxes, "x"))
        y0 = cut if band.edge == "top" else 0
        box = (x0, y0, x0 + keep_w, y0 + keep_h)
    else:
        keep_w = w - cut
        keep_h = max(1, round(keep_w * h / w))
        crop = 1 - keep_h / h
        if crop > max_crop:
            return None
        y0 = _offset(h, keep_h, _subject_span(boxes, "y"))
        x0 = cut if band.edge == "left" else 0
        box = (x0, y0, x0 + keep_w, y0 + keep_h)
    return Rescue(band.edge, band.kind, round(cut / side, 3), round(crop, 3), box)


def rescue_strip(png: bytes, band: Band, boxes: Sequence[Box] = (), *,
                 max_depth: float = DEFAULT_MAX_DEPTH,
                 max_crop: float = DEFAULT_MAX_CROP) -> tuple[bytes, Rescue] | None:
    """(rescued PNG at the original size, what was cut), or None (too deep, too much crop,
    or unreadable). CPU only; callers run it in a thread."""
    try:
        with Image.open(io.BytesIO(png)) as src:
            im = src.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    plan = plan_rescue(im.size, band, boxes, max_depth=max_depth, max_crop=max_crop)
    if plan is None:
        return None
    out = im.crop(plan.box).resize(im.size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    log.info("strip rescue: %s -> kept box %s of %dx%d", plan.describe(), plan.box, *im.size)
    return buf.getvalue(), plan


@dataclass(frozen=True)
class RescueResult:
    """``png`` is the rescued picture (None = not rescued); ``steps`` what was cut;
    ``why`` the reason when it failed ('too deep', 'band again ...')."""

    png: bytes | None
    steps: tuple[Rescue, ...]
    why: str = ""

    def describe(self) -> str:
        return " + ".join(s.describe() for s in self.steps) or "nothing cut"


def rescue_strips(png: bytes, band: Band, find_band: Callable[[bytes], Band | None],
                  boxes: Sequence[Box] = (), *, max_depth: float = DEFAULT_MAX_DEPTH,
                  max_crop: float = DEFAULT_MAX_CROP, max_passes: int = 2) -> RescueResult:
    """``rescue_strip`` repeated while ``find_band`` still finds a strip on ANOTHER edge
    (the model often pads the top AND the bottom; the band check reports the deeper one
    first), at most ``max_passes`` times. Every strip must be at most ``max_depth`` deep
    and the aspect crops together may remove at most ``max_crop`` of the other side."""
    current, steps, kept = png, [], 1.0
    while True:
        made = rescue_strip(current, band, boxes if not steps else (), max_depth=max_depth,
                            max_crop=1.0)
        if made is None:
            return RescueResult(None, tuple(steps),
                                f"{band.kind} strip {band.edge} {band.fraction:.0%} deeper "
                                f"than {max_depth:.0%} (or unreadable)")
        current, step = made
        steps.append(step)
        kept *= 1 - step.crop
        if 1 - kept > max_crop + 1e-9:
            return RescueResult(None, tuple(steps),
                                f"aspect crop {1 - kept:.0%} over {max_crop:.0%}")
        again = find_band(current)
        if again is None:
            return RescueResult(current, tuple(steps))
        if len(steps) >= max_passes or any(s.edge == again.edge for s in steps):
            return RescueResult(None, tuple(steps),
                                f"{again.kind} band again {again.edge} {again.fraction:.0%}")
        band = again
