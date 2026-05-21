"""SQLite engine + session factory for the Popular Network dashboard.

The schema is created idempotently at startup via `init_db()`. We also include
a defensive `_add_col_if_missing` helper for forward migrations on a Railway
persistent-volume style deploy — earlier deploys can leave tables that need
new columns, and we don't want a code rollback to be wedged by schema drift.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Override via POPULAR_DB_PATH for isolated test runs. Default = dev DB.
DB_PATH = Path(os.environ.get("POPULAR_DB_PATH") or (DATA_DIR / "popular_network.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _add_col_if_missing(table: str, column: str, type_sql: str) -> None:
    """Add a column to an existing table if it isn't there yet.

    Wrap any forward-schema additions in this so a persistent SQLite file
    survives across code changes without manual ALTER TABLE.
    """
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns(table)}
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}"))


def init_db() -> None:
    """Create all tables that don't yet exist. Safe to call repeatedly."""
    from . import models  # noqa: F401  — ensure models are imported so metadata is populated

    Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency that yields a session and closes it on teardown."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
