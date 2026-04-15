"""Structured retrieval decision logging (Phase 6c).

Every user turn writes one JSON line to data/retrieval_decisions.jsonl capturing:
    - what the router decided
    - what retrievers returned
    - whether the entity gate fired
    - what the LLM eventually answered (token count only — no PII in the log)
    - latency budget breakdown

This file is the single source of truth for answering "why did the bot say X?"
after-the-fact. It's what lets us diagnose a regression on Tuesday that a user
reported from Monday, without needing to reproduce their session.

Design choices:
    - JSONL (one object per line), not DB rows. Appends are O(1), no schema
      migrations, grep-friendly. We'll sample into SQLite at the 10K+ turn
      scale if query patterns demand it.
    - Best-effort writes. A logging failure never blocks the user response.
    - Log the chunk doc_id/title/score/rrf/rerank — NOT the chunk body. The
      log file should be shareable in an incident ticket without worrying
      about PII or copyright.
    - Store query verbatim (short) — that IS the signal for pattern-mining.
      Retention policy (30d? 90d?) is a separate concern.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Resolve path relative to repo root
_LOG_PATH = Path(__file__).resolve().parents[3] / "data" / "retrieval_decisions.jsonl"


def _chunk_summary(chunk: dict) -> dict:
    """Extract the log-safe fields from a chunk (no full body text)."""
    md = chunk.get("metadata", {}) or {}
    return {
        "doc_id": str(md.get("doc_id", "") or ""),
        "title": str(md.get("title", "") or "")[:80],
        "publisher": str(md.get("publisher", "") or ""),
        "edition_id": str(md.get("edition_id", "") or ""),
        "score": round(float(chunk.get("score", 0.0) or 0.0), 4),
        "rrf_score": round(float(chunk.get("rrf_score", 0.0) or 0.0), 4)
            if "rrf_score" in chunk else None,
        "dense_rank": chunk.get("dense_rank"),
        "lex_rank": chunk.get("lex_rank"),
        "rerank_score": round(float(chunk.get("rerank_score", 0.0) or 0.0), 4)
            if "rerank_score" in chunk else None,
    }


def log_retrieval_decision(
    *,
    conversation_id: int | None,
    query: str,
    publisher: str | None,
    intent: str,
    intent_reason: str,
    current_edition_only: bool,
    entity_gate: dict[str, Any],
    chunks: list[dict],
    abstained: bool,
    abstain_reason: str | None,
    latency_ms: int,
    response_preview: str | None = None,
    grounding_audit: dict[str, Any] | None = None,
) -> None:
    """Append one decision row to data/retrieval_decisions.jsonl. Best-effort.

    Never raises — a logging failure should not break the chat path.
    """
    try:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "conversation_id": conversation_id,
            "query": (query or "")[:500],
            "publisher": publisher,
            "intent": intent,
            "intent_reason": intent_reason,
            "current_edition_only": current_edition_only,
            "entity_gate": entity_gate,
            "n_chunks": len(chunks),
            "chunks": [_chunk_summary(c) for c in chunks[:10]],
            "abstained": abstained,
            "abstain_reason": abstain_reason,
            "latency_ms": latency_ms,
            "response_preview": (response_preview or "")[:200] if response_preview else None,
            "grounding_audit": grounding_audit,
        }
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:
        # Don't use logger.warning at WARNING since this can be noisy if the
        # filesystem is read-only (which shouldn't happen in our deploys, but
        # defense-in-depth). Logger at INFO: we want to see this in CI.
        logger.info(f"[decision-log] write failed (non-fatal): {e}")
