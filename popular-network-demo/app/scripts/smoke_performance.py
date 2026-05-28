"""Phase B.6 smoke — exercise GET /api/performance + regenerate insights.

Run with:  uv run python -m app.scripts.smoke_performance
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b6_"))
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
    from app.scripts._auth_helper import bootstrap_login
    from app.db import SessionLocal
    from app.models import PerformanceSummary

    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client, SessionLocal, PerformanceSummary)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.6 (Performance) smoke green ✓")


def _run_assertions(client, SessionLocal, PerformanceSummary) -> None:
    # --- default period (30) returns seeded values ---
    r = client.get("/api/performance?period=30")
    if r.status_code != 200:
        _fail(f"GET period=30 → HTTP {r.status_code} {r.text}")
    p30 = r.json()
    if "reach" not in p30 or "engagement" not in p30:
        _fail(f"period=30 payload missing fields: {p30}")
    if p30["period"] != "30":
        _fail(f"period field wrong: {p30['period']}")
    reach30 = p30["reach"]["value"]
    _ok(f"GET period=30 → reach={reach30}")

    # --- ytd scales up ~5× (or equals 0 when baseline is 0, e.g. Day-1 seed) ---
    r = client.get("/api/performance?period=ytd")
    pytd = r.json()
    if reach30 > 0 and pytd["reach"]["value"] < reach30 * 4:
        _fail(f"ytd reach should scale much higher than 30d: ytd={pytd['reach']['value']} 30={reach30}")
    if reach30 == 0 and pytd["reach"]["value"] != 0:
        _fail(f"ytd reach should also be 0 when 30d baseline is 0: ytd={pytd['reach']['value']}")
    _ok(f"GET period=ytd → reach scaled to {pytd['reach']['value']} (vs 30d={reach30})")

    # --- prev30 reduces (or stays 0 when baseline is 0) ---
    r = client.get("/api/performance?period=prev30")
    pprev = r.json()
    if reach30 > 0 and pprev["reach"]["value"] >= reach30:
        _fail(f"prev30 should be lower than current: prev={pprev['reach']['value']} 30={reach30}")
    if reach30 == 0 and pprev["reach"]["value"] != 0:
        _fail(f"prev30 should be 0 when 30d baseline is 0: prev={pprev['reach']['value']}")
    _ok(f"GET period=prev30 → reach scaled down to {pprev['reach']['value']}")

    # --- 422 on bogus period ---
    r = client.get("/api/performance?period=lol")
    if r.status_code != 422:
        _fail(f"bogus period expected 422, got {r.status_code}")
    _ok("GET period='lol' → 422")

    # --- regenerate insights mutates DB + returns new set ---
    r = client.post("/api/performance/regenerate-insights")
    if r.status_code != 200:
        _fail(f"regen → HTTP {r.status_code} {r.text}")
    data = r.json()
    if not data.get("ok") or len(data.get("insights", [])) != 4:
        _fail(f"regen response shape: {data}")
    new_titles = {i["title"] for i in data["insights"]}
    if not new_titles:
        _fail("regen returned empty insights")
    with SessionLocal() as s:
        perf = s.get(PerformanceSummary, 1)
        db_titles = {i["title"] for i in perf.insights_json}
        if db_titles != new_titles:
            _fail(f"regen response vs DB mismatch: {new_titles} vs {db_titles}")
        for i in perf.insights_json:
            if "generated_at" not in i:
                _fail(f"insight missing generated_at: {i}")
    _ok(f"POST regenerate-insights → 4 fresh insights in DB, generated_at stamped")

    # --- /api/bootstrap reflects the regenerated insights ---
    boot = client.get("/api/bootstrap").json()
    boot_titles = {i["title"] for i in boot["performance"]["insights"]}
    if boot_titles != new_titles:
        _fail(f"bootstrap insights stale: {boot_titles} vs {new_titles}")
    _ok("/api/bootstrap → regenerated insights visible")


if __name__ == "__main__":
    main()
