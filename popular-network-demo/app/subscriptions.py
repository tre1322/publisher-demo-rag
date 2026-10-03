"""Billing with Stripe (Phase 3).

Decisions (Trevor, 2026-10-03): four tiers at $30 / $75 / $150 / $799 a
month, no setup fee, no trial, cancel any time (takes effect at the end of
the paid month). A failed payment gets GRACE (7 days) of full access; after
that the business is read-only until it's paid. A subscription that ends
starts the 30-day deletion clock from Phase 1; paying again stops it.

Stripe is the source of truth. The signed webhook (apply_event) is the only
thing that marks a business paid, past due, or canceled; the checkout
success page never does. Plan changes, card updates, invoices and
cancelling all happen in Stripe's customer portal.

Who it applies to: businesses with billing_mode "stripe" (every business
the admin console creates, unless Amplafai bills it some other way). The
demo account and "outside" businesses are never gated. Nothing is gated
until Stripe is configured (STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET), so
the code can ship before the keys exist.

Prices are found by lookup key (amplafai_tier_<n>_monthly); each Stripe
price also carries metadata.tier. app/scripts/stripe_setup.py creates them.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import Business, PlanChange, StripeEvent, Subscription
from .routers.billing import TIER_LABELS, TIER_PRICES

log = logging.getLogger("popular_network.billing")

TERMS_VERSION = "2026-06-01"  # effective date of https://amplafai.com/terms
TERMS_URL = "https://amplafai.com/terms"
PRIVACY_URL = "https://amplafai.com/privacy"
GRACE = timedelta(days=7)
TIERS = tuple(sorted(TIER_PRICES))

ACTIVE = ("active", "trialing")
UNPAID_NOW = ("past_due",)
LOCKED = ("unpaid", "paused")
NOT_STARTED = (None, "incomplete", "incomplete_expired")

# Billing states that stop changes (everything except looking and paying).
WRITE_BLOCKING = ("needs_payment", "read_only", "canceled")


class BillingError(Exception):
    """A billing failure with a message fit to show the owner."""


def lookup_key(tier: int) -> str:
    return f"amplafai_tier_{tier}_monthly"


def is_configured() -> bool:
    return bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_WEBHOOK_SECRET"))


def _client():
    """The Stripe client. The smokes replace this with a fake."""
    import stripe

    return stripe.StripeClient(os.environ["STRIPE_SECRET_KEY"])


def base_url(fallback: Optional[str] = None) -> str:
    return (os.getenv("APP_BASE_URL") or fallback or "https://dashboard.amplafai.com").rstrip("/")


def enforced(biz: Optional[Business]) -> bool:
    """Does payment status gate this business?"""
    return bool(biz is not None and not biz.is_demo and biz.billing_mode == "stripe" and is_configured())


def get_subscription(db: Session, business_id: int) -> Optional[Subscription]:
    return (
        db.query(Subscription).filter(Subscription.business_id == business_id)
        .execution_options(include_all_tenants=True).first()
    )


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def billing_state(db: Session, biz: Business, now: Optional[datetime] = None) -> dict[str, Any]:
    """Where this business stands, for the server gate and the dashboard.

    state: ok | grace | read_only | needs_payment | canceled
    """
    now = now or datetime.utcnow()
    sub = get_subscription(db, biz.id)
    out: dict[str, Any] = {
        "mode": "stripe" if biz.billing_mode == "stripe" else "outside",
        "enforced": enforced(biz),
        "configured": is_configured(),
        "status": sub.status if sub else None,
        "tier": (sub.tier if sub and sub.tier else biz.tier),
        "currentPeriodEnd": _iso(sub.current_period_end) if sub else None,
        "cancelAtPeriodEnd": bool(sub.cancel_at_period_end) if sub else False,
        "hasCustomer": bool(sub and sub.stripe_customer_id),
        "graceEndsAt": None,
        "termsUrl": TERMS_URL,
        "prices": {str(t): TIER_PRICES[t] for t in TIERS},
        "labels": {str(t): TIER_LABELS[t] for t in TIERS},
    }
    if not out["enforced"]:
        out["state"] = "ok"
        return out
    status = sub.status if sub else None
    if status in NOT_STARTED:
        out["state"] = "needs_payment"
    elif status in ACTIVE:
        out["state"] = "ok"
    elif status in UNPAID_NOW:
        since = sub.past_due_since or sub.updated_at or now
        ends = since + GRACE
        out["graceEndsAt"] = _iso(ends)
        out["state"] = "grace" if now < ends else "read_only"
    elif status in LOCKED:
        out["state"] = "read_only"
    elif status == "canceled":
        out["state"] = "canceled"
    else:  # a status Stripe adds later: don't lock anyone out over it
        log.warning("unknown Stripe status %r for business %s", status, biz.id)
        out["state"] = "ok"
    return out


def blocks_writes(state: dict[str, Any]) -> bool:
    return state.get("state") in WRITE_BLOCKING


def blocked_message(state: dict[str, Any]) -> str:
    s = state.get("state")
    if s == "needs_payment":
        return "Choose a plan to start using the dashboard. Only the business owner can do this."
    if s == "read_only":
        return ("A payment is overdue, so the dashboard is read-only. "
                "The business owner can update the card under Settings → Plan & billing.")
    if s == "canceled":
        return "This plan has ended, so the dashboard is read-only. The business owner can restart it under Settings → Plan & billing."
    return "Billing needs attention."


# --------------------------------------------------------------------------- #
# Checkout and the customer portal
# --------------------------------------------------------------------------- #

_PRICE_CACHE: dict[str, Any] = {"at": 0.0, "ids": {}}


def price_ids() -> dict[int, str]:
    """{tier: Stripe price id}, found by lookup key. Cached for 10 minutes."""
    if _PRICE_CACHE["ids"] and time.time() - _PRICE_CACHE["at"] < 600:
        return _PRICE_CACHE["ids"]
    res = _client().v1.prices.list(params={"lookup_keys": [lookup_key(t) for t in TIERS], "active": True})
    ids: dict[int, str] = {}
    for price in res.data:
        for t in TIERS:
            if price.lookup_key == lookup_key(t):
                ids[t] = price.id
    missing = [t for t in TIERS if t not in ids]
    if missing:
        log.error("Stripe prices missing for tiers %s (run app.scripts.stripe_setup)", missing)
        raise BillingError("Billing isn't fully set up yet. Tell Amplafai.")
    _PRICE_CACHE.update(at=time.time(), ids=ids)
    return ids


def _subscription_row(db: Session, biz: Business) -> Subscription:
    sub = get_subscription(db, biz.id)
    if sub is None:
        sub = Subscription(business_id=biz.id)
        db.add(sub)
        db.flush()
    return sub


def start_checkout(db: Session, biz: Business, *, tier: int, email: str, base: str) -> str:
    """Create a Stripe Checkout session for `tier`; returns its URL."""
    if tier not in TIER_PRICES:
        raise BillingError("Pick one of the four plans.")
    if biz.billing_mode != "stripe":
        raise BillingError("This business is billed outside the app, so there's nothing to pay here.")
    sub = _subscription_row(db, biz)
    if sub.status in ACTIVE + UNPAID_NOW + LOCKED:
        raise BillingError("You already have a plan. Use Manage billing to change it or update your card.")
    client = _client()
    try:
        if not sub.stripe_customer_id:
            customer = client.v1.customers.create(params={
                "email": email,
                "name": biz.name,
                "metadata": {"business_id": str(biz.id)},
            })
            sub.stripe_customer_id = customer.id
            db.commit()
        session = client.v1.checkout.sessions.create(params={
            "mode": "subscription",
            "customer": sub.stripe_customer_id,
            "client_reference_id": str(biz.id),
            "line_items": [{"price": price_ids()[tier], "quantity": 1}],
            "subscription_data": {"metadata": {"business_id": str(biz.id), "tier": str(tier)}},
            "metadata": {"business_id": str(biz.id)},
            "allow_promotion_codes": True,
            "success_url": f"{base}/?tab=billing&b={biz.id}&checkout=success",
            "cancel_url": f"{base}/?tab=billing&b={biz.id}&checkout=canceled",
        })
    except BillingError:
        raise
    except Exception as e:  # Stripe errors, network
        log.exception("checkout failed for business %s", biz.id)
        raise BillingError("Couldn't open the payment page. Try again in a minute.") from e
    return session.url


def portal_url(db: Session, biz: Business, *, base: str) -> str:
    """Stripe's customer portal: card, invoices, plan changes, cancelling."""
    sub = get_subscription(db, biz.id)
    if sub is None or not sub.stripe_customer_id:
        raise BillingError("There's no billing account yet. Choose a plan first.")
    params: dict[str, Any] = {"customer": sub.stripe_customer_id, "return_url": f"{base}/?tab=billing&b={biz.id}"}
    if os.getenv("STRIPE_PORTAL_CONFIGURATION"):
        params["configuration"] = os.environ["STRIPE_PORTAL_CONFIGURATION"]
    try:
        return _client().v1.billing_portal.sessions.create(params=params).url
    except Exception as e:
        log.exception("portal session failed for business %s", biz.id)
        raise BillingError("Couldn't open billing. Try again in a minute.") from e


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #

def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if obj is None:
            return None
        if isinstance(obj, list):
            obj = obj[0] if obj else None
            if obj is None:
                return None
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def _ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.utcfromtimestamp(int(value)) if value else None
    except (TypeError, ValueError):
        return None


def _tier_of(sub_obj: dict) -> Optional[int]:
    price = _get(sub_obj, "items", "data", "price") or {}
    for raw in ((price.get("metadata") or {}).get("tier"), (sub_obj.get("metadata") or {}).get("tier")):
        try:
            if raw is not None and int(raw) in TIER_PRICES:
                return int(raw)
        except (TypeError, ValueError):
            pass
    for t in TIERS:
        if price.get("lookup_key") == lookup_key(t):
            return t
    return None


def _business_for(db: Session, obj: dict, *, sub_id: Optional[str], customer: Optional[str]) -> Optional[Business]:
    raw = (obj.get("metadata") or {}).get("business_id") or obj.get("client_reference_id")
    if raw:
        try:
            return db.get(Business, int(raw))
        except (TypeError, ValueError):
            pass
    q = db.query(Subscription).execution_options(include_all_tenants=True)
    row = None
    if sub_id:
        row = q.filter(Subscription.stripe_subscription_id == sub_id).first()
    if row is None and customer:
        row = q.filter(Subscription.stripe_customer_id == customer).first()
    return db.get(Business, row.business_id) if row else None


def _set_tier(biz: Business, tier: Optional[int]) -> None:
    if tier and tier in TIER_PRICES and biz.tier != tier:
        biz.tier, biz.tier_label, biz.monthly_price = tier, TIER_LABELS[tier], TIER_PRICES[tier]


def _apply_subscription(db: Session, event: dict, obj: dict) -> dict[str, Any]:
    from .data_lifecycle import cancel_deletion, schedule_deletion

    sub_id, customer = obj.get("id"), obj.get("customer")
    biz = _business_for(db, obj, sub_id=sub_id, customer=customer)
    if biz is None:
        log.warning("Stripe %s for unknown business (sub=%s customer=%s)", event.get("type"), sub_id, customer)
        return {"action": "skipped_unknown_business"}
    row = _subscription_row(db, biz)
    created = int(event.get("created") or 0)
    if row.last_event_created and created and created < row.last_event_created:
        return {"action": "skipped_stale", "business_id": biz.id}

    status = "canceled" if event.get("type") == "customer.subscription.deleted" else obj.get("status")
    tier = _tier_of(obj) or row.tier
    before = (row.tier, row.status)
    row.stripe_subscription_id = sub_id or row.stripe_subscription_id
    row.stripe_customer_id = customer or row.stripe_customer_id
    row.status = status
    row.tier = tier
    row.current_period_end = _ts(_get(obj, "items", "data", "current_period_end") or obj.get("current_period_end"))
    row.cancel_at_period_end = bool(obj.get("cancel_at_period_end") or obj.get("cancel_at"))
    row.canceled_at = _ts(obj.get("ended_at") or obj.get("canceled_at")) if status == "canceled" else None
    row.last_event_created = created or row.last_event_created
    row.updated_at = datetime.utcnow()
    if status in ACTIVE:
        row.past_due_since = None
    elif status in UNPAID_NOW and row.past_due_since is None:
        row.past_due_since = _ts(created) or datetime.utcnow()
    _set_tier(biz, tier)

    # The plan ended: the 30-day deletion clock the terms promise. Paying
    # again before it runs out stops the clock.
    if status == "canceled" and biz.deletion_due_at is None:
        schedule_deletion(db, biz)
    elif status in ACTIVE and biz.deletion_due_at is not None and before[1] == "canceled":
        cancel_deletion(biz)

    if before != (tier, status):
        db.add(PlanChange(business_id=biz.id, from_tier=before[0], to_tier=tier, from_status=before[1],
                          to_status=status, source=f"stripe:{event.get('id')} {event.get('type')}"[:120]))
    return {"action": "subscription", "business_id": biz.id, "status": status, "tier": tier}


def _invoice_subscription(inv: dict) -> Optional[str]:
    return inv.get("subscription") or _get(inv, "parent", "subscription_details", "subscription")


def apply_event(db: Session, event: dict) -> dict[str, Any]:
    """Apply one verified Stripe event. Idempotent per event id. Commits."""
    eid, etype = event.get("id"), event.get("type", "")
    if not eid:
        return {"action": "ignored_no_id"}
    if db.get(StripeEvent, eid) is not None:
        return {"action": "duplicate"}
    obj = _get(event, "data", "object") or {}
    summary: dict[str, Any] = {"action": "ignored"}

    if etype == "checkout.session.completed":
        biz = _business_for(db, obj, sub_id=obj.get("subscription"), customer=obj.get("customer"))
        if biz is not None:
            row = _subscription_row(db, biz)
            row.stripe_customer_id = obj.get("customer") or row.stripe_customer_id
            row.stripe_subscription_id = obj.get("subscription") or row.stripe_subscription_id
            summary = {"action": "checkout_linked", "business_id": biz.id}
    elif etype.startswith("customer.subscription."):
        summary = _apply_subscription(db, event, obj)
    elif etype in ("invoice.payment_failed", "invoice.paid", "invoice.payment_succeeded"):
        biz = _business_for(db, {}, sub_id=_invoice_subscription(obj), customer=obj.get("customer"))
        row = get_subscription(db, biz.id) if biz else None
        if row is not None:
            if etype == "invoice.payment_failed":
                if row.past_due_since is None:
                    row.past_due_since = _ts(event.get("created")) or datetime.utcnow()
                summary = {"action": "payment_failed", "business_id": biz.id}
            else:
                row.past_due_since = None
                summary = {"action": "payment_ok", "business_id": biz.id}

    db.add(StripeEvent(id=eid, type=etype[:80]))
    db.commit()
    log.info("stripe event %s %s → %s", eid, etype, summary)
    return summary
