"""Phase B.8 smoke — exercise PUT /api/marketing-plan end-to-end.

Run with:  uv run python -m app.scripts.smoke_marketing_plan
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b8_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import MarketingPlan

    with TestClient(app) as client:
        _run_assertions(client, SessionLocal, MarketingPlan)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.8 (Marketing Plan) smoke green ✓")


def _run_assertions(client, SessionLocal, MarketingPlan) -> None:
    boot = client.get("/api/bootstrap").json()
    original_audience = boot["marketingPlan"]["audience"]
    if not original_audience:
        _fail(f"seeded marketing plan missing audience")
    _ok(f"bootstrap → audience seeded ({original_audience[:40]}…)")

    # --- update audience only ---
    new_audience = "EDITED: Westbrook drivers who value honesty over speed."
    r = client.put("/api/marketing-plan", json={"audience": new_audience})
    if r.status_code != 200:
        _fail(f"PUT audience → HTTP {r.status_code} {r.text}")
    if r.json()["marketingPlan"]["audience"] != new_audience:
        _fail(f"response audience: {r.json()['marketingPlan']['audience']!r}")
    with SessionLocal() as s:
        mp = s.get(MarketingPlan, 1)
        if mp.audience != new_audience:
            _fail(f"DB audience: {mp.audience!r}")
    _ok("PUT audience → persisted + response echoes")

    # --- update valueProp only ---
    new_vp = "EDITED VALUE PROP: Honest, no upsell, lifetime mechanic."
    r = client.put("/api/marketing-plan", json={"valueProp": new_vp})
    if r.status_code != 200:
        _fail(f"PUT valueProp → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        mp = s.get(MarketingPlan, 1)
        if mp.value_prop != new_vp:
            _fail(f"DB value_prop: {mp.value_prop!r}")
        if mp.audience != new_audience:
            _fail(f"audience clobbered by valueProp PUT: {mp.audience!r}")
    _ok("PUT valueProp → persisted; audience untouched")

    # --- update customerLanguage list ---
    new_lang = ['"new chip 1"', '"another phrase"', "raw text"]
    r = client.put("/api/marketing-plan", json={"customerLanguage": new_lang})
    if r.status_code != 200:
        _fail(f"PUT customerLanguage → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        mp = s.get(MarketingPlan, 1)
        if mp.customer_language_json != new_lang:
            _fail(f"DB customer_language_json: {mp.customer_language_json}")
    _ok(f"PUT customerLanguage → list[{len(new_lang)}] persisted")

    # --- customerLanguage filters empty/whitespace entries ---
    r = client.put("/api/marketing-plan", json={"customerLanguage": ["valid", "  ", "", "  also valid  "]})
    if r.status_code != 200:
        _fail(f"PUT customerLanguage strip → HTTP {r.status_code}")
    with SessionLocal() as s:
        mp = s.get(MarketingPlan, 1)
        if mp.customer_language_json != ["valid", "also valid"]:
            _fail(f"strip didn't filter empties: {mp.customer_language_json}")
    _ok("PUT customerLanguage → strips empty/whitespace entries")

    # --- 422 on empty body ---
    r = client.put("/api/marketing-plan", json={})
    if r.status_code != 422:
        _fail(f"empty body expected 422, got {r.status_code}")
    _ok("PUT empty body → 422")

    # --- 422 on whitespace audience ---
    r = client.put("/api/marketing-plan", json={"audience": "   "})
    if r.status_code != 422:
        _fail(f"whitespace audience expected 422, got {r.status_code}")
    _ok("PUT audience='   ' → 422")

    # --- bootstrap reflects all updates ---
    boot2 = client.get("/api/bootstrap").json()
    if boot2["marketingPlan"]["audience"] != new_audience:
        _fail(f"bootstrap audience stale: {boot2['marketingPlan']['audience']!r}")
    if boot2["marketingPlan"]["valueProp"] != new_vp:
        _fail(f"bootstrap valueProp stale: {boot2['marketingPlan']['valueProp']!r}")
    _ok("/api/bootstrap → all edits visible")


if __name__ == "__main__":
    main()
