"""speaker portraits: character_portraits, book_pages.portrait(_png),
page_attempts.visible_characters

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16 00:00:00

A page whose speaker is NOT in its picture (the grader says so) shows a small portrait
of the speaker in its dialogue box (app.pipeline.portraits).

* ``character_portraits``: a supporting character's portrait per story x style preset x
  costume x expression, made lazily by the first book that needs it and reused by every
  later book (unique key; inserts are ON CONFLICT DO NOTHING).
* ``book_pages.portrait`` (JSON: character, costume, expression) and
  ``book_pages.portrait_png``: the portrait a page shows (NULL = none).
* ``page_attempts.visible_characters`` (JSON): the character names the grader saw in the
  attempt's picture (portrait pages only; NULL = not asked / unknown).

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

KEY = ["story", "character_id", "costume_id", "expression", "preset"]


def upgrade() -> None:
    op.create_table(
        "character_portraits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story", sa.String(16), nullable=False),
        sa.Column("character_id", sa.String(48), nullable=False),
        sa.Column("costume_id", sa.String(32), nullable=False),
        sa.Column("expression", sa.String(16), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("png", sa.LargeBinary(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("uq_character_portraits_key", "character_portraits", KEY, unique=True)
    op.add_column("book_pages", sa.Column("portrait", sa.JSON(), nullable=True))
    op.add_column("book_pages", sa.Column("portrait_png", sa.LargeBinary(), nullable=True))
    op.add_column("page_attempts", sa.Column("visible_characters", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page_attempts") as batch:
        batch.drop_column("visible_characters")
    with op.batch_alter_table("book_pages") as batch:
        batch.drop_column("portrait_png")
        batch.drop_column("portrait")
    op.drop_index("uq_character_portraits_key", table_name="character_portraits")
    op.drop_table("character_portraits")
