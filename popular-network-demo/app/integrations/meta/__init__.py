"""Meta (Facebook + Instagram) ad-platform integration — Phase 5c.

Dormant until META_ACCESS_TOKEN + META_APP_SECRET are set; see config.is_live().
Self-contained: no app.models import (DB glue lives in ../meta_store.py).

Public surface:
    is_live() / get_config()          the switch and its settings
    MetaClient                        Graph API calls, signed with appsecret_proof
    read_account / read_page / find_city      checks when Amplafai links a client
    create_paused / activate / set_status     campaigns (always created PAUSED)
    daily_results                     spend, impressions, clicks per campaign per day
    MetaError / MetaAPIError / MetaNotConfigured
"""
from __future__ import annotations

from .ads import (
    MAX_RADIUS_MILES,
    MIN_RADIUS_MILES,
    activate,
    create_paused,
    daily_results,
    find_city,
    money_to_cents,
    radius_from_hint,
    read_account,
    read_page,
    set_status,
    split_location,
    targeting,
)
from .client import MetaAPIError, MetaClient, MetaError, MetaNotConfigured, appsecret_proof
from .config import DEFAULT_VERSION, get_config, is_live

__all__ = [
    "DEFAULT_VERSION", "MAX_RADIUS_MILES", "MIN_RADIUS_MILES", "MetaAPIError", "MetaClient", "MetaError",
    "MetaNotConfigured", "activate", "appsecret_proof", "create_paused", "daily_results", "find_city",
    "get_config", "is_live", "money_to_cents", "radius_from_hint", "read_account", "read_page", "set_status",
    "split_location", "targeting",
]
