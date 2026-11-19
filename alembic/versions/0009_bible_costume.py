"""character_bible.costume_id: supporting-character sheets per costume

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-16 00:00:00

Supporting characters (the wolf, the giant, the hen ...) get a reference sheet per story x
style preset x costume (the wolf plain and in Grandma's nightcap and shawl), generated
lazily by the first book that needs one (app.pipeline.cast_sheets) and reused by every
later book. ``costume_id`` is "" for the plain look and for every existing row; the unique
key gains the column, so a concurrent insert of the same sheet is a no-op (ON CONFLICT DO
NOTHING) instead of an error.
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

OLD_KEY = ["story", "character_id", "variant_id", "preset"]
NEW_KEY = ["story", "character_id", "variant_id", "costume_id", "preset"]


def upgrade() -> None:
    with op.batch_alter_table("character_bible") as batch:
        batch.add_column(sa.Column("costume_id", sa.String(32), nullable=False,
                                   server_default=""))
        batch.drop_index("uq_character_bible_key")
        batch.create_index("uq_character_bible_key", NEW_KEY, unique=True)


def downgrade() -> None:
    # Costume sheets cannot be kept under the old key: drop them first.
    op.execute("DELETE FROM character_bible WHERE costume_id <> ''")
    with op.batch_alter_table("character_bible") as batch:
        batch.drop_index("uq_character_bible_key")
        batch.create_index("uq_character_bible_key", OLD_KEY, unique=True)
        batch.drop_column("costume_id")
