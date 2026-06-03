"""DB glue between AdConnection rows and the pure `linkedin` API package.

Keeps the linkedin/ subpackage free of any app.models / SQLAlchemy import (it
stays a clean API wrapper that's unit-testable with a MockTransport). All the
"read the token off the row, refresh it if stale, build a client" logic lives
here, shared by the integrations router and the agent-tool swap point so there's
exactly one place that touches token lifecycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import AdConnection
from . import linkedin as li

# Refresh the access token if it's within this window of expiry. LinkedIn
# access tokens last ~60 days, so 5 minutes of skew is plenty of headroom.
_REFRESH_SKEW = timedelta(minutes=5)

_PLATFORM = "linkedin"


def get_connection(db: Session, business_id: int) -> Optional[AdConnection]:
    return (
        db.query(AdConnection)
        .filter(AdConnection.business_id == business_id, AdConnection.platform == _PLATFORM)
        .one_or_none()
    )


def get_or_create_connection(db: Session, business_id: int) -> AdConnection:
    conn = get_connection(db, business_id)
    if conn is None:
        conn = AdConnection(
            business_id=business_id,
            platform=_PLATFORM,
            account_label="LinkedIn Campaign Manager",
            status="disconnected",
        )
        db.add(conn)
        db.flush()
    return conn


def connection_is_live(conn: Optional[AdConnection]) -> bool:
    """True iff this connection can make a real call right now: connected,
    has an access token, and the token hasn't fully expired."""
    if conn is None or conn.status != "connected" or not conn.oauth_token:
        return False
    if conn.token_expires_at and conn.token_expires_at <= datetime.utcnow():
        return False
    return True


def store_tokens(
    db: Session,
    conn: AdConnection,
    bundle: "li.TokenBundle",
    *,
    user_name: Optional[str] = None,
    account_urn: Optional[str] = None,
) -> None:
    """Persist a fresh token bundle onto the connection row. Does not commit —
    caller owns the transaction."""
    conn.oauth_token = bundle.access_token
    conn.token_expires_at = bundle.expires_at
    if bundle.refresh_token:
        conn.refresh_token = bundle.refresh_token
    if bundle.refresh_expires_at:
        conn.refresh_expires_at = bundle.refresh_expires_at
    conn.scope = bundle.scope
    conn.status = "connected"
    conn.oauth_state = None
    conn.last_synced_at = datetime.utcnow()
    if user_name is not None:
        conn.connected_user_name = user_name
    if account_urn is not None:
        conn.account_urn = account_urn
        conn.external_account_id = account_urn


def ensure_fresh_token(db: Session, conn: AdConnection) -> str:
    """Return a usable access token, refreshing in place if it's near expiry.

    Raises LinkedInAuthError if the token is expired and there's no refresh
    token to trade in (the owner must reconnect).
    """
    now = datetime.utcnow()
    near_expiry = conn.token_expires_at is not None and (conn.token_expires_at - _REFRESH_SKEW) <= now
    if near_expiry:
        if not conn.refresh_token:
            raise li.LinkedInAuthError("access token expired and no refresh token — reconnect required")
        bundle = li.refresh_access_token(conn.refresh_token)
        store_tokens(db, conn, bundle)
        db.commit()
    return conn.oauth_token  # type: ignore[return-value]


def build_client(db: Session, conn: AdConnection) -> "li.LinkedInClient":
    return li.LinkedInClient(ensure_fresh_token(db, conn))


def provision_linkedin_campaign(
    db: Session,
    business_id: int,
    *,
    name: str,
    daily_budget_cents: int,
    duration_days: int,
) -> str:
    """Create a real LinkedIn campaign and return its URN.

    Caller must have already confirmed `li.is_live()`. Raises
    LinkedInProvisioningError if the account isn't connected, has no ad account,
    or the API call fails — so the caller surfaces it instead of masking it with
    a mock id.
    """
    conn = get_connection(db, business_id)
    if not connection_is_live(conn):
        raise li.LinkedInProvisioningError("LinkedIn account is not connected")
    assert conn is not None  # narrowed by connection_is_live
    if not conn.account_urn:
        raise li.LinkedInProvisioningError("no LinkedIn ad account on the connection")
    client = build_client(db, conn)
    try:
        return li.create_boost_campaign(
            client,
            account_urn=conn.account_urn,
            name=name,
            daily_budget_cents=daily_budget_cents,
            duration_days=duration_days,
        )
    finally:
        client.close()
