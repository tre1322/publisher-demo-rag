"""Spend policy — when may the AI agent move money on its own?

Every agent ad-spend tool (allocate_platform_budget, schedule_boost,
pause_campaign) asks this module one question: APPLY the change now, or
PROPOSE it to the owner through the Approvals queue? Enforced in code, never
in the prompt — same discipline as the rest of the agent tools (see
feedback: tier-gated autonomous with soft fall).

Two layers:
  * The decision functions (`cap_change_decision`, `boost_decision`,
    `pause_decision`) are PURE business rules — no DB. This is where the
    policy lives; change the rules here and smoke_money_path.py pins them.
  * The DB helpers below gather the facts those rules need (is autonomy on,
    what did the owner authorize, how much headroom is left).

Default policy (Phase 0, 2026-10-01):
  1. Autonomy is opt-in. Tier 3+ AND the owner's server-side switch is on.
  2. The agent can never raise a cap above what the OWNER authorized for that
     platform this month. No owner cap at all → propose.
  3. A boost needs a cap with room for its full planned total AFTER money
     already committed to scheduled/active campaigns. No cap → propose.
  4. Pausing reduces spend, so it only needs autonomy on.
Anything that doesn't qualify soft-falls to a proposal — it never errors.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlalchemy.orm import Session

from ..models import AdCampaign, AdPlatformBudget, Business, SettingsRow

Decision = Literal["apply", "propose"]

# Reason codes travel to the tool card + the model so the owner is told WHY a
# change came to Approvals instead of happening.
REASON_TEXT: dict[str, str] = {
    "autonomy_off":    "Autonomous ad spend is off, so this needs your approval.",
    "no_owner_cap":    "You haven't set a monthly cap for this platform yet, so this needs your approval.",
    "above_owner_cap": "This is above the monthly cap you set, so it needs your approval.",
    "no_cap":          "There's no monthly cap on this platform yet, so this needs your approval.",
    "over_cap":        "This would go past what's left under your monthly cap, so it needs your approval.",
    "within_cap":      "Within the monthly cap you set.",
}


# --------------------------------------------------------------------------- #
# Pure decision rules
# --------------------------------------------------------------------------- #

def cap_change_decision(
    *, requested_cents: int, owner_cap_cents: Optional[int], autonomy_enabled: bool
) -> tuple[Decision, str]:
    """May the agent set a platform's monthly cap to `requested_cents` itself?"""
    if not autonomy_enabled:
        return "propose", "autonomy_off"
    if owner_cap_cents is None:
        return "propose", "no_owner_cap"
    if requested_cents > owner_cap_cents:
        return "propose", "above_owner_cap"
    return "apply", "within_cap"


def boost_decision(
    *, planned_total_cents: int, headroom_cents: Optional[int], autonomy_enabled: bool
) -> tuple[Decision, str]:
    """May the agent launch a boost costing `planned_total_cents` itself?

    `headroom_cents` is None when the platform has no cap this month.
    """
    if not autonomy_enabled:
        return "propose", "autonomy_off"
    if headroom_cents is None:
        return "propose", "no_cap"
    if planned_total_cents > headroom_cents:
        return "propose", "over_cap"
    return "apply", "within_cap"


def pause_decision(*, autonomy_enabled: bool) -> tuple[Decision, str]:
    """May the agent pause a campaign itself? Pausing only lowers spend."""
    if not autonomy_enabled:
        return "propose", "autonomy_off"
    return "apply", "within_cap"


# --------------------------------------------------------------------------- #
# DB facts the rules need
# --------------------------------------------------------------------------- #

def current_month_year() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def autonomy_enabled(db: Session, business_id: int) -> bool:
    """Tier 3+ plan AND the owner switched autonomous spend on (server-side)."""
    biz = db.get(Business, business_id)
    if biz is None or (biz.tier or 0) < 3:
        return False
    row = db.get(SettingsRow, business_id)
    return bool(row is not None and row.ad_autonomy_enabled)


def budget_row(db: Session, business_id: int, platform: str) -> Optional[AdPlatformBudget]:
    return (
        db.query(AdPlatformBudget)
        .filter(
            AdPlatformBudget.business_id == business_id,
            AdPlatformBudget.platform == platform,
            AdPlatformBudget.month_year == current_month_year(),
        )
        .one_or_none()
    )


def committed_cents(db: Session, business_id: int, platform: str) -> int:
    """Money promised to scheduled/active campaigns but not yet spent."""
    rows = (
        db.query(AdCampaign)
        .filter(
            AdCampaign.business_id == business_id,
            AdCampaign.platform == platform,
            AdCampaign.status.in_(("scheduled", "active")),
        )
        .all()
    )
    return sum(max(0, c.planned_total_cents - c.actual_spend_cents) for c in rows)


def platform_headroom_cents(db: Session, business_id: int, platform: str) -> Optional[int]:
    """Cap minus spent minus committed. None when there's no cap this month."""
    row = budget_row(db, business_id, platform)
    if row is None or row.monthly_cap_cents <= 0:
        return None
    left = row.monthly_cap_cents - row.spend_cents - committed_cents(db, business_id, platform)
    return max(0, left)
