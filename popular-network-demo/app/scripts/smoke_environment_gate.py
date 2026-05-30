"""Smoke for the ENVIRONMENT=production gate on dev-only routes.

Run with:  uv run python -m app.scripts.smoke_environment_gate

Asserts:
1. ENVIRONMENT unset (default development) → /widget-test.html → 200.
2. ENVIRONMENT=production → /widget-test.html → 404.
3. Either way, /static/widget.js (the actual widget bundle) still serves.
   (Publishers need the widget bundle to be reachable from prod URLs.)
"""
from __future__ import annotations

import importlib
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_envgate_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _fails.append(label)


def _fresh_app(env_value: str | None):
    """Reload app.main so the module-level _IS_PRODUCTION re-reads env."""
    if env_value is None:
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["ENVIRONMENT"] = env_value
    # Drop already-imported modules so the next import re-evaluates them.
    for mod in list(sys.modules.keys()):
        if mod.startswith("app."):
            del sys.modules[mod]
    main_mod = importlib.import_module("app.main")
    return main_mod.app


def main() -> int:
    from fastapi.testclient import TestClient

    # 1. Default (dev) — widget-test.html is reachable.
    app = _fresh_app(env_value=None)
    with TestClient(app) as client:
        r = client.get("/widget-test.html")
        check("1. dev → widget-test.html 200", r.status_code == 200, r.text[:80])
        r = client.get("/static/widget.js")
        check("1. dev → widget.js reachable", r.status_code == 200, str(r.status_code))

    # 2. Production — widget-test.html 404, but widget.js still serves.
    app = _fresh_app(env_value="production")
    with TestClient(app) as client:
        r = client.get("/widget-test.html")
        check("2. prod → widget-test.html 404", r.status_code == 404, r.text[:80])
        r = client.get("/static/widget.js")
        check("2. prod → widget.js still reachable", r.status_code == 200, str(r.status_code))

    # Reset so other smokes don't inherit prod env.
    os.environ.pop("ENVIRONMENT", None)

    print()
    total = 4
    if _fails:
        print(f"FAIL — {len(_fails)} of {total} checks failed")
        return 1
    print(f"{total} pass / 0 fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
