"""LinkedIn 3-legged OAuth handshake + token refresh.

Flow (member authorization, per LinkedIn docs):
  1. build_authorization_url(state) → redirect the member's browser there.
  2. LinkedIn redirects back to our callback with ?code=...&state=...
  3. exchange_code_for_token(code) → access + refresh token bundle.
  4. refresh_access_token(refresh_token) → new access token when it nears expiry.

Every network function takes an optional injected `http` client so smokes can
drive the handshake through an httpx.MockTransport without touching the
network. `now` is injectable for deterministic expiry math in tests.

Token lifetimes (LinkedIn, as of v202405): access tokens ~60 days,
refresh tokens ~365 days. Refresh-token issuance requires the program to be
approved for it; if absent, the member simply re-authorizes when the access
token lapses.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

from .config import AUTH_BASE, get_config
from .errors import LinkedInAuthError, LinkedInNotConfigured

_TIMEOUT = httpx.Timeout(15.0)


@dataclass
class TokenBundle:
    """Parsed, expiry-resolved result of a token grant."""

    access_token: str
    expires_at: datetime
    refresh_token: Optional[str]
    refresh_expires_at: Optional[datetime]
    scope: str


def generate_state() -> str:
    """Opaque CSRF token. Persisted on the AdConnection row, verified on callback."""
    return secrets.token_urlsafe(24)


def build_authorization_url(state: str) -> str:
    """The URL we send the member's browser to for consent."""
    cfg = get_config()
    if not cfg.is_live:
        raise LinkedInNotConfigured("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET not set")
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
        "scope": cfg.scope_param,
    }
    return f"{AUTH_BASE}/authorization?{urlencode(params)}"


def exchange_code_for_token(
    code: str,
    *,
    http: Optional[httpx.Client] = None,
    now: Optional[datetime] = None,
) -> TokenBundle:
    """Trade the authorization code for an access (+ refresh) token."""
    cfg = get_config()
    if not cfg.is_live:
        raise LinkedInNotConfigured("cannot exchange code while unconfigured")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
    }
    return _post_token(body, http, now or datetime.utcnow())


def refresh_access_token(
    refresh_token: str,
    *,
    http: Optional[httpx.Client] = None,
    now: Optional[datetime] = None,
) -> TokenBundle:
    """Exchange a refresh token for a fresh access token."""
    cfg = get_config()
    if not cfg.is_live:
        raise LinkedInNotConfigured("cannot refresh while unconfigured")
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
    }
    return _post_token(body, http, now or datetime.utcnow())


def _post_token(body: dict[str, str], http: Optional[httpx.Client], now: datetime) -> TokenBundle:
    owns = http is None
    client = http or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.post(
            f"{AUTH_BASE}/accessToken",
            data=body,  # form-encoded, per LinkedIn
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise LinkedInAuthError(f"token endpoint returned {resp.status_code}: {resp.text}")
        data = resp.json()
    finally:
        if owns:
            client.close()
    return _parse_token_response(data, now)


def _parse_token_response(data: dict, now: datetime) -> TokenBundle:
    access = data.get("access_token")
    if not access:
        raise LinkedInAuthError(f"token response missing access_token: {data!r}")
    expires_in = int(data.get("expires_in", 0))
    refresh = data.get("refresh_token")
    refresh_expires_in = data.get("refresh_token_expires_in")
    return TokenBundle(
        access_token=access,
        expires_at=now + timedelta(seconds=expires_in),
        refresh_token=refresh,
        refresh_expires_at=(
            now + timedelta(seconds=int(refresh_expires_in)) if refresh_expires_in else None
        ),
        scope=data.get("scope", ""),
    )
