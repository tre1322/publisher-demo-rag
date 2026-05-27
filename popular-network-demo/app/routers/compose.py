"""Compose router — Re-draft from brief.

POST /api/compose/redraft  — one-shot LLM call that regenerates per-platform
                             drafts given the current Compose form state. The
                             voice brief flows through build_system_prompt(),
                             so AMPLIFY/MAINTAIN/MUTE rules + the founder's
                             voice are honored on every regen.

Structured output is forced via a single `compose_drafts` tool with strict
input_schema + tool_choice={"type":"tool","name":"compose_drafts"}. This is the
same Sonnet 4.6 reliability trick the chat router uses — see chat.py for the
detailed RLHF / tool-routing context.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.system_prompt import build_system_prompt
from ..db import get_db

log = logging.getLogger("popular_network.compose")
router = APIRouter()

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2048

# Platform-key whitelist matching the frontend's `chosen` array.
_VALID_PLATFORMS = {"fb", "ig", "gbp", "web"}


class RedraftRequest(BaseModel):
    business_id: int = 1
    topic: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=2000)
    tone: str = Field(default="friendly", max_length=40)
    image_mode: str = Field(default="owner", max_length=40)
    platforms: list[str] = Field(default_factory=lambda: ["fb", "ig", "gbp", "web"])


# Tool schema — Claude returns one object per requested platform. Optional
# fields per platform match the dashboard's `drafts` state shape exactly so the
# frontend can spread the response straight into setDrafts().
_COMPOSE_DRAFTS_TOOL = {
    "name": "compose_drafts",
    "description": (
        "Return a set of platform-specific draft fields based on the supplied "
        "topic, notes, tone, and the business's voice brief. Populate only the "
        "platforms the owner asked for. Match the owner's established voice "
        "and honor every AMPLIFY/MAINTAIN/MUTE rule in the brief."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fb": {
                "type": "object",
                "description": "Facebook post draft. Optimal length ~280 chars.",
                "properties": {
                    "text": {"type": "string"},
                    "cta": {"type": "string", "description": "Short call-to-action label, e.g. 'Learn more', 'Start free trial'."},
                },
                "required": ["text", "cta"],
            },
            "ig": {
                "type": "object",
                "description": "Instagram caption + hashtags. Caption optimal ~150 chars.",
                "properties": {
                    "caption": {"type": "string"},
                    "hashtags": {"type": "string", "description": "Space-separated hashtags starting with #."},
                },
                "required": ["caption", "hashtags"],
            },
            "gbp": {
                "type": "object",
                "description": "Google Business Profile update. Max ~1500 chars on body.",
                "properties": {
                    "headline": {"type": "string"},
                    "text": {"type": "string"},
                    "cta": {"type": "string"},
                },
                "required": ["headline", "text", "cta"],
            },
            "web": {
                "type": "object",
                "description": "Website / blog-style long-form post.",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "body": {"type": "string", "description": "3-6 short paragraphs."},
                    "slug": {"type": "string", "description": "URL slug starting with '/blog/'."},
                    "readTime": {"type": "string", "description": "Short read-time hint like '3 min'."},
                },
                "required": ["title", "subtitle", "body", "slug", "readTime"],
            },
        },
    },
}


def _build_redraft_message(req: RedraftRequest) -> str:
    """Compose the user-message brief the model receives."""
    parts = [
        f"Re-draft the Compose surface for these platforms: {', '.join(req.platforms)}.",
        f"Topic: {req.topic}",
    ]
    if req.notes.strip():
        parts.append(f"Owner notes:\n{req.notes.strip()}")
    parts.append(f"Tone: {req.tone}")
    parts.append(f"Image strategy: {req.image_mode}")
    parts.append(
        "Honor the voice brief above — AMPLIFY items lead, MAINTAIN items support, "
        "MUTE items stay out entirely. Use customer language verbatim where it fits. "
        "Return ONLY the platforms requested via the compose_drafts tool."
    )
    return "\n\n".join(parts)


@router.post("/compose/redraft")
def redraft(req: RedraftRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    # Validate platforms early — the frontend's `chosen` array is the source.
    bad = [p for p in req.platforms if p not in _VALID_PLATFORMS]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown platform(s): {bad}")
    if not req.platforms:
        raise HTTPException(status_code=422, detail="platforms must be non-empty")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    system_text = build_system_prompt(db, req.business_id)
    user_message = _build_redraft_message(req)

    client = Anthropic()
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            tools=[_COMPOSE_DRAFTS_TOOL],
            tool_choice={"type": "tool", "name": "compose_drafts"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        log.exception("compose redraft failed")
        raise HTTPException(status_code=502, detail=f"model call failed: {e}")

    # Extract the forced tool input from the response content.
    tool_input: Optional[dict[str, Any]] = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "compose_drafts":
            tool_input = dict(block.input) if isinstance(block.input, dict) else json.loads(block.input)
            break
    if tool_input is None:
        raise HTTPException(status_code=502, detail="model did not return compose_drafts tool_use block")

    # Drop any platforms the model returned that the owner didn't ask for.
    drafts = {k: v for k, v in tool_input.items() if k in req.platforms}

    return {"ok": True, "drafts": drafts}
