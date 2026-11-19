"""quality tier per book: books.tier

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11 00:00:00

Existing books were all made with the Pro image model, so the server default labels them
``professional``. New books always store their tier explicitly.
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("tier", sa.String(20), nullable=False,
                                     server_default="professional"))


def downgrade() -> None:
    op.drop_column("books", "tier")
