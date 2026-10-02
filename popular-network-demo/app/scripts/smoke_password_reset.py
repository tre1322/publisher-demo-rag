"""Phase 1c smoke — forgot-password by email works end to end.

Run with:  uv run python -m app.scripts.smoke_password_reset

Before this, a forgotten password meant Amplafai reset it by hand on the
server. Covers:
  A. Request: same answer for real and unknown emails; email sent only for
     real, active accounts; the link is absolute and single-use
  B. Lookup + confirm: weak password refused; reset works; old password and
     every old session die; the link can't be reused
  C. Expiry, superseded links, inactive users, throttling
  D. Pages + email body
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_reset_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

EMAIL = "owner-reset@example.com"
OLD_PW = "old-password-correct-horse"
NEW_PW = "new-password-battery-staple"
SENT: list[dict] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def _capture(to_email: str, reset_url: str, *, minutes_valid: int) -> dict:
    SENT.append({"to": to_email, "url": reset_url, "minutes": minutes_valid})
    return {"sent": True, "messageId": "smoke"}


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    from app.main import app

    os.environ.pop("POSTMARK_API_KEY", None)  # hermetic; see smoke_money_path
    with patch("app.routers.password_reset.send_password_reset_email", _capture), \
         TestClient(app, follow_redirects=False) as anon, \
         TestClient(app, follow_redirects=False) as signed_in:
        _run(anon, signed_in)
    _email_body()
    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 1c password-reset smoke green ✓")


def _token(url: str) -> str:
    return url.split("token=", 1)[1]


def _run(anon, signed_in) -> None:  # noqa: C901 — linear smoke
    import app.routers.password_reset as pr
    from app.db import SessionLocal
    from app.models import BusinessUser, PasswordReset, User
    from app.pwhash import hash_password

    with SessionLocal() as db:
        u = User(email=EMAIL, password_hash=hash_password(OLD_PW), display_name="Owner", is_superuser=False, is_active=True)
        off = User(email="disabled@example.com", password_hash=hash_password(OLD_PW), is_superuser=False, is_active=False)
        db.add_all([u, off])
        db.flush()
        db.add(BusinessUser(user_id=u.id, business_id=1, role="owner"))
        db.commit()
        user_id = u.id

    r = signed_in.post("/api/auth/login", json={"email": EMAIL, "password": OLD_PW})
    check("setup: owner signs in with the old password", r.status_code == 200, r.text)
    check("setup: signed-in session works", signed_in.get("/api/bootstrap").status_code == 200)

    # ---- A. request -------------------------------------------------------------
    print("\nA. request a reset")
    r_real = anon.post("/api/auth/password-reset/request", json={"email": EMAIL.upper()})
    r_none = anon.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"})
    check("A1 real email → 200", r_real.status_code == 200, r_real.text)
    check("A2 unknown email → identical answer (no account oracle)",
          r_none.status_code == 200 and r_none.json() == r_real.json(), (r_none.json(), r_real.json()))
    check("A3 exactly one email sent, to the real account", [s["to"] for s in SENT] == [EMAIL], SENT)
    check("A4 link goes to the reset page", SENT[0]["url"].startswith("/reset-password?token=pwr_"), SENT[0]["url"])
    check("A5 email states the expiry", SENT[0]["minutes"] == 60, SENT[0]["minutes"])
    with SessionLocal() as db:
        row = db.query(PasswordReset).filter(PasswordReset.user_id == user_id).one()
        check("A6 only the token hash is stored", _token(SENT[0]["url"]) not in (row.token_hash or "")
              and len(row.token_hash) == 64)
    r = anon.post("/api/auth/password-reset/request", json={"email": "disabled@example.com"})
    check("A7 disabled account: same answer, no email", r.status_code == 200 and len(SENT) == 1, SENT)
    r = anon.post("/api/auth/password-reset/request", json={"email": "not-an-email"})
    check("A8 malformed email → 422", r.status_code == 422, r.status_code)

    # ---- B. lookup + confirm -------------------------------------------------------
    print("\nB. use the link")
    token = _token(SENT[0]["url"])
    r = anon.get(f"/api/auth/password-reset/lookup?token={token}")
    check("B1 lookup says valid, email masked", r.json().get("valid") is True and r.json()["email"].startswith("o")
          and EMAIL not in r.json()["email"], r.json())
    r = anon.get("/api/auth/password-reset/lookup?token=pwr_not_a_real_token_123")
    check("B2 bogus token → valid false", r.status_code == 200 and r.json() == {"valid": False}, r.text)
    r = anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": "short"})
    check("B3 weak password → 422, link still good", r.status_code == 422
          and anon.get(f"/api/auth/password-reset/lookup?token={token}").json()["valid"] is True, r.status_code)
    r = anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": NEW_PW})
    check("B4 confirm → 200, sessions signed out", r.status_code == 200 and r.json()["signedOutSessions"] >= 1, r.text)
    r = signed_in.get("/api/bootstrap")
    check("B5 the old session no longer works", r.status_code == 401, r.status_code)
    r = anon.post("/api/auth/login", json={"email": EMAIL, "password": OLD_PW})
    check("B6 old password refused", r.status_code == 401, r.status_code)
    r = anon.post("/api/auth/login", json={"email": EMAIL, "password": NEW_PW})
    check("B7 new password signs in", r.status_code == 200, r.text)
    anon.post("/api/auth/logout")
    r = anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": "another-password-1"})
    check("B8 link can't be reused", r.status_code == 400 and "expired or was already used" in r.json()["detail"], r.text)

    # ---- C. expiry, superseded, throttle --------------------------------------------
    print("\nC. expiry, superseded links, throttling")
    pr._HITS.clear()
    anon.post("/api/auth/password-reset/request", json={"email": EMAIL})
    first = _token(SENT[-1]["url"])
    anon.post("/api/auth/password-reset/request", json={"email": EMAIL})
    second = _token(SENT[-1]["url"])
    check("C1 a newer link retires the older one",
          anon.get(f"/api/auth/password-reset/lookup?token={first}").json()["valid"] is False
          and anon.get(f"/api/auth/password-reset/lookup?token={second}").json()["valid"] is True)
    with SessionLocal() as db:
        for row in db.query(PasswordReset).filter(PasswordReset.used_at.is_(None)).all():
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    r = anon.post("/api/auth/password-reset/confirm", json={"token": second, "password": "another-password-2"})
    check("C2 expired link refused", r.status_code == 400, r.status_code)
    r = anon.post("/api/auth/login", json={"email": EMAIL, "password": NEW_PW})
    check("C3 password unchanged after the refused attempts", r.status_code == 200, r.status_code)
    anon.post("/api/auth/logout")
    pr._HITS.clear()
    codes = [anon.post("/api/auth/password-reset/request", json={"email": EMAIL}).status_code for _ in range(4)]
    check("C4 more than 3 requests/hour for one email → 429", codes == [200, 200, 200, 429], codes)
    pr._HITS.clear()
    codes = [anon.post("/api/auth/password-reset/request", json={"email": f"x{i}@example.com"}).status_code
             for i in range(11)]
    check("C5 more than 10 requests/hour from one IP → 429", codes[:10] == [200] * 10 and codes[10] == 429, codes)
    pr._HITS.clear()

    # ---- D. pages ------------------------------------------------------------------
    print("\nD. pages")
    r = anon.get("/forgot-password")
    check("D1 /forgot-password served signed out", r.status_code == 200 and b"Send reset link" in r.content, r.status_code)
    r = anon.get("/reset-password?token=x")
    check("D2 /reset-password served", r.status_code == 200 and b"Choose a new password" in r.content, r.status_code)
    r = anon.get("/login")
    check("D3 login page links to forgot-password", b'href="/forgot-password"' in r.content
          and b"popularnetwork.example" not in r.content)


def _email_body() -> None:
    import app.email as email_mod

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"MessageID": "m1"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            captured.update(json)
            return _Resp()

    os.environ["POSTMARK_API_KEY"] = "smoke-not-real"
    os.environ["APP_BASE_URL"] = "https://dashboard.example.com"
    try:
        with patch.object(email_mod.httpx, "Client", _Client):
            out = email_mod.send_password_reset_email("a@example.com", "/reset-password?token=pwr_abc", minutes_valid=60)
    finally:
        os.environ.pop("POSTMARK_API_KEY", None)
    print("\nE. email")
    check("E1 sent through Postmark", out == {"sent": True, "messageId": "m1"}, out)
    check("E2 link is absolute", "https://dashboard.example.com/reset-password?token=pwr_abc" in captured["TextBody"],
          captured.get("TextBody"))
    check("E3 says it expires + is safe to ignore", "60 minutes" in captured["TextBody"] and "ignore" in captured["TextBody"])
    check("E4 tagged for Postmark reporting", captured.get("Tag") == "password-reset", captured.get("Tag"))


if __name__ == "__main__":
    main()
