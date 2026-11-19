"""Read-only loader for story YAML (app/stories/*.yaml) and the page template
(app/template/layouts.yaml). Tolerant of extra fields: everything unknown stays
in ``raw``.

Story-level ``props`` (id -> description) are the single source of truth for objects
that recur across pages (Jack's five beans, Red's basket). A page lists the ones it
shows in ``props: [beans]``; the prompt builder and the grader both get the canonical
text. A page that names an unknown prop id is a ``ValueError`` at load time.

A prop may be given as a dict ``{description: ..., count: 1}``; ``count`` appends an
"exactly N in the picture" clause to the canonical text, so single objects (basket,
nightcap, axe) are never drawn twice. A prop may also have page-dependent ``states``
(``[{pages: [...], description}]``, like costumes): on those pages its line adds "On this
page: <state>" (Jack's beanstalk being chopped on 17, falling with a stump on 18).

Characters may give an apparent ``age`` (whole years, 1..120); family characters must
(``ValueError`` otherwise). The family sheet prompt and the grader use it: anyone under
``ELDERLY_AGE`` is drawn without grey hair or wrinkles.

Characters may also give ``size`` (their size relative to the hero child, an art field
used by page prompts, reference sheets and the grader) and page-dependent ``costumes``:
``[{id, pages: [...], description, sheet: true}]``. The base ``description`` must not
mention a costume; on pages without one the prompt says the character wears none
(``plain`` overrides that wording). ``sheet: false`` means the costume gets no reference
sheet of its own (pages use the plain one). Overlapping costume pages are an error.
``sheet_description`` (optional) replaces ``description`` on the character's reference
sheets only - gentler wording where an isolated figure trips the image model's safety
filter.

Pages may give ``art_caption`` (the scene sent to the image model and the grader instead
of ``caption``, which is also shown to the reader in the web viewer) and ``expression``
(the hero's emotion on that page; the prompt and the grader both get it).

Pages may give ``expressions`` (``{character_id: emotion}``): the face of a SUPPORTING or
family character on that page (the wolf dazed after tripping, the giant asleep). The page
prompt and the grader both get it; a clearly different face is a definite error. Every id
must be in the page's ``characters`` (``ValueError`` otherwise). ``sizes`` (same shape)
anchors a character's apparent size on that page to things in the picture ("towering
right behind the hero child, about four times the child's height"); it is added to the
character's line in the prompt and the grader's cast line.

Characters may give ``ref_priority`` (0 = most important): when a page has more supporting
characters than reference sheets it may send, the lowest numbers are sent first (large
humanoid characters 0, animals 1, small props / pets 2). Default: 2 when ``size`` starts
with "Small", else 1.

The ``hero_outfit`` may give ``extras`` (``[{pages: [...], description}]``): items the hero
wears only on some pages (Red's whistle, handed over on page 1, worn from page 2 on). The
hero reference uses the base outfit; ``Story.hero_outfit_for(page)`` adds the extras.

Pages may give ``speaker`` (a character id, or ``hero``): the one character who speaks on
that page. The PDF draws it as a name tab on the text box; it never reaches a prompt.
Leave it out for narration and for pages where two characters speak. A character may give
``speaker_label`` (the short tab name; default: ``name`` without a leading "the").
A page may also give ``speaker_label`` (a short, non-empty string, at most
``MAX_SPEAKER_LABEL`` characters) that overrides the tab text on that page, e.g. "???"
to keep a voice a mystery; ``speaker`` still says who speaks (speech semantics).

Pages may give ``speech``: which parts of ``text`` are spoken, and by whom -
``[{who: wolf, says: "Good morning,"}, ...]`` in reading order. ``who`` is a character id
or ``hero``; ``says`` is an exact substring of ``text`` (no surrounding spaces, never
cutting a ``{placeholder}``), found left to right after the previous span. Story text has
no quotation marks: the PDF and the web viewer add them around each span and draw the
spoken words in a highlight colour. A span that does not match is a ``ValueError`` at load
time; a page with ``speaker`` whose spans name someone else is one too.

Pages with a single ``speaker`` may set ``portrait`` and ``portrait_expression`` (one of
``PORTRAIT_EXPRESSIONS``, default ``neutral``): the dialogue box may then show a small
portrait of the speaker. ``portrait: offscreen`` (``true`` means the same) - only when the
grader confirms the speaker is NOT in the final picture; ``portrait: moment`` - the line
is spoken at another moment than the picture shows, so the portrait (the speaker's face
while speaking) is shown even when the speaker is in the picture (RPG convention, on
purpose; ``app.pipeline.portraits``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

STORIES_DIR = Path(__file__).parent
LAYOUTS_PATH = Path(__file__).parent.parent / "template" / "layouts.yaml"
ELDERLY_AGE = 60  # apparent age from which grey hair and wrinkles are expected
# Faces a speaker portrait can show (``portrait_expression``); app.pipeline.portraits
# describes each one to the image model.
PORTRAIT_EXPRESSIONS = ("neutral", "happy", "scared", "sly", "angry")
# Why a page has a portrait: the speaker is out of the picture (grader-gated) or the line
# is another moment than the picture (always shown).
PORTRAIT_OFFSCREEN, PORTRAIT_MOMENT = "offscreen", "moment"
PORTRAIT_REASONS = (PORTRAIT_OFFSCREEN, PORTRAIT_MOMENT)
MAX_SPEAKER_LABEL = 20  # page-level ``speaker_label`` limit (it must fit the name tab)
HERO = "hero"  # ``speaker`` / ``speech.who`` value for the hero child
OPEN_QUOTE, CLOSE_QUOTE = "\u201c", "\u201d"


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _list(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


@dataclass
class FamilyVariant:
    id: str
    skin_tone: str
    hair: str


@dataclass
class Costume:
    """What a character wears on some pages only (e.g. the wolf in Grandma's nightcap)."""

    id: str
    pages: list[int]
    description: str
    sheet: bool = True  # gets its own reference sheet (False: pages use the plain one)


@dataclass
class Character:
    id: str
    name: str
    description: str
    family: bool = False
    variants: dict[str, FamilyVariant] = field(default_factory=dict)
    pages: list[int] = field(default_factory=list)
    age: int | None = None  # apparent age in years (required for family characters)
    size: str = ""  # size relative to the hero child ("" = not stated)
    costumes: list[Costume] = field(default_factory=list)
    plain: str = ""  # what "no costume" means for this character ("" = generic wording)
    speaker_label: str = ""  # short name for the page-text name tab ("" = derived from name)
    # Gentler wording for the character's reference sheet ("" = ``description``): a
    # close-up, isolated figure of e.g. a wolf was refused as PROHIBITED_CONTENT.
    sheet_description: str = ""
    # Order in which supporting characters' reference sheets are sent when a page has more
    # than it may send (0 first: large humanoid characters; small animals / props last).
    ref_priority: int = 1
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def tab_label(self) -> str:
        """Short display name for the dialogue-box tab: ``speaker_label`` or the name
        without a leading article, first letter capitalised ("the wolf" -> "Wolf")."""
        if self.speaker_label:
            return self.speaker_label
        name = self.name
        for article in ("the ", "The "):
            if name.startswith(article):
                name = name[len(article):]
        return name[:1].upper() + name[1:]

    @property
    def elderly(self) -> bool:
        return self.age is not None and self.age >= ELDERLY_AGE

    def costume_for(self, page: int | None) -> Costume | None:
        if page is None:
            return None
        return next((c for c in self.costumes if page in c.pages), None)

    def sheet_costume_id(self, page: int | None) -> str:
        """Costume id of the reference sheet a page uses ("" = the plain sheet)."""
        cos = self.costume_for(page)
        return cos.id if cos is not None and cos.sheet else ""

    def sheet_costumes(self) -> list[Costume | None]:
        """Every distinct reference sheet of this character: plain (None) + own costumes."""
        return [None] + [c for c in self.costumes if c.sheet]

    def costume(self, costume_id: str) -> Costume | None:
        return next((c for c in self.costumes if c.id == costume_id), None)


@dataclass(frozen=True)
class Speech:
    """One spoken span of a page's ``text`` (template, before placeholders are filled)."""

    who: str  # character id or "hero"
    says: str  # exact substring of the page text


@dataclass
class Page:
    page: int
    layout: str
    title: str
    text: str
    caption: str
    plate_prompt: str
    hero_instruction: str
    characters: list[str]
    palette_note: str
    must_be_visible: list[str]
    props: list[str] = field(default_factory=list)  # prop ids from Story.props
    art_caption: str = ""  # scene for the image model / grader ("" = caption)
    expression: str = ""  # the hero's emotion on this page ("" = not stated)
    # Who speaks on this page, for the name tab on the text box: a character id, or
    # "hero" for the child ("" = no tab: narration or several speakers). Art/meta only.
    speaker: str = ""
    # Overrides the tab text on this page ("" = the speaker's own label), e.g. "???".
    speaker_label: str = ""
    # Spoken spans of ``text`` in reading order (quotes + highlight when rendered).
    speech: list[Speech] = field(default_factory=list)
    # The dialogue box may show the speaker's portrait (only if the grader confirms the
    # speaker is not in the chosen picture) with this face.
    portrait: bool = False
    # Why: "offscreen" (only if the speaker is not in the picture) or "moment" (always).
    portrait_when: str = PORTRAIT_OFFSCREEN
    portrait_expression: str = "neutral"
    # Faces of other characters on this page (character id -> emotion) and apparent size
    # anchors (character id -> size on this page), both art fields.
    expressions: dict[str, str] = field(default_factory=dict)
    sizes: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def scene(self) -> str:
        """The scene description sent to the image model and the grader. ``caption`` is
        also shown to the reader, so art-only rewrites go into ``art_caption``."""
        return self.art_caption or self.caption


@dataclass
class Story:
    id: str
    title: str
    hero_outfit: str  # description + continuity, "" if none
    characters: dict[str, Character]
    pages: list[Page]
    subtitle: str = ""  # contains text placeholders, e.g. "starring {name} as Jack"
    family_variant_ids: list[str] = field(default_factory=list)
    about_me: dict = field(default_factory=dict)  # raw about_me block (heading, ...)
    props: dict[str, str] = field(default_factory=dict)  # prop id -> canonical description
    prop_states: dict[str, list[Costume]] = field(default_factory=dict)  # page-dependent
    # Items the hero wears only on some pages (``hero_outfit.extras``).
    hero_extras: list[Costume] = field(default_factory=list)
    # The outfit's ``description`` alone (no continuity details such as which elbow a patch
    # is on): what the hero REFERENCE check judges.
    hero_outfit_core: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def hero_outfit_for(self, page: int | None) -> str:
        """The hero outfit on ``page``: the base outfit plus that page's extras."""
        extras = [x.description for x in self.hero_extras
                  if page is not None and page in x.pages]
        return " ".join([self.hero_outfit, *extras]).strip()

    def prop_state(self, prop_id: str, page: int) -> str:
        """The prop's state on ``page`` ("" = none stated)."""
        return next((st.description for st in self.prop_states.get(prop_id, ())
                     if page in st.pages), "")

    def speaker_label(self, number: int, hero_name: str = "") -> str:
        """Name-tab text for a page: the hero's name for ``hero``, the character's short
        label otherwise, "" when the page has no speaker."""
        pg = self.page(number)
        who = pg.speaker
        if not who:
            return ""
        if pg.speaker_label:
            return pg.speaker_label
        if who == "hero":
            return hero_name
        char = self.characters.get(who)
        return char.tab_label if char else ""

    def speaker_name(self, who: str, hero_name: str = "") -> str:
        """Display name of a speaker id: the hero's name, else the character's name."""
        if who == HERO:
            return hero_name or "the hero child"
        char = self.characters.get(who)
        return char.name if char else who

    def speech_runs(self, number: int, text: str, hero_name: str,
                    pronouns: str) -> list[tuple[str, str | None]]:
        """``text`` (the page text as rendered for this hero) split into runs
        ``(text, who)``; ``who`` is None for narration. Each span's ``says`` is rendered
        with the same name / pronouns and found left to right; if one is missing (the text
        was edited) the whole page stays narration. Raises LookupError for an unknown
        page."""
        spans = [(sp.who, render_text(sp.says, hero_name, pronouns))
                 for sp in self.page(number).speech]
        return split_speech(text, spans)

    def page(self, number: int) -> Page:
        for p in self.pages:
            if p.page == number:
                return p
        raise LookupError(f"page {number} not found in {self.id}")


@dataclass
class Layout:
    id: str
    slot: int | None
    composition: str
    allowed_text_zones: list[dict]
    quiet_zone_instruction: str
    raw: dict = field(default_factory=dict, repr=False)


def _outfit(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return " ".join(
            _text(value[k]) for k in ("description", "continuity") if value.get(k)
        )
    return _text(value)


def _char_map(p: dict, key: str, story_id: str) -> dict[str, str]:
    """A page's ``expressions`` / ``sizes``: {character id: text}; ids must be on the page."""
    raw = p.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"story {story_id!r} page {p.get('page')}: `{key}` must be a "
                         "mapping of character id -> text")
    chars = {str(c) for c in _list(p.get("characters"))}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if str(k) not in chars:
            raise ValueError(f"story {story_id!r} page {p.get('page')}: `{key}` names "
                             f"{k!r}, which is not in the page's characters")
        if not _text(v):
            raise ValueError(f"story {story_id!r} page {p.get('page')}: empty `{key}` "
                             f"for {k!r}")
        out[str(k)] = _text(v)
    return out


def _ref_priority(c: dict, story_id: str) -> int:
    value = c.get("ref_priority")
    if value is None:
        return 2 if _text(c.get("size")).lower().startswith("small") else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"story {story_id!r} character {c['id']!r}: `ref_priority` must be "
                         f"a whole number >= 0, got {value!r}")
    return value


def _age(c: dict, story_id: str) -> int | None:
    """The character's apparent ``age``: whole years 1..120; required for family."""
    value = c.get("age")
    if value is None:
        if c.get("family"):
            raise ValueError(f"story {story_id!r} character {c['id']!r}: family characters "
                             "need an apparent `age` (years)")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise ValueError(f"story {story_id!r} character {c['id']!r}: `age` must be a whole "
                         f"number of years 1..120, got {value!r}")
    return value


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _prop(story_id: str, pid: str, value: Any) -> str:
    """Canonical prop text; a dict with ``count`` gets an "exactly N" clause."""
    if not isinstance(value, dict):
        return _text(value)
    desc = _text(value.get("description"))
    count = value.get("count")
    if count is None:
        return desc
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"story {story_id!r} prop {pid!r}: `count` must be a whole number "
                         f">= 1, got {count!r}")
    if count == 1:
        return (f"{desc} - exactly one in the whole picture, never two: no second copy "
                "of it anywhere else in the scene")
    word = _NUMBER_WORDS.get(count, str(count))
    return f"{desc} - exactly {word} in the whole picture, never more"


def _states(value: Any, story_id: str, pid: str) -> list[Costume]:
    """Page-dependent prop states (same shape as costumes; ``id`` optional)."""
    raw = value.get("states") if isinstance(value, dict) else None
    out: list[Costume] = []
    seen: set[int] = set()
    for i, st in enumerate(_list(raw)):
        if not isinstance(st, dict) or not st.get("description"):
            raise ValueError(f"story {story_id!r} prop {pid!r}: every state needs a "
                             "`description`")
        pages = [int(n) for n in _list(st.get("pages"))]
        if seen & set(pages):
            raise ValueError(f"story {story_id!r} prop {pid!r}: overlapping state pages")
        seen |= set(pages)
        out.append(Costume(id=str(st.get("id") or f"state{i + 1}"), pages=pages,
                           description=_text(st["description"]), sheet=False))
    return out


def _costumes(c: dict, story_id: str) -> list[Costume]:
    out: list[Costume] = []
    seen_pages: dict[int, str] = {}
    for raw in _list(c.get("costumes")):
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ValueError(f"story {story_id!r} character {c['id']!r}: every costume needs "
                             "an `id`")
        cid = str(raw["id"])
        if len(cid) > 32 or any(x.id == cid for x in out):
            raise ValueError(f"story {story_id!r} character {c['id']!r}: costume id "
                             f"{cid!r} is duplicated or longer than 32 characters")
        pages = [int(n) for n in _list(raw.get("pages"))]
        for n in pages:
            if n in seen_pages:
                raise ValueError(f"story {story_id!r} character {c['id']!r}: page {n} has "
                                 f"two costumes ({seen_pages[n]!r} and {cid!r})")
            seen_pages[n] = cid
        out.append(Costume(id=cid, pages=pages, description=_text(raw.get("description")),
                           sheet=bool(raw.get("sheet", True))))
    return out


def _speech(p: dict, story_id: str) -> list[Speech]:
    out: list[Speech] = []
    for raw in _list(p.get("speech")):
        if not isinstance(raw, dict) or not raw.get("who") or not raw.get("says"):
            raise ValueError(f"story {story_id!r} page {p.get('page')}: every speech span "
                             "needs `who` and `says`")
        out.append(Speech(who=str(raw["who"]).strip(), says=str(raw["says"])))
    return out


def speech_offsets(text: str,
                   spans: list[tuple[str, str]]) -> list[tuple[int, int, str]] | None:
    """(start, end, who) of each ``says`` found left to right in ``text``; None if one is
    missing or empty."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for who, says in spans:
        if not says:
            return None
        at = text.find(says, pos)
        if at < 0:
            return None
        out.append((at, at + len(says), who))
        pos = at + len(says)
    return out


def split_speech(text: str, spans: list[tuple[str, str]]) -> list[tuple[str, str | None]]:
    """``text`` as runs ``(text, who)`` (who None = narration); all narration when a span
    does not match."""
    found = speech_offsets(text, spans) if spans else None
    if not found:
        return [(text, None)] if text else []
    runs: list[tuple[str, str | None]] = []
    pos = 0
    for start, end, who in found:
        if start > pos:
            runs.append((text[pos:start], None))
        runs.append((text[start:end], who))
        pos = end
    if pos < len(text):
        runs.append((text[pos:], None))
    return runs


def display_runs(runs: list[tuple[str, str | None]]) -> list[tuple[str, bool]]:
    """Runs as shown to the reader: each spoken span wrapped in curly quotation marks and
    flagged ``True`` (highlighted); narration ``False``. Quotes, never colour alone, mark
    speech (colour-blind safe). The story wording itself is unchanged."""
    return [(f"{OPEN_QUOTE}{t}{CLOSE_QUOTE}", True) if who else (t, False)
            for t, who in runs]


def _portrait_mode(value: Any) -> str:
    """``portrait:`` value -> "" (none), "offscreen" (true) or "moment"; anything else is
    returned as text and rejected by ``_check_speech``."""
    if value is None or value is False:
        return ""
    if value is True:
        return PORTRAIT_OFFSCREEN
    return _text(value)


def _check_speech(story_id: str, pg: Page, chars: dict[str, Character]) -> None:
    """Load-time validation of ``speech``, ``portrait`` and ``portrait_expression``."""
    where = f"story {story_id!r} page {pg.page}"
    for sp in pg.speech:
        if sp.who != HERO and sp.who not in chars:
            raise ValueError(f"{where}: unknown speech speaker {sp.who!r}")
        if sp.says != sp.says.strip() or " ".join(sp.says.split()) != sp.says:
            raise ValueError(f"{where}: speech span {sp.says!r} must not start or end with "
                             "a space or contain double spaces")
        if any(q in sp.says for q in ('"', OPEN_QUOTE, CLOSE_QUOTE)):
            raise ValueError(f"{where}: speech span {sp.says!r} must not contain quotation "
                             "marks (they are added when the page is drawn)")
    found = speech_offsets(pg.text, [(sp.who, sp.says) for sp in pg.speech])
    if pg.speech and found is None:
        raise ValueError(f"{where}: every speech `says` must be an exact substring of the "
                         f"page text, in reading order: {[sp.says for sp in pg.speech]}")
    for start, end, _ in found or []:
        for m in _PLACEHOLDER.finditer(pg.text):
            inside = start <= m.start() and m.end() <= end
            if start < m.end() and m.start() < end and not inside:
                raise ValueError(f"{where}: a speech span cuts the placeholder "
                                 f"{m.group(0)!r}")
    whos = {sp.who for sp in pg.speech}
    if pg.speaker and whos and whos != {pg.speaker}:
        raise ValueError(f"{where}: speaker {pg.speaker!r} but speech spans by "
                         f"{sorted(whos)}; leave `speaker` out when several speak")
    if "speaker_label" in pg.raw:
        label = pg.raw["speaker_label"]
        if (not isinstance(label, str) or not label.strip()
                or len(label.strip()) > MAX_SPEAKER_LABEL):
            raise ValueError(f"{where}: `speaker_label` must be a short non-empty string "
                             f"(at most {MAX_SPEAKER_LABEL} characters), got {label!r}")
        if not pg.speaker:
            raise ValueError(f"{where}: `speaker_label` needs a `speaker`")
    if pg.portrait and not pg.speaker:
        raise ValueError(f"{where}: `portrait` needs a single `speaker`")
    if pg.portrait_when not in PORTRAIT_REASONS:
        raise ValueError(f"{where}: `portrait` must be true, false, 'offscreen' or "
                         f"'moment', got {pg.raw.get('portrait')!r}")
    if pg.portrait_expression not in PORTRAIT_EXPRESSIONS:
        raise ValueError(f"{where}: portrait_expression must be one of "
                         f"{PORTRAIT_EXPRESSIONS}, got {pg.portrait_expression!r}")


def _parse_story(data: dict, fallback_id: str) -> Story:
    story_id = str(data.get("id") or fallback_id)
    chars: dict[str, Character] = {}
    for c in _list(data.get("characters")):
        if isinstance(c, dict) and c.get("id"):
            chars[str(c["id"])] = Character(
                id=str(c["id"]),
                name=_text(c.get("name") or c["id"]),
                description=_text(c.get("description")),
                family=bool(c.get("family")),
                variants={
                    str(v["id"]): FamilyVariant(
                        id=str(v["id"]),
                        skin_tone=_text(v.get("skin_tone")),
                        hair=_text(v.get("hair")),
                    )
                    for v in _list(c.get("variants"))
                    if isinstance(v, dict) and v.get("id")
                },
                pages=[int(n) for n in _list(c.get("pages"))],
                age=_age(c, story_id),
                size=_text(c.get("size")),
                costumes=_costumes(c, story_id),
                plain=_text(c.get("plain")),
                speaker_label=_text(c.get("speaker_label")),
                sheet_description=_text(c.get("sheet_description")),
                ref_priority=_ref_priority(c, story_id),
                raw=c,
            )
    pages = []
    for p in _list(data.get("pages")):
        if not isinstance(p, dict) or "page" not in p:
            continue
        pages.append(
            Page(
                page=int(p["page"]),
                layout=str(p.get("layout") or ""),
                title=_text(p.get("title")),
                text=_text(p.get("text")),
                caption=_text(p.get("caption")),
                plate_prompt=_text(p.get("plate_prompt")),
                hero_instruction=_text(p.get("hero_instruction")),
                characters=[str(c) for c in _list(p.get("characters"))],
                palette_note=_text(p.get("palette_note")),
                must_be_visible=[_text(m) for m in _list(p.get("must_be_visible"))],
                props=[str(x) for x in _list(p.get("props"))],
                art_caption=_text(p.get("art_caption")),
                expression=_text(p.get("expression")),
                speaker=_text(p.get("speaker")),
                speaker_label=_text(p.get("speaker_label")),
                speech=_speech(p, story_id),
                portrait=bool(_portrait_mode(p.get("portrait"))),
                portrait_when=_portrait_mode(p.get("portrait")) or PORTRAIT_OFFSCREEN,
                portrait_expression=_text(p.get("portrait_expression")) or "neutral",
                expressions=_char_map(p, "expressions", story_id),
                sizes=_char_map(p, "sizes", story_id),
                raw=p,
            )
        )
    pages.sort(key=lambda pg: pg.page)
    raw_props = data.get("props")
    props = ({str(k): _prop(story_id, str(k), v) for k, v in raw_props.items()}
             if isinstance(raw_props, dict) else {})
    for pg in pages:
        if pg.speaker and pg.speaker != "hero" and pg.speaker not in chars:
            raise ValueError(f"story {story_id!r} page {pg.page}: unknown speaker "
                             f"{pg.speaker!r}; use a character id or 'hero'")
        _check_speech(story_id, pg, chars)
        unknown = [x for x in pg.props if x not in props]
        if unknown:
            raise ValueError(f"story {story_id!r} page {pg.page}: unknown prop id(s) "
                             f"{unknown}; known: {sorted(props)}")
    return Story(
        id=story_id,
        title=_text(data.get("title")),
        hero_outfit=_outfit(data.get("hero_outfit")),
        hero_extras=(_states({"states": data["hero_outfit"].get("extras")}, story_id,
                             "hero_outfit.extras")
                     if isinstance(data.get("hero_outfit"), dict)
                     and data["hero_outfit"].get("extras") else []),
        hero_outfit_core=(_text(data["hero_outfit"].get("description"))
                          if isinstance(data.get("hero_outfit"), dict)
                          else _outfit(data.get("hero_outfit"))),
        characters=chars,
        pages=pages,
        subtitle=_text(data.get("subtitle")),
        family_variant_ids=[str(v) for v in _list(data.get("family_variant_ids"))],
        about_me=data.get("about_me") if isinstance(data.get("about_me"), dict) else {},
        props=props,
        prop_states=({str(k): _states(v, story_id, str(k)) for k, v in raw_props.items()
                      if isinstance(v, dict) and v.get("states")}
                     if isinstance(raw_props, dict) else {}),
        raw=data,
    )


@lru_cache
def load_story(story_id: str) -> Story:
    if not re.fullmatch(r"[a-z0-9_]+", story_id):
        raise ValueError(f"bad story id: {story_id!r}")
    path = STORIES_DIR / f"{story_id}.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _parse_story(data, story_id)


def _parse_layout(d: dict) -> Layout:
    return Layout(
        id=str(d.get("id")),
        slot=d.get("slot"),
        composition=_text(d.get("composition")),
        allowed_text_zones=[z for z in _list(d.get("allowed_text_zones")) if isinstance(z, dict)],
        quiet_zone_instruction=_text(d.get("quiet_zone_instruction")),
        raw=d,
    )


@lru_cache
def load_layouts() -> dict[str, Layout]:
    with LAYOUTS_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    layouts = {
        str(s["id"]): _parse_layout(s)
        for s in _list(data.get("slots"))
        if isinstance(s, dict) and s.get("id")
    }
    about = data.get("about_me")
    if isinstance(about, dict):
        layouts[str(about.get("id") or "about_me")] = _parse_layout(
            {"id": "about_me", **about}
        )
    return layouts


def get_layout(layout_id: str) -> Layout | None:
    return load_layouts().get(layout_id)


def find_page(story: Story, *, title: str | None = None, page: int | None = None) -> Page:
    """Find a page by title (case-insensitive, must be unique); fall back to page number."""
    if title:
        wanted = title.strip().casefold()
        hits = [p for p in story.pages if p.title.casefold() == wanted]
        if len(hits) == 1:
            return hits[0]
    if page is not None:
        hits = [p for p in story.pages if p.page == page]
        if len(hits) == 1:
            return hits[0]
    raise LookupError(f"page not found in {story.id}: title={title!r} page={page!r}")


def clear_caches() -> None:
    load_story.cache_clear()
    load_layouts.cache_clear()


# --- page text placeholders --------------------------------------------------

_PRONOUNS: dict[str, dict[str, str]] = {
    "he": {"they": "he", "them": "him", "their": "his", "theirs": "his", "themself": "himself",
           "is": "is", "was": "was", "has": "has", "does": "does"},
    "she": {"they": "she", "them": "her", "their": "her", "theirs": "hers", "themself": "herself",
            "is": "is", "was": "was", "has": "has", "does": "does"},
    "they": {"they": "they", "them": "them", "their": "their", "theirs": "theirs",
             "themself": "themself", "is": "are", "was": "were", "has": "have", "does": "do"},
}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def normalise_pronouns(pronouns: str) -> str:
    key = (pronouns or "").strip().lower().split("/")[0]
    if key not in _PRONOUNS:
        raise ValueError(f"pronouns must be he, she or they, got {pronouns!r}")
    return key


def render_text(text: str, name: str, pronouns: str) -> str:
    """Fill {name} {they} {them} {their} {theirs} {themself} {They} {Their} {is} {was}
    {has} {does}. Capitalised placeholders produce capitalised words. Unknown
    placeholders are left untouched."""
    table = dict(_PRONOUNS[normalise_pronouns(pronouns)])
    table["name"] = name
    table["hero_name"] = name

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in table:
            return table[key]
        low = key[:1].lower() + key[1:]
        if key[:1].isupper() and low in table:
            value = table[low]
            return value[:1].upper() + value[1:]
        return m.group(0)

    return _PLACEHOLDER.sub(sub, text)
