"""Where a page's text goes, so it never hides a face or anything the page needs.

``place_text`` uses the vision grader's boxes (faces, key objects and character bodies -
kind ``figure`` - normalised to the picture). The picture is 3:4 and the trim area is
210 x 280 mm (also 3:4) and the art is drawn full-bleed, so image coordinates and
page-zone coordinates are treated as the same space (the 3 mm bleed crop shifts them by
about 1%, covered by the padding).

Candidates: the layout's ``allowed_text_zones`` in preference order, then a centred
``middle`` band as a last resort on every page (unless the layout already lists one).

Rules:
1. The first candidate that is completely clear (no face or key-object box touches it,
   boxes grown by ``PAD``) wins - layout zones first, middle last.
2. Otherwise the least-covering acceptable zone (not ``blocked``), weighted overlap with
   faces counted x3; ties go to the earlier candidate, so middle only wins when it
   overlaps less than the alternatives.
3. Otherwise (every candidate blocked) the least severe zone, flagged ``blocked``:
   zones touching no face beat zones touching a face (a face is never covered to spare an
   object), then zones touching no figure beat ones that do, then least weighted overlap,
   then the earlier candidate.

Character bodies (``figure`` boxes) matter only for the ``middle`` band: text in the
middle of the picture must never cover any character's body, so a middle zone that
touches a figure (padded) is ``blocked``. The top and bottom zones ignore figures - they
may overlap feet, legs or the top of a head slightly - and keep the face/object rules.

Key-object boxes that are really regions (more than ``OBJECT_MAX_AREA`` of the image,
default 0.30, or wider than ``OBJECT_MAX_WIDTH`` = 85% of it - "the garden below the
window", the sky, the ground) are ignored: a text band over part of a garden hides
nothing the page needs. Faces and figures are never ignored.

A zone is ``blocked`` when it touches any face, is the middle band and touches any
figure, or covers more than
``OBJECT_MAX_COVER`` of any key object's area. The quality loop counts a blocked
placement as a failed check and regenerates with a stronger quiet-zone rule that names
the blocking item.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from app.config import get_settings
from app.providers.base import Box
from app.stories.loader import Layout, Page

# Used when a layout has no zones at all (should not happen; tests check the YAML).
FALLBACK_ZONE = {"zone": "bottom", "box": {"x": 0.06, "y": 0.75, "w": 0.88, "h": 0.19}}
PAD = 0.02
FACE_WEIGHT = 3.0
FIGURE_WEIGHT = 1.0  # middle band only
# A zone may cover at most this share of any key object's box (more = blocked).
OBJECT_MAX_COVER = 0.20
# Key-object boxes bigger than this are regions, not objects (OBJECT_MAX_AREA overrides
# the area share; the width share is fixed).
OBJECT_MAX_AREA = 0.30
OBJECT_MAX_WIDTH = 0.85

log = logging.getLogger("app.pipeline.placement")

ZONE_WHERE = {"top": "top fifth", "bottom": "bottom fifth", "middle": "middle band"}


@dataclass(frozen=True)
class TextZone:
    """A text box normalised 0..1 to the TRIM area, origin top-left, y down."""

    zone: str
    x: float
    y: float
    w: float
    h: float

    def as_dict(self) -> dict:
        return asdict(self)


# Last-resort centred band, about the height of the top/bottom zones.
MIDDLE_ZONE = TextZone("middle", 0.08, 0.41, 0.84, 0.18)


def zone_where(zone: str | None) -> str:
    """Plain-words name of a zone for prompts ("bottom fifth")."""
    return ZONE_WHERE.get(zone or "", "text area")


@dataclass(frozen=True)
class Placement:
    zone: TextZone
    overlap: float  # weighted overlapping area (0 = clear)
    face_overlap: bool  # the chosen zone touches a face
    blocker: Box | None = None  # what blocks it (a face, or the most-covered key object)
    # When every zone is blocked and the middle band was the least bad, ``hint`` is the
    # layout's first-preference zone with its own blocker: the next attempt is asked to
    # clear THAT zone (asking to clear the middle of the picture would fight the scene).
    hint: Placement | None = None

    @property
    def blocked(self) -> bool:
        return self.blocker is not None

    @property
    def retarget(self) -> Placement:
        """The placement the corrective prompt should name (see ``hint``)."""
        return self.hint if self.hint is not None else self

    @property
    def blocker_phrase(self) -> str | None:
        """'every face' or 'the golden harp' - for corrective prompts."""
        if self.blocker is None:
            return None
        if self.blocker.kind == "face":
            return "every face"
        if self.blocker.kind == "figure":
            return "every character's body"
        label = self.blocker.label.strip() or "the important object"
        return label if label.lower().startswith(("the ", "a ", "an ")) else f"the {label}"

    def issue(self) -> str | None:
        if self.blocker is None:
            return None
        return (f"keep {self.blocker_phrase} out of the {zone_where(self.zone.zone)} of the "
                "picture - the page text is printed there")


def zone_from_yaml(entry: dict) -> TextZone:
    box = entry.get("box") or {}
    return TextZone(
        zone=str(entry.get("zone") or "custom"),
        x=float(box["x"]),
        y=float(box["y"]),
        w=float(box["w"]),
        h=float(box["h"]),
    )


def _intersection(z: TextZone, b: Box, pad: float) -> float:
    x0, y0 = max(z.x, b.x - pad), max(z.y, b.y - pad)
    x1, y1 = min(z.x + z.w, b.x + b.w + pad), min(z.y + z.h, b.y + b.h + pad)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def candidates(layout: Layout | None) -> list[TextZone]:
    entries = layout.allowed_text_zones if layout and layout.allowed_text_zones else []
    zones = [zone_from_yaml(e) for e in entries] or [zone_from_yaml(FALLBACK_ZONE)]
    if not any(z.zone == "middle" for z in zones):
        zones.append(MIDDLE_ZONE)
    return zones


@dataclass(frozen=True)
class _Scored:
    index: int
    zone: TextZone
    weighted: float
    face_hit: Box | None
    worst_object: Box | None
    worst_cover: float
    figure_hit: Box | None = None

    @property
    def blocker(self) -> Box | None:
        if self.face_hit is not None:
            return self.face_hit
        if self.figure_hit is not None:
            return self.figure_hit
        if self.worst_cover > OBJECT_MAX_COVER:
            return self.worst_object
        return None


def _score(i: int, z: TextZone, boxes: list[Box], pad: float) -> _Scored:
    weighted, face_hit, worst, worst_cover, figure_hit = 0.0, None, None, 0.0, None
    for b in boxes:
        if b.kind == "figure":
            if z.zone != "middle":
                continue  # top/bottom bands may touch feet or legs; faces/objects decide
            padded = _intersection(z, b, pad)
            weighted += FIGURE_WEIGHT * padded
            if padded > 0 and figure_hit is None:
                figure_hit = b
            continue
        padded = _intersection(z, b, pad)
        if b.kind == "face":
            weighted += FACE_WEIGHT * padded
            if padded > 0 and face_hit is None:
                face_hit = b
        else:
            weighted += padded
            area = b.w * b.h
            cover = _intersection(z, b, 0.0) / area if area > 0 else 0.0
            if cover > worst_cover:
                worst, worst_cover = b, cover
    return _Scored(i, z, weighted, face_hit, worst, worst_cover, figure_hit)


def is_region(b: Box, max_area: float | None = None) -> bool:
    """A key-object box so big it is a region (garden, sky, wall), not an object. Faces
    and figures (character bodies, often large) are never regions."""
    if b.kind in ("face", "figure"):
        return False
    max_area = OBJECT_MAX_AREA if max_area is None else max_area
    return b.w * b.h > max_area or b.w > OBJECT_MAX_WIDTH


def relevant_boxes(boxes: Iterable[Box], max_area: float | None = None) -> list[Box]:
    """Faces, figures and real key objects (regions dropped, see ``is_region``)."""
    if max_area is None:
        max_area = get_settings().object_max_area
    kept, dropped = [], []
    for b in boxes:
        (dropped if is_region(b, max_area) else kept).append(b)
    if dropped:
        log.info("placement ignores region boxes %s", [b.label for b in dropped])
    return kept


def place_text(layout: Layout | None, boxes: Iterable[Box] = (), pad: float = PAD,
               max_object_area: float | None = None) -> Placement:
    """Choose a zone given the detected boxes (see module docstring)."""
    zones = candidates(layout)
    boxes = relevant_boxes(boxes, max_object_area)
    if not boxes:
        return Placement(zones[0], 0.0, False)
    scored = [_score(i, z, boxes, pad) for i, z in enumerate(zones)]
    clear = [s for s in scored if s.weighted == 0]
    if clear:
        best = clear[0]
    else:
        ok = [s for s in scored if s.blocker is None]
        if ok:
            best = min(ok, key=lambda s: (s.weighted, s.index))
        else:
            # Every zone blocked: covering a face is worse than covering a figure, which
            # is worse than covering an object; then least overlap, then preference.
            best = min(scored, key=lambda s: (
                s.face_hit is not None, s.figure_hit is not None, s.weighted, s.index))
    hint = None
    if best.blocker is not None and best.zone.zone == "middle" and best.index != 0:
        first = scored[0]
        hint = Placement(first.zone, first.weighted, first.face_hit is not None, first.blocker)
    return Placement(best.zone, best.weighted, best.face_hit is not None, best.blocker, hint)


def choose_zone(
    page: Page | None, layout: Layout | None, image: bytes | None, analysis=None
) -> TextZone:
    """Pick the text zone for one page. ``analysis`` is a ``GradeResult`` (or anything with
    ``.boxes``); without it the layout's first preference is used."""
    boxes = getattr(analysis, "boxes", None) or []
    return place_text(layout, boxes).zone
