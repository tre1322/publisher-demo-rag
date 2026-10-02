"""Hotfix smoke — nothing outside static/ is served as a file.

Run with:  uv run python -m app.scripts.smoke_static_exposure

Born 2026-10-02: the app mounted the whole project root at "/", so the live
SQLite DB (/data/popular_network.db), its pre-deploy backups, and the app
source were downloadable without signing in. Checks both signed-out and
signed-in, since a session must not unlock raw files either.
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_static_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

PRIVATE = [
    "/data/popular_network.db",
    "/data/popular_network.db.pre-phase1a-20261002-202632",
    "/app/main.py",
    "/app/db.py",
    "/app/__init__.py",
    "/pyproject.toml",
    "/uv.lock",
    "/Dockerfile",
    "/.env",
    "/.env.example",
    "/voice-briefs/quadd_ai.json",
    "/docs/amplora_business_plan.md",
    "/handoff.md",
    "/README.md",
    "/static/../app/main.py",
    "/static/%2e%2e/app/main.py",
]


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

    with TestClient(app, follow_redirects=False) as client:
        import app.main as main_mod
        check("API explorer only outside production", main_mod._API_DOCS == (
            os.getenv("ENVIRONMENT", "development").lower() != "production"))
        for path in PRIVATE:
            r = client.get(path)
            check(f"signed out: {path} not served", r.status_code in (404, 401, 302) and b"SQLite" not in r.content,
                  f"{r.status_code} {r.content[:40]!r}")
        check("signed out: /static/widget.js still served", client.get("/static/widget.js").status_code == 200)
        check("signed out: /login page served", client.get("/login").status_code == 200)
        check("signed out: / redirects to login", client.get("/").status_code == 302)
        check("signed out: /invite page served", client.get("/invite").status_code == 200)

        bootstrap_login(client)
        for path in PRIVATE:
            r = client.get(path)
            check(f"signed in: {path} not served", r.status_code in (404, 401) and b"SQLite" not in r.content,
                  f"{r.status_code} {r.content[:40]!r}")
        r = client.get("/")
        check("signed in: / serves the dashboard", r.status_code == 200 and b"BUILD_VERSION" in r.content, r.status_code)
        check("signed in: /dashboard.html serves the dashboard", client.get("/dashboard.html").status_code == 200)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  static-exposure smoke green ✓")


if __name__ == "__main__":
    main()
