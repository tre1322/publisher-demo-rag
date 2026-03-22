# CLI Scripts & Utilities

This document covers the command-line scripts in the `scripts/` directory.

## Document Ingestion

### ingest.py

Main document ingestion CLI for indexing articles.

**Usage**:
```bash
uv run python scripts/ingest.py [OPTIONS]
```

**Options**:
| Option | Default | Description |
|--------|---------|-------------|
| `--directory`, `-d` | `data/documents` | Directory containing documents |
| `--no-metadata` | False | Skip Claude metadata extraction |
| `--stats` | False | Show ingestion statistics only |
| `--publisher` | None | Publisher name for all documents |

**Examples**:
```bash
# Ingest all documents in default directory
uv run python scripts/ingest.py

# Ingest from custom directory
uv run python scripts/ingest.py -d /path/to/docs

# Fast ingestion without metadata extraction
uv run python scripts/ingest.py --no-metadata

# View current stats without ingesting
uv run python scripts/ingest.py --stats

# Set publisher for all documents
uv run python scripts/ingest.py --publisher "The Daily Tribune"
```

**Supported Formats**:
- `.txt` - Plain text
- `.pdf` - PDF documents (via pdfplumber)
- `.rtf` - Rich Text Format (via striprtf)

**Process**:
1. Scans directory for supported files
2. Checks `ingested_files.json` for duplicates
3. Extracts text from each file
4. Splits into chunks (size: 1024, overlap: 200)
5. Generates embeddings (sentence-transformers)
6. Optionally extracts metadata via Claude
7. Stores in ChromaDB and SQLite
8. Updates `ingested_files.json`

**Output**:
```
Ingesting documents from: data/documents
Processing: article1.txt
  - Extracted 2,450 characters
  - Split into 3 chunks
  - Generated embeddings
  - Extracted metadata: Technology, 2024-12-07
Processing: article2.pdf
  ...

Ingestion complete:
  - Ingested: 15 files
  - Skipped (already indexed): 5 files
  - Failed: 0 files
  - Total chunks: 45
```

---

### download_samples.py

Downloads sample articles from RSS feeds.

**Usage**:
```bash
uv run python scripts/download_samples.py
```

**RSS Sources** (10 feeds):
- NPR News
- BBC World
- New York Times
- Reuters
- The Guardian
- Washington Post
- CNN
- Ars Technica
- Wired
- TechCrunch

**Process**:
1. Fetches each RSS feed
2. Parses feed entries
3. Cleans HTML from content
4. Sanitizes filenames
5. Saves as `.txt` files to `data/documents/`

**Output**:
```
Downloading from NPR News...
  - Saved: npr_tech_layoffs_continue.txt
  - Saved: npr_climate_summit_update.txt
  ...
Downloaded 47 articles total.
```

---

## Advertisement & Event Loaders

### ingest_ads.py

Ingests advertisements from files.

**Usage**:
```bash
uv run python scripts/ingest_ads.py [OPTIONS] FILE
```

**Options**:
| Option | Description |
|--------|-------------|
| `--format` | File format: `json`, `csv`, `txt`, `html` |
| `--no-metadata` | Skip Claude metadata extraction |
| `--publisher` | Publisher name |

**JSON Format**:
```json
[
  {
    "product_name": "Widget Pro",
    "advertiser": "WidgetCorp",
    "description": "The best widget ever",
    "category": "Electronics",
    "price": 29.99,
    "original_price": 39.99,
    "discount_percent": 25,
    "valid_to": "2024-12-31",
    "url": "https://example.com/widget"
  }
]
```

---

### ingest_events.py

Ingests events from files.

**Usage**:
```bash
uv run python scripts/ingest_events.py [OPTIONS] FILE
```

**Options**:
| Option | Description |
|--------|-------------|
| `--format` | File format: `json`, `csv`, `txt`, `html` |
| `--no-metadata` | Skip Claude metadata extraction |
| `--publisher` | Publisher name |

**JSON Format**:
```json
[
  {
    "title": "Summer Festival",
    "description": "Annual community celebration",
    "location": "City Park",
    "address": "123 Main St",
    "event_date": "2024-07-15",
    "event_time": "14:00",
    "end_time": "22:00",
    "category": "Community",
    "price": 0,
    "url": "https://example.com/festival"
  }
]
```

---

### load_sample_ads.py

Loads hardcoded sample advertisements (Pipestone, MN businesses).

**Usage**:
```bash
uv run python scripts/load_sample_ads.py
```

**Sample Data**:
- Pipestone Hardware & Lumber
- Lange's Cafe
- Hy-Vee Pipestone
- Korkow Chevrolet
- Pipestone Floral
- Lewis Drug
- NAPA Auto Parts
- And more...

**Output**:
```
Loading sample advertisements...
Inserted: Pipestone Hardware & Lumber - Power Tools Sale
Inserted: Lange's Cafe - Friday Fish Fry
...
Loaded 10 sample advertisements.
```

---

### load_sample_events.py

Loads hardcoded sample events.

**Usage**:
```bash
uv run python scripts/load_sample_events.py
```

**Output**:
```
Loading sample events...
Inserted: Pipestone County Fair
Inserted: Summer Concert Series
...
Loaded 10 sample events.
```

---

## Database Management

### reset_db.py

Clears all databases and resets to clean state.

**Usage**:
```bash
uv run python scripts/reset_db.py [OPTIONS]
```

**Options**:
| Option | Description |
|--------|-------------|
| `--force`, `-f` | Skip confirmation prompt |

**What Gets Deleted**:
- `data/chroma_db/` directory (vector store)
- `data/articles.db` (SQLite database)
- `data/ingested_files.json` (deduplication tracker)

**Example**:
```bash
# Interactive (with confirmation)
uv run python scripts/reset_db.py

# Force without confirmation
uv run python scripts/reset_db.py --force
```

**Output**:
```
This will delete all indexed data:
  - ChromaDB vector store
  - SQLite database (articles, ads, events, conversations)
  - Ingestion tracking file

Are you sure? [y/N]: y

Deleted: data/chroma_db/
Deleted: data/articles.db
Deleted: data/ingested_files.json

Database reset complete.
```

---

### init_db.py

Initializes all database tables without adding data.

**Usage**:
```bash
uv run python scripts/init_db.py
```

**Tables Created**:
- `articles`
- `advertisements`
- `events`
- `conversations`
- `conversation_messages`
- `content_impressions`
- `url_clicks`

**Output**:
```
Initializing database tables...
Created: articles
Created: advertisements
Created: events
Created: conversations
Created: conversation_messages
Created: content_impressions
Created: url_clicks
Database initialization complete.
```

---

## Analytics

### analyze_conversations.py

CLI tool for conversation analysis and export.

**Usage**:
```bash
uv run python scripts/analyze_conversations.py [OPTIONS]
```

**Options**:
| Option | Description |
|--------|-------------|
| `--analyze` | Show detailed analysis |
| `--recent` | Show recent conversations |
| `--limit N` | Limit results (default: 10) |
| `--export FILE` | Export to JSON file |

**Examples**:
```bash
# Show conversation statistics
uv run python scripts/analyze_conversations.py --analyze

# View 20 most recent conversations
uv run python scripts/analyze_conversations.py --recent --limit 20

# Export all conversations to file
uv run python scripts/analyze_conversations.py --export conversations.json
```

**Analysis Output**:
```
Conversation Analysis
=====================

Statistics:
  Total conversations: 42
  Total messages: 156
  Avg messages/conversation: 3.71
  Most recent: 2024-12-07 10:30:00

Top Queries:
  1. "What's happening in technology?" (5 times)
  2. "Any sales today?" (3 times)
  3. "Events this weekend" (3 times)

Common Words:
  technology: 12
  events: 9
  sales: 7
  local: 6
```

---

## Deployment

### init.sh

Docker entrypoint script for container initialization.

**Usage** (automatic in Docker):
```bash
./scripts/init.sh
```

**Process**:
1. Initialize database tables
2. Check for pre-baked data
3. Start the application

---

### upload_to_spaces.py

Uploads data files to DigitalOcean Spaces for backup.

**Usage**:
```bash
uv run python scripts/upload_to_spaces.py
```

**Required Environment Variables**:
- `DO_SPACES_KEY` - Access key
- `DO_SPACES_SECRET` - Secret key
- `DO_SPACES_BUCKET` - Bucket name
- `DO_SPACES_REGION` - Region (e.g., `nyc3`)

**Files Uploaded**:
- `data/articles.db`
- `data/chroma_db/` (entire directory)
- `data/ingested_files.json`

---

## Common Workflows

### Fresh Setup
```bash
# 1. Initialize database
uv run python scripts/init_db.py

# 2. Download sample articles
uv run python scripts/download_samples.py

# 3. Ingest articles
uv run python scripts/ingest.py

# 4. Load sample ads and events
uv run python scripts/load_sample_ads.py
uv run python scripts/load_sample_events.py

# 5. Start application
uv run python src/chatbot.py
```

### Reset and Rebuild
```bash
# 1. Clear everything
uv run python scripts/reset_db.py --force

# 2. Re-initialize
uv run python scripts/init_db.py

# 3. Re-ingest
uv run python scripts/ingest.py
uv run python scripts/load_sample_ads.py
uv run python scripts/load_sample_events.py
```

### Add New Content
```bash
# Add new articles
cp new_article.txt data/documents/
uv run python scripts/ingest.py

# Add new ads from JSON
uv run python scripts/ingest_ads.py --format json new_ads.json

# Add new events from JSON
uv run python scripts/ingest_events.py --format json new_events.json
```

### Export Data
```bash
# Export conversations
uv run python scripts/analyze_conversations.py --export backup.json

# Backup to cloud
uv run python scripts/upload_to_spaces.py
```
