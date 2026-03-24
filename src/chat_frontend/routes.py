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
    return templates.TemplateResponse("chat.html", {"request": request})


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
    message: str, session_id: str | None = None
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

            # Perform search
            chunks = engine.search_agent.search(message)

            # Send thinking status
            yield b'{"type": "status", "content": "Thinking..."}\n'

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
