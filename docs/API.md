# API Reference

This document covers all HTTP endpoints in the Publisher RAG Demo application.

## Chat API

Base path: `/chat`

### GET /chat

Renders the chat interface HTML page.

**Response**: HTML page with chat UI

---

### GET /chat/stream

Streams an assistant response to a user message.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message` | string | Yes | User's message (URL-encoded) |
| `session_id` | string | No | Session UUID for conversation tracking |

**Response**: `application/x-ndjson` (newline-delimited JSON stream)

**Stream Message Types**:

```json
// Status update
{"type": "status", "content": "Searching..."}

// Response token
{"type": "token", "content": "The "}

// Completion signal
{"type": "done"}

// Error
{"type": "error", "content": "Error message"}
```

**Example**:
```bash
curl "http://localhost:7860/chat/stream?message=What%20news%20is%20there%3F&session_id=abc-123"
```

**Response Stream**:
```
{"type": "status", "content": "Searching..."}
{"type": "status", "content": "Thinking..."}
{"type": "token", "content": "Based "}
{"type": "token", "content": "on "}
{"type": "token", "content": "the "}
...
{"type": "done"}
```

---

### GET /chat/history

Retrieves conversation history for a session.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `session_id` | string | Yes | Session UUID |

**Response**: JSON
```json
{
  "messages": [
    {"role": "user", "content": "What news is there?"},
    {"role": "assistant", "content": "Here are the latest articles..."}
  ]
}
```

**Example**:
```bash
curl "http://localhost:7860/chat/history?session_id=abc-123"
```

---

## Admin API

Base path: `/admin`

All admin endpoints require HTTP Basic Authentication:
- **Username**: `admin`
- **Password**: Value of `ADMIN_PASSWORD` env var (default: `admin`)

### GET /admin

Renders the admin dashboard HTML page.

**Response**: HTML page with Tabler-based dashboard

---

### GET /admin/api/stats

Returns conversation statistics.

**Response**:
```json
{
  "total_conversations": 42,
  "total_messages": 156,
  "avg_messages_per_conversation": 3.71,
  "most_recent_conversation": "2024-12-07T10:30:00"
}
```

---

### GET /admin/api/queries

Returns most common user queries.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `limit` | int | 100 | Number of conversations to analyze |
| `top_n` | int | 20 | Number of top queries to return |

**Response**:
```json
[
  {"query": "What's happening in technology?", "count": 5},
  {"query": "Any sales today?", "count": 3}
]
```

---

### GET /admin/api/words

Returns word frequency analysis of user queries (excluding stop words).

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `limit` | int | 100 | Number of conversations to analyze |
| `top_n` | int | 30 | Number of top words to return |

**Response**:
```json
[
  {"word": "technology", "count": 12},
  {"word": "events", "count": 9},
  {"word": "sales", "count": 7}
]
```

---

### GET /admin/api/conversations

Returns recent conversations with previews.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `limit` | int | 50 | Maximum conversations to return |

**Response**:
```json
[
  {
    "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "started_at": "2024-12-07T10:30:00",
    "ended_at": "2024-12-07T10:35:00",
    "message_count": 4,
    "duration": "5m",
    "preview": "U: What articles are there?\nA: Here are the top articles..."
  }
]
```

---

### GET /admin/api/engagement

Returns analytics on content impressions and clicks.

**Response**:
```json
{
  "total_impressions": 1200,
  "total_clicks": 85,
  "overall_ctr": "7.1%",
  "ctr_by_type": [
    {
      "content_type": "article",
      "shown": 800,
      "clicked": 70,
      "ctr": "8.8%"
    },
    {
      "content_type": "advertisement",
      "shown": 300,
      "clicked": 10,
      "ctr": "3.3%"
    }
  ],
  "top_clicked": [
    {"content_type": "article", "content_id": "doc_123", "clicks": 15}
  ],
  "top_shown": [
    {"content_type": "article", "content_id": "doc_456", "impressions": 150}
  ]
}
```

---

### GET /admin/api/table/{table_name}

Browse database tables with pagination.

**Path Parameters**:
| Name | Type | Description |
|------|------|-------------|
| `table_name` | string | One of: `articles`, `advertisements`, `events`, `conversations`, `conversation_messages`, `content_impressions`, `url_clicks` |

**Query Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `page` | int | 1 | Page number (1-indexed) |
| `page_size` | int | 25 | Rows per page |

**Response**:
```json
{
  "columns": ["doc_id", "title", "publish_date", "author"],
  "rows": [
    ["doc_001", "Tech News Today", "2024-12-07", "John Doe"],
    ["doc_002", "Local Sports Update", "2024-12-06", "Jane Smith"]
  ],
  "page": 1,
  "page_size": 25,
  "total_pages": 10,
  "total_count": 250
}
```

---

### GET /admin/api/tables

Lists all available database tables.

**Response**:
```json
["articles", "advertisements", "events", "conversations", "conversation_messages", "content_impressions", "url_clicks"]
```

---

### GET /admin/api/export

Exports conversations to a JSON file.

**Parameters**:
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `limit` | int | 1000 | Maximum conversations to export |

**Response**:
```json
{
  "exported": 42,
  "file": "data/conversations_export.json"
}
```

The exported file is saved to `data/conversations_export.json`.

---

## Tracking API

### GET /track

Tracks a URL click and redirects to the target URL.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | Yes | Target URL (URL-encoded) |
| `type` | string | Yes | Content type: `article`, `advertisement`, `event` |
| `id` | string | Yes | Content ID (doc_id, ad_id, or event_id) |
| `conv` | int | No | Conversation ID |

**Response**: 302 Redirect to the target URL

**Example**:
```
/track?url=https%3A%2F%2Fexample.com%2Farticle&type=article&id=doc_123&conv=42
```

This endpoint:
1. Logs the click to the `url_clicks` table
2. Records user agent and timestamp
3. Redirects to the decoded URL

---

## Demo Routes

Base path: `/demo`

### GET /demo/newspaper

Renders a newspaper mockup page demonstrating chat integration.

**Response**: HTML page with:
- Newspaper-style layout with articles
- Embed mode switcher (floating, sidebar, inline, none)
- Integration with chat widget

---

## Static Assets

### GET /static/chat-widget.js

Returns the embeddable chat widget JavaScript.

**Usage**:
```html
<script src="http://localhost:7860/static/chat-widget.js"></script>
```

See [Frontend Documentation](FRONTEND.md#embeddable-widget) for configuration options.

---

## Error Responses

All API endpoints may return error responses:

**400 Bad Request**:
```json
{"detail": "Missing required parameter: message"}
```

**401 Unauthorized** (Admin endpoints):
```json
{"detail": "Not authenticated"}
```

**404 Not Found**:
```json
{"detail": "Not Found"}
```

**500 Internal Server Error**:
```json
{"detail": "Internal server error"}
```

---

## CORS

The application does not configure CORS headers by default. For cross-origin widget embedding, you may need to add CORS middleware to `src/chatbot.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-publisher-site.com"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```
