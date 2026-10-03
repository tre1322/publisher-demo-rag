"""Phase 2a smoke — a new owner goes from invite to first approved post
without help, through the setup wizard.

Run with:  uv run python -m app.scripts.smoke_onboarding

Claude is mocked (no network): `_call_structured` returns canned JSON and
records what it was sent. Section J drives the real parsing code with a fake
Anthropic client instead.

  A. Invite → the owner signs in and lands needing setup
  B. Answers are validated and saved as progress
  C. Claude drafts the brief from the answers (+ website, read by Claude)
  D. The owner edits the brief; bad edits are refused
  E. Finish → the brief goes live and the first week is planned
  F. Approving a planned draft puts it on its planned day
  G. Failures are reported and retryable; a retry doesn't double drafts
  H. Roles: viewers can look, not edit
  I. A job killed mid-run is noticed; jobs really run in the background
  J. The Claude call handles refusals, paused turns, and bad output
  K. Quadd (demo) and interview-uploaded briefs skip the wizard
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_onboarding_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"

OWNER_EMAIL = "dana@lakeside-bakery.example.com"
OWNER_PW = "dana-correct-horse-battery"
VIEWER_EMAIL = "sam@lakeside-bakery.example.com"
VIEWER_PW = "sam-correct-horse-battery"

ANSWERS = {
    "what_you_do": "We bake bread, kolaches and birthday cakes in Windom.",
    "favorite_customer": "Folks who stop in on the way to work and order cakes for every family event.",
    "trigger": "Somebody's birthday sneaks up on them.",
    "grow": "Custom cakes\nWedding orders",
    "steady": "Daily bread",
    "play_down": "Gluten-free (shared kitchen)",
    "never_say": "No 'artisanal'. No emojis.",
    "seasons": "Swamped at graduation and Christmas. Slow in August.",
    "channels": ["fb", "gbp"],
    "writing_sample": "Honestly? Because we start at 4am and you can taste it. Call ahead for kolaches.",
}

BRIEF_OUT = {
    "voice": "Short, warm-but-dry sentences. Says 'Honestly?' and 'call ahead'.",
    "amplify": [{"label": "Custom cakes", "detail": "Lead with birthdays sneaking up."},
                {"label": "Wedding orders", "detail": "Book early."}],
    "maintain": [{"label": "Daily bread", "detail": "Mention the 4am start."}],
    "mute": [{"label": "Gluten-free", "detail": "Shared kitchen; don't promote."}],
    "audience": "Commuters and families in Windom.",
    "value_prop": "Baked from 4am, by people you know.",
    "customer_language": ["birthday sneaks up on them"],
    "proof_points": [],
    "constraints": ["Never say 'artisanal'.", "Never use emojis."],
    "seasonal_patterns": ["Graduation and Christmas rush", "Slow in August"],
    "notes": "",
    "website_summary": "The site lists cakes, kolaches and hours.",
}


def _plan_out(channels):
    return {
        "audience": "Commuters and families in Windom.",
        "value_prop": "Fresh from 4am.",
        "pulls": ["Baked at 4am", "Knows your family"],
        "pushes": ["Grocery-store cakes taste like the box"],
        "goals": [{"label": "Posts approved per week", "target": 3, "unit": "posts/wk"},
                  {"label": "Cake orders from posts", "target": 4, "unit": "orders/mo"},
                  {"label": "New Google reviews", "target": 5, "unit": "reviews"}],
        "channel_mix": [{"platform": channels[0], "pct": 60}, {"platform": channels[-1], "pct": 41}],
        "posts": [
            {"platform": channels[0], "day": 1, "title": "Birthday cakes", "draft": "Birthday sneak up on you? Call ahead.", "why": "Top AMPLIFY item."},
            {"platform": channels[-1], "day": 3, "title": "Wedding season", "draft": "Booking wedding cakes now.", "why": "Second AMPLIFY item."},
            {"platform": channels[0], "day": 3, "title": "4am bread", "draft": "Bread's out of the oven at 6.", "why": "Keeps daily bread visible."},
            {"platform": "ig", "day": 5, "title": "Not a chosen platform", "draft": "x", "why": "should be dropped"},
        ],
    }


CALLS: list[dict] = []
FAIL: dict = {"brief": None, "plan": None}


def fake_call(*, purpose, system, user, schema, tools=None, effort="medium"):
    CALLS.append({"purpose": purpose, "system": system, "user": user, "schema": schema, "tools": tools})
    from app.onboarding import OnboardingError
    if FAIL.get(purpose):
        raise OnboardingError(FAIL[purpose])
    if purpose == "brief":
        return json.loads(json.dumps(BRIEF_OUT))
    channels = schema["properties"]["posts"]["items"]["properties"]["platform"]["enum"]
    return _plan_out(channels)


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.onboarding as ob
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

    inline = lambda target, business_id: target(business_id)  # noqa: E731
    with patch.object(ob, "_call_structured", fake_call), patch.object(ob, "start_job", inline), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as viewer:
        bootstrap_login(admin)
        _run(admin, owner, viewer)
        _background(owner)
    _claude_call_handling()

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 2a onboarding smoke green ✓")


STATE: dict = {}


def _claim(client, invite, password, name):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": name})
    check(f"claim invite for {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner, viewer) -> None:  # noqa: C901 — linear smoke
    from app.agent.system_prompt import build_system_prompt
    from app.db import SessionLocal
    from app.models import Approval, Business, MarketingPlan

    # ---- A ---------------------------------------------------------------
    print("\nA. invite → owner lands needing setup")
    r = admin.post("/api/admin/businesses", json={
        "name": "Lakeside Bakery", "owner": "Dana Lee", "owner_email": OWNER_EMAIL,
        "location": "Windom, MN", "tier": 2,
    })
    check("A1 admin creates the business + owner invite", r.status_code == 200, r.text[:300])
    biz_id = STATE["biz_id"] = r.json()["business"]["id"]
    check("A2 admin console shows setup not started", r.json()["business"]["onboardingStatus"] == "not_started",
          r.json()["business"].get("onboardingStatus"))
    _claim(owner, r.json()["invite"], OWNER_PW, "Dana Lee")
    boot = owner.get("/api/bootstrap").json()
    check("A3 owner lands on their business", boot["business"]["id"] == biz_id)
    check("A4 bootstrap says setup is needed", boot["onboarding"] == {"status": "not_started", "needed": True}, boot["onboarding"])
    check("A5 Home's first item is setup, pointing at the wizard",
          boot["attention"] and boot["attention"][0]["target"] == "onboarding" and boot["attention"][0]["cta"] == "Start setup",
          boot["attention"])
    check("A6 no stale Phase 1 static items", not any("voice interview" in a["title"].lower() for a in boot["attention"]),
          [a["title"] for a in boot["attention"]])
    r = owner.get("/api/onboarding")
    o = r.json()
    check("A7 GET onboarding → questions, steps, channels", r.status_code == 200 and len(o["questions"]) >= 10
          and [s["step"] for s in o["steps"]] == [1, 2, 3] and {c["key"] for c in o["channels"]} == {"fb", "ig", "gbp", "web"}, o.keys())
    check("A8 owner can edit", o["onboarding"]["canEdit"] is True)

    # ---- B ---------------------------------------------------------------
    print("\nB. answers")
    r = owner.post("/api/onboarding/draft")
    check("B1 drafting before answering → 422 naming what's missing",
          r.status_code == 422 and "Answer these first" in r.json()["detail"], r.text)
    r = owner.put("/api/onboarding/answers", json={"answers": {"shoe_size": "9"}})
    check("B2 unknown question → 422", r.status_code == 422 and "Unknown question" in r.json()["detail"], r.text)
    r = owner.put("/api/onboarding/answers", json={"answers": {"channels": ["fb", "myspace"]}})
    check("B3 unknown channel → 422", r.status_code == 422, r.text)
    r = owner.put("/api/onboarding/answers", json={"answers": {}, "website": "not a website"})
    check("B4 junk website → 422 with an example", r.status_code == 422 and "example.com" in r.json()["detail"], r.text)
    r = owner.put("/api/onboarding/answers", json={"answers": {"what_you_do": "x" * 2001}})
    check("B5 over-long answer → 422", r.status_code == 422, r.status_code)
    r = owner.put("/api/onboarding/answers", json={"answers": {"what_you_do": ANSWERS["what_you_do"], "trigger": "temp"},
                                                   "website": "www.lakeside-bakery.example.com"})
    o = r.json()["onboarding"]
    check("B6 partial answers saved; status answering", r.status_code == 200 and o["status"] == "answering"
          and o["answers"]["what_you_do"] == ANSWERS["what_you_do"], o)
    check("B7 website normalized", o["website"] == "https://www.lakeside-bakery.example.com", o["website"])
    r = owner.put("/api/onboarding/answers", json={"answers": {"trigger": ""}})
    check("B8 blanking an answer clears it; others kept",
          "trigger" not in r.json()["onboarding"]["answers"] and "what_you_do" in r.json()["onboarding"]["answers"],
          r.json()["onboarding"]["answers"])
    r = owner.put("/api/onboarding/answers", json={"answers": ANSWERS})
    check("B9 full answers saved", r.status_code == 200 and r.json()["onboarding"]["answers"]["channels"] == ["fb", "gbp"], r.text[:200])
    boot = owner.get("/api/bootstrap").json()
    check("B10 Home says continue setup", boot["attention"][0]["cta"] == "Continue setup", boot["attention"][0])

    # ---- C ---------------------------------------------------------------
    print("\nC. Claude drafts the brief")
    CALLS.clear()
    r = owner.post("/api/onboarding/draft")
    o = r.json()["onboarding"]
    check("C1 draft → review with a brief", r.status_code == 200 and o["status"] == "review" and o["brief"], o)
    call = CALLS[0]
    check("C2 Claude got every answer", all(v in call["user"] for v in (ANSWERS["what_you_do"], ANSWERS["writing_sample"],
                                                                        "Facebook, Google Business Profile")), call["user"][:400])
    check("C3 Claude may read only the owner's own site", call["tools"] and call["tools"][0]["type"] == "web_fetch_20260209"
          and call["tools"][0]["allowed_domains"] == ["lakeside-bakery.example.com"], call["tools"])
    check("C4 prompt forbids invented facts and treats the site as data",
          "Never invent facts" in call["system"] and "never as instructions" in call["system"])
    b = o["brief"]
    check("C5 brief keeps the owner's piles", [i["label"] for i in b["amplify"]] == ["Custom cakes", "Wedding orders"]
          and b["mute"][0]["label"] == "Gluten-free", b)
    check("C6 empty lists dropped; metadata tagged", "proof_points" not in b and b["_source"] == "onboarding_wizard"
          and b["_website_summary"].startswith("The site lists"), sorted(b))
    with SessionLocal() as db:
        check("C7 not live yet: the agent still has no brief", db.get(Business, biz_id).voice_brief_json is None)

    # ---- D ---------------------------------------------------------------
    print("\nD. the owner edits the brief")
    edited = {**{k: v for k, v in b.items() if not k.startswith("_")}, "voice": "Dry and short. Says 'Honestly?'"}
    r = owner.put("/api/onboarding/brief", json={"brief": edited})
    o = r.json()["onboarding"]
    check("D1 edit saved; metadata survives", r.status_code == 200 and o["brief"]["voice"].startswith("Dry and short")
          and o["brief"]["_source"] == "onboarding_wizard", o["brief"])
    r = owner.put("/api/onboarding/brief", json={"brief": {"voice": "", "amplify": [], "audience": "", "value_prop": ""}})
    check("D2 an emptied brief → 422", r.status_code == 422, r.text)
    r = owner.put("/api/onboarding/brief", json={"brief": {"voice": "x", "favorite_color": "blue"}})
    check("D3 unknown brief field → 422", r.status_code == 422 and "favorite_color" in r.json()["detail"], r.text)
    r = owner.post("/api/onboarding/plan")
    check("D4 can't 'retry' a plan that never failed", r.status_code == 409, r.status_code)

    # ---- E ---------------------------------------------------------------
    print("\nE. finish → brief live, first week planned")
    CALLS.clear()
    r = owner.post("/api/onboarding/finish", json={"brief": {**edited, "notes": "Sponsors the county fair."}})
    o = r.json()["onboarding"]
    check("E1 finish → done with 3 planned drafts", r.status_code == 200 and o["status"] == "done" and o["planned"] == 3, o)
    plan_call = CALLS[0]
    check("E2 planner got the edited brief, not the draft", "Sponsors the county fair" in plan_call["user"]
          and "Dry and short" in plan_call["user"] and "_source" not in plan_call["user"], plan_call["user"][:300])
    check("E3 drafts limited to the owner's platforms",
          plan_call["schema"]["properties"]["posts"]["items"]["properties"]["platform"]["enum"] == ["fb", "gbp"])
    with SessionLocal() as db:
        biz = db.get(Business, biz_id)
        check("E4 brief is live on the business", biz.voice_brief_json["voice"].startswith("Dry and short")
              and biz.voice_interview == "wizard", biz.voice_interview)
        prompt = build_system_prompt(db, biz_id)
        check("E5 the agent's system prompt now carries the voice", "Dry and short" in prompt and "Custom cakes" in prompt)
        mp = db.get(MarketingPlan, biz_id)
        check("E6 marketing plan filled", mp.audience.startswith("Commuters") and mp.switching_json["pulls"]
              and len(mp.q3_goals_json) == 3 and all(g["current"] == 0 for g in mp.q3_goals_json), mp.audience)
        check("E7 channel mix adds to exactly 100", sum(c["pct"] for c in mp.channels_json) == 100
              and {c["platform"] for c in mp.channels_json} == {"fb", "gbp"}, mp.channels_json)
        apps = db.query(Approval).filter(Approval.business_id == biz_id).order_by(Approval.id).all()
        planned = [a.payload_json["plannedDate"] for a in apps]
        check("E8 three drafts queued, off-platform one dropped", len(apps) == 3 and {a.platform for a in apps} <= {"fb", "gbp"},
              [(a.platform, a.title) for a in apps])
        today = date.today()
        check("E9 planned on three different days this coming week", len(set(planned)) == 3
              and all(today < date.fromisoformat(d) <= today + timedelta(days=7) for d in planned), planned)
        check("E10 each card says when and why", all(a.note.startswith("Planned for ") for a in apps), apps[0].note)
    boot = owner.get("/api/bootstrap").json()
    check("E11 bootstrap: setup done", boot["onboarding"] == {"status": "done", "needed": False}, boot["onboarding"])
    check("E12 Home now points at the 3 waiting posts",
          boot["attention"][0]["target"] == "approvals" and boot["attention"][0]["title"].startswith("3 posts waiting"),
          boot["attention"])
    check("E13 approvals carry their planned dates", all(a.get("plannedDate") for a in boot["approvals"]), boot["approvals"])
    check("E14 week recap records it", any("Voice brief created" in w["text"] for w in boot["weekRecap"]), boot["weekRecap"])
    r = owner.put("/api/onboarding/answers", json={"answers": {"seasons": "x"}})
    check("E15 answers locked after finishing (Redo setup unlocks)", r.status_code == 409, r.status_code)
    lst = admin.get("/api/admin/businesses").json()["businesses"]
    check("E16 admin console shows setup done", next(b for b in lst if b["id"] == biz_id)["onboardingStatus"] == "done")

    # ---- F ---------------------------------------------------------------
    print("\nF. approve the first post")
    first = boot["approvals"][0]
    r = owner.post(f"/api/approvals/{first['internalId']}/decide", json={"decision": "approve"})
    post = r.json().get("post") or {}
    check("F1 approve → a real post on its planned day", r.status_code == 200 and post.get("date") == first["plannedDate"]
          and post.get("status") == "approved", r.text[:300])
    boot = owner.get("/api/bootstrap").json()
    check("F2 the post is on the calendar; 2 left to review",
          any(p["internalId"] == post["internalId"] for p in boot["posts"]) and len(boot["approvals"]) == 2)
    check("F3 Home count follows", boot["attention"][0]["title"].startswith("2 posts waiting"), boot["attention"][0])
    with SessionLocal() as db:
        check("F3b the approval records the decision", db.get(Approval, first["internalId"]).decision == "approved")
        a2 = Approval(business_id=biz_id, kind="post", platform="fb", title="Past", draft="x",
                      payload_json={"plannedDate": "2020-01-01"})
        db.add(a2)
        db.commit()
        past_id = a2.id
    r = owner.post(f"/api/approvals/{past_id}/decide", json={"decision": "approve"})
    check("F4 a planned day already gone → lands today", r.json()["post"]["date"] == datetime.utcnow().strftime("%Y-%m-%d"),
          r.json()["post"]["date"])

    # ---- G ---------------------------------------------------------------
    print("\nG. failures are reported and retryable")
    r = owner.post("/api/onboarding/restart")
    check("G1 Redo setup → answering, answers kept, marked as a redo", r.json()["onboarding"]["status"] == "answering"
          and r.json()["onboarding"]["answers"]["grow"] == ANSWERS["grow"] and r.json()["onboarding"]["redo"] is True,
          r.json()["onboarding"]["status"])
    with SessionLocal() as db:
        check("G2 the live brief keeps working during a redo", db.get(Business, biz_id).voice_brief_json is not None)
    FAIL["brief"] = "The AI service is busy right now. Wait a minute and try again."
    r = owner.post("/api/onboarding/draft")
    o = r.json()["onboarding"]
    check("G3 a failed draft returns to the questions with the reason", o["status"] in ("answering", "review")
          and o["error"] == FAIL["brief"], o)
    FAIL["brief"] = None
    r = owner.post("/api/onboarding/draft")
    check("G4 retry drafts fine", r.json()["onboarding"]["status"] == "review" and r.json()["onboarding"]["error"] is None)
    FAIL["plan"] = "The AI didn't return any usable drafts. Try again."
    r = owner.post("/api/onboarding/finish", json={})
    o = r.json()["onboarding"]
    check("G5 a failed plan → plan_failed with the reason", o["status"] == "plan_failed" and o["error"] == FAIL["plan"], o)
    boot = owner.get("/api/bootstrap").json()
    check("G6 Home still nudges to finish", boot["attention"][0]["target"] == "onboarding", boot["attention"][0])
    FAIL["plan"] = None
    r = owner.put("/api/onboarding/brief", json={"brief": {**edited, "voice": "Edited while failed."}})
    check("G7 the brief can be edited while the plan is failed", r.status_code == 200, r.text)
    with SessionLocal() as db:
        before = db.query(Approval).filter(Approval.business_id == biz_id, Approval.decision.is_(None)).count()
    r = owner.post("/api/onboarding/plan")
    check("G8 retry → done", r.json()["onboarding"]["status"] == "done", r.json()["onboarding"])
    with SessionLocal() as db:
        undecided = db.query(Approval).filter(Approval.business_id == biz_id, Approval.decision.is_(None)).all()
        first_week = [a for a in undecided if (a.payload_json or {}).get("source") == "first_week"]
        check("G9 the retry replaced its own undecided drafts (no doubles)", len(first_week) == 3,
              (before, len(first_week)))
        check("G10 the retry used the edit", db.get(Business, biz_id).voice_brief_json["voice"] == "Edited while failed.")

    # ---- H ---------------------------------------------------------------
    print("\nH. roles")
    r = admin.post(f"/api/admin/businesses/{biz_id}/invites", json={"email": VIEWER_EMAIL, "role": "viewer"})
    check("H0 viewer invited", r.status_code == 200, r.text)
    _claim(viewer, r.json(), VIEWER_PW, "Sam")
    r = viewer.get("/api/onboarding")
    check("H1 viewer can look (canEdit false)", r.status_code == 200 and r.json()["onboarding"]["canEdit"] is False)
    for method, path, body in (("put", "/api/onboarding/answers", {"answers": {}}), ("post", "/api/onboarding/draft", None),
                               ("post", "/api/onboarding/skip", None), ("post", "/api/onboarding/restart", None)):
        r = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
        check(f"H2 viewer {method.upper()} {path} → 403", r.status_code == 403, r.status_code)

    # ---- I (stale) -------------------------------------------------------
    print("\nI. a job that died with the server")
    with SessionLocal() as db:
        biz = db.get(Business, biz_id)
        state = dict(biz.onboarding_json)
        state.update(status="drafting", jobStartedAt=(datetime.utcnow() - timedelta(minutes=30)).isoformat())
        biz.onboarding_json = state
        db.commit()
    o = owner.get("/api/onboarding").json()["onboarding"]
    check("I1 stuck 'drafting' is reported, not spun forever", o["status"] == "review" and "took too long" in o["error"], o)
    with SessionLocal() as db:  # back to done, then start a redo
        biz = db.get(Business, biz_id)
        biz.onboarding_json = {**biz.onboarding_json, "status": "done", "error": None}
        db.commit()
    owner.post("/api/onboarding/restart")
    r = owner.post("/api/onboarding/skip")
    check("I2 leaving a redo keeps the working brief (done, no nudge)", r.json()["onboarding"]["status"] == "done"
          and owner.get("/api/bootstrap").json()["onboarding"]["needed"] is False, r.json()["onboarding"])
    with SessionLocal() as db:
        biz = db.get(Business, biz_id)
        biz.onboarding_json = {**biz.onboarding_json, "status": "answering", "redo": False}
        brief, biz.voice_brief_json = biz.voice_brief_json, None
        db.commit()
    r = owner.post("/api/onboarding/skip")
    check("I2b skipping a first-time setup → skipped; Home keeps the nudge", r.json()["onboarding"]["status"] == "skipped"
          and owner.get("/api/bootstrap").json()["attention"][0]["target"] == "onboarding")
    with SessionLocal() as db:
        db.get(Business, biz_id).voice_brief_json = brief
        db.commit()

    # ---- K ---------------------------------------------------------------
    print("\nK. who skips the wizard")
    boot = admin.get("/api/bootstrap").json()
    check("K1 Quadd (demo, has a brief) counts as set up", boot["business"]["isDemo"] is True
          and boot["onboarding"] == {"status": "done", "needed": False}, boot["onboarding"])
    check("K2 Quadd's curated Home feed is untouched", not any(a.get("target") == "onboarding" for a in boot["attention"]))
    r = admin.post("/api/admin/businesses", json={"name": "Interview Co", "owner": "Ivy"})
    other = r.json()["business"]["id"]
    r = admin.put(f"/api/admin/businesses/{other}/voice-brief", json={"brief": {"voice": "From the recorded interview."}})
    check("K3 a brief uploaded from the recorded interview marks setup done",
          r.status_code == 200 and r.json()["business"]["onboardingStatus"] == "done", r.text[:200])
    r = owner.get("/api/onboarding")
    check("K4 the owner's wizard is still their own business", r.json()["onboarding"]["answers"].get("grow") == ANSWERS["grow"])


def _background(owner) -> None:
    import threading

    import app.onboarding as ob
    from app.db import SessionLocal
    from app.models import Business

    print("\nI. jobs run off the request thread")
    biz_id = STATE["biz_id"]

    def slow(**kw):
        time.sleep(0.6)
        return fake_call(**kw)

    def threaded(target, bid):  # what the real start_job does
        threading.Thread(target=target, args=(bid,), daemon=True).start()

    with patch.object(ob, "_call_structured", slow), patch.object(ob, "start_job", threaded):
        owner.post("/api/onboarding/restart")
        t0 = time.time()
        r = owner.post("/api/onboarding/draft")
        check("I3 POST /draft answers at once with 'drafting'", r.json()["onboarding"]["status"] == "drafting"
              and time.time() - t0 < 0.5, (r.json()["onboarding"]["status"], round(time.time() - t0, 2)))
        r = owner.post("/api/onboarding/draft")
        check("I4 a second draft while busy → 409", r.status_code == 409 and "Still drafting" in r.json()["detail"], r.text)
        deadline = time.time() + 10
        status = "drafting"
        while status == "drafting" and time.time() < deadline:
            time.sleep(0.2)
            status = owner.get("/api/onboarding").json()["onboarding"]["status"]
        check("I5 polling sees the job finish", status == "review", status)
    with SessionLocal() as db:
        check("I6 business row intact after the threaded write", db.get(Business, biz_id).name == "Lakeside Bakery")


def _claude_call_handling() -> None:
    """Drive the real _call_structured with a fake Anthropic client."""
    import anthropic

    import app.onboarding as ob

    print("\nJ. the Claude call itself")

    def resp(stop, blocks, model="claude-opus-5-5"):
        return SimpleNamespace(stop_reason=stop, content=blocks, model=model,
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    def text(s):
        return SimpleNamespace(type="text", text=s)

    seen: list[dict] = []
    queue: list = []

    class FakeMessages:
        def create(self, **kw):
            seen.append(kw)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=FakeMessages())

    args = dict(purpose="brief", system="s", user="u", schema={"type": "object"})
    with patch.object(anthropic, "Anthropic", FakeClient):
        queue[:] = [resp("end_turn", [text('{"voice": "ok"}')])]
        out = ob._call_structured(**args, tools=[{"type": "web_fetch_20260209", "name": "web_fetch"}])
        kw = seen[-1]
        check("J1 parses the JSON answer", out == {"voice": "ok"}, out)
        check("J2 asks for schema-checked JSON on Opus 5.5 with refusal fallback",
              kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
              and kw["extra_body"] == {"fallbacks": "default"} and kw["betas"] == [ob.FALLBACK_BETA], kw.keys())
        queue[:] = [resp("pause_turn", [SimpleNamespace(type="server_tool_use")]), resp("end_turn", [text('{"a": 1}')])]
        seen.clear()
        out = ob._call_structured(**args)
        check("J3 a paused turn is resumed", out == {"a": 1} and len(seen) == 2
              and seen[1]["messages"][-1]["role"] == "assistant", len(seen))
        for stop, blocks, needle in (("refusal", [], "declined"), ("max_tokens", [text("{")], "cut off"),
                                     ("end_turn", [text("not json")], "unreadable")):
            queue[:] = [resp(stop, blocks)]
            try:
                ob._call_structured(**args)
                check(f"J4 {stop} raises", False)
            except ob.OnboardingError as e:
                check(f"J4 {stop} → owner-facing error", needle in str(e), str(e))
        req = SimpleNamespace(method="POST", url="https://api.anthropic.com")
        bad_tool = anthropic.BadRequestError("web_fetch tool not enabled", response=SimpleNamespace(
            status_code=400, headers={}, request=req), body=None)
        queue[:] = [bad_tool, resp("end_turn", [text('{"b": 2}')])]
        seen.clear()
        out = ob._call_structured(**args, tools=[{"type": "web_fetch_20260209", "name": "web_fetch"}])
        check("J5 website reading unavailable → drafts from answers alone", out == {"b": 2} and "tools" not in seen[-1], seen[-1].keys())
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ob._call_structured(**args)
            check("J6 missing key raises", False)
        except ob.OnboardingError as e:
            check("J6 no API key → plain message, no call", "isn't configured" in str(e), str(e))
        finally:
            if saved:
                os.environ["ANTHROPIC_API_KEY"] = saved
    check("J7 website domain strips www", ob._domain("https://www.example.com/about") == "example.com")


if __name__ == "__main__":
    main()
