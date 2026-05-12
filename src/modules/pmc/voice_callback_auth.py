"""HMAC-signed tokens for the W2.2 voice-agent callback.

When the owner clicks "Save and start interview", the server mints a
signed callback token that travels via LiveKit room metadata to the
agent worker. When the call ends, the agent POSTs /business/pmc/voice/complete
with that token, and the server uses it to:

  1. Prove the POST is from the legitimate agent worker for THIS session
     (not an attacker forging a transcript for someone else's org).
  2. Resolve the session row → look up the org and the stashed quantitative
     answers.

Stateless. Reuses BUSINESS_SESSION_SECRET so we don't introduce a second
secret to rotate. Namespaced with a distinct salt so a leaked session
cookie cannot be replayed as a callback token (and vice versa).

Token TTL is 90 min — comfortably longer than the 60-min interview hard
cap from interview_script.INTERVIEW_LENGTH_CAP_MINUTES, with buffer for
egress finalization. Replay protection beyond the TTL is delegated to
the application-level idempotency check (one PMC draft per session).
"""

import logging
import os
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

CALLBACK_SALT = "amplora.pmc.voice_callback.v1"
CALLBACK_PURPOSE = "voice_complete"
DEFAULT_TTL_SECONDS = 90 * 60  # 90 min — longer than the hard interview cap

_secret = os.getenv("BUSINESS_SESSION_SECRET", "dev-secret-change-me")
if _secret == "dev-secret-change-me":
    logger.warning(
        "BUSINESS_SESSION_SECRET not set — voice callback tokens are insecure"
    )
_serializer = URLSafeTimedSerializer(_secret, salt=CALLBACK_SALT)


def mint_callback_token(session_id: int, organization_id: int) -> str:
    """Return a signed token the agent will return on /voice/complete.

    Payload is JSON-serializable; itsdangerous handles the signing.
    """
    return _serializer.dumps(
        {
            "session_id": session_id,
            "org_id": organization_id,
            "purpose": CALLBACK_PURPOSE,
        }
    )


def verify_callback_token(
    token: str, max_age_seconds: int = DEFAULT_TTL_SECONDS
) -> dict[str, Any] | None:
    """Verify a callback token. Returns the decoded payload or None.

    Returns None on: bad signature, expired token, wrong purpose, malformed
    payload. Logs the reason at INFO so failures show up in Railway logs
    when an attacker probes the endpoint.
    """
    try:
        payload = _serializer.loads(token, max_age=max_age_seconds)
    except SignatureExpired:
        logger.info("voice callback token rejected: expired")
        return None
    except BadSignature:
        logger.info("voice callback token rejected: bad signature")
        return None

    if not isinstance(payload, dict):
        logger.info("voice callback token rejected: payload not a dict")
        return None

    if payload.get("purpose") != CALLBACK_PURPOSE:
        logger.info(
            "voice callback token rejected: wrong purpose %r", payload.get("purpose")
        )
        return None

    session_id = payload.get("session_id")
    org_id = payload.get("org_id")
    if not isinstance(session_id, int) or not isinstance(org_id, int):
        logger.info("voice callback token rejected: session_id/org_id not ints")
        return None

    return payload
