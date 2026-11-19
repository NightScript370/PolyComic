"""Print-ready picture-book PDF (ReportLab).

Geometry: trim 210 x 280 mm (3:4 portrait) plus 3 mm bleed on every side, so the
media box is 216 x 286 mm. Art is drawn full-bleed (cover-cropped). Text zones
from layouts.yaml are normalised 0..1 to the TRIM area, origin top-left.

All text is drawn here - the image model never draws text. User text is drawn
with ``drawString`` (no markup parsing), stripped of control characters and
wrapped to the zone width, so nothing the user types can inject PDF/markup.

Page text with ``speech`` spans (app.stories.loader) is drawn as mixed runs: the story
wording unchanged, curly quotation marks around each spoken span and the spoken words in
the preset's highlight colour (narration plain). Runs share one font, so wrapping and
fitting work on the plain string and a per-character "spoken" mask colours the pieces
(still ``drawString``, no markup to escape). A page with a speaker portrait
(``BookPage.portrait_png``) gets it inside the dialogue window on the left; the text is
fitted into the remaining width.

Pages: cover, the story pages in order, About Me.
"""

from __future__ import annotations

import io
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.pipeline.book import BookResult
from app.pipeline.dialogue import DialogueStyle, draw_box, draw_portrait, style_for
from app.pipeline.placement import TextZone, zone_from_yaml
from app.stories.loader import display_runs, get_layout, render_text

log = logging.getLogger("app.pipeline.pdf")

# --- geometry ------------------------------------------------------------------

TRIM_W = 210 * mm
TRIM_H = 280 * mm
BLEED = 3 * mm
PAGE_W = TRIM_W + 2 * BLEED
PAGE_H = TRIM_H + 2 * BLEED
SAFE = 0.05  # layouts.yaml page.safe_margin, fraction of the trim

BODY_MIN, BODY_MAX = 14.0, 26.0
DIALOGUE_MIN = 11.0  # smallest page-text size inside a dialogue window
JPEG_QUALITY = 92

# --- fonts (SIL OFL, bundled; licences next to the files) -----------------------

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BODY = "PC-Andika-Bold"
BODY_REGULAR = "PC-Andika"
DISPLAY = "PC-PressStart2P"
_FONT_FILES = {
    BODY: FONT_DIR / "andika" / "Andika-Bold.ttf",
    BODY_REGULAR: FONT_DIR / "andika" / "Andika-Regular.ttf",
    DISPLAY: FONT_DIR / "pressstart2p" / "PressStart2P-Regular.ttf",
}


@lru_cache
def register_fonts() -> None:
    for name, path in _FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))


def _covers(font_name: str, text: str) -> bool:
    """True if every non-space character of ``text`` has a glyph in the font."""
    cmap = pdfmetrics.getFont(font_name).face.charToGlyph
    return all(ord(ch) in cmap for ch in text if not ch.isspace())


def display_font_for(text: str) -> str:
    """Pixel display font when it has every glyph (it is ASCII-ish only), else body."""
    return DISPLAY if _covers(DISPLAY, text) else BODY


# --- themes ----------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    cover_bg: str
    cover_text: str
    back_bg: str
    accent: str


THEMES = {
    "jack": Theme(cover_bg="#2f6f45", cover_text="#fff7e0", back_bg="#dff0fb", accent="#2f6f45"),
    "red": Theme(cover_bg="#a8323a", cover_text="#fff7e0", back_bg="#e6f3df", accent="#a8323a"),
}
DEFAULT_THEME = Theme(cover_bg="#33415c", cover_text="#fff7e0", back_bg="#eef1f6",
                      accent="#33415c")
INK = HexColor("#1d2240")
SPEECH_INK = HexColor("#8a2d00")  # spoken words on the plain (light) scrim box
SCRIM = Color(1, 1, 1, alpha=0.82)
# Speaker portrait inside the dialogue window (points).
PORTRAIT_MAX = 96.0
PORTRAIT_MIN = 40.0  # smaller than this: no portrait (the text keeps the whole box)
PORTRAIT_GAP = 12.0  # between the portrait and the text
PORTRAIT_W_FRAC = 0.26  # a portrait never takes more than this share of the box width

# --- text helpers ----------------------------------------------------------------


def clean_text(text: str) -> str:
    """Drop control/format characters and collapse whitespace."""
    kept = [
        " " if ch.isspace() else ch
        for ch in text or ""
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    ]
    return " ".join("".join(kept).split())


def clean_runs(runs: list[tuple[str, bool]]) -> tuple[str, list[bool]]:
    """Display runs ``(text, spoken)`` -> (cleaned text, per-character spoken mask): the
    same cleaning as ``clean_text`` (control characters dropped, whitespace collapsed),
    keeping each kept character's flag. A space is spoken only inside a spoken run, so a
    space between two runs is never highlighted."""
    chars: list[tuple[str, bool]] = []
    for text, spoken in runs:
        for ch in text or "":
            if ch.isspace():
                if chars and chars[-1][0] != " ":
                    chars.append((" ", chars[-1][1] and spoken))
            elif unicodedata.category(ch)[0] != "C":
                chars.append((ch, spoken))
    while chars and chars[-1][0] == " ":
        chars.pop()
    return "".join(ch for ch, _ in chars), [f for _, f in chars]


def wrap_offsets(text: str, font: str, size: float, max_w: float) -> list[tuple[int, int]]:
    """Greedy word wrap of single-spaced ``text`` as ``(start, end)`` slices; words wider
    than the line are broken by character."""
    lines: list[tuple[int, int]] = []
    start = end = -1  # current line slice (-1 = empty)
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] == " ":
            i += 1
        if i >= n:
            break
        j = text.find(" ", i)
        j = n if j < 0 else j
        if start >= 0 and pdfmetrics.stringWidth(text[start:j], font, size) <= max_w:
            end = j
            i = j
            continue
        if start >= 0:
            lines.append((start, end))
            start = -1
        w0 = i
        while pdfmetrics.stringWidth(text[w0:j], font, size) > max_w and j - w0 > 1:
            cut = j - 1
            while cut > w0 + 1 and pdfmetrics.stringWidth(text[w0:cut], font, size) > max_w:
                cut -= 1
            lines.append((w0, cut))
            w0 = cut
        start, end = w0, j
        i = j
    if start >= 0:
        lines.append((start, end))
    return lines


def wrap(text: str, font: str, size: float, max_w: float) -> list[str]:
    """Greedy word wrap; words wider than the line are broken by character."""
    text = " ".join(text.split())
    return [text[a:b] for a, b in wrap_offsets(text, font, size, max_w)]


@dataclass
class Para:
    text: str
    font: str = BODY
    scale: float = 1.0  # relative to the block's base size
    gap_after: float = 0.35  # extra space after, in base-size ems
    color: Color | None = None
    # Per-character "spoken" flags (same length as ``text``, the single-spaced text from
    # ``clean_runs``); None = all narration.
    spoken: list[bool] | None = None


@dataclass
class _Line:
    text: str
    font: str
    size: float
    color: Color | None
    height: float
    width: float
    spoken: list[bool] | None = None  # per character; None = all narration

    def runs(self) -> list[tuple[str, bool]]:
        """Consecutive pieces with the same spoken flag."""
        if not self.spoken or not any(self.spoken):
            return [(self.text, False)]
        out: list[tuple[str, bool]] = []
        for ch, f in zip(self.text, self.spoken, strict=False):
            if out and out[-1][1] == f:
                out[-1] = (out[-1][0] + ch, f)
            else:
                out.append((ch, f))
        return out

    def truncate(self, max_w: float) -> None:
        """End the line with "..." (the block was cut at the smallest size), dropping
        characters until it fits ``max_w``."""
        kept = self.text.rstrip(" .,;")
        while kept and pdfmetrics.stringWidth(kept + "...", self.font,
                                              self.size) > max_w:
            kept = kept[:-1].rstrip(" .,;")
        flag = bool(self.spoken and kept and self.spoken[len(kept) - 1])
        if self.spoken is not None:
            self.spoken = self.spoken[:len(kept)] + [flag] * 3
        self.text = kept + "..."
        self.width = pdfmetrics.stringWidth(self.text, self.font, self.size)


def _layout(paras: list[Para], size: float, max_w: float, leading: float) -> list[_Line | float]:
    out: list[_Line | float] = []
    for i, p in enumerate(paras):
        s = size * p.scale
        text = p.text if p.spoken is not None else " ".join(p.text.split())
        for a, b in wrap_offsets(text, p.font, s, max_w):
            t = text[a:b]
            out.append(_Line(t, p.font, s, p.color, s * leading,
                             pdfmetrics.stringWidth(t, p.font, s),
                             p.spoken[a:b] if p.spoken is not None else None))
        if i < len(paras) - 1:
            out.append(size * p.gap_after)
    return out


def _draw_line(c: canvas.Canvas, it: _Line, x: float, baseline: float, *, color: Color,
               speech: Color, centred: bool = True, shadow: Color | None = None) -> None:
    """One wrapped line, centred on ``x`` (or starting at ``x``), spoken pieces in
    ``speech``; an optional 1pt drop ``shadow`` under every piece."""
    c.setFont(it.font, it.size)
    left = x - it.width / 2 if centred else x
    for text, spoken in it.runs():
        if shadow is not None:
            c.setFillColor(shadow)
            c.drawString(left + 1, baseline - 1, text)
        c.setFillColor(speech if spoken else color)
        c.drawString(left, baseline, text)
        left += pdfmetrics.stringWidth(text, it.font, it.size)


def _height(items: list[_Line | float]) -> float:
    return sum(i.height if isinstance(i, _Line) else i for i in items)


def fit_block(
    paras: list[Para], max_w: float, max_h: float, *, max_size: float = BODY_MAX,
    min_size: float = BODY_MIN, leading: float = 1.25,
) -> tuple[float, list[_Line | float]]:
    """Largest base size in [min_size, max_size] (0.5pt steps) whose wrapped block fits
    ``max_h``. If even ``min_size`` does not fit, returns the min-size layout (the
    caller lets the box grow)."""
    size = max_size
    while True:
        items = _layout(paras, size, max_w, leading)
        if _height(items) <= max_h or size <= min_size:
            return size, items
        size = max(min_size, size - 0.5)


# --- drawing helpers ----------------------------------------------------------------


def zone_rect(z: TextZone) -> tuple[float, float, float, float]:
    """TextZone (trim-normalised, top-left origin) -> PDF rect (x, y_bottom, w, h)."""
    x = BLEED + z.x * TRIM_W
    w = z.w * TRIM_W
    h = z.h * TRIM_H
    y = BLEED + (1 - z.y - z.h) * TRIM_H
    return x, y, w, h


def _cover_crop(png: bytes, target_aspect: float, anchor_y: float = 0.5) -> bytes:
    """Crop to ``target_aspect`` (w/h) filling the box; returns JPEG bytes."""
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w / h > target_aspect:  # too wide -> trim sides
            nw = round(h * target_aspect)
            left = (w - nw) // 2
            im = im.crop((left, 0, left + nw, h))
        else:  # too tall -> trim top/bottom around anchor
            nh = round(w / target_aspect)
            top = round((h - nh) * anchor_y)
            im = im.crop((0, top, w, top + nh))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue()


def _draw_image(c: canvas.Canvas, png: bytes, x: float, y: float, w: float, h: float,
                anchor_y: float = 0.5) -> None:
    jpeg = _cover_crop(png, w / h, anchor_y)
    c.drawImage(ImageReader(io.BytesIO(jpeg)), x, y, w, h)


def _draw_block(
    c: canvas.Canvas, paras: list[Para], rect: tuple[float, float, float, float], *,
    scrim: bool, max_size: float = BODY_MAX, min_size: float = BODY_MIN,
    pad: float = 10.0, align_top: bool = False,
) -> float:
    """Fit ``paras`` into ``rect`` and draw them centred (optionally on a rounded scrim).
    Returns the base font size used."""
    x, y, w, h = rect
    inner_w, inner_h = w - 2 * pad, h - 2 * pad
    size, items = fit_block(paras, inner_w, inner_h, max_size=max_size, min_size=min_size)
    block_h = _height(items)
    block_w = max((i.width for i in items if isinstance(i, _Line)), default=0)

    box_w = min(w, block_w + 2 * pad + size)  # a little side air
    box_h = block_h + 2 * pad
    cx = x + w / 2
    top = y + h if align_top else y + h / 2 + box_h / 2
    # Overflow at min size: grow downward/upward but stay inside the trim safe area.
    lo, hi = BLEED + SAFE * TRIM_H * 0.5, BLEED + TRIM_H - SAFE * TRIM_H * 0.5
    top = min(top, hi)
    if top - box_h < lo:
        top = min(hi, lo + box_h)

    if scrim:
        c.saveState()
        c.setFillColor(SCRIM)
        c.roundRect(cx - box_w / 2, top - box_h, box_w, box_h, radius=min(14, box_h / 4),
                    stroke=0, fill=1)
        c.restoreState()

    cursor = top - pad
    for it in items:
        if not isinstance(it, _Line):
            cursor -= it
            continue
        asc, desc = pdfmetrics.getAscentDescent(it.font, it.size)
        baseline = cursor - (it.height - (asc - desc)) / 2 - asc
        _draw_line(c, it, cx, baseline, color=it.color or INK, speech=SPEECH_INK)
        cursor -= it.height
    return size


# --- pages -----------------------------------------------------------------------------


def _cover(c: canvas.Canvas, book: BookResult, theme: Theme) -> None:
    c.setFillColor(HexColor(theme.cover_bg))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    title = clean_text(book.story.title)
    subtitle = clean_text(book.subtitle)
    fg = HexColor(theme.cover_text)
    title_rect = zone_rect(TextZone("top", 0.06, 0.04, 0.88, 0.24))
    _draw_block(
        c,
        [Para(title, display_font_for(title), 1.0, 0.9, fg),
         Para(subtitle, BODY, 0.95, 0, fg)],
        title_rect, scrim=False, max_size=30, min_size=14,
    )
    # Hero in a rounded white frame, lower part of the cover.
    fw = 0.64 * TRIM_W
    fh = fw * 4 / 3
    fx = (PAGE_W - fw) / 2
    fy = BLEED + 0.05 * TRIM_H
    _framed_image(c, book.hero_png, fx, fy, fw, fh, anchor_y=0.5)


def _framed_image(c: canvas.Canvas, png: bytes, x: float, y: float, w: float, h: float,
                  anchor_y: float) -> None:
    border, radius = 6, 18
    c.saveState()
    c.setFillColor(HexColor("#ffffff"))
    c.roundRect(x - border, y - border, w + 2 * border, h + 2 * border, radius + border,
                stroke=0, fill=1)
    path = c.beginPath()
    path.roundRect(x, y, w, h, radius)
    c.clipPath(path, stroke=0, fill=0)
    _draw_image(c, png, x, y, w, h, anchor_y)
    c.restoreState()


def page_paragraph(book: BookResult | None, page) -> Para:
    """The page text as one paragraph, with the spoken mask when the story marks speech
    on this page (narration only for an unknown story / page or unmatched spans)."""
    if book is None:
        return Para(clean_text(page.text))
    try:
        runs = book.story.speech_runs(page.page, page.text, book.hero_details.name,
                                      book.hero_details.pronouns)
    except LookupError:
        runs = [(page.text, None)]
    text, mask = clean_runs(display_runs(runs))
    return Para(text, spoken=mask if any(mask) else None)


def _story_page(c: canvas.Canvas, page, book: BookResult | None = None) -> None:
    _draw_image(c, page.png, 0, 0, PAGE_W, PAGE_H)
    para = page_paragraph(book, page)
    style = style_for(book.preset if book is not None else None)
    if style is None:  # unknown preset: the plain scrim box (no tab, no portrait)
        _draw_block(c, [para], zone_rect(page.zone), scrim=True)
        return
    tab = ""
    if book is not None:
        try:
            tab = clean_text(book.story.speaker_label(page.page, book.hero_details.name))
        except LookupError:
            tab = ""
    _draw_dialogue(c, para, zone_rect(page.zone), style, tab,
                   portrait=getattr(page, "portrait_png", None))


@dataclass
class DialogueLayout:
    """Where everything of one dialogue window goes (PDF points, y = bottom)."""

    size: float  # base font size
    items: list  # wrapped lines / gaps
    box: tuple[float, float, float, float]  # x, y, w, h of the window
    text_x: float  # centre of the text (centred) or its left edge (with a portrait)
    text_top: float
    text_w: float  # width the text was fitted to
    centred: bool
    portrait: tuple[float, float, float] | None = None  # x, y, side


def layout_dialogue(para: Para | str, rect: tuple[float, float, float, float],
                    style: DialogueStyle, tab: bool = False,
                    portrait: bool = False) -> DialogueLayout:
    """Fit the text (and the portrait, if any) into a window that never leaves ``rect``
    (the zone the placement logic chose, which keeps faces out). Without a portrait the
    window hugs the centred text; with one it spans the zone, the portrait square on the
    left and the text left-aligned beside it. A zone too low for a ``PORTRAIT_MIN``
    portrait drops it. At the smallest size the text is cut with "...", never grown."""
    if isinstance(para, str):
        para = Para(para)
    x, y, w, h = rect
    reach = style.tab_h / 2 if tab else 0.0  # the name tab pokes above the box
    avail_h = h - reach
    inner_h = avail_h - style.pad - style.pad_bottom
    # the portrait starts below the name tab, which pokes into the window
    ptop = max(style.pad, style.tab_h / 2 + 4) if tab else style.pad
    side = (min(PORTRAIT_MAX, avail_h - ptop - style.pad_bottom, w * PORTRAIT_W_FRAC)
            if portrait else 0.0)
    if side < PORTRAIT_MIN:
        side = 0.0
    inner_w = w - 2 * style.pad - (side + PORTRAIT_GAP if side else 0.0)
    size, items = fit_block([para], inner_w, inner_h, min_size=DIALOGUE_MIN)
    if _height(items) > inner_h:  # even the smallest size overflows: cut, never grow
        size = DIALOGUE_MIN
        lines = [i for i in items if isinstance(i, _Line)]
        while lines and sum(i.height for i in lines) > inner_h:
            lines.pop()
        if lines:
            lines[-1].truncate(inner_w)
        items = list(lines)
    block_h = _height(items)
    block_w = max((i.width for i in items if isinstance(i, _Line)), default=0)
    if side:
        box_w = w
        box_h = min(avail_h, max(block_h + style.pad, side + ptop) + style.pad_bottom)
    else:
        box_w = min(w, max(block_w + 2 * style.pad + size, 150.0 if tab else 0.0))
        box_h = min(avail_h, block_h + style.pad + style.pad_bottom)
    cx = x + w / 2
    box_x = cx - box_w / 2
    top = y + (avail_h + box_h) / 2  # vertically centred in the zone below the tab reach
    box = (box_x, top - box_h, box_w, box_h)
    if not side:
        return DialogueLayout(size, items, box, cx, top - style.pad, inner_w, True)
    px, py = box_x + style.pad, top - ptop - side
    text_top = top - style.pad - max(0.0, (side + ptop - style.pad - block_h) / 2)
    return DialogueLayout(size, items, box, px + side + PORTRAIT_GAP, text_top, inner_w,
                          False, (px, py, side))


def _draw_dialogue(c: canvas.Canvas, para: Para | str,
                   rect: tuple[float, float, float, float], style: DialogueStyle,
                   tab: str = "", portrait: bytes | None = None) -> float:
    """Text (and an optional speaker portrait) in a preset-styled dialogue window inside
    ``rect``. Returns the font size used."""
    lay = layout_dialogue(para, rect, style, bool(tab), portrait is not None)
    bx, by, bw, bh = lay.box
    draw_box(c, style, bx, by, bw, bh, tab, tab_font_for(tab))
    if lay.portrait is not None and portrait is not None:
        px, py, side = lay.portrait
        try:
            jpeg = _cover_crop(portrait, 1.0, 0.35)
            draw_portrait(c, style, ImageReader(io.BytesIO(jpeg)), px, py, side)
        except Exception:  # a broken portrait never breaks the page (the text still fits)
            log.exception("speaker portrait could not be drawn")
    shadow = Color(0.04, 0.07, 0.2, alpha=0.9) if style.shadow else None
    cursor = lay.text_top
    for it in lay.items:
        if not isinstance(it, _Line):
            cursor -= it
            continue
        asc, desc = pdfmetrics.getAscentDescent(it.font, it.size)
        baseline = cursor - (it.height - (asc - desc)) / 2 - asc
        _draw_line(c, it, lay.text_x, baseline, color=style.text_color, speech=style.speech,
                   centred=lay.centred, shadow=shadow)
        cursor -= it.height
    return lay.size


def tab_font_for(label: str) -> str:
    """Name-tab font: the pixel font, or the body font for names it has no glyphs for."""
    return display_font_for(label) if label else DISPLAY


def _about_me(c: canvas.Canvas, book: BookResult, theme: Theme) -> None:
    layout = get_layout("about_me")
    raw = layout.raw if layout else {}
    hero = book.hero_details
    c.setFillColor(HexColor(theme.back_bg))
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    pb = raw.get("portrait_box") or {"x": 0.2, "y": 0.07, "w": 0.6, "h": 0.42}
    px, py, pw, ph = zone_rect(TextZone("portrait", pb["x"], pb["y"], pb["w"], pb["h"]))
    # Hero reference is full-body: anchor near the top for a waist-up crop.
    _framed_image(c, book.hero_png, px, py, pw, ph, anchor_y=0.08)

    labels = {f.get("id"): f for f in raw.get("text_fields") or [] if isinstance(f, dict)}

    def label(fid: str, default: str) -> str:
        return str((labels.get(fid) or {}).get("label") or default)

    name = clean_text(hero.name)
    heading_tpl = str(book.story.about_me.get("heading") or "About {name}")
    heading = clean_text(render_text(heading_tpl, name, hero.pronouns))
    accent = HexColor(theme.accent)
    paras = [Para(heading, display_font_for(heading), 1.0, 0.8, accent),
             Para(f"{label('name', 'My name is')} {name}.", BODY, 0.8, 0.2)]
    if hero.age:
        suffix = str((labels.get("age") or {}).get("suffix") or "years old")
        paras.append(Para(f"{label('age', 'I am')} {hero.age} {suffix}.", BODY, 0.8, 0.2))
    favs = hero.filled_favourites()
    if favs:
        paras.append(Para(f"{label('favourite_things', 'My favourite things')}:", BODY, 0.8,
                          0.1, accent))
        paras += [Para(f"{k}: {clean_text(v)}", BODY_REGULAR, 0.75, 0.05) for k, v in favs]
        paras[-1].gap_after = 0.3
    about = clean_text(hero.about)
    if about:
        paras.append(Para(about, BODY_REGULAR, 0.75, 0))
    paras[-1].gap_after = 0

    zone = zone_from_yaml((layout.allowed_text_zones or [{}])[0]) if layout else TextZone(
        "bottom", 0.08, 0.55, 0.84, 0.38)
    _draw_block(c, paras, zone_rect(zone), scrim=False, max_size=28, min_size=12,
                align_top=True)


def build_book_pdf(book: BookResult) -> bytes:
    register_fonts()
    theme = THEMES.get(book.story.id, DEFAULT_THEME)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(f"{clean_text(book.story.title)} - {clean_text(book.subtitle)}")
    c.setAuthor("PolyComic")
    c.setSubject("Personalised picture book")
    c.setCreator("PolyComic")

    def finish_page() -> None:
        c.setTrimBox((BLEED, BLEED, BLEED + TRIM_W, BLEED + TRIM_H))
        c.setBleedBox((0, 0, PAGE_W, PAGE_H))
        c.showPage()

    _cover(c, book, theme)
    finish_page()
    for page in book.pages:
        _story_page(c, page, book)
        finish_page()
    _about_me(c, book, theme)
    finish_page()
    c.save()
    return buf.getvalue()


def pdf_filename(title: str) -> str:
    """'Jack and the Beanstalk' -> 'JackAndTheBeanstalk.pdf'."""
    words = "".join(ch if ch.isascii() and ch.isalnum() else " " for ch in title).split()
    return ("".join(w[:1].upper() + w[1:] for w in words) or "Book") + ".pdf"
