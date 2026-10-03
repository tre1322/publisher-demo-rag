"""Phase 4a smoke — a real client's campaigns are run by hand, and the
dashboard never claims a change the ad platform hasn't made.

Run with:  uv run python -m app.scripts.smoke_managed_ads

Before Phase 4, approving a real client's campaign gave it a made-up
platform ID and showed it as "scheduled", though nothing existed on Meta or
Google, and Pause only changed the dashboard's record.

  A. Plans: Amplafai runs campaigns from Tier 3 up (API, agent, approvals)
  B. A new campaign waits for launch and asks Amplafai (email after commit)
  C. The admin queue: launch needs the real platform ID
  D. Pause / keep running / restart become requests until Amplafai confirms
  E. Cancel on the platform is a request too
  F. Before launch, hold / restart / cancel apply at once
  G. "Can't launch" cancels the campaign and tells the owner why
  H. The agent and the Approvals queue go through the same rules
  I. Campaigns past their end date complete
  J. The demo account still changes at once (simulated)
  K. Other businesses and non-staff can't touch the queue
  L. Plan copy and the agent's instructions
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_managed_ads_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["POPULAR_NOTIFY_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

OWNER = ("ana@hardware.example.com", "ana-correct-horse-battery")
SMALL = ("sam@smallshop.example.com", "sam-correct-horse-battery")
OPS = "ops@amplafai.example.com"

OUTBOX: list[dict] = []
SENT: list[dict] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def capture_send(to_email, subject, html_body, text_body, *, kind):
    SENT.append({"to": to_email, "subject": subject, "text": text_body, "kind": kind})
    return {"sent": True, "messageId": "smoke"}


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.managed_ads as ma
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    os.environ["ADS_OPS_EMAIL"] = OPS
    with patch.object(ma, "dispatch", OUTBOX.extend), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as small:
        bootstrap_login(admin)
        _run(admin, owner, small)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 4a managed ads smoke green ✓")


def _claim(client, invite, password):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": "x",
                                                         "accept_terms": True})
    check(f"claim {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner, small) -> None:  # noqa: C901 — linear smoke
    import app.email as mailer
    import app.managed_ads as ma
    from app.agent import tools
    from app.agent.system_prompt import build_system_prompt
    from app.db import SessionLocal
    from app.models import AdCampaign, AdOpsRequest, Approval, Business, Post, SettingsRow
    from app.subscriptions import TIER_FEATURES

    def mk_biz(name, owner_name, email, tier):
        r = admin.post("/api/admin/businesses", json={"name": name, "owner": owner_name, "owner_email": email,
                                                      "location": "Windom, MN", "tier": tier})
        check(f"created {name} (Tier {tier})", r.status_code == 200, r.text)
        return r.json()["business"]["id"], r.json()["invite"]

    biz_id, inv = mk_biz("Main St Hardware", "Ana Ruiz", OWNER[0], 3)
    _claim(owner, inv, OWNER[1])
    small_id, inv2 = mk_biz("Small Shop", "Sam Lee", SMALL[0], 2)
    _claim(small, inv2, SMALL[1])

    def ads(client=owner):
        r = client.get("/api/ads")
        assert r.status_code == 200, r.text
        return r.json()

    def camp(cid):
        return next(c for c in ads()["campaigns"] if c["id"] == cid)

    def create(client=owner, **kw):
        body = {"platform": "fb_ig", "name": "Fall paint sale", "daily_budget_cents": 2000, "duration_days": 7,
                "target_audience": "Homeowners within 20 miles of Windom"}
        body.update(kw)
        return client.post("/api/ads/campaigns", json=body)

    def queue():
        r = admin.get("/api/admin/ads/requests")
        assert r.status_code == 200, r.text
        return r.json()

    def open_req(cid):
        return next((q for q in queue()["requests"] if q["campaign"]["id"] == cid), None)

    def put(cid, status, client=owner):
        return client.put(f"/api/ads/campaigns/{cid}", json={"status": status})

    with SessionLocal() as db:
        for bid in (biz_id, small_id):
            db.add(Post(business_id=bid, date="2026-10-03", platform="fb", title="Paint sale",
                        draft="Fall paint sale this week.", status="published"))
        db.commit()
        post_id = db.query(Post).filter(Post.business_id == biz_id).first().id
        small_post = db.query(Post).filter(Post.business_id == small_id).first().id

    # ---- A ------------------------------------------------------------------
    print("\nA. plans")
    r = create(small)
    check("A1 Tier 2 can't start a campaign Amplafai would run (403, says which plan)",
          r.status_code == 403 and "Tier 3" in r.json()["detail"], r.text)
    s = ads(small)
    check("A2 the Ads screen knows (planAllows false, managed true)", s["planAllows"] is False and s["managed"] is True, s)
    with SessionLocal() as db:
        res = tools._exec_schedule_boost(db, small_id, {"post_id": small_post, "platform": "meta",
                                                        "daily_budget_cents": 1000, "duration_days": 5,
                                                        "audience_hint": "locals"})
        db.commit()
        n = db.query(AdCampaign).filter(AdCampaign.business_id == small_id).count()
    check("A3 the agent can't either, and nothing is written", res.is_error and "Tier 3" in res.text and n == 0, res.text)
    with SessionLocal() as db:  # a proposal from before a downgrade
        c = AdCampaign(business_id=small_id, platform="fb_ig", name="Old proposal", daily_budget_cents=1000,
                       duration_days=5, planned_total_cents=5000, status="pending_approval", origin="agent_proposed")
        db.add(c)
        db.flush()
        db.add(Approval(business_id=small_id, external_id=f"boost-{c.id}", kind="boost", platform="fb_ig",
                        title="Boost: Old proposal", draft="x", payload_json={"action": "boost", "campaign_id": c.id}))
        db.commit()
        appr_id = db.query(Approval).filter(Approval.business_id == small_id).one().id
        old_id = c.id
    r = small.post(f"/api/approvals/{appr_id}/decide", json={"decision": "approve"})
    with SessionLocal() as db:
        appr, oc = db.get(Approval, appr_id), db.get(AdCampaign, old_id)
        check("A4 approving an old proposal after a downgrade is refused and changes nothing",
              r.status_code == 403 and appr.decision is None and oc.status == "pending_approval", (r.status_code, r.text))
    check("A5 no requests or emails from any of that", not queue()["requests"] and not OUTBOX, (queue(), OUTBOX))

    # ---- B ------------------------------------------------------------------
    print("\nB. a new campaign waits for launch")
    r = create()
    check("B1 created", r.status_code == 200, r.text)
    c1 = r.json()
    check("B2 no made-up platform ID; it says waiting for launch",
          c1["externalCampaignId"] is None and c1["status"] == "scheduled" and c1["stage"] == "waiting_for_launch", c1)
    s = ads()
    check("B3 the Ads screen counts it as waiting", s["waitingCount"] == 1 and s["managed"] and s["planAllows"], s)
    check("B4 one email to Amplafai, after the save", len(OUTBOX) == 1 and OUTBOX[0]["kind"] == "launch"
          and OUTBOX[0]["by"] == OWNER[0] and OUTBOX[0]["source"] == "owner", OUTBOX)
    subject, text = ma.build_email(OUTBOX[0], "https://dashboard.amplafai.com")
    check("B5 the email says what to set up: lifetime budget, end date, audience, queue link",
          subject.startswith("[Amplafai ads] Launch needed: Main St Hardware") and "LIFETIME budget of $140" in text
          and "end date 7 days after launch" in text and "Homeowners within 20 miles" in text
          and "https://dashboard.amplafai.com/admin#ads" in text, text)
    with patch.object(mailer, "_send", capture_send):
        ma._deliver([OUTBOX[0]])
    check("B6 delivery goes to ADS_OPS_EMAIL", [m["to"] for m in SENT] == [OPS] and SENT[0]["kind"] == "ads-ops", SENT)
    OUTBOX.clear()
    r = owner.post("/api/ads/campaigns", json={"platform": "fb_ig", "name": "x", "daily_budget_cents": 2000,
                                                "duration_days": 7, "post_id": 999999})
    check("B7 a refused create sends nothing", r.status_code == 400 and not OUTBOX, (r.status_code, OUTBOX))
    r = put(c1["id"], "active")
    check("B8 the owner can't mark it live themselves", r.status_code == 409 and camp(c1["id"])["status"] == "scheduled", r.text)
    r = owner.post("/api/ads/tick?hours=24")
    check("B9 no simulated spend for a real client", r.status_code in (403, 404, 409) and camp(c1["id"])["actualSpendCents"] == 0, r.status_code)
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"daily_budget_cents": 2500})
    check("B10 the budget can still change before launch", r.status_code == 200 and r.json()["plannedTotalCents"] == 17500, r.text)

    # ---- C ------------------------------------------------------------------
    print("\nC. the admin queue")
    q = open_req(c1["id"])
    check("C1 the request is in the queue with what Amplafai needs",
          q and q["kind"] == "launch" and q["business"]["name"] == "Main St Hardware"
          and q["campaign"]["plannedTotalCents"] == 17500 and q["overdue"] is False and q["campaign"]["adsManager"] == "Meta Ads Manager", q)
    check("C2 the queue knows where emails go", queue()["emailsTo"] == [OPS], queue()["emailsTo"])
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={})
    check("C3 a launch needs the platform's campaign ID", r.status_code == 422, r.text)
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": "mock_meta_1234"})
    check("C4 a demo-style ID is refused", r.status_code == 422, r.text)
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": "1202 1000"})
    check("C5 spaces are refused", r.status_code == 422, r.text)
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/done",
                   json={"external_campaign_id": "120210000000000001", "note": "Live with a 20-mile radius."})
    check("C6 marked launched", r.status_code == 200 and not any(x["id"] == q["id"] for x in r.json()["requests"]), r.text)
    c = camp(c1["id"])
    check("C7 now it's live, with the real ID, start and end dates, and Amplafai's note",
          c["stage"] == "live" and c["status"] == "active" and c["externalCampaignId"] == "120210000000000001"
          and c["launchedAt"] and c["endsAt"] and c["opsNote"] == "Live with a 20-mile radius.", c)
    check("C8 it shows under recently handled", queue()["recent"][0]["id"] == q["id"] and queue()["recent"][0]["doneBy"], queue()["recent"])
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": "120210000000000001"})
    check("C9 a handled request can't be handled twice", r.status_code == 409, r.text)
    c2 = create(name="Second campaign").json()
    q2 = open_req(c2["id"])
    r = admin.post(f"/api/admin/ads/requests/{q2['id']}/done", json={"external_campaign_id": "120210000000000001"})
    check("C10 the same platform ID can't go on two campaigns", r.status_code == 409 and "already has that ID" in r.text, r.text)
    admin.post(f"/api/admin/ads/requests/{q2['id']}/done", json={"external_campaign_id": "120210000000000002"})
    OUTBOX.clear()

    # ---- D ------------------------------------------------------------------
    print("\nD. pause, keep running, restart")
    r = put(c1["id"], "paused")
    check("D1 Pause becomes a request, and says so", r.status_code == 200 and r.json()["message"].startswith("Pause requested")
          and "within one business day" in r.json()["message"], r.text)
    c = camp(c1["id"])
    check("D2 it's still running until Amplafai pauses it", c["status"] == "active" and c["stage"] == "pause_requested"
          and c["openRequest"]["kind"] == "pause", c)
    check("D3 Amplafai is emailed the platform ID", len(OUTBOX) == 1 and OUTBOX[0]["kind"] == "pause"
          and "120210000000000001" in ma.build_email(OUTBOX[0], "x")[1], OUTBOX)
    r = put(c1["id"], "paused")
    check("D4 asking twice doesn't open a second request", r.status_code == 200 and "Already asked" in r.json()["message"]
          and len(OUTBOX) == 1, r.text)
    r = put(c1["id"], "active")
    check("D5 'Keep running' withdraws the pause request", r.status_code == 200 and "withdrawn" in r.json()["message"]
          and camp(c1["id"])["stage"] == "live", r.text)
    check("D6 ...and tells Amplafai not to bother", OUTBOX[-1]["what"] == "withdrawn"
          and "Withdrawn" in ma.build_email(OUTBOX[-1], "x")[0], OUTBOX[-1])
    put(c1["id"], "paused")
    q = open_req(c1["id"])
    admin.post(f"/api/admin/ads/requests/{q['id']}/done", json={})
    c = camp(c1["id"])
    check("D7 once Amplafai confirms, it's paused", c["status"] == "paused" and c["stage"] == "paused" and not c["openRequest"], c)
    r = put(c1["id"], "active")
    check("D8 Restart is a request too", r.status_code == 200 and r.json()["message"].startswith("Restart requested")
          and camp(c1["id"])["stage"] == "resume_requested" and camp(c1["id"])["status"] == "paused", r.text)
    r = put(c1["id"], "paused")
    check("D9 'Keep paused' withdraws the restart", "withdrawn" in r.json()["message"] and camp(c1["id"])["stage"] == "paused", r.text)
    put(c1["id"], "active")
    admin.post(f"/api/admin/ads/requests/{open_req(c1['id'])['id']}/done", json={})
    check("D10 restart confirmed → live", camp(c1["id"])["stage"] == "live", camp(c1["id"]))
    r = owner.put(f"/api/ads/campaigns/{c1['id']}", json={"daily_budget_cents": 5000})
    check("D11 a running campaign's budget can't be changed from here", r.status_code == 409, r.text)

    # ---- E ------------------------------------------------------------------
    print("\nE. cancel")
    r = owner.delete(f"/api/ads/campaigns/{c2['id']}")
    check("E1 Cancel on the platform is a request", r.status_code == 200 and r.json()["message"].startswith("Cancel requested")
          and camp(c2["id"])["stage"] == "cancel_requested" and camp(c2["id"])["status"] == "active", r.text)
    r = put(c2["id"], "paused")
    check("E2 nothing else stacks on a cancel", "Already asked" in r.json()["message"], r.text)
    admin.post(f"/api/admin/ads/requests/{open_req(c2['id'])['id']}/done", json={})
    c = camp(c2["id"])
    check("E3 confirmed → cancelled", c["status"] == "cancelled" and c["endedAt"], c)
    r = owner.delete(f"/api/ads/campaigns/{c2['id']}")
    check("E4 cancelling twice is refused clearly", r.status_code == 409 and "already cancelled" in r.text, r.text)

    # ---- F ------------------------------------------------------------------
    print("\nF. before launch")
    OUTBOX.clear()
    c3 = create(name="Holiday hours").json()
    r = put(c3["id"], "paused")
    c = camp(c3["id"])
    check("F1 pausing before launch puts it on hold at once", r.status_code == 200 and c["status"] == "paused"
          and c["stage"] == "held" and not c["openRequest"], c)
    check("F2 ...withdraws the launch request and tells Amplafai", open_req(c3["id"]) is None
          and [o["what"] for o in OUTBOX] == ["new", "withdrawn"], OUTBOX)
    r = put(c3["id"], "active")
    check("F3 restarting it asks for a launch again", r.status_code == 200 and camp(c3["id"])["stage"] == "waiting_for_launch"
          and open_req(c3["id"])["kind"] == "launch", r.text)
    r = owner.delete(f"/api/ads/campaigns/{c3['id']}")
    check("F4 cancelling before launch is immediate and says nothing was spent",
          r.status_code == 200 and "never launched" in r.json()["message"] and camp(c3["id"])["status"] == "cancelled"
          and open_req(c3["id"]) is None, r.text)

    # ---- G ------------------------------------------------------------------
    print("\nG. can't launch")
    c4 = create(name="Contractor night", platform="google_ads").json()
    q = open_req(c4["id"])
    check("G1 Google requests point at Google Ads", q["campaign"]["adsManager"] == "Google Ads", q)
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/drop", json={"note": ""})
    check("G2 a reason is required", r.status_code == 422, r.text)
    r = admin.post(f"/api/admin/ads/requests/{q['id']}/drop", json={"note": "Google flagged the ad text; let's reword it together."})
    c = camp(c4["id"])
    check("G3 the campaign is cancelled and the owner sees why",
          r.status_code == 200 and c["status"] == "cancelled"
          and c["opsNote"].startswith("Amplafai couldn't launch this campaign: Google flagged"), c)

    # ---- H ------------------------------------------------------------------
    print("\nH. agent and approvals")
    OUTBOX.clear()
    with SessionLocal() as db:
        res = tools._exec_schedule_boost(db, biz_id, {"post_id": post_id, "platform": "meta", "daily_budget_cents": 1500,
                                                      "duration_days": 4, "audience_hint": "DIYers"})
        db.commit()
    check("H1 autonomy off: the agent's boost is a proposal (no request yet)",
          not res.is_error and res.attachment["tierMode"] == "proposal" and not OUTBOX, res.text)
    with SessionLocal() as db:
        appr = db.query(Approval).filter(Approval.business_id == biz_id, Approval.decision.is_(None)).one()
        cid = appr.payload_json["campaign_id"]
    r = owner.post(f"/api/approvals/{appr.id}/decide", json={"decision": "approve"})
    c = camp(cid)
    check("H2 approving it sends it to Amplafai to launch", r.status_code == 200 and c["stage"] == "waiting_for_launch"
          and c["externalCampaignId"] is None, (r.text, c))
    check("H3 the request says who approved it", OUTBOX and OUTBOX[-1]["by"] == f"{OWNER[0]}, approving the agent's proposal", OUTBOX)
    admin.post(f"/api/admin/ads/requests/{open_req(cid)['id']}/done", json={"external_campaign_id": "120210000000000003"})
    with SessionLocal() as db:
        res = tools._exec_pause_campaign(db, biz_id, {"campaign_id": cid, "reason": "Low clicks"})
        db.commit()
        pa = db.query(Approval).filter(Approval.external_id == f"pause-{cid}").one()
    check("H4 autonomy off: the agent's pause is a proposal", not res.is_error and res.attachment["tierMode"] == "proposal", res.text)
    r = owner.post(f"/api/approvals/{pa.id}/decide", json={"decision": "approve"})
    c = camp(cid)
    check("H5 approving the pause asks Amplafai; the campaign keeps running until then",
          r.status_code == 200 and c["stage"] == "pause_requested" and c["status"] == "active"
          and r.json()["message"].startswith("Pause requested"), (r.text, c))
    admin.post(f"/api/admin/ads/requests/{open_req(cid)['id']}/done", json={})
    put(cid, "active")
    admin.post(f"/api/admin/ads/requests/{open_req(cid)['id']}/done", json={})
    r = owner.put("/api/settings/ad-autonomy", json={"enabled": True})
    check("H6 owner turns autonomy on", r.status_code == 200, r.text)
    owner.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": 100000})
    OUTBOX.clear()
    with SessionLocal() as db:
        res = tools._exec_schedule_boost(db, biz_id, {"post_id": post_id, "platform": "meta", "daily_budget_cents": 1000,
                                                      "duration_days": 3, "audience_hint": "DIYers"})
        db.commit()
        auto = db.get(AdCampaign, res.attachment["campaignId"])
        auto_ext = auto.external_campaign_id
    check("H7 autonomy on: within the cap it goes straight to Amplafai, and the agent says it's waiting",
          res.attachment["tierMode"] == "autonomous" and auto_ext is None and "Waiting for launch" in res.text
          and OUTBOX and OUTBOX[-1]["source"] == "agent", (res.text, OUTBOX))
    with SessionLocal() as db:
        res = tools._exec_pause_campaign(db, biz_id, {"campaign_id": cid, "reason": "Budget better spent elsewhere"})
        db.commit()
    c = camp(cid)
    check("H8 autonomy on: the agent's pause is a request, and the agent is told to say so",
          not res.is_error and res.attachment["requested"] is True and "Pause requested" in res.text
          and c["status"] == "active" and c["stage"] == "pause_requested", (res.text, c))

    # ---- I ------------------------------------------------------------------
    print("\nI. end dates")
    with SessionLocal() as db:
        x = db.get(AdCampaign, c1["id"])
        x.launched_at = datetime.utcnow() - timedelta(days=8)
        db.commit()
    check("I1 the Ads screen shows a campaign past its end date as completed", camp(c1["id"])["status"] == "completed", camp(c1["id"]))
    with SessionLocal() as db:
        x = db.get(AdCampaign, cid)
        x.launched_at = datetime.utcnow() - timedelta(days=5)
        db.commit()
        n = ma.complete_finished(db)
        db.commit()
        left = db.query(AdOpsRequest).filter(AdOpsRequest.campaign_id == cid, AdOpsRequest.status == "open").count()
    check("I2 the background check completes it too and closes its open request", n == 1 and left == 0, (n, left))

    # ---- J ------------------------------------------------------------------
    print("\nJ. the demo account")
    r = admin.post("/api/ads/campaigns", json={"platform": "fb_ig", "name": "Demo boost", "daily_budget_cents": 1000,
                                                "duration_days": 3})
    d = r.json()
    check("J1 demo campaigns still get a simulated ID and no request",
          r.status_code == 200 and d["externalCampaignId"].startswith("mock_") and d["managed"] is False
          and d["stage"] == "scheduled" and open_req(d["id"]) is None, d)
    r = admin.put(f"/api/ads/campaigns/{d['id']}", json={"status": "paused"})
    check("J2 demo pause applies at once", r.status_code == 200 and r.json()["status"] == "paused" and "message" not in r.json(), r.text)

    # ---- K ------------------------------------------------------------------
    print("\nK. who can do what")
    check("K1 owners can't see the queue", owner.get("/api/admin/ads/requests").status_code in (401, 403))
    c5 = create(name="Last one").json()
    q = open_req(c5["id"])
    r = owner.post(f"/api/admin/ads/requests/{q['id']}/done", json={"external_campaign_id": "999"})
    check("K2 ...or mark anything done", r.status_code in (401, 403) and camp(c5["id"])["stage"] == "waiting_for_launch", r.text)
    r = small.put(f"/api/ads/campaigns/{c5['id']}", json={"status": "paused"})
    check("K3 another business can't touch the campaign", r.status_code == 404, r.text)
    r = admin.post("/api/admin/ads/requests/999999/done", json={})
    check("K4 unknown request → 404", r.status_code == 404, r.text)

    # ---- L ------------------------------------------------------------------
    print("\nL. plan copy and the agent's instructions")
    check("L1 Tiers 3 and 4 list managed ads; Tiers 1 and 2 don't",
          all(any("run for you by Amplafai" in f for f in TIER_FEATURES[t]) for t in (3, 4))
          and not any("run for you by Amplafai" in f for t in (1, 2) for f in TIER_FEATURES[t]), TIER_FEATURES)
    with SessionLocal() as db:
        p3 = build_system_prompt(db, biz_id)
        p2 = build_system_prompt(db, small_id)
    check("L2 the agent knows Amplafai launches by hand and not to claim changes early",
          "Waiting for launch" in p3 and "Never say a campaign is live" in p3, p3[-1500:])
    check("L3 a Tier 2 agent is told not to propose paid boosts", "Don't propose paid boosts" in p2, p2[-800:])
    with SessionLocal() as db:
        check("L4 nothing leaked into the other business", db.query(AdOpsRequest).filter(AdOpsRequest.business_id == small_id).count() == 0)
        check("L5 the demo flag is untouched", db.get(Business, biz_id).is_demo in (None, False))
        settings = db.get(SettingsRow, biz_id)
        check("L6 autonomy stayed where the owner put it", bool(settings.ad_autonomy_enabled))


if __name__ == "__main__":
    main()
