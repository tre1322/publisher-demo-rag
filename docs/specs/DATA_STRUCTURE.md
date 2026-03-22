# Data Structure Documentation

This document describes the data schemas used in the Publisher RAG Demo.

## Overview

The system uses two storage backends:
- **SQLite** - Structured metadata for articles, advertisements, and events
- **ChromaDB** - Vector embeddings for semantic search

## SQLite Database Schema

Located at `data/articles.db`

### Articles Table

Stores metadata for ingested news articles.

```sql
CREATE TABLE articles (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    publish_date TEXT,           -- Format: YYYY-MM-DD
    source_file TEXT NOT NULL,
    location TEXT,               -- Extracted location (country, city)
    subjects TEXT,               -- JSON array of topics
    summary TEXT,
    url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_publish_date ON articles(publish_date);
CREATE INDEX idx_author ON articles(author);
CREATE INDEX idx_location ON articles(location);
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `doc_id` | TEXT | Unique document identifier (UUID) |
| `title` | TEXT | Article title |
| `author` | TEXT | Author name |
| `publish_date` | TEXT | Publication date (YYYY-MM-DD) |
| `source_file` | TEXT | Original filename |
| `location` | TEXT | Primary location mentioned |
| `subjects` | TEXT | JSON array of topics (e.g., `["Politics", "Economy"]`) |
| `summary` | TEXT | Brief summary of content |
| `url` | TEXT | Source URL if available |
| `created_at` | TEXT | Ingestion timestamp |

### Advertisements Table

Stores product advertisements and deals.

```sql
CREATE TABLE advertisements (
    ad_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    advertiser TEXT NOT NULL,
    description TEXT,
    category TEXT,               -- Electronics, Fashion, Food, Home, Services, Sports
    price REAL,                  -- Current price
    original_price REAL,         -- Price before discount
    discount_percent REAL,       -- Discount percentage
    valid_from TEXT,             -- Format: YYYY-MM-DD
    valid_to TEXT,               -- Format: YYYY-MM-DD
    url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_ad_category ON advertisements(category);
CREATE INDEX idx_ad_valid_to ON advertisements(valid_to);
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `ad_id` | TEXT | Unique advertisement identifier (UUID) |
| `product_name` | TEXT | Product/service name |
| `advertiser` | TEXT | Company/brand name |
| `description` | TEXT | Ad copy/description |
| `category` | TEXT | Product category |
| `price` | REAL | Current sale price |
| `original_price` | REAL | Original price before discount |
| `discount_percent` | REAL | Percentage discount |
| `valid_from` | TEXT | Start date of offer |
| `valid_to` | TEXT | End date of offer |
| `url` | TEXT | Link to product/offer |
| `created_at` | TEXT | Creation timestamp |

**Categories:**
- Electronics
- Fashion
- Food
- Home
- Services
- Sports

### Events Table

Stores local events and activities.

```sql
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,               -- Venue/place name
    address TEXT,                -- Full address
    event_date TEXT,             -- Format: YYYY-MM-DD
    event_time TEXT,             -- Format: HH:MM
    end_time TEXT,               -- Format: HH:MM
    category TEXT,               -- Music, Sports, Arts, Food, Community
    price REAL,                  -- NULL for free events
    url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_event_category ON events(category);
CREATE INDEX idx_event_date ON events(event_date);
CREATE INDEX idx_event_location ON events(location);
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `event_id` | TEXT | Unique event identifier (UUID) |
| `title` | TEXT | Event title |
| `description` | TEXT | Event description |
| `location` | TEXT | Venue name |
| `address` | TEXT | Full street address |
| `event_date` | TEXT | Event date (YYYY-MM-DD) |
| `event_time` | TEXT | Start time (HH:MM) |
| `end_time` | TEXT | End time (HH:MM) |
| `category` | TEXT | Event category |
| `price` | REAL | Ticket price (NULL = free) |
| `url` | TEXT | Event URL |
| `created_at` | TEXT | Creation timestamp |

**Categories:**
- Music
- Sports
- Arts
- Food
- Community

## ChromaDB Vector Store

Located at `data/chroma_db/`

### Collection: `publisher_main`

Stores document chunks with embeddings for semantic search.

**Embedding Model:** `all-MiniLM-L6-v2` (384 dimensions)

### Chunk Metadata Schema

Each chunk in ChromaDB stores the following metadata:

```python
{
    "doc_id": str,           # Reference to articles table
    "title": str,            # Article title
    "author": str,           # Author name
    "publish_date": str,     # YYYY-MM-DD format
    "source_file": str,      # Original filename
    "chunk_index": int,      # Position within document (0-indexed)
    "url": str,              # Article URL
}
```

**Chunking Parameters:**
- Chunk size: 1024 tokens
- Chunk overlap: 200 tokens
- Splitter: Sentence-based (LlamaIndex SentenceSplitter)

## Search Agent Tool Schemas

The search agent uses these tool definitions to select the appropriate search method.

### semantic_search

```json
{
    "name": "semantic_search",
    "description": "Search for articles using natural language...",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query describing what to find"
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default: 5)"
            }
        },
        "required": ["query"]
    }
}
```

### metadata_search

```json
{
    "name": "metadata_search",
    "description": "Search for articles by metadata like date, author, location, or subject...",
    "parameters": {
        "type": "object",
        "properties": {
            "date_from": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format"
            },
            "date_to": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format"
            },
            "author": {
                "type": "string",
                "description": "Author name to search for"
            },
            "location": {
                "type": "string",
                "description": "Location/region to filter by"
            },
            "subject": {
                "type": "string",
                "description": "Subject/topic to filter by"
            }
        }
    }
}
```

### hybrid_search

```json
{
    "name": "hybrid_search",
    "description": "Combine semantic search with metadata filters...",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query"
            },
            "date_from": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format"
            },
            "date_to": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format"
            },
            "location": {
                "type": "string",
                "description": "Location to filter by"
            },
            "subject": {
                "type": "string",
                "description": "Subject to filter by"
            }
        },
        "required": ["query"]
    }
}
```

### search_advertisements

```json
{
    "name": "search_advertisements",
    "description": "Search for product advertisements and deals...",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional search query for products"
            },
            "category": {
                "type": "string",
                "description": "Product category (Electronics, Fashion, Food, Home, Services, Sports)"
            },
            "max_price": {
                "type": "number",
                "description": "Maximum price filter"
            },
            "on_sale_only": {
                "type": "boolean",
                "description": "Only return items currently on sale"
            }
        }
    }
}
```

### search_events

```json
{
    "name": "search_events",
    "description": "Search for local events like concerts, sports, arts...",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Event category (Music, Sports, Arts, Food, Community)"
            },
            "location": {
                "type": "string",
                "description": "Venue or area to search in (partial match)"
            },
            "date_from": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format"
            },
            "date_to": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format"
            },
            "max_price": {
                "type": "number",
                "description": "Maximum ticket price filter"
            },
            "free_only": {
                "type": "boolean",
                "description": "Only return free events"
            }
        }
    }
}
```

## Data Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Documents  │     │    Ads      │     │   Events    │
│  (.txt/.pdf)│     │   (manual)  │     │  (manual)   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Ingestion  │     │ load_sample │     │ load_sample │
│   Script    │     │   _ads.py   │     │  _events.py │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   └─────────┬─────────┘
┌─────────────┐                      │
│  Metadata   │                      │
│  Extractor  │                      │
│  (Claude)   │                      │
└──────┬──────┘                      │
       │                             │
       ▼                             ▼
┌─────────────┐             ┌─────────────┐
│  ChromaDB   │             │   SQLite    │
│  (vectors)  │             │  (metadata) │
└──────┬──────┘             └──────┬──────┘
       │                           │
       └─────────────┬─────────────┘
                     │
                     ▼
              ┌─────────────┐
              │   Search    │
              │    Agent    │
              └──────┬──────┘
                     │
                     ▼
              ┌─────────────┐
              │   Query     │
              │   Engine    │
              └──────┬──────┘
                     │
                     ▼
              ┌─────────────┐
              │   Gradio    │
              │  Interface  │
              └─────────────┘
```

## Search Result Format

All search tools return results in a consistent format:

```python
{
    "text": str,           # Main content text
    "metadata": {
        # Tool-specific metadata
        # For articles: doc_id, title, author, publish_date, url
        # For ads: ad_id, product_name, advertiser, category, price, url
        # For events: event_id, title, location, event_date, category, price, url
    },
    "score": float,        # Relevance score (0.0 - 1.0)
    "search_type": str,    # "semantic", "metadata", "advertisement", "event"
}
```

## Conversation History Format

The query engine maintains conversation history using the Gradio message format:

```python
[
    {"role": "user", "content": "What's happening in technology?"},
    {"role": "assistant", "content": "Here's what's happening in tech..."},
    {"role": "user", "content": "Tell me more about AI"},
    {"role": "assistant", "content": "Regarding AI developments..."},
]
```

- Maximum history: 10 turns (20 messages)
- History is passed to Claude for context but not to the search agent
