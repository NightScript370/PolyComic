import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.config import Settings, get_settings, normalise_database_url
from app.db.models import Base


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql://u:p@h/d?sslmode=require", "postgresql+asyncpg://u:p@h/d?sslmode=require"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("  postgres://u@h/d \n", "postgresql+asyncpg://u@h/d"),
        ("sqlite+aiosqlite:///x.db", "sqlite+aiosqlite:///x.db"),
    ],
)
def test_normalise_database_url(url, expected):
    assert normalise_database_url(url) == expected


def test_settings_normalise_env_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.render.example/polycomic")
    assert Settings().database_url == "postgresql+asyncpg://u:p@db.render.example/polycomic"


@pytest.mark.parametrize(("raw", "pages"), [("1,2,3", (1, 2, 3)), (" 4, 20 ", (4, 20))])
def test_book_pages(raw, pages):
    assert Settings(book_pages=raw).book_page_numbers() == pages


@pytest.mark.parametrize("raw", ["", "a,b", "1,1"])
def test_book_pages_invalid(raw):
    with pytest.raises(ValueError):
        Settings(book_pages=raw).book_page_numbers()


def test_alembic_migrations_match_models(tmp_path, monkeypatch):
    """Run 0001+0002 on SQLite and diff against the ORM metadata. (Postgres itself is not
    exercised here; the migration only uses portable types.)"""
    db = (tmp_path / "mig.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        assert {"users", "books", "book_pages", "api_calls"} <= tables
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff

    command.downgrade(cfg, "0001")
    with engine.connect() as conn:
        assert "books" not in inspect(conn).get_table_names()
        assert "picture" not in {c["name"] for c in inspect(conn).get_columns("users")}
    engine.dispose()
