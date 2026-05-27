"""Phase E.1 smoke — Ads backend foundation.

Run with:  uv run python -m app.scripts.smoke_ads

Covers (in order of failure cost):
1. Bootstrap exposes the new `ads` slice with the right shape.
2. Day-1 honest empty state (Quadd: no caps, no connections, no campaigns).
3. GET /api/ads returns full picture with empty arrays.
4. PUT budget creates row first time, updates on subsequent calls.
5. Mock OAuth flow: connect → status=connected with mock IDs; disconnect.
6. Campaign create (manual_owner) lands as 'scheduled' w/ mock external_campaign_id.
7. Campaign create (agent_proposed) lands as 'pending_approval' + Approval row.
8. Approve flow advances pending_approval → scheduled.
9. Tick simulator advances spend + impressions on active campaigns.
10. Budget cap pauses campaigns when exceeded.
11. Bogus platform → 422 validation.
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_e1_"))
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

    with TestClient(app) as client:
        _run_assertions(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase E.1 (Ads backend) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- 1. bootstrap.ads exists and has the right shape ----
    boot = client.get("/api/bootstrap").json()
    if "ads" not in boot:
        _fail("bootstrap missing 'ads' key")
    ads = boot["ads"]
    for k in ("monthYear", "totalCapCents", "totalSpendCents", "activeCount", "pendingCount",
              "connectionStatus", "hasAnyCap", "hasAnyConnection"):
        if k not in ads:
            _fail(f"bootstrap.ads missing '{k}'")
    _ok(f"bootstrap.ads has all expected keys (month={ads['monthYear']})")

    # ---- 2. Day-1 honest empty state ----
    if ads["totalCapCents"] != 0 or ads["totalSpendCents"] != 0:
        _fail(f"Day-1 ads should have zero spend/cap: {ads}")
    if ads["hasAnyCap"] or ads["hasAnyConnection"]:
        _fail(f"Day-1 ads should have hasAnyCap=False and hasAnyConnection=False: {ads}")
    for p in ("fb_ig", "google_ads", "tiktok", "linkedin"):
        if ads["connectionStatus"].get(p) != "disconnected":
            _fail(f"Day-1 connection for {p} should be 'disconnected', got {ads['connectionStatus'].get(p)}")
    _ok("Day-1 honest empty state (4 disconnected platforms, $0 caps, $0 spend)")

    # ---- 3. GET /api/ads full picture ----
    r = client.get("/api/ads")
    if r.status_code != 200:
        _fail(f"GET /api/ads → {r.status_code}")
    full = r.json()
    if len(full["budgets"]) != 4 or len(full["connections"]) != 4 or len(full["campaigns"]) != 0:
        _fail(f"/api/ads shape: budgets={len(full['budgets'])} connections={len(full['connections'])} campaigns={len(full['campaigns'])}")
    _ok(f"GET /api/ads → 4 budgets, 4 connections, 0 campaigns")

    # ---- 4. PUT budget creates / updates ----
    r = client.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": 30000})
    if r.status_code != 200:
        _fail(f"PUT budget fb_ig → {r.status_code} {r.text}")
    body = r.json()
    if body["monthlyCapCents"] != 30000 or body["platform"] != "fb_ig":
        _fail(f"PUT budget response wrong: {body}")
    _ok(f"PUT /api/ads/budgets/fb_ig → cap $300, remaining {body['remainingCents']/100:.0f}")

    # Update existing
    r = client.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": 50000})
    body = r.json()
    if body["monthlyCapCents"] != 50000:
        _fail(f"PUT budget update didn't take: {body}")
    _ok("PUT same platform again → cap updated to $500 (no duplicate row)")

    # ---- 5. Mock OAuth flow ----
    r = client.post("/api/ads/connections", json={"platform": "fb_ig"})
    if r.status_code != 200:
        _fail(f"POST connect fb_ig → {r.status_code} {r.text}")
    conn = r.json()
    if conn["status"] != "connected" or not conn["externalAccountId"]:
        _fail(f"Connect didn't yield connected status: {conn}")
    fb_conn_id = conn["id"]
    _ok(f"POST connect fb_ig → connected, externalAccountId={conn['externalAccountId']}")

    # Disconnect
    r = client.delete(f"/api/ads/connections/{fb_conn_id}")
    if r.status_code != 200 or r.json().get("status") != "disconnected":
        _fail(f"Disconnect didn't work: {r.text}")
    # Reconnect for the campaign create test below.
    client.post("/api/ads/connections", json={"platform": "fb_ig"})
    _ok("DELETE connection → disconnected; reconnect → connected (idempotent)")

    # ---- 6. Campaign create — manual_owner lands scheduled ----
    r = client.post("/api/ads/campaigns", json={
        "platform": "fb_ig",
        "name": "Quadd free-trial — week 1",
        "daily_budget_cents": 2000,  # $20/day
        "duration_days": 7,
        "origin": "manual_owner",
    })
    if r.status_code != 200:
        _fail(f"POST campaign manual_owner → {r.status_code} {r.text}")
    c = r.json()
    if c["status"] != "scheduled" or c["approvedBy"] != "owner" or not c["externalCampaignId"]:
        _fail(f"manual_owner campaign wrong: {c}")
    if c["plannedTotalCents"] != 14000:
        _fail(f"plannedTotalCents wrong: {c['plannedTotalCents']} (expected 14000)")
    owner_campaign_id = c["id"]
    _ok(f"POST campaign manual_owner → scheduled, externalCampaignId={c['externalCampaignId']}")

    # ---- 7. Campaign create — agent_proposed lands pending_approval ----
    r = client.post("/api/ads/campaigns", json={
        "platform": "google_ads",
        "name": "Quadd search ads — proposal",
        "daily_budget_cents": 1500,
        "duration_days": 14,
        "origin": "agent_proposed",
    })
    c = r.json()
    if c["status"] != "pending_approval" or c["approvedBy"] is not None:
        _fail(f"agent_proposed should be pending_approval w/ approvedBy=null: {c}")
    if c["externalCampaignId"]:
        _fail(f"agent_proposed should not have externalCampaignId yet: {c['externalCampaignId']}")
    pending_id = c["id"]
    # Approval row should exist too.
    boot = client.get("/api/bootstrap").json()
    approval_ids = [a["id"] for a in boot["approvals"]]
    if f"boost-{pending_id}" not in approval_ids:
        _fail(f"Approval row for boost-{pending_id} not in queue: {approval_ids}")
    _ok(f"POST campaign agent_proposed → pending_approval + Approval row created")

    # ---- 8. Approve flow ----
    r = client.post(f"/api/ads/campaigns/{pending_id}/approve")
    if r.status_code != 200:
        _fail(f"POST approve → {r.status_code} {r.text}")
    c = r.json()
    if c["status"] != "scheduled" or c["approvedBy"] != "owner" or not c["externalCampaignId"]:
        _fail(f"Approve didn't advance correctly: {c}")
    _ok(f"POST approve → scheduled w/ externalCampaignId={c['externalCampaignId']}")

    # ---- 9. Tick simulator ----
    r = client.post("/api/ads/tick", params={"hours": 24})
    if r.status_code != 200:
        _fail(f"POST tick → {r.status_code} {r.text}")
    tick = r.json()
    if tick["advanced"] < 1:
        _fail(f"Tick should have advanced at least one campaign: {tick}")
    _ok(f"POST tick (24h) → advanced {tick['advanced']} campaigns, completed {tick['completed']}")

    # Refetch to see actual spend mutated.
    r = client.get(f"/api/ads/campaigns?status=active")
    actives = r.json()
    if not actives:
        # Could already be completed if budget tiny — check that case
        r = client.get("/api/ads/campaigns")
        any_progress = any(c["actualSpendCents"] > 0 for c in r.json())
        if not any_progress:
            _fail("Tick didn't actually advance spend on any campaign")
    else:
        if all(c["actualSpendCents"] == 0 for c in actives):
            _fail(f"Active campaigns still have zero spend after tick: {actives}")
        spends = [(c["name"], c["actualSpendCents"], (c["performance"] or {}).get("impressions", 0)) for c in actives]
        _ok(f"Tick mutated spend + impressions on {len(actives)} active campaigns: {spends[:2]}")

    # Verify budget aggregate followed.
    r = client.get("/api/ads/budgets").json()
    fb_budget = next(b for b in r["budgets"] if b["platform"] == "fb_ig")
    if fb_budget["spendCents"] == 0:
        _fail(f"fb_ig budget should have non-zero spend after tick: {fb_budget}")
    _ok(f"fb_ig budget spend = ${fb_budget['spendCents']/100:.2f} (cap $500, {fb_budget['pctUsed']*100:.0f}% used)")

    # ---- 10. Budget cap enforcement: set tiny cap, tick repeatedly ----
    # Set a tiny LinkedIn cap and schedule an over-budget campaign.
    client.put("/api/ads/budgets/linkedin", json={"monthly_cap_cents": 500})  # $5 cap
    client.post("/api/ads/connections", json={"platform": "linkedin"})
    r = client.post("/api/ads/campaigns", json={
        "platform": "linkedin",
        "name": "LinkedIn cap test",
        "daily_budget_cents": 1000,  # $10/day
        "duration_days": 5,           # $50 planned
        "origin": "manual_owner",
    })
    linkedin_id = r.json()["id"]
    # Tick multiple times to exceed the $5 cap.
    for _ in range(3):
        client.post("/api/ads/tick", params={"hours": 24})
    r = client.get(f"/api/ads/campaigns?platform=linkedin").json()
    li = next(c for c in r if c["id"] == linkedin_id)
    if li["status"] != "paused":
        _fail(f"LinkedIn campaign should be 'paused' after exceeding $5 cap, got status={li['status']} spend=${li['actualSpendCents']/100:.2f}")
    _ok(f"Cap enforcement: LinkedIn campaign paused at ${li['actualSpendCents']/100:.2f} (cap $5.00)")

    # ---- 11. Bogus platform → 422 ----
    r = client.post("/api/ads/campaigns", json={
        "platform": "myspace",
        "name": "lol",
        "daily_budget_cents": 1000,
        "duration_days": 1,
    })
    if r.status_code != 422:
        _fail(f"Bogus platform should be 422, got {r.status_code}")
    _ok("POST campaign w/ bogus platform 'myspace' → 422")

    # Bogus budget body too
    r = client.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": -100})
    if r.status_code != 422:
        _fail(f"Negative cap should be 422, got {r.status_code}")
    _ok("PUT budget w/ negative cap → 422")


if __name__ == "__main__":
    main()
