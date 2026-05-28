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
