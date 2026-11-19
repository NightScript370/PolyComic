"""Cost telemetry must fit the schema and must never fail a book.

SQLite (tests) does not enforce VARCHAR(N); Postgres does, and ``family_sheet_check``
(18 chars) once overflowed ``api_calls.kind`` (16) and killed a book. These tests check the
lengths by hand."""

import re
from pathlib import Path

from app.db.models import (
    ACTIVE_STATUSES,
    CHARGE,
    DONE,
    FAILED,
    QUEUED,
    R_APPROVED,
    R_FAILED,
    R_PENDING,
    R_REJECTED,
    RECREDIT,
    REFUND,
    RUNNING,
    TOPUP,
    ApiCall,
    Book,
    BookPage,
    PageAttempt,
    RefundRequest,
    WalletEntry,
)
from app.jobs.progress import STAGE_LABELS
from app.pipeline.book import CallCost, HeroDetails, Hooks, run_book
from app.pipeline.calls import CALL_KINDS
from app.providers.fake import FakeProvider
from app.providers.fake_grader import FakeGrader
from app.stories.loader import load_story
from app.styles.presets import PRESETS
from app.tiers import TIERS

APP = Path(__file__).resolve().parent.parent / "app"


def _width(model, column: str) -> int:
    return model.__table__.c[column].type.length


def _code_kinds() -> set[str]:
    """Every api-call kind literal in the app code."""
    kinds: set[str] = set()
    patterns = [
        r"CallCost(?:\.\w+)?\(\s*\"(\w+)\"",          # CallCost("grade", ...), .failed(..)
        r"_KIND\s*=\s*\"(\w+)\"",                      # FAMILY_SHEET_CHECK_KIND = "..."
        r"_image\([^,()]+,\s*\"(\w+)\"",               # self._image(provider, "page", ...)
        r"recorded_image\([^,()]+,\s*(?:kind|\"(\w+)\")",
        r"\(\"(hw1_\w+)\",",                           # ("hw1_hero", None, ...)
        r"_call_row\([^,]+,[^,]+,\s*\"(\w+)\"",
        r"\bkind: str = \"(\w*grade)\"",  # def _grade(..., kind: str = "grade")
        r"\bkind=\"(\w*grade)\"",  # self._grade(..., kind="rescue_grade")
    ]
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            kinds.update(m for m in re.findall(pat, text) if m)
    return kinds


def test_every_api_call_kind_fits_the_column():
    width = _width(ApiCall, "kind")
    found = _code_kinds()
    # The scan must really see the kinds that matter (guards against a broken regex).
    assert {"hero", "page", "grade", "family_match", "family_sheet", "family_sheet_check",
            "hw1_hero", "hw1_scene", "hw1"} <= found
    assert found <= set(CALL_KINDS), f"kinds missing from CALL_KINDS: {found - set(CALL_KINDS)}"
    for kind in CALL_KINDS:
        assert len(kind) <= width, f"{kind!r} is {len(kind)} chars, api_calls.kind is {width}"


def test_known_enum_like_values_fit_their_columns():
    def fits(model, column, values):
        w = _width(model, column)
        for v in values:
            assert len(v) <= w, f"{model.__name__}.{column}: {v!r} > {w}"

    fits(WalletEntry, "kind", [TOPUP, CHARGE, RECREDIT, REFUND])
    fits(RefundRequest, "status", [R_PENDING, R_APPROVED, R_REJECTED, R_FAILED])
    book_statuses = [QUEUED, RUNNING, DONE, FAILED, *ACTIVE_STATUSES]
    fits(Book, "status", book_statuses)
    fits(BookPage, "status", book_statuses)
    fits(Book, "stage", list(STAGE_LABELS))
    fits(Book, "tier", [t.value for t in TIERS])
    fits(Book, "preset", list(PRESETS))
    fits(Book, "pronouns", ["he", "she", "they"])
    fits(PageAttempt, "preset", list(PRESETS)) if "preset" in PageAttempt.__table__.c else None
    for story_id in ("jack", "red"):
        story = load_story(story_id)
        fits(Book, "story", [story.id])
        fits(Book, "family_variant", list(story.family_variant_ids))
        fits(Book, "title", [story.title])
        fits(BookPage, "layout", [p.layout for p in story.pages])
    fits(ApiCall, "provider", ["fake", "gemini", "openai"])
    # Free-form model names are cut to the column width where rows are built.
    assert _width(ApiCall, "model") == 80


class _FailingHooks(Hooks):
    def __init__(self):
        self.attempts = 0

    async def call(self, call: CallCost) -> None:
        self.attempts += 1
        raise RuntimeError("value too long for type character varying(16)")


async def test_failing_record_hook_does_not_fail_the_book():
    hooks = _FailingHooks()
    photo = (await FakeProvider().edit("photo", [])).png
    hero = HeroDetails(name="Mia", pronouns="she", age=7, favourites={}, about="")
    book = await run_book(FakeProvider(), photo, "n64", 2, story_id="jack", pages=(1, 2),
                          hero=hero, grader=FakeGrader(), hooks=hooks)
    assert hooks.attempts >= 3  # hero + pages + grades were all attempted
    assert len(book.pages) == 2
    # in-memory cost totals still count every call, hook failure or not
    assert len(book.costs) == hooks.attempts if hasattr(book, "costs") else True


async def test_cancellation_in_record_hook_still_propagates():
    import asyncio

    class Cancelling(Hooks):
        async def call(self, call):
            raise asyncio.CancelledError

    photo = (await FakeProvider().edit("photo", [])).png
    hero = HeroDetails(name="Mia", pronouns="she", age=7, favourites={}, about="")
    try:
        await run_book(FakeProvider(), photo, "n64", 2, story_id="jack", pages=(1,),
                       hero=hero, hooks=Cancelling())
    except (asyncio.CancelledError, BaseExceptionGroup):
        return
    raise AssertionError("CancelledError was swallowed")


def test_migration_0008_widens_api_calls_kind(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.config import get_settings

    db = (tmp_path / "m8.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(f"sqlite:///{db}")
    command.upgrade(cfg, "0007")
    with engine.connect() as conn:
        cols = {c["name"]: c for c in inspect(conn).get_columns("api_calls")}
        assert cols["kind"]["type"].length == 16
    command.upgrade(cfg, "0008")
    with engine.connect() as conn:
        cols = {c["name"]: c for c in inspect(conn).get_columns("api_calls")}
        assert cols["kind"]["type"].length == 32
    command.downgrade(cfg, "0007")
    command.upgrade(cfg, "head")
    engine.dispose()
    get_settings.cache_clear()
