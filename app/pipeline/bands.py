"""Cheap local check (no API call) for a blank band along an edge of a page picture.

Image models sometimes leave a plain, unpainted strip across the top or bottom of a page
(seen on a real book: a flat cream band over the top ~15% of pages 15 and 16), often
because the prompt asked for a "plain" quiet zone there. The vision grader did not catch
it, and the page is printed full-bleed, so the band shows. ``find_blank_band`` runs before
the grade call; a band fails the attempt at once (no grade call, no grading cost) with
``BAND_ISSUE`` as its correction.

How it works: the picture is shrunk to at most ``WORK_SIDE`` px (box filter, which also
averages away pixel noise and JPEG grain), then, from each edge inwards, lines (rows for
top/bottom, columns for left/right) are added to the band while

* the line itself is flat: the largest per-channel standard deviation along it is at most
  ``LINE_STD_MAX`` (0..255 scale), and
* its mean colour stays within ``DRIFT_MAX`` per channel of the edge line's colour (so a
  smooth sky gradient, whose rows are each flat but drift, stops growing quickly).

A band deeper than ``MIN_FRACTION`` of the height (top/bottom) or width (left/right) is
reported if its colour looks like an unpainted area: light and nearly unsaturated
(paper, cream, white, light grey: value >= ``PAPER_MIN_VALUE`` and saturation <=
``PAPER_MAX_SATURATION``) or almost black (value <= ``BLACK_MAX_VALUE``, a border). The
colour guard keeps flat painted areas - a flat blue pixel-art sky, a dark-green lawn -
from failing; an unshaded white or pale-grey area painted perfectly flat edge to edge
could still be flagged (see the README's quality-loop section).

Padding seams (``find_seam_band``, top and bottom only) - the main cause, confirmed on
the stored pages of three real books (all 896x1200, i.e. the right 3:4 size): the image
model paints a squarer picture INSIDE the 3:4 canvas and fills the leftover top and/or
bottom with a strip - a blurred zoomed copy of the picture, a flat wall / white strip, or
an unrelated sky - joined by a hard, perfectly straight, full-width horizontal seam. On a
grey copy ``SEAM_WORK_WIDTH`` px wide, the absolute difference between neighbouring rows
is averaged in ``SEAM_BLOCKS`` column blocks. A row (two rows, for resampling) is a seam
candidate in the outer ``SEAM_MAX_DEPTH`` of the height where most blocks jump: a block
counts when its jump is at least ``SEAM_MIN_JUMP`` and at least 3x (+2) the mean of the
rows 3-7 above and below it (a sharp step, not a gradual change). ``cov`` = the share of
blocks that jump at the best row. A seam is reported when the strip beyond it is much
smoother than the picture just inside it (``ratio`` = mean dx+dy detail of the strip /
the same of an equally deep band inside, at least 16 rows):

* ``cov >= SEAM_STRONG_COV`` (0.7) and (``ratio <= SEAM_MAX_RATIO`` (0.35), or the strip is
  near-featureless - detail <= ``SEAM_FLAT_DETAIL`` - and ``ratio <= 0.5``), or
* ``cov >= SEAM_WEAK_COV`` (0.6) and ``ratio <= SEAM_WEAK_RATIO`` (0.15), or
* ``cov >= SEAM_MIN_COV`` (0.5) and ``ratio <= SEAM_FAINT_RATIO`` (0.10) - a blurred copy
  keeps the local colours, so fewer blocks jump at its seam;

and the strip is at least ``SEAM_MIN_FRACTION`` of the height deep. Calibrated on the 60
real pages (see to-do.md): legit full-width horizontals - ceiling beams, a door lintel,
a stone-floor edge, a tablecloth hem, a threshold step - fail the coverage or the
smoothness test because the scene beyond them keeps its detail.

Blurred bands (``find_blurred_band``, top and bottom only, a fallback for a strip whose
boundary is too soft for the seam test, e.g. a plain table-top filling the bottom; at
least ``BLUR_MIN_FRACTION`` deep): a real Red book had heavily blurred / smeared strips
of the scene across the top or bottom of pages 10 and 12 (not flat colour, so the check
above missed them, and the grader only called them an issue).
On a grey-level copy at most ``BLUR_WORK_SIDE`` px, the detail of every row (mean
absolute difference to the right and lower neighbour pixel) is measured in
``BLUR_STRIPS`` vertical strips. A band is reported when ALL of these hold:

* the picture is detailed elsewhere: the mean row detail of the middle half of the
  picture (``ref``) is at least ``BLUR_MIN_REF`` (a softly painted / defocused picture as
  a whole never fails);
* detail reaches ``BLUR_ONSET`` x ``ref`` at about the same depth from the edge (the
  median strip's, +- ``BLUR_STRAIGHT`` of the height) in at least half of the strips, and
  clearly EARLIER in at most one strip - a straight horizontal boundary, as left by
  padding or outpainting. Strips may turn detailed later (plain scenery next to the
  band). A sky above trees, hills or a castle has a ragged boundary and passes;
* that depth is more than ``BLUR_MIN_FRACTION`` (15%) of the height;
* the band is blurred (its mean detail is at most ``BLUR_MAX_RATIO`` x that of the middle
  half) or smeared (the edge row stretched outwards: almost no vertical detail,
  ``SMEAR_MAX_Y_RATIO``, and at most ``SMEAR_MAX_X_RATIO`` of the horizontal detail);
* the band still has visible structure: its rows' mean colour spread (largest per-channel
  standard deviation along a row) is at least ``BLUR_MIN_STRUCTURE``. A smooth gradient
  sky, fog or a plain wall has nearly uniform rows and passes; a smeared strip of the
  scene keeps its blotchy colours and fails.

Known risk: a soft, cloudy painted sky above a perfectly straight horizon (sea, a flat
field) can look like a blurred band; thresholds are calibrated on synthetic images only.
"""

from __future__ import annotations

import io
import logging
from array import array
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageOps, ImageStat, UnidentifiedImageError

log = logging.getLogger("app.pipeline.bands")

WORK_SIDE = 256
LINE_STD_MAX = 5.0
DRIFT_MAX = 8.0
MIN_FRACTION = 0.08
PAPER_MIN_VALUE = 0.70
PAPER_MAX_SATURATION = 0.22  # a flat pale-blue sky (sat ~0.29) is painted
BLACK_MAX_VALUE = 0.08

BLUR_WORK_SIDE = 384
BLUR_MIN_FRACTION = 0.15  # real low-detail floors / roads at 10-14% were false positives

SEAM_WORK_WIDTH = 448
SEAM_BLOCKS = 32
SEAM_MIN_JUMP = 6.0  # mean |row - next row| in a block (0..255)
SEAM_MAX_DEPTH = 0.45
SEAM_MIN_FRACTION = 0.05
SEAM_MIN_COV = 0.5
SEAM_WEAK_COV = 0.6
SEAM_STRONG_COV = 0.7
SEAM_MAX_RATIO = 0.35
SEAM_WEAK_RATIO = 0.15
SEAM_FAINT_RATIO = 0.10
SEAM_FLAT_DETAIL = 2.0
BLUR_STRIPS = 12
BLUR_ONSET = 0.35  # a strip's band ends where its row detail first reaches 35% of ref
BLUR_MAX_RATIO = 0.18  # band detail / ref
BLUR_MIN_REF = 4.0  # mean row detail of the middle half (0..510 scale)
BLUR_STRAIGHT = 0.04  # onset spread (share of the height) for a "straight" boundary
BLUR_MIN_STRUCTURE = 6.0  # mean per-row colour spread inside the band (0..255)
SMEAR_MAX_Y_RATIO = 0.05  # smeared band: vertical detail / the middle's vertical detail
SMEAR_MAX_X_RATIO = 0.5  # ... and horizontal detail / the middle's horizontal detail

EDGE_WORDS = {"top": "at the top", "bottom": "at the bottom", "left": "down the left side",
              "right": "down the right side"}


@dataclass(frozen=True)
class Band:
    edge: str  # top | bottom | left | right
    fraction: float  # depth as a share of the height (top/bottom) or width (left/right)
    colour: tuple[int, int, int]
    kind: str = "blank"  # blank (flat, unpainted) | blurred (smeared strip) | seam (padding)

    @property
    def issue(self) -> str:
        if self.kind == "seam":
            return seam_issue(self.edge)
        return blurred_band_issue(self.edge) if self.kind == "blurred" else band_issue(self.edge)


def band_issue(edge: str) -> str:
    where = EDGE_WORDS.get(edge, "along the edge")
    return (f"paint the whole picture edge to edge; no empty band {where} - continue the "
            "scene (sky, grass, wall, floor) right to the edge")


def blurred_band_issue(edge: str) -> str:
    where = EDGE_WORDS.get(edge, "along the edge")
    return (f"paint the whole picture sharply edge to edge; no blurred, smeared or "
            f"out-of-focus strip {where} - continue the scene in the same crisp style right "
            "to the edge, composed for the tall frame from the start")


def seam_issue(edge: str) -> str:
    where = EDGE_WORDS.get(edge, "along the edge")
    return (f"compose ONE tall portrait picture that fills the whole frame; no separate "
            f"strip {where} joined by a straight seam (no padding, blurred copy, plain wall "
            "or extra sky) - the scene continues right to the edge")


def _seam_from_top(gray: Image.Image) -> tuple[float, float, float] | None:
    """(depth fraction, cov, ratio) of a padding seam near the TOP of ``gray``, or None."""
    w, h = gray.size
    if w < SEAM_BLOCKS or h < 40:
        return None
    dyi = ImageChops.difference(gray, ImageChops.offset(gray, 0, 1)).crop((0, 1, w, h))
    dxi = ImageChops.difference(gray, ImageChops.offset(gray, 1, 0)).crop((1, 0, w, h))
    flat = _floats(dyi.resize((SEAM_BLOCKS, h - 1), Image.Resampling.BOX))
    rows = [flat[i * SEAM_BLOCKS:(i + 1) * SEAM_BLOCKS] for i in range(h - 1)]
    n = len(rows)
    best_y, best_cov = -1, 0
    for y in range(max(3, int(SEAM_MIN_FRACTION * n) - 1), int(SEAM_MAX_DEPTH * n)):
        near = [rows[i] for i in (*range(max(0, y - 7), y - 2), *range(y + 4, min(n, y + 9)))]
        cov = 0
        for k in range(SEAM_BLOCKS):
            jump = max(rows[y][k], rows[y + 1][k])
            base = sum(r[k] for r in near) / len(near)
            if jump >= SEAM_MIN_JUMP and jump >= 3 * base + 2:
                cov += 1
        if cov > best_cov:
            best_y, best_cov = y, cov
    cov = best_cov / SEAM_BLOCKS
    if cov < SEAM_MIN_COV:
        return None
    y = best_y
    depth = (y + 1) / h
    if depth < SEAM_MIN_FRACTION:
        return None
    detail = ImageChops.add(dxi.crop((0, 0, w - 1, h - 1)), dyi.crop((0, 0, w - 1, h - 1)))
    strip = max(2, y - 2)
    beyond = ImageStat.Stat(detail.crop((0, 0, w - 1, strip))).mean[0]
    inside = ImageStat.Stat(detail.crop((0, y + 3, w - 1, min(h - 1, y + 3 + max(strip, 16))))
                            ).mean[0]
    ratio = beyond / max(inside, 0.1)
    if cov >= SEAM_STRONG_COV:
        ok = ratio <= SEAM_MAX_RATIO or (beyond <= SEAM_FLAT_DETAIL and ratio <= 0.5)
    elif cov >= SEAM_WEAK_COV:
        ok = ratio <= SEAM_WEAK_RATIO
    else:  # a blurred copy keeps the local colours, so fewer blocks jump at the seam
        ok = ratio <= SEAM_FAINT_RATIO
    return (round(depth, 3), round(cov, 2), round(ratio, 2)) if ok else None


def find_seam_band(rgb: Image.Image) -> Band | None:
    """A padding strip across the top or bottom joined by a straight full-width seam (see
    the module docstring), the deeper one if both, or None."""
    w, h = rgb.size
    if w > SEAM_WORK_WIDTH:
        rgb = rgb.resize((SEAM_WORK_WIDTH, round(h * SEAM_WORK_WIDTH / w)),
                         Image.Resampling.BOX)
    elif w < SEAM_WORK_WIDTH:  # small inputs (tests): same scale as real pages
        rgb = rgb.resize((SEAM_WORK_WIDTH, round(h * SEAM_WORK_WIDTH / w)),
                         Image.Resampling.BILINEAR)
    found = []
    for edge, im in (("top", rgb), ("bottom", ImageOps.flip(rgb))):
        hit = _seam_from_top(im.convert("L"))
        if hit is not None:
            depth = hit[0]
            strip = im.crop((0, 0, im.size[0], max(1, int(depth * im.size[1]))))
            colour = tuple(int(round(c)) for c in ImageStat.Stat(strip).mean[:3])
            found.append(Band(edge, depth, colour, kind="seam"))  # type: ignore[arg-type]
            log.debug("seam edge=%s depth=%.3f cov=%.2f ratio=%.2f", edge, *hit)
    return max(found, key=lambda b: b.fraction) if found else None


def _line_stats(im: Image.Image, box: tuple[int, int, int, int]) -> tuple[list[float], float]:
    """Mean colour and the largest per-channel standard deviation of one line (C code)."""
    st = ImageStat.Stat(im.crop(box))
    return st.mean, max(st.stddev)


def _looks_unpainted(rgb) -> bool:
    hi, lo = max(rgb) / 255.0, min(rgb) / 255.0
    sat = 0.0 if hi == 0 else (hi - lo) / hi
    return (hi >= PAPER_MIN_VALUE and sat <= PAPER_MAX_SATURATION) or hi <= BLACK_MAX_VALUE


def _band_depth(im: Image.Image, edge: str, limit: int) -> tuple[int, list[float]]:
    """Number of flat, same-coloured lines from ``edge`` inwards (at most ``limit``), and
    the edge line's colour. Stops at the first line that is not flat - normally line 1."""
    w, h = im.size

    def line(i: int) -> tuple[int, int, int, int]:
        return {"top": (0, i, w, i + 1), "bottom": (0, h - 1 - i, w, h - i),
                "left": (i, 0, i + 1, h), "right": (w - 1 - i, 0, w - i, h)}[edge]

    edge_mean, std = _line_stats(im, line(0))
    if std > LINE_STD_MAX:
        return 0, edge_mean
    depth = 1
    while depth < limit:
        mean, std = _line_stats(im, line(depth))
        if std > LINE_STD_MAX or max(abs(a - b) for a, b in zip(mean, edge_mean,
                                                                 strict=True)) > DRIFT_MAX:
            break
        depth += 1
    return depth, edge_mean


def _floats(im: Image.Image) -> array:
    """Pixel values of a single-band image as floats (no ``getdata``, deprecated in
    Pillow 12+)."""
    return array("f", im.convert("F").tobytes())


def _row_means(im: Image.Image) -> list[float]:
    """Mean of every row of a single-band image (float precision, C code)."""
    w, h = im.size
    return list(_floats(im.resize((1, h), Image.Resampling.BOX)))


def _smooth(values: list[float], r: int = 1) -> list[float]:
    """Centred moving average over 2r+1 values (pixel art: detail sits on block edges)."""
    out = []
    for i in range(len(values)):
        win = values[max(0, i - r):i + r + 1]
        out.append(sum(win) / len(win))
    return out


def _blur_from_top(rgb: Image.Image, min_fraction: float) -> tuple[float, tuple] | None:
    """(depth fraction, mean colour) of a blurred band at the TOP of ``rgb``, or None."""
    gray = rgb.convert("L")
    w, h = gray.size
    if w < BLUR_STRIPS * 2 or h < 16:
        return None
    # offset() wraps around: drop the first row / column, which compare opposite edges.
    dx = ImageChops.difference(gray, ImageChops.offset(gray, 1, 0)).crop((1, 1, w, h))
    dy = ImageChops.difference(gray, ImageChops.offset(gray, 0, 1)).crop((1, 1, w, h))
    detail = ImageChops.add(dx, dy)
    dw, dh = detail.size
    rows = _row_means(detail)
    middle = rows[dh // 4: 3 * dh // 4]
    ref = sum(middle) / len(middle) if middle else 0.0
    if ref < BLUR_MIN_REF:
        return None  # the picture is soft everywhere: not a band
    limit = dh // 2
    onsets = []
    for k in range(BLUR_STRIPS):
        x0, x1 = k * dw // BLUR_STRIPS, (k + 1) * dw // BLUR_STRIPS
        prof = _smooth(_row_means(detail.crop((x0, 0, x1, dh))), max(1, dh // 96))
        onsets.append(next((y for y in range(limit) if prof[y] >= BLUR_ONSET * ref), limit))
    depth = sorted(onsets)[len(onsets) // 2]
    tol = BLUR_STRAIGHT * dh
    # A straight boundary: most strips turn detailed at the same depth. A strip may turn
    # detailed later (the scene next to the band is plain there), but at most one may
    # turn detailed clearly EARLIER - sharp detail inside the band means it is a ragged
    # boundary (a sky above trees or hills), not a band.
    if sum(abs(o - depth) <= tol for o in onsets) < BLUR_STRIPS // 2:
        return None
    if sum(o < depth - tol for o in onsets) > 1:
        return None
    if depth / dh <= min_fraction:
        return None
    inner = max(1, int(depth - tol))
    box = (0, 0, dw, inner)
    band_x = ImageStat.Stat(dx.crop(box)).mean[0]
    band_y = ImageStat.Stat(dy.crop(box)).mean[0]
    mid_box = (0, dh // 4, dw, 3 * dh // 4)
    ref_x = max(ImageStat.Stat(dx.crop(mid_box)).mean[0], 1e-6)
    ref_y = max(ImageStat.Stat(dy.crop(mid_box)).mean[0], 1e-6)
    blurred = (band_x + band_y) <= BLUR_MAX_RATIO * (ref_x + ref_y)
    # smeared: the edge row stretched outwards - no vertical change, soft horizontal change
    smeared = band_y <= SMEAR_MAX_Y_RATIO * ref_y and band_x <= SMEAR_MAX_X_RATIO * ref_x
    if not (blurred or smeared):
        return None
    step = max(1, inner // 48)
    spreads = [max(ImageStat.Stat(rgb.crop((0, y, w, y + 1))).stddev)
               for y in range(0, inner, step)]
    if sum(spreads) / len(spreads) < BLUR_MIN_STRUCTURE:
        return None  # nearly uniform rows: smooth sky, fog or a plain wall
    colour = tuple(int(round(c)) for c in ImageStat.Stat(rgb.crop((0, 0, w, inner))).mean)
    return round((depth + 1) / h, 3), colour


def find_blurred_band(rgb: Image.Image, min_fraction: float = BLUR_MIN_FRACTION
                      ) -> Band | None:
    """A blurred / smeared band across the top or bottom of ``rgb`` (see the module
    docstring), the deeper one if both, or None."""
    found = []
    for edge, im in (("top", rgb), ("bottom", ImageOps.flip(rgb))):
        hit = _blur_from_top(im, min_fraction)
        if hit is not None:
            found.append(Band(edge, hit[0], hit[1], kind="blurred"))  # type: ignore[arg-type]
    return max(found, key=lambda b: b.fraction) if found else None


def find_blank_band(png: bytes, min_fraction: float = MIN_FRACTION) -> Band | None:
    """The deepest blank band along any edge deeper than ``min_fraction``; else a padding
    seam across the top or bottom (``find_seam_band``); else a blurred band
    (``find_blurred_band``); or None. Never raises: an image that cannot be read counts as
    no band (the grader decides)."""
    try:
        with Image.open(io.BytesIO(png)) as src:
            src.draft("RGB", (SEAM_WORK_WIDTH, SEAM_WORK_WIDTH))  # JPEG: decode smaller
            full = src.convert("RGB")
        im = full.copy()
        im.thumbnail((WORK_SIDE, WORK_SIDE), Image.Resampling.BOX)
        base = full.copy()
        base.thumbnail((BLUR_WORK_SIDE, BLUR_WORK_SIDE), Image.Resampling.BOX)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    w, h = im.size
    if w < 8 or h < 8:
        return None
    found: list[Band] = []
    for edge in ("top", "bottom", "left", "right"):
        size = h if edge in ("top", "bottom") else w
        depth, colour = _band_depth(im, edge, max(1, size // 2))  # never past the middle
        fraction = depth / size
        if fraction > min_fraction and _looks_unpainted(colour):
            found.append(Band(edge, round(fraction, 3),
                              tuple(int(round(c)) for c in colour)))  # type: ignore[arg-type]
    if not found:
        try:
            other = find_seam_band(full) or find_blurred_band(
                base, max(min_fraction, BLUR_MIN_FRACTION))
        except (OSError, ValueError):
            other = None
        if other is not None:
            log.info("%s band found edge=%s depth=%.0f%% colour=%s", other.kind, other.edge,
                     other.fraction * 100, other.colour)
        return other
    band = max(found, key=lambda b: b.fraction)
    log.info("blank band found edge=%s depth=%.0f%% colour=%s", band.edge,
             band.fraction * 100, band.colour)
    return band
