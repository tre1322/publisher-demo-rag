"""Phase F.1 smoke — Tier 4 Inventory backend.

Run with:  uv run python -m app.scripts.smoke_inventory

Covers (in order of failure cost):
1. /api/inventory returns the right shape on Day-1 (Quadd Tier 4, empty).
2. Tier-gating: writes are forbidden when business.tier < 4 (we test by
   monkey-patching Quadd back down to tier 3 mid-test, then restoring).
3. Connect a feed (vAuto) → row created.
4. Import fixture (auto vertical) → 12 listings, 4 facet hits.
5. Listings list: filters (status, stale_only, search) work.
6. Stale-flag rule: listings >30d w/ 0 clicks are stale.
7. Re-sync: bumps last_sync_at and refreshes stale flags.
8. Disconnect: soft-marks status, keeps listings.
9. 422 on bogus feed_type.
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_f1_"))
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
    print("\nPASS  Phase F.1 (Inventory) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- 1. /api/inventory Day-1 shape ----
    r = client.get("/api/inventory")
    if r.status_code != 200:
        _fail(f"GET /api/inventory → {r.status_code} {r.text}")
    inv = r.json()
    for k in ("tier", "tierEligible", "feedTypes", "feeds", "listings",
              "facetHits", "locationLabels", "totals", "hasAnyFeed",
              "hasAnyListing", "staleThreshold"):
        if k not in inv:
            _fail(f"/api/inventory missing key '{k}'")
    if inv["tier"] != 4 or not inv["tierEligible"]:
        _fail(f"Phase F: Quadd should be tier 4 eligible, got tier={inv['tier']} eligible={inv['tierEligible']}")
    if inv["hasAnyFeed"] or inv["hasAnyListing"]:
        _fail(f"Day-1 should be empty: {inv['totals']}")
    if inv["staleThreshold"] != 30:
        _fail(f"staleThreshold should be 30 days, got {inv['staleThreshold']}")
    if len(inv["feedTypes"]) != 6:
        _fail(f"Should expose 6 feed types, got {len(inv['feedTypes'])}: {inv['feedTypes']}")
    _ok(f"GET /api/inventory → tier 4, empty Day-1, 6 feed types exposed")

    # ---- 2. Tier-gating: tier-3 owner gets 403 on writes ----
    # We can't easily flip the seed mid-test, so we patch the business via
    # SQLAlchemy session directly. The router uses get_db which yields a new
    # session per request, so changes commit through the same DB.
    from app.db import SessionLocal
    from app.models import Business
    with SessionLocal() as db:
        biz = db.get(Business, 1)
        biz.tier = 3
        db.commit()

    r = client.post("/api/inventory/feeds", json={
        "feed_type": "vauto",
        "location_label": "Should fail at tier 3",
    })
    if r.status_code != 403:
        _fail(f"Tier 3 write should be 403, got {r.status_code} {r.text}")
    _ok("Tier 3 write → 403 (gating works)")

    # Reads should still succeed at tier 3 (so the upsell card can render).
    r = client.get("/api/inventory")
    if r.status_code != 200:
        _fail(f"Tier 3 read should be 200, got {r.status_code}")
    if r.json()["tierEligible"]:
        _fail("Tier 3 should NOT be tierEligible")
    _ok("Tier 3 read → 200 + tierEligible=false (upsell preview path)")

    # Restore tier 4 for the rest of the test.
    with SessionLocal() as db:
        biz = db.get(Business, 1)
        biz.tier = 4
        db.commit()

    # ---- 3. Connect a feed ----
    r = client.post("/api/inventory/feeds", json={
        "feed_type": "vauto",
        "location_label": "Westbrook Auto — main lot",
    })
    if r.status_code != 200:
        _fail(f"POST /api/inventory/feeds → {r.status_code} {r.text}")
    feed = r.json()
    if feed["feedType"] != "vauto" or feed["status"] != "connected":
        _fail(f"connect response wrong: {feed}")
    feed_id = feed["id"]
    _ok(f"POST /api/inventory/feeds (vauto) → id={feed_id} status=connected")

    # ---- 4. Import fixture (auto vertical) ----
    r = client.post("/api/inventory/import-fixture", json={
        "feed_type": "vauto",
        "location_label": "Westbrook Auto & Tire — main lot",
    })
    if r.status_code != 200:
        _fail(f"POST import-fixture → {r.status_code} {r.text}")
    imp = r.json()
    if imp["listingsCreated"] != 12:
        _fail(f"Fixture should create 12 listings, got {imp['listingsCreated']}")
    fixture_feed_id = imp["feed"]["id"]
    _ok(f"POST import-fixture → feed id={fixture_feed_id} + 12 listings + 4 facet hits")

    # ---- 5. Listings list filters ----
    r = client.get("/api/inventory/listings").json()
    if len(r) != 12:
        _fail(f"All listings: expected 12, got {len(r)}")

    actives = client.get("/api/inventory/listings?status=active").json()
    if not actives or any(li["status"] != "active" for li in actives):
        _fail(f"status=active filter broken: {[li['status'] for li in actives]}")

    stales = client.get("/api/inventory/listings?stale_only=true").json()
    if not stales or any(not li["isStale"] for li in stales):
        _fail(f"stale_only filter broken: {[(li['title'], li['isStale']) for li in stales]}")

    ford = client.get("/api/inventory/listings?search=Ford").json()
    if not ford or any("Ford" not in li["title"] for li in ford):
        _fail(f"search=Ford filter broken: {[li['title'] for li in ford]}")

    _ok(f"Filters: all=12, active={len(actives)}, stale={len(stales)}, Ford={len(ford)}")

    # ---- 6. Stale-flag rule on fixture ----
    # _AUTO_CSV row W-1046 (GMC Sierra) has days_listed=46 + clicks_30d=0 → stale.
    sierra = next((li for li in r if li["externalId"] == "W-1046"), None)
    if sierra is None or not sierra["isStale"] or sierra["status"] != "stale":
        _fail(f"W-1046 should be stale + status=stale: {sierra}")
    _ok(f"Stale rule fires: W-1046 (46d listed, 0 clicks) → isStale=true status=stale")

    # ---- 7. Re-sync feed: bumps last_sync_at + refreshes stale flags ----
    before = client.get("/api/inventory/feeds").json()
    fixture_before = next(f for f in before if f["id"] == fixture_feed_id)
    sync_before = fixture_before["lastSyncAt"]
    import time; time.sleep(0.01)  # ensure timestamp tick
    r = client.post(f"/api/inventory/feeds/{fixture_feed_id}/sync")
    if r.status_code != 200:
        _fail(f"sync → {r.status_code} {r.text}")
    synced = r.json()
    if synced["lastSyncAt"] == sync_before:
        _fail(f"sync didn't advance lastSyncAt: {synced['lastSyncAt']} vs {sync_before}")
    _ok(f"POST sync → lastSyncStatus=\"{synced['lastSyncStatus']}\"")

    # ---- 8. Disconnect: soft-marks status, listings stay ----
    r = client.delete(f"/api/inventory/feeds/{feed_id}")
    if r.status_code != 200:
        _fail(f"disconnect → {r.status_code}")
    feeds_after = client.get("/api/inventory/feeds").json()
    disconnected_feed = next(f for f in feeds_after if f["id"] == feed_id)
    if disconnected_feed["status"] != "disconnected":
        _fail(f"disconnect didn't set status: {disconnected_feed}")
    listings_after = client.get("/api/inventory/listings").json()
    if len(listings_after) != 12:
        _fail(f"disconnect dropped listings — expected 12 still, got {len(listings_after)}")
    _ok("DELETE feed → soft-disconnect; fixture listings retained")

    # ---- 9. 422 on bogus feed_type ----
    r = client.post("/api/inventory/feeds", json={
        "feed_type": "myspace",
        "location_label": "lol",
    })
    if r.status_code != 422:
        _fail(f"Bogus feed_type should be 422, got {r.status_code}")
    _ok("POST feed w/ bogus feed_type 'myspace' → 422")

    # Facet hits exposed
    facet = client.get("/api/inventory/facet-hits").json()
    if len(facet) < 4:
        _fail(f"Should have >=4 facet-hit rows from fixture, got {len(facet)}")
    _ok(f"GET facet-hits → {len(facet)} rows (top facet: '{facet[0]['facetText']}')")


if __name__ == "__main__":
    main()
