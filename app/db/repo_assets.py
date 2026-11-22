"""Repository for pre-generated assets (character bible, scene plates) and the per-picture
quality-loop attempts. Kept apart from ``app.db.repo`` (shared with billing)."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, CharacterBible, CharacterPortrait, PageAttempt, ScenePlate

# --- character bible / plates --------------------------------------------------------


BIBLE_KEY = ("story", "character_id", "variant_id", "costume_id", "preset")


async def bible_keys(s: AsyncSession, story: str, preset: str) -> set[tuple[str, str]]:
    """(character_id, variant_id) of the plain sheets (no costume)."""
    q = select(CharacterBible.character_id, CharacterBible.variant_id).where(
        CharacterBible.story == story, CharacterBible.preset == preset,
        CharacterBible.costume_id == "")
    return {(c, v) for c, v in (await s.execute(q)).all()}


async def sheet_keys(s: AsyncSession, story: str, preset: str) -> set[tuple[str, str, str]]:
    """(character_id, variant_id, costume_id) of every sheet of a story x preset."""
    q = select(CharacterBible.character_id, CharacterBible.variant_id,
               CharacterBible.costume_id).where(CharacterBible.story == story,
                                                CharacterBible.preset == preset)
    return {(c, v, k) for c, v, k in (await s.execute(q)).all()}


async def plate_pages(s: AsyncSession, story: str, preset: str) -> set[int]:
    q = select(ScenePlate.page).where(ScenePlate.story == story, ScenePlate.preset == preset)
    return set((await s.execute(q)).scalars().all())


async def get_bible_png(s: AsyncSession, story: str, preset: str, character_id: str,
                        variant_id: str = "", costume_id: str = "") -> bytes | None:
    q = select(CharacterBible.png).where(
        CharacterBible.story == story, CharacterBible.preset == preset,
        CharacterBible.character_id == character_id, CharacterBible.variant_id == variant_id,
        CharacterBible.costume_id == costume_id)
    return (await s.execute(q)).scalar_one_or_none()


async def get_sheet_png(s: AsyncSession, story: str, preset: str, character_id: str,
                        costume_id: str = "") -> bytes | None:
    """A supporting (non-family) character's sheet for ``costume_id`` ("" = plain)."""
    return await get_bible_png(s, story, preset, character_id, "", costume_id)


async def insert_sheet(s: AsyncSession, *, story: str, character_id: str, costume_id: str,
                       preset: str, png: bytes, prompt: str, model: str) -> bytes:
    """Store a supporting character's sheet unless one exists for the same key (INSERT ...
    ON CONFLICT DO NOTHING - two books making the same sheet at once never fail on the
    unique index); returns the stored sheet, which is the other book's if it won."""
    values = dict(story=story, character_id=character_id, variant_id="",
                  costume_id=costume_id, preset=preset, png=png, prompt=prompt, model=model)
    dialect = s.get_bind().dialect.name
    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = ins(CharacterBible).values(**values).on_conflict_do_nothing(
        index_elements=list(BIBLE_KEY))
    await s.execute(stmt)
    await s.commit()
    stored = await get_sheet_png(s, story, preset, character_id, costume_id)
    return stored if stored is not None else png


PORTRAIT_KEY = ("story", "character_id", "costume_id", "expression", "preset")


async def get_portrait_png(s: AsyncSession, story: str, preset: str, character_id: str,
                           costume_id: str, expression: str) -> bytes | None:
    """A supporting character's stored speaker portrait, or None."""
    q = select(CharacterPortrait.png).where(
        CharacterPortrait.story == story, CharacterPortrait.preset == preset,
        CharacterPortrait.character_id == character_id,
        CharacterPortrait.costume_id == costume_id,
        CharacterPortrait.expression == expression)
    return (await s.execute(q)).scalar_one_or_none()


async def insert_portrait(s: AsyncSession, *, story: str, character_id: str,
                          costume_id: str, expression: str, preset: str, png: bytes,
                          prompt: str, model: str) -> bytes:
    """Store a speaker portrait unless one exists for the same key (INSERT ... ON CONFLICT
    DO NOTHING); returns the stored portrait (the other book's if it won)."""
    values = dict(story=story, character_id=character_id, costume_id=costume_id,
                  expression=expression, preset=preset, png=png, prompt=prompt, model=model)
    dialect = s.get_bind().dialect.name
    ins = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = ins(CharacterPortrait).values(**values).on_conflict_do_nothing(
        index_elements=list(PORTRAIT_KEY))
    await s.execute(stmt)
    await s.commit()
    stored = await get_portrait_png(s, story, preset, character_id, costume_id, expression)
    return stored if stored is not None else png


async def get_bible_pngs(s: AsyncSession, story: str, preset: str,
                         keys: list[tuple[str, str]]) -> dict[tuple[str, str], bytes]:
    """PNG per (character_id, variant_id) for the keys that exist."""
    if not keys:
        return {}
    ids = {c for c, _ in keys}
    q = select(CharacterBible.character_id, CharacterBible.variant_id, CharacterBible.png).where(
        CharacterBible.story == story, CharacterBible.preset == preset,
        CharacterBible.character_id.in_(ids))
    wanted = set(keys)
    q = q.where(CharacterBible.costume_id == "")
    return {(c, v): png for c, v, png in (await s.execute(q)).all() if (c, v) in wanted}


async def get_plate_png(s: AsyncSession, story: str, page: int, preset: str) -> bytes | None:
    q = select(ScenePlate.png).where(ScenePlate.story == story, ScenePlate.page == page,
                                     ScenePlate.preset == preset)
    return (await s.execute(q)).scalar_one_or_none()


async def save_bible(s: AsyncSession, *, story: str, character_id: str, variant_id: str,
                     preset: str, png: bytes, prompt: str, model: str,
                     costume_id: str = "") -> None:
    """Insert or replace (``--force``)."""
    await s.execute(delete(CharacterBible).where(
        CharacterBible.story == story, CharacterBible.character_id == character_id,
        CharacterBible.variant_id == variant_id, CharacterBible.costume_id == costume_id,
        CharacterBible.preset == preset))
    s.add(CharacterBible(story=story, character_id=character_id, variant_id=variant_id,
                         costume_id=costume_id, preset=preset, png=png, prompt=prompt,
                         model=model))
    await s.commit()


async def save_plate(s: AsyncSession, *, story: str, page: int, preset: str, png: bytes,
                     prompt: str, model: str) -> None:
    await s.execute(delete(ScenePlate).where(
        ScenePlate.story == story, ScenePlate.page == page, ScenePlate.preset == preset))
    s.add(ScenePlate(story=story, page=page, preset=preset, png=png, prompt=prompt,
                     model=model))
    await s.commit()


# --- quality-loop attempts -------------------------------------------------------------


async def save_attempt(s: AsyncSession, row: PageAttempt) -> None:
    s.add(row)
    await s.commit()


async def get_attempt_thumb(s: AsyncSession, book_id: str, user_id: int, page: int,
                            attempt: int) -> bytes | None:
    """The 160 px JPEG of one generation of a picture (owner only), or None."""
    from app.db.models import Book

    q = (select(PageAttempt.thumb_jpeg).join(Book, Book.id == PageAttempt.book_id)
         .where(PageAttempt.book_id == book_id, PageAttempt.page == page,
                PageAttempt.attempt == attempt, Book.user_id == user_id))
    return (await s.execute(q)).scalars().first()


async def mark_chosen(s: AsyncSession, book_id: str, page: int, attempt: int) -> None:
    await s.execute(update(PageAttempt).where(
        PageAttempt.book_id == book_id, PageAttempt.page == page,
    ).values(chosen=PageAttempt.attempt == attempt))
    await s.commit()


async def mark_budget_stopped(s: AsyncSession, book_id: str, page: int, attempt: int) -> None:
    """Flag the kept attempt of a picture whose loop the time budget stopped."""
    await s.execute(update(PageAttempt).where(
        PageAttempt.book_id == book_id, PageAttempt.page == page,
        PageAttempt.attempt == attempt).values(budget_stopped=True))
    await s.commit()


async def mark_cost_stopped(s: AsyncSession, book_id: str, page: int, attempt: int) -> None:
    """Flag the kept attempt of a picture whose loop the book's cost cap stopped."""
    await s.execute(update(PageAttempt).where(
        PageAttempt.book_id == book_id, PageAttempt.page == page,
        PageAttempt.attempt == attempt).values(cost_stopped=True))
    await s.commit()


async def set_cost_skips(s: AsyncSession, book_id: str, skips: list[str]) -> None:
    """Store what the book's cost cap skipped (shown in reviews / admin)."""
    await s.execute(update(Book).where(Book.id == book_id).values(
        cost_skips=list(skips)[:100]))
    await s.commit()


async def list_attempts(s: AsyncSession, book_id: str) -> list[PageAttempt]:
    q = (select(PageAttempt).where(PageAttempt.book_id == book_id)
         .order_by(PageAttempt.page, PageAttempt.attempt))
    return list((await s.execute(q)).scalars().all())


async def attempt_summary(s: AsyncSession, book_id: str) -> dict[int, dict]:
    """Per picture (0 = hero): ``{"attempts": n, "scores": {...} | None,
    "budget_stopped": bool}`` for the event snapshot, from the chosen attempt."""
    counts = dict((await s.execute(
        select(PageAttempt.page, func.count()).where(PageAttempt.book_id == book_id)
        .group_by(PageAttempt.page))).all())
    chosen = (await s.execute(select(PageAttempt).where(
        PageAttempt.book_id == book_id, PageAttempt.chosen.is_(True)))).scalars().all()
    out: dict[int, dict] = {p: {"attempts": int(n), "scores": None, "budget_stopped": False}
                            for p, n in counts.items()}
    for row in chosen:
        entry = out.setdefault(row.page, {"attempts": 1, "scores": None,
                                          "budget_stopped": False})
        entry["scores"] = scores_dict(row.likeness, row.fidelity, row.passed)
        entry["budget_stopped"] = bool(row.budget_stopped)
    return out


def scores_dict(likeness: float | None, fidelity: float | None, passed: bool) -> dict | None:
    if likeness is None and fidelity is None:
        return None
    return {"likeness": None if likeness is None else round(likeness, 3),
            "fidelity": None if fidelity is None else round(fidelity, 3),
            "passed": bool(passed)}


async def delete_attempts(s: AsyncSession, book_id: str) -> None:
    """Explicit delete (SQLite does not enforce ON DELETE CASCADE without the pragma)."""
    await s.execute(delete(PageAttempt).where(PageAttempt.book_id == book_id))
    await s.commit()


async def set_family_variant(s: AsyncSession, book_id: str, variant: str) -> None:
    await s.execute(update(Book).where(Book.id == book_id).values(family_variant=variant))
    await s.commit()


async def set_family_sheet(s: AsyncSession, book_id: str, png: bytes) -> None:
    await s.execute(update(Book).where(Book.id == book_id).values(family_sheet_png=png))
    await s.commit()
