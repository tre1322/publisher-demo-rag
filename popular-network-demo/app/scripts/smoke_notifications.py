"""Phase 2b smoke — approval reminders and the weekly summary arrive, and
their links open the right screen.

Run with:  uv run python -m app.scripts.smoke_notifications

Postmark is captured (no network) and Claude is mocked.

  A. Who gets the emails (owners + editors, active only)
  B. When the "posts waiting" reminder is due
  C. run_due sends it, once, with links to the right business
  D. Settings, demo accounts, deletion, and a missing Postmark key stop it
  E. The Monday summary: timing, content, the agent's queued suggestion
  F. A failed send retries without drafting a second suggestion
  G. Links survive sign-in and switch to the right business
  H. Admin preview emails go to the admin only and change nothing
  I. The loop is off outside production
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_notify_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ.pop("POPULAR_NOTIFY_LOOP", None)
os.environ["ENVIRONMENT"] = "development"

OWNER = ("lee@corner-cafe.example.com", "lee-correct-horse-battery")
EDITOR = ("max@corner-cafe.example.com", "max-correct-horse-battery")
VIEWER = ("viv@corner-cafe.example.com", "viv-correct-horse-battery")

SENT: list[dict] = []
SEND_OK = {"value": True}
CLAUDE_CALLS: list[dict] = []
MONDAY = datetime(2026, 10, 5, 14, 0)  # a Monday, 14:00 UTC


def capture(to_email, subject, html_body, text_body, *, kind):
    SENT.append({"to": to_email, "subject": subject, "html": html_body, "text": text_body, "kind": kind})
    return {"sent": True, "messageId": "smoke"} if SEND_OK["value"] else {"sent": False, "reason": "postmark_500"}


def fake_claude(*, purpose, system, user, schema, tools=None, effort="medium"):
    CLAUDE_CALLS.append({"purpose": purpose, "user": user, "schema": schema})
    platform = schema["properties"]["platform"]["enum"][0]
    return {"platform": platform, "title": "Pumpkin bars are back", "draft": "Pumpkin bars are back this week.",
            "why": "Fall is your busiest baking season."}


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.email as mailer
    import app.main as main_mod
    import app.onboarding as ob
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    with patch.object(mailer, "_send", capture), patch.object(ob, "_call_structured", fake_claude), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as editor, \
         TestClient(app, follow_redirects=False) as viewer, \
         TestClient(app, follow_redirects=False) as anon:
        check("I1 the notification loop stays off outside production", main_mod._NOTIFY_LOOP_STARTED is False)
        bootstrap_login(admin)
        _run(admin, owner, editor, viewer, anon)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 2b notifications smoke green ✓")


def _claim(client, invite, password):
    token = invite["claimUrl"].split("token=", 1)[1]
    r = client.post("/api/auth/invites/claim", json={"token": token, "password": password, "display_name": "x"})
    check(f"claim {invite['email']}", r.status_code == 200, r.text)


def _run(admin, owner, editor, viewer, anon) -> None:  # noqa: C901 — linear smoke
    from app import notifications as nt
    from app.db import SessionLocal
    from app.models import Approval, Business, BusinessUser, Post, SettingsRow, User
    from app.pwhash import hash_password

    # ---- A ---------------------------------------------------------------
    print("\nA. recipients")
    r = admin.post("/api/admin/businesses", json={"name": "Corner Cafe", "owner": "Lee Park", "owner_email": OWNER[0],
                                                  "location": "Windom, MN"})
    biz_id = r.json()["business"]["id"]
    _claim(owner, r.json()["invite"], OWNER[1])
    for client, (email, pw), role in ((editor, EDITOR, "editor"), (viewer, VIEWER, "viewer")):
        _claim(client, admin.post(f"/api/admin/businesses/{biz_id}/invites", json={"email": email, "role": role}).json(), pw)
    with SessionLocal() as db:
        gone = User(email="former@corner-cafe.example.com", password_hash=hash_password("x" * 12), is_active=False)
        db.add(gone)
        db.flush()
        db.add(BusinessUser(user_id=gone.id, business_id=biz_id, role="owner"))
        db.commit()
        check("A1 owners and editors only; viewers and deactivated people never",
              nt.recipients(db, biz_id) == sorted([OWNER[0], EDITOR[0]]), nt.recipients(db, biz_id))

    # ---- B ---------------------------------------------------------------
    print("\nB. when a reminder is due")
    now = MONDAY

    def a(age_h):
        return Approval(business_id=biz_id, kind="post", platform="fb", title="t", draft="d",
                        created_at=now - timedelta(hours=age_h))

    check("B1 nothing waiting → not due", not nt.reminder_due([], {}, now))
    check("B2 a fresh draft gets an hour's grace", not nt.reminder_due([a(0.5)], {}, now))
    check("B3 an hour-old draft → due", nt.reminder_due([a(2)], {}, now))
    last = {"approvalsAt": (now - timedelta(hours=5)).isoformat()}
    check("B4 never twice in a day", not nt.reminder_due([a(1.5)], last, now))
    last = {"approvalsAt": (now - timedelta(hours=30)).isoformat()}
    check("B5 a day later with nothing new → quiet", not nt.reminder_due([a(40)], last, now))
    check("B6 a day later with a new draft → due", nt.reminder_due([a(40), a(3)], last, now))
    last = {"approvalsAt": (now - timedelta(days=3, hours=1)).isoformat()}
    check("B7 three days of silence → one more nudge", nt.reminder_due([a(100)], last, now))

    # ---- C ---------------------------------------------------------------
    print("\nC. run_due sends the reminder")
    os.environ["POSTMARK_API_KEY"] = "smoke-not-a-real-key"
    with SessionLocal() as db:
        for title, day in (("Fresh scones", "2026-10-06"), ("Soup Friday", None)):
            db.add(Approval(business_id=biz_id, kind="post", platform="fb", title=title, draft="d",
                            created_at=now - timedelta(days=1, hours=3),
                            payload_json={"plannedDate": day} if day else None))
        db.add(Approval(business_id=biz_id, kind="boost", platform="fb_ig", title="Spend proposal", draft="d",
                        created_at=now - timedelta(days=1, hours=3)))
        db.commit()
    SENT.clear()
    with SessionLocal() as db:
        done = nt.run_due(db, now - timedelta(days=1))  # a Sunday: no weekly summary yet
    check("C1 reminder sent for this business", done["approvals"] == [biz_id], done)
    check("C2 one email each to the owner and the editor", sorted(m["to"] for m in SENT) == sorted([OWNER[0], EDITOR[0]]),
          [m["to"] for m in SENT])
    m = SENT[0]
    check("C3 subject counts the posts (not the spend proposal)", m["subject"] == "2 posts are waiting for your approval — Corner Cafe",
          m["subject"])
    check("C4 lists titles with their planned day", "Fresh scones" in m["html"] and "Tue, Oct 6" in m["html"]
          and "Soup Friday" in m["text"] and "Spend proposal" not in m["text"], m["text"])
    check("C5 the button opens Approvals on this business",
          f"https://dashboard.amplafai.com/?tab=approvals&amp;b={biz_id}" in m["html"]
          and f"/?tab=approvals&b={biz_id}" in m["text"], m["text"])
    check("C6 a settings link to turn it off", f"/?tab=settings&b={biz_id}" in m["text"] and "notification settings" in m["text"])
    check("C7 tagged for Postmark", m["kind"] == "approvals-reminder")
    SENT.clear()
    with SessionLocal() as db:
        nt.run_due(db, now - timedelta(days=1, minutes=-15))
    check("C8 the next tick sends nothing (state saved)", SENT == [], [x["to"] for x in SENT])

    # ---- D ---------------------------------------------------------------
    print("\nD. what stops it")
    with SessionLocal() as db:
        s = db.get(SettingsRow, biz_id)
        s.notifications_json = [{**n, "on": False} if n["key"] == "post_scheduled" else n for n in s.notifications_json]
        s.notify_state_json = {}
        db.commit()
        SENT.clear()
        nt.run_due(db, now - timedelta(days=1))
        check("D1 owner turned it off → no reminder", SENT == [])
        s = db.get(SettingsRow, biz_id)
        s.notifications_json = [{**n, "on": True} for n in s.notifications_json]
        db.commit()
        quadd_pending = db.query(Approval).filter(Approval.business_id == 1, Approval.decision.is_(None)).count()
        quadd_people = nt.recipients(db, 1)
        db.query(SettingsRow).filter(SettingsRow.business_id == 1).one().notify_state_json = {}
        db.commit()
        SENT.clear()
        nt.run_due(db, now - timedelta(days=1))
        check("D2 the demo account never gets reminders (it has drafts and an owner)",
              quadd_pending > 0 and quadd_people and not any("Quadd" in x["subject"] for x in SENT),
              (quadd_pending, quadd_people, [x["subject"] for x in SENT]))
        check("D3 a real client does", any("Corner Cafe" in x["subject"] for x in SENT))
        db.get(SettingsRow, biz_id).notify_state_json = {}
        db.get(Business, biz_id).deletion_due_at = now + timedelta(days=20)
        db.commit()
        SENT.clear()
        nt.run_due(db, now - timedelta(days=1))
        check("D4 a business scheduled for deletion gets nothing", SENT == [])
        db.get(Business, biz_id).deletion_due_at = None
        db.commit()
        os.environ.pop("POSTMARK_API_KEY")
        SENT.clear()
        check("D5 no Postmark key → the run does nothing", nt.run_due(db, now - timedelta(days=1)) == {"approvals": [], "weekly": []}
              and SENT == [])
        os.environ["POSTMARK_API_KEY"] = "smoke-not-a-real-key"

    # ---- E ---------------------------------------------------------------
    print("\nE. the Monday summary")
    check("E1 not on Sunday", not nt.weekly_due({}, MONDAY - timedelta(days=1)))
    check("E2 not before 13:00 UTC Monday", not nt.weekly_due({}, MONDAY.replace(hour=12, minute=59)))
    check("E3 due from 13:00 UTC Monday", nt.weekly_due({}, MONDAY.replace(hour=13)))
    check("E4 Tuesday catches up a missed Monday", nt.weekly_due({}, MONDAY + timedelta(days=1)))
    check("E5 not Wednesday", not nt.weekly_due({}, MONDAY + timedelta(days=2)))
    check("E6 once per week", not nt.weekly_due({"weeklyWeek": nt.week_key(MONDAY)}, MONDAY + timedelta(days=1)))
    with SessionLocal() as db:
        biz = db.get(Business, biz_id)
        biz.voice_brief_json = {"voice": "Warm and quick.", "amplify": [{"label": "Pumpkin bars", "detail": "Fall favorite"}]}
        biz.onboarding_json = {"status": "done", "answers": {"channels": ["gbp", "fb"]}}
        db.add(Post(business_id=biz_id, date="2026-10-01", platform="fb", status="approved", title="Back-to-school muffins",
                    draft="d", decided_at=MONDAY - timedelta(days=3)))
        db.add(Post(business_id=biz_id, date="2026-10-07", platform="gbp", status="approved", title="New fall hours",
                    draft="d", decided_at=MONDAY - timedelta(days=1)))
        db.add(Post(business_id=biz_id, date="2026-09-01", platform="fb", status="approved", title="Old news",
                    draft="d", decided_at=MONDAY - timedelta(days=30)))
        db.get(SettingsRow, biz_id).notify_state_json = {"approvalsAt": MONDAY.isoformat()}
        # Two posts are waiting: under the cap, so the agent adds one.
        db.commit()
    SENT.clear()
    CLAUDE_CALLS.clear()
    with SessionLocal() as db:
        done = nt.run_due(db, MONDAY)
    check("E7 summary sent Monday", done["weekly"] == [biz_id] and len(SENT) == 2, (done, len(SENT)))
    m = next(x for x in SENT if x["to"] == OWNER[0])
    check("E8 subject", m["subject"] == "Your week with Amplafai — Corner Cafe", m["subject"])
    check("E9 approved last week listed; older posts not", "Back-to-school muffins" in m["text"] and "New fall hours" in m["text"]
          and "Old news" not in m["text"], m["text"])
    check("E10 coming up this week", "Coming up this week" in m["text"] and "Wed, Oct 7" in m["text"], m["text"])
    check("E11 the agent drafted one post (owner's platforms, the brief, recent titles)",
          len(CLAUDE_CALLS) == 1 and CLAUDE_CALLS[0]["schema"]["properties"]["platform"]["enum"] == ["gbp", "fb"]
          and "Warm and quick" in CLAUDE_CALLS[0]["user"] and "Back-to-school muffins" in CLAUDE_CALLS[0]["user"],
          CLAUDE_CALLS[0]["user"][:300] if CLAUDE_CALLS else None)
    check("E12 the idea is in the email and waiting in Approvals", "Pumpkin bars are back" in m["text"]
          and "3 posts are waiting" in m["text"], m["text"])
    with SessionLocal() as db:
        sug = [x for x in db.query(Approval).filter(Approval.business_id == biz_id).all()
               if (x.payload_json or {}).get("source") == "weekly_suggestion"]
        check("E13 suggestion queued with a planned day", len(sug) == 1 and sug[0].platform == "gbp"
              and sug[0].payload_json["plannedDate"] == "2026-10-07", [(x.platform, x.payload_json) for x in sug])
    check("E14 links: approvals and calendar for this business", f"/?tab=approvals&b={biz_id}" in m["text"]
          and f"/?tab=calendar&b={biz_id}" in m["text"])
    check("E15 setup finished → no setup nudge", "setup isn't finished" not in m["text"])
    SENT.clear()
    CLAUDE_CALLS.clear()
    with SessionLocal() as db:
        nt.run_due(db, MONDAY + timedelta(hours=3))
    check("E16 not sent twice that week", not any(x["kind"] == "weekly-summary" for x in SENT) and CLAUDE_CALLS == [])

    # ---- F ---------------------------------------------------------------
    print("\nF. a failed send retries without a second suggestion")
    with SessionLocal() as db:
        db.get(SettingsRow, biz_id).notify_state_json = {"approvalsAt": MONDAY.isoformat()}
        for x in db.query(Approval).filter(Approval.business_id == biz_id, Approval.decision.is_(None)).all():
            if (x.payload_json or {}).get("source") == "weekly_suggestion":
                db.delete(x)
        db.commit()
    SEND_OK["value"] = False
    CLAUDE_CALLS.clear()
    with SessionLocal() as db:
        done = nt.run_due(db, MONDAY)
        state = db.get(SettingsRow, biz_id).notify_state_json
    check("F1 Postmark down → not marked sent, suggestion kept and remembered",
          done["weekly"] == [] and "weeklyWeek" not in state and state.get("suggestedWeek") == nt.week_key(MONDAY), state)
    SEND_OK["value"] = True
    CLAUDE_CALLS.clear()
    SENT.clear()
    with SessionLocal() as db:
        done = nt.run_due(db, MONDAY + timedelta(minutes=15))
        n_sug = sum(1 for x in db.query(Approval).filter(Approval.business_id == biz_id).all()
                    if (x.payload_json or {}).get("source") == "weekly_suggestion")
    check("F2 the retry sends, with no second Claude call or duplicate draft",
          done["weekly"] == [biz_id] and CLAUDE_CALLS == [] and n_sug == 1, (done, len(CLAUDE_CALLS), n_sug))
    with SessionLocal() as db:
        biz = db.get(Business, biz_id)
        biz.onboarding_json = {"status": "answering", "answers": {}}
        res = nt.send_weekly_summary(db, biz, MONDAY, to=["x@example.com"], suggest=False)
        db.rollback()
    m = SENT[-1]
    check("F3 setup unfinished → the summary nudges to finish it",
          res["sent"] == 1 and "setup isn't finished" in m["text"] and f"/?tab=onboarding&b={biz_id}" in m["text"], m["text"][:300])

    # ---- G ---------------------------------------------------------------
    print("\nG. links")
    target = f"/?tab=approvals&b={biz_id}"
    r = anon.get(target)
    check("G1 signed out → sign-in keeps where they were going",
          r.status_code == 302 and r.headers["location"] == f"/login?next=%2F%3Ftab%3Dapprovals%26b%3D{biz_id}",
          r.headers.get("location"))
    r = anon.get("/")
    check("G2 plain / still goes to /login", r.headers["location"] == "/login")
    r = owner.get(f"/login?next=%2F%3Ftab%3Dapprovals%26b%3D{biz_id}")
    check("G3 signed in → /login sends them on", r.status_code == 302 and r.headers["location"] == target,
          r.headers.get("location"))
    for bad in ("//evil.example.com", "https://evil.example.com", "/\\evil.example.com"):
        r = owner.get("/login", params={"next": bad})
        check(f"G4 next={bad!r} is refused (goes to /)", r.headers["location"] == "/", r.headers.get("location"))
    from app.main import ROOT
    login_html = (ROOT / "login.html").read_text(encoding="utf-8")
    dash = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    check("G5 login page follows a safe next after signing in", "safeNext()" in login_html and 'startsWith("//")' in login_html)
    check("G6 dashboard opens ?tab= views and switches to ?b=", "VIEW_IDS.includes(t)" in dash
          and "linkedBusiness" in dash and "/api/auth/switch" in dash)
    r = owner.post("/api/auth/switch", json={"business_id": biz_id})
    check("G7 switching to their own business works", r.status_code == 200, r.text)
    r = owner.post("/api/auth/switch", json={"business_id": 1})
    check("G8 a link to someone else's business is refused", r.status_code == 403, r.status_code)

    # ---- H ---------------------------------------------------------------
    print("\nH. admin previews")
    SENT.clear()
    with SessionLocal() as db:
        before = (db.query(Approval).filter(Approval.business_id == biz_id).count(),
                  dict(db.get(SettingsRow, biz_id).notify_state_json or {}))
    r = admin.post(f"/api/admin/businesses/{biz_id}/notifications/preview", json={"kind": "approvals"})
    check("H1 approvals preview → the admin only", r.status_code == 200 and r.json()["ok"]
          and [m["to"] for m in SENT] == ["smoke@example.com"], (r.text, [m["to"] for m in SENT]))
    r = admin.post(f"/api/admin/businesses/{biz_id}/notifications/preview", json={"kind": "weekly"})
    check("H2 weekly preview → the admin only", r.status_code == 200 and SENT[-1]["to"] == "smoke@example.com"
          and SENT[-1]["kind"] == "weekly-summary", r.text)
    with SessionLocal() as db:
        after = (db.query(Approval).filter(Approval.business_id == biz_id).count(),
                 dict(db.get(SettingsRow, biz_id).notify_state_json or {}))
    check("H3 previews add no drafts and change no send state", before == after, (before, after))
    r = admin.post(f"/api/admin/businesses/{biz_id}/notifications/preview", json={"kind": "sms"})
    check("H4 unknown kind → 422", r.status_code == 422)
    r = owner.post(f"/api/admin/businesses/{biz_id}/notifications/preview", json={"kind": "approvals"})
    check("H5 owners can't use previews", r.status_code == 403, r.status_code)
    r = admin.post("/api/admin/businesses", json={"name": "Quiet Co", "owner": "Q"})
    r = admin.post(f"/api/admin/businesses/{r.json()['business']['id']}/notifications/preview", json={"kind": "approvals"})
    check("H6 nothing pending → 409 with a reason", r.status_code == 409 and "Nothing is waiting" in r.json()["detail"], r.text)


if __name__ == "__main__":
    main()
