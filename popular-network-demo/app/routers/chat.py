"""Chat router — Phase C.1 blocking endpoint.

POST /api/chat/turn  — owner sends a message, server calls Claude with the
                      full conversation history + DB-derived system prompt,
                      persists both the owner turn and the agent reply, and
                      returns them in a single response.

No streaming yet (C.2). No tool use yet (C.3). The goal of C.1 is to prove
the end-to-end loop works with the simplest possible wire format.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.system_prompt import build_system_prompt
from ..db import get_db
from ..models import ChatTurn

log = logging.getLogger("popular_network.chat")
router = APIRouter()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_LIVE_CONVERSATION_ID = "seed"  # live turns continue the seeded thread


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    business_id: int = 1


class ChatTurnPayload(BaseModel):
    who: str
    when: str
    text: str
    attachment: Optional[dict[str, Any]] = None


class ChatTurnResponse(BaseModel):
    ok: bool
    ownerTurn: ChatTurnPayload
    agentTurn: ChatTurnPayload


def _format_when(dt: datetime) -> str:
    """Match the human-readable timestamps used in the seeded thread."""
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"Today, {hour}:{dt.minute:02d}{suffix}"


def _turn_to_message(turn: ChatTurn) -> dict[str, str]:
    role = "user" if turn.who == "owner" else "assistant"
    return {"role": role, "content": turn.text}


@router.post("/chat/turn", response_model=ChatTurnResponse)
def take_turn(req: ChatTurnRequest, db: Session = Depends(get_db)) -> ChatTurnResponse:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY not set. Add it to popular-network-demo/.env "
                "(or the parent publisher-demo-rag/.env) and restart the server."
            ),
        )

    history = (
        db.query(ChatTurn)
        .filter(ChatTurn.business_id == req.business_id)
        .order_by(ChatTurn.id.asc())
        .all()
    )
    messages = [_turn_to_message(t) for t in history]
    messages.append({"role": "user", "content": req.message})

    system_text = build_system_prompt(db, req.business_id)
    system_blocks = [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_blocks,
            messages=messages,
        )
    except Exception as exc:
        log.exception("Anthropic call failed")
        raise HTTPException(status_code=502, detail=f"Claude call failed: {exc}") from exc

    agent_text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    agent_text = "".join(agent_text_parts).strip()
    if not agent_text:
        raise HTTPException(status_code=502, detail="Claude returned an empty response.")

    cache_create = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    log.info(
        f"chat.turn business={req.business_id} cache_create={cache_create} "
        f"cache_read={cache_read} input={resp.usage.input_tokens} output={resp.usage.output_tokens}"
    )

    now = datetime.utcnow()
    when_label = _format_when(now)

    owner_turn = ChatTurn(
        business_id=req.business_id,
        conversation_id=_LIVE_CONVERSATION_ID,
        who="owner",
        when_label=when_label,
        text=req.message,
        attachment_json=None,
    )
    agent_turn = ChatTurn(
        business_id=req.business_id,
        conversation_id=_LIVE_CONVERSATION_ID,
        who="agent",
        when_label=when_label,
        text=agent_text,
        attachment_json=None,
    )
    db.add(owner_turn)
    db.add(agent_turn)
    db.commit()

    return ChatTurnResponse(
        ok=True,
        ownerTurn=ChatTurnPayload(who="owner", when=when_label, text=req.message),
        agentTurn=ChatTurnPayload(who="agent", when=when_label, text=agent_text),
    )
