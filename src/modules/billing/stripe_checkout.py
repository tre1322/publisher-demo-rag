"""Stripe Checkout helpers for Amplora subscription signup + tier change.

Two layers:
  1. build_checkout_session_params() is a pure function that produces the
     dict you'd pass to stripe.checkout.Session.create. Testable without
     hitting Stripe.
  2. create_checkout_session() does the actual SDK call. Routes use this;
     tests use the pure builder.

Stripe Price IDs are env-driven so staging vs. prod can use different
prices without code changes:
    STRIPE_PRICE_STARTER     - $99 / month price_id (sk_test_* and sk_live_*)
    STRIPE_PRICE_GROWTH      - $299 / month
    STRIPE_PRICE_CONCIERGE   - $499 / month

Each Stripe Price object MUST also have metadata.tier set to the matching
string ('starter' | 'growth' | 'concierge'); the webhook reads it back.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Stripe Price IDs are read at call-time (not import-time) so tests and
# staging can override via environment without restarting.
def _price_id_for_tier(tier: str) -> str:
    env_var = f"STRIPE_PRICE_{tier.upper()}"
    price_id = os.getenv(env_var)
    if not price_id:
        raise ValueError(
            f"{env_var} is not set. Configure your Stripe Price IDs before "
            f"creating Checkout sessions."
        )
    return price_id


def build_checkout_session_params(
    organization_id: int,
    tier: str,
    customer_email: str,
    base_url: str,
    existing_customer_id: str | None = None,
) -> dict[str, Any]:
    """Produce the params dict for stripe.checkout.Session.create.

    Pure function — no Stripe SDK calls, no DB reads. Validates inputs and
    returns a dict ready to splat into the SDK.

    Args:
        organization_id: local org id; embedded in metadata so the webhook
            can resolve back when Stripe fires customer.subscription.created.
        tier: one of {'starter','growth','concierge'}.
        customer_email: email to seed Stripe Customer creation if the org
            doesn't already have a processor_customer_id.
        base_url: external URL where Stripe will redirect users
            (e.g., 'https://app.amplafai.com'). Trailing slash optional.
        existing_customer_id: Stripe cus_* if this org already has one.
            When provided, skips `customer_email` and reuses the customer.

    Returns:
        Dict ready for stripe.checkout.Session.create(**params).

    Raises:
        ValueError on bad tier or missing STRIPE_PRICE_* env var.
    """
    if tier not in ("starter", "growth", "concierge"):
        raise ValueError(f"unknown tier {tier!r}; expected starter|growth|concierge")
    if not customer_email and not existing_customer_id:
        raise ValueError("either customer_email or existing_customer_id is required")
    if not base_url:
        raise ValueError("base_url is required for Stripe redirect targets")

    base_url = base_url.rstrip("/")
    price_id = _price_id_for_tier(tier)
    metadata = {"organization_id": str(organization_id), "tier": tier}

    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        # Stripe puts metadata on BOTH the Checkout Session and the resulting
        # Subscription (via subscription_data). Webhook reads from the
        # Subscription, but the session metadata is useful for the success
        # page lookup.
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
        "success_url": (
            f"{base_url}/business/billing/success"
            "?session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{base_url}/business/billing/cancel",
        "allow_promotion_codes": True,
    }
    if existing_customer_id:
        params["customer"] = existing_customer_id
    else:
        params["customer_email"] = customer_email

    return params


def create_checkout_session(
    organization_id: int,
    tier: str,
    customer_email: str,
    base_url: str,
    existing_customer_id: str | None = None,
) -> dict[str, Any]:
    """Wrapper that builds params + actually calls Stripe.

    Returns the Stripe Session object as a dict (has .id and .url).
    Routes use this; tests use build_checkout_session_params() directly
    so they don't need Stripe network access.
    """
    import stripe

    api_key = os.getenv("STRIPE_API_KEY")
    if not api_key:
        raise ValueError("STRIPE_API_KEY not set")
    stripe.api_key = api_key

    params = build_checkout_session_params(
        organization_id=organization_id,
        tier=tier,
        customer_email=customer_email,
        base_url=base_url,
        existing_customer_id=existing_customer_id,
    )
    session = stripe.checkout.Session.create(**params)
    logger.info(
        "Stripe Checkout Session created: id=%s tier=%s org=%s",
        session.get("id"), tier, organization_id,
    )
    return dict(session)
