from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings

TABLES = {"character_bible", "scene_plates", "page_attempts"}


def test_migration_0004_upgrade_downgrade(tmp_path, monkeypatch):
    db = (tmp_path / "m4.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    engine = create_engine(f"sqlite:///{db}")

    command.upgrade(cfg, "0003")
    with engine.connect() as conn:
        assert not TABLES & set(inspect(conn).get_table_names())
    command.upgrade(cfg, "0004")
    with engine.connect() as conn:
        insp = inspect(conn)
        assert TABLES <= set(insp.get_table_names())
        idx = {i["name"]: i for i in insp.get_indexes("character_bible")}
        assert idx["uq_character_bible_key"]["unique"]
        assert {i["name"] for i in insp.get_indexes("page_attempts")} == {
            "ix_page_attempts_book_page"}
    command.downgrade(cfg, "0003")
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        assert not TABLES & tables and "wallet_entries" in tables
    command.upgrade(cfg, "head")
    engine.dispose()
