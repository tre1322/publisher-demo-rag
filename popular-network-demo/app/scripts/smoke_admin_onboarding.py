"""Phase 1b smoke — onboard a real client from the admin console; nothing
from Quadd or the simulator reaches them.

Run with:  uv run python -m app.scripts.smoke_admin_onboarding

This is the Phase 1 "done when" test: "a test creates a second business and
confirms nothing from Quadd or the simulator reaches it."

Covers, in order:
  A. Only superusers reach the admin console
  B. Create a business + owner invite in one call
  C. The new business starts with a clean, honest first day (no Quadd text)
  D. The invited owner signs in and lands on their own business
  E. Demo-only actions are refused for a real client; still work for Quadd
  F. Admin edits: website → widget origins, plan, voice brief in the DB
  G. Superuser can open any business (and only real ones)
  H. Widget embed code names the right business
  I. A restart's backfills don't inject Quadd copy into the new business
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_admin_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

OWNER_EMAIL = "pat@smith-hardware.example.com"
OWNER_PW = "pat-correct-horse-battery"
# Strings that only exist in Quadd's seed or the old demos. None may appear
# anywhere in a real client's bootstrap.
QUADD_MARKERS = ("quadd", "trevor", "cottonwood", "new ulm", "westbrook auto", "citizen publishing",
                 "universal document extractor", "28 year", "28-year")

BRIEF = {
    "voice": "Plainspoken, neighborly, a little dry.",
    "amplify": [{"label": "Key cutting while you wait", "detail": "Under two minutes"}],
    "audience": "Homeowners and contractors in Windom",
    "value_prop": "The hardware store that knows your project",
    "_source": "smoke",
}


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def no_quadd(label: str, payload: object) -> None:
    text = json.dumps(payload).lower()
    hits = [m for m in QUADD_MARKERS if m in text]
    check(label, not hits, hits)


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)  # hermetic; see smoke_money_path

    with TestClient(app, follow_redirects=False) as admin:
        bootstrap_login(admin)  # superuser
        with TestClient(app, follow_redirects=False) as outsider, \
             TestClient(app, follow_redirects=False) as owner:
            _run(app, admin, outsider, owner)

    # I. restart → backfills run again on a DB that now has a real client.
    with TestClient(app, follow_redirects=False) as admin2:
        bootstrap_login(admin2)
        _after_restart(admin2)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 1b admin onboarding smoke green ✓")


STATE: dict = {}


def _run(app, admin, outsider, owner) -> None:  # noqa: C901 — linear smoke
    from app.db import SessionLocal
    from app.models import Business, BusinessUser, InventoryFacetHit, User
    from app.pwhash import hash_password

    # ---- A. superusers only ----------------------------------------------------
    print("\nA. admin console is superuser-only")
    with SessionLocal() as db:
        u = User(email="quadd-owner@example.com", password_hash=hash_password("quadd-owner-pw-123"),
                 display_name="Q Owner", is_superuser=False, is_active=True)
        db.add(u)
        db.flush()
        db.add(BusinessUser(user_id=u.id, business_id=1, role="owner"))
        db.commit()
    r = outsider.post("/api/auth/login", json={"email": "quadd-owner@example.com", "password": "quadd-owner-pw-123"})
    check("A0 a business owner signs in", r.status_code == 200, r.text)
    r = outsider.get("/api/admin/businesses")
    check("A1 business owner → admin API 403", r.status_code == 403, r.status_code)
    r = outsider.post("/api/admin/businesses", json={"name": "x", "owner": "y"})
    check("A2 business owner can't create businesses", r.status_code == 403, r.status_code)
    r = outsider.get("/admin")
    check("A3 /admin page sends non-admins to their dashboard", r.status_code == 302 and r.headers["location"] == "/",
          (r.status_code, r.headers.get("location")))
    r = admin.get("/admin")
    check("A4 /admin page serves for a superuser", r.status_code == 200 and b"Amplafai admin" in r.content, r.status_code)

    # ---- B. create + invite ------------------------------------------------------
    print("\nB. create a client")
    r = admin.post("/api/admin/businesses", json={
        "name": "Smith Hardware", "owner": "Pat Smith", "owner_email": OWNER_EMAIL,
        "location": "Windom, MN", "publisher": "Windom Herald", "phone": "(507) 555-0199",
        "tier": 4, "website": "smith-hardware.example.com/about",
    })
    check("B1 create → 200", r.status_code == 200, r.text[:300])
    body = r.json()
    biz = body["business"]
    STATE["biz_id"] = biz_id = biz["id"]
    check("B2 real client by default", biz["isDemo"] is False, biz)
    check("B3 plan label + price from the tier", biz["tierLabel"] == "Tier 4 — Inventory" and biz["monthlyPrice"] == 799, biz)
    check("B4 website normalized to an origin", biz["website"] == "https://smith-hardware.example.com", biz["website"])
    check("B5 widget allowed on the site with and without www",
          set(biz["allowedOrigins"]) == {"https://smith-hardware.example.com", "https://www.smith-hardware.example.com"},
          biz["allowedOrigins"])
    inv = body["invite"]
    check("B6 owner invite minted", inv and inv["email"] == OWNER_EMAIL and inv["role"] == "owner", inv)
    check("B7 email skipped without a key, link still returned",
          inv["emailDelivery"].get("sent") is False and inv["claimUrl"].startswith("/invite?token="), inv["emailDelivery"])
    lst = admin.get("/api/admin/businesses").json()["businesses"]
    row = next(b for b in lst if b["id"] == biz_id)
    check("B9 listing shows the pending owner invite", [i["email"] for i in row["pendingInvites"]] == [OWNER_EMAIL],
          row["pendingInvites"])
    r = admin.post("/api/admin/businesses", json={"name": "Bad Site", "owner": "X", "website": "not a site"})
    check("B10 bad website → 422 with a reason", r.status_code == 422 and "website" in r.json()["detail"], r.text)
    r = admin.post("/api/admin/businesses", json={"name": "Bad Tier", "owner": "X", "tier": 9})
    check("B11 bad tier → 422", r.status_code == 422, r.status_code)
    r = admin.post("/api/admin/businesses", json={"name": "Smith Hardware", "owner": "Other"})
    check("B12 duplicate name gets its own slug", r.status_code == 200 and r.json()["business"]["slug"] == "smith_hardware_2",
          r.json()["business"]["slug"] if r.status_code == 200 else r.text)

    # ---- C. clean first day -----------------------------------------------------
    print("\nC. clean, honest first day")
    r = admin.post(f"/api/admin/businesses/{biz_id}/open")
    check("C0 superuser opens the new business", r.status_code == 200, r.text)
    boot = admin.get("/api/bootstrap").json()
    check("C1 bootstrap is the new business", boot["business"]["id"] == biz_id and boot["business"]["name"] == "Smith Hardware")
    check("C2 no posts, approvals, chat", boot["posts"] == [] and boot["approvals"] == [] and boot["chat"] == [],
          (len(boot["posts"]), len(boot["approvals"]), len(boot["chat"])))
    check("C3 no reviews", boot["reviews"]["recent"] == [] and boot["reviews"]["pinned"] is None and boot["reviews"]["total"] == 0)
    check("C4 no canned insights", boot["performance"].get("insights") == [], boot["performance"].get("insights"))
    check("C5 empty marketing plan", boot["marketingPlan"]["audience"] == "" and boot["marketingPlan"]["proofPoints"] == [])
    check("C6 no fake connected accounts", boot["settings"]["connections"] == [], boot["settings"]["connections"])
    check("C7 no voice brief yet", boot["voiceBrief"] is None)
    stats = boot["stats"]
    check("C8 home tiles are real zeros", stats["posts"]["value"] == 0 and stats["reviews"]["value"] == 0
          and stats["engagement"]["value"] == "—", stats)
    check("C9 ad accounts all disconnected, no spend",
          set(boot["ads"]["connectionStatus"].values()) == {"disconnected"} and boot["ads"]["totalSpendCents"] == 0,
          boot["ads"])
    check("C10 no inventory, no chatbot conversations",
          boot["inventory"]["listingCount"] == 0 and boot["chatbot"]["conversationCount"] == 0)
    check("C11 reach copy names their own paper + town",
          any("Windom Herald" in t["description"] and "Windom" in t["description"] for t in boot["reach"]["tiers"]),
          [t["description"] for t in boot["reach"]["tiers"]][:1])
    with SessionLocal() as db:
        from app.models import ReachTier
        tiers = {r.tier_key: r.territories_json for r in db.query(ReachTier).filter(ReachTier.business_id == biz_id)}
        quadd_local = db.query(ReachTier).filter(ReachTier.business_id == 1, ReachTier.tier_key == "local").one()
    check("C11b Local reach is their own town only", tiers["local"] == ["windom"], tiers["local"])
    check("C11c wider tiers start at their town", tiers["regional"][0] == "windom" and "windom" not in tiers["regional"][1:],
          tiers["regional"])
    check("C11d Quadd's local reach unchanged (New Ulm)", quadd_local.territories_json == ["new_ulm"],
          quadd_local.territories_json)
    no_quadd("C12 nothing from Quadd anywhere in the bootstrap", boot)

    # ---- D. invited owner signs in ----------------------------------------------
    print("\nD. the owner claims the invite")
    token = inv["claimUrl"].split("token=", 1)[1]
    r = owner.post("/api/auth/invites/claim", json={"token": token, "password": OWNER_PW, "display_name": "Pat Smith"})
    check("D1 claim → 200", r.status_code == 200, r.text)
    boot = owner.get("/api/bootstrap").json()
    check("D2 owner lands on Smith Hardware", boot["business"]["id"] == biz_id, boot["business"]["name"])
    check("D3 owner is a real owner (not superuser)", boot["access"]["role"] == "owner" and boot["access"]["isSuperuser"] is False,
          boot["access"])
    no_quadd("D4 nothing from Quadd in the owner's bootstrap", boot)
    r = owner.post("/api/posts", json={"platform": "fb", "status": "pending", "title": "Spring hours",
                                       "draft": "Open till 8 starting Monday."})
    check("D5 owner's first post lands on their business", r.status_code == 200, r.text)
    with SessionLocal() as db:
        from app.models import Post
        check("D6 post row is Smith Hardware's", db.get(Post, r.json()["post"]["internalId"]).business_id == biz_id)

    # ---- E. demo-only actions -----------------------------------------------------
    print("\nE. demo-only actions refused for a real client")
    for method, path, payload in [
        ("POST", "/api/ads/tick", None),
        ("POST", "/api/performance/regenerate-insights", None),
        ("POST", "/api/chatbot/seed-fixtures", None),
        ("POST", "/api/ads/connections", {"platform": "fb_ig"}),
        ("POST", "/api/inventory/feeds", {"feed_type": "dealercenter", "location_label": "Main"}),
    ]:
        r = owner.request(method, path, json=payload)
        check(f"E1 {path} → 409 for a real client", r.status_code == 409 and "demo" in r.json()["detail"].lower(),
              f"{r.status_code} {r.text[:120]}")
    r = owner.post("/api/inventory/import-fixture", json={"feed_type": "generic_csv"})
    check("E2 sample inventory import refused (CSV required)", r.status_code == 422 and "csv" in r.json()["detail"].lower(),
          r.text[:160])
    r = owner.post("/api/inventory/import-fixture", json={"feed_type": "generic_csv", "csv_text": "price_cents\n100\n"})
    check("E3 CSV without a title column → 422", r.status_code == 422, r.text[:160])
    r = owner.post("/api/inventory/import-fixture", json={"feed_type": "generic_csv",
                                                          "csv_text": "title,price_cents\nGarden hose,abc\n"})
    check("E4 bad number names the row", r.status_code == 422 and "Row 2" in r.json()["detail"], r.text[:160])
    csv_text = "Title,Price_cents,attribute_brand\nCordless drill,12999,DeWalt\nShop vac,8999,Craftsman\n"
    r = owner.post("/api/inventory/import-fixture", json={"feed_type": "generic_csv", "csv_text": csv_text,
                                                          "location_label": "Main store"})
    check("E5 real CSV upload → 2 listings", r.status_code == 200 and r.json()["listingsCreated"] == 2, r.text[:200])
    with SessionLocal() as db:
        n_facets = db.query(InventoryFacetHit).filter(InventoryFacetHit.business_id == biz_id).count()
    check("E6 no sample search-visibility rows for a real upload", n_facets == 0, n_facets)
    inv_view = owner.get("/api/inventory").json()
    no_quadd("E7 inventory view has no sample data", inv_view)
    r = admin.post("/api/admin/businesses/1/open")
    check("E8 superuser back on Quadd", r.status_code == 200)
    r = admin.post("/api/ads/tick")
    check("E9 Quadd (demo) can still simulate", r.status_code == 200, r.text[:160])

    # ---- F. admin edits -----------------------------------------------------------
    print("\nF. admin edits")
    r = admin.put(f"/api/admin/businesses/{biz_id}", json={"website": "https://www.smithhw.example.com/", "tier": 3})
    check("F1 edit website + plan → 200", r.status_code == 200, r.text[:200])
    b = r.json()["business"]
    check("F2 origins follow the website", set(b["allowedOrigins"]) ==
          {"https://smithhw.example.com", "https://www.smithhw.example.com"}, b["allowedOrigins"])
    check("F3 plan follows the tier", b["tier"] == 3 and b["monthlyPrice"] == 150, b)
    r = admin.put(f"/api/admin/businesses/{biz_id}/voice-brief", json={"brief": {"nonsense": 1}})
    check("F4 malformed brief → 422", r.status_code == 422, r.text[:160])
    r = admin.put(f"/api/admin/businesses/{biz_id}/voice-brief", json={"brief": BRIEF})
    check("F5 store a brief → hasVoiceBrief", r.status_code == 200 and r.json()["business"]["hasVoiceBrief"] is True, r.text[:160])
    boot = owner.get("/api/bootstrap").json()
    check("F6 owner's bootstrap carries the brief", boot["voiceBrief"] == BRIEF, boot["voiceBrief"])
    check("F7 voice interview marked complete", boot["business"]["voiceInterview"] == "complete")
    from app.agent.system_prompt import build_system_prompt
    with SessionLocal() as db:
        prompt = build_system_prompt(db, biz_id)
    check("F8 agent prompt includes the brief", "Plainspoken, neighborly" in prompt and "Smith Hardware" in prompt)
    no_quadd("F9 agent prompt has nothing from Quadd", prompt)
    r = admin.put(f"/api/admin/businesses/{biz_id}", json={"demo": True})
    check("F10 demo flag can be set by an admin", r.json()["business"]["isDemo"] is True)
    r = admin.put(f"/api/admin/businesses/{biz_id}", json={"demo": False})
    check("F11 …and cleared", r.json()["business"]["isDemo"] is False)

    # ---- G. open any business ---------------------------------------------------
    print("\nG. open any business")
    r = admin.post("/api/admin/businesses/999/open")
    check("G1 open a missing business → 404", r.status_code == 404, r.status_code)
    r = admin.post("/api/auth/switch", json={"business_id": 999})
    check("G2 switch to a missing business → 404", r.status_code == 404, r.status_code)
    r = owner.post("/api/auth/switch", json={"business_id": 1})
    check("G3 a client can't switch into Quadd", r.status_code == 403, r.status_code)
    r = owner.post("/api/admin/businesses/1/open")
    check("G4 a client can't use admin open", r.status_code == 403, r.status_code)

    # ---- H. widget embed ------------------------------------------------------------
    print("\nH. widget embed code")
    r = admin.get(f"/api/admin/businesses/{biz_id}/embed")
    snippet = r.json()["snippet"]
    check("H1 snippet names this business", f"businessId: {biz_id}," in snippet, snippet)
    check("H2 snippet loads widget.js from this server", "/static/widget.js" in snippet)
    boot = owner.get("/api/bootstrap").json()
    check("H3 dashboard knows its own business id (Settings snippet)", boot["business"]["id"] == biz_id)

    with SessionLocal() as db:
        STATE["attention"] = db.get(__import__("app.models", fromlist=["DashboardNotices"]).DashboardNotices, biz_id).attention_json
        STATE["tier"] = db.get(Business, biz_id).tier


def _after_restart(admin) -> None:
    from app.db import SessionLocal
    from app.models import Business, DashboardNotices

    print("\nI. restart backfills leave the client alone")
    biz_id = STATE["biz_id"]
    with SessionLocal() as db:
        # Bump to tier 4: the legacy Quadd backfill only rewrites tier ≥4 rows.
        db.get(Business, biz_id).tier = 4
        db.commit()
    from app.main import _startup
    _startup()
    with SessionLocal() as db:
        notices = db.get(DashboardNotices, biz_id)
        quadd = db.get(Business, 1)
        check("I1 no Quadd inventory notice injected", notices.attention_json == STATE["attention"],
              [a.get("title") for a in notices.attention_json])
        check("I2 Quadd is still the demo account", quadd.is_demo is True)
        check("I3 client is still real", db.get(Business, biz_id).is_demo is False)
        check("I4 Quadd's brief moved into the DB", isinstance(quadd.voice_brief_json, dict) and quadd.voice_brief_json)
    r = admin.post(f"/api/admin/businesses/{biz_id}/open")
    check("I5 superuser reopens the client after restart", r.status_code == 200, r.status_code)
    boot = admin.get("/api/bootstrap").json()
    check("I6 bootstrap is the client's", boot["business"]["id"] == biz_id, boot["business"]["name"])
    no_quadd("I7 client bootstrap still clean after restart", boot)


if __name__ == "__main__":
    main()
