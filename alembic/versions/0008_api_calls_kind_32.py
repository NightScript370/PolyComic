"""api_calls.kind: VARCHAR(16) -> VARCHAR(32)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-16 00:00:00

``family_sheet_check`` (18 chars) did not fit VARCHAR(16) on Postgres. Postgres rejects
values longer than the column; SQLite (tests) does not, which hid the bug.
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_calls") as batch:
        batch.alter_column("kind", existing_type=sa.String(16), type_=sa.String(32),
                           existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("api_calls") as batch:
        batch.alter_column("kind", existing_type=sa.String(32), type_=sa.String(16),
                           existing_nullable=False)
