"""LinkedIn integration config — env-driven, live the instant creds exist.

The single switch for the whole integration is `is_live()`. It returns True
only when both LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET are set. Until
then every real-call path (OAuth exchange, Marketing API) raises
LinkedInNotConfigured and the dashboard falls back to the Phase E mock — so the
Quadd demo keeps working while we wait on Marketing Developer Platform approval.

Going live post-approval is therefore a pure env diff: drop the two secrets
into the droplet's .env, restart, done. No code change.

Config is read at *call time* (not import time) so smokes can set env vars and
then exercise the live path without re-importing the module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Minimum scopes the agent actually uses. Requesting more than this risks an
# MDP review rejection. (per the integration plan)
#   r_ads            — read ad accounts / campaigns
#   rw_ads           — create / manage / pause campaigns
#   r_ads_reporting  — pull performance metrics
#   r_basicprofile   — authed user's name for the "Connected as ..." UI
DEFAULT_SCOPES: tuple[str, ...] = ("r_ads", "rw_ads", "r_ads_reporting", "r_basicprofile")

# Endpoints. OAuth lives on www.linkedin.com; data on api.linkedin.com.
AUTH_BASE = "https://www.linkedin.com/oauth/v2"
API_BASE = "https://api.linkedin.com"

# Versioned REST: the LinkedIn-Version header is YYYYMM. Bump as LinkedIn
# deprecates older months (they keep ~12 months live).
REST_VERSION = "202405"

# The OAuth redirect must EXACTLY match a redirect URL registered on the
# developer app. Production points at the prod dashboard host.
DEFAULT_REDIRECT_URI = "https://dashboard.amplafai.com/api/integrations/linkedin/callback"


@dataclass(frozen=True)
class LinkedInConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]

    @property
    def is_live(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def scope_param(self) -> str:
        """Space-delimited scope string for the authorization URL."""
        return " ".join(self.scopes)


def get_config() -> LinkedInConfig:
    """Build config fresh from the environment on every call."""
    scopes_env = os.getenv("LINKEDIN_SCOPES", "").strip()
    scopes = tuple(s for s in scopes_env.split() if s) or DEFAULT_SCOPES
    return LinkedInConfig(
        client_id=os.getenv("LINKEDIN_CLIENT_ID", "").strip(),
        client_secret=os.getenv("LINKEDIN_CLIENT_SECRET", "").strip(),
        redirect_uri=os.getenv("LINKEDIN_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
        scopes=scopes,
    )


def is_live() -> bool:
    """True iff real LinkedIn credentials are configured. The whole switch."""
    return get_config().is_live
