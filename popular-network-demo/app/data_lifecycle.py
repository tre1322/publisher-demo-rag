"""Export and delete a business's data (Phase 1).

The privacy policy promises a client's data is deleted 30 days after they
cancel, and that they can have a copy. This module is the one place that
knows how:

  export_business(db, id)   every row the business owns, as JSON-ready dicts
  schedule_deletion(...)    start the 30-day clock (cancellable)
  purge_business(db, id)    delete it all now; returns per-table counts
  purge_due(db)             purge every business whose clock has run out

Tables are found from the schema, not a hand-kept list: anything with a
`business_id` column belongs to the business, so a table added later is
covered automatically (smoke_data_lifecycle checks no row survives).

People: a user who belonged only to this business is deleted with it
(with their sessions and reset links). Users who also belong to another
business, and Amplafai superusers, are kept; only their membership goes.

Not covered here: copies inside backups. Those age out on the backup
rotation, which the privacy policy should say.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Table, delete, select, update
from sqlalchemy.orm import Session

from .db import Base
from .models import Business, BusinessUser, PasswordReset, User, UserSession

log = logging.getLogger("popular_network.data_lifecycle")

DELETION_GRACE = timedelta(days=30)

# Never written into an export: credentials and token material.
_SECRET_COLUMNS = frozenset({
    "password_hash", "token_hash", "key_hash", "oauth_token", "refresh_token", "oauth_state",
    "posting_profile_key",
})


def _business_tables() -> list[Table]:
    """Every table with a business_id column, children before parents."""
    return [t for t in reversed(Base.metadata.sorted_tables) if "business_id" in t.columns]


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row_dict(row: Any, columns) -> dict[str, Any]:
    return {c.name: _jsonable(row._mapping[c]) for c in columns if c.name not in _SECRET_COLUMNS}


def export_business(db: Session, business_id: int) -> dict[str, Any]:
    biz_table = Base.metadata.tables["businesses"]
    biz_row = db.execute(select(biz_table).where(biz_table.c.id == business_id)).first()
    if biz_row is None:
        raise LookupError(f"business {business_id} not found")
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in sorted(_business_tables(), key=lambda t: t.name):
        rows = db.execute(
            select(table).where(table.c.business_id == business_id)
            .execution_options(include_all_tenants=True)
        ).all()
        if rows:
            tables[table.name] = [_row_dict(r, table.columns) for r in rows]
    members = (
        db.query(BusinessUser, User)
        .join(User, User.id == BusinessUser.user_id)
        .filter(BusinessUser.business_id == business_id)
        .all()
    )
    return {
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "format": "amplafai-business-export/1",
        "business": _row_dict(biz_row, biz_table.columns),
        "people": [
            {"email": u.email, "name": u.display_name, "role": bu.role,
             "joinedAt": _jsonable(bu.accepted_at)}
            for bu, u in members
        ],
        "tables": tables,
    }


def schedule_deletion(db: Session, biz: Business, *, now: datetime | None = None) -> Business:
    now = now or datetime.utcnow()
    biz.deletion_requested_at = now
    biz.deletion_due_at = now + DELETION_GRACE
    return biz


def cancel_deletion(biz: Business) -> Business:
    biz.deletion_requested_at = None
    biz.deletion_due_at = None
    return biz


def purge_business(db: Session, business_id: int) -> dict[str, int]:
    """Delete everything the business owns, plus people who only belonged
    to it. Commits. Returns {table: rows_deleted}."""
    if db.get(Business, business_id) is None:
        raise LookupError(f"business {business_id} not found")

    member_ids = [
        uid for (uid,) in db.query(BusinessUser.user_id).filter(BusinessUser.business_id == business_id).all()
    ]
    counts: dict[str, int] = {}
    for table in _business_tables():
        res = db.execute(delete(table).where(table.c.business_id == business_id))
        if res.rowcount:
            counts[table.name] = res.rowcount

    # Sessions that had this business open fall back to "no active business".
    db.execute(
        update(UserSession).where(UserSession.active_business_id == business_id).values(active_business_id=None)
    )

    # People with no other business (and not Amplafai operators) go too.
    removed_users = 0
    for uid in member_ids:
        user = db.get(User, uid)
        if user is None or user.is_superuser:
            continue
        if db.query(BusinessUser).filter(BusinessUser.user_id == uid).first() is not None:
            continue
        db.query(UserSession).filter(UserSession.user_id == uid).delete()
        db.query(PasswordReset).filter(PasswordReset.user_id == uid).delete()
        db.delete(user)
        removed_users += 1
    if removed_users:
        counts["users"] = removed_users

    db.execute(delete(Business.__table__).where(Business.__table__.c.id == business_id))
    counts["businesses"] = 1
    db.commit()
    log.info("Purged business_id=%s: %s", business_id, counts)
    return counts


def purge_due(db: Session, *, now: datetime | None = None) -> list[int]:
    now = now or datetime.utcnow()
    due = [
        b.id for b in db.query(Business)
        .filter(Business.deletion_due_at.is_not(None), Business.deletion_due_at <= now)
        .all()
    ]
    for business_id in due:
        purge_business(db, business_id)
    return due
