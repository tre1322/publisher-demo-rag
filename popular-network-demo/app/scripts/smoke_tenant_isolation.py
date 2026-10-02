"""Phase 1a smoke — a second business's people only ever touch that business.

Run with:  uv run python -m app.scripts.smoke_tenant_isolation

Born from Phase 0: posts, reviews, and the marketing plan took business_id
from the request body (default 1), so a second client's posts would have been
filed under Quadd. Roles existed but almost nothing enforced them, so a
view-only teammate could publish, spend, and change settings.

Covers, in order:
  A. Writes land on the signed-in business, whatever the body says
  B. Another business's records read as "not found" (incl. for superusers)
  C. Viewers are read-only on every write path (403 with a readable reason)
  D. Viewers get the assistant without tools; owners get tools
  E. A client's chatbot relay can ingest without a dashboard login
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_tenant_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")

OWNER2 = ("owner-b@example.com", "owner-b-correct-horse")
VIEWER2 = ("viewer-b@example.com", "viewer-b-correct-horse")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        _fail(f"{label} — {detail}")
    print(f"  ok  {label}")


# Minimal Anthropic stand-in that records what each stream() was offered.
class _RecordingMessages:
    calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        final = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )

        class _S:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def __iter__(self_inner):
                yield SimpleNamespace(type="content_block_delta",
                                      delta=SimpleNamespace(type="text_delta", text="ok"))

            def get_final_message(self_inner):
                return final

        return _S()


class _RecordingAnthropic:
    messages = _RecordingMessages()

    def __init__(self, api_key: str | None = None) -> None:
        pass


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)  # hermetic; see smoke_money_path
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-never-used")

    with TestClient(app, follow_redirects=False) as admin:
        bootstrap_login(admin)  # superuser, active on Quadd (business 1)
        ids = _seed_business_b()
        with TestClient(app, follow_redirects=False) as owner, \
             TestClient(app, follow_redirects=False) as viewer, \
             TestClient(app, follow_redirects=False) as relay:
            _login(owner, *OWNER2)
            _login(viewer, *VIEWER2)
            _run(admin, owner, viewer, relay, ids)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 1a tenant-isolation smoke green ✓")


def _login(client, email: str, password: str) -> None:
    r = client.post("/api/auth/login", json={"email": email, "password": password, "active_business_id": 2})
    if r.status_code != 200:
        _fail(f"login {email} → {r.status_code} {r.text}")


def _seed_business_b() -> dict:
    """Business 2 with an owner + a viewer, one review, and a plan to edit."""
    from app.db import SessionLocal
    from app.models import Business, BusinessUser, MarketingPlan, Review, SettingsRow, User
    from app.pwhash import hash_password

    with SessionLocal() as db:
        db.add(Business(
            id=2, slug="tenant_biz_b", name="Biz B", owner="B Owner", owner_initials="BO",
            location="Elsewhere, MN", publisher="Other Paper", phone="(000) 000-0000",
            tier=4, tier_label="Tier 4", monthly_price=799, joined_days_ago=0,
            joined_date="Today", voice_interview="pending",
        ))
        db.flush()
        db.add(SettingsRow(business_id=2, cadence="weekly", notifications_json=[]))
        db.add(MarketingPlan(business_id=2, audience="B audience", value_prop="B value",
                             switching_json={}, customer_language_json=[], proof_points_json=[],
                             channels_json=[], q3_goals_json=[]))
        review = Review(business_id=2, platform="google", stars=5, when_label="today",
                        author="Pat", body="Great.")
        db.add(review)
        for (email, pw), role in ((OWNER2, "owner"), (VIEWER2, "viewer")):
            u = User(email=email, password_hash=hash_password(pw), display_name=role, is_superuser=False, is_active=True)
            db.add(u)
            db.flush()
            db.add(BusinessUser(user_id=u.id, business_id=2, role=role))
        db.commit()
        return {"review_b": review.id}


def _run(admin, owner, viewer, relay, ids: dict) -> None:  # noqa: C901 — linear smoke
    from app.db import SessionLocal
    from app.models import Approval, ChatbotConversation, MarketingPlan, Post, Review

    with SessionLocal() as db:
        quadd_post = db.query(Post).filter(Post.business_id == 1, Post.status != "published").first()
        quadd_review = db.query(Review).filter(Review.business_id == 1).first()
        quadd_approval = db.query(Approval).filter(Approval.business_id == 1, Approval.decision.is_(None)).first()
        quadd_plan_audience = db.get(MarketingPlan, 1).audience
        quadd_post_title = quadd_post.title
        quadd_review_response = quadd_review.owner_response

    # ---- A. writes land on the signed-in business ---------------------------
    print("\nA. writes land on the signed-in business")
    r = owner.post("/api/posts", json={
        "business_id": 1, "platform": "fb", "status": "pending",
        "title": "B's first post", "draft": "Hello from Biz B."})
    check("A1 owner B creates a post (body says business 1)", r.status_code == 200, r.text)
    post_b = r.json()["post"]["internalId"]
    approval_b = r.json()["approval"]["internalId"]
    with SessionLocal() as db:
        check("A2 post filed under business 2", db.get(Post, post_b).business_id == 2, db.get(Post, post_b).business_id)
        check("A3 its approval filed under business 2", db.get(Approval, approval_b).business_id == 2)

    r = owner.put("/api/marketing-plan", json={"audience": "B's real audience", "business_id": 1})
    check("A4 owner B edits the plan (body says business 1)", r.status_code == 200, r.text)
    with SessionLocal() as db:
        check("A5 business 2 plan changed", db.get(MarketingPlan, 2).audience == "B's real audience")
        check("A6 Quadd plan untouched", db.get(MarketingPlan, 1).audience == quadd_plan_audience)

    boot = owner.get("/api/bootstrap").json()
    check("A7 B's bootstrap shows only B's posts",
          [p["internalId"] for p in boot["posts"]] == [post_b], [p["internalId"] for p in boot["posts"]])
    check("A8 B's bootstrap shows only B's approvals",
          [a["internalId"] for a in boot["approvals"]] == [approval_b])
    check("A9 owner access flags", boot["access"]["role"] == "owner" and boot["access"]["can"]["publish_post"] is True,
          boot["access"])

    # ---- B. other businesses' records are "not found" ------------------------
    print("\nB. cross-business ids read as not found")
    r = owner.put(f"/api/posts/{quadd_post.id}", json={"title": "hijacked"})
    check("B1 owner B edits a Quadd post → 404", r.status_code == 404, r.status_code)
    r = owner.post(f"/api/reviews/{quadd_review.id}/respond", json={"response": "hijacked", "action": "send"})
    check("B2 owner B replies to a Quadd review → 404", r.status_code == 404, r.status_code)
    if quadd_approval is not None:
        r = owner.post(f"/api/approvals/{quadd_approval.id}/decide", json={"decision": "approve"})
        check("B3 owner B decides a Quadd approval → 404", r.status_code == 404, r.status_code)
    # Superusers skip the ORM auto-filter, so the explicit ownership check is
    # the only thing standing between them and another tenant's rows.
    r = admin.put(f"/api/posts/{post_b}", json={"title": "edited from Quadd"})
    check("B4 superuser active on Quadd edits B's post → 404", r.status_code == 404, r.status_code)
    r = admin.post(f"/api/reviews/{ids['review_b']}/respond", json={"response": "x", "action": "send"})
    check("B5 superuser active on Quadd replies to B's review → 404", r.status_code == 404, r.status_code)
    with SessionLocal() as db:
        check("B6 Quadd post untouched", db.get(Post, quadd_post.id).title == quadd_post_title)
        check("B7 Quadd review untouched", db.get(Review, quadd_review.id).owner_response == quadd_review_response)
        check("B8 B's post untouched", db.get(Post, post_b).title == "B's first post")
    r = owner.post(f"/api/reviews/{ids['review_b']}/respond", json={"response": "Thanks, Pat!", "action": "send"})
    check("B9 owner B replies to B's own review → 200", r.status_code == 200, r.text)

    # ---- C. viewers are read-only --------------------------------------------
    print("\nC. viewer is read-only")
    boot = viewer.get("/api/bootstrap")
    check("C1 viewer can load the dashboard", boot.status_code == 200, boot.text)
    access = boot.json()["access"]
    check("C2 bootstrap says viewer + no publish", access["role"] == "viewer" and access["can"]["publish_post"] is False,
          access)
    writes = [
        ("POST", "/api/posts", {"platform": "fb", "status": "draft", "title": "t", "draft": "d"}),
        ("PUT", f"/api/posts/{post_b}", {"title": "viewer edit"}),
        ("POST", f"/api/reviews/{ids['review_b']}/respond", {"response": "viewer", "action": "send"}),
        ("PUT", "/api/marketing-plan", {"audience": "viewer edit"}),
        ("POST", f"/api/approvals/{approval_b}/decide", {"decision": "approve"}),
        ("PUT", "/api/ads/budgets/fb_ig", {"monthly_cap_cents": 100000}),
        ("POST", "/api/ads/tick", None),
        ("POST", "/api/ads/connections", {"platform": "fb_ig"}),
        ("PUT", "/api/settings/notifications", {"cadence": "each"}),
        ("PUT", "/api/settings/ad-autonomy", {"enabled": True}),
        ("POST", "/api/chatbot/keys", {"label": "viewer key"}),
        ("POST", "/api/inventory/import-fixture", None),
        ("POST", "/api/performance/regenerate-insights", None),
        ("POST", "/api/billing/change-tier-request", {"to_tier": 3}),
        ("POST", "/api/compose/redraft", {"platform": "fb", "draft": "x", "instruction": "shorter"}),
        ("POST", "/api/auth/invites", {"email": "x@example.com", "role": "owner"}),
    ]
    for method, path, body in writes:
        r = viewer.request(method, path, json=body)
        check(f"C3 viewer {method} {path} → 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
    r = viewer.post("/api/posts", json={"platform": "fb", "status": "draft", "title": "t", "draft": "d"})
    check("C4 403 reason is plain English", "view" in r.json()["detail"].lower() and "owner" in r.json()["detail"].lower(),
          r.json()["detail"])
    with SessionLocal() as db:
        check("C5 B's approval still undecided", db.get(Approval, approval_b).decision is None)
        check("C6 B's post title unchanged", db.get(Post, post_b).title == "B's first post")
    r = viewer.post("/api/escalations", json={"message": "Can someone call me?"})
    check("C7 viewer can still ask a human for help", r.status_code == 200, r.text)

    # ---- D. assistant tools follow the role ----------------------------------
    print("\nD. assistant tools follow the role")
    _RecordingMessages.calls.clear()
    with patch("app.routers.chat.Anthropic", _RecordingAnthropic):
        r = viewer.post("/api/chat/turn", json={"message": "Draft a Facebook post about spring hours"})
        check("D1 viewer chat turn → 200", r.status_code == 200, r.text[:200])
        viewer_call = _RecordingMessages.calls[-1]
        check("D2 viewer's model call has no tools", "tools" not in viewer_call and "tool_choice" not in viewer_call,
              sorted(viewer_call))
        check("D3 viewer's system prompt explains view-only",
              "view-only" in viewer_call["system"][0]["text"])
        r = owner.post("/api/chat/turn", json={"message": "What should I post this week?"})
        check("D4 owner chat turn → 200", r.status_code == 200, r.text[:200])
        check("D5 owner's model call has tools", bool(_RecordingMessages.calls[-1].get("tools")))

    # ---- E. chatbot relay ingest without a login -----------------------------
    print("\nE. chatbot relay ingest")
    r = owner.post("/api/chatbot/keys", json={"label": "B production"})
    check("E1 owner B mints an ingest key", r.status_code == 200, r.text)
    raw_key = r.json()["rawKey"]
    transcript = [{"who": "consumer", "text": "Are you open Sunday?"}, {"who": "bot", "text": "Yes, 10 to 4."}]
    r = relay.post("/api/chatbot/ingest", headers={"X-Amplafai-Key": raw_key},
                   json={"external_session_id": "relay-sess-1", "transcript": transcript})
    check("E2 cookie-less relay ingest → 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    with SessionLocal() as db:
        row = db.query(ChatbotConversation).filter(
            ChatbotConversation.external_id == "relay-sess-1").one_or_none()
    check("E3 conversation filed under business 2", row is not None and row.business_id == 2,
          row.business_id if row else None)
    r = relay.post("/api/chatbot/ingest", json={"external_session_id": "relay-sess-2", "transcript": transcript})
    check("E4 relay without a key → 401", r.status_code == 401, r.status_code)


if __name__ == "__main__":
    main()
