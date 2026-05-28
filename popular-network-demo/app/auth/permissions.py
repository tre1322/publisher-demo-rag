"""Role enum + capability predicates for BusinessUser.

Locked 2026-05-27 by Trevor's pick: Linear-style three-role model.

  owner   — full access (billing, invite, publish, ads spend, settings)
  editor  — publish + compose + ads, but NO billing/invite/settings mutation
  viewer  — read-only across all dashboards; cannot mutate anything

Add a new capability:
  1. Add the key to `Capability` literal
  2. Add the per-role boolean to `_CAPABILITY_MATRIX`
  3. Call `can(user_role, "your_cap")` at the route boundary

Why a matrix and not per-cap functions: a single dict is grep-able and you can
audit "what can an editor do?" by reading one column. Per-cap functions scatter
the answer across files.
"""
from __future__ import annotations

from typing import Literal

Role = Literal["owner", "editor", "viewer"]
Capability = Literal[
    "view_dashboard",     # read any tab
    "publish_post",       # Compose → publish
    "manage_ads",         # Ads & Spend → start/pause/budget
    "respond_to_review",  # Reviews → reply
    "edit_marketing_plan",
    "manage_inventory",
    "manage_chatbot",     # Phase G keys + voice brief
    "manage_billing",     # Settings → Billing → tier change
    "manage_invites",     # invite teammates
    "manage_settings",    # cadence + notifications
]

# Rows = roles, columns = capabilities. The "default tightest" rule: when in
# doubt about a new capability, default editor=False/viewer=False — easier to
# loosen later than to take a capability away from someone who had it.
_CAPABILITY_MATRIX: dict[Role, dict[Capability, bool]] = {
    "owner": {
        "view_dashboard": True,
        "publish_post": True,
        "manage_ads": True,
        "respond_to_review": True,
        "edit_marketing_plan": True,
        "manage_inventory": True,
        "manage_chatbot": True,
        "manage_billing": True,
        "manage_invites": True,
        "manage_settings": True,
    },
    "editor": {
        "view_dashboard": True,
        "publish_post": True,
        "manage_ads": True,
        "respond_to_review": True,
        "edit_marketing_plan": True,
        "manage_inventory": True,
        "manage_chatbot": True,
        "manage_billing": False,
        "manage_invites": False,
        "manage_settings": False,
    },
    "viewer": {
        "view_dashboard": True,
        "publish_post": False,
        "manage_ads": False,
        "respond_to_review": False,
        "edit_marketing_plan": False,
        "manage_inventory": False,
        "manage_chatbot": False,
        "manage_billing": False,
        "manage_invites": False,
        "manage_settings": False,
    },
}


def can(role: str, capability: Capability) -> bool:
    """True if `role` is allowed `capability`. Unknown role → False (fail closed)."""
    row = _CAPABILITY_MATRIX.get(role)  # type: ignore[arg-type]
    if row is None:
        return False
    return row.get(capability, False)


VALID_ROLES: tuple[Role, ...] = ("owner", "editor", "viewer")
