# Modules & Components

This document covers the core components and content modules of the Publisher RAG Demo.

## Core Components

### Configuration (`src/core/config.py`)

Centralized configuration loaded from environment variables.

**Key Exports**:
```python
# Paths
PROJECT_ROOT: Path          # Project root directory
DATA_DIR: Path              # data/ directory
DOCUMENTS_DIR: Path         # data/documents/
CHROMA_PERSIST_DIR: Path    # data/chroma_db/
INGESTED_FILES_PATH: Path   # data/ingested_files.json

# API
ANTHROPIC_API_KEY: str      # Required

# LLM Settings
LLM_MODEL: str              # Default: claude-sonnet-4-20250514
LLM_TEMPERATURE: float      # Default: 0.3

# Embedding
EMBEDDING_MODEL: str        # Default: all-MiniLM-L6-v2

# Chunking
CHUNK_SIZE: int             # Default: 1024
CHUNK_OVERLAP: int          # Default: 200

# Retrieval
RETRIEVAL_TOP_K: int        # Default: 5
SIMILARITY_THRESHOLD: float # Default: 0.7
MAX_CONTEXT_TOKENS: int     # Default: 8000

# ChromaDB
CHROMA_COLLECTION_NAME: str # Default: publisher_main

# Server
BASE_URL: str               # Default: http://localhost:7860
```

See [Configuration Reference](CONFIGURATION.md) for full details.

---

### Database Utilities (`src/core/database.py`)

Shared SQLite connection management.

**Functions**:

```python
def get_connection() -> sqlite3.Connection:
    """Get SQLite connection with Row factory."""

def init_all_tables() -> None:
    """Initialize all module tables."""

def get_all_publishers() -> list[str]:
    """Get unique publisher names across all tables."""
```

**Database Path**: `data/articles.db`

---

### Query Engine (`src/query_engine.py`)

Main RAG orchestrator that coordinates search and response generation.

**Class: QueryEngine**

```python
class QueryEngine:
    def __init__(self):
        """Initialize embedding model, ChromaDB, Claude client, SearchAgent."""

    def retrieve(self, query: str) -> list[dict]:
        """
        Semantic search via ChromaDB.

        Returns chunks with:
        - text: Chunk content
        - metadata: {doc_id, title, publish_date, author, ...}
        - score: Similarity score (0-1)
        """

    def generate_response(
        self,
        query: str,
        chunks: list[dict],
        history: list[dict] | None = None
    ) -> str:
        """Generate response using Claude with context."""

    def generate_response_streaming(
        self,
        query: str,
        chunks: list[dict],
        history: list[dict] | None = None,
        conversation_id: int | None = None
    ) -> Iterator[str]:
        """Stream response tokens."""

    def query(
        self,
        query: str,
        history: list[dict] | None = None
    ) -> dict:
        """
        Full query pipeline.

        Returns:
        - response: Generated text
        - sources: Formatted source citations
        - chunks: Retrieved chunks
        """

    def is_ready(self) -> bool:
        """Check if documents are indexed."""
```

**Key Behaviors**:
- Truncates context to `MAX_CONTEXT_TOKENS` (keeps at least 2 chunks)
- Maintains up to 10 conversation turns in history
- Tracks consecutive empty results (suggests help after 3)

---

### Search Agent (`src/search_agent.py`)

Claude-powered tool selection for intelligent search.

**Class: SearchAgent**

```python
class SearchAgent:
    def __init__(self):
        """Initialize Claude client and SearchTools."""

    def search(self, query: str) -> list[dict]:
        """
        Use Claude to select and execute search tools.

        Returns deduplicated, sorted results from all tools.
        """
```

**Tool Selection Strategy**:

The Search Agent uses Claude's tool_use capability to:
1. Analyze user intent
2. Select appropriate tools (typically all three for comprehensive results)
3. Execute tools in parallel
4. Deduplicate and sort results by relevance

**Available Tools** (6 total):

| Tool | Purpose | Parameters |
|------|---------|------------|
| `semantic_search` | Find articles by meaning | `query`, `top_k` |
| `metadata_search` | Filter articles by metadata | `date_from`, `date_to`, `author`, `location`, `subject` |
| `hybrid_search` | Combined semantic + metadata | `query`, `date_from`, `date_to`, `location`, `subject` |
| `search_advertisements` | Find product ads | `query`, `category`, `max_price`, `on_sale_only` |
| `search_events` | Find local events | `query`, `category`, `location`, `date_from`, `date_to`, `max_price`, `free_only` |
| `get_database_info` | Get content counts | None |

**System Prompt Highlights**:
- Includes current date context for time-based queries
- Instructs Claude to call all relevant tools for comprehensive results
- Provides examples of query interpretation

---

### Search Tools (`src/search_tools.py`)

Aggregation layer that delegates to content-specific search modules.

**Class: SearchTools**

```python
class SearchTools:
    # Article searches
    def semantic_search(self, query: str, top_k: int = 5) -> list[dict]
    def metadata_search(self, **filters) -> list[dict]
    def hybrid_search(self, query: str, **filters) -> list[dict]
    def get_chunks_for_article(self, doc_id: str) -> list[dict]

    # Advertisement search
    def search_advertisements(
        self,
        query: str = None,
        category: str = None,
        max_price: float = None,
        on_sale_only: bool = False
    ) -> list[dict]

    # Event search
    def search_events(
        self,
        query: str = None,
        category: str = None,
        location: str = None,
        date_from: str = None,
        date_to: str = None,
        max_price: float = None,
        free_only: bool = False
    ) -> list[dict]

    # Database info
    def get_database_info(self) -> dict
```

**Function: get_search_tools_schema()**

Generates dynamic tool schemas from database content:
```python
def get_search_tools_schema() -> list[dict]:
    """
    Returns tool definitions for Claude tool_use.

    Dynamically loads:
    - Available categories from ads/events
    - Available subjects/locations from articles
    """
```

---

### Prompts (`src/prompts.py`)

Prompt templates and URL tracking utilities.

**Constants**:

```python
HELP_MESSAGE: str
    # User guide explaining capabilities

SYSTEM_PROMPT: str
    # Core instructions for Claude:
    # - Only use provided context
    # - Always cite sources with HTML links
    # - Be concise but complete
    # - Handle empty results conversationally

QUERY_TEMPLATE: str
    # Format: "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
```

**Functions**:

```python
def make_tracked_url(
    url: str,
    content_type: str,
    content_id: str,
    conversation_id: int | None = None
) -> str:
    """
    Wrap URL in tracking redirect.

    Returns: /track?url={encoded}&type={type}&id={id}&conv={conv}
    """

def get_content_id(chunk: dict) -> str:
    """Extract ID from chunk metadata (doc_id, ad_id, or event_id)."""

def format_context(
    chunks: list[dict],
    conversation_id: int | None = None
) -> str:
    """
    Format chunks for Claude context.

    Output format:
    [Article 1]
    Title: {title}
    Date: {date}
    Author: {author}
    URL: {tracked_url}

    {content}
    """

def format_sources(chunks: list[dict]) -> str:
    """
    Format source citations.

    Output:
    **Sources:**
    - {title} ({date}) - Relevance: {score:.2f}
    """
```

---

## Content Modules

All content modules follow a consistent pattern:
- `database.py` - CRUD operations and SQL queries
- `search.py` - Search class with result formatting
- `__init__.py` - Exports and tool schema

---

### Articles Module (`src/modules/articles/`)

News article storage and semantic/metadata search.

**Database Schema** (`articles` table):

| Column | Type | Description |
|--------|------|-------------|
| `doc_id` | TEXT PK | Unique document identifier |
| `title` | TEXT | Article title |
| `author` | TEXT | Author name |
| `publish_date` | TEXT | Date (YYYY-MM-DD) |
| `source_file` | TEXT | Original filename |
| `location` | TEXT | Geographic location |
| `subjects` | TEXT | JSON array of topics |
| `summary` | TEXT | Article summary |
| `url` | TEXT | Source URL |
| `publisher` | TEXT | Publisher name |
| `created_at` | TEXT | Ingestion timestamp |

**Indexes**: `publish_date`, `author`, `location`

**Database Functions**:

```python
def insert_article(doc_id, title, author, publish_date, source_file,
                   location, subjects, summary, url, publisher) -> None

def search_by_metadata(date_from, date_to, author, location,
                       subject, limit=20) -> list[dict]

def get_recent_articles(limit=5) -> list[dict]

def get_all_subjects() -> list[str]

def get_all_locations() -> list[str]

def get_date_range() -> tuple[str | None, str | None]

def get_article_count() -> int

def clear_articles() -> None
```

**Class: ArticleSearch**

```python
class ArticleSearch:
    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.7
    ) -> list[dict]:
        """Embedding-based similarity search via ChromaDB."""

    def metadata_search(self, **filters) -> list[dict]:
        """Database-only search with exact/partial matching."""

    def hybrid_search(
        self,
        query: str,
        **filters
    ) -> list[dict]:
        """
        1. Filter articles by metadata
        2. Semantic search on filtered results
        3. Re-rank by similarity
        """

    def get_article_details(self, doc_id: str) -> dict | None

    def get_chunks_for_article(self, doc_id: str) -> list[dict]
```

**Result Format**:
```python
{
    "text": "Chunk content...",
    "metadata": {
        "doc_id": "doc_123",
        "title": "Article Title",
        "publish_date": "2024-12-07",
        "author": "John Doe",
        "location": "New York",
        "subjects": ["technology", "business"],
        "url": "https://...",
        "chunk_index": 0
    },
    "score": 0.85,
    "search_type": "semantic"  # or "direct", "hybrid"
}
```

---

### Advertisements Module (`src/modules/advertisements/`)

Product advertisement storage and search.

**Database Schema** (`advertisements` table):

| Column | Type | Description |
|--------|------|-------------|
| `ad_id` | TEXT PK | Unique ad identifier |
| `product_name` | TEXT | Product name |
| `advertiser` | TEXT | Brand/company |
| `description` | TEXT | Ad description |
| `category` | TEXT | Product category |
| `price` | REAL | Current price |
| `original_price` | REAL | Price before discount |
| `discount_percent` | REAL | Discount percentage |
| `valid_from` | TEXT | Start date (YYYY-MM-DD) |
| `valid_to` | TEXT | End date (YYYY-MM-DD) |
| `url` | TEXT | Product URL |
| `raw_text` | TEXT | Original ad text |
| `publisher` | TEXT | Publisher name |
| `created_at` | TEXT | Creation timestamp |

**Indexes**: `category`, `valid_to`

**Database Functions**:

```python
def insert_advertisement(ad_id, product_name, advertiser, description,
                        category, price, original_price, discount_percent,
                        valid_from, valid_to, url, raw_text, publisher) -> None

def search_advertisements(category, max_price, on_sale_only,
                         active_only=True, limit=20) -> list[dict]

def get_random_advertisements(limit=2) -> list[dict]

def get_all_ad_categories() -> list[str]

def get_advertisement_count() -> int

def clear_advertisements() -> None
```

**Class: AdvertisementSearch**

```python
class AdvertisementSearch:
    def search(
        self,
        query: str = None,
        category: str = None,
        max_price: float = None,
        on_sale_only: bool = False
    ) -> list[dict]
```

**Result Format**:
```python
{
    "text": "Product Name from Advertiser: Description - $19.99 (20% off)",
    "metadata": {
        "ad_id": "ad_123",
        "product_name": "Product Name",
        "advertiser": "Brand",
        "category": "Electronics",
        "price": 19.99,
        "discount_percent": 20.0,
        "url": "https://..."
    },
    "score": 1.0,
    "search_type": "advertisement"
}
```

---

### Events Module (`src/modules/events/`)

Local event storage and search.

**Database Schema** (`events` table):

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | TEXT PK | Unique event identifier |
| `title` | TEXT | Event title |
| `description` | TEXT | Event description |
| `location` | TEXT | Venue name |
| `address` | TEXT | Full address |
| `event_date` | TEXT | Date (YYYY-MM-DD) |
| `event_time` | TEXT | Start time (HH:MM) |
| `end_time` | TEXT | End time (HH:MM) |
| `category` | TEXT | Event category |
| `price` | REAL | Ticket price (NULL = free) |
| `url` | TEXT | Event URL |
| `raw_text` | TEXT | Original text |
| `publisher` | TEXT | Publisher name |
| `created_at` | TEXT | Creation timestamp |

**Indexes**: `category`, `event_date`, `location`

**Database Functions**:

```python
def insert_event(event_id, title, description, location, address,
                event_date, event_time, end_time, category, price,
                url, raw_text, publisher) -> None

def search_events(category, location, date_from, date_to,
                 max_price, free_only, limit=20) -> list[dict]

def get_all_event_categories() -> list[str]

def get_event_count() -> int

def clear_events() -> None
```

**Class: EventSearch**

```python
class EventSearch:
    def search(
        self,
        query: str = None,
        category: str = None,
        location: str = None,
        date_from: str = None,
        date_to: str = None,
        max_price: float = None,
        free_only: bool = False
    ) -> list[dict]
```

**Result Format**:
```python
{
    "text": "Event Title: Description - Dec 15, 2024 at 7:00 PM at Venue. $25",
    "metadata": {
        "event_id": "evt_123",
        "title": "Event Title",
        "location": "Venue Name",
        "address": "123 Main St",
        "event_date": "2024-12-15",
        "event_time": "19:00",
        "category": "Music",
        "price": 25.00,
        "url": "https://..."
    },
    "score": 1.0,
    "search_type": "event"
}
```

---

### Conversations Module (`src/modules/conversations/`)

Chat session and message logging.

**Database Schemas**:

**`conversations` table**:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `session_id` | TEXT UNIQUE | Frontend-generated UUID |
| `started_at` | TEXT | Session start (ISO format) |
| `ended_at` | TEXT | Session end (ISO format) |
| `message_count` | INTEGER | Total messages |

**`conversation_messages` table**:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `conversation_id` | INTEGER FK | References conversations.id |
| `role` | TEXT | "user" or "assistant" |
| `content` | TEXT | Message content |
| `timestamp` | TEXT | ISO timestamp |
| `metadata` | TEXT | JSON (search results, etc.) |

**Indexes**: `conversation_id`, `timestamp`, `started_at`

**Functions**:

```python
def insert_conversation(session_id: str) -> int:
    """Create conversation, return ID."""

def insert_message(
    conversation_id: int,
    role: str,
    content: str,
    metadata: dict | None = None
) -> int:
    """Insert message, update conversation count."""

def update_conversation_end_time(conversation_id: int) -> None

def get_conversation(session_id: str) -> dict | None

def get_conversation_messages(conversation_id: int) -> list[dict]

def get_all_conversations(limit: int = 100) -> list[dict]

def get_conversation_stats() -> dict:
    """
    Returns:
    - total_conversations
    - total_messages
    - avg_messages_per_conversation
    - most_recent_conversation
    """
```

---

### Analytics Module (`src/modules/analytics/`)

Content impression and click tracking.

**Database Schemas**:

**`content_impressions` table**:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `conversation_id` | INTEGER | Optional conversation reference |
| `message_id` | INTEGER | Optional message reference |
| `content_type` | TEXT | "article", "event", "advertisement" |
| `content_id` | TEXT | doc_id, event_id, or ad_id |
| `shown_at` | TEXT | ISO timestamp |

**`url_clicks` table**:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `conversation_id` | INTEGER | Optional conversation reference |
| `content_type` | TEXT | Content type |
| `content_id` | TEXT | Content ID |
| `url` | TEXT | Clicked URL |
| `clicked_at` | TEXT | ISO timestamp |
| `user_agent` | TEXT | Browser user agent |

**Functions**:

```python
def log_content_impression(
    content_type: str,
    content_id: str,
    conversation_id: int | None = None,
    message_id: int | None = None
) -> int

def log_url_click(
    content_type: str,
    content_id: str,
    url: str,
    conversation_id: int | None = None,
    user_agent: str | None = None
) -> int

def get_impression_stats() -> dict:
    """
    Returns:
    - by_type: Count per content type
    - unique_content_shown: Distinct content count
    - top_content: Top 10 most shown
    """

def get_click_stats() -> dict:
    """
    Returns:
    - total_clicks
    - by_type: Clicks per content type
    - top_clicked: Top 10 most clicked
    - CTR calculations by type
    """
```

---

## Module Dependencies

```
query_engine.py
    └── search_agent.py
            └── search_tools.py
                    ├── articles/search.py
                    │       └── articles/database.py
                    ├── advertisements/search.py
                    │       └── advertisements/database.py
                    └── events/search.py
                            └── events/database.py

chat_frontend/routes.py
    ├── query_engine.py
    └── conversations/database.py

admin_frontend/routes.py
    ├── conversations/database.py
    └── analytics/database.py
```

---

## Adding a New Content Type

To add a new content type (e.g., "classifieds"):

1. **Create module directory**: `src/modules/classifieds/`

2. **Create database.py**:
   - Define table schema
   - Implement CRUD functions
   - Add `init_table()` function

3. **Create search.py**:
   - Create `ClassifiedsSearch` class
   - Define `CLASSIFIEDS_TOOLS_SCHEMA`
   - Implement search with result formatting

4. **Create __init__.py**:
   - Export database functions
   - Export search class and schema

5. **Update search_tools.py**:
   - Import ClassifiedsSearch
   - Add delegation methods
   - Include schema in `get_search_tools_schema()`

6. **Update search_agent.py**:
   - Add tool to system prompt
   - Handle new tool in `_execute_tool()`

7. **Update core/database.py**:
   - Import and call `init_table()` in `init_all_tables()`
