"""Phase A smoke — assert /api/bootstrap returns the shape dashboard.html expects.

Per global CLAUDE.md test-before-handoff: lint passing and module imports are
NOT sufficient. This script exercises the real code path with TestClient.

Run with:  uv run python -m app.scripts.smoke_bootstrap
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

# Windows default code page (cp1252) can't print non-ASCII. Force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# Isolate from the dev DB — point at a fresh temp file BEFORE importing the app.
_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_a_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

from fastapi.testclient import TestClient

from app.main import app


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def main() -> None:
    # Context manager triggers FastAPI startup → init_db + seed against the
    # temp DB selected at module load.
    with TestClient(app) as client:
        _run_assertions(client)


def _run_assertions(client) -> None:
    resp = client.get("/api/bootstrap")
    if resp.status_code != 200:
        _fail(f"/api/bootstrap returned {resp.status_code} — expected 200")
    data = resp.json()

    expected_keys = {
        "business", "stats", "attention", "weekRecap",
        "posts", "approvals", "performance", "reviews",
        "marketingPlan", "chat", "settings",
        # Phase D/E/F additions:
        "reach", "ads",
        "inventory", "billing", "chatbot",
    }
    missing = expected_keys - set(data.keys())
    if missing:
        _fail(f"missing top-level keys: {sorted(missing)}")
    _ok("top-level keys present")

    # Phase F: Quadd is bumped to Tier 4 ($799) — confirm the bump took.
    if biz_stub := data["business"]:
        if biz_stub.get("tier") != 4:
            _fail(f"Phase F: business.tier should be 4 (Quadd bumped), got {biz_stub.get('tier')}")
        if biz_stub.get("monthlyPrice") != 799:
            _fail(f"Phase F: business.monthlyPrice should be 799, got {biz_stub.get('monthlyPrice')}")
    # Phase F.1 inventory slim slice
    inv = data["inventory"]
    for k in ("tierEligible", "feedCount", "listingCount", "staleCount", "hasAnyFeed"):
        if k not in inv:
            _fail(f"bootstrap.inventory.{k} missing")
    if not inv["tierEligible"] or inv["feedCount"] != 0 or inv["hasAnyFeed"]:
        _fail(f"Day-1 inventory should be eligible (tier 4) + empty: {inv}")
    _ok(f"inventory slim slice → tierEligible={inv['tierEligible']} feeds={inv['feedCount']} listings={inv['listingCount']}")
    # Phase F.2 billing slim slice
    bil = data["billing"]
    for k in ("tier", "monthlyPrice", "invoiceCount", "currentUsage", "stripeEnabled"):
        if k not in bil:
            _fail(f"bootstrap.billing.{k} missing")
    if bil["stripeEnabled"] is not False:
        _fail(f"billing.stripeEnabled should be False for the demo: {bil}")
    _ok(f"billing slim slice → tier={bil['tier']} monthlyPrice={bil['monthlyPrice']} invoices={bil['invoiceCount']}")
    # Phase F.3 chatbot slim slice (+ Phase G hasAnyKey)
    cb = data["chatbot"]
    for k in ("tierEligible", "conversationCount", "escalationCount", "hasAnyKey"):
        if k not in cb:
            _fail(f"bootstrap.chatbot.{k} missing")
    if cb["hasAnyKey"] is not False:
        _fail(f"Day-1 chatbot.hasAnyKey should be False (no keys minted yet): {cb}")
    _ok(f"chatbot slim slice → eligible={cb['tierEligible']} convos={cb['conversationCount']} hasAnyKey={cb['hasAnyKey']}")

    biz = data["business"]
    for k in ("name", "owner", "ownerInitials", "publisher", "tier", "tierLabel"):
        if k not in biz:
            _fail(f"business.{k} missing")
    if biz["name"] != "Quadd.ai":
        _fail(f"business.name = {biz['name']!r}, expected Quadd.ai")
    _ok(f"business → {biz['name']} ({biz['tierLabel']})")

    # Quadd Day-1 seed: 3 posts (1 published + 1 draft + 1 pending).
    if len(data["posts"]) < 3:
        _fail(f"posts: {len(data['posts'])} (expected >= 3)")
    _ok(f"posts: {len(data['posts'])} rows")

    # Quadd Day-1 seed: 4 approvals (a1/a2/a3 posts + a4 review response).
    if len(data["approvals"]) < 4:
        _fail(f"approvals: {len(data['approvals'])} (expected >= 4)")
    _ok(f"approvals: {len(data['approvals'])} rows")

    revs = data["reviews"]
    for k in ("aggregate", "total", "sparkline", "sparklineLabels", "pinned", "recent"):
        if k not in revs:
            _fail(f"reviews.{k} missing")
    if revs["pinned"] is None or revs["pinned"].get("author") != "Citizen Publishing (early user)":
        _fail(f"reviews.pinned should be the Citizen Publishing testimonial, got {revs.get('pinned')}")
    _ok(f"reviews → {revs['aggregate']}★ / {revs['total']} total / pinned={revs['pinned']['author']}")

    perf = data["performance"]
    for k in ("reach", "engagement", "followers", "ctr", "channelMix", "topPosts", "insights",
              "dailyReachCurrent", "dailyReachPrev"):
        if k not in perf:
            _fail(f"performance.{k} missing")
    if len(perf["dailyReachCurrent"]) != 30 or len(perf["dailyReachPrev"]) != 30:
        _fail(f"performance daily-reach arrays should each have 30 entries")
    _ok(f"performance → reach={perf['reach']['value']} dailyReach=30+30")

    plan = data["marketingPlan"]
    for k in ("audience", "valueProp", "switching", "customerLanguage", "proofPoints", "channels", "q3Goals"):
        if k not in plan:
            _fail(f"marketingPlan.{k} missing")
    _ok(f"marketingPlan → audience={plan['audience'][:48]}…")

    # Quadd Day-1: chat is empty by design — voice carries via system prompt's
    # voice brief, not via seeded messages. Asserting >= 0 keeps the shape
    # check while documenting the intentional Day-1 zero.
    if not isinstance(data["chat"], list):
        _fail(f"chat should be a list, got {type(data['chat']).__name__}")
    _ok(f"chat turns: {len(data['chat'])} (Day-1 expects 0)")

    if len(data["attention"]) < 4:
        _fail(f"attention items: {len(data['attention'])} (expected >= 4)")
    if len(data["weekRecap"]) < 5:
        _fail(f"weekRecap days: {len(data['weekRecap'])} (expected >= 5)")
    _ok(f"attention={len(data['attention'])} weekRecap={len(data['weekRecap'])}")

    # voiceBrief: should load for the Quadd seed (file at voice-briefs/quadd_ai.json)
    vb = data.get("voiceBrief")
    if vb is None:
        _fail("voiceBrief is None — expected the synthesized Quadd brief to load")
    for key in ("voice", "amplify", "maintain", "mute", "customer_language", "proof_points"):
        if key not in vb:
            _fail(f"voiceBrief.{key} missing")
    if not vb["amplify"] or not vb["customer_language"]:
        _fail(f"voiceBrief amplify/customer_language unexpectedly empty: {vb}")
    _ok(f"voiceBrief → amplify={len(vb['amplify'])} mute={len(vb['mute'])} customerLanguage={len(vb['customer_language'])}")

    sett = data["settings"]
    if "cadence" not in sett or "connections" not in sett:
        _fail("settings missing cadence/connections")
    if len(sett["connections"]) != 3:
        _fail(f"settings.connections should have 3 entries, got {len(sett['connections'])}")
    _ok(f"settings → cadence={sett['cadence']} connections={len(sett['connections'])}")

    # Confirm dashboard.html is reachable through the same server
    html_resp = client.get("/dashboard.html")
    if html_resp.status_code != 200:
        _fail(f"dashboard.html: {html_resp.status_code}")
    if b"Popular Network" not in html_resp.content:
        _fail("dashboard.html does not look like the demo")
    _ok(f"dashboard.html served ({len(html_resp.content)} bytes)")

    print("\nPASS  Phase A smoke green ✓")


if __name__ == "__main__":
    main()
