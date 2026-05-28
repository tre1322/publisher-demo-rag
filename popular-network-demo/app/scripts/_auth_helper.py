"""Shared login helper for Phase H smokes.

After Phase H.1 every /api/* call requires a session cookie. The pre-existing
smoke suite (smoke_bootstrap, smoke_chat, smoke_ads, etc) was written before
auth existed and would 401 on every request.

`bootstrap_login(client)` does the minimum to get a session cookie on a
fresh-DB TestClient:
  1. POST /api/auth/register — first user in a clean DB becomes superuser
     (the "bootstrap exception" path in app/routers/auth.py). Body includes
     business_id=1 + role="owner" so the user is wired to the seeded Quadd
     business in one shot.
  2. POST /api/auth/login — returns a session cookie scoped to business_id=1.

Returns the same client (mutated — cookie jar carries the session).

Usage:
    with TestClient(app) as client:
        bootstrap_login(client)
        # ...rest of the smoke runs as the Quadd owner
"""
from __future__ import annotations

from typing import Any

SMOKE_EMAIL = "smoke@example.com"  # RFC 2606 reserved-for-testing; pydantic email-validator rejects .local
SMOKE_PASSWORD = "smokepass-correct-horse"  # min_length=8 enforced server-side


def bootstrap_login(client: Any, *, business_id: int = 1, role: str = "owner") -> None:
    """Register the bootstrap superuser + log in. Mutates client.cookies."""
    r = client.post(
        "/api/auth/register",
        json={
            "email": SMOKE_EMAIL,
            "password": SMOKE_PASSWORD,
            "business_id": business_id,
            "role": role,
        },
    )
    # In a fresh-DB smoke this is 200 (bootstrap path). If the smoke is re-run
    # against a non-fresh DB we'll get 403 — the user already exists from a
    # prior run. Either way, login below is the authoritative step.
    if r.status_code not in (200, 403, 409):
        raise RuntimeError(f"bootstrap register unexpected status {r.status_code}: {r.text}")

    r = client.post(
        "/api/auth/login",
        json={
            "email": SMOKE_EMAIL,
            "password": SMOKE_PASSWORD,
            "active_business_id": business_id,
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"bootstrap login failed: {r.status_code} {r.text}")
