"""billing: wallet_entries (ledger), refund_requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09 00:00:00

Money is stored as integer cents. Written for PostgreSQL; also runs on SQLite.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallet_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after_cents", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("book_id", sa.String(32), nullable=True),
        sa.Column("topup_id", sa.Integer(), sa.ForeignKey("wallet_entries.id"), nullable=True),
        sa.Column("charge_entry_id", sa.Integer(), nullable=True),
        sa.Column("stripe_checkout_session_id", sa.String(255), nullable=True, unique=True),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("stripe_refund_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_wallet_entries_user_id", "wallet_entries", ["user_id", "id"])
    op.create_index("ix_wallet_entries_book", "wallet_entries", ["book_id"])

    op.create_table(
        "refund_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topup_entry_id", sa.Integer(),
                  sa.ForeignKey("wallet_entries.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("admin_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=False),
        sa.Column("stripe_refund_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refund_requests_user", "refund_requests", ["user_id"])
    op.create_index("ix_refund_requests_status", "refund_requests", ["status"])
    # One pending request per top-up (partial unique index; PostgreSQL and SQLite).
    op.create_index(
        "uq_refund_pending_per_topup", "refund_requests", ["topup_entry_id"], unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_refund_pending_per_topup", table_name="refund_requests")
    op.drop_index("ix_refund_requests_status", table_name="refund_requests")
    op.drop_index("ix_refund_requests_user", table_name="refund_requests")
    op.drop_table("refund_requests")
    op.drop_index("ix_wallet_entries_book", table_name="wallet_entries")
    op.drop_index("ix_wallet_entries_user_id", table_name="wallet_entries")
    op.drop_table("wallet_entries")
