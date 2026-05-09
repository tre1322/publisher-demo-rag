"""Amplora billing module — subscriptions, tier history, and publisher revenue share.

Three tables:
  - subscriptions: current Stripe-state per business (one row per Stripe sub_id)
  - tier_history: append-only audit log of tier transitions
  - publisher_revenue_share: who gets paid what cut, for what window

The webhook handler in stripe_webhook.py is the source of truth for all three
tables when billing changes. The attribution module decides which publisher
gets revenue credit at business signup time (called from the register flow,
not from the webhook).
"""
