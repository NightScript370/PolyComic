"""Small repository over the ORM. Every write commits; blobs are read column-by-column.

Functions take an ``AsyncSession`` so request handlers can use the request session and
background jobs can open short sessions of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ACTIVE_STATUSES,
    FAILED,
    ApiCall,
    Book,
    BookPage,
    User,
    utcnow,
)

STALE_JOB_MESSAGE = "Server restarted - please try again."


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    dt = as_utc(dt)
    return dt.isoformat() if dt else None


# --- users ---------------------------------------------------------------------------


async def get_user(s: AsyncSession, user_id: int) -> User | None:
    return await s.get(User, user_id)


async def upsert_google_user(
    s: AsyncSession, *, sub: str, email: str, name: str, picture: str | None
) -> tuple[User, bool]:
    """Find by Google ``sub`` (stable id; emails can change) or create. Returns (user, created)."""
    user = (await s.execute(select(User).where(User.google_sub == sub))).scalar_one_or_none()
    created = user is None
    if user is None:
        user = User(google_sub=sub, email=email, name=name, picture=picture)
        s.add(user)
    else:
        user.email, user.name, user.picture = email, name, picture
    user.last_login_at = utcnow()
    await s.commit()
    await s.refresh(user)
    return user, created


# --- books -----------------------------------------------------------------------------


async def count_active_books(s: AsyncSession, user_id: int) -> int:
    q = select(func.count()).select_from(Book).where(
        Book.user_id == user_id, Book.status.in_(ACTIVE_STATUSES)
    )
    return int((await s.execute(q)).scalar_one())


async def create_book(s: AsyncSession, book: Book) -> Book:
    s.add(book)
    await s.commit()
    return book


async def get_book(s: AsyncSession, book_id: str, user_id: int | None = None) -> Book | None:
    """Book metadata (no blobs). With ``user_id``, returns None unless that user owns it."""
    q = select(Book).where(Book.id == book_id)
    if user_id is not None:
        q = q.where(Book.user_id == user_id)
    return (await s.execute(q)).scalar_one_or_none()


@dataclass
class BookListItem:
    book: Book
    has_hero: bool


async def list_books(s: AsyncSession, user_id: int) -> list[BookListItem]:
    q = (
        select(Book, Book.hero_thumb_jpeg.is_not(None))
        .where(Book.user_id == user_id)
        .order_by(Book.created_at.desc(), Book.id)
    )
    return [BookListItem(b, bool(h)) for b, h in (await s.execute(q)).all()]


async def has_hero(s: AsyncSession, book_id: str) -> bool:
    q = select(Book.hero_thumb_jpeg.is_not(None)).where(Book.id == book_id)
    return bool((await s.execute(q)).scalar_one_or_none())


async def set_book_state(
    s: AsyncSession,
    book_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    error: str | None = None,
    finished: bool = False,
) -> None:
    values: dict = {}
    if status is not None:
        values["status"] = status
    if stage is not None:
        values["stage"] = stage
    if error is not None:
        values["error"] = error
    if finished:
        values["finished_at"] = utcnow()
    if values:
        await s.execute(update(Book).where(Book.id == book_id).values(**values))
        await s.commit()


async def save_hero(s: AsyncSession, book_id: str, png: bytes, thumb: bytes) -> None:
    await s.execute(
        update(Book).where(Book.id == book_id).values(hero_png=png, hero_thumb_jpeg=thumb)
    )
    await s.commit()


async def save_page(s: AsyncSession, page: BookPage) -> None:
    s.add(page)
    await s.commit()


async def save_pdf(s: AsyncSession, book_id: str, pdf: bytes, filename: str) -> None:
    await s.execute(update(Book).where(Book.id == book_id).values(pdf=pdf, pdf_filename=filename))
    await s.commit()


async def list_pages(s: AsyncSession, book_id: str) -> list[BookPage]:
    q = select(BookPage).where(BookPage.book_id == book_id).order_by(BookPage.page)
    return list((await s.execute(q)).scalars().all())


async def get_book_blob(s: AsyncSession, book_id: str, user_id: int, column: str) -> bytes | None:
    col = {"pdf": Book.pdf, "hero_png": Book.hero_png, "hero_thumb": Book.hero_thumb_jpeg,
           "photo": Book.photo_jpeg}[column]
    q = select(col).where(Book.id == book_id, Book.user_id == user_id)
    return (await s.execute(q)).scalar_one_or_none()


async def get_photo(s: AsyncSession, book_id: str) -> bytes | None:
    return (await s.execute(select(Book.photo_jpeg).where(Book.id == book_id))).scalar_one_or_none()


async def get_page_blob(
    s: AsyncSession, book_id: str, user_id: int, page: int, column: str
) -> bytes | None:
    col = {"png": BookPage.png, "thumb": BookPage.thumb_jpeg,
           "portrait": BookPage.portrait_png}[column]
    q = (
        select(col)
        .join(Book, Book.id == BookPage.book_id)
        .where(BookPage.book_id == book_id, BookPage.page == page, Book.user_id == user_id)
    )
    return (await s.execute(q)).scalar_one_or_none()


async def delete_book(s: AsyncSession, book_id: str) -> None:
    # Explicit (SQLite does not enforce FK actions unless PRAGMA foreign_keys is on).
    await s.execute(update(ApiCall).where(ApiCall.book_id == book_id).values(book_id=None))
    await s.execute(delete(BookPage).where(BookPage.book_id == book_id))
    await s.execute(delete(Book).where(Book.id == book_id))
    await s.commit()


async def fail_stale_books(
    s: AsyncSession, message: str = STALE_JOB_MESSAGE, *, user_id: int | None = None
) -> int:
    """Mark queued/running books failed (their in-process job died with the server).
    With ``user_id``, only that user's books."""
    q = update(Book).where(Book.status.in_(ACTIVE_STATUSES))
    if user_id is not None:
        q = q.where(Book.user_id == user_id)
    res = await s.execute(q.values(status=FAILED, error=message, finished_at=utcnow()))
    await s.commit()
    return res.rowcount or 0


# --- api calls ---------------------------------------------------------------------------


async def record_call(s: AsyncSession, call: ApiCall) -> None:
    s.add(call)
    await s.commit()


async def book_cost(s: AsyncSession, book_id: str) -> float:
    q = select(func.coalesce(func.sum(ApiCall.est_cost_usd), 0.0)).where(
        ApiCall.book_id == book_id
    )
    return round(float((await s.execute(q)).scalar_one()), 4)


async def set_book_cost(s: AsyncSession, book_id: str) -> float:
    cost = await book_cost(s, book_id)
    await s.execute(update(Book).where(Book.id == book_id).values(est_cost_usd=cost))
    await s.commit()
    return cost


async def list_calls(s: AsyncSession, book_id: str) -> list[ApiCall]:
    q = select(ApiCall).where(ApiCall.book_id == book_id).order_by(ApiCall.id)
    return list((await s.execute(q)).scalars().all())


# --- billing queries (writes live in app.billing.wallet, which owns the user lock) ----------


async def list_wallet_entries(s: AsyncSession, user_id: int, limit: int = 500):
    from app.db.models import WalletEntry

    q = (select(WalletEntry).where(WalletEntry.user_id == user_id)
         .order_by(WalletEntry.id.desc()).limit(limit))
    return list((await s.execute(q)).scalars().all())


async def list_refund_requests(s: AsyncSession, *, user_id: int | None = None,
                               status: str | None = None, limit: int = 200):
    from app.db.models import RefundRequest

    q = select(RefundRequest).order_by(RefundRequest.id.desc()).limit(limit)
    if user_id is not None:
        q = q.where(RefundRequest.user_id == user_id)
    if status:
        q = q.where(RefundRequest.status == status)
    return list((await s.execute(q)).scalars().all())


@dataclass
class BookCostRow:
    book_id: str
    story: str
    created_at: datetime
    cost_usd: float
    calls: int
    retries: int
    tier: str = "professional"
    # One-time shared assets this book happened to pay for (first-time supporting-character
    # sheets and their checks): reused by every later book of the story + style, so they
    # are NOT in ``cost_usd`` / ``calls``.
    shared_cost_usd: float = 0.0


# Call kinds of assets generated once per story x style and reused by later books (see
# app.pipeline.book.SHARED_KINDS). Excluded from a book's own cost, reported separately.
SHARED_ASSET_KINDS = ("cast_sheet", "cast_sheet_check")


async def done_book_costs(s: AsyncSession) -> list[BookCostRow]:
    """Per-book API spend and tier for completed books (every call tagged with the book:
    hero, page, grade and retries; plate/bible pregeneration and HW1 calls carry no book
    id). The book's own spend leaves out the shared one-time assets (``SHARED_ASSET_KINDS``),
    which are in ``shared_cost_usd``. ``retries`` = page calls beyond one per page. Newest
    book first."""
    from sqlalchemy import case

    page_calls = func.sum(case((ApiCall.kind == "page", 1), else_=0))
    distinct_pages = func.count(func.distinct(case((ApiCall.kind == "page", ApiCall.page))))
    shared = ApiCall.kind.in_(SHARED_ASSET_KINDS)
    q = (
        select(Book.id, Book.story, Book.created_at,
               func.coalesce(func.sum(case((shared, 0.0), else_=ApiCall.est_cost_usd)), 0.0),
               func.sum(case((shared, 0), else_=1)), page_calls, distinct_pages, Book.tier,
               func.coalesce(func.sum(case((shared, ApiCall.est_cost_usd), else_=0.0)), 0.0))
        .join(ApiCall, ApiCall.book_id == Book.id)
        .where(Book.status == "done")
        .group_by(Book.id, Book.story, Book.created_at, Book.tier)
        .order_by(Book.created_at.desc())
    )
    rows = (await s.execute(q)).all()
    return [BookCostRow(r[0], r[1], r[2], float(r[3]), int(r[4] or 0),
                        max(0, int(r[5] or 0) - int(r[6] or 0)), r[7], float(r[8] or 0.0))
            for r in rows]


async def shared_asset_cost(s: AsyncSession) -> tuple[int, float]:
    """(calls, cost_usd) of every recorded shared one-time asset call, all books."""
    q = select(func.count(ApiCall.id), func.coalesce(func.sum(ApiCall.est_cost_usd), 0.0)
               ).where(ApiCall.kind.in_(SHARED_ASSET_KINDS))
    n, c = (await s.execute(q)).one()
    return int(n), float(c)


async def cost_by_kind(s: AsyncSession) -> tuple[dict[str, tuple[int, float]], int]:
    """({kind: (calls, cost_usd)}, failed_call_count) over all recorded calls."""
    q = select(ApiCall.kind, func.count(ApiCall.id),
               func.coalesce(func.sum(ApiCall.est_cost_usd), 0.0)).group_by(ApiCall.kind)
    by_kind = {k: (int(n), float(c)) for k, n, c in (await s.execute(q)).all()}
    failed = (await s.execute(
        select(func.count(ApiCall.id)).where(ApiCall.ok.is_(False)))).scalar_one()
    return by_kind, int(failed)
