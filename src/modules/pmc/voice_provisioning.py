"""LiveKit room + token + agent-dispatch helpers for the W2.2 voice interview.

This module is the only place that imports `livekit.api`. The routes in
src/business_frontend/routes.py call these helpers so they don't have to
know about the LiveKit SDK shape — which makes the routes testable with a
plain stub and keeps the SDK-churn blast radius small.

Room naming convention: room name = f"pmc-{session_id}". This is stable
for the lifetime of the session, so a page refresh on /pmc/interview
re-mints a participant token for the SAME room without disturbing the
agent that may already be in it.

Metadata: server-set, immutable from clients. Contains:
    {
      "session_id": int,
      "org_id": int,
      "owner_name": str,
      "org_name": str,
      "callback_token": str,   # HMAC-signed via voice_callback_auth
    }
The agent reads this once on join via ctx.room.metadata and uses it to
build the system prompt + the POST /voice/complete payload.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from src.core.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    PMC_AGENT_NAME,
)

logger = logging.getLogger(__name__)


class VoiceProvisioningError(Exception):
    """Raised when LiveKit provisioning fails — caller should return 503."""


def is_configured() -> bool:
    """Return True iff all LiveKit env vars are non-empty.

    Used by /pmc/voice/start to surface a 503 with a clear message when
    Trevor hasn't filled in the LiveKit Cloud keys yet, rather than a
    confusing stack trace from inside the SDK.
    """
    return bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)


def room_name_for_session(session_id: int) -> str:
    """Stable room name derived from session id. Pure function."""
    return f"pmc-{session_id}"


def mint_participant_token(
    room_name: str, identity: str, display_name: str
) -> str:
    """Mint a JWT the browser uses to join the LiveKit room.

    Token grants room_join for THIS room only — the user can't join other
    rooms with it. Identity must be unique per participant within a room.
    """
    if not is_configured():
        raise VoiceProvisioningError(
            "LiveKit not configured: set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
        )
    try:
        from livekit import api as lkapi
    except ImportError as e:
        raise VoiceProvisioningError(
            "livekit-api package not installed (run `uv sync`)"
        ) from e

    token = (
        lkapi.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(display_name)
        .with_grants(
            lkapi.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        # 90 min — comfortably longer than the 60-min interview hard cap.
        .with_ttl(timedelta(minutes=90))
    )
    return token.to_jwt()


async def start_voice_session(
    *,
    session_id: int,
    organization_id: int,
    owner_name: str,
    org_name: str,
    callback_token: str,
) -> str:
    """Create the LiveKit room with metadata + dispatch the agent.

    Returns the room name. Raises VoiceProvisioningError on misconfig
    or SDK error so the route can return 503 cleanly.

    Idempotent on the room: if a room with this name already exists
    (e.g. owner refreshed the start page), LiveKit returns the existing
    room and we re-dispatch the agent if needed.
    """
    if not is_configured():
        raise VoiceProvisioningError(
            "LiveKit not configured: set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
        )
    try:
        from livekit import api as lkapi
    except ImportError as e:
        raise VoiceProvisioningError(
            "livekit-api package not installed (run `uv sync`)"
        ) from e

    room_name = room_name_for_session(session_id)
    metadata: dict[str, Any] = {
        "session_id": session_id,
        "org_id": organization_id,
        "owner_name": owner_name,
        "org_name": org_name,
        "callback_token": callback_token,
    }
    metadata_json = json.dumps(metadata)

    # NB: api.LiveKitAPI is an async context manager in livekit-api v1.x.
    # Day 0 spike must verify this remains stable; if the SDK churns, this
    # is the one place to touch.
    async with lkapi.LiveKitAPI(
        LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    ) as client:
        try:
            await client.room.create_room(
                lkapi.CreateRoomRequest(
                    name=room_name,
                    metadata=metadata_json,
                    # Empty timeout = use server default. Don't auto-close
                    # the room before the agent arrives.
                )
            )
            logger.info("LiveKit room created: %s (session=%s)", room_name, session_id)
        except Exception as e:
            # Room may already exist (refresh); LiveKit returns AlreadyExists.
            # We accept that and continue to dispatch.
            logger.info(
                "create_room non-fatal: %s (room may already exist for session=%s)",
                e,
                session_id,
            )

        # Dispatch agent by name. The agent worker registers with this
        # same `agent_name` and the LiveKit Cloud router routes the job.
        try:
            await client.agent_dispatch.create_dispatch(
                lkapi.CreateAgentDispatchRequest(
                    agent_name=PMC_AGENT_NAME,
                    room=room_name,
                    metadata=metadata_json,
                )
            )
            logger.info(
                "Agent dispatch created: agent=%s room=%s",
                PMC_AGENT_NAME,
                room_name,
            )
        except Exception as e:
            # Dispatch may already exist (refresh) — same idempotency story.
            logger.info("create_dispatch non-fatal: %s", e)

    return room_name
