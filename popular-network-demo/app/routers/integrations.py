"""Integrations router — real OAuth for external platforms (Phase I.1).

LinkedIn is the first real one. Endpoints:

    GET /api/integrations/linkedin/connect
        Start the handshake. Returns {live, authUrl, scopes}. When credentials
        aren't configured, returns {live: false} and the frontend falls back to
        the Phase E mock connect so the demo keeps working.

    GET /api/integrations/linkedin/callback?code&state[&error]
        LinkedIn redirects the member's browser here after consent. Verifies the
        CSRF state, exchanges the code for tokens, stores them on the
        AdConnection row, and redirects back to the dashboard.

    GET /api/integrations/linkedin/status
        Connection health for the Settings UI (live flag, connected account,
        scopes, token expiry).

All three are tenant-scoped via get_tenant_id (the callback is an authed
top-level navigation — SameSite=Lax sends the session cookie on the redirect).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from ..auth.deps import get_tenant_id
from ..db import get_db
from ..integrations import linkedin as li
from ..integrations import linkedin_store as store

router = APIRouter()

# Where the callback sends the browser when done. Relative so it works on any
# host (localhost dev, dashboard.amplafai.com prod).
_DASHBOARD_RETURN = "/?tab=settings&li={status}"


@router.get("/integrations/linkedin/connect")
def linkedin_connect(
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Begin the 3-legged OAuth handshake (or report that it's not live yet)."""
    cfg = li.get_config()
    if not cfg.is_live:
        # Honest: the demo's "Connect" falls back to the mock when we report this.
        return {"live": False, "reason": "credentials_not_configured"}

    state = li.generate_state()
    conn = store.get_or_create_connection(db, business_id)
    conn.oauth_state = state
    conn.status = "pending"
    db.commit()

    return {
        "live": True,
        "authUrl": li.build_authorization_url(state),
        "scopes": list(cfg.scopes),
    }


@router.get("/integrations/linkedin/callback")
def linkedin_callback(
    request: Request,
    business_id: int = Depends(get_tenant_id),
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Handle the redirect back from LinkedIn. Always 302s to the dashboard with
    a `li=` status so the UI can toast the outcome — never returns raw JSON to
    the browser."""

    def _return(status: str) -> RedirectResponse:
        return RedirectResponse(url=_DASHBOARD_RETURN.format(status=status), status_code=302)

    # Member denied consent, or LinkedIn reported an error.
    if error:
        return _return("denied")

    if not code or not state:
        return _return("missing_params")

    conn = store.get_connection(db, business_id)
    if conn is None or not conn.oauth_state:
        return _return("no_handshake")
    # Constant-ish CSRF check: the state we stored must match what came back.
    if state != conn.oauth_state:
        return _return("state_mismatch")

    try:
        bundle = li.exchange_code_for_token(code)
        # Best-effort enrichment: the member's name + their first ad account.
        user_name: Optional[str] = None
        account_urn: Optional[str] = None
        client = li.LinkedInClient(bundle.access_token)
        try:
            try:
                user_name = client.me().get("name") or None
            except li.LinkedInError:
                user_name = None
            try:
                account_urn = client.primary_ad_account_urn()
            except li.LinkedInError:
                account_urn = None
        finally:
            client.close()

        store.store_tokens(db, conn, bundle, user_name=user_name, account_urn=account_urn)
        conn.account_label = (
            f"LinkedIn — {user_name}" if user_name else "LinkedIn Campaign Manager"
        )
        db.commit()
    except li.LinkedInError:
        db.rollback()
        # Leave the row pending so the owner can retry; surface the failure.
        return _return("error")

    return _return("connected")


@router.get("/integrations/linkedin/status")
def linkedin_status(
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Connection health for the Settings Ad-accounts row."""
    cfg = li.get_config()
    conn = store.get_connection(db, business_id)

    connected = store.connection_is_live(conn)
    expired = bool(
        conn is not None
        and conn.status == "connected"
        and conn.token_expires_at is not None
        and conn.token_expires_at <= datetime.utcnow()
    )

    return {
        "live": cfg.is_live,                       # are real credentials configured?
        "connected": connected,                    # connected AND token valid
        "status": conn.status if conn else "disconnected",
        "expired": expired,                        # connected but token lapsed → reconnect
        "accountUrn": conn.account_urn if conn else None,
        "connectedUserName": conn.connected_user_name if conn else None,
        "scopes": (conn.scope.split() if conn and conn.scope else list(cfg.scopes)),
        "tokenExpiresAt": (
            conn.token_expires_at.isoformat() if conn and conn.token_expires_at else None
        ),
        "redirectUri": cfg.redirect_uri,
    }
