"""Phase 0 smoke — the money path does what the owner approved, nothing more.

Run with:  uv run python -m app.scripts.smoke_money_path

Born from the 2026-10-01 readiness review, which reproduced these gaps while
the other 24 smokes stayed green (they only exercised the simulator):
  - approving the agent's pause / budget proposals did nothing
  - the Approvals queue skipped the real-platform swap point
  - "Simulate today" wrote fake spend onto real-platform campaigns
  - the agent could raise its own spend cap without limit
  - the autonomy off-switch lived in browser storage only
  - no cap meant no limit; the cap check ignored money already committed
  - settings writes trusted a business_id from the request body

Covers, in order:
  A. Pure policy rules (app/agent/spend_policy.py)
  B. Autonomy switch is server-side, default off, owner-only
  C. Settings + escalations are scoped to the session's business
  D. Autonomy OFF → every agent spend action becomes a proposal
  E. Autonomy ON → acts only within owner-authorized caps + headroom
  F. Approving proposals applies exactly what was proposed
  G. Queue approval uses the real-platform swap point; failure changes nothing
  H. Simulator never touches real-platform campaigns
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_money_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

FAKE_URN = "urn:li:sponsoredCampaign:999000111"


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        _fail(f"{label} — {detail}")
    _ok(label)


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    # Hermetic: section B mints an invite, which would otherwise make a LIVE
    # Postmark send attempt. Must run AFTER importing app.main — its
    # load_dotenv(override=True) restores the key from the parent .env.
    # (Same reasoning as smoke_invite.py.)
    os.environ.pop("POSTMARK_API_KEY", None)

    with TestClient(app, follow_redirects=False) as client:
        bootstrap_login(client)
        _run(client, app)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 0 money-path smoke green ✓")


# --------------------------------------------------------------------------- #

def _run(client, app) -> None:  # noqa: C901 — linear smoke script
    import app.routers.ads as ads_mod
    from app.agent import spend_policy as sp
    from app.agent import tools
    from app.db import SessionLocal
    from app.models import AdCampaign, AdPlatformBudget, Approval, Escalation, Post, SettingsRow

    def boot() -> dict:
        r = client.get("/api/bootstrap")
        assert r.status_code == 200, r.text
        return r.json()

    def run_tool(fn, args: dict):
        with SessionLocal() as db:
            res = fn(db, 1, args)
            db.commit()
            return res

    def budget(platform: str) -> AdPlatformBudget | None:
        with SessionLocal() as db:
            return sp.budget_row(db, 1, platform)

    def campaign(cid: int) -> AdCampaign:
        with SessionLocal() as db:
            return db.get(AdCampaign, cid)

    def approval_by_ext(ext: str) -> Approval:
        with SessionLocal() as db:
            return db.query(Approval).filter(Approval.external_id == ext).one()

    def decide(approval_id: int, decision: str, **extra):
        return client.post(f"/api/approvals/{approval_id}/decide", json={"decision": decision, **extra})

    with SessionLocal() as db:
        post_id = db.query(Post).filter(Post.business_id == 1).first().id

    # ---- A. pure policy rules ------------------------------------------------
    print("\nA. policy rules")
    check("A1 autonomy off → cap change proposes",
          sp.cap_change_decision(requested_cents=100, owner_cap_cents=500, autonomy_enabled=False) == ("propose", "autonomy_off"))
    check("A2 no owner cap → cap change proposes",
          sp.cap_change_decision(requested_cents=100, owner_cap_cents=None, autonomy_enabled=True) == ("propose", "no_owner_cap"))
    check("A3 above owner cap → proposes",
          sp.cap_change_decision(requested_cents=501, owner_cap_cents=500, autonomy_enabled=True) == ("propose", "above_owner_cap"))
    check("A4 at/below owner cap → applies",
          sp.cap_change_decision(requested_cents=500, owner_cap_cents=500, autonomy_enabled=True)[0] == "apply")
    check("A5 no cap → boost proposes",
          sp.boost_decision(planned_total_cents=100, headroom_cents=None, autonomy_enabled=True) == ("propose", "no_cap"))
    check("A6 over headroom → boost proposes",
          sp.boost_decision(planned_total_cents=101, headroom_cents=100, autonomy_enabled=True) == ("propose", "over_cap"))
    check("A7 within headroom → boost applies",
          sp.boost_decision(planned_total_cents=100, headroom_cents=100, autonomy_enabled=True)[0] == "apply")
    check("A8 pause needs autonomy",
          sp.pause_decision(autonomy_enabled=False)[0] == "propose" and sp.pause_decision(autonomy_enabled=True)[0] == "apply")

    # ---- B. autonomy switch --------------------------------------------------
    print("\nB. autonomy switch")
    check("B1 bootstrap exposes adAutonomyEnabled, default False",
          boot()["settings"].get("adAutonomyEnabled") is False, boot()["settings"].get("adAutonomyEnabled"))
    r = client.put("/api/settings/ad-autonomy", json={"enabled": True})
    check("B2 owner/superuser can turn it on → 200", r.status_code == 200 and r.json()["adAutonomyEnabled"] is True, r.text)
    check("B3 persisted server-side", boot()["settings"]["adAutonomyEnabled"] is True)
    r = client.put("/api/settings/ad-autonomy", json={"enabled": False})
    check("B4 turn off → persisted", r.status_code == 200 and boot()["settings"]["adAutonomyEnabled"] is False, r.text)

    r = client.post("/api/auth/invites", json={"email": "editor-money@example.com", "role": "editor"})
    check("B5 mint editor invite", r.status_code == 200, r.text)
    raw_token = r.json()["rawToken"]
    with TestClientFactory(app) as editor:
        r = editor.post("/api/auth/invites/claim", json={
            "token": raw_token, "password": "editor-pw-correct-horse", "display_name": "Ed Itor",
            "accept_terms": True})
        check("B6 editor claims invite", r.status_code == 200, r.text)
        r = editor.put("/api/settings/ad-autonomy", json={"enabled": True})
        check("B7 editor cannot enable autonomous spend → 403", r.status_code == 403, r.text)
    check("B8 still off after editor attempt", boot()["settings"]["adAutonomyEnabled"] is False)

    # ---- C. tenant scoping ---------------------------------------------------
    print("\nC. settings scoped to the session's business")
    from app.models import Business
    with SessionLocal() as db:
        db.add(Business(
            id=2, slug="money_biz_b", name="Biz B", owner="B Owner", owner_initials="BO",
            location="Elsewhere, MN", publisher="Other Paper", phone="(000) 000-0000",
            tier=4, tier_label="Tier 4", monthly_price=799, joined_days_ago=0,
            joined_date="Today", voice_interview="pending",
        ))
        db.flush()
        db.add(SettingsRow(business_id=2, cadence="weekly", notifications_json=[], ad_autonomy_enabled=False))
        db.commit()
    r = client.put("/api/settings/notifications", json={"cadence": "each", "business_id": 2})
    check("C1 notifications PUT with foreign business_id → 200", r.status_code == 200, r.text)
    with SessionLocal() as db:
        check("C2 foreign business untouched", db.get(SettingsRow, 2).cadence == "weekly", db.get(SettingsRow, 2).cadence)
        check("C3 session business updated", db.get(SettingsRow, 1).cadence == "each", db.get(SettingsRow, 1).cadence)
    r = client.post("/api/escalations", json={"message": "help", "business_id": 2})
    with SessionLocal() as db:
        esc = db.query(Escalation).order_by(Escalation.id.desc()).first()
    check("C4 escalation lands on session business, not body's", r.status_code == 200 and esc.business_id == 1,
          f"{r.status_code} biz={esc.business_id if esc else None}")

    # ---- D. autonomy OFF → proposals -----------------------------------------
    print("\nD. autonomy off")
    res = run_tool(tools._exec_allocate_platform_budget, {"platform": "meta", "monthly_cents": 30000, "reasoning": "test"})
    check("D1 allocate → proposal (autonomy_off)",
          res.attachment["tierMode"] == "proposal" and res.attachment["reason"] == "autonomy_off", res.attachment)
    check("D2 nothing applied", budget("fb_ig") is None or budget("fb_ig").monthly_cap_cents == 0, budget("fb_ig"))
    res = run_tool(tools._exec_schedule_boost, {"post_id": post_id, "platform": "meta",
                                                "daily_budget_cents": 1000, "duration_days": 3, "audience_hint": "locals"})
    check("D3 schedule_boost → proposal, campaign pending_approval",
          res.attachment["tierMode"] == "proposal" and campaign(res.attachment["campaignId"]).status == "pending_approval",
          res.attachment)
    with SessionLocal() as db:
        a = db.query(Approval).filter(Approval.external_id == f"boost-{res.attachment['campaignId']}").one()
    check("D4 boost proposal carries payload", a.payload_json == {"action": "boost", "campaign_id": res.attachment["campaignId"]},
          a.payload_json)

    # ---- E. autonomy ON → only within owner-authorized caps ------------------
    print("\nE. autonomy on")
    client.put("/api/settings/ad-autonomy", json={"enabled": True})
    res = run_tool(tools._exec_allocate_platform_budget, {"platform": "meta", "monthly_cents": 10000, "reasoning": "t"})
    check("E1 no owner cap yet → allocate proposes (no_owner_cap)",
          res.attachment["tierMode"] == "proposal" and res.attachment["reason"] == "no_owner_cap", res.attachment)

    r = client.put("/api/ads/budgets/fb_ig", json={"monthly_cap_cents": 20000})
    check("E2 owner sets $200 cap → ownerCapCents recorded",
          r.status_code == 200 and r.json()["ownerCapCents"] == 20000, r.text)
    res = run_tool(tools._exec_allocate_platform_budget, {"platform": "meta", "monthly_cents": 15000, "reasoning": "t"})
    check("E3 agent lowers to $150 within ceiling → applied",
          res.attachment["tierMode"] == "autonomous" and budget("fb_ig").monthly_cap_cents == 15000
          and budget("fb_ig").owner_cap_cents == 20000, (res.attachment, budget("fb_ig").monthly_cap_cents))
    res = run_tool(tools._exec_allocate_platform_budget, {"platform": "meta", "monthly_cents": 5_000_000, "reasoning": "scale"})
    check("E4 agent tries $50,000 → proposal, cap unchanged",
          res.attachment["reason"] == "above_owner_cap" and budget("fb_ig").monthly_cap_cents == 15000,
          (res.attachment, budget("fb_ig").monthly_cap_cents))

    res = run_tool(tools._exec_schedule_boost, {"post_id": post_id, "platform": "meta",
                                                "daily_budget_cents": 1000, "duration_days": 10, "audience_hint": "a"})
    first_boost = res.attachment["campaignId"]
    check("E5 $100 boost under $150 cap → scheduled autonomously",
          res.attachment["tierMode"] == "autonomous" and campaign(first_boost).status == "scheduled", res.attachment)
    res = run_tool(tools._exec_schedule_boost, {"post_id": post_id, "platform": "meta",
                                                "daily_budget_cents": 1000, "duration_days": 10, "audience_hint": "b"})
    check("E6 second $100 boost counts the first as committed → proposal (over_cap)",
          res.attachment["tierMode"] == "proposal" and res.attachment["reason"] == "over_cap", res.attachment)
    res = run_tool(tools._exec_schedule_boost, {"post_id": post_id, "platform": "google",
                                                "daily_budget_cents": 1000, "duration_days": 2, "audience_hint": "c"})
    check("E7 platform with no cap → proposal (no_cap), never unlimited",
          res.attachment["tierMode"] == "proposal" and res.attachment["reason"] == "no_cap", res.attachment)

    # ---- F. approvals apply what was proposed --------------------------------
    print("\nF. approving proposals")
    with SessionLocal() as db:
        alloc = (db.query(Approval)
                 .filter(Approval.external_id.like("allocate-fb_ig-%"), Approval.decision.is_(None))
                 .order_by(Approval.id.desc()).first())
        alloc_id, alloc_payload, alloc_ext = alloc.id, alloc.payload_json, alloc.external_id
    queued = {a["id"]: a.get("kind") for a in boot()["approvals"]}
    check("F0 bootstrap sends kind='boost' for spend proposals (else they render as posts + get batch-approved)",
          queued.get(alloc_ext) == "boost", queued)
    check("F1 allocate proposal stores exact amount", alloc_payload.get("monthly_cents") == 5_000_000, alloc_payload)
    r = decide(alloc_id, "edit", edited_draft="make it smaller")
    check("F2 editing a budget proposal → 422", r.status_code == 422, r.text)
    r = decide(alloc_id, "approve")
    check("F3 approving it sets cap AND owner ceiling",
          r.status_code == 200 and budget("fb_ig").monthly_cap_cents == 5_000_000 and budget("fb_ig").owner_cap_cents == 5_000_000,
          r.text)

    # A running campaign + autonomy off → pause becomes a proposal → approve pauses it.
    client.post("/api/ads/tick?hours=1")  # first_boost scheduled → active
    check("F4 campaign active after tick", campaign(first_boost).status == "active", campaign(first_boost).status)
    client.put("/api/settings/ad-autonomy", json={"enabled": False})
    res = run_tool(tools._exec_pause_campaign, {"campaign_id": first_boost, "reason": "CTR tanking"})
    check("F5 pause with autonomy off → proposal", res.attachment["tierMode"] == "proposal", res.attachment)
    pa = approval_by_ext(f"pause-{first_boost}")
    r = decide(pa.id, "approve")
    check("F6 approving the pause proposal pauses the campaign",
          r.status_code == 200 and campaign(first_boost).status == "paused", (r.text, campaign(first_boost).status))

    with SessionLocal() as db:
        legacy = Approval(business_id=1, external_id="allocate-fb_ig-legacy", kind="boost", platform="fb_ig",
                          title="old", draft="Set monthly cap to $99 on meta.", note="legacy")
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    r = decide(legacy_id, "approve")
    check("F7 legacy budget proposal with no amount → 409, not a silent no-op", r.status_code == 409, r.text)
    r = decide(legacy_id, "reject")
    check("F8 ...and it can still be rejected", r.status_code == 200, r.text)

    # ---- G. queue approval uses the real-platform swap point -----------------
    print("\nG. queue approval → swap point")
    real_resolve = ads_mod.resolve_external_campaign_id
    calls: list[str] = []

    def fake_resolve(db, business_id, platform, **kw):
        calls.append(platform)
        return FAKE_URN if platform == "linkedin" else ads_mod._mock_external_id(platform)

    def failing_resolve(db, business_id, platform, **kw):
        raise RuntimeError("LinkedIn said no")

    r = client.post("/api/ads/campaigns", json={"platform": "linkedin", "name": "LI test", "daily_budget_cents": 2000,
                                                 "duration_days": 5, "origin": "agent_proposed"})
    li_id = r.json()["id"]
    li_appr = approval_by_ext(f"boost-{li_id}")
    try:
        ads_mod.resolve_external_campaign_id = failing_resolve
        r = decide(li_appr.id, "approve")
        check("G1 platform failure → 502", r.status_code == 502, r.text)
        check("G2 ...approval left undecided", approval_by_ext(f"boost-{li_id}").decision is None)
        check("G3 ...campaign still pending_approval", campaign(li_id).status == "pending_approval", campaign(li_id).status)

        ads_mod.resolve_external_campaign_id = fake_resolve
        r = decide(li_appr.id, "approve")
        check("G4 queue approve calls the swap point and stores the real id",
              r.status_code == 200 and calls == ["linkedin"] and campaign(li_id).external_campaign_id == FAKE_URN,
              (r.text, calls, campaign(li_id).external_campaign_id))
    finally:
        ads_mod.resolve_external_campaign_id = real_resolve

    # ---- H. simulator leaves real campaigns alone ----------------------------
    print("\nH. simulator")
    r = client.post("/api/ads/tick?hours=24")
    li = campaign(li_id)
    check("H1 tick skips the real-platform campaign",
          r.status_code == 200 and r.json()["skippedReal"] >= 1 and li.actual_spend_cents == 0 and li.status == "scheduled",
          (r.json(), li.actual_spend_cents, li.status))
    r = client.post("/api/ads/campaigns", json={"platform": "tiktok", "name": "demo", "daily_budget_cents": 500,
                                                 "duration_days": 3, "origin": "manual_owner"})
    demo_id = r.json()["id"]
    client.post("/api/ads/tick?hours=24")
    check("H2 tick still advances demo (mock_) campaigns", campaign(demo_id).actual_spend_cents > 0,
          campaign(demo_id).actual_spend_cents)


class TestClientFactory:
    """Fresh TestClient with its own cookie jar (second signed-in user)."""

    def __init__(self, app) -> None:
        from fastapi.testclient import TestClient
        self._client = TestClient(app, follow_redirects=False)

    def __enter__(self):
        return self._client.__enter__()

    def __exit__(self, *exc) -> None:
        self._client.__exit__(*exc)


if __name__ == "__main__":
    main()
