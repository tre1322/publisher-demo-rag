"""Phase 5a smoke — the safety rules that must hold before any ad-platform
key goes onto the production server.

Run with:  uv run python -m app.scripts.smoke_ad_safety

A fake Meta connection stands in for the real API (Phase 5c builds that).

  A. Ad-account tokens are encrypted in the database
  B. Campaigns are created PAUSED; turning one on is a separate, logged step
  C. Pause / restart / cancel reach the platform; failures fall back to Amplafai
  D. "Pause all paid ads" for one business, and what it blocks
  E. Stopping spend works even when billing has made the account read-only
  F. Amplafai's switch for every business
  G. Real spend is read from the platform on a schedule; 100% of a cap pauses
  H. The action log
  I. LinkedIn payloads: created PAUSED, status changes, daily results
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_ad_safety_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["POPULAR_NOTIFY_LOOP"] = "0"
os.environ["POPULAR_AD_SYNC_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

OWNER = ("ana@hardware.example.com", "ana-correct-horse-battery")
OTHER = ("bo@bakery.example.com", "bo-correct-horse-battery")
OUTBOX: list[dict] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


class FakeMeta:
    """Stands in for the Meta adapter. One shared call log per business."""
    platform = "fb_ig"
    calls: list[tuple] = []
    fail: set[str] = set()
    results: list = []
    seq = [0]

    def __init__(self, business_id):
        self.business_id = business_id

    def _maybe_fail(self, what):
        from app.ad_platforms import PlatformError

        if what in FakeMeta.fail:
            raise PlatformError(f"(#100) {what} rejected in test")

    def create_paused(self, *, name, daily_budget_cents, duration_days, audience):
        self._maybe_fail("create")
        FakeMeta.seq[0] += 1
        FakeMeta.calls.append(("create_paused", self.business_id, name, daily_budget_cents, duration_days, audience))
        return f"23850000000{FakeMeta.seq[0]:04d}"

    def activate(self, external_id, *, ends_at):
        self._maybe_fail("activate")
        FakeMeta.calls.append(("activate", external_id, ends_at))

    def pause(self, external_id):
        self._maybe_fail("pause")
        FakeMeta.calls.append(("pause", external_id))

    def cancel(self, external_id):
        self._maybe_fail("cancel")
        FakeMeta.calls.append(("cancel", external_id))

    def daily_results(self, external_ids, start, end):
        self._maybe_fail("results")
        FakeMeta.calls.append(("results", tuple(external_ids), start, end))
        return [r for r in FakeMeta.results if r.external_id in external_ids]

    def close(self):
        pass


CONNECTED: set[int] = set()


def fake_factory(db, business_id):
    return FakeMeta(business_id) if business_id in CONNECTED else None


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.managed_ads as ma
    from app import ad_platforms
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    os.environ["ADS_OPS_EMAIL"] = "ops@amplafai.example.com"
    ad_platforms.register("fb_ig", fake_factory)
    with patch.object(ma, "dispatch", OUTBOX.extend), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as other:
        bootstrap_login(admin)
        _run(admin, owner, other)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 5a ad safety smoke green ✓")


def _claim(client, invite, password):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": "x",
                                                         "accept_terms": True})
    check(f"claim {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner, other) -> None:  # noqa: C901 — linear smoke
    from sqlalchemy import text

    from app import ad_imports, ad_platforms, ad_sync
    from app import token_crypto as tc
    from app.agent import tools
    from app.db import SessionLocal, current_tenant_id, engine
    from app.models import AdActionLog, AdCampaign, AdConnection, AdOpsRequest, Approval, Business, Post

    def mk(name, owner_name, email, pw, client, tier=3):
        r = admin.post("/api/admin/businesses", json={"name": name, "owner": owner_name, "owner_email": email,
                                                      "location": "Windom, MN", "tier": tier, "billing_mode": "outside"})
        bid = r.json()["business"]["id"]
        _claim(client, r.json()["invite"], pw)
        return bid

    biz_id = mk("Main St Hardware", "Ana Ruiz", *OWNER, owner)
    other_id = mk("Corner Bakery", "Bo Lind", *OTHER, other)

    def camp(cid, client=owner):
        return next(c for c in client.get("/api/ads").json()["campaigns"] if c["id"] == cid)

    def create(client=owner, **kw):
        body = {"platform": "fb_ig", "name": "Fall paint sale", "daily_budget_cents": 2000, "duration_days": 7,
                "target_audience": "Homeowners within 20 miles"}
        body.update(kw)
        return client.post("/api/ads/campaigns", json=body)

    def logs(business_id=biz_id, action=None):
        with SessionLocal() as db:
            q = db.query(AdActionLog).filter(AdActionLog.business_id == business_id)
            if action:
                q = q.filter(AdActionLog.action == action)
            return q.order_by(AdActionLog.id).all()

    def calls(kind):
        return [c for c in FakeMeta.calls if c[0] == kind]

    def open_req(cid):
        with SessionLocal() as db:
            return db.query(AdOpsRequest).filter(AdOpsRequest.campaign_id == cid, AdOpsRequest.status == "open").one_or_none()

    # ---- A ------------------------------------------------------------------
    print("\nA. tokens are encrypted")
    with SessionLocal() as db:
        db.add(AdConnection(business_id=biz_id, platform="linkedin", account_label="smoke-mock", oauth_token="mock_token_1",
                            status="disconnected"))
        db.commit()
        check("A1 without a key, the demo's simulated tokens still store", True)
        db.add(AdConnection(business_id=biz_id, platform="tiktok", account_label="smoke-real", oauth_token="EAAG-real-token",
                            status="connected"))
        try:
            db.commit()
            stored, err = True, ""
        except Exception as e:  # SQLAlchemy wraps the TypeDecorator's TokenKeyMissing
            db.rollback()
            stored, err = False, str(e)
    check("A2 without a key, a real token is refused (never stored in plain text)",
          stored is False and "TOKEN_ENCRYPTION_KEY" in err, err[:200])
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    os.environ["TOKEN_ENCRYPTION_KEY"] = key
    with SessionLocal() as db:
        db.add(AdConnection(business_id=biz_id, platform="tiktok", account_label="smoke-real", oauth_token="EAAG-real-token",
                            refresh_token="refresh-real", status="connected"))
        db.commit()
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT oauth_token, refresh_token FROM ad_connections WHERE account_label='smoke-real'")).one()
    check("A3 with the key, the database holds only ciphertext", raw[0].startswith("enc:v1:") and "EAAG" not in raw[0]
          and raw[1].startswith("enc:v1:"), raw)
    with SessionLocal() as db:
        row = db.query(AdConnection).filter(AdConnection.account_label == "smoke-real").one()
        check("A4 ...and the app reads the real token back", row.oauth_token == "EAAG-real-token" and row.refresh_token == "refresh-real")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ad_connections (business_id, platform, account_label, oauth_token, status) "
                          "VALUES (:b, 'google_ads', 'legacy', 'plain-legacy-token', 'connected')"), {"b": other_id})
    n = tc.encrypt_existing(engine)
    with engine.connect() as conn:
        legacy = conn.execute(text("SELECT oauth_token FROM ad_connections WHERE account_label='legacy'")).scalar()
        mock = conn.execute(text("SELECT oauth_token FROM ad_connections WHERE account_label='smoke-mock'")).scalar()
    check("A5 startup encrypts tokens stored before encryption existed", n >= 2 and legacy.startswith("enc:v1:")
          and mock.startswith("enc:v1:"), (n, legacy, mock))
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    with SessionLocal() as db:
        row = db.query(AdConnection).filter(AdConnection.account_label == "smoke-real").one()
        unreadable = row.oauth_token
    r = owner.get("/api/ads")
    check("A6 a wrong key makes the token unusable but pages still load", unreadable is None and r.status_code == 200, r.status_code)
    os.environ["TOKEN_ENCRYPTION_KEY"] = key

    # ---- B ------------------------------------------------------------------
    print("\nB. created paused, turned on by a person")
    CONNECTED.add(biz_id)
    r = create()
    c1 = r.json()
    check("B1 with Meta connected, the campaign is created on Meta, paused",
          r.status_code == 200 and calls("create_paused") and c1["externalCampaignId"].startswith("23850")
          and c1["status"] == "scheduled" and c1["stage"] == "ready_to_turn_on" and not calls("activate"), c1)
    check("B2 the audience and budget went to the platform", calls("create_paused")[-1][3:] == (2000, 7, "Homeowners within 20 miles"),
          calls("create_paused"))
    check("B3 no hand request for Amplafai (the API did it)", open_req(c1["id"]) is None)
    check("B4 the creation is in the action log", logs(action="create_paused")[-1].ok and "created paused" in logs(action="create_paused")[-1].detail)
    FakeMeta.fail = {"activate"}
    r = owner.post(f"/api/ads/campaigns/{c1['id']}/turn-on")
    check("B5 a platform refusal on turn-on → 502, still not running, failure logged",
          r.status_code == 502 and camp(c1["id"])["status"] == "scheduled" and not logs(action="activate")[-1].ok, r.text)
    FakeMeta.fail = set()
    r = owner.post(f"/api/ads/campaigns/{c1['id']}/turn-on")
    c = camp(c1["id"])
    check("B6 Turn on: Meta activates it, the end date is sent, it's live",
          r.status_code == 200 and c["status"] == "active" and c["stage"] == "live" and c["launchedAt"]
          and calls("activate")[-1][1] == c1["externalCampaignId"]
          and abs((calls("activate")[-1][2] - datetime.utcnow()).days - 7) <= 1 and "Turned on" in r.json()["message"], (r.text, c))
    check("B7 ...and it's logged with who did it", logs(action="activate")[-1].ok and logs(action="activate")[-1].actor == OWNER[0],
          logs(action="activate")[-1].actor)
    r = owner.post(f"/api/ads/campaigns/{c1['id']}/turn-on")
    check("B8 turning on twice → 409", r.status_code == 409, r.text)
    FakeMeta.fail = {"create"}
    before = len(calls("create_paused"))
    r = create(name="Broken one")
    with SessionLocal() as db:
        n_broken = db.query(AdCampaign).filter(AdCampaign.name == "Broken one").count()
    check("B9 a failed creation → 502, nothing half-made, failure logged",
          r.status_code == 502 and n_broken == 0 and len(calls("create_paused")) == before
          and not logs(action="create_paused")[-1].ok, r.text)
    FakeMeta.fail = set()
    owner.put("/api/settings/ad-autonomy", json={"enabled": True})
    owner.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": 50000})
    with SessionLocal() as db:
        db.add(Post(business_id=biz_id, date="2026-10-03", platform="fb", title="Paint sale", draft="x", status="published"))
        db.commit()
        post_id = db.query(Post).filter(Post.business_id == biz_id).first().id
        res = tools._exec_schedule_boost(db, biz_id, {"post_id": post_id, "platform": "meta", "daily_budget_cents": 1000,
                                                      "duration_days": 3, "audience_hint": "DIYers"})
        db.commit()
        agent_c = db.get(AdCampaign, res.attachment["campaignId"])
        agent_state = (agent_c.status, agent_c.launched_at, agent_c.external_campaign_id)
    check("B10 even with autonomy on, the agent only creates it paused; a person turns it on",
          res.attachment["tierMode"] == "autonomous" and agent_state[0] == "scheduled" and agent_state[1] is None
          and agent_state[2] and "Turn on" in res.text, (res.text, agent_state))

    # ---- C ------------------------------------------------------------------
    print("\nC. pause, restart, cancel through the API")
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "paused"})
    c = camp(c1["id"])
    check("C1 Pause calls Meta and it's paused at once (no hand request)",
          r.status_code == 200 and r.json()["message"] == "Paused on Meta." and c["status"] == "paused"
          and calls("pause")[-1][1] == c1["externalCampaignId"] and open_req(c1["id"]) is None, (r.text, c))
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("C2 Restart calls Meta and it's running again", r.status_code == 200 and camp(c1["id"])["status"] == "active"
          and "Running again" in r.json()["message"], r.text)
    FakeMeta.fail = {"pause"}
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "paused"})
    c = camp(c1["id"])
    check("C3 if Meta refuses, it falls back to Amplafai pausing it by hand (and says so)",
          r.status_code == 200 and "didn't take the change" in r.json()["message"] and "Pause requested" in r.json()["message"]
          and c["stage"] == "pause_requested" and c["status"] == "active" and not logs(action="pause")[-1].ok, (r.text, c))
    FakeMeta.fail = set()
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("C4 'Keep running' still withdraws a hand request (no platform call)",
          "withdrawn" in r.json()["message"] and camp(c1["id"])["stage"] == "live", r.text)
    c2 = create(name="Holiday hours").json()
    n_pause = len(calls("pause"))
    r = owner.put(f"/api/ads/campaigns/{c2['id']}", json={"status": "paused"})
    check("C5 holding a never-turned-on campaign doesn't call the platform (nothing is spending)",
          r.status_code == 200 and camp(c2["id"])["stage"] == "held" and len(calls("pause")) == n_pause, r.text)
    r = owner.put(f"/api/ads/campaigns/{c2['id']}", json={"status": "active"})
    check("C6 ...and restarting it makes it ready to turn on again (not running)",
          camp(c2["id"])["stage"] == "ready_to_turn_on" and "Ready to turn on" in r.json()["message"], r.text)
    r = owner.delete(f"/api/ads/campaigns/{c2['id']}")
    check("C7 cancelling it archives it on Meta and says nothing was spent",
          r.status_code == 200 and calls("cancel")[-1][1] == c2["externalCampaignId"] and "never turned on" in r.json()["message"]
          and camp(c2["id"])["status"] == "cancelled", r.text)

    # ---- D ------------------------------------------------------------------
    print("\nD. pause all paid ads (one business)")
    # a hand-run Google campaign that's live, and one waiting for launch
    g1 = create(platform="google_ads", name="Contractor night").json()
    q = next(x for x in admin.get("/api/admin/ads/requests").json()["requests"] if x["campaign"]["id"] == g1["id"])
    admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": "21456789012"})
    g2 = create(platform="google_ads", name="Snow blowers").json()
    ready = create(name="Ready one").json()
    n_pause = len(calls("pause"))
    r = owner.post("/api/ads/halt")
    res = r.json()
    check("D1 one click: running Meta campaigns paused through the API, hand-run ones sent to Amplafai, others held",
          r.status_code == 200 and res["counts"] == {"paused": 1, "requested": 1, "held": 3}
          and len(calls("pause")) == n_pause + 1, res)
    check("D2 statuses: Meta paused; Google keeps running until Amplafai pauses it; nothing else can start",
          camp(c1["id"])["status"] == "paused" and camp(g1["id"])["stage"] == "pause_requested"
          and camp(g2["id"])["stage"] == "held" and camp(ready["id"])["stage"] == "held", [camp(x["id"])["stage"] for x in (c1, g1, g2, ready)])
    check("D3 the hand request says why", open_req(g1["id"]).source == "halt", open_req(g1["id"]).source)
    s = owner.get("/api/ads").json()
    check("D4 the Ads screen shows who paused everything and when", s["halt"]["scope"] == "business" and s["halt"]["by"] == OWNER[0], s["halt"])
    r = create(name="New while paused")
    check("D5 nothing new can be created", r.status_code == 409 and "Paid ads are paused" in r.json()["detail"], r.text)
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("D6 nothing can restart", r.status_code == 409, r.text)
    owner.put(f"/api/ads/campaigns/{ready['id']}", json={"status": "active"})
    r = owner.post(f"/api/ads/campaigns/{ready['id']}/turn-on")
    check("D7 nothing can be turned on", r.status_code == 409 and camp(ready["id"])["status"] != "active", r.text)
    with SessionLocal() as db:
        res = tools._exec_schedule_boost(db, biz_id, {"post_id": post_id, "platform": "meta", "daily_budget_cents": 1000,
                                                      "duration_days": 3, "audience_hint": "x"})
        db.rollback()
    check("D8 the agent can't schedule spend either", res.is_error and res.attachment["reason"] == "halted", res.text)
    with SessionLocal() as db:
        pc = AdCampaign(business_id=biz_id, platform="fb_ig", name="Proposal", daily_budget_cents=1000, duration_days=3,
                        planned_total_cents=3000, status="pending_approval", origin="agent_proposed")
        db.add(pc)
        db.flush()
        db.add(Approval(business_id=biz_id, external_id=f"boost-{pc.id}", kind="boost", platform="fb_ig", title="Boost",
                        draft="x", payload_json={"action": "boost", "campaign_id": pc.id}))
        db.commit()
        appr_id = db.query(Approval).filter(Approval.external_id == f"boost-{pc.id}").one().id
    r = owner.post(f"/api/approvals/{appr_id}/decide", json={"decision": "approve"})
    check("D9 approving a waiting proposal is refused too", r.status_code == 409, r.text)
    r = other.post("/api/ads/halt")
    check("D10 another business's switch is its own", r.status_code == 200 and owner.get("/api/ads").json()["halt"]["by"] == OWNER[0])
    other.post("/api/ads/allow")
    r = owner.post("/api/ads/allow")
    check("D11 the owner allows paid ads again; nothing restarts on its own",
          r.status_code == 200 and r.json()["ok"] and camp(c1["id"])["status"] == "paused", r.text)

    # ---- E ------------------------------------------------------------------
    print("\nE. stopping spend never waits on billing")
    owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    with SessionLocal() as db:
        db.get(Business, biz_id).billing_mode = "stripe"
        db.commit()
    with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x"}):
        r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "paused"})
        check("E1 with payment overdue, Pause still works", r.status_code == 200 and camp(c1["id"])["status"] == "paused", r.text)
        r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
        check("E2 ...but Restart waits for payment (402)", r.status_code == 402, r.text)
        r = owner.post("/api/ads/halt")
        check("E3 Pause all paid ads still works", r.status_code == 200, r.text)
        r = create(name="x")
        check("E4 creating needs payment (402)", r.status_code == 402, r.text)
    with SessionLocal() as db:
        db.get(Business, biz_id).billing_mode = "outside"
        db.commit()
    owner.post("/api/ads/allow")
    owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})

    # ---- F ------------------------------------------------------------------
    print("\nF. Amplafai's switch for everyone")
    CONNECTED.add(other_id)
    oc = create(client=other, name="Bread week").json()
    other.post(f"/api/ads/campaigns/{oc['id']}/turn-on")
    check("F0 the other business has a live Meta campaign", camp(oc["id"], other)["status"] == "active")
    check("F1 owners can't reach the global switch", owner.post("/api/admin/ads/halt-all").status_code in (401, 403))
    r = admin.post("/api/admin/ads/halt-all")
    res = r.json()
    check("F2 one switch pauses running campaigns at every business", r.status_code == 200 and res["haltAll"]["on"]
          and camp(c1["id"])["status"] == "paused" and camp(oc["id"], other)["status"] == "paused"
          and res["totals"]["businesses"] >= 2, res.get("totals"))
    s = owner.get("/api/ads").json()
    check("F3 owners see it's Amplafai's pause", s["halt"]["scope"] == "all", s["halt"])
    r = owner.post("/api/ads/allow")
    check("F4 an owner can't lift Amplafai's switch", r.json()["ok"] is False and "every client" in r.json()["message"], r.text)
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    check("F5 restarts are blocked everywhere", r.status_code == 409, r.text)
    r = admin.post("/api/admin/ads/allow-all")
    check("F6 Amplafai lifts it; nothing restarts on its own", r.status_code == 200 and not r.json()["haltAll"]["on"]
          and camp(c1["id"])["status"] == "paused" and owner.get("/api/ads").json()["halt"] is None, r.text)
    r = admin.post(f"/api/admin/businesses/{other_id}/ads/halt")
    check("F7 Amplafai can pause one client from the admin console", r.status_code == 200 and r.json()["business"]["adsHalt"]["scope"] == "business", r.text)
    admin.post(f"/api/admin/businesses/{other_id}/ads/allow")

    # ---- G ------------------------------------------------------------------
    print("\nG. real spend from the platform on a schedule")
    owner.put(f"/api/ads/campaigns/{c1['id']}", json={"status": "active"})
    ext = c1["externalCampaignId"]
    today = date.today()
    d1, d2 = (today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()
    FakeMeta.results = [ad_platforms.DayResult(ext, d1, 2050, 1500, 30), ad_platforms.DayResult(ext, d2, 1999, 1400, 25)]
    with SessionLocal() as db:
        token = current_tenant_id.set(biz_id)
        out = ad_sync.sync_business(db, db.get(Business, biz_id))
        db.commit()
        current_tenant_id.reset(token)
    c = camp(c1["id"])
    check("G1 the sync stores the platform's days, to the cent", out["platforms"]["fb_ig"]["ok"] and c["actualSpendCents"] == 4049
          and c["performance"]["source"] == "Meta (synced from its API)" and c["performance"]["impressions"] == 2900, (out, c["performance"]))
    FakeMeta.results = [ad_platforms.DayResult(ext, d2, 2100, 1450, 26)]
    with SessionLocal() as db:
        token = current_tenant_id.set(biz_id)
        ad_sync.sync_business(db, db.get(Business, biz_id))
        db.commit()
        current_tenant_id.reset(token)
    check("G2 the platform's revised day replaces the old number", camp(c1["id"])["actualSpendCents"] == 4150, camp(c1["id"])["actualSpendCents"])
    with SessionLocal() as db:
        month_cents = ad_imports.month_spend(db, biz_id, "fb_ig", today.strftime("%Y-%m"))
    owner.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": max(100, month_cents - 100)})
    OUTBOX.clear()
    n_pause = len(calls("pause"))
    with SessionLocal() as db:
        token = current_tenant_id.set(biz_id)
        out = ad_sync.sync_business(db, db.get(Business, biz_id))
        db.commit()
        current_tenant_id.reset(token)
    c = camp(c1["id"])
    if today.day >= 3:
        check("G3 over 100% of the cap: paused through Meta's API at once (no waiting on a person)",
              c["status"] == "paused" and len(calls("pause")) == n_pause + 1 and out["alerts"] and out["alerts"][0]["level"] == 100, (c["status"], out))
        check("G4 ...and the owner and Amplafai are emailed", any(o.get("type") == "cap" for o in OUTBOX), OUTBOX)
    else:
        print("  --  G3/G4 skipped: early in the month the test days fall in last month")
    FakeMeta.fail = {"results"}
    with SessionLocal() as db:
        done = ad_sync.run_all(db)
    check("G5 a platform outage is logged, never a crash", not logs(action="sync")[-1].ok and "rejected in test" in logs(action="sync")[-1].detail,
          logs(action="sync")[-1].detail)
    FakeMeta.fail = set()

    # ---- H ------------------------------------------------------------------
    print("\nH. the action log")
    r = admin.get(f"/api/admin/ads/log?business_id={biz_id}")
    actions = {x["action"] for x in r.json()["log"]}
    check("H1 every kind of change is on the record", {"create_paused", "activate", "pause", "resume", "cancel", "halt", "unhalt", "sync"} <= actions, actions)
    check("H2 owners can't read the admin log", owner.get("/api/admin/ads/log").status_code in (401, 403))
    check("H3 the admin queue shows recent platform actions", admin.get("/api/admin/ads/requests").json()["log"])

    # ---- I ------------------------------------------------------------------
    print("\nI. LinkedIn payloads")
    from app.integrations.linkedin import campaigns as lic
    from app.integrations.linkedin import reporting as lir

    class FakeLI:
        def __init__(self):
            self.created, self.posted, self.got = [], [], None

        def create(self, path, *, json, urn_prefix, headers=None):
            self.created.append((path, json))
            return f"{urn_prefix}:{len(self.created)}"

        def post(self, path, *, json=None, headers=None, **kw):
            self.posted.append((path, json, headers))

        def get(self, path, *, params=None, **kw):
            self.got = params
            return {"elements": [{"pivotValues": ["urn:li:sponsoredCampaign:7"], "impressions": 900, "clicks": 9,
                                  "costInLocalCurrency": "12.345", "dateRange": {"start": {"year": 2026, "month": 10, "day": 2}}}]}

    f = FakeLI()
    urn = lic.create_boost_campaign(f, account_urn="urn:li:sponsoredAccount:1", name="B2B", daily_budget_cents=1500, duration_days=5)
    check("I1 LinkedIn campaigns are created PAUSED", f.created[1][1]["status"] == "PAUSED" and urn.endswith(":2"), f.created[1][1]["status"])
    lic.set_campaign_status(f, "urn:li:sponsoredCampaign:7", "ACTIVE")
    check("I2 turning on is a status patch on that campaign", f.posted[-1][0].endswith("/adCampaigns/7")
          and f.posted[-1][1] == {"patch": {"$set": {"status": "ACTIVE"}}}, f.posted[-1])
    rows = lir.fetch_daily_analytics(f, campaign_urns=["urn:li:sponsoredCampaign:7"], start=date(2026, 10, 1), end=date(2026, 10, 3))
    check("I3 daily results: per-day rows, spend in cents", f.got["timeGranularity"] == "DAILY"
          and rows == [{"campaign_urn": "urn:li:sponsoredCampaign:7", "day": "2026-10-02", "impressions": 900, "clicks": 9, "spend_cents": 1235}], rows)


if __name__ == "__main__":
    main()
