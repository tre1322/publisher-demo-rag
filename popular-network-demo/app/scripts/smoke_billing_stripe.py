"""Phase 3a smoke — card billing: sign-up, upgrade, failed payment,
cancellation, and what each does to the dashboard.

Run with:  uv run python -m app.scripts.smoke_billing_stripe

Stripe is faked: the API client is replaced, and webhooks are signed with
Stripe's real scheme (HMAC-SHA256 over "<timestamp>.<body>") so the
production signature check runs unchanged.

  A. Before Stripe is configured nothing is gated
  B. A new client accepts the terms and must pick a plan
  C. Checkout sends the right plan, customer and return links
  D. The webhook rejects anything Stripe didn't sign
  E. Payment confirmed → full access; plan mirrored to the business
  F. Upgrade; a late, older event can't roll it back; duplicates count once
  G. Failed payment → 7 days' grace → read-only → paid again
  H. Cancel at period end → plan ends → read-only + 30-day deletion clock;
     paying again stops the clock
  I. Admin: billing on each card, plan edits refused for card payers,
     "billed outside the app", superusers never blocked
  J. The demo account is never gated; unpaid clients get no reminder emails
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_billing_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"
os.environ["ENVIRONMENT"] = "development"

SECRET = "whsec_smoke_secret"
OWNER = ("robin@prairie-flowers.example.com", "robin-correct-horse-battery")
CALLS: list[tuple[str, dict]] = []
EVENT_N = {"n": 0}


class _Obj(SimpleNamespace):
    pass


class FakeStripe:
    def __init__(self):
        self.v1 = SimpleNamespace(
            customers=SimpleNamespace(create=self._customer),
            checkout=SimpleNamespace(sessions=SimpleNamespace(create=self._session)),
            prices=SimpleNamespace(list=self._prices),
            billing_portal=SimpleNamespace(sessions=SimpleNamespace(create=self._portal)),
        )

    def _customer(self, params=None, options=None):
        CALLS.append(("customer", params))
        return _Obj(id=f"cus_{params['metadata']['business_id']}")

    def _session(self, params=None, options=None):
        CALLS.append(("checkout", params))
        return _Obj(id="cs_test_1", url="https://checkout.stripe.test/c/pay/cs_test_1")

    def _prices(self, params=None, options=None):
        CALLS.append(("prices", params))
        return _Obj(data=[_Obj(id=f"price_t{t}", lookup_key=f"amplafai_tier_{t}_monthly") for t in (1, 2, 3, 4)])

    def _portal(self, params=None, options=None):
        CALLS.append(("portal", params))
        return _Obj(url="https://billing.stripe.test/p/session_1")


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def signed(payload: str, *, secret: str = SECRET, ts: int | None = None) -> dict[str, str]:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def event(etype: str, obj: dict, *, created: int | None = None, eid: str | None = None) -> dict:
    EVENT_N["n"] += 1
    return {"id": eid or f"evt_{EVENT_N['n']}", "type": etype, "created": created or int(time.time()) + EVENT_N["n"],
            "data": {"object": obj}}


def sub_obj(biz_id: int, *, tier: int, status: str = "active", cancel_at_period_end: bool = False,
            ended_at: int | None = None) -> dict:
    period_end = int(time.time()) + 30 * 86400
    return {
        "id": f"sub_{biz_id}", "object": "subscription", "customer": f"cus_{biz_id}", "status": status,
        "metadata": {"business_id": str(biz_id), "tier": str(tier)},
        "cancel_at_period_end": cancel_at_period_end, "ended_at": ended_at, "canceled_at": ended_at,
        # API 2025-03+: the period lives on the item.
        "items": {"data": [{"current_period_end": period_end,
                            "price": {"id": f"price_t{tier}", "lookup_key": f"amplafai_tier_{tier}_monthly",
                                      "metadata": {"tier": str(tier)}}}]},
    }


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.email as mailer
    import app.subscriptions as subs
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)
    for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PORTAL_CONFIGURATION"):
        os.environ.pop(k, None)
    sent: list[dict] = []
    with patch.object(subs, "_client", FakeStripe), \
         patch.object(mailer, "_send", lambda to, subject, h, t, *, kind: sent.append({"to": to, "kind": kind}) or {"sent": True}), \
         TestClient(app, follow_redirects=False) as admin, \
         TestClient(app, follow_redirects=False) as owner, \
         TestClient(app, follow_redirects=False) as stripe_side:
        bootstrap_login(admin)
        _run(admin, owner, stripe_side, sent)
    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 3a billing smoke green ✓")


def _run(admin, owner, stripe_side, sent) -> None:  # noqa: C901 — linear smoke
    import app.subscriptions as subs
    from app.db import SessionLocal
    from app.models import Approval, Business, PlanChange, StripeEvent, Subscription, User

    def hook(ev: dict, **kw):
        body = json.dumps(ev)
        return stripe_side.post("/api/billing/webhook", content=body, headers=signed(body, **kw))

    def post_something():
        return owner.post("/api/posts", json={"platform": "fb", "status": "draft", "title": "Fall bouquets",
                                              "draft": "In stock now."})

    def boot():
        return owner.get("/api/bootstrap").json()

    # ---- A ---------------------------------------------------------------
    print("\nA. before Stripe is configured")
    r = admin.post("/api/admin/businesses", json={"name": "Prairie Flowers", "owner": "Robin Lee",
                                                  "owner_email": OWNER[0], "tier": 2})
    biz_id = r.json()["business"]["id"]
    check("A1 new businesses default to card billing", r.json()["business"]["billing"]["mode"] == "stripe",
          r.json()["business"]["billing"])
    invite = r.json()["invite"]
    token = invite["claimUrl"].split("token=", 1)[1]
    r = owner.post("/api/auth/invites/claim", json={"token": token, "password": OWNER[1]})
    check("A2 accepting the invite without agreeing to the terms → 422", r.status_code == 422
          and r.json()["detail"] == "terms_not_accepted", r.text)
    r = owner.post("/api/auth/invites/claim", json={"token": token, "password": OWNER[1], "accept_terms": True})
    check("A3 accepting with the box ticked → signed in", r.status_code == 200, r.text)
    with SessionLocal() as db:
        u = db.query(User).filter(User.email == OWNER[0]).one()
        check("A4 the terms version and time are recorded", u.terms_version == subs.TERMS_VERSION == "2026-06-01"
              and u.terms_accepted_at is not None, (u.terms_version, u.terms_accepted_at))
    b = boot()
    check("A5 unconfigured → not gated (state ok, not enforced)", b["billing"]["state"] == "ok"
          and b["billing"]["enforced"] is False and b["access"]["billingBlocked"] is None, b["billing"])
    check("A6 writes work", post_something().status_code == 200)
    r = owner.post("/api/billing/checkout", json={"tier": 2})
    check("A7 checkout says payments aren't on yet (503)", r.status_code == 503 and "switched on" in r.json()["detail"], r.text)

    os.environ["STRIPE_SECRET_KEY"] = "sk_test_smoke"
    os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
    os.environ["APP_BASE_URL"] = "https://dashboard.amplafai.com"

    # ---- B ---------------------------------------------------------------
    print("\nB. configured: a new client must pick a plan")
    b = boot()
    check("B1 needs payment", b["billing"]["state"] == "needs_payment" and b["billing"]["enforced"] is True, b["billing"])
    check("B2 the UI can tell why: role allows, payment doesn't",
          b["access"]["can"]["publish_post"] is False and b["access"]["canByRole"]["publish_post"] is True
          and "Choose a plan" in (b["access"]["billingBlocked"] or ""), b["access"])
    check("B3 paying is still allowed", b["access"]["can"]["manage_billing"] is True)
    r = post_something()
    check("B4 creating a post → 402 with the reason", r.status_code == 402 and "Choose a plan" in r.json()["detail"], r.text)
    r = owner.put("/api/onboarding/answers", json={"answers": {"what_you_do": "Flowers."}})
    check("B5 setup waits for payment too (402)", r.status_code == 402, r.status_code)
    r = owner.post("/api/chat/turn", json={"message": "hi"})
    check("B6 the AI agent waits for payment (402, no API call)", r.status_code == 402, r.status_code)
    r = owner.get("/api/account/export")
    check("B7 exporting data always works", r.status_code == 200, r.status_code)
    r = owner.post("/api/escalations", json={"message": "Question about plans"})
    check("B8 'talk to a human' always works", r.status_code in (200, 201), r.text[:200])

    # ---- C ---------------------------------------------------------------
    print("\nC. checkout")
    CALLS.clear()
    r = owner.post("/api/billing/checkout", json={"tier": 9})
    check("C1 a made-up plan → 422", r.status_code == 422)
    r = owner.post("/api/billing/checkout", json={"tier": 3})
    check("C2 checkout → Stripe's payment page", r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test/"), r.text)
    cust = next(p for k, p in CALLS if k == "customer")
    sess = next(p for k, p in CALLS if k == "checkout")
    check("C3 Stripe customer carries the owner's email and business id",
          cust["email"] == OWNER[0] and cust["metadata"]["business_id"] == str(biz_id) and cust["name"] == "Prairie Flowers", cust)
    check("C4 a monthly subscription for the chosen plan's price", sess["mode"] == "subscription"
          and sess["line_items"] == [{"price": "price_t3", "quantity": 1}], sess)
    check("C5 the subscription is tagged with the business and plan",
          sess["subscription_data"]["metadata"] == {"business_id": str(biz_id), "tier": "3"}
          and sess["client_reference_id"] == str(biz_id), sess["subscription_data"])
    check("C6 return links land on this business's billing screen",
          sess["success_url"] == f"https://dashboard.amplafai.com/?tab=billing&b={biz_id}&checkout=success"
          and sess["cancel_url"].endswith("checkout=canceled"), sess["success_url"])
    check("C7 no trial, no setup fee", "trial_period_days" not in json.dumps(sess) and len(sess["line_items"]) == 1)
    CALLS.clear()
    owner.post("/api/billing/checkout", json={"tier": 3})
    check("C8 a second try reuses the Stripe customer", not any(k == "customer" for k, _ in CALLS), CALLS)
    check("C9 coming back from the payment page changes nothing by itself",
          boot()["billing"]["state"] == "needs_payment")
    r = owner.post("/api/billing/portal")
    check("C10 billing portal opens once a customer exists", r.status_code == 200 and "billing.stripe.test" in r.json()["url"], r.text)

    # ---- D ---------------------------------------------------------------
    print("\nD. webhook security")
    ev = event("customer.subscription.created", sub_obj(biz_id, tier=3))
    body = json.dumps(ev)
    r = stripe_side.post("/api/billing/webhook", content=body, headers={"Content-Type": "application/json"})
    check("D1 no signature → 400", r.status_code == 400, r.status_code)
    r = hook(ev, secret="whsec_wrong")
    check("D2 signed with the wrong secret → 400", r.status_code == 400, r.status_code)
    r = hook(ev, ts=int(time.time()) - 3600)
    check("D3 an hour-old signature (replay) → 400", r.status_code == 400, r.status_code)
    r = stripe_side.post("/api/billing/webhook", content=body.replace('"active"', '"canceled"'), headers=signed(body))
    check("D4 a body changed after signing → 400", r.status_code == 400, r.status_code)
    check("D5 none of those changed anything", boot()["billing"]["state"] == "needs_payment")

    # ---- E ---------------------------------------------------------------
    print("\nE. payment confirmed")
    r = hook(event("checkout.session.completed", {"object": "checkout.session", "client_reference_id": str(biz_id),
                                                  "customer": f"cus_{biz_id}", "subscription": f"sub_{biz_id}",
                                                  "metadata": {"business_id": str(biz_id)}}))
    check("E1 checkout completed links the subscription", r.status_code == 200 and r.json()["action"] == "checkout_linked", r.text)
    created_ev = event("customer.subscription.created", sub_obj(biz_id, tier=3))
    r = hook(created_ev)
    check("E2 subscription active → applied", r.json()["action"] == "subscription", r.text)
    b = boot()
    check("E3 full access", b["billing"]["state"] == "ok" and b["access"]["can"]["publish_post"] is True
          and b["access"]["billingBlocked"] is None, b["billing"])
    check("E4 the next charge date is known", b["billing"]["currentPeriodEnd"] is not None)
    check("E5 the business is on the plan they paid for", b["business"]["tier"] == 3 and b["business"]["monthlyPrice"] == 150,
          (b["business"]["tier"], b["business"]["monthlyPrice"]))
    check("E6 posts work now", post_something().status_code == 200)
    r = hook(created_ev)
    check("E7 the same event again counts once", r.json()["action"] == "duplicate", r.text)
    with SessionLocal() as db:
        n_changes = db.query(PlanChange).filter(PlanChange.business_id == biz_id).count()
        check("E8 one plan-change record", n_changes == 1, n_changes)
    r = owner.post("/api/billing/checkout", json={"tier": 4})
    check("E9 can't start a second subscription; use Manage billing", r.status_code == 409 and "Manage billing" in r.json()["detail"], r.text)

    # ---- F ---------------------------------------------------------------
    print("\nF. upgrade, late events")
    newer = int(time.time()) + 1000
    r = hook(event("customer.subscription.updated", sub_obj(biz_id, tier=4), created=newer))
    b = boot()
    check("F1 upgrade in the portal → Tier 4 at $799", b["business"]["tier"] == 4 and b["business"]["monthlyPrice"] == 799,
          b["business"]["tier"])
    r = hook(event("customer.subscription.updated", sub_obj(biz_id, tier=2), created=newer - 500))
    check("F2 an older event arriving late is ignored", r.json()["action"] == "skipped_stale"
          and boot()["business"]["tier"] == 4, r.text)

    # ---- G ---------------------------------------------------------------
    print("\nG. failed payment")
    inv = {"object": "invoice", "customer": f"cus_{biz_id}",
           "parent": {"subscription_details": {"subscription": f"sub_{biz_id}"}}}
    r = hook(event("invoice.payment_failed", inv, created=newer + 10))
    check("G1 payment failed recorded (new invoice shape)", r.json()["action"] == "payment_failed", r.text)
    hook(event("customer.subscription.updated", sub_obj(biz_id, tier=4, status="past_due"), created=newer + 20))
    b = boot()
    check("G2 grace period: still full access, with an end date", b["billing"]["state"] == "grace"
          and b["billing"]["graceEndsAt"] and b["access"]["can"]["publish_post"] is True, b["billing"])
    with SessionLocal() as db:
        row = db.query(Subscription).filter(Subscription.business_id == biz_id).one()
        since = row.past_due_since
        ends = datetime.fromisoformat(b["billing"]["graceEndsAt"])
        check("G3 grace is exactly 7 days", ends - since == timedelta(days=7), (since, ends))
        biz = db.get(Business, biz_id)
        check("G4 day 6 → grace; day 8 → read-only",
              subs.billing_state(db, biz, now=since + timedelta(days=6))["state"] == "grace"
              and subs.billing_state(db, biz, now=since + timedelta(days=8))["state"] == "read_only")
        row.past_due_since = datetime.utcnow() - timedelta(days=8)
        db.commit()
    b = boot()
    check("G5 after 7 days: read-only, with the reason", b["billing"]["state"] == "read_only"
          and "overdue" in (b["access"]["billingBlocked"] or ""), b["access"]["billingBlocked"])
    r = post_something()
    check("G6 writes refused (402)", r.status_code == 402 and "overdue" in r.json()["detail"], r.text)
    check("G7 the owner can still open billing to fix the card", owner.post("/api/billing/portal").status_code == 200)
    check("G8 and still export their data", owner.get("/api/account/export").status_code == 200)
    hook(event("invoice.paid", inv, created=newer + 30))
    hook(event("customer.subscription.updated", sub_obj(biz_id, tier=4, status="active"), created=newer + 40))
    b = boot()
    check("G9 paid → full access again", b["billing"]["state"] == "ok" and post_something().status_code == 200, b["billing"])
    with SessionLocal() as db:
        check("G10 the overdue clock is cleared",
              db.query(Subscription).filter(Subscription.business_id == biz_id).one().past_due_since is None)

    # ---- H ---------------------------------------------------------------
    print("\nH. cancellation")
    hook(event("customer.subscription.updated", sub_obj(biz_id, tier=4, cancel_at_period_end=True), created=newer + 50))
    b = boot()
    check("H1 cancel in the portal → keeps working until the paid month ends", b["billing"]["state"] == "ok"
          and b["billing"]["cancelAtPeriodEnd"] is True, b["billing"])
    hook(event("customer.subscription.deleted", sub_obj(biz_id, tier=4, status="canceled", ended_at=int(time.time())),
               created=newer + 60))
    b = boot()
    check("H2 plan ended → read-only", b["billing"]["state"] == "canceled" and post_something().status_code == 402, b["billing"])
    due = b["business"]["deletionDueAt"]
    check("H3 the 30-day deletion clock started", due and 29 <= (datetime.fromisoformat(due) - datetime.utcnow()).days <= 30, due)
    check("H4 export still works", owner.get("/api/account/export").status_code == 200)
    r = owner.post("/api/billing/checkout", json={"tier": 2})
    check("H5 they can pay again", r.status_code == 200, r.text)
    hook(event("customer.subscription.created", {**sub_obj(biz_id, tier=2), "id": f"sub_{biz_id}_b"}, created=newer + 70))
    b = boot()
    check("H6 paying again → full access and the deletion clock stops", b["billing"]["state"] == "ok"
          and b["business"]["deletionDueAt"] is None and b["business"]["tier"] == 2, (b["billing"]["state"], b["business"]["deletionDueAt"]))
    with SessionLocal() as db:
        statuses = [(p.from_status, p.to_status) for p in db.query(PlanChange).filter(PlanChange.business_id == biz_id).order_by(PlanChange.id)]
        check("H7 the history tells the whole story", statuses[0] == (None, "active") and ("active", "canceled") in statuses
              and statuses[-1] == ("canceled", "active"), statuses)
        n_events = db.query(StripeEvent).count()
        check("H8 every event recorded once", n_events >= 10, n_events)

    # ---- I ---------------------------------------------------------------
    print("\nI. admin")
    row = next(b for b in admin.get("/api/admin/businesses").json()["businesses"] if b["id"] == biz_id)
    check("I1 each card shows billing", row["billing"]["state"] == "ok" and row["billing"]["status"] == "active"
          and row["billing"]["stripeCustomerUrl"] == f"https://dashboard.stripe.com/test/customers/cus_{biz_id}", row["billing"])
    r = admin.put(f"/api/admin/businesses/{biz_id}", json={"tier": 1})
    check("I2 changing a card payer's plan here is refused (Stripe decides)", r.status_code == 409, r.text)
    r = admin.post("/api/admin/businesses", json={"name": "Unpaid Co", "owner": "U"})
    unpaid_id = r.json()["business"]["id"]
    admin.post(f"/api/admin/businesses/{unpaid_id}/open")
    r = admin.post("/api/posts", json={"platform": "fb", "status": "draft", "title": "x", "draft": "x"})
    check("I3 Amplafai staff are never blocked (to set things up)", r.status_code == 200, r.text)
    r = admin.post("/api/admin/businesses", json={"name": "Invoice Co", "owner": "I", "billing_mode": "outside"})
    check("I4 'billed outside the app' → never gated", r.json()["business"]["billing"]["mode"] == "outside"
          and r.json()["business"]["billing"]["state"] == "ok", r.json()["business"]["billing"])
    r = admin.put(f"/api/admin/businesses/{unpaid_id}", json={"billing_mode": "outside"})
    check("I5 switching a business to outside billing lifts the gate", r.json()["business"]["billing"]["state"] == "ok")
    admin.put(f"/api/admin/businesses/{unpaid_id}", json={"billing_mode": "stripe"})

    # ---- J ---------------------------------------------------------------
    print("\nJ. demo account and emails")
    admin.post("/api/admin/businesses/1/open")
    b = admin.get("/api/bootstrap").json()
    check("J1 the demo account is never gated", b["billing"]["state"] == "ok" and b["billing"]["enforced"] is False, b["billing"])
    from app import notifications as nt
    with SessionLocal() as db:
        db.add(Approval(business_id=unpaid_id, kind="post", platform="fb", title="t", draft="d",
                        created_at=datetime.utcnow() - timedelta(hours=5)))
        u = User(email="u@unpaid.example.com", password_hash="x", is_active=True)
        db.add(u)
        db.flush()
        from app.models import BusinessUser
        db.add(BusinessUser(user_id=u.id, business_id=unpaid_id, role="owner"))
        db.commit()
        os.environ["POSTMARK_API_KEY"] = "smoke"
        sent.clear()
        done = nt.run_due(db, datetime.utcnow())
        os.environ.pop("POSTMARK_API_KEY")
    check("J2 an unpaid client gets no reminder (and no AI suggestion)", unpaid_id not in done["approvals"]
          and not any(m["to"] == "u@unpaid.example.com" for m in sent), (done, sent))
    r = stripe_side.post("/api/billing/webhook", content="{}", headers=signed("{}"))
    check("J3 an event with no id is accepted and ignored", r.status_code == 200 and r.json()["action"] == "ignored_no_id", r.text)
    r = hook(event("customer.subscription.updated", {**sub_obj(999, tier=2), "metadata": {}, "customer": "cus_unknown",
                                                     "id": "sub_unknown"}))
    check("J4 an event for an unknown business is skipped, not crashed", r.json()["action"] == "skipped_unknown_business", r.text)


if __name__ == "__main__":
    main()
