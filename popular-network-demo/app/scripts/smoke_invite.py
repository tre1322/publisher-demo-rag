"""H.1.6 smoke — invite flow.

Verifies:
  1. Bootstrap owner can mint an invite (POST /api/auth/invites)
  2. Listing returns it as 'pending'
  3. Public /lookup returns business + email + role preview
  4. Public /claim creates the new user + BusinessUser + session cookie
  5. New user is logged in and sees the dashboard
  6. Claimed invite shows status='accepted' in the list
  7. Re-claiming the same token → 404 (invite_not_found_or_invalid)
  8. Mint another invite, revoke it, /lookup → 404
  9. Mint invite for an email that's already a member → 409
 10. Editor role can't mint invites (can('manage_invites') is False)
 11. GET /invite (HTML page) is publicly reachable

Run with:  uv run python -m app.scripts.smoke_invite
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_invite_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BusinessUser, User  # noqa: E402
from app.scripts._auth_helper import bootstrap_login  # noqa: E402

# Keep this smoke hermetic — it must never touch the Postmark API.
#
# This pop MUST come after `import app.main` above, not before it: app.main
# calls load_dotenv(find_dotenv(usecwd=True), override=True), which walks UP
# out of this repo into the parent publisher-demo-rag/.env — and that file
# sets POSTMARK_API_KEY. override=True means anything we unset beforehand
# gets restored at import time.
#
# Without this, the smoke made a LIVE Postmark send attempt on every run and
# failed with postmark_422 (Postmark rejects the reserved example.com domain),
# which reads as a broken invite flow when the flow is actually fine.
# app/email.py reads the key via os.getenv at call time, so popping it here
# is enough to force the no_api_key branch.
os.environ.pop("POSTMARK_API_KEY", None)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


def main() -> int:
    print("smoke_invite -- Phase H.1.6 invite flow\n")

    with TestClient(app, follow_redirects=False) as client:
        # Set up the bootstrap owner (Quadd, business_id=1).
        bootstrap_login(client)

        # 1. Mint an invite for a NEW email as 'editor' role.
        r = client.post("/api/auth/invites", json={
            "email": "newhire@example.com",
            "role": "editor",
        })
        check("1. mint invite -> 200", r.status_code == 200, r.text)
        minted = r.json()
        for k in ("id", "email", "role", "tokenPrefix", "rawToken", "claimUrl", "expiresAt", "emailDelivery"):
            check(f"1. minted has {k}", k in minted, str(minted)[:200])
        raw_token = minted.get("rawToken", "")
        check("1. claimUrl points at /invite", minted.get("claimUrl", "").startswith("/invite?token="), minted.get("claimUrl"))
        check("1. tokenPrefix matches first 12 of raw", minted.get("tokenPrefix") == raw_token[:12], "")
        # Smoke runs with POSTMARK_API_KEY unset → email skipped, no_api_key reason.
        delivery = minted.get("emailDelivery") or {}
        check("1. email skipped without POSTMARK_API_KEY", delivery.get("sent") is False, str(delivery))
        check("1. email skip reason is no_api_key", delivery.get("reason") == "no_api_key", str(delivery))

        # 2. List shows it as pending.
        r = client.get("/api/auth/invites")
        check("2. list -> 200", r.status_code == 200, r.text)
        invs = r.json().get("invites", [])
        check("2. list has exactly 1 invite", len(invs) == 1, str(invs))
        check("2. that invite is pending", invs and invs[0].get("status") == "pending", str(invs))

        # 3. Public lookup (no auth) shows preview.
        # Use a separate client so we know the cookie isn't carrying us.
        with TestClient(app, follow_redirects=False) as anon:
            r = anon.get(f"/api/auth/invites/lookup?token={raw_token}")
            check("3. anon lookup -> 200", r.status_code == 200, r.text)
            preview = r.json()
            check("3. preview has businessName", preview.get("businessName") == "Quadd.ai", str(preview))
            check("3. preview email matches", preview.get("email") == "newhire@example.com", str(preview))
            check("3. preview role matches", preview.get("role") == "editor", str(preview))

            # 4. Claim (anon)
            r = anon.post("/api/auth/invites/claim", json={
                "token": raw_token,
                "password": "claim-pw-correct-horse",
                "display_name": "New Hire",
            })
            check("4. claim -> 200", r.status_code == 200, r.text)
            claim = r.json()
            check("4. claim returns active_business_id=1", claim.get("active_business_id") == 1, str(claim))
            check("4. claim returns role=editor", claim.get("role") == "editor", str(claim))
            check("4. claim sets session cookie", "popular_session" in anon.cookies, str(anon.cookies))

            # 5. Now logged in as the new user — root should serve dashboard.
            r = anon.get("/")
            check("5. anon (now authed) GET / -> 200", r.status_code == 200, f"got {r.status_code}")

            # New user must NOT be superuser.
            r = anon.get("/api/auth/me")
            me = r.json()
            check("5. /me reports new user", me.get("email") == "newhire@example.com", str(me))
            check("5. /me reports role=editor", me.get("role") == "editor", str(me))
            check("5. /me reports is_superuser=False", me.get("is_superuser") is False, str(me))
            check("5. /me capabilities.manage_invites=False",
                  me.get("capabilities", {}).get("manage_invites") is False, str(me))

        # 6. Back as bootstrap owner — invite shows accepted.
        r = client.get("/api/auth/invites")
        invs = r.json().get("invites", [])
        check("6. invite now status=accepted", invs and invs[0].get("status") == "accepted", str(invs))

        # 7. Re-claim same token -> 404.
        with TestClient(app, follow_redirects=False) as anon:
            r = anon.post("/api/auth/invites/claim", json={
                "token": raw_token,
                "password": "claim-pw-correct-horse",
            })
            check("7. re-claim used token -> 404", r.status_code == 404, r.text)

        # 8. Mint another invite, revoke it, lookup -> 404.
        r = client.post("/api/auth/invites", json={"email": "tobe-revoked@example.com", "role": "viewer"})
        check("8a. mint another -> 200", r.status_code == 200, r.text)
        another = r.json()
        another_token = another.get("rawToken", "")
        another_id = another.get("id")
        r = client.delete(f"/api/auth/invites/{another_id}")
        check("8b. revoke -> 200", r.status_code == 200, r.text)
        check("8b. revoked=True", r.json().get("revoked") is True, r.text)
        with TestClient(app, follow_redirects=False) as anon:
            r = anon.get(f"/api/auth/invites/lookup?token={another_token}")
            check("8c. revoked lookup -> 404", r.status_code == 404, r.text)

        # 9. Invite an already-member email -> 409.
        # newhire@example.com is now a member from step 4.
        r = client.post("/api/auth/invites", json={"email": "newhire@example.com", "role": "viewer"})
        check("9. invite existing member -> 409", r.status_code == 409, r.text)

        # 10. Editor role can't mint invites — log in as newhire and try.
        with TestClient(app, follow_redirects=False) as ed:
            r = ed.post("/api/auth/login", json={
                "email": "newhire@example.com",
                "password": "claim-pw-correct-horse",
            })
            check("10a. editor login -> 200", r.status_code == 200, r.text)
            r = ed.post("/api/auth/invites", json={"email": "other@example.com", "role": "viewer"})
            check("10b. editor mint -> 403", r.status_code == 403, r.text)

        # 11. GET /invite (HTML page) is publicly reachable.
        with TestClient(app, follow_redirects=False) as anon:
            r = anon.get("/invite?token=anything")
            check("11. GET /invite -> 200", r.status_code == 200, f"got {r.status_code}")
            check("11. /invite body has the claim form", b"claim-form" in r.content, "missing #claim-form")

    print(f"\n{len(PASSED)} pass / {len(FAILED)} fail")
    for label, detail in FAILED:
        print(f"  FAIL: {label} -- {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
