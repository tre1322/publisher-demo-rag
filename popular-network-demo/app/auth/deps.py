"""FastAPI dependencies for tenant-scoped routes.

`get_tenant_id` reads `request.state.business_id` (populated by
RequireBusinessMiddleware) and 401s if absent. Use it to replace the old
hardcoded `business_id: int = 1` query-param default:

    # before
    def get_bootstrap(business_id: int = 1, db: Session = Depends(get_db)): ...

    # after
    def get_bootstrap(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)): ...

Why a dependency and not just reading state in the body: keeps the function
signature self-documenting AND lets tests override `get_tenant_id` in one
line via app.dependency_overrides.

`get_tenant_id_optional` returns None instead of 401-ing — for routes a
superuser can call across all tenants (admin endpoints, later).
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from .permissions import Capability, can


def get_tenant_id(request: Request) -> int:
    bid = getattr(request.state, "business_id", None)
    if bid is None:
        raise HTTPException(status_code=409, detail="no_active_business")
    return bid


def get_tenant_id_optional(request: Request) -> Optional[int]:
    return getattr(request.state, "business_id", None)


def require_role(*allowed: str):
    """Factory for a dependency that 403s if the user's role isn't in `allowed`."""

    def _checker(request: Request) -> str:
        role = getattr(request.state, "user_role", None)
        if getattr(request.state, "is_superuser", False):
            return role or "owner"  # superusers pass any role gate
        if role not in allowed:
            raise HTTPException(status_code=403, detail=f"role_required: {allowed}")
        return role

    return _checker


# Plain-English verbs for the 403 message, so the dashboard can show the
# detail as-is. Keys mirror permissions.Capability.
_CAPABILITY_VERB: dict[str, str] = {
    "publish_post": "create or edit posts",
    "manage_ads": "change ads or ad budgets",
    "respond_to_review": "reply to reviews",
    "edit_marketing_plan": "edit the marketing plan",
    "manage_inventory": "change inventory",
    "manage_chatbot": "change the chatbot",
    "manage_billing": "change billing",
    "manage_invites": "invite teammates",
    "manage_settings": "change settings",
    "authorize_ad_autonomy": "change autonomous ad spend",
}


def has_capability(request: Request, capability: Capability) -> bool:
    """Superusers pass every check; everyone else goes through the matrix."""
    if getattr(request.state, "is_superuser", False):
        return True
    return can(getattr(request.state, "user_role", None) or "", capability)


def require_capability(capability: Capability):
    """Route dependency: 403 unless the signed-in role has `capability`.

    Use it on the decorator so the handler signature stays unchanged:

        @router.post("/posts", dependencies=[Depends(require_capability("publish_post"))])
    """

    def _checker(request: Request) -> None:
        if has_capability(request, capability):
            return
        role = getattr(request.state, "user_role", None) or "current"
        verb = _CAPABILITY_VERB.get(capability, "do that")
        raise HTTPException(
            status_code=403,
            detail=f"Your {role} access can't {verb}. Ask the business owner if you need it.",
        )

    return _checker
