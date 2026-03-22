"""Gradio chat interface for the Publisher RAG Demo."""

import logging
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

# Import config first to configure logging with timestamps
import src.core.config  # noqa: F401
from src.modules.advertisements import get_random_advertisements
from src.modules.analytics import log_content_impression, log_url_click
from src.modules.articles import get_recent_articles
from src.modules.conversations import (
    insert_conversation,
    insert_message,
    update_conversation_end_time,
)
from src.admin_frontend import router as admin_router
from src.chat_frontend import router as chat_router
from src.mock_integrations import router as demo_router
from src.prompts import (
    HELP_MESSAGE,
    ensure_sponsored_disclosure,
    get_content_id,
    make_tracked_url,
)
from src.query_engine import QueryEngine

logger = logging.getLogger(__name__)


def sanitize_partial_html(text: str) -> str:
    """Hide incomplete HTML tags during streaming.

    Removes any incomplete HTML tag at the end of the text to prevent
    showing raw HTML syntax while streaming.

    Args:
        text: The accumulated text that may have incomplete HTML.

    Returns:
        Text with incomplete trailing HTML tags removed.
    """
    # Check for incomplete opening tag at the end (e.g., "<a href=..." without ">")
    # Find the last '<' that doesn't have a matching '>'
    last_open = text.rfind("<")
    if last_open != -1:
        # Check if there's a '>' after this '<'
        last_close = text.rfind(">")
        if last_close < last_open:
            # Incomplete tag - truncate at the '<'
            return text[:last_open]

    return text


# Global conversation tracking
current_conversation_id = None
current_session_id = None


def create_chatbot() -> gr.Blocks:
    """Create and configure the Gradio chatbot interface.

    Returns:
        Configured Gradio Blocks interface.
    """
    # Initialize query engine
    try:
        engine = QueryEngine()
    except ValueError as e:
        logger.error(f"Failed to initialize query engine: {e}")
        raise

    def respond_streaming(
        message: str, history: list
    ) -> Iterator[tuple[str, list]]:
        """Generate streaming response to user message.

        Args:
            message: User's input message.
            history: Chat history for conversation context.

        Yields:
            Tuple of (empty string for textbox, updated history).
        """
        global current_conversation_id, current_session_id

        if not message.strip():
            yield "", history
            return

        if not engine.is_ready():
            error_msg = (
                "No documents have been indexed yet. "
                "Please run the ingestion script first:\n\n"
                "`uv run python scripts/ingest.py`"
            )
            history = history + [{"role": "assistant", "content": error_msg}]
            yield "", history
            return

        # Initialize conversation on first message
        if current_conversation_id is None:
            current_session_id = str(uuid.uuid4())
            current_conversation_id = insert_conversation(current_session_id)
            logger.info(f"Started new conversation: {current_session_id}")

        # Log user message
        insert_message(current_conversation_id, "user", message)

        # Get previous history for context (excluding current user message)
        previous_history = history[:-1] if len(history) > 1 else []

        # Show "Searching..." status
        history = history + [{"role": "assistant", "content": "🔍 *Searching...*"}]
        yield "", history

        try:
            # Check for help request first
            if engine._is_help_request(message):
                logger.info("Help request detected")
                history[-1]["content"] = HELP_MESSAGE
                insert_message(current_conversation_id, "assistant", HELP_MESSAGE)
                yield "", history
                return

            # Perform search (blocking)
            chunks = engine.search_agent.search(message)

            # Log content impressions for analytics
            for chunk in chunks:
                content_type = chunk.get("search_type", "article")
                content_id = get_content_id(chunk)
                log_content_impression(
                    content_type=content_type,
                    content_id=content_id,
                    conversation_id=current_conversation_id,
                )

            # Update to "Thinking..." status
            history[-1]["content"] = "💭 *Thinking...*"
            yield "", history

            # Stream response tokens (replaces status message)
            # Pass conversation_id for URL tracking
            accumulated = ""
            for token in engine.generate_response_streaming(
                message, chunks, previous_history, current_conversation_id
            ):
                accumulated += token
                # Hide incomplete HTML tags during streaming
                display_text = sanitize_partial_html(accumulated)
                history[-1]["content"] = display_text
                yield "", history

            # Ensure sponsored disclosure for any ads (legal requirement)
            accumulated = ensure_sponsored_disclosure(accumulated, chunks)

            # Final update with complete text (in case last token completed a tag)
            history[-1]["content"] = accumulated
            yield "", history

            # Log complete response
            insert_message(current_conversation_id, "assistant", accumulated)

        except Exception as e:
            logger.error(f"Error processing query: {e}")
            error_msg = f"An error occurred while processing your question: {str(e)}"
            history[-1]["content"] = error_msg
            insert_message(current_conversation_id, "assistant", error_msg)
            yield "", history

    def load_sidebar_content() -> tuple[str, str]:
        """Load content for sidebar (top articles and ads).

        Returns:
            Tuple of (articles_markdown, ads_markdown).
        """
        # Get top 5 recent articles
        articles = get_recent_articles(limit=5)
        articles_md = ""
        for article in articles:
            title = article["title"]
            original_url = article.get("url", "")
            doc_id = article.get("doc_id", "unknown")

            # Make title clickable with tracking if URL available
            if original_url and original_url.strip():
                tracked_url = make_tracked_url(
                    original_url, "article", doc_id, conversation_id=None
                )
                articles_md += f"[**{title}**]({tracked_url})\n"
            else:
                articles_md += f"**{title}**\n"

            articles_md += f"*{article['publish_date']}*\n\n"

        if not articles_md:
            articles_md = "*No articles available*"

        # Get 2 featured ads (best discounts)
        ads = get_random_advertisements(limit=2)
        ads_md = ""
        for ad in ads:
            discount = ad.get("discount_percent", 0)
            product_name = ad["product_name"]
            original_url = ad.get("url", "")
            ad_id = ad.get("ad_id", "unknown")

            # Legal disclosure for sponsored content
            ads_md += "*[Sponsored]*\n"

            # Make product name clickable with tracking if URL available
            if original_url and original_url.strip():
                tracked_url = make_tracked_url(
                    original_url, "advertisement", ad_id, conversation_id=None
                )
                ads_md += f"[**{product_name}**]({tracked_url})\n"
            else:
                ads_md += f"**{product_name}**\n"

            if ad.get("price") is not None:
                ads_md += f"${ad['price']:.2f}"
                if discount > 0:
                    ads_md += f" *({discount}% off)*"
                ads_md += "\n"
            ads_md += f"_{ad['advertiser']}_\n\n"

        if not ads_md:
            ads_md = "*No ads available*"

        return articles_md, ads_md

    # Create Gradio interface
    with gr.Blocks(title="Publisher News Assistant") as demo:
        gr.Markdown(
            """
            # Publisher News Assistant

            Ask questions about our articles and news content.
            The assistant will search through indexed documents and provide
            answers with source citations.

            *Note: Conversations are logged for analysis and service improvement.*
            """
        )

        with gr.Row():
            # LEFT: Sidebar
            with gr.Column(scale=2):
                gr.Markdown("### 📰 Recent Articles")
                articles_display = gr.Markdown()

                gr.Markdown("---")

                gr.Markdown("### 🛍️ Featured Deals")
                ads_display = gr.Markdown()

            # RIGHT: Chat area
            with gr.Column(scale=6):
                # Try to use 'messages' type if supported, otherwise fall back to no initial message
                try:
                    chatbot = gr.Chatbot(
                        label="Chat",
                        height=500,
                        type="messages",
                        sanitize_html=False,  # Allow HTML links with target="_blank"
                        value=[
                            {
                                "role": "assistant",
                                "content": "Welcome! I can help you with:\n\n"
                                "📰 **News Articles** - Ask about recent news, topics, or specific articles\n"
                                "🛍️ **Shopping Deals** - Find products on sale and current promotions\n"
                                "📅 **Local Events** - Discover upcoming events and activities\n\n"
                                "Try asking: *\"What's happening in technology?\"* or *\"Any deals on electronics?\"*",
                            }
                        ],
                    )
                except TypeError:
                    # Older version of Gradio doesn't support type='messages'
                    chatbot = gr.Chatbot(
                        label="Chat",
                        height=500,
                    )

                msg = gr.Textbox(
                    label="Your Question",
                    placeholder="Ask a question about the articles...",
                    lines=2,
                )

                with gr.Row():
                    submit_btn = gr.Button("Send", variant="primary")
                    clear_btn = gr.Button("Clear")

                # Status indicator
                if engine.is_ready():
                    stats = engine.collection.count() if engine.collection else 0
                    gr.Markdown(f"*{stats} document chunks indexed and ready*")
                else:
                    gr.Markdown(
                        "*No documents indexed. Run `uv run python scripts/ingest.py` first.*"
                    )

        def user_submit(
            user_message: str, history: list
        ) -> Iterator[tuple[str, list]]:
            """Handle user message submission with streaming.

            Yields:
                Tuple of (empty string for textbox, updated history).
            """
            if not user_message.strip():
                yield "", history
                return

            # Add user message to history first
            history = history + [{"role": "user", "content": user_message}]
            yield "", history

            # Stream the response
            yield from respond_streaming(user_message, history)

        def clear_chat() -> tuple:
            """Clear chat history."""
            global current_conversation_id, current_session_id

            # Mark conversation as ended
            if current_conversation_id is not None:
                update_conversation_end_time(current_conversation_id)
                logger.info(f"Ended conversation: {current_session_id}")

            # Reset conversation tracking
            current_conversation_id = None
            current_session_id = None

            return "", []

        # Wire up events
        msg.submit(user_submit, [msg, chatbot], [msg, chatbot])
        submit_btn.click(user_submit, [msg, chatbot], [msg, chatbot])
        clear_btn.click(clear_chat, None, [msg, chatbot])

        # Load sidebar content on page load
        demo.load(load_sidebar_content, outputs=[articles_display, ads_display])

    return demo


def create_app() -> FastAPI:
    """Create FastAPI app with tracking endpoint and Gradio interface.

    Returns:
        FastAPI application with mounted Gradio interface.
    """
    app = FastAPI(title="Publisher News Assistant")

    # Include chat frontend routes
    app.include_router(chat_router)

    # Include demo integration routes
    app.include_router(demo_router)

    # Include admin dashboard routes
    app.include_router(admin_router)

    # Serve static files (chat widget) if directory exists
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    static_dir = Path("static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/mock-content")
    def mock_content(
        type: str,
        id: str,
        title: str = "Unknown",
    ):  # type: ignore[return-value]
        """Mock endpoint for testing - displays content info.

        Args:
            type: Content type (article, event, advertisement).
            id: Content ID.
            title: Content title.

        Returns:
            HTML page showing the content info.
        """
        from fastapi.responses import HTMLResponse

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{title}</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    max-width: 600px;
                    margin: 100px auto;
                    padding: 20px;
                    text-align: center;
                }}
                .badge {{
                    display: inline-block;
                    padding: 4px 12px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: bold;
                    text-transform: uppercase;
                    margin-bottom: 20px;
                }}
                .article {{ background: #e3f2fd; color: #1565c0; }}
                .advertisement {{ background: #fff3e0; color: #ef6c00; }}
                .event {{ background: #e8f5e9; color: #2e7d32; }}
                h1 {{ margin: 20px 0; }}
                .id {{ color: #666; font-size: 14px; }}
                .back {{ margin-top: 30px; }}
                .back a {{ color: #1976d2; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="badge {type}">{type}</div>
            <h1>{title}</h1>
            <p class="id">ID: {id}</p>
            <p>This is a mock page for testing click tracking.</p>
            <p class="back">Close this tab to return to the chatbot</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    @app.get("/track")
    def track_click(
        url: str,
        type: str,
        id: str,
        conv: int | None = None,
        request: Request = None,  # type: ignore[assignment]
    ) -> RedirectResponse:
        """Track a URL click and redirect to the target URL.

        Args:
            url: The target URL (URL-encoded).
            type: Content type (article, event, advertisement).
            id: Content ID.
            conv: Optional conversation ID.
            request: FastAPI request object.

        Returns:
            Redirect response to the target URL.
        """
        # Decode the URL
        target_url = unquote(url)

        # Log the click
        user_agent = None
        if request:
            user_agent = request.headers.get("user-agent")

        log_url_click(
            content_type=type,
            content_id=id,
            url=target_url,
            conversation_id=conv,
            user_agent=user_agent,
        )

        logger.info(f"Click tracked: {type}/{id} -> {target_url[:50]}...")

        # Redirect to the actual URL
        return RedirectResponse(url=target_url, status_code=302)

    # Create and mount Gradio app
    demo = create_chatbot()
    app = gr.mount_gradio_app(app, demo, path="/")

    return app


def main() -> None:
    """Launch the chatbot interface with FastAPI."""
    import uvicorn

    app = create_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7860,
        log_level="info",
    )


if __name__ == "__main__":
    main()
