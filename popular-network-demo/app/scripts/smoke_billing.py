"""Phase F.2 smoke — Billing / Usage backend.

Run with:  uv run python -m app.scripts.smoke_billing

Covers:
1. /api/billing returns tier + usage + invoices + tier options.
2. Day-1: zero usage metrics, zero invoices (Quadd just enrolled).
3. /api/billing/usage current-month rows present.
4. Tier-change request: from current (4) to lower (3) records pending row.
5. Pending tier change visible in GET /api/billing.
6. Re-requesting supersedes the previous pending row.
7. Requesting same tier as current → 400.
8. Stripe is disabled in the demo (stripeEnabled=false).
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_f2_"))
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

    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase F.2 (Billing) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- 1. /api/billing shape ----
    r = client.get("/api/billing")
    if r.status_code != 200:
        _fail(f"GET /api/billing → {r.status_code} {r.text}")
    bil = r.json()
    for k in ("currentTier", "currentTierLabel", "monthlyPrice", "monthlyPriceCents",
              "tierOptions", "usage", "invoices", "paidToDateCents",
              "pendingTierChange", "stripeEnabled"):
        if k not in bil:
            _fail(f"/api/billing missing '{k}'")
    if bil["currentTier"] != 4:
        _fail(f"Phase F: currentTier should be 4 (Quadd), got {bil['currentTier']}")
    if bil["monthlyPrice"] != 799 or bil["monthlyPriceCents"] != 79900:
        _fail(f"Tier 4 price should be $799 / 79900 cents, got {bil['monthlyPrice']} / {bil['monthlyPriceCents']}")
    _ok(f"GET /api/billing → tier {bil['currentTier']} ({bil['currentTierLabel']}, ${bil['monthlyPrice']}/mo)")

    # tierOptions: 4 entries (1/2/3/4) with current=tier 4
    if len(bil["tierOptions"]) != 4:
        _fail(f"tierOptions should have 4 entries, got {len(bil['tierOptions'])}")
    current_opt = next(o for o in bil["tierOptions"] if o["isCurrent"])
    if current_opt["tier"] != 4:
        _fail(f"isCurrent flag wrong: {current_opt}")
    _ok(f"tierOptions: 4 entries, current=tier {current_opt['tier']}")

    # ---- 2. Day-1: zero usage, zero invoices ----
    if bil["invoices"] != []:
        _fail(f"Day-1 should have zero invoices, got {bil['invoices']}")
    if bil["paidToDateCents"] != 0:
        _fail(f"Day-1 paid-to-date should be $0, got {bil['paidToDateCents']}")
    if any(m["value"] != 0 for m in bil["usage"]["metrics"]):
        _fail(f"Day-1 usage rows should all be zero: {bil['usage']['metrics']}")
    if len(bil["usage"]["metrics"]) != 4:
        _fail(f"Should expose 4 usage metric rows, got {len(bil['usage']['metrics'])}")
    _ok(f"Day-1 honest empty: 0 invoices, 4 usage rows all at zero")

    # ---- 3. /api/billing/usage standalone ----
    r = client.get("/api/billing/usage").json()
    if "metrics" not in r or "monthYear" not in r:
        _fail(f"/api/billing/usage shape wrong: {r}")
    _ok(f"GET /api/billing/usage → {r['monthYear']} {len(r['metrics'])} metrics")

    # ---- 4. Tier-change request: tier 4 → tier 3 ----
    r = client.post("/api/billing/change-tier-request", json={
        "to_tier": 3,
        "note": "Concierge is enough — don't need inventory feeds.",
    })
    if r.status_code != 200:
        _fail(f"POST change-tier-request → {r.status_code} {r.text}")
    req = r.json()
    if req["fromTier"] != 4 or req["toTier"] != 3 or req["status"] != "pending":
        _fail(f"Tier change response wrong: {req}")
    req_id = req["id"]
    _ok(f"POST change-tier-request 4→3 → pending id={req_id}")

    # ---- 5. Pending tier change visible in billing slice ----
    r = client.get("/api/billing").json()
    if r["pendingTierChange"] is None or r["pendingTierChange"]["id"] != req_id:
        _fail(f"Pending tier change should appear in /api/billing: {r['pendingTierChange']}")
    _ok(f"GET /api/billing.pendingTierChange.id == {req_id}")

    # ---- 6. Re-request supersedes ----
    r = client.post("/api/billing/change-tier-request", json={"to_tier": 2})
    new_id = r.json()["id"]
    r = client.get("/api/billing").json()
    if r["pendingTierChange"]["id"] != new_id or r["pendingTierChange"]["toTier"] != 2:
        _fail(f"Re-request didn't supersede: {r['pendingTierChange']}")
    _ok(f"Re-request supersedes: old id={req_id} cancelled, new id={new_id} pending")

    # ---- 7. Same-tier request → 400 ----
    r = client.post("/api/billing/change-tier-request", json={"to_tier": 4})
    if r.status_code != 400:
        _fail(f"Same-tier request should be 400, got {r.status_code}")
    _ok("Same-tier change request → 400")

    # ---- 8. Stripe disabled ----
    if r := client.get("/api/billing").json():
        if r["stripeEnabled"] is not False:
            _fail(f"stripeEnabled should be False for demo: {r['stripeEnabled']}")
    _ok("stripeEnabled=False (BILLING_ENABLED=false mode)")

    # Bad to_tier validation (out of range)
    r = client.post("/api/billing/change-tier-request", json={"to_tier": 99})
    if r.status_code != 422:
        _fail(f"out-of-range tier should be 422, got {r.status_code}")
    _ok("Out-of-range to_tier=99 → 422")


if __name__ == "__main__":
    main()
