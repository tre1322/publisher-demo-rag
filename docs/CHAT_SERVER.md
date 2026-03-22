# Chat Server Documentation

The chat server is a FastAPI application that provides:
- RAG-powered chat with streaming responses
- URL click tracking for analytics
- Admin dashboard for data management
- Two frontends: Gradio (main) and vanilla JS

## Running the Server

```bash
uv run python src/chatbot.py
```

Server starts on `http://localhost:7860`

---

## API Endpoints

### Chat Frontend

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | GET | Vanilla JS chat page |
| `/chat/stream` | GET | Streaming response endpoint |

### Gradio Frontend

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Gradio chat interface (main UI) |

### Tracking & Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/track` | GET | URL click tracking with redirect |
| `/mock-content` | GET | Mock content page for testing |

### Admin

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin` | GET | Admin dashboard (Gradio) |

---

## Streaming Response Format

The `/chat/stream` endpoint uses **ndjson** (newline-delimited JSON):

```
{"type": "status", "content": "Searching..."}
{"type": "status", "content": "Thinking..."}
{"type": "token", "content": "Hello"}
{"type": "token", "content": ", "}
{"type": "token", "content": "world!"}
{"type": "done"}
```

### Message Types

| Type | Description |
|------|-------------|
| `status` | Status update (Searching, Thinking) |
| `token` | LLM response token |
| `done` | Stream complete |
| `error` | Error occurred |

### Client Example

```javascript
const response = await fetch(`/chat/stream?message=${encodeURIComponent(msg)}`);
const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const lines = decoder.decode(value).split('\n');
    for (const line of lines) {
        if (!line.trim()) continue;
        const data = JSON.parse(line);
        // Handle data.type: status, token, done, error
    }
}
```

---

## URL Tracking

### Track Endpoint

```
GET /track?url=<encoded_url>&type=<content_type>&id=<content_id>&conv=<conversation_id>
```

| Parameter | Description |
|-----------|-------------|
| `url` | Target URL (URL-encoded) |
| `type` | Content type: `article`, `event`, `advertisement` |
| `id` | Content ID |
| `conv` | Optional conversation ID |

Returns a 302 redirect to the target URL after logging the click.

### Mock Content Endpoint

For testing without real URLs:

```
GET /mock-content?type=article&id=123&title=Sample%20Article
```

Displays a simple page showing the content type, ID, and title.

---

## Configuration

Environment variables (set in `.env`):

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key |

### LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Claude model ID |
| `LLM_TEMPERATURE` | `0.3` | Response temperature |

### Embedding Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence transformer model |

### Retrieval Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `RETRIEVAL_TOP_K` | `5` | Number of chunks to retrieve |
| `SIMILARITY_THRESHOLD` | `0.7` | Minimum similarity score |
| `MAX_CONTEXT_TOKENS` | `8000` | Max context size |

### Chunking Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_SIZE` | `1024` | Chunk size in tokens |
| `CHUNK_OVERLAP` | `200` | Overlap between chunks |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:7860` | Base URL for tracking links |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Core Components

### QueryEngine (`src/query_engine.py`)

Handles query processing and response generation:

- `retrieve(query)` - Get relevant chunks from ChromaDB
- `generate_response(query, chunks, history)` - Generate response
- `generate_response_streaming(query, chunks, history, conversation_id)` - Stream response
- `is_ready()` - Check if documents are indexed

### SearchAgent (`src/search_agent.py`)

Intelligent search with query transformation:

- Classifies query intent (article, event, advertisement)
- Transforms queries for better retrieval
- Routes to appropriate content type

### Prompts (`src/prompts.py`)

- `SYSTEM_PROMPT` - Instructions for Claude
- `format_context(chunks)` - Format retrieved chunks
- `make_tracked_url(url, type, id)` - Create tracking URLs

---

## Database

SQLite database at `data/publisher.db`:

### Content Tables
- `articles` - News articles
- `events` - Local events
- `advertisements` - Ads/deals

### Analytics Tables
- `conversations` - Chat sessions
- `conversation_messages` - Individual messages
- `content_impressions` - What was shown
- `url_clicks` - What was clicked

See `docs/specs/DATA_STRUCTURE.md` for full schema.

---

## File Structure

```
src/
├── chatbot.py           # Main FastAPI app with Gradio
├── query_engine.py      # RAG query processing
├── search_agent.py      # Query transformation
├── prompts.py           # LLM prompts and formatting
├── core/
│   └── config.py        # Configuration
├── chat_frontend/       # Vanilla JS frontend
│   ├── routes.py        # /chat endpoints
│   └── templates/       # Jinja2 templates
├── modules/
│   ├── articles.py      # Article CRUD
│   ├── events.py        # Event CRUD
│   ├── advertisements.py # Ad CRUD
│   ├── conversations.py # Conversation tracking
│   └── analytics/       # Click/impression tracking
└── admin_dashboard.py   # Admin UI
```
