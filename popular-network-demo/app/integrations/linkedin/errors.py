"""Typed errors for the LinkedIn integration.

Callers branch on these instead of catching bare Exception so a "not
configured yet" (expected, pre-approval) reads differently from a real API
failure (unexpected, needs surfacing per Trevor's surface-errors rule).
"""
from __future__ import annotations

from typing import Any, Optional


class LinkedInError(Exception):
    """Base for everything in this package."""


class LinkedInNotConfigured(LinkedInError):
    """A live call was attempted without client credentials.

    Expected pre-MDP-approval. The connect endpoint checks is_live() first, so
    in normal flow we never reach a real call while unconfigured — this guards
    misuse / direct calls.
    """


class LinkedInAuthError(LinkedInError):
    """OAuth handshake or token problem — bad code, expired refresh, CSRF state
    mismatch, malformed token response."""


class LinkedInAPIError(LinkedInError):
    """Non-2xx from a Marketing API call."""

    def __init__(self, status_code: int, message: str, body: Optional[Any] = None) -> None:
        super().__init__(f"{message} (HTTP {status_code})")
        self.status_code = status_code
        self.body = body


class LinkedInProvisioningError(LinkedInError):
    """Failed to create / pause a campaign on LinkedIn's side. Wraps the
    underlying LinkedInAPIError so the agent tool can return a clean error card
    instead of silently falling back to a mock id (which would hide the
    failure)."""
