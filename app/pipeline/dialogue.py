"""In-game style dialogue windows for the page text box (PDF drawing, ReportLab).

One look per art-style preset id; ``style_for`` returns ``None`` for an unknown preset
(the caller then keeps the plain scrim box). The looks are generic (no game, console or
company imitated):

* ``sprite16`` - deep navy window, white frame with a dark outline, stepped corners,
  white next-arrow, white name tab with navy pixel-font text.
* ``ps1`` - blue gradient window, thin bevelled silver border with a dark inner line,
  small corner radius, text shadow, next marker, silver name tab.
* ``n64`` - dark see-through rounded box, chunky white rim, gold next-arrow, gold pill
  name tab with dark text.

Text stays white on a dark fill for contrast. These helpers know nothing about fonts:
the caller passes the name-tab font.

Spoken words (``speech`` spans of a page) are drawn in ``DialogueStyle.speech`` - a warm
yellow with strong contrast on every dark window (>= 4.5:1, checked in the tests) - and
always inside curly quotation marks, so speech never relies on colour alone.

A speaker portrait (``draw_portrait``) sits inside the window on the left, in a small
square frame that matches the preset (stepped white for sprite16, bevelled silver for
ps1, chunky white rounded rim for n64).
"""

from __future__ import annotations

from dataclasses import dataclass

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

TAB_MAX_W_FRAC = 0.7  # a name tab never gets wider than this share of the box
TAB_PAD = 16.0


@dataclass(frozen=True)
class DialogueStyle:
    id: str
    pad: float  # side/top padding between frame and text
    pad_bottom: float  # bottom padding, includes room for the next-arrow
    text_color: Color
    shadow: bool  # dark drop shadow behind the text
    tab_h: float
    tab_size: float  # pixel-font size of the name
    speech: Color = HexColor("#ffd54a")  # spoken words (always also in quotation marks)


STYLES: dict[str, DialogueStyle] = {
    "sprite16": DialogueStyle("sprite16", 20, 28, HexColor("#ffffff"), False, 38, 17,
                              HexColor("#ffd54a")),
    "ps1": DialogueStyle("ps1", 18, 26, HexColor("#ffffff"), True, 36, 17,
                         HexColor("#ffe066")),
    "n64": DialogueStyle("n64", 22, 28, HexColor("#ffffff"), False, 38, 17,
                         HexColor("#ffd84a")),
}
# The darkest / lightest fill behind the text, per look (for the contrast test).
FILLS: dict[str, tuple[str, ...]] = {
    "sprite16": ("#1a237e",),
    "ps1": ("#3a5ca8", "#243f80"),
    "n64": ("#000000", "#474747"),  # 72 % black over the picture; worst case: over white
}


def style_for(preset: str | None) -> DialogueStyle | None:
    return STYLES.get(preset or "")


def tab_label_fit(label: str, font: str, size: float, max_w: float) -> tuple[str, float]:
    """Shrink, then truncate with a full stop, until the label fits ``max_w``."""
    while size > 5 and pdfmetrics.stringWidth(label, font, size) > max_w:
        size -= 0.5
    while len(label) > 1 and pdfmetrics.stringWidth(label, font, size) > max_w:
        label = label[:-2].rstrip() + "." if len(label) > 2 else label[:-1]
    return label, size


def _stair(c: canvas.Canvas, x: float, y: float, w: float, h: float, s: float):
    """Rectangle path with two-step (pixel) chamfered corners."""
    p = c.beginPath()
    p.moveTo(x + 2 * s, y)
    for px, py in (
        (x + w - 2 * s, y), (x + w - 2 * s, y + s), (x + w - s, y + s),
        (x + w - s, y + 2 * s), (x + w, y + 2 * s), (x + w, y + h - 2 * s),
        (x + w - s, y + h - 2 * s), (x + w - s, y + h - s), (x + w - 2 * s, y + h - s),
        (x + w - 2 * s, y + h), (x + 2 * s, y + h), (x + 2 * s, y + h - s),
        (x + s, y + h - s), (x + s, y + h - 2 * s), (x, y + h - 2 * s), (x, y + 2 * s),
        (x + s, y + 2 * s), (x + s, y + s), (x + 2 * s, y + s),
    ):
        p.lineTo(px, py)
    p.close()
    return p


def _poly(c: canvas.Canvas, pts: list[tuple[float, float]], fill: Color,
          edge: Color | None) -> None:
    p = c.beginPath()
    p.moveTo(*pts[0])
    for pt in pts[1:]:
        p.lineTo(*pt)
    p.close()
    c.saveState()
    c.setFillColor(fill)
    if edge is not None:
        c.setStrokeColor(edge)
        c.setLineWidth(1)
    c.drawPath(p, stroke=1 if edge is not None else 0, fill=1)
    c.restoreState()


def _arrow_down(c, cx: float, cy: float, r: float, fill: Color, edge: Color | None) -> None:
    _poly(c, [(cx - r, cy + r * 0.7), (cx + r, cy + r * 0.7), (cx, cy - r * 0.7)], fill, edge)


def _arrow_right(c, cx: float, cy: float, r: float, fill: Color, edge: Color | None) -> None:
    _poly(c, [(cx - r * 0.7, cy + r), (cx - r * 0.7, cy - r), (cx + r * 0.7, cy)], fill, edge)


def draw_box(c: canvas.Canvas, st: DialogueStyle, x: float, y: float, w: float, h: float,
             tab: str = "", tab_font: str = "Helvetica") -> None:
    """Draw the window (frame, fill, next-arrow, optional name tab) in rect ``x,y,w,h``
    (PDF coordinates, y = bottom). The tab straddles the top-left edge, reaching
    ``st.tab_h / 2`` above ``y + h``."""
    c.saveState()
    if st.id == "sprite16":
        _sprite16(c, x, y, w, h)
    elif st.id == "ps1":
        _ps1(c, x, y, w, h)
    else:
        _n64(c, x, y, w, h)
    c.restoreState()
    if tab:
        _draw_tab(c, st, x, y + h, w, tab, tab_font)


def _sprite16(c, x, y, w, h) -> None:
    s = 3.0
    navy, dark, white = HexColor("#1a237e"), HexColor("#0a0d2e"), HexColor("#ffffff")
    c.setLineJoin(0)
    c.setFillColor(Color(navy.red, navy.green, navy.blue, alpha=0.94))
    c.drawPath(_stair(c, x + 3, y + 3, w - 6, h - 6, s), stroke=0, fill=1)
    c.setStrokeColor(dark)
    c.setLineWidth(1.5)
    c.drawPath(_stair(c, x + 0.75, y + 0.75, w - 1.5, h - 1.5, s + 0.7), stroke=1, fill=0)
    c.setStrokeColor(white)
    c.setLineWidth(3)
    c.drawPath(_stair(c, x + 3, y + 3, w - 6, h - 6, s), stroke=1, fill=0)
    _arrow_down(c, x + w - 18, y + 15, 5.5, white, None)


def _ps1(c, x, y, w, h) -> None:
    r = 3.0
    silver, dark = HexColor("#cfd5df"), HexColor("#101c44")
    c.saveState()
    clip = c.beginPath()
    clip.roundRect(x, y, w, h, r)
    c.clipPath(clip, stroke=0, fill=0)
    c.linearGradient(x, y + h, x, y, (HexColor("#3a5ca8"), HexColor("#243f80")), extend=False)
    c.restoreState()
    # bevelled silver border: bright top/left, shaded bottom/right
    c.setLineWidth(2.5)
    c.setStrokeColor(silver)
    c.roundRect(x + 1.25, y + 1.25, w - 2.5, h - 2.5, r, stroke=1, fill=0)
    c.setLineWidth(1)
    c.setStrokeColor(HexColor("#ffffff"))
    c.line(x + r + 1, y + h - 0.6, x + w - r - 1, y + h - 0.6)
    c.line(x + 0.6, y + r + 1, x + 0.6, y + h - r - 1)
    c.setStrokeColor(HexColor("#7d8798"))
    c.line(x + r + 1, y + 0.6, x + w - r - 1, y + 0.6)
    c.line(x + w - 0.6, y + r + 1, x + w - 0.6, y + h - r - 1)
    c.setLineWidth(0.9)
    c.setStrokeColor(dark)
    c.roundRect(x + 3.6, y + 3.6, w - 7.2, h - 7.2, 1.5, stroke=1, fill=0)
    _arrow_right(c, x + w - 17, y + 15, 5, HexColor("#ffffff"), dark)


def _n64(c, x, y, w, h) -> None:
    r = min(16.0, h / 3)
    c.setFillColor(Color(0, 0, 0, alpha=0.72))
    c.roundRect(x + 2, y + 2, w - 4, h - 4, r, stroke=0, fill=1)
    c.setStrokeColor(HexColor("#ffffff"))
    c.setLineWidth(4)
    c.roundRect(x + 2, y + 2, w - 4, h - 4, r, stroke=1, fill=0)
    _arrow_down(c, x + w - 24, y + 17, 6.5, HexColor("#ffc928"), None)


def _draw_tab(c, st: DialogueStyle, bx: float, top: float, bw: float, label: str,
              font: str) -> None:
    label, size = tab_label_fit(label, font, st.tab_size, bw * TAB_MAX_W_FRAC - 2 * TAB_PAD)
    tw = pdfmetrics.stringWidth(label, font, size) + 2 * TAB_PAD
    h = st.tab_h
    x = bx + (34 if st.id == "n64" else 26)
    y = top - h / 2
    c.saveState()
    if st.id == "sprite16":
        c.setLineJoin(0)
        c.setFillColor(HexColor("#ffffff"))
        c.setStrokeColor(HexColor("#0a0d2e"))
        c.setLineWidth(1.5)
        c.drawPath(_stair(c, x, y, tw, h, 2.5), stroke=1, fill=1)
        color = HexColor("#1a237e")
    elif st.id == "ps1":
        c.setFillColor(HexColor("#cfd5df"))
        c.setStrokeColor(HexColor("#101c44"))
        c.setLineWidth(1)
        c.roundRect(x, y, tw, h, 3, stroke=1, fill=1)
        c.setStrokeColor(HexColor("#ffffff"))
        c.line(x + 3, y + h - 1.5, x + tw - 3, y + h - 1.5)
        color = HexColor("#14275f")
    else:
        c.setFillColor(HexColor("#ffc928"))
        c.setStrokeColor(HexColor("#ffffff"))
        c.setLineWidth(2)
        c.roundRect(x, y, tw, h, h / 2, stroke=1, fill=1)
        color = HexColor("#2a1d00")
    c.setFont(font, size)
    c.setFillColor(color)
    asc, desc = pdfmetrics.getAscentDescent(font, size)
    c.drawCentredString(x + tw / 2, y + h / 2 - (asc + desc) / 2, label)
    c.restoreState()


def draw_portrait(c: canvas.Canvas, st: DialogueStyle, image: ImageReader, x: float,
                  y: float, side: float) -> None:
    """Square speaker portrait at ``x, y`` (bottom-left), ``side`` wide, in the preset's
    frame. ``image`` is already cropped square."""
    c.saveState()
    if st.id == "sprite16":
        c.setLineJoin(0)
        path = _stair(c, x, y, side, side, 2.5)
        c.saveState()
        c.clipPath(path, stroke=0, fill=0)
        c.drawImage(image, x, y, side, side)
        c.restoreState()
        c.setStrokeColor(HexColor("#0a0d2e"))
        c.setLineWidth(1.5)
        c.drawPath(_stair(c, x - 1.5, y - 1.5, side + 3, side + 3, 3), stroke=1, fill=0)
        c.setStrokeColor(HexColor("#ffffff"))
        c.setLineWidth(2.5)
        c.drawPath(path, stroke=1, fill=0)
    elif st.id == "ps1":
        path = c.beginPath()
        path.roundRect(x, y, side, side, 2)
        c.saveState()
        c.clipPath(path, stroke=0, fill=0)
        c.drawImage(image, x, y, side, side)
        c.restoreState()
        c.setLineWidth(2)
        c.setStrokeColor(HexColor("#cfd5df"))
        c.roundRect(x, y, side, side, 2, stroke=1, fill=0)
        c.setLineWidth(0.9)
        c.setStrokeColor(HexColor("#101c44"))
        c.roundRect(x - 1.6, y - 1.6, side + 3.2, side + 3.2, 3, stroke=1, fill=0)
    else:
        r = min(10.0, side / 6)
        path = c.beginPath()
        path.roundRect(x, y, side, side, r)
        c.saveState()
        c.clipPath(path, stroke=0, fill=0)
        c.drawImage(image, x, y, side, side)
        c.restoreState()
        c.setLineWidth(3)
        c.setStrokeColor(HexColor("#ffffff"))
        c.roundRect(x, y, side, side, r, stroke=1, fill=0)
    c.restoreState()


def contrast(fg: Color, bg: Color) -> float:
    """WCAG contrast ratio of two opaque colours."""

    def lum(col: Color) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(col.red) + 0.7152 * ch(col.green) + 0.0722 * ch(col.blue)

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)
