"""Chat router — Phase C.3 multi-step tool use.

POST /api/chat/turn  — owner sends a message, server runs the multi-step
                      Anthropic tool_use loop (capped at MAX_TOOL_ITERATIONS),
                      persists owner + final-agent turns (with all tool
                      attachments inlined on the agent turn), and returns
                      both in a single response.

Loop shape (chosen 2026-05-26):
  - Pass TOOL_SCHEMAS on every call.
  - While stop_reason == "tool_use" and iterations < cap:
      execute each tool block → append assistant+user-tool_result pair → re-call
  - When stop_reason == "end_turn" (or cap hit): collect final text + all
    attachments accumulated across iterations.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.system_prompt import build_system_prompt
from ..agent.tools import MAX_TOOL_ITERATIONS, TOOL_SCHEMAS, execute_tool
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


def _extract_text(content_blocks: list[Any]) -> str:
    """Pull all text blocks out of a Claude response and concatenate."""
    parts = [b.text for b in content_blocks if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


# Trigger phrases that force tool_choice={"type":"tool","name":"draft_post"}
# on the first model call. Discovered during 2026-05-26 live tuning that
# Sonnet 4.6 is RLHF-conservative on this tool and drafts inline by default,
# even with explicit system-prompt directives. Routing bypasses the model's
# "should I use this tool?" decision when the user message is unambiguous.
_DRAFT_POST_TRIGGER_RE = re.compile(
    r"\b(?:"
    r"draft (?:me )?(?:a |an |another |one |us )"
    r"|write (?:me )?(?:a |an )"
    r"|give me a (?:draft|post)"
    r"|compose (?:me )?(?:a |an )"
    r"|put together a (?:draft|post)"
    r")",
    re.IGNORECASE,
)


def _detect_forced_tool(message: str) -> Optional[str]:
    """Return a tool name to force via tool_choice, or None for auto."""
    if _DRAFT_POST_TRIGGER_RE.search(message):
        return "draft_post"
    return None


def _content_blocks_to_dicts(content_blocks: list[Any]) -> list[dict[str, Any]]:
    """Round-trip the assistant's content blocks back to dicts for the next call.

    The Anthropic SDK returns typed objects; the next messages.create needs them
    as JSON-shaped dicts so we can append the tool_result user message.
    """
    out: list[dict[str, Any]] = []
    for b in content_blocks:
        kind = getattr(b, "type", None)
        if kind == "text":
            out.append({"type": "text", "text": b.text})
        elif kind == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                }
            )
        # ignore unknown block types — model may add new ones over time
    return out


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
    messages: list[dict[str, Any]] = [_turn_to_message(t) for t in history]
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

    # Force a specific tool on iteration 0 when the user message has a clear
    # trigger phrase. Subsequent iterations stay tool_choice="auto" so the
    # model can stop naturally with end_turn after the tool_result.
    forced_tool = _detect_forced_tool(req.message)

    # --- multi-step loop --------------------------------------------------- #
    final_text_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_create = 0
    total_cache_read = 0

    iterations = 0
    while True:
        if iterations >= MAX_TOOL_ITERATIONS:
            # Defensive: cap reached. Treat as final, surface a note.
            log.warning(
                f"chat.turn business={req.business_id} hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS}"
            )
            if not final_text_parts:
                final_text_parts.append(
                    "I ran into my tool-call limit before finishing — try asking again "
                    "in a simpler shape and I'll pick it up."
                )
            break

        # Force the tool only on the first call. After tool_result comes back,
        # let the model decide whether to fire another tool or end_turn.
        if iterations == 0 and forced_tool is not None:
            tool_choice: dict[str, Any] = {"type": "tool", "name": forced_tool}
            log.info(
                f"chat.turn business={req.business_id} forcing tool_choice={forced_tool!r}"
            )
        else:
            tool_choice = {"type": "auto"}

        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_blocks,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
            )
        except Exception as exc:
            log.exception("Anthropic call failed")
            raise HTTPException(status_code=502, detail=f"Claude call failed: {exc}") from exc

        total_input_tokens += resp.usage.input_tokens
        total_output_tokens += resp.usage.output_tokens
        total_cache_create += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        total_cache_read += getattr(resp.usage, "cache_read_input_tokens", 0) or 0

        # Collect any text the model emitted in this turn — it may be present
        # alongside tool_use blocks (model often says "Let me draft that…" then
        # emits tool_use).
        step_text = _extract_text(resp.content)
        if step_text:
            final_text_parts.append(step_text)

        if resp.stop_reason != "tool_use":
            break

        # Execute every tool_use block in this assistant response, collect
        # tool_result blocks for the next user message.
        assistant_dict_blocks = _content_blocks_to_dicts(resp.content)
        tool_result_blocks: list[dict[str, Any]] = []

        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_name = block.name
            tool_input = block.input or {}
            log.info(
                f"chat.turn business={req.business_id} tool={tool_name} input_keys={list(tool_input)}"
            )
            result = execute_tool(tool_name, db, req.business_id, tool_input)
            attachments.append(result.attachment)
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result.text,
                    **({"is_error": True} if result.is_error else {}),
                }
            )

        # Append the assistant's tool-use turn and the synthetic user
        # tool_result turn so the next iteration sees them.
        messages.append({"role": "assistant", "content": assistant_dict_blocks})
        messages.append({"role": "user", "content": tool_result_blocks})

        iterations += 1

    log.info(
        f"chat.turn business={req.business_id} iterations={iterations} "
        f"tools={len(attachments)} cache_create={total_cache_create} "
        f"cache_read={total_cache_read} input={total_input_tokens} output={total_output_tokens}"
    )

    agent_text = "\n\n".join(p for p in final_text_parts if p).strip()
    if not agent_text and not attachments:
        raise HTTPException(status_code=502, detail="Claude returned an empty response.")
    if not agent_text:
        # Tools fired but no text — surface a minimal acknowledgement.
        agent_text = "Done. See the card below."

    # Single attachment dict per ChatTurn (schema is one JSON blob). For
    # multi-tool turns wrap in {"items": [...]}; legacy seeded boost-card uses
    # the singular shape {"kind": "boost-card", ...}.
    if len(attachments) == 0:
        attachment_payload: Optional[dict[str, Any]] = None
    elif len(attachments) == 1:
        attachment_payload = {"items": attachments}  # uniform shape for new turns
    else:
        attachment_payload = {"items": attachments}

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
        attachment_json=attachment_payload,
    )
    db.add(owner_turn)
    db.add(agent_turn)
    db.commit()

    return ChatTurnResponse(
        ok=True,
        ownerTurn=ChatTurnPayload(who="owner", when=when_label, text=req.message),
        agentTurn=ChatTurnPayload(
            who="agent", when=when_label, text=agent_text, attachment=attachment_payload
        ),
    )
