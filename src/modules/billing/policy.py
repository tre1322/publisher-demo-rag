"""Amplora billing policy — knobs that encode business rules, not engineering.

Trevor owns these. They control how customers experience payment problems,
tier transitions, and grace periods. Each constant has a TODO with the
trade-off captured.
"""

import logging

logger = logging.getLogger(__name__)


# ── Past-due policy ────────────────────────────────────────────────
#
# When Stripe sends invoice.payment_failed (card declined, expired, etc.),
# the subscription rolls to status='past_due'. Stripe then automatically
# retries the charge for ~3 attempts over ~1 week before marking unpaid
# and (eventually) canceled.
#
# What does Amplora do during that window?
#
# TODO(trevor): set these two constants. Both are 1-line decisions but
# they shape the customer experience.

# Days after first payment_failed before we DOWNGRADE service.
# - 0   = immediate (revenue-first; expect support calls)
# - 3   = brief grace (most card-failure churn happens in the first 72h)
# - 7   = full grace (matches Stripe's automatic retry window)
# - 14  = generous (one billing cycle of leeway; soft on revenue)
PAST_DUE_GRACE_DAYS: int | None = None  # set me

# What "downgrade" actually means once the grace runs out:
# - "freeze"   → no new posts get drafted/published; existing content stays;
#                chatbot still answers from indexed content (revenue-soft)
# - "pause"    → everything stops, including chatbot answers about this
#                business (revenue-hard, but the business effectively
#                vanishes from the network)
# - "cancel"   → flip to status='canceled' immediately; same as deletion
#                from the user's POV (revenue-hardest)
PAST_DUE_DOWNGRADE_MODE: str | None = None  # one of: "freeze" | "pause" | "cancel"

# Days BEFORE downgrade that we show the business owner a banner / send
# an SMS warning. 0 = no warning; ~2 days is normal.
PAST_DUE_WARN_DAYS_BEFORE: int = 2


# ── Helper used by the rest of the system ──────────────────────────


def is_past_due_policy_set() -> bool:
    """Returns True iff Trevor has filled in the past-due constants."""
    return (
        PAST_DUE_GRACE_DAYS is not None
        and PAST_DUE_DOWNGRADE_MODE in ("freeze", "pause", "cancel")
    )


def assert_past_due_policy() -> None:
    """Loud failure for code paths that depend on the policy being set.

    Use this in cron jobs / sweepers that act on past_due subscriptions.
    The webhook handler does NOT call this — it just records the status —
    so the lack of a policy doesn't break Stripe ack'ing.
    """
    if not is_past_due_policy_set():
        raise RuntimeError(
            "Past-due policy not configured. Set PAST_DUE_GRACE_DAYS and "
            "PAST_DUE_DOWNGRADE_MODE in src/modules/billing/policy.py before "
            "running any sweep/cron that acts on past_due subscriptions."
        )
