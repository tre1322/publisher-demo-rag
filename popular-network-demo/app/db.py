"""SQLite engine + session factory for the Popular Network dashboard.

The schema is created idempotently at startup via `init_db()`. We also include
a defensive `_add_col_if_missing` helper for forward migrations on a Railway
persistent-volume style deploy — earlier deploys can leave tables that need
new columns, and we don't want a code rollback to be wedged by schema drift.

Phase H.1 — tenant isolation belt-and-suspenders:
A `do_orm_execute` event listener auto-injects `WHERE business_id = ?` for
any SELECT against a model that has a `business_id` column, based on a
context-local tenant id set by RequireBusinessMiddleware. Opt out by passing
`execution_options(include_all_tenants=True)` on the query.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, with_loader_criteria
from sqlalchemy import event

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


# ---------------------------------------------------------------------------
# Tenant-isolation auto-filter (Phase H.1)
# ---------------------------------------------------------------------------
# `current_tenant_id` is a context-local int set by RequireBusinessMiddleware
# at the start of each request. The `do_orm_execute` listener below reads it
# on every ORM SELECT and auto-appends `WHERE business_id = ?` for any mapped
# entity whose class has a `business_id` attribute.
#
# Opt out via session.execute(stmt.execution_options(include_all_tenants=True)).
# Superuser admin paths use the opt-out; everything else stays scoped.
#
# Gotcha: this only filters ORM-style execute (Query / select()). Raw
# session.execute(text("SELECT ..."))  is NOT filtered — keep raw SQL rare
# and audit it explicitly.

current_tenant_id: ContextVar[Optional[int]] = ContextVar("current_tenant_id", default=None)


@event.listens_for(SessionLocal, "do_orm_execute")
def _tenant_filter(state):  # type: ignore[no-untyped-def]
    if state.is_select and not state.execution_options.get("include_all_tenants", False):
        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return  # no active tenant — let the query run (e.g. login endpoint reads User)
        # Apply WHERE business_id = ? to every mapped entity in the statement
        # that actually has a business_id column.
        for mapper in state.bind_mapper.self_and_descendants if state.bind_mapper else []:
            if "business_id" in mapper.attrs:
                state.statement = state.statement.options(
                    with_loader_criteria(
                        mapper.class_,
                        lambda cls: cls.business_id == tenant_id,
                        include_aliases=True,
                    )
                )
