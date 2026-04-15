"""Gradio chat interface for the Publisher RAG Demo."""

import logging
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Import config first to configure logging with timestamps
import src.core.config  # noqa: F401

# Initialize all database tables on startup (single authoritative path
# via each module's init_table(), which handles CREATE + ALTER migrations).
from src.core.database import init_all_tables

init_all_tables()

# Seed quadd articles into the main database if available
# Gated by SEED_QUADD_ON_STARTUP env var (same as init.sh)
_quadd_db = Path(__file__).parent.parent / "data" / "quadd_articles.db"
if _quadd_db.exists() and os.environ.get("SEED_QUADD_ON_STARTUP") == "true":
    try:
        import sqlite3 as _sq3
        import uuid as _uuid
        _qconn = _sq3.connect(str(_quadd_db))
        _qconn.row_factory = _sq3.Row
        _rows = _qconn.execute("""
            SELECT headline, byline, cleaned_web_text, start_page, jump_pages_json, section, edition_id, publisher_id
            FROM content_items
            WHERE cleaned_web_text IS NOT NULL AND length(cleaned_web_text) >= 100
              AND headline IS NOT NULL AND headline != '?'
              AND edition_id IN (31, 1312)
        """).fetchall()
        _qconn.close()

        _main_db = Path(__file__).parent.parent / "data" / "articles.db"
        _mconn = _sq3.connect(str(_main_db))
        _mc = _mconn.cursor()
        _seeded = 0
        for _r in _rows:
            _r = dict(_r)
            _hl = (_r.get("headline") or "").strip()
            _body = (_r.get("cleaned_web_text") or "").strip()
            if not _hl or len(_body) < 50:
                continue
            _eid = _r.get("edition_id", 0)
            _pid = _r.get("publisher_id", 1)
            _did = f"quadd_{_eid}_{_uuid.uuid5(_uuid.NAMESPACE_DNS, f'{_eid}_{_hl}')}"
            # Determine publisher, location, and date based on publisher_id
            if _pid == 2:
                _pub = "Pipestone County Star"
                _loc = "Pipestone, MN"
                _pdate = "2026-01-08"
            else:
                _pub = "Observer/Advocate"
                _pdate = "2026-01-28"
                _loc = "Cottonwood County, MN"
                if "butterfield" in _hl.lower(): _loc = "Butterfield, MN"
                elif "bingham" in _hl.lower(): _loc = "Bingham Lake, MN"
                elif "larson" in _hl.lower() or "mt. lake" in _hl.lower(): _loc = "Mountain Lake, MN"
                elif "pipestone" in _hl.lower(): _loc = "Pipestone, MN"
            _mc.execute("""INSERT OR REPLACE INTO articles
                (doc_id,title,author,publish_date,source_file,location,publisher,edition_id,section,start_page,continuation_pages,full_text,cleaned_text,needs_review,status,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,'parsed',CURRENT_TIMESTAMP)""",
                (_did, _hl, _r.get("byline"), _pdate, "quadd_extraction", _loc,
                 _pub, _eid, _r.get("section"), _r.get("start_page"),
                 _r.get("jump_pages_json"), _body, _body))
            _seeded += 1
        _mconn.commit()
        _mconn.close()
        logging.getLogger(__name__).info(f"SEED: Inserted {_seeded} quadd articles into articles table")
    except Exception as e:
        logging.getLogger(__name__).error(f"SEED FAILED: {e}", exc_info=True)
else:
    logging.getLogger(__name__).info(f"SEED: No quadd DB at {_quadd_db}, skipping")

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
    # Initialize query engine and content orchestrator
    try:
        engine = QueryEngine()
    except ValueError as e:
        logger.error(f"Failed to initialize query engine: {e}")
        raise

    from src.content_orchestrator import ContentOrchestrator

    try:
        orchestrator = ContentOrchestrator()
        logger.info("Content orchestrator initialized")
    except Exception as e:
        logger.error(f"Content orchestrator init failed: {e}")
        orchestrator = None

    def respond(message: str, history: list) -> tuple[str, list]:
        """Non-streaming response wrapper."""
        for response in respond_streaming(message, history):
            pass
        return "", response[1] if response else ("", history)

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

            # Perform search via content orchestrator (intent-based routing)
            if orchestrator is not None:
                chunks = orchestrator.search(message)
            elif engine.search_agent is not None:
                chunks = engine.search_agent.search(message)
            else:
                chunks = engine.retrieve(message)

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

    # Option B Layout - Chat-First Design
    custom_css = """
    * { box-sizing: border-box; margin: 0; padding: 0; }
    .header { background: #1a365d; color: white; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; }
    .logo { font-size: 18px; font-weight: bold; display: flex; align-items: center; gap: 10px; }
    .logo-icon { background: #c53030; padding: 6px 12px; border-radius: 4px; }
    .network-tag { background: #2d3748; padding: 4px 10px; border-radius: 4px; font-size: 12px; opacity: 0.9; }
    .hero-chat { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 24px; text-align: center; color: white; margin: -20px -20px 20px -20px; }
    .hero-chat h1 { font-size: 28px; margin-bottom: 8px; }
    .hero-chat p { opacity: 0.9; margin-bottom: 24px; }
    .chat-box { max-width: 700px; margin: 0 auto; background: white; border-radius: 16px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); overflow: hidden; }
    .footer-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; padding: 20px; }
    .footer-card { background: #f7fafc; border-radius: 12px; padding: 16px; text-align: center; }
    .footer-card h4 { font-size: 14px; margin-bottom: 8px; color: #1a365d; }
    .footer-card p { font-size: 12px; color: #718096; }
    .quick-links h3, .top-stories h3, .local-sections h3 { font-size: 15px; margin-bottom: 14px; color: #1a365d; }
    .section-item { display: flex; justify-content: space-between; align-items: center; padding: 10px; background: #f7fafc; border-radius: 8px; font-size: 13px; margin-bottom: 8px; }
    .section-count { background: #c53030; color: white; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
    """
    
    # Use a theme and add header at the top
    theme = gr.themes.Default(
        primary_hue="blue",
        secondary_hue="purple",
    )
    
    with gr.Blocks(title="Pipestone Star - Grand Network", theme=theme, css=custom_css) as demo:
        # Header using HTML
        gr.HTML("""
        <div style="background: #1a365d; color: white; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; margin: -20px -20px 20px -20px;">
            <div style="font-size: 18px; font-weight: bold; display: flex; align-items: center; gap: 10px;">
                <span style="background: #c53030; padding: 6px 12px; border-radius: 4px;">📰</span>
                Pipestone Star
                <span style="background: #2d3748; padding: 4px 10px; border-radius: 4px; font-size: 12px; opacity: 0.9;">🌐 Grand Network</span>
            </div>
            <div style="display: flex; gap: 16px; font-size: 14px;">
                <span>Nearby Papers</span>
                <span>Business Directory</span>
                <span>Events</span>
            </div>
        </div>
        """)
        
        # Chat area
        with gr.Row():
            with gr.Column(scale=6):
                gr.Markdown("### 💬 Chat with Your Local Assistant")
                # Try to use 'messages' type if supported, otherwise fall back to no initial message
                try:
                    chatbot = gr.Chatbot(
                        height=400,
                        type="messages",
                        sanitize_html=False,
                        value=[
                            {
                                "role": "assistant",
                                "content": "👋 Hi! I'm your Pipestone assistant. What would you like to know about our community?",
                            }
                        ],
                    )
                except TypeError:
                    chatbot = gr.Chatbot(height=400)

                msg = gr.Textbox(
                    label="Ask a question",
                    placeholder="Type your question here... (e.g., 'Any events this weekend?')",
                    lines=2,
                )
                with gr.Row():
                    clear_btn = gr.Button("Clear Chat", variant="secondary")
                    submit_btn = gr.Button("Send", variant="primary")
                    
                submit_btn.click(respond, [msg, chatbot], [msg, chatbot])
                msg.submit(respond, [msg, chatbot], [msg, chatbot])
                clear_btn.click(lambda: (None, [{"role": "assistant", "content": "👋 Hi! I'm your Pipestone assistant. What would you like to know?"}]), None, [chatbot])

            # Sidebar
            with gr.Column(scale=2):
                gr.Markdown("### 🔗 Quick Links")
                gr.HTML("""
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">📰 News</div>
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">🏠 Real Estate</div>
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">💼 Jobs</div>
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">🚗 Autos</div>
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">📅 Events</div>
                    <div style="background: #f7fafc; padding: 12px; border-radius: 8px; text-align: center; font-size: 13px; cursor: pointer;">🛍️ Shopping</div>
                </div>
                """)
                
                gr.Markdown("### 📰 Featured Stories")
                articles_display = gr.Markdown()
                
                gr.Markdown("### 🛍️ Local Deals")
                ads_display = gr.Markdown()

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
    import sys
    print("DEBUG: create_app starting...", flush=True)
    sys.stdout.flush()
    """Create FastAPI app with tracking endpoint and Gradio interface.

    Returns:
        FastAPI application with mounted Gradio interface.
    """
    app = FastAPI(title="Publisher News Assistant")

    # v2 Phase 3: ensure FTS5 index is built. Idempotent (<1s at current scale).
    try:
        from src.modules.articles.fts import rebuild_fts
        rebuild_fts()
    except Exception as e:
        logger.warning(f"FTS rebuild at startup failed (non-fatal): {e}")

    # v2 diagnostic: quick health endpoint for the RAG stack. Reports SQLite
    # article/FTS counts, Chroma chunk count, and whether a probe term (default
    # "Koerner", overridable via ?q=) appears in SQLite and in the Chroma
    # index. Used to diagnose SQLite/Chroma drift on deployed environments
    # where we can't easily open a shell.
    @app.get("/rag-health")
    def _rag_health(q: str = "Koerner") -> dict:
        out: dict = {"probe": q}
        # SQLite / FTS
        try:
            from src.core.database import get_connection
            conn = get_connection()
            try:
                out["articles_count"] = conn.execute(
                    "SELECT count(*) FROM articles"
                ).fetchone()[0]
            except Exception as e:
                out["articles_count_error"] = str(e)
            try:
                out["fts_count"] = conn.execute(
                    "SELECT count(*) FROM articles_fts"
                ).fetchone()[0]
            except Exception as e:
                out["fts_count_error"] = str(e)
            try:
                rows = conn.execute(
                    "SELECT doc_id, title, publish_date, publisher "
                    "FROM articles WHERE full_text LIKE ? LIMIT 5",
                    (f"%{q}%",),
                ).fetchall()
                out["probe_in_sqlite"] = [
                    {
                        "doc_id": r[0],
                        "title": r[1],
                        "publish_date": r[2],
                        "publisher": r[3],
                    }
                    for r in rows
                ]
            except Exception as e:
                out["probe_in_sqlite_error"] = str(e)
        except Exception as e:
            out["sqlite_error"] = str(e)

        # Chroma
        try:
            from src.core.vector_store import get_articles_collection
            coll = get_articles_collection()
            out["chroma_chunks"] = coll.count()
            # Count chroma chunks that contain probe text AND belong to
            # any of the matching doc_ids in SQLite.
            doc_ids = [d["doc_id"] for d in out.get("probe_in_sqlite", [])]
            if doc_ids:
                try:
                    got = coll.get(
                        where={"doc_id": {"$in": doc_ids}},
                        limit=20,
                    )
                    out["probe_chroma_chunks"] = len(got.get("ids", []))
                    out["probe_chroma_doc_ids"] = sorted(
                        set(
                            (m or {}).get("doc_id")
                            for m in got.get("metadatas", [])
                            if m
                        )
                    )
                except Exception as e:
                    out["probe_chroma_error"] = str(e)
            else:
                out["probe_chroma_chunks"] = 0
        except Exception as e:
            out["chroma_error"] = str(e)

        return out

    # Include chat frontend routes
    app.include_router(chat_router)

    # Include demo integration routes
    app.include_router(demo_router)

    # Include admin dashboard routes
    app.include_router(admin_router)

    # Include business console routes (Main Street OS)
    try:
        from src.business_frontend import router as business_router
        from src.business_frontend.auth import AuthRequired

        app.include_router(business_router)

        @app.exception_handler(AuthRequired)
        async def _biz_auth_redirect(request, exc):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/business/login", status_code=303)

        logger.info("Business console routes mounted at /business/")
    except Exception as e:
        logger.warning(f"Could not mount business console routes: {e}")

    # Include public news routes
    try:
        from src.public_frontend import router as news_router
        app.include_router(news_router)
        logger.info("Public news routes mounted at /news/")
    except Exception as e:
        logger.warning(f"Could not mount public news routes: {e}")

    # Serve static files (chat widget) if directory exists
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    static_dir = Path("static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory="static"), name="static")

    # Landing page templates
    landing_templates = Jinja2Templates(
        directory=str(Path(__file__).parent / "chat_frontend" / "templates")
    )

    @app.get("/", response_class=HTMLResponse)
    async def landing_page(request: Request) -> HTMLResponse:
        """Render the Observer/Advocate landing page with AI chat hero."""
        return landing_templates.TemplateResponse(request=request, name="landing.html")

    @app.get("/windom", response_class=HTMLResponse)
    async def windom_landing(request: Request) -> HTMLResponse:
        """Render the Windom / Cottonwood County Citizen landing page."""
        return landing_templates.TemplateResponse(request=request, name="landing.html")

    @app.get("/pipestone", response_class=HTMLResponse)
    async def pipestone_landing(request: Request) -> HTMLResponse:
        """Render the Pipestone County Star landing page (green theme)."""
        return landing_templates.TemplateResponse(request=request, name="landing_pipestone.html")

    # ── Homepage Stories API ──

    @app.get("/api/homepage-stories")
    async def homepage_stories(
        publisher: str = "",
        limit: int = 6,
        section: str = "",
        all_publishers: bool = False,
        front_page: bool = False,
    ):
        """Return top stories for a publisher's landing page.

        Args:
            publisher: Publisher name to filter by. Ignored if all_publishers=True.
            limit: Max stories to return.
            section: Optional content_type filter (e.g. 'news', 'sports').
            all_publishers: If true, return stories from all publishers (for regional column).
            front_page: If true, only return stories with start_page=1 (front page stories).
        """
        from src.modules.publishers.database import get_publisher_by_name
        from src.modules.content_items.database import get_homepage_content

        publisher_id = 0  # 0 = all publishers

        if not all_publishers:
            if not publisher:
                return {"stories": []}
            pub = get_publisher_by_name(publisher)
            if not pub:
                return {"stories": []}
            publisher_id = pub["id"]

        # When filtering for front page, fetch extra items since we filter after
        fetch_limit = limit * 3 if front_page else limit
        items = get_homepage_content(publisher_id, limit=fetch_limit, section=section)

        # Filter to front-page stories only, then take the top N by size
        if front_page:
            front_items = [i for i in items if str(i.get("start_page", "")) == "1"]
            if front_items:
                # Sort by article length (largest = most prominent front-page stories)
                front_items.sort(
                    key=lambda x: len(x.get("cleaned_web_text") or x.get("raw_text") or ""),
                    reverse=True,
                )
                items = front_items[:limit]
            # else: fall back to all items (no front-page data available)
        stories = []
        for item in items:
            body = item.get("cleaned_web_text", "") or item.get("raw_text", "")
            excerpt = body[:200].rsplit(" ", 1)[0] + "..." if len(body) > 200 else body
            stories.append({
                "headline": item.get("headline", "Untitled"),
                "byline": item.get("byline", ""),
                "section": item.get("content_type", "news"),
                "date": item.get("edition_date", ""),
                "excerpt": excerpt,
                "start_page": item.get("start_page"),
                "item_id": item.get("id"),
                "publisher_id": item.get("publisher_id"),
            })
        return {"stories": stories}

    # ── Story Detail (content_items) ──

    @app.get("/story/{item_id}", response_class=HTMLResponse)
    async def story_detail_page(request: Request, item_id: int):
        """Render article detail page from content_items table."""
        from src.modules.content_items.database import get_content_item
        from src.modules.publishers.database import get_publisher as get_publisher_by_id

        item = get_content_item(item_id)
        if not item:
            return HTMLResponse("<h1>Story not found</h1>", status_code=404)

        # Resolve publisher name
        pub_name = "Grand Network"
        pub = get_publisher_by_id(item.get("publisher_id")) if item.get("publisher_id") else None
        if pub:
            pub_name = pub.get("name", pub_name)

        # Map content_items fields to article_detail template fields
        article = {
            "title": item.get("headline", "Untitled"),
            "author": item.get("byline", ""),
            "publish_date": item.get("edition_date", ""),
            "section": item.get("content_type", ""),
            "publisher": pub_name,
            "location": "",
            "subheadline": item.get("subheadline", ""),
            "cleaned_text": item.get("cleaned_web_text", "") or item.get("raw_text", ""),
            "full_text": item.get("cleaned_web_text", "") or item.get("raw_text", ""),
            "start_page": item.get("start_page"),
            "continuation_pages": None,
        }

        # Related stories from same publisher
        related = []
        if item.get("publisher_id"):
            from src.modules.content_items.database import get_homepage_content
            all_items = get_homepage_content(item["publisher_id"], limit=8)
            for r in all_items:
                if r["id"] != item_id:
                    body = r.get("cleaned_web_text", "") or r.get("raw_text", "")
                    related.append({
                        "doc_id": f"../story/{r['id']}",
                        "title": r.get("headline", ""),
                        "author": r.get("byline", ""),
                        "publish_date": r.get("edition_date", ""),
                        "publisher": pub_name,
                        "section": r.get("content_type", ""),
                        "excerpt": body[:150],
                    })
                    if len(related) >= 4:
                        break

        # Check for podcast audio file
        podcast_url = None
        podcast_path = Path("static/podcasts") / f"{item_id}.m4a"
        if podcast_path.exists():
            podcast_url = f"/static/podcasts/{item_id}.m4a"

        return landing_templates.TemplateResponse(
            request=request, name="article_detail.html",
            context={"article": article, "related": related, "podcast_url": podcast_url},
        )

    # ── Ad Serving Endpoints ──

    # ── Business Profile Page ──

    @app.get("/business/{org_id}", response_class=HTMLResponse)
    async def business_profile_page(request: Request, org_id: int):
        """Render business directory profile page."""
        import json as _json
        from src.core.database import get_connection

        conn = get_connection()
        conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
        org = cursor.fetchone()

        if not org:
            conn.close()
            return HTMLResponse("<h1>Business not found</h1>", status_code=404)

        # Check if currently advertising (has active ads)
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM advertisements WHERE advertiser = ? AND status = 'active'",
            (org.get("name", ""),),
        )
        active_count = cursor.fetchone()
        conn.close()

        # Parse social JSON
        social = {}
        if org.get("social_json"):
            try:
                social = _json.loads(org["social_json"])
            except Exception:
                pass

        business = {
            "name": org.get("name", ""),
            "category": org.get("category", ""),
            "address": org.get("address", ""),
            "city": org.get("city", ""),
            "state": org.get("state", ""),
            "phone": org.get("phone", ""),
            "email": org.get("email", ""),
            "website": org.get("website", ""),
            "hours": org.get("hours_json", ""),
            "description": org.get("description", ""),
            "services": org.get("services", ""),
            "keywords": org.get("keywords", ""),
            "social": social,
            "is_active_advertiser": (active_count or {}).get("cnt", 0) > 0,
        }

        return landing_templates.TemplateResponse(
            request=request, name="business_detail.html",
            context={"business": business},
        )

    @app.get("/api/directory")
    async def list_directory(publisher: str = ""):
        """List all business directory entries."""
        from src.core.database import get_connection as _get_conn

        conn = _get_conn()
        conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        cursor = conn.cursor()

        sql = "SELECT id, name, city, state, phone, category, website, description, enrichment_status, last_advertised_at FROM organizations"
        params: list = []
        if publisher:
            sql += " WHERE publisher = ?"
            params.append(publisher)
        sql += " ORDER BY last_advertised_at DESC NULLS LAST"

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()
        return {"businesses": rows, "total": len(rows)}

    # ── Ad Serving Endpoints ──

    @app.get("/ad/{ad_id}")
    async def serve_ad_web_image(ad_id: str):
        """Serve web-optimized ad image for modal display."""
        from fastapi.responses import FileResponse
        from src.modules.advertisements.database import get_advertisement_by_id as get_advertisement

        ad = get_advertisement(ad_id)
        if not ad or not ad.get("web_image_path"):
            return HTMLResponse("<h1>Ad image not found</h1>", status_code=404)
        web_path = Path(ad["web_image_path"])
        if not web_path.exists():
            return HTMLResponse("<h1>Ad image file missing</h1>", status_code=404)
        return FileResponse(str(web_path), media_type="image/jpeg")

    @app.get("/ad/{ad_id}/original")
    async def serve_ad_original(ad_id: str):
        """Serve original ad file for download/print."""
        from fastapi.responses import FileResponse
        from src.modules.advertisements.database import get_advertisement_by_id as get_advertisement

        ad = get_advertisement(ad_id)
        if not ad or not ad.get("file_path"):
            return HTMLResponse("<h1>Ad file not found</h1>", status_code=404)
        file_path = Path(ad["file_path"])
        if not file_path.exists():
            return HTMLResponse("<h1>Ad file missing</h1>", status_code=404)
        media_types = {
            "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
            "jpeg": "image/jpeg", "gif": "image/gif", "tiff": "image/tiff",
            "tif": "image/tiff", "webp": "image/webp", "bmp": "image/bmp",
        }
        ft = ad.get("file_type", "pdf")
        return FileResponse(
            str(file_path),
            media_type=media_types.get(ft, "application/octet-stream"),
            filename=f"{ad.get('advertiser', 'ad')}.{ft}",
        )

    @app.get("/api/ads/{ad_id}")
    async def get_ad_metadata(ad_id: str):
        """Return ad metadata as JSON (for modal display)."""
        from src.modules.advertisements.database import get_advertisement_by_id as get_advertisement

        ad = get_advertisement(ad_id)
        if not ad:
            return JSONResponse(status_code=404, content={"error": "Ad not found"})
        return {
            "ad_id": ad.get("ad_id"),
            "advertiser": ad.get("advertiser", ""),
            "ad_type": ad.get("ad_type", ""),
            "ad_category": ad.get("ad_category", ""),
            "location": ad.get("location", ""),
            "file_type": ad.get("file_type", ""),
            "has_image": bool(ad.get("web_image_path")),
            "has_original": bool(ad.get("file_path")),
        }

    # ── Article Detail Pages ──

    @app.get("/api/articles/{doc_id}")
    async def get_article_api(doc_id: str):
        """API: fetch a single article by doc_id."""
        import sqlite3
        db_path = Path(__file__).parent.parent / "data" / "articles.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM articles WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        conn.close()
        if not row:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "Article not found"})
        return dict(row)

    @app.get("/article/{doc_id}", response_class=HTMLResponse)
    async def article_detail_page(request: Request, doc_id: str):
        """Render the article detail page."""
        import sqlite3
        db_path = Path(__file__).parent.parent / "data" / "articles.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM articles WHERE doc_id = ?", (doc_id,)
        ).fetchone()

        # Get related articles (same publisher, different article)
        related = []
        if row:
            related = conn.execute(
                """SELECT doc_id, title, author, publish_date, publisher, section,
                          substr(COALESCE(cleaned_text, full_text, ''), 1, 150) as excerpt
                   FROM articles
                   WHERE publisher = ? AND doc_id != ?
                     AND (cleaned_text IS NOT NULL AND length(cleaned_text) > 50)
                   ORDER BY RANDOM() LIMIT 4""",
                (row["publisher"], doc_id)
            ).fetchall()

        conn.close()

        if not row:
            return HTMLResponse("<h1>Article not found</h1>", status_code=404)

        article = dict(row)
        related_list = [dict(r) for r in related]

        return landing_templates.TemplateResponse(
            request=request, name="article_detail.html",
            context={"article": article, "related": related_list},
        )

    @app.get("/health")
    def health_check():
        """Health check endpoint for Railway."""
        return {"status": "ok"}

    @app.get("/debug/seed-status")
    def seed_status():
        """Debug: check if articles were seeded and quadd DB exists."""
        import sqlite3 as sq
        result = {}
        # Check quadd DB
        qdb = Path(__file__).parent.parent / "data" / "quadd_articles.db"
        result["quadd_db_exists"] = qdb.exists()
        if qdb.exists():
            result["quadd_db_size"] = qdb.stat().st_size
            try:
                c = sq.connect(str(qdb))
                tables = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                result["quadd_tables"] = tables
                if "content_items" in tables:
                    result["quadd_content_items"] = c.execute("SELECT COUNT(*) FROM content_items").fetchone()[0]
                    result["quadd_edition_31"] = c.execute("SELECT COUNT(*) FROM content_items WHERE edition_id=31").fetchone()[0]
                c.close()
            except Exception as e:
                result["quadd_error"] = str(e)
        # Check main DB
        mdb = Path(__file__).parent.parent / "data" / "articles.db"
        result["main_db_exists"] = mdb.exists()
        if mdb.exists():
            try:
                c = sq.connect(str(mdb))
                result["main_articles"] = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
                c.close()
            except Exception as e:
                result["main_error"] = str(e)
        # Check chroma
        cdb = Path(__file__).parent.parent / "data" / "chroma_db"
        result["chroma_dir_exists"] = cdb.exists()
        if cdb.exists():
            result["chroma_files"] = [str(p.name) for p in cdb.iterdir()]
        # Check ChromaDB collection counts
        try:
            from src.core.vector_store import get_articles_collection, get_ads_collection
            art_coll = get_articles_collection()
            result["chroma_articles_count"] = art_coll.count()
            # Do a test query for "wolverines"
            from sentence_transformers import SentenceTransformer
            from src.core.config import EMBEDDING_MODEL
            model = SentenceTransformer(EMBEDDING_MODEL)
            emb = model.encode(["Did the wolverines win?"]).tolist()
            test_results = art_coll.query(query_embeddings=emb, n_results=3, include=["documents", "metadatas", "distances"])
            result["test_wolverines_query"] = []
            if test_results and test_results["documents"]:
                for i, doc in enumerate(test_results["documents"][0]):
                    dist = test_results["distances"][0][i]
                    title = test_results["metadatas"][0][i].get("title", "?")
                    doc_id = test_results["metadatas"][0][i].get("doc_id", "?")
                    result["test_wolverines_query"].append({
                        "rank": i+1, "score": round(1-dist, 3),
                        "title": title[:60], "doc_id": doc_id[:40],
                        "text_preview": doc[:80]
                    })
        except Exception as e:
            result["chroma_error"] = str(e)
        return result

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

    # Create and mount Gradio chatbot at /chat (requires ANTHROPIC_API_KEY)
    try:
        demo = create_chatbot()
        app = gr.mount_gradio_app(app, demo, path="/chat")
        logger.info("Chatbot mounted at /chat")
    except (ValueError, Exception) as e:
        logger.warning(
            f"Chatbot disabled: {e}. "
            "Admin routes still available. Set ANTHROPIC_API_KEY to enable chat."
        )

    return app


def main() -> None:
    """Launch the chatbot interface with FastAPI."""
    import os

    # Get port from environment - Railway expects PORT
    port = int(os.environ.get("PORT", "8080"))
    print(f"Starting on port {port}", flush=True)
    
    app = create_app()
    
    # Run with uvicorn
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    import sys
    print("DEBUG: Starting chatbot...", flush=True)
    sys.stdout.flush()
    main()
