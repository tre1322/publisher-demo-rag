# Publisher RAG Demo - Developer Documentation

## Overview

Publisher RAG Demo is a RAG-based (Retrieval-Augmented Generation) chatbot designed for local publishers. It enables users to query news articles, product advertisements, and local events through a conversational interface powered by Claude.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              User Interface                              │
├──────────────────┬──────────────────┬──────────────────┬────────────────┤
│   Chat Frontend  │  Admin Dashboard │  Newspaper Demo  │ Embed Widget   │
│   (/chat)        │  (/admin)        │  (/demo/...)     │ (chat-widget.js)
└────────┬─────────┴────────┬─────────┴────────┬─────────┴────────────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Application                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ Chat Routes │  │Admin Routes │  │ Demo Routes │  │ Track Route │    │
│  └──────┬──────┘  └──────┬──────┘  └─────────────┘  └──────┬──────┘    │
└─────────┼────────────────┼──────────────────────────────────┼──────────┘
          │                │                                  │
          ▼                │                                  ▼
┌─────────────────────┐    │                    ┌─────────────────────────┐
│    Query Engine     │    │                    │   Analytics Module      │
│  ┌───────────────┐  │    │                    │  - Impressions          │
│  │ Search Agent  │  │    │                    │  - Click tracking       │
│  │ (Claude API)  │  │    │                    └─────────────────────────┘
│  └───────┬───────┘  │    │
│          ▼          │    │
│  ┌───────────────┐  │    │
│  │ Search Tools  │  │    │
│  └───────┬───────┘  │    │
└──────────┼──────────┘    │
           │               │
           ▼               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Data Layer                                     │
│  ┌─────────────────┐  ┌─────────────────────────────────────────────┐   │
│  │    ChromaDB     │  │              SQLite (articles.db)            │   │
│  │  Vector Store   │  │  ┌─────────┐ ┌──────┐ ┌────────┐ ┌────────┐ │   │
│  │  - Embeddings   │  │  │Articles │ │ Ads  │ │ Events │ │  ...   │ │   │
│  │  - Chunks       │  │  └─────────┘ └──────┘ └────────┘ └────────┘ │   │
│  └─────────────────┘  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| LLM | Claude (Anthropic) | Response generation, tool selection, metadata extraction |
| Vector DB | ChromaDB | Semantic search via embeddings |
| SQL DB | SQLite | Metadata storage, conversations, analytics |
| Embeddings | sentence-transformers | Document vectorization (all-MiniLM-L6-v2) |
| Web Framework | FastAPI | HTTP API and routing |
| Frontend | Vanilla JS + CSS | Chat UI, Admin dashboard |
| CSS Frameworks | Pico CSS, Bulma, Tabler | Styling options |

## Request Flow

### Chat Query Flow

```
1. User sends message
   │
2. Frontend sends GET /chat/stream?message=...&session_id=...
   │
3. Query Engine receives request
   │
4. Search Agent (Claude) selects tools
   │  - Analyzes user intent
   │  - Calls: hybrid_search, search_advertisements, search_events
   │
5. Search Tools execute queries
   │  - ChromaDB: semantic similarity search
   │  - SQLite: metadata filtering
   │
6. Results combined and deduplicated
   │
7. Context formatted with source URLs
   │
8. Claude generates response (streaming)
   │
9. Response streamed to frontend as NDJSON
   │
10. Analytics logged (impressions, conversation)
```

## Project Structure

```
publisher_rag_demo/
├── src/
│   ├── core/                    # Shared configuration and database
│   │   ├── config.py            # Environment variables, constants
│   │   └── database.py          # SQLite connection utilities
│   │
│   ├── modules/                 # Content type modules
│   │   ├── articles/            # News article storage and search
│   │   ├── advertisements/      # Product ads
│   │   ├── events/              # Local events
│   │   ├── conversations/       # Chat session logging
│   │   └── analytics/           # Impression and click tracking
│   │
│   ├── chat_frontend/           # HTML/JS chat interface
│   │   ├── routes.py            # /chat endpoints
│   │   └── templates/           # Jinja2 templates
│   │
│   ├── admin_frontend/          # Admin dashboard
│   │   ├── routes.py            # /admin endpoints
│   │   └── templates/           # Dashboard template
│   │
│   ├── mock_integrations/       # Demo pages
│   │   ├── routes.py            # /demo endpoints
│   │   └── templates/           # Newspaper mockup
│   │
│   ├── query_engine.py          # Main RAG orchestrator
│   ├── search_agent.py          # Claude tool-use for search
│   ├── search_tools.py          # Search aggregation layer
│   ├── ingestion.py             # Document processing
│   ├── prompts.py               # Prompt templates
│   └── chatbot.py               # FastAPI app entry point
│
├── scripts/                     # CLI utilities
│   ├── ingest.py                # Document ingestion
│   ├── reset_db.py              # Database reset
│   └── ...                      # Other utilities
│
├── static/                      # Static assets
│   └── chat-widget.js           # Embeddable chat widget
│
├── data/                        # Data storage
│   ├── documents/               # Source documents for ingestion
│   ├── chroma_db/               # ChromaDB persistent storage
│   └── articles.db              # SQLite database
│
└── docs/                        # Documentation (you are here)
```

## Quick Start

### Prerequisites
- Python 3.13+
- [uv](https://github.com/astral-sh/uv) package manager
- Anthropic API key

### Setup

```bash
# Clone and enter directory
cd publisher_rag_demo

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Initialize database
uv run python scripts/init_db.py

# Ingest sample documents
uv run python scripts/ingest.py --directory data/documents

# Load sample ads and events
uv run python scripts/load_sample_ads.py
uv run python scripts/load_sample_events.py

# Start the application
uv run python src/chatbot.py
```

### Access Points
- **Chat Interface**: http://localhost:7860/chat
- **Admin Dashboard**: http://localhost:7860/admin (user: admin, pass: admin)
- **Newspaper Demo**: http://localhost:7860/demo/newspaper

## Documentation Index

| Document | Description |
|----------|-------------|
| [API Reference](API.md) | HTTP endpoints, request/response formats |
| [Modules](MODULES.md) | Core components and content modules |
| [Frontend](FRONTEND.md) | Chat UI, Admin dashboard, Widget |
| [Scripts](SCRIPTS.md) | CLI tools and utilities |
| [Configuration](CONFIGURATION.md) | Environment variables and settings |
| [Widget Integration](WIDGET.md) | Embedding chat widget on external sites |

## Key Concepts

### Multi-Type Search
The Search Agent queries all content types (articles, ads, events) for every user query, ensuring comprehensive results. For example, a query about "roofing" returns:
- News articles about roofing regulations
- Ads from local roofing contractors
- Home improvement expo events

### URL Tracking
All source URLs in responses are wrapped with a tracking redirect:
```
/track?url={encoded_url}&type={content_type}&id={content_id}&conv={conversation_id}
```
This enables click-through rate analytics without modifying original URLs.

### Session Persistence
Chat sessions are identified by a UUID stored in localStorage. This enables:
- Conversation history restoration on page refresh
- Analytics tracking across messages
- Admin dashboard conversation viewing

### Streaming Responses
Responses use NDJSON (newline-delimited JSON) for real-time streaming:
```json
{"type": "status", "content": "Searching..."}
{"type": "status", "content": "Thinking..."}
{"type": "token", "content": "The "}
{"type": "token", "content": "answer "}
{"type": "done"}
```
