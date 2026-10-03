"""Set up Stripe for the dashboard (Phase 3). Safe to run again.

    uv run python -m app.scripts.stripe_setup --base https://dashboard.amplafai.com [--emit-env] [--live]

With STRIPE_SECRET_KEY set, this:
  1. creates a product + monthly price per tier, found by lookup key
     amplafai_tier_<n>_monthly (prices carry metadata.tier); existing ones
     are kept, and a price whose amount is wrong is reported, not changed
  2. creates the customer-portal configuration (card, invoices, switch
     between the four plans, cancel at the end of the paid month)
  3. registers the webhook endpoint <base>/api/billing/webhook

--emit-env prints only KEY=VALUE lines for new settings (the webhook secret
exists only at creation), so a deploy can append them straight to the
server's .env without the secret appearing anywhere else. Progress goes to
stderr. Refuses a live key (sk_live_) unless --live is given.
"""
from __future__ import annotations

import argparse
import os
import sys

from app.routers.billing import TIER_LABELS, TIER_PRICES
from app.subscriptions import PRIVACY_URL, TERMS_URL, TIERS, lookup_key

WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.paid",
]
PORTAL_MARKER = "amplafai_dashboard"


def say(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("APP_BASE_URL", "https://dashboard.amplafai.com"))
    ap.add_argument("--emit-env", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        sys.exit("STRIPE_SECRET_KEY is not set")
    if key.startswith("sk_live") and not args.live:
        sys.exit("That's a live key. Re-run with --live once test mode has been checked end to end.")
    import stripe

    client = stripe.StripeClient(key)
    base = args.base.rstrip("/")
    mode = "LIVE" if key.startswith("sk_live") else "test"
    say(f"Stripe setup ({mode} mode) for {base}")
    emit: dict[str, str] = {}

    # 1. prices ---------------------------------------------------------------
    existing = {p.lookup_key: p for p in client.v1.prices.list(
        params={"lookup_keys": [lookup_key(t) for t in TIERS], "expand": ["data.product"]}).data}
    products: dict[int, tuple[str, str]] = {}
    for tier in TIERS:
        cents = TIER_PRICES[tier] * 100
        price = existing.get(lookup_key(tier))
        if price is None:
            product = client.v1.products.create(params={
                "name": f"Amplafai {TIER_LABELS[tier]}",
                "metadata": {"tier": str(tier), "app": PORTAL_MARKER},
            })
            price = client.v1.prices.create(params={
                "product": product.id,
                "currency": "usd",
                "unit_amount": cents,
                "recurring": {"interval": "month"},
                "lookup_key": lookup_key(tier),
                "metadata": {"tier": str(tier)},
            })
            say(f"  created {TIER_LABELS[tier]}: ${TIER_PRICES[tier]}/month ({price.id})")
            product_id = product.id
        else:
            product_id = price.product.id if hasattr(price.product, "id") else price.product
            note = "" if price.unit_amount == cents else f"  <-- WARNING: Stripe has {price.unit_amount} cents, app expects {cents}"
            say(f"  kept {TIER_LABELS[tier]} ({price.id}){note}")
        products[tier] = (product_id, price.id)

    # 2. customer portal --------------------------------------------------------
    portal = next((c for c in client.v1.billing_portal.configurations.list(params={"active": True, "limit": 100}).data
                   if (c.metadata or {}).get("app") == PORTAL_MARKER), None)
    if portal is None:
        portal = client.v1.billing_portal.configurations.create(params={
            "business_profile": {"headline": "Amplafai: manage your plan",
                                 "privacy_policy_url": PRIVACY_URL, "terms_of_service_url": TERMS_URL},
            "default_return_url": f"{base}/?tab=billing",
            "features": {
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "customer_update": {"enabled": True, "allowed_updates": ["email", "address"]},
                "subscription_cancel": {"enabled": True, "mode": "at_period_end",
                                        "cancellation_reason": {"enabled": True, "options": [
                                            "too_expensive", "missing_features", "switched_service", "unused", "other"]}},
                "subscription_update": {"enabled": True, "default_allowed_updates": ["price"],
                                        "proration_behavior": "create_prorations",
                                        "products": [{"product": prod, "prices": [price]}
                                                     for prod, price in products.values()]},
            },
            "metadata": {"app": PORTAL_MARKER},
        })
        say(f"  created customer portal configuration ({portal.id})")
    else:
        say(f"  kept customer portal configuration ({portal.id})")
    if os.getenv("STRIPE_PORTAL_CONFIGURATION") != portal.id:
        emit["STRIPE_PORTAL_CONFIGURATION"] = portal.id

    # 3. webhook ----------------------------------------------------------------
    url = f"{base}/api/billing/webhook"
    hook = next((w for w in client.v1.webhook_endpoints.list(params={"limit": 100}).data if w.url == url), None)
    if hook is None:
        hook = client.v1.webhook_endpoints.create(params={
            "url": url, "enabled_events": WEBHOOK_EVENTS, "description": "Amplafai dashboard billing",
        })
        emit["STRIPE_WEBHOOK_SECRET"] = hook.secret
        say(f"  created webhook endpoint {url} ({hook.id}); its signing secret is shown only now")
    else:
        missing = sorted(set(WEBHOOK_EVENTS) - set(hook.enabled_events or []))
        if missing:
            client.v1.webhook_endpoints.update(hook.id, params={"enabled_events": WEBHOOK_EVENTS})
            say(f"  updated webhook endpoint events (added {missing})")
        else:
            say(f"  kept webhook endpoint {url} ({hook.id})")
        if not os.getenv("STRIPE_WEBHOOK_SECRET"):
            say("  NOTE: STRIPE_WEBHOOK_SECRET isn't set here; copy it from the Stripe dashboard (Developers → Webhooks)")

    if args.emit_env:
        for k, v in emit.items():
            print(f"{k}={v}")
    else:
        for k in emit:
            say(f"  new setting to add to .env: {k} (re-run with --emit-env to print it)")
    say("done")


if __name__ == "__main__":
    main()
