"""Chat router — Phase C.2 streaming + Phase C.3 multi-step tool use.

POST /api/chat/turn  — owner sends a message, server returns an SSE stream
                      that emits text deltas as Claude generates them, tool
                      events as they fire, and a final `done` event with the
                      persisted ownerTurn + agentTurn.

SSE event shapes:
  event: delta        data: {"text": "..."}            text token arrived
  event: tool_start   data: {"name": "draft_post"}     model decided to fire a tool
  event: tool_result  data: {"attachment": {...}}      tool executed, card payload
  event: done         data: {ok, ownerTurn, agentTurn} final, both turns persisted
  event: error        data: {"detail": "..."}          something went wrong

Multi-step loop coordinates one Anthropic stream per iteration. Tools execute
between streams. The final `done` event lands once stop_reason="end_turn" or
MAX_TOOL_ITERATIONS is hit.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Generator, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.system_prompt import build_system_prompt
from ..agent.tools import MAX_TOOL_ITERATIONS, TOOL_SCHEMAS, execute_tool
from ..auth.deps import get_tenant_id
from ..db import get_db
from ..models import ChatTurn

log = logging.getLogger("popular_network.chat")
router = APIRouter()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_LIVE_CONVERSATION_ID = "seed"  # live turns continue the seeded thread


class ChatTurnRequest(BaseModel):
    # business_id was previously a body field; Phase H.1 moved it to a
    # route-level dependency (business_id → Depends(get_tenant_id)) so
    # the active tenant can't be spoofed by the request body.
    message: str = Field(min_length=1, max_length=4000)


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


def _format_when(dt: datetime) -> str:
    """Match the human-readable timestamps used in the seeded thread."""
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"Today, {hour}:{dt.minute:02d}{suffix}"


def _turn_to_message(turn: ChatTurn) -> dict[str, str]:
    role = "user" if turn.who == "owner" else "assistant"
    return {"role": role, "content": turn.text}


def _content_blocks_to_dicts(content_blocks: list[Any]) -> list[dict[str, Any]]:
    """Round-trip the assistant's content blocks back to dicts for the next call."""
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
    return out


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE event. data is JSON-encoded on a single line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/turn")
def take_turn(
    req: ChatTurnRequest,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream the chat turn. Validation errors (422) and missing API key (503)
    are raised before the generator starts so they surface as proper HTTP
    statuses rather than SSE errors.
    """
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
        .filter(ChatTurn.business_id == business_id)
        .order_by(ChatTurn.id.asc())
        .all()
    )
    messages: list[dict[str, Any]] = [_turn_to_message(t) for t in history]
    messages.append({"role": "user", "content": req.message})

    system_text = build_system_prompt(db, business_id)
    system_blocks = [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    forced_tool = _detect_forced_tool(req.message)

    return StreamingResponse(
        _event_generator(
            api_key=api_key,
            db=db,
            business_id=business_id,
            owner_message=req.message,
            messages=messages,
            system_blocks=system_blocks,
            forced_tool=forced_tool,
        ),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering so chunks reach the browser immediately.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


def _event_generator(
    *,
    api_key: str,
    db: Session,
    business_id: int,
    owner_message: str,
    messages: list[dict[str, Any]],
    system_blocks: list[dict[str, Any]],
    forced_tool: Optional[str],
) -> Generator[str, None, None]:
    client = Anthropic(api_key=api_key)
    full_text_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_create = 0
    total_cache_read = 0

    try:
        iterations = 0
        while True:
            if iterations >= MAX_TOOL_ITERATIONS:
                log.warning(
                    f"chat.turn business={business_id} hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS}"
                )
                if not full_text_parts:
                    cap_msg = (
                        "I ran into my tool-call limit before finishing — try asking "
                        "again in a simpler shape and I'll pick it up."
                    )
                    yield _sse("delta", {"text": cap_msg})
                    full_text_parts.append(cap_msg)
                break

            if iterations == 0 and forced_tool is not None:
                tool_choice: dict[str, Any] = {"type": "tool", "name": forced_tool}
                log.info(
                    f"chat.turn business={business_id} forcing tool_choice={forced_tool!r}"
                )
            else:
                tool_choice = {"type": "auto"}

            iter_text = ""
            with client.messages.stream(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_blocks,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
            ) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block is not None and getattr(block, "type", None) == "tool_use":
                            yield _sse("tool_start", {"name": block.name})
                    elif etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None) if delta else None
                        if dtype == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                iter_text += text
                                yield _sse("delta", {"text": text})
                        # input_json_delta chunks are accumulated by the SDK
                        # internally; we read the final input from get_final_message().

                final_msg = stream.get_final_message()

            usage = getattr(final_msg, "usage", None)
            if usage is not None:
                total_input_tokens += getattr(usage, "input_tokens", 0) or 0
                total_output_tokens += getattr(usage, "output_tokens", 0) or 0
                total_cache_create += getattr(usage, "cache_creation_input_tokens", 0) or 0
                total_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0

            if iter_text:
                full_text_parts.append(iter_text)

            if final_msg.stop_reason != "tool_use":
                break

            # Execute every tool_use block in this assistant response, emit
            # tool_result events, and collect tool_result blocks for the next
            # user message.
            assistant_dict_blocks = _content_blocks_to_dicts(final_msg.content)
            tool_result_blocks: list[dict[str, Any]] = []

            for block in final_msg.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_name = block.name
                tool_input = block.input or {}
                log.info(
                    f"chat.turn business={business_id} tool={tool_name} input_keys={list(tool_input)}"
                )
                result = execute_tool(tool_name, db, business_id, tool_input)
                attachments.append(result.attachment)
                yield _sse("tool_result", {"attachment": result.attachment})
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result.text,
                        **({"is_error": True} if result.is_error else {}),
                    }
                )

            messages.append({"role": "assistant", "content": assistant_dict_blocks})
            messages.append({"role": "user", "content": tool_result_blocks})

            iterations += 1

        log.info(
            f"chat.turn business={business_id} iterations={iterations} "
            f"tools={len(attachments)} cache_create={total_cache_create} "
            f"cache_read={total_cache_read} input={total_input_tokens} "
            f"output={total_output_tokens}"
        )

        agent_text = "\n\n".join(p for p in full_text_parts if p).strip()
        if not agent_text and not attachments:
            yield _sse("error", {"detail": "Claude returned an empty response."})
            return
        if not agent_text:
            agent_text = "Done. See the card below."

        attachment_payload: Optional[dict[str, Any]] = (
            {"items": attachments} if attachments else None
        )

        now = datetime.utcnow()
        when_label = _format_when(now)

        owner_turn = ChatTurn(
            business_id=business_id,
            conversation_id=_LIVE_CONVERSATION_ID,
            who="owner",
            when_label=when_label,
            text=owner_message,
            attachment_json=None,
        )
        agent_turn = ChatTurn(
            business_id=business_id,
            conversation_id=_LIVE_CONVERSATION_ID,
            who="agent",
            when_label=when_label,
            text=agent_text,
            attachment_json=attachment_payload,
        )
        db.add(owner_turn)
        db.add(agent_turn)
        db.commit()

        yield _sse(
            "done",
            {
                "ok": True,
                "ownerTurn": {
                    "who": "owner",
                    "when": when_label,
                    "text": owner_message,
                },
                "agentTurn": {
                    "who": "agent",
                    "when": when_label,
                    "text": agent_text,
                    "attachment": attachment_payload,
                },
            },
        )
    except Exception as exc:
        log.exception("Chat stream failed")
        yield _sse("error", {"detail": f"Claude call failed: {exc}"})
