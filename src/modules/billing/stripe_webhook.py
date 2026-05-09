"""Stripe webhook handler for Amplora subscription lifecycle.

Two layers:
  1. The FastAPI route /webhooks/stripe verifies the Stripe-Signature header
     using the official SDK, then hands the parsed event to apply_event().
  2. apply_event() takes a plain dict (the Stripe event payload) and updates
     subscriptions + tier_history. Tests call it directly to bypass the
     SDK signature gate.

Stripe events handled (others are no-ops with a log line):
  - customer.subscription.created
  - customer.subscription.updated     ← tier upgrades/downgrades land here
  - customer.subscription.deleted     ← cancellation
  - invoice.payment_failed            ← drives status to past_due
  - invoice.payment_succeeded         ← used for confirmation logging only

Required Stripe Price metadata: every Price object configured in Stripe must
have metadata.tier set to one of {'starter','growth','concierge'}. The
webhook reads it from event.data.object.items.data[0].price.metadata.tier.

Required subscription metadata: every Stripe Subscription created from the
app must include metadata.organization_id so this handler can resolve the
local org. Without it, the webhook drops the event with a warning.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.modules.billing.database import (
    get_subscription_by_processor_id,
    log_tier_change,
    upsert_subscription,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── Stripe → local conversion ───────────────────────────────────────


def _ts_to_iso(ts: int | None) -> str | None:
    """Stripe gives unix seconds; we store ISO datetime."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _extract_tier(sub: dict[str, Any]) -> str | None:
    """Pull tier from the first item's price metadata."""
    try:
        items = sub.get("items", {}).get("data", [])
        if not items:
            return None
        return items[0].get("price", {}).get("metadata", {}).get("tier")
    except (AttributeError, KeyError, IndexError):
        return None


def _extract_org_id(sub: dict[str, Any]) -> int | None:
    """Pull organization_id from subscription.metadata."""
    md = sub.get("metadata") or {}
    raw = md.get("organization_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Stripe sub %s has non-int organization_id: %r",
                       sub.get("id"), raw)
        return None


# ── Event application (the testable core) ──────────────────────────


def apply_event(event: dict[str, Any]) -> dict[str, Any]:
    """Apply a parsed Stripe event to local DB. Returns a small summary dict.

    Pure function: takes a dict, writes to DB, returns what it did.
    Smoke tests call this directly with handcrafted event dicts.
    """
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {}) or {}
    summary: dict[str, Any] = {"event_type": event_type, "action": "ignored"}

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        org_id = _extract_org_id(obj)
        if org_id is None:
            logger.warning(
                "Stripe %s missing metadata.organization_id; sub_id=%s",
                event_type,
                obj.get("id"),
            )
            summary["action"] = "skipped_missing_org"
            return summary

        tier = _extract_tier(obj) or "starter"
        status = obj.get("status") or "incomplete"
        sub_id_stripe = obj.get("id")
        customer_id = obj.get("customer")
        period_start = _ts_to_iso(obj.get("current_period_start"))
        period_end = _ts_to_iso(obj.get("current_period_end"))
        canceled_at = _ts_to_iso(obj.get("canceled_at"))

        if event_type == "customer.subscription.deleted":
            status = "canceled"
            canceled_at = canceled_at or datetime.now(timezone.utc).isoformat()

        # Detect tier change by comparing to existing row before upserting.
        prior = (
            get_subscription_by_processor_id(sub_id_stripe)
            if sub_id_stripe
            else None
        )
        prior_tier = prior["tier"] if prior else None

        sub_row_id = upsert_subscription(
            organization_id=org_id,
            tier=tier,
            status=status,
            processor="stripe",
            processor_customer_id=customer_id,
            processor_subscription_id=sub_id_stripe,
            current_period_start=period_start,
            current_period_end=period_end,
            canceled_at=canceled_at,
        )
        summary["subscription_id"] = sub_row_id
        summary["organization_id"] = org_id
        summary["tier"] = tier
        summary["status"] = status

        if prior_tier != tier:
            log_tier_change(
                organization_id=org_id,
                subscription_id=sub_row_id,
                from_tier=prior_tier,
                to_tier=tier,
                changed_by=f"webhook:stripe:{event_type}",
                reason=event.get("id"),
            )
            summary["tier_changed"] = True

        summary["action"] = "applied"
        return summary

    if event_type == "invoice.payment_failed":
        # Stripe will follow up with a customer.subscription.updated
        # carrying status='past_due'; we just log here.
        logger.info("invoice.payment_failed: %s", obj.get("id"))
        summary["action"] = "logged"
        return summary

    if event_type == "invoice.payment_succeeded":
        logger.info("invoice.payment_succeeded: %s", obj.get("id"))
        summary["action"] = "logged"
        return summary

    logger.debug("Stripe event ignored: %s", event_type)
    return summary


# ── HTTP route (signature-verifying entrypoint) ─────────────────────


@router.post("/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Receive a Stripe webhook, verify signature, apply the event."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if not secret:
        logger.error("STRIPE_WEBHOOK_SECRET unset — rejecting webhook")
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    try:
        import stripe  # type: ignore[import-not-found]
    except ImportError as e:
        logger.error("stripe SDK not installed: %s", e)
        raise HTTPException(status_code=503, detail="stripe SDK missing") from e

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as e:
        logger.warning("Stripe webhook bad payload: %s", e)
        raise HTTPException(status_code=400, detail="bad payload") from e
    except stripe.error.SignatureVerificationError as e:  # type: ignore[attr-defined]
        logger.warning("Stripe webhook bad signature: %s", e)
        raise HTTPException(status_code=400, detail="bad signature") from e

    # stripe.Event behaves like a dict in modern SDKs; coerce to be safe.
    event_dict = dict(event) if not isinstance(event, dict) else event
    return apply_event(event_dict)
