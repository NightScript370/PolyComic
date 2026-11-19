"""per-book family reference sheet: books.family_sheet_png

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11 00:00:00

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("family_sheet_png", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("books", "family_sheet_png")
