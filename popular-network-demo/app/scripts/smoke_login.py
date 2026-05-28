"""H.1.5 smoke — dashboard auth gate + login page.

Verifies:
  1. GET /          unauthed → 302 to /login
  2. GET /dashboard.html unauthed → 302 to /login
  3. GET /login     unauthed → 200 + login HTML body
  4. POST /api/auth/login → sets cookie
  5. GET /          with cookie → 200 + dashboard HTML
  6. GET /login     with cookie → 302 to /
  7. After logout: GET / → 302 to /login again

Run with:  uv run python -m app.scripts.smoke_login
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

# Force UTF-8 stdio on Windows.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_login_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.scripts._auth_helper import bootstrap_login  # noqa: E402


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
    print("smoke_login -- Phase H.1.5 auth gate\n")

    # follow_redirects=False so we can inspect the 302 responses directly.
    with TestClient(app, follow_redirects=False) as client:
        # 1. Root unauthed
        r = client.get("/")
        check("1. GET / unauthed -> 302", r.status_code == 302, f"got {r.status_code}")
        check("1. GET / unauthed redirects to /login",
              r.headers.get("location") == "/login",
              f"location={r.headers.get('location')}")

        # 2. /dashboard.html unauthed (covers direct-URL + StaticFiles fallback)
        r = client.get("/dashboard.html")
        check("2. GET /dashboard.html unauthed -> 302", r.status_code == 302, f"got {r.status_code}")
        check("2. /dashboard.html unauthed redirects to /login",
              r.headers.get("location") == "/login",
              f"location={r.headers.get('location')}")

        # 3. /login unauthed serves the page
        r = client.get("/login")
        check("3. GET /login unauthed -> 200", r.status_code == 200, f"got {r.status_code}")
        check("3. /login body has the form", b"login-form" in r.content, "missing #login-form")
        check("3. /login body has Sign in heading", b"Sign in" in r.content, "missing Sign in")

        # 4-5. Log in via the auth helper (POST /api/auth/login + cookie)
        bootstrap_login(client)
        check("4. cookie set after login", "popular_session" in client.cookies, str(client.cookies))

        r = client.get("/")
        check("5. GET / with cookie -> 200", r.status_code == 200, f"got {r.status_code}")
        check("5. GET / body looks like dashboard",
              b"Popular Network" in r.content and b"dashboard" in r.content.lower(),
              "doesn't look like dashboard.html")

        # 6. Logged-in user on /login redirects to /
        r = client.get("/login")
        check("6. GET /login with cookie -> 302", r.status_code == 302, f"got {r.status_code}")
        check("6. /login with cookie redirects to /",
              r.headers.get("location") == "/",
              f"location={r.headers.get('location')}")

        # 7. Logout, then / should redirect to /login again
        r = client.post("/api/auth/logout")
        check("7. logout -> 200", r.status_code == 200, f"got {r.status_code}")
        client.cookies.clear()  # belt + suspenders; logout already cleared the cookie
        r = client.get("/")
        check("7. GET / after logout -> 302", r.status_code == 302, f"got {r.status_code}")
        check("7. GET / after logout redirects to /login",
              r.headers.get("location") == "/login",
              f"location={r.headers.get('location')}")

    print(f"\n{len(PASSED)} pass / {len(FAILED)} fail")
    for label, detail in FAILED:
        print(f"  FAIL: {label} -- {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
