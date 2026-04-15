"""Chat interface routes with streaming support."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from src.modules.conversations import (
    get_conversation,
    get_conversation_messages,
    insert_conversation,
    insert_message,
)
from src.prompts import ensure_sponsored_disclosure
from src.query_engine import QueryEngine
from src.query_router import classify as classify_intent

logger = logging.getLogger(__name__)

# Setup templates
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Create router
router = APIRouter(prefix="/chat", tags=["chat"])

# Lazy-loaded query engine
_engine: QueryEngine | None = None


def get_engine() -> QueryEngine:
    """Get or create the query engine singleton."""
    global _engine
    if _engine is None:
        _engine = QueryEngine()
    return _engine


@router.get("", response_class=HTMLResponse)
async def chat_page(request: Request) -> HTMLResponse:
    """Render the chat page."""
    return templates.TemplateResponse(request=request, name="chat.html")


@router.get("/history")
async def get_history(session_id: str) -> dict:
    """Get conversation history for a session.

    Args:
        session_id: The session identifier.

    Returns:
        Dictionary with messages list.
    """
    try:
        conversation = get_conversation(session_id)
        if not conversation:
            return {"messages": []}

        messages = get_conversation_messages(conversation["id"])
    except Exception as e:
        logger.error(f"Error loading history: {e}")
        return {"messages": []}
    # Return only role and content for frontend
    return {
        "messages": [
            {"role": msg["role"], "content": msg["content"]} for msg in messages
        ]
    }


@router.get("/stream")
async def stream_response(
    message: str, session_id: str | None = None, publisher: str | None = None
) -> StreamingResponse:
    """Stream the assistant response.

    Args:
        message: The user's message (URL encoded).
        session_id: Optional session identifier for conversation tracking.

    Returns:
        Streaming response with JSON lines.
    """
    engine = get_engine()

    # Get or create conversation for this session
    conversation_id: int | None = None
    if session_id:
        try:
            conversation = get_conversation(session_id)
            if conversation:
                conversation_id = int(conversation["id"])
            else:
                conversation_id = insert_conversation(session_id, publisher=publisher)
                logger.info(
                    f"Created new conversation for session: {session_id} (publisher: {publisher})"
                )

            # Log user message
            insert_message(conversation_id, "user", message)
        except Exception as e:
            logger.error(f"Conversation tracking error: {e}")
            conversation_id = None

    def generate() -> Iterator[bytes]:
        # Check if engine is ready (articles, ads, or legacy content)
        if not engine.is_ready():
            logger.warning("No content available — articles and ads collections empty")
            yield b'{"type": "error", "content": "No documents indexed."}\n'
            return

        import time as _time
        turn_start = _time.perf_counter()
        # Collected during the turn and flushed once in finally. Kept in a
        # mutable dict so the nested try/except blocks below can annotate it.
        log_state: dict = {
            "route": None,
            "chunks": [],
            "entity_gate": {"ok": True, "missing": None},
            "abstained": False,
            "abstain_reason": None,
            "grounding_audit": None,
        }

        accumulated = ""
        try:
            # Send searching status
            yield b'{"type": "status", "content": "Searching..."}\n'

            # v2 Phase 2c (2026-04-14): intent router. Classifies the query
            # into a small set of intents (article_qa / business_lookup /
            # event_lookup / current_edition / out_of_scope) and decides
            # WHICH corpora to retrieve from. This replaces the v1 "merge
            # everything into one evidence bag" pattern that let the LLM
            # stitch ads+events+articles into confabulations. See
            # src/query_router.py for the full intent contract.
            route = classify_intent(message)
            log_state["route"] = route
            logger.info(
                f"[router] intent={route.intent} reason={route.reason!r} "
                f"lanes=(articles={route.use_articles}, "
                f"ads_dir={route.use_ads_directory}, "
                f"events={route.use_events}, "
                f"sponsored={route.use_sponsored}, "
                f"current_only={route.current_edition_only})"
            )

            # Out-of-scope short-circuit BEFORE retrieval. The bot has nothing
            # useful to say about weather/stock prices/recipes, and the LLM's
            # general knowledge is the MOST dangerous thing here.
            if route.intent == "out_of_scope" and route.abstain_message:
                yield b'{"type": "status", "content": "Checking..."}\n'
                accumulated = route.abstain_message
                data = json.dumps({"type": "token", "content": route.abstain_message})
                yield f"{data}\n".encode()
                if conversation_id:
                    insert_message(conversation_id, "assistant", accumulated)
                log_state["abstained"] = True
                log_state["abstain_reason"] = f"intent=out_of_scope ({route.reason})"
                yield b'{"type": "done"}\n'
                return

            # v2 Phase 1b+1c (2026-04-14): strict publisher default.
            #
            # Removed:
            #   (1) cross_ref_keywords auto-expansion — a 30-word list that
            #       silently widened scope whenever a user's message happened
            #       to contain "network"/"regional"/a town name. Users at `/`
            #       would get Pipestone content blended in without any visible
            #       signal that scope had shifted.
            #   (2) unconditional secondary publisher=None retrieval — every
            #       turn ran a second cross-network query and stapled its
            #       results (at 0.6x score) onto the primary chunks. This is
            #       what was leaking "wrestler from another town" chunks into
            #       the evidence bag for Koerner-style entity queries.
            #   (3) parallel secondary ads/directory search — same pattern.
            #
            # Cross-publisher expansion will return in Phase 4 as a Claude
            # tool call (`search_grand_network`), triggered only when the LLM
            # decides local results are insufficient AND the user has signaled
            # intent. That is deterministic, auditable, and language-agnostic
            # — none of which the keyword heuristic was.
            chunks: list[dict] = []
            if route.use_articles:
                chunks = engine.retrieve(
                    message,
                    publisher=publisher,
                    current_edition_only=route.current_edition_only,
                )

            # Router-gated supplemental corpora. Each lane only runs when the
            # router says the intent calls for it.
            try:
                from src.modules.advertisements.search import AdvertisementSearch
                from src.modules.events.search import EventSearch
                from src.search_tools import SearchTools

                if route.use_ads_directory:
                    ad_results = AdvertisementSearch().search(message, publisher=publisher)
                    chunks.extend(ad_results)
                    dir_results = SearchTools().search_directory(
                        query=message, publisher=publisher
                    )
                    chunks.extend(dir_results)

                if route.use_events:
                    # Events are network-wide by design (a calendar covers the
                    # whole region), so no publisher filter here.
                    event_results = EventSearch().search(message)
                    chunks.extend(event_results)

                # Main Street OS: sponsored answers are the load-bearing
                # revenue loop. They fire on every intent EXCEPT out_of_scope
                # (which has already returned above). Router controls this via
                # route.use_sponsored — currently true for every in-scope intent.
                if route.use_sponsored:
                    try:
                        sponsored_results = SearchTools().search_sponsored_answers(
                            query=message
                        )
                        if sponsored_results:
                            logger.info(
                                f"Surfaced {len(sponsored_results)} sponsored "
                                f"answer(s) for query: {message!r}"
                            )
                            chunks.extend(sponsored_results)
                    except Exception as sp_err:
                        logger.warning(f"Sponsored search error: {sp_err}")
            except Exception as sup_err:
                logger.warning(f"Supplemental search error: {sup_err}")

            # Re-sort all results by score (publisher's own content first)
            chunks.sort(key=lambda c: c.get("score", 0), reverse=True)

            # v2 Phase 2a: entity coverage gate. If the user's query contains a
            # specific proper noun (e.g. "Koerner") and NO retrieved chunk
            # contains that token, short-circuit with a canned abstention and
            # never call the LLM. This is the structural guard against the
            # "wrong wrestler from another edition" failure mode: Claude can
            # only confabulate from chunks we hand it, so we don't hand it
            # chunks that can't support the question in the first place.
            from src.modules.articles.grounding import (
                has_entity_coverage,
                abstention_message,
            )
            ok, missing = has_entity_coverage(message, chunks)
            log_state["entity_gate"] = {"ok": ok, "missing": missing}
            log_state["chunks"] = chunks
            if not ok and missing is not None:
                logger.info(
                    f"Entity gate FIRED: token={missing!r} not found in any "
                    f"of {len(chunks)} retrieved chunks. Skipping LLM call."
                )
                yield b'{"type": "status", "content": "Checking..."}\n'
                canned = abstention_message(missing, publisher)
                accumulated = canned
                data = json.dumps({"type": "token", "content": canned})
                yield f"{data}\n".encode()
                if conversation_id:
                    insert_message(conversation_id, "assistant", accumulated)
                log_state["abstained"] = True
                log_state["abstain_reason"] = f"entity_gate: {missing!r} not in any chunk"
                yield b'{"type": "done"}\n'
                return

            # Send thinking status
            yield b'{"type": "status", "content": "Thinking..."}\n'

            # Set publisher context for dynamic system prompt
            engine._current_publisher = publisher

            # Stream response tokens
            for token in engine.generate_response_streaming(message, chunks):
                accumulated += token
                data = json.dumps({"type": "token", "content": token})
                yield f"{data}\n".encode()

            # v2 Phase 2b: post-generation grounding audit. Reports which
            # proper nouns in the LLM's answer do NOT appear in any retrieved
            # chunk. Runs in observability mode — does NOT modify the response
            # to avoid false-positive stripping of legitimate inferences. The
            # log lets us diagnose after-the-fact whether the LLM drifted.
            try:
                from src.modules.articles.grounding import validate_response_grounding
                audit = validate_response_grounding(accumulated, chunks)
                log_state["grounding_audit"] = audit
                if not audit["ok"]:
                    logger.warning(
                        f"[grounding-audit] unverified proper nouns in response: "
                        f"{audit['unverified']} (response had "
                        f"{len(audit['response_nouns'])} nouns; chunks covered "
                        f"{len(audit['chunk_nouns'])} distinct nouns). "
                        f"query={message!r}"
                    )
                else:
                    logger.info(
                        f"[grounding-audit] response grounded; "
                        f"{len(audit['response_nouns'])} nouns all verified"
                    )
            except Exception as gax:
                logger.warning(f"[grounding-audit] validator error: {gax}")

            # Ensure sponsored disclosure for any ads (legal requirement)
            corrected = ensure_sponsored_disclosure(accumulated, chunks)
            if corrected != accumulated:
                # Send correction with full response
                replace_data = json.dumps({"type": "replace", "content": corrected})
                yield f"{replace_data}\n".encode()
                accumulated = corrected

            # Log assistant response if tracking conversation
            if conversation_id:
                insert_message(conversation_id, "assistant", accumulated)

            # Signal completion
            yield b'{"type": "done"}\n'

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            error_data = json.dumps({"type": "error", "content": str(e)})
            yield f"{error_data}\n".encode()
        finally:
            # v2 Phase 6c: append one structured decision row. Best-effort —
            # this is the single source of truth for incident debugging after
            # the fact. See src/modules/observability/decision_log.py.
            try:
                from src.modules.observability import log_retrieval_decision
                route = log_state.get("route")
                latency_ms = int((_time.perf_counter() - turn_start) * 1000)
                log_retrieval_decision(
                    conversation_id=conversation_id,
                    query=message,
                    publisher=publisher,
                    intent=(route.intent if route else "unknown"),
                    intent_reason=(route.reason if route else ""),
                    current_edition_only=bool(route.current_edition_only) if route else False,
                    entity_gate=log_state["entity_gate"],
                    chunks=log_state["chunks"],
                    abstained=log_state["abstained"],
                    abstain_reason=log_state["abstain_reason"],
                    latency_ms=latency_ms,
                    response_preview=accumulated,
                    grounding_audit=log_state["grounding_audit"],
                )
            except Exception as log_err:
                logger.info(f"[decision-log] emit failed (non-fatal): {log_err}")

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
