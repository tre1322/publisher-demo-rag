"""Phase D.1 smoke — POST /api/reach/estimate + bootstrap inclusion.

Run with:  uv run python -m app.scripts.smoke_reach

Assertions (in order of failure cost):
1. Bootstrap exposes the new `reach` slice with `tiers`, `territoryRevenue`,
   `localFirst` — regardless of whether the formula is filled in yet.
2. Tier ladder has all four tiers in the right multiplier order (0/50/100/175).
3. Day-1 telemetry is honest-empty (`hasData=false` for both telemetry slices).
4. POST estimate returns 422 on bogus tier_key.
5. (After Trevor writes the formula) POST estimate for all 4 tiers obeys the
   multiplier contract — total_cents == base_cents * (1 + multiplier_pct/100) —
   AND impression estimates are monotonically non-decreasing across tiers.
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_d1_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def _skip(msg: str) -> None:
    print(f"  skip {msg}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient
    from app.main import app

    # raise_server_exceptions=False lets the NotImplementedError surface as
    # an HTTP 500 we can detect — otherwise TestClient re-raises and we can't
    # gracefully report "formula not implemented yet."
    with TestClient(app, raise_server_exceptions=False) as client:
        _run_assertions(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase D.1 (Reach backend) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- bootstrap exposes the new reach slice ----
    boot = client.get("/api/bootstrap").json()
    if "reach" not in boot:
        _fail("bootstrap missing 'reach' key")
    reach = boot["reach"]
    for k in ("tiers", "territoryRevenue", "localFirst"):
        if k not in reach:
            _fail(f"bootstrap.reach missing '{k}'")
    _ok("bootstrap.reach has tiers + territoryRevenue + localFirst")

    # ---- tier ladder in multiplier order, 4 tiers ----
    tiers = reach["tiers"]
    if len(tiers) != 4:
        _fail(f"expected 4 tiers, got {len(tiers)}: {[t['key'] for t in tiers]}")
    keys = [t["key"] for t in tiers]
    if keys != ["local", "regional", "network", "maximum"]:
        _fail(f"tier order wrong: {keys}")
    mults = [t["multiplierPct"] for t in tiers]
    if mults != [0, 50, 100, 175]:
        _fail(f"multiplier ladder wrong: {mults}")
    territories = [len(t["territories"]) for t in tiers]
    if not (territories[0] < territories[1] < territories[2] < territories[3]):
        _fail(f"territory count should widen with tier: {territories}")
    _ok(f"tier ladder OK — {keys}, mults={mults}, territories widen {territories}")

    # ---- Day-1 honest empty telemetry ----
    tr = reach["territoryRevenue"]
    lf = reach["localFirst"]
    if tr["hasData"] is not False or tr["totalImpressions"] != 0:
        _fail(f"Day-1 territoryRevenue should be empty: {tr}")
    if lf["hasData"] is not False or lf["queriesTotal"] != 0:
        _fail(f"Day-1 localFirst should be empty: {lf}")
    _ok("Day-1 telemetry honest-empty (territoryRevenue + localFirst both hasData=false)")

    # ---- 422 on bogus tier_key ----
    r = client.post("/api/reach/estimate", json={
        "tier_key": "galactic",
        "base_rate_cents": 20000,
        "days": 7,
        "platforms": ["fb", "web"],
    })
    if r.status_code != 422:
        _fail(f"bogus tier_key expected 422, got {r.status_code} {r.text}")
    _ok("POST estimate w/ bogus tier_key → 422")

    # ---- formula contract — gated on whether Trevor's filled it in yet ----
    # Probe the function directly so we can distinguish "formula not done"
    # from "formula crashed in a way I didn't expect." TestClient swallows
    # the exception class once it crosses the HTTP boundary; calling the
    # function gives us the actual error type back.
    from app.db import SessionLocal
    from app.models import ReachTier
    from app.routers.reach import estimate_reach

    with SessionLocal() as db:
        local_tier = db.query(ReachTier).filter(
            ReachTier.business_id == 1, ReachTier.tier_key == "local",
        ).one()
        try:
            estimate_reach(tier=local_tier, base_rate_cents=20000, days=7, platforms=["fb", "web"])
        except NotImplementedError:
            _skip("estimate formula not yet implemented — open app/routers/reach.py and fill in the marked block")
            return
        except Exception as e:
            _fail(f"estimate_reach raised unexpected {type(e).__name__}: {e}")

    sample = {
        "tier_key": "local",
        "base_rate_cents": 20000,  # $200 base
        "days": 7,
        "platforms": ["fb", "web"],
    }
    r = client.post("/api/reach/estimate", json=sample)
    if r.status_code != 200:
        _fail(f"POST estimate (local) → HTTP {r.status_code} {r.text}")

    # All four tiers — check multiplier contract + impression monotonicity.
    results = []
    for tier_key, expected_mult in [("local", 0), ("regional", 50), ("network", 100), ("maximum", 175)]:
        r = client.post("/api/reach/estimate", json={**sample, "tier_key": tier_key})
        if r.status_code != 200:
            _fail(f"POST estimate ({tier_key}) → HTTP {r.status_code} {r.text}")
        data = r.json()
        cb = data["costBreakdown"]
        if cb["baseCents"] != 20000:
            _fail(f"{tier_key}: baseCents should equal base_rate_cents 20000, got {cb['baseCents']}")
        expected_total = round(20000 * (1 + expected_mult / 100))
        if cb["totalCents"] != expected_total:
            _fail(f"{tier_key}: totalCents={cb['totalCents']} violates multiplier contract — should be {expected_total} (base × {1 + expected_mult/100})")
        if cb["upliftCents"] != cb["totalCents"] - cb["baseCents"]:
            _fail(f"{tier_key}: upliftCents={cb['upliftCents']} != total - base = {cb['totalCents'] - cb['baseCents']}")
        if data["estimatedImpressions"] < 0 or data["estimatedUniqueReach"] < 0:
            _fail(f"{tier_key}: negative impressions/reach: {data}")
        if data["estimatedUniqueReach"] > data["estimatedImpressions"]:
            _fail(f"{tier_key}: unique > impressions ({data['estimatedUniqueReach']} > {data['estimatedImpressions']})")
        results.append(data)
        _ok(f"{tier_key:9s} → ${cb['totalCents']/100:>6.2f} · ~{data['estimatedImpressions']:,} impressions · ~{data['estimatedUniqueReach']:,} unique")

    # Impressions monotonically non-decreasing as tier widens.
    imps = [r["estimatedImpressions"] for r in results]
    if imps != sorted(imps):
        _fail(f"impressions should be non-decreasing across tiers, got {imps}")
    if imps[3] <= imps[0]:
        _fail(f"maximum tier impressions ({imps[3]}) should exceed local tier impressions ({imps[0]})")
    _ok(f"impression ladder monotonic: {imps}")


if __name__ == "__main__":
    main()
