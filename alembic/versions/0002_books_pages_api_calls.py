"""users: picture, last_login_at; books, book_pages, api_calls

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 00:00:00

Written for PostgreSQL (LargeBinary -> bytea, JSON -> json); also runs on SQLite
(used by the test suite to check the migration matches the models).
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("picture", sa.String(2048), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "books",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("detail", sa.Integer(), nullable=False),
        sa.Column("hero_name", sa.String(80), nullable=False),
        sa.Column("pronouns", sa.String(16), nullable=False),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("favourites", sa.JSON(), nullable=False),
        sa.Column("about", sa.Text(), nullable=False),
        sa.Column("family_variant", sa.String(32), nullable=False),
        sa.Column("page_numbers", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("est_cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pdf", sa.LargeBinary(), nullable=True),
        sa.Column("pdf_filename", sa.String(120), nullable=False),
        sa.Column("hero_png", sa.LargeBinary(), nullable=True),
        sa.Column("hero_thumb_jpeg", sa.LargeBinary(), nullable=True),
        sa.Column("photo_jpeg", sa.LargeBinary(), nullable=True),
    )
    op.create_index("ix_books_user_created", "books", ["user_id", "created_at"])
    op.create_index("ix_books_status", "books", ["status"])

    op.create_table(
        "book_pages",
        sa.Column("book_id", sa.String(32),
                  sa.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("layout", sa.String(64), nullable=False),
        sa.Column("zone", sa.JSON(), nullable=False),
        sa.Column("png", sa.LargeBinary(), nullable=True),
        sa.Column("thumb_jpeg", sa.LargeBinary(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )

    op.create_table(
        "api_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("book_id", sa.String(32),
                  sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("est_cost_usd", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_api_calls_book", "api_calls", ["book_id"])
    op.create_index("ix_api_calls_user_created", "api_calls", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_api_calls_user_created", table_name="api_calls")
    op.drop_index("ix_api_calls_book", table_name="api_calls")
    op.drop_table("api_calls")
    op.drop_table("book_pages")
    op.drop_index("ix_books_status", table_name="books")
    op.drop_index("ix_books_user_created", table_name="books")
    op.drop_table("books")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "picture")
