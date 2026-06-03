"""LinkedIn ad-platform integration (Phase I.1) — the first REAL ad platform.

Dormant until LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET are set; see
config.is_live(). While dormant, the dashboard falls back to the Phase E mock
so the Quadd demo keeps working through the MDP-approval wait.

Public surface:
    is_live()                       — the one switch
    get_config()                    — env-derived config (client id, scopes, redirect)
    generate_state()                — CSRF state for the handshake
    build_authorization_url(state)  — where to send the member's browser
    exchange_code_for_token(code)   — code → TokenBundle
    refresh_access_token(rt)        — refresh → TokenBundle
    LinkedInClient                  — Marketing API wrapper (.me / .create / .get / .post)
    create_boost_campaign(...)      — orchestrate group+campaign, return URN
    pause_campaign(...)             — pause a live campaign
    fetch_campaign_analytics(...)   — pull impressions/clicks/spend
    errors: LinkedInError, LinkedInNotConfigured, LinkedInAuthError,
            LinkedInAPIError, LinkedInProvisioningError
"""
from __future__ import annotations

from .campaigns import create_boost_campaign, list_campaigns, pause_campaign
from .client import LinkedInClient
from .config import DEFAULT_SCOPES, get_config, is_live
from .errors import (
    LinkedInAPIError,
    LinkedInAuthError,
    LinkedInError,
    LinkedInNotConfigured,
    LinkedInProvisioningError,
)
from .oauth import (
    TokenBundle,
    build_authorization_url,
    exchange_code_for_token,
    generate_state,
    refresh_access_token,
)
from .reporting import date_range_last_n_days, fetch_campaign_analytics

__all__ = [
    "DEFAULT_SCOPES",
    "LinkedInAPIError",
    "LinkedInAuthError",
    "LinkedInClient",
    "LinkedInError",
    "LinkedInNotConfigured",
    "LinkedInProvisioningError",
    "TokenBundle",
    "build_authorization_url",
    "create_boost_campaign",
    "date_range_last_n_days",
    "exchange_code_for_token",
    "fetch_campaign_analytics",
    "generate_state",
    "get_config",
    "is_live",
    "list_campaigns",
    "pause_campaign",
    "refresh_access_token",
]
