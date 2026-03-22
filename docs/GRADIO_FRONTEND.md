# Gradio Frontend Documentation

The Gradio frontend is the primary chat interface, designed for deployment on Hugging Face Spaces. It provides a full-featured chat experience with sidebar content and conversation tracking.

## Location

- **Source**: `src/chatbot.py`
- **URL**: `http://localhost:7860/` (root path)

---

## Features

### Chat Interface

- Streaming responses with status indicators ("Searching...", "Thinking...")
- Conversation history within session
- HTML link support with `target="_blank"` for new tabs
- Partial HTML tag sanitization during streaming

### Sidebar

- **Recent Articles**: Top 5 articles by publish date
- **Featured Deals**: 2 random advertisements with discounts
- Clickable links with URL tracking

### Analytics Integration

- Conversation logging (session ID, timestamps)
- Message logging (user and assistant)
- Content impression tracking (what was shown)
- URL click tracking via `/track` redirect

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Gradio Blocks                         │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌───────────────────────────────────┐ │
│  │  Sidebar    │  │            Chat Area              │ │
│  │             │  │  ┌─────────────────────────────┐  │ │
│  │ 📰 Articles │  │  │        Chatbot              │  │ │
│  │  - Title 1  │  │  │  [User]: Hello              │  │ │
│  │  - Title 2  │  │  │  [Bot]: Hi! I can help...   │  │ │
│  │  - ...      │  │  └─────────────────────────────┘  │ │
│  │             │  │                                   │ │
│  │ 🛍️ Deals    │  │  ┌─────────────────────────────┐  │ │
│  │  - Deal 1   │  │  │  [Input] [Send] [Clear]     │  │ │
│  │  - Deal 2   │  │  └─────────────────────────────┘  │ │
│  └─────────────┘  └───────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Key Components

### `create_chatbot()`

Creates and configures the Gradio interface:

```python
def create_chatbot() -> gr.Blocks:
    # Initialize QueryEngine
    # Define streaming response handler
    # Build Gradio layout with sidebar + chat
    # Wire up event handlers
    return demo
```

### `respond_streaming()`

Handles user messages with streaming:

1. Validates message and engine readiness
2. Initializes conversation on first message
3. Shows "Searching..." status
4. Performs search via `SearchAgent`
5. Logs content impressions
6. Shows "Thinking..." status
7. Streams response tokens
8. Logs complete response

### `load_sidebar_content()`

Loads sidebar data on page load:

1. Fetches recent articles via `get_recent_articles(limit=5)`
2. Fetches random ads via `get_random_advertisements(limit=2)`
3. Formats as Markdown with tracked URLs
4. Returns tuple of (articles_md, ads_md)

### `sanitize_partial_html()`

Hides incomplete HTML tags during streaming:

```python
def sanitize_partial_html(text: str) -> str:
    # Find last '<' without matching '>'
    # Truncate at incomplete tag
    # Prevents showing raw HTML during stream
```

---

## Conversation Tracking

### Session Management

```python
# Global state (per-process)
current_conversation_id = None
current_session_id = None
```

- New session created on first message
- Session UUID stored in `current_session_id`
- Database ID stored in `current_conversation_id`
- Cleared on "Clear" button click

### Database Integration

```python
# On first message
current_session_id = str(uuid.uuid4())
current_conversation_id = insert_conversation(current_session_id)

# On each message
insert_message(current_conversation_id, "user", message)
insert_message(current_conversation_id, "assistant", response)

# On clear
update_conversation_end_time(current_conversation_id)
```

---

## URL Tracking

### Tracked URLs

All URLs in responses use the `/track` redirect:

```python
tracked_url = make_tracked_url(
    original_url,
    content_type,  # "article", "event", "advertisement"
    content_id,
    conversation_id
)
# Returns: /track?url=<encoded>&type=<type>&id=<id>&conv=<conv_id>
```

### Links Format

The LLM generates HTML links:

```html
<a href="/track?url=..." target="_blank">Article Title</a>
```

Gradio is configured with `sanitize_html=False` to render these.

---

## Configuration

The Chatbot component configuration:

```python
chatbot = gr.Chatbot(
    label="Chat",
    height=500,
    type="messages",           # Uses messages format
    sanitize_html=False,       # Allow HTML links
    value=[...initial_message...]
)
```

---

## Integration with FastAPI

The Gradio app is mounted on FastAPI:

```python
def create_app() -> FastAPI:
    app = FastAPI(title="Publisher News Assistant")

    # Add /chat routes
    app.include_router(chat_router)

    # Add /track endpoint
    @app.get("/track")
    def track_click(...): ...

    # Mount Gradio at root
    demo = create_chatbot()
    app = gr.mount_gradio_app(app, demo, path="/")

    return app
```

---

## Deployment

### Local Development

```bash
uv run python src/chatbot.py
```

### Hugging Face Spaces

See `docs/specs/DEPLOYMENT.md` for full deployment guide.

Key points:
- Uses Docker container
- Pre-baked data in image
- Environment variables for API keys
- Auto-reload disabled in production

---

## File Dependencies

```
src/chatbot.py
├── src/core/config.py          # Configuration
├── src/query_engine.py         # RAG processing
├── src/prompts.py              # URL tracking helpers
├── src/modules/articles.py     # Article queries
├── src/modules/advertisements.py # Ad queries
├── src/modules/conversations.py  # Conversation logging
└── src/modules/analytics/      # Impression/click tracking
```
