"""free strip rescue: page_attempts.rescued

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16 18:00:00

* ``page_attempts.rescued``: True when the attempt's picture is a free strip rescue of its
  generation (a padded top / bottom / side strip cropped away, re-cropped to 3:4,
  upscaled and graded - no extra image call). Existing rows: false.

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_attempts", sa.Column("rescued", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("page_attempts") as batch:
        batch.drop_column("rescued")
