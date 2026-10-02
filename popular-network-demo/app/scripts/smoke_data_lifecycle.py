"""Phase 1c smoke — export and delete a business's data, completely and
only that business's.

Run with:  uv run python -m app.scripts.smoke_data_lifecycle

The privacy policy promises deletion 30 days after cancelling and a copy
on request. Covers:
  A. Admin export: everything the business owns, nothing of anyone else's,
     no credentials
  B. Owner self-service export; non-owners refused
  C. 30-day deletion clock: confirm-by-name, owner sees it, cancellable
  D. Clock runs out → every row gone; sole members deleted, shared members
     and Quadd untouched
  E. Delete-now path, and only superusers can do any of it
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_lifecycle_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"

OWNER = ("sole-owner@example.com", "sole-owner-correct-horse")
SHARED = ("shared-editor@example.com", "shared-editor-correct-horse")
VIEWER = ("lc-viewer@example.com", "lc-viewer-correct-horse")
SECRETS = ("password_hash", "token_hash", "key_hash", "oauth_token", "refresh_token")


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)  # hermetic; see smoke_money_path
    with TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as shared, \
         TestClient(app, follow_redirects=False) as viewer:
        bootstrap_login(admin)
        _run(admin, owner, shared, viewer)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 1c data-lifecycle smoke green ✓")


def _rows_for(db, business_id: int) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.db import Base
    out = {}
    for t in Base.metadata.sorted_tables:
        if "business_id" in t.columns:
            n = db.execute(select(func.count()).select_from(t).where(t.c.business_id == business_id)
                           .execution_options(include_all_tenants=True)).scalar()
            if n:
                out[t.name] = n
    return out


def _run(admin, owner, shared, viewer) -> None:  # noqa: C901 — linear smoke
    from app.data_lifecycle import purge_due
    from app.db import SessionLocal
    from app.models import Business, BusinessUser, PasswordReset, User, UserSession
    from app.provisioning import create_business
    from app.pwhash import hash_password

    # ---- setup: a real client with data + people ---------------------------------
    with SessionLocal() as db:
        biz = create_business(db, name="Lifecycle Hardware", owner="Sam Sole", location="Tracy, MN", tier=4)
        biz_id = biz.id
        users = {}
        for (email, pw), role, biz_ids in ((OWNER, "owner", [biz_id]), (SHARED, "editor", [biz_id, 1]),
                                           (VIEWER, "viewer", [biz_id])):
            u = User(email=email, password_hash=hash_password(pw), display_name=role, is_superuser=False, is_active=True)
            db.add(u)
            db.flush()
            users[email] = u.id
            for b in biz_ids:
                db.add(BusinessUser(user_id=u.id, business_id=b, role=role))
        db.commit()
    for client, (email, pw) in ((owner, OWNER), (shared, SHARED), (viewer, VIEWER)):
        r = client.post("/api/auth/login", json={"email": email, "password": pw, "active_business_id": biz_id})
        check(f"setup: {email} signs in", r.status_code == 200, r.text)
    owner.post("/api/posts", json={"platform": "fb", "status": "pending", "title": "Open house", "draft": "Saturday 9-2."})
    owner.post("/api/chatbot/keys", json={"label": "site relay"})
    owner.post("/api/inventory/import-fixture", json={"feed_type": "generic_csv", "csv_text": "title\nHammer\nSaw\n"})
    owner.post("/api/escalations", json={"message": "Please call me"})
    owner.post("/api/auth/invites", json={"email": "later@example.com", "role": "viewer"})
    with SessionLocal() as db:
        db.add(PasswordReset(user_id=users[OWNER[0]], token_hash="x" * 64, expires_at=datetime.utcnow()))
        db.commit()
        before = _rows_for(db, biz_id)
        quadd_before = _rows_for(db, 1)
    check("setup: client has rows in many tables",
          {"posts", "approvals", "chatbot_ingestion_keys", "inventory_listings", "escalations", "invites",
           "business_users", "settings", "marketing_plan"} <= set(before), sorted(before))

    # ---- A. admin export --------------------------------------------------------
    print("\nA. admin export")
    r = admin.get(f"/api/admin/businesses/{biz_id}/export")
    check("A1 export → 200 as a download", r.status_code == 200
          and "attachment" in r.headers.get("content-disposition", "") and "lifecycle_hardware" in r.headers["content-disposition"],
          (r.status_code, r.headers.get("content-disposition")))
    data = r.json()
    check("A2 business + people included", data["business"]["name"] == "Lifecycle Hardware"
          and {p["email"] for p in data["people"]} == {OWNER[0], SHARED[0], VIEWER[0]}, data["people"])
    check("A3 every owned table exported", {k: len(v) for k, v in data["tables"].items()} == before,
          ({k: len(v) for k, v in data["tables"].items()}, before))
    check("A4 nothing from another business",
          all(row["business_id"] == biz_id for rows in data["tables"].values() for row in rows))
    blob = json.dumps(data)
    check("A5 no credentials or token hashes", not any(f'"{s}"' in blob for s in SECRETS),
          [s for s in SECRETS if f'"{s}"' in blob])

    # ---- B. owner export ---------------------------------------------------------
    print("\nB. owner self-service export")
    r = owner.get("/api/account/export")
    check("B1 owner downloads their data", r.status_code == 200 and r.json()["business"]["id"] == biz_id, r.status_code)
    check("B2 same content as the admin export", {k: len(v) for k, v in r.json()["tables"].items()} == before)
    r = viewer.get("/api/account/export")
    check("B3 viewer can't", r.status_code == 403, r.status_code)
    r = shared.get("/api/account/export")
    check("B4 editor can't", r.status_code == 403, r.status_code)

    # ---- C. deletion clock -----------------------------------------------------
    print("\nC. 30-day deletion clock")
    r = admin.post(f"/api/admin/businesses/{biz_id}/schedule-deletion", json={"confirm_name": "lifecycle hardware"})
    check("C1 wrong name → 422, nothing scheduled", r.status_code == 422, r.status_code)
    r = admin.post(f"/api/admin/businesses/{biz_id}/schedule-deletion", json={"confirm_name": "Lifecycle Hardware"})
    due = datetime.fromisoformat(r.json()["business"]["deletionDueAt"])
    check("C2 scheduled ~30 days out", r.status_code == 200 and timedelta(days=29, hours=23) < due - datetime.utcnow()
          <= timedelta(days=30), due)
    boot = owner.get("/api/bootstrap").json()
    check("C3 owner's dashboard knows the date", boot["business"]["deletionDueAt"] == r.json()["business"]["deletionDueAt"])
    with SessionLocal() as db:
        check("C4 not due yet → nothing purged", purge_due(db) == [] and db.get(Business, biz_id) is not None)
    r = admin.post(f"/api/admin/businesses/{biz_id}/cancel-deletion")
    check("C5 cancel clears it", r.json()["business"]["deletionDueAt"] is None)
    with SessionLocal() as db:
        check("C6 cancelled → never due", purge_due(db, now=datetime.utcnow() + timedelta(days=60)) == [])
    admin.post(f"/api/admin/businesses/{biz_id}/schedule-deletion", json={"confirm_name": "Lifecycle Hardware"})

    # ---- D. the clock runs out -------------------------------------------------
    print("\nD. deletion runs when due")
    with SessionLocal() as db:
        purged = purge_due(db, now=datetime.utcnow() + timedelta(days=31))
    check("D1 purge_due deletes it", purged == [biz_id], purged)
    with SessionLocal() as db:
        left = _rows_for(db, biz_id)
        check("D2 no row of it left in any table", left == {}, left)
        check("D3 business row gone", db.get(Business, biz_id) is None)
        check("D4 sole owner + viewer accounts deleted",
              db.query(User).filter(User.email.in_([OWNER[0], VIEWER[0]])).count() == 0)
        check("D5 their sessions + reset links gone",
              db.query(UserSession).filter(UserSession.user_id.in_([users[OWNER[0]], users[VIEWER[0]]])).count() == 0
              and db.query(PasswordReset).filter(PasswordReset.user_id == users[OWNER[0]]).count() == 0)
        shared_user = db.query(User).filter(User.email == SHARED[0]).one_or_none()
        check("D6 shared editor kept, still on Quadd", shared_user is not None and
              db.query(BusinessUser).filter(BusinessUser.user_id == shared_user.id).all()[0].business_id == 1)
        check("D7 no session still points at the deleted business",
              db.query(UserSession).filter(UserSession.active_business_id == biz_id).count() == 0)
        check("D8 Quadd untouched", _rows_for(db, 1) == quadd_before, (_rows_for(db, 1), quadd_before))
        check("D9 superuser kept", db.query(User).filter(User.is_superuser.is_(True)).count() == 1)
    r = owner.get("/api/bootstrap")
    check("D10 the deleted owner's session is dead", r.status_code == 401, r.status_code)
    r = shared.get("/api/bootstrap")
    check("D11 shared editor lands on their remaining business", r.status_code == 200
          and r.json()["business"]["id"] == 1 and r.json()["access"]["role"] == "editor", r.status_code)

    # ---- E. delete now + permissions -------------------------------------------
    print("\nE. delete now; superusers only")
    with SessionLocal() as db:
        other = create_business(db, name="Gone Today", owner="X", tier=2)
        db.commit()
        other_id = other.id
    r = shared.post(f"/api/admin/businesses/{other_id}/delete-now", json={"confirm_name": "Gone Today"})
    check("E1 non-superuser can't delete", r.status_code == 403, r.status_code)
    r = shared.get(f"/api/admin/businesses/{other_id}/export")
    check("E2 non-superuser can't export another business", r.status_code == 403, r.status_code)
    r = admin.post(f"/api/admin/businesses/{other_id}/delete-now", json={"confirm_name": "gone"})
    check("E3 wrong name → 422", r.status_code == 422, r.status_code)
    r = admin.post(f"/api/admin/businesses/{other_id}/delete-now", json={"confirm_name": "Gone Today"})
    check("E4 delete now → 200 with counts", r.status_code == 200 and r.json()["deleted"]["businesses"] == 1, r.text[:200])
    names = [b["name"] for b in admin.get("/api/admin/businesses").json()["businesses"]]
    check("E5 gone from the admin list", "Gone Today" not in names and "Lifecycle Hardware" not in names, names)
    r = admin.post(f"/api/admin/businesses/{other_id}/open")
    check("E6 can't open a deleted business", r.status_code == 404, r.status_code)
    check("E7 admin + Quadd still fine", admin.get("/api/bootstrap").status_code == 200)


if __name__ == "__main__":
    main()
