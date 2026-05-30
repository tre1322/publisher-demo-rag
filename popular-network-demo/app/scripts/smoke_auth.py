"""End-to-end auth smoke — Phase H.1.

Exercises the real HTTP path with FastAPI TestClient:

1. App boots, first-user-as-superuser bootstrap works
2. Login mints a session cookie
3. /me returns the right user + capabilities
4. Logout invalidates the cookie
5. /api/* without a cookie → 401
6. Login throttle: 6 failed attempts → 429
7. Tenant isolation: user A logged into business_id=1 cannot read business_id=2's
   posts (this is the primary defense the SQLAlchemy auto-filter protects).

Run with:  uv run python -m app.scripts.smoke_auth
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

# Reconfigure stdout for utf-8 so unicode arrows in check() labels don't
# crash on Windows cp1252 consoles. Same pattern as the other smokes.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# Isolate DB before importing the app — the engine binds to POPULAR_DB_PATH at import.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["POPULAR_DB_PATH"] = _TMP.name

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Business, BusinessUser, Post, User  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append((label, detail))
        print(f"  FAIL  {label}  — {detail}")


def main() -> int:
    print("smoke_auth — Phase H.1 end-to-end\n")

    # TestClient as context manager so startup events fire (seeding etc).
    with TestClient(app) as client:
        # --- 1. Bootstrap superuser register (zero users in DB) ---
        r = client.post(
            "/api/auth/register",
            json={"email": "trevor@example.com", "password": "hunter2-correct-horse"},
        )
        check("1. bootstrap register → 200", r.status_code == 200, r.text)
        check("1. bootstrap user is_superuser", r.json().get("is_superuser") is True, r.text)
        check("1. bootstrap flag set", r.json().get("bootstrapped") is True, r.text)

        # --- 2. Second register without auth → 403 ---
        r = client.post(
            "/api/auth/register",
            json={"email": "rando@example.com", "password": "anotherpass1234"},
        )
        check("2. non-bootstrap register without auth → 403", r.status_code == 403, r.text)

        # --- 3. Login as the bootstrap superuser ---
        # Seeded business_id=1 is Quadd; we need a second business for isolation later.
        with SessionLocal() as db:
            biz_a = db.query(Business).filter(Business.id == 1).first()
            check("3. seed business_id=1 exists", biz_a is not None, "no seeded biz")
            # Make a second business
            biz_b = Business(
                slug="other-biz", name="Other Biz", owner="Someone", owner_initials="SO",
                location="Marshall", publisher="Other Pub", phone="555-9999",
                tier=2, tier_label="Tier 2", monthly_price=75,
                joined_days_ago=0, joined_date="2026-05-27", voice_interview="no",
            )
            db.add(biz_b); db.flush()
            # Trevor is owner of both businesses (so we can prove /switch works);
            # but isolation test below needs a SEPARATE user who only owns biz_b.
            user_a = db.query(User).filter(User.email == "trevor@example.com").first()
            assert user_a is not None
            db.add(BusinessUser(user_id=user_a.id, business_id=1, role="owner"))
            db.add(BusinessUser(user_id=user_a.id, business_id=biz_b.id, role="owner"))
            # Create a post owned by biz_b
            db.add(Post(
                business_id=biz_b.id, date="2026-05-27", platform="fb",
                status="draft", title="biz_b only", draft="should not leak",
            ))
            db.commit()
            biz_b_id = biz_b.id

        r = client.post(
            "/api/auth/login",
            json={"email": "trevor@example.com", "password": "hunter2-correct-horse"},
        )
        check("3. login → 200", r.status_code == 200, r.text)
        check("3. session cookie set", "popular_session" in client.cookies, str(client.cookies))

        # --- 4. /me returns user + active business ---
        r = client.get("/api/auth/me")
        check("4. /me → 200", r.status_code == 200, r.text)
        me = r.json()
        check("4. /me email correct", me.get("email") == "trevor@example.com", str(me))
        check("4. /me is_superuser=True", me.get("is_superuser") is True, str(me))
        check("4. /me capabilities present", "capabilities" in me, str(me))

        # --- 5. /businesses lists both ---
        r = client.get("/api/auth/businesses")
        check("5. /businesses → 200", r.status_code == 200, r.text)
        bizes = r.json().get("businesses", [])
        check("5. /businesses returns 2 rows", len(bizes) == 2, str(bizes))

        # --- 6. /switch to biz_b ---
        r = client.post("/api/auth/switch", json={"business_id": biz_b_id})
        check("6. /switch → 200", r.status_code == 200, r.text)
        r = client.get("/api/auth/me")
        check("6. /me reflects new active_business_id",
              r.json().get("active_business_id") == biz_b_id, r.text)

        # --- 7. Logout ---
        r = client.post("/api/auth/logout")
        check("7. logout → 200", r.status_code == 200, r.text)

        # --- 8. /api/* without cookie → 401 ---
        client.cookies.clear()
        r = client.get("/api/auth/me")
        check("8. /me without cookie → 401", r.status_code == 401, r.text)
        r = client.get("/api/bootstrap")
        check("8. /api/bootstrap without cookie → 401", r.status_code == 401, r.text)

        # --- 9. Login throttle: 6 bad attempts → 429 on the 6th ---
        # Each wrong attempt records. After 5 fails the IP is blocked.
        last_status = None
        for i in range(7):
            r = client.post(
                "/api/auth/login",
                json={"email": "trevor@example.com", "password": "WRONG-WRONG"},
            )
            last_status = r.status_code
        check("9. login throttle eventually 429", last_status == 429, f"last={last_status}")

        # --- 10. Cross-tenant isolation ---
        # Make user_b who only owns biz_b. Then log in as user_b, try to query
        # the seeded business_id=1's data.
        with SessionLocal() as db:
            from app.pwhash import hash_password as hp
            user_b = User(email="other@example.com", password_hash=hp("anotherpass1234"),
                          is_superuser=False)
            db.add(user_b); db.flush()
            db.add(BusinessUser(user_id=user_b.id, business_id=biz_b_id, role="owner"))
            # Seed: business 1 has a post that user_b should NEVER see.
            db.add(Post(
                business_id=1, date="2026-05-27", platform="fb",
                status="draft", title="QUADD SECRET", draft="user_b must not see this",
            ))
            db.commit()

        # Clear throttle by spinning the clock would be hard — just use a fresh
        # client (new cookie jar). Throttle is per-IP, but TestClient sets the
        # same IP. Workaround: monkey-patch the throttle bucket.
        from app.routers.auth import _FAILS
        _FAILS.clear()

        client.cookies.clear()
        r = client.post(
            "/api/auth/login",
            json={"email": "other@example.com", "password": "anotherpass1234"},
        )
        check("10a. user_b login → 200", r.status_code == 200, r.text)

        # user_b is on biz_b. The bootstrap endpoint returns
        # business-scoped data; it MUST NOT include biz_a's posts/etc.
        r = client.get("/api/bootstrap")
        check("10b. user_b /bootstrap → 200", r.status_code == 200, r.text[:300])

        # The seeded business_id=1's "QUADD SECRET" post must not appear in
        # the response body when user_b is logged in.
        body = r.text
        check("10c. cross-tenant leak: QUADD SECRET absent for user_b",
              "QUADD SECRET" not in body,
              "BUSINESS_ID=1 POST CONTENT LEAKED INTO BUSINESS_ID=2 RESPONSE")

    print(f"\n{len(PASSED)} pass · {len(FAILED)} fail")
    for label, detail in FAILED:
        print(f"  FAIL: {label} — {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
