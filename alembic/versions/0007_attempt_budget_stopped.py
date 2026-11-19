"""quality-loop time budget: page_attempts.budget_stopped

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 00:00:00

True on the chosen attempt of a picture whose quality loop was stopped by
QUALITY_TIME_BUDGET_S (no further generation was started). Existing rows: false.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_attempts", sa.Column("budget_stopped", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("page_attempts") as batch:
        batch.drop_column("budget_stopped")
