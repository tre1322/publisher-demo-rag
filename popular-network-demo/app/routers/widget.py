"""Public chatbot widget endpoint — Phase H.2.2.

POST /api/widget/chat — anonymous, called from the publisher's website by the
embedded widget bundle (static/widget.js). Returns Claude's reply and persists
the running conversation to ChatbotConversation so the owner's ChatbotPreview
dashboard tab can show what consumers are asking.

Rate limiting (two layers):
  1. Per-(IP, business) per-hour:  WIDGET_MAX_PER_IP_PER_HOUR=50 — prevents
     a single visitor from burning a business's Claude budget.
  2. Per-business per-day token ceiling:  WIDGET_DAILY_TOKEN_CAP=100_000 —
     ~$0.50 in Sonnet input cost. Caps blast radius if a publisher's site
     gets scraped or LLM-abuser'd.

Both buckets are in-process dicts for the demo. Swap to Redis when you have
more than one app instance — until then, single uvicorn worker keeps it
honest.

CORS:
  - Business.allowed_origins_json null/empty → wildcard (* / no Origin check)
  - Populated list → enforced on preflight (OPTIONS) and actual request
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Any, Optional

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.system_prompt import build_system_prompt
from ..chatbot_extract import detect_escalation, extract_topic_label, score_sentiment
from ..db import get_db
from ..models import Business, ChatbotConversation

log = logging.getLogger("popular_network.widget")
router = APIRouter(prefix="/widget")

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 600  # widget replies should be conversational, not essays
WIDGET_MAX_PER_IP_PER_HOUR = 50
WIDGET_DAILY_TOKEN_CAP = 100_000

# ---- in-process rate-limit state ----
# IP throttle: dict[(ip, business_id) -> deque[unix_ts]]
_IP_HITS: dict[tuple[str, int], deque[float]] = defaultdict(deque)
# Token cap: dict[business_id -> (date_iso, tokens_used)]
_BIZ_TOKENS: dict[int, tuple[str, int]] = {}


def _ip_throttle(ip: str, biz_id: int) -> bool:
    """Returns True if request is allowed (under cap)."""
    key = (ip, biz_id)
    bucket = _IP_HITS[key]
    now = time.time()
    cutoff = now - 3600
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= WIDGET_MAX_PER_IP_PER_HOUR:
        return False
    bucket.append(now)
    return True


def _under_token_cap(biz_id: int) -> bool:
    today = date.today().isoformat()
    rec = _BIZ_TOKENS.get(biz_id)
    if rec is None or rec[0] != today:
        return True
    return rec[1] < WIDGET_DAILY_TOKEN_CAP


def _record_tokens(biz_id: int, tokens: int) -> None:
    today = date.today().isoformat()
    rec = _BIZ_TOKENS.get(biz_id)
    if rec is None or rec[0] != today:
        _BIZ_TOKENS[biz_id] = (today, tokens)
    else:
        _BIZ_TOKENS[biz_id] = (today, rec[1] + tokens)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


# ---- CORS helpers ----
def _origin_allowed(business: Business, request_origin: Optional[str]) -> Optional[str]:
    """Return the value to set in Access-Control-Allow-Origin, or None to deny.

    - If business has no allowed_origins_json (null/empty) → echo back the
      Origin (effectively wildcard, but with credentials-friendly echo).
    - If business has a list → exact match required.
    """
    allow_list = business.allowed_origins_json or []
    if not allow_list:
        return request_origin or "*"
    if request_origin and request_origin in allow_list:
        return request_origin
    return None


def _apply_cors_headers(response: Response, allow_value: Optional[str]) -> None:
    if allow_value is None:
        return
    response.headers["Access-Control-Allow-Origin"] = allow_value
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "3600"
    response.headers["Vary"] = "Origin"


# ---- schemas ----
class ChatBody(BaseModel):
    business_id: int
    session_id: Optional[str] = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    referrer: Optional[str] = Field(default=None, max_length=200)


# ---- endpoints ----
@router.options("/chat")
def widget_chat_preflight(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Response:
    """CORS preflight. business_id isn't in the body for OPTIONS, so we
    look it up via the X-Business-Id header that the widget sets, or fall
    back to allow-any if the header is absent (browsers may not send it
    on preflight depending on implementation)."""
    biz_id_str = request.headers.get("x-business-id")
    biz_id: Optional[int] = None
    try:
        if biz_id_str:
            biz_id = int(biz_id_str)
    except ValueError:
        biz_id = None

    request_origin = request.headers.get("origin")
    allow_value: Optional[str] = request_origin or "*"
    if biz_id is not None:
        biz = db.get(Business, biz_id)
        if biz is not None:
            allow_value = _origin_allowed(biz, request_origin)

    _apply_cors_headers(response, allow_value)
    return Response(status_code=204, headers=dict(response.headers))


@router.post("/chat")
def widget_chat(
    body: ChatBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    biz = db.get(Business, body.business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail="business_not_found")

    # CORS check (after biz lookup so we can apply per-business policy).
    request_origin = request.headers.get("origin")
    allow_value = _origin_allowed(biz, request_origin)
    if request_origin and allow_value is None:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    _apply_cors_headers(response, allow_value)

    # Rate limits.
    ip = _client_ip(request)
    if not _ip_throttle(ip, biz.id):
        raise HTTPException(status_code=429, detail="too_many_messages_from_this_ip")
    if not _under_token_cap(biz.id):
        raise HTTPException(status_code=429, detail="daily_token_cap_reached")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    # Resolve or create the running conversation.
    session_id = body.session_id or f"widget_{uuid.uuid4().hex[:16]}"
    convo = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.business_id == biz.id,
            ChatbotConversation.external_id == session_id,
        )
        .first()
    )
    if convo is None:
        convo = ChatbotConversation(
            business_id=biz.id,
            external_id=session_id,
            consumer_label=f"anonymous · {ip}",
            topic_label="(in progress)",
            transcript_json=[],
            turn_count=0,
            duration_seconds=0,
            referrer_label=(body.referrer or "")[:80] or None,
            started_at=datetime.utcnow(),
            source="ingested",
        )
        db.add(convo)
        db.flush()

    transcript = list(convo.transcript_json or [])
    transcript.append({"who": "consumer", "text": body.message, "at": datetime.utcnow().isoformat()})

    # Build the Anthropic messages from the running transcript.
    messages = []
    for turn in transcript:
        role = "user" if turn["who"] == "consumer" else "assistant"
        messages.append({"role": role, "content": turn["text"]})

    system_text = build_system_prompt(db, biz.id)
    client = Anthropic()
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
    except Exception as exc:
        log.exception("widget chat call failed for business=%s session=%s", biz.id, session_id)
        raise HTTPException(status_code=502, detail=f"upstream_error: {exc}") from exc

    bot_text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            bot_text += block.text
    if not bot_text:
        bot_text = "Sorry — I couldn't form a response. Please try again."

    transcript.append({"who": "bot", "text": bot_text, "at": datetime.utcnow().isoformat()})

    # Persist conversation state.
    convo.transcript_json = transcript
    convo.turn_count = len(transcript)
    convo.ended_at = datetime.utcnow()
    convo.duration_seconds = int((convo.ended_at - convo.started_at).total_seconds())
    convo.topic_label = extract_topic_label(transcript) or convo.topic_label
    convo.sentiment = score_sentiment(transcript)
    esc_flag, esc_reason = detect_escalation(transcript)
    if esc_flag:
        convo.escalation_flag = True
        convo.escalation_reason = esc_reason

    db.commit()

    # Record tokens.
    usage = getattr(resp, "usage", None)
    tokens = (getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)) if usage else 0
    if tokens:
        _record_tokens(biz.id, tokens)

    return {
        "sessionId": session_id,
        "reply": bot_text,
        "transcript": transcript,
        "tokenCount": tokens,
    }
