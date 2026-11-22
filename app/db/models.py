"""ORM models. Types are portable (Postgres in production, SQLite in tests).

Large binaries (PNG/JPEG/PDF) live in ``LargeBinary`` columns (``bytea`` on Postgres).
They are never loaded by listing queries: ``app.db.repo`` selects them explicitly.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Book.status values
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
ACTIVE_STATUSES = (QUEUED, RUNNING)


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        Index("ix_books_user_created", "user_id", "created_at"),
        Index("ix_books_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    story: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    preset: Mapped[str] = mapped_column(String(32))
    detail: Mapped[int] = mapped_column(Integer)
    hero_name: Mapped[str] = mapped_column(String(80))
    pronouns: Mapped[str] = mapped_column(String(16))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favourites: Mapped[dict] = mapped_column(JSON, default=dict)
    about: Mapped[str] = mapped_column(Text, default="")
    family_variant: Mapped[str] = mapped_column(String(32), default="")
    # Quality tier (app.tiers): professional | cost_efficient. Rows from before tiers were
    # all made with the Pro model, hence the server default (migration 0006).
    tier: Mapped[str] = mapped_column(String(20), server_default="professional")
    page_numbers: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=QUEUED)
    stage: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    pdf_filename: Mapped[str] = mapped_column(String(120), default="")
    hero_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    hero_thumb_jpeg: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True,
                                                          deferred=True)
    # Normalised upload (EXIF-stripped JPEG, <=1536px) kept so a book can be regenerated.
    # Privacy: this is a child's photo - deleted with the book; see to-do.md (retention).
    photo_jpeg: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    # Per-book family reference sheet (Mother / Grandma in the book's family variant), a
    # reference for the pages; kept for inspection, not needed to rebuild the snapshot/PDF.
    family_sheet_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True,
                                                           deferred=True)
    # Optional calls the per-book cost cap skipped (migration 0011); NULL = none.
    cost_skips: Mapped[list | None] = mapped_column(JSON, nullable=True)


class BookPage(Base):
    __tablename__ = "book_pages"

    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    page: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(200))
    caption: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    layout: Mapped[str] = mapped_column(String(64))
    zone: Mapped[dict] = mapped_column(JSON)
    png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    thumb_jpeg: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=DONE)
    # Speaker portrait shown in the page's dialogue box (migration 0010): only when the
    # grader confirmed the speaker is not in the picture. ``portrait`` says which one
    # ({character, costume, expression}); NULL = no portrait.
    portrait: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    portrait_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True,
                                                       deferred=True)


class ApiCall(Base):
    """One upstream provider call (successful, failed or cancelled). Cost is an estimate."""

    __tablename__ = "api_calls"
    __table_args__ = (
        Index("ix_api_calls_book", "book_id"),
        Index("ix_api_calls_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(32))  # see app.pipeline.calls.CALL_KINDS
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


# --- billing (integer cents, never floats) ----------------------------------------------

# WalletEntry.kind values
TOPUP, CHARGE, RECREDIT, REFUND = "topup", "charge", "recredit", "refund"
# Wallet credit for one finished book, approved by an admin (migration 0013): no card refund.
BOOK_REFUND = "book_refund"
# RefundRequest.status values
R_PENDING, R_APPROVED, R_REJECTED, R_FAILED = "pending", "approved", "rejected", "failed"
# RefundRequest.kind values: a top-up returned to the card, or one book's charge returned
# to the wallet.
RK_TOPUP, RK_BOOK = "topup", "book"


class WalletEntry(Base):
    """Append-only ledger: balance = sum(amount_cents). ``balance_after_cents`` is written
    in the same transaction, under a per-user lock (see app.billing.wallet)."""

    __tablename__ = "wallet_entries"
    __table_args__ = (
        Index("ix_wallet_entries_user_id", "user_id", "id"),
        Index("ix_wallet_entries_book", "book_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    amount_cents: Mapped[int] = mapped_column(Integer)  # +credit / -debit
    balance_after_cents: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(300))
    book_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topup_id: Mapped[int | None] = mapped_column(ForeignKey("wallet_entries.id"), nullable=True)
    # recredit entries: the charge entry being reversed (idempotency key).
    charge_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True,
                                                                    nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class RefundRequest(Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        Index("ix_refund_requests_user", "user_id"),
        Index("ix_refund_requests_status", "status"),
        Index("uq_refund_pending_per_topup", "topup_entry_id", unique=True,
              postgresql_where=text("status = 'pending'"),
              sqlite_where=text("status = 'pending'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # kind "topup": the top-up entry. kind "book": the book's CHARGE entry.
    topup_entry_id: Mapped[int] = mapped_column(ForeignKey("wallet_entries.id"))
    kind: Mapped[str] = mapped_column(String(16), default=RK_TOPUP, server_default=RK_TOPUP)
    book_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=R_PENDING)
    admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    stripe_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- generation assets + quality loop (M4b; migration 0004) -----------------------------
# Owned by the pipeline. Repository functions live in app.db.repo_assets.


class CharacterBible(Base):
    """Character reference sheet per story x style preset: pre-generated
    (scripts/pregenerate_assets.py) or, for supporting characters, generated lazily by the
    first book that needs it (``app.pipeline.cast_sheets``) and reused by later books.

    ``variant_id`` is the family variant (``fam_a``..) for family characters and ``""``
    for everyone else; ``costume_id`` is the costume shown (``""`` = plain; migration
    0009). Empty strings, not NULL, so the unique index really is unique."""

    __tablename__ = "character_bible"
    __table_args__ = (
        Index("uq_character_bible_key", "story", "character_id", "variant_id", "costume_id",
              "preset", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    story: Mapped[str] = mapped_column(String(16))
    character_id: Mapped[str] = mapped_column(String(48))
    variant_id: Mapped[str] = mapped_column(String(32), default="")
    costume_id: Mapped[str] = mapped_column(String(32), default="", server_default="")
    preset: Mapped[str] = mapped_column(String(32))
    png: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class CharacterPortrait(Base):
    """Speaker portrait of a supporting character per story x style preset x costume x
    expression (head and shoulders, drawn from the character's reference sheet), made
    lazily by the first book that needs it (``app.pipeline.portraits``) and reused by
    every later book. Hero and family portraits are per book and never stored here.
    Empty strings, not NULL, so the unique index really is unique (migration 0010)."""

    __tablename__ = "character_portraits"
    __table_args__ = (
        Index("uq_character_portraits_key", "story", "character_id", "costume_id",
              "expression", "preset", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    story: Mapped[str] = mapped_column(String(16))
    character_id: Mapped[str] = mapped_column(String(48))
    costume_id: Mapped[str] = mapped_column(String(32), default="")
    expression: Mapped[str] = mapped_column(String(16))
    preset: Mapped[str] = mapped_column(String(32))
    png: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class ScenePlate(Base):
    """Pre-generated background plate for one story page (no hero, no family members)."""

    __tablename__ = "scene_plates"
    __table_args__ = (
        Index("uq_scene_plates_key", "story", "page", "preset", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    story: Mapped[str] = mapped_column(String(16))
    page: Mapped[int] = mapped_column(Integer)
    preset: Mapped[str] = mapped_column(String(32))
    png: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class PageAttempt(Base):
    """One generation of one picture in the quality loop (page 0 = hero).

    Only a thumbnail is kept per attempt; the chosen attempt's full PNG is in
    ``book_pages.png`` (or ``books.hero_png``)."""

    __tablename__ = "page_attempts"
    __table_args__ = (Index("ix_page_attempts_book_page", "book_id", "page"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    thumb_jpeg: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    likeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    fidelity: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_text_artifacts: Mapped[bool] = mapped_column(Boolean, default=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    zone: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set on the chosen attempt when QUALITY_TIME_BUDGET_S stopped further generations.
    budget_stopped: Mapped[bool] = mapped_column(Boolean, default=False,
                                                 server_default=false())
    # Set on the chosen attempt when the tier's per-book cost cap stopped further
    # generations (migration 0011).
    cost_stopped: Mapped[bool] = mapped_column(Boolean, default=False,
                                               server_default=false())
    # The attempt's picture is a FREE STRIP RESCUE of its generation (migration 0012): the
    # padded strip cropped away, re-cropped to 3:4 and graded - no extra image call.
    rescued: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Speaker-portrait pages only (migration 0010): the character names the grader saw
    # in this attempt's picture; NULL = not asked or unknown.
    visible_characters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())
