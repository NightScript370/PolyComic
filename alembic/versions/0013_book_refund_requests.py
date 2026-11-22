"""per-book refund requests: refund_requests.kind + book_id

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-22 12:00:00

* ``refund_requests.kind``: ``topup`` (existing: unspent top-up back to the card) or
  ``book`` (one finished book's charge back to the wallet; ``topup_entry_id`` then holds
  the book's charge entry). Existing rows: ``topup``.
* ``refund_requests.book_id``: the book of a ``book`` request, else NULL.

Portable types only (PostgreSQL in production, SQLite in tests).
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refund_requests", sa.Column("kind", sa.String(16), nullable=False,
                                               server_default="topup"))
    op.add_column("refund_requests", sa.Column("book_id", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("refund_requests") as batch:
        batch.drop_column("book_id")
        batch.drop_column("kind")
