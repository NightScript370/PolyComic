"""per-book cost cap: page_attempts.cost_stopped, books.cost_skips

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16 12:00:00

* ``page_attempts.cost_stopped``: True on the kept attempt of a picture whose quality loop
  was stopped by the tier's per-book cost cap (COST_CAP_*_USD). Existing rows: false.
* ``books.cost_skips`` (JSON list of strings, NULL = none / older book): every optional
  call the cost cap skipped ("page 7 retry (soft issues)", "portrait p3 mother/angry").

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_attempts", sa.Column("cost_stopped", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))
    op.add_column("books", sa.Column("cost_skips", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("cost_skips")
    with op.batch_alter_table("page_attempts") as batch:
        batch.drop_column("cost_stopped")
