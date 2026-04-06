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
                conversation_id = insert_conversation(session_id)
                logger.info(f"Created new conversation for session: {session_id}")

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

        accumulated = ""
        try:
            # Send searching status
            yield b'{"type": "status", "content": "Searching..."}\n'

            # Perform search — direct ChromaDB retrieval is fast and reliable.
            # Grand Network: default to this publisher's content, but if the user
            # explicitly asks about another city/paper, search across ALL publishers.
            effective_publisher = publisher
            if publisher:
                cross_ref_keywords = [
                    "windom", "pipestone", "mountain lake", "mt. lake", "mt lake",
                    "butterfield", "cottonwood", "jackson", "murray",
                    "observer", "advocate", "pipestone star", "county star",
                    "other papers", "other newspapers", "regional", "network",
                    "across", "all papers", "all publications",
                ]
                msg_lower = message.lower()
                if any(kw in msg_lower for kw in cross_ref_keywords):
                    effective_publisher = None  # Search all publishers
                    logger.info(f"Cross-network search triggered: '{message}' (was: {publisher})")

            chunks = engine.retrieve(message, publisher=effective_publisher)
            # Supplement with ads, directory, and events
            try:
                from src.modules.advertisements.search import AdvertisementSearch
                from src.modules.events.search import EventSearch
                from src.search_tools import SearchTools
                ad_results = AdvertisementSearch().search(message, publisher=effective_publisher)
                chunks.extend(ad_results)
                dir_results = SearchTools().search_directory(query=message, publisher=effective_publisher)
                chunks.extend(dir_results)
                event_results = EventSearch().search(message)
                chunks.extend(event_results)
            except Exception as sup_err:
                logger.warning(f"Supplemental search error: {sup_err}")

            # Send thinking status
            yield b'{"type": "status", "content": "Thinking..."}\n'

            # Set publisher context for dynamic system prompt
            engine._current_publisher = publisher

            # Stream response tokens
            for token in engine.generate_response_streaming(message, chunks):
                accumulated += token
                data = json.dumps({"type": "token", "content": token})
                yield f"{data}\n".encode()

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

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
