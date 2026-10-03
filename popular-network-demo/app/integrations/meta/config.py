"""Meta Marketing API config — env-driven, dormant until both secrets exist.

Amplafai works as an agency: each client adds Amplafai's business as a
partner on their own ad account (docs/managed_ads_runbook.md), and every API
call uses ONE token, a system user's, from Amplafai's own Business Manager.
There is no per-client sign-in to Meta.

The switch is `is_live()`: META_ACCESS_TOKEN (the system user's token) and
META_APP_SECRET (the app's secret, used to sign every call with
appsecret_proof) must both be set. Going live is an env change plus a
restart; no code change.

Read at call time, not import time, so smokes can flip it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Graph API version. Meta keeps each version about two years; bump when the
# changelog says this one is retiring (META_GRAPH_VERSION overrides).
DEFAULT_VERSION = "v26.0"
GRAPH_BASE = "https://graph.facebook.com"


@dataclass(frozen=True)
class MetaConfig:
    access_token: str
    app_secret: str
    version: str

    @property
    def is_live(self) -> bool:
        return bool(self.access_token and self.app_secret)

    @property
    def base_url(self) -> str:
        return f"{GRAPH_BASE}/{self.version}"


def get_config() -> MetaConfig:
    return MetaConfig(
        access_token=os.getenv("META_ACCESS_TOKEN", "").strip(),
        app_secret=os.getenv("META_APP_SECRET", "").strip(),
        version=(os.getenv("META_GRAPH_VERSION", "").strip() or DEFAULT_VERSION),
    )


def is_live() -> bool:
    """True iff Meta's API is switched on for this server. The whole switch."""
    return get_config().is_live
