"""generation assets + quality loop: character_bible, scene_plates, page_attempts

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09 00:00:00

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_bible",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story", sa.String(16), nullable=False),
        sa.Column("character_id", sa.String(48), nullable=False),
        sa.Column("variant_id", sa.String(32), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("png", sa.LargeBinary(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("uq_character_bible_key", "character_bible",
                    ["story", "character_id", "variant_id", "preset"], unique=True)

    op.create_table(
        "scene_plates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story", sa.String(16), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("png", sa.LargeBinary(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("uq_scene_plates_key", "scene_plates", ["story", "page", "preset"],
                    unique=True)

    op.create_table(
        "page_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("thumb_jpeg", sa.LargeBinary(), nullable=True),
        sa.Column("likeness", sa.Float(), nullable=True),
        sa.Column("fidelity", sa.Float(), nullable=True),
        sa.Column("has_text_artifacts", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("zone", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chosen", sa.Boolean(), nullable=False),
        sa.Column("est_cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_page_attempts_book_page", "page_attempts", ["book_id", "page"])


def downgrade() -> None:
    op.drop_index("ix_page_attempts_book_page", table_name="page_attempts")
    op.drop_table("page_attempts")
    op.drop_index("uq_scene_plates_key", table_name="scene_plates")
    op.drop_table("scene_plates")
    op.drop_index("uq_character_bible_key", table_name="character_bible")
    op.drop_table("character_bible")
