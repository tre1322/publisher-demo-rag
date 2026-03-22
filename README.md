# Publisher RAG Demo

A RAG-based chatbot for querying a local publisher's news articles, advertisements, and local events. Features an intelligent search agent that routes queries to the appropriate search tool, with support for semantic search, metadata filtering, and conversation history.

## Features

- **Smart Search Agent** - Automatically selects the best search strategy for each query
- **Hybrid Search** - Combines semantic (RAG) and metadata-based search
- **Multiple Data Sources** - News articles, product advertisements, and local events
- **Conversation Memory** - Maintains context across chat turns for follow-up questions
- **Source Citations** - Responses include hyperlinked source references
- **Gradio Interface** - Simple, responsive chat UI

## Architecture

```
┌─────────────────┐
│   Gradio Chat   │
│    Interface    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Query Engine   │◄──── Conversation History
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Search Agent   │──── Tool Selection
└────────┬────────┘
         │
    ┌────┴────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│Semantic│ │Metadata│ │  Ads  │ │Events │
│Search │ │Search │ │Search │ │Search │
└───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘
    │         │         │         │
    ▼         ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌─────────────────┐
│ChromaDB│ │SQLite │ │   SQLite DB     │
│Vectors │ │Articles│ │ Ads & Events   │
└───────┘ └───────┘ └─────────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Create .env file with your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 3. Download sample articles (optional)
uv run python scripts/download_samples.py

# 4. Ingest documents
uv run python scripts/ingest.py

# 5. Load sample data
uv run python scripts/load_sample_ads.py
uv run python scripts/load_sample_events.py

# 6. Launch chatbot
uv run python src/chatbot.py
```

The chatbot will be available at http://localhost:7860

## Docker Setup

### Using Docker Compose (Recommended)

```bash
# 1. Create .env file with your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 2. Build and start the container
# Note: First build takes ~5-10 minutes (downloads and ingests articles)
docker-compose up -d

# 3. Access the chatbot at http://localhost:7860
# Articles are prebaked, ads/events load automatically on startup

# View logs
docker-compose logs -f

# Stop the container
docker-compose down
```

### Using Docker CLI

```bash
# Build the image (takes ~5-10 minutes, downloads and ingests articles)
docker build -t publisher-rag-demo .

# Run the container
docker run -d \
  --name publisher-rag-demo \
  -p 7860:7860 \
  -e ANTHROPIC_API_KEY=your_api_key_here \
  -v $(pwd)/data/chroma_db:/app/data/chroma_db \
  -v $(pwd)/data/articles.db:/app/data/articles.db \
  -v $(pwd)/data/documents:/app/data/documents \
  publisher-rag-demo

# Access at http://localhost:7860
# Articles are prebaked, ads/events load automatically

# Stop the container
docker stop publisher-rag-demo
docker rm publisher-rag-demo
```

### Docker Notes

- **Prebaked Articles**: Sample articles are downloaded and ingested during Docker build
- **Container Port**: Exposes port 7860 for the Gradio interface
- **Data Persistence**: Use volume mounts to persist databases and documents (optional)
- **API Key**: Set `ANTHROPIC_API_KEY` via environment variable or `.env` file
- **Startup**: Runs `scripts/init.sh` which loads ads/events and starts chatbot
- **Health Checks**: Service readiness is verified automatically
- **Build Time**: Initial Docker build takes longer (~5-10 minutes) due to article ingestion
- **Startup Time**: Container starts in ~10-15 seconds (articles are prebaked)

## DigitalOcean App Platform Deployment

Deploy as a serverless container on DigitalOcean App Platform with automatic initialization.

### Quick Deploy

1. **Fork/Clone this repository to GitHub**

2. **Create new App in DigitalOcean**
   ```bash
   # Via doctl CLI
   doctl apps create --spec .do/app.yaml

   # Or use the DigitalOcean console:
   # Apps → Create App → GitHub → Select repository
   ```

3. **Set Environment Variables**
   - In App settings, add `ANTHROPIC_API_KEY` as a secret
   - Optional: Override other settings (model, temperature, etc.)

4. **Deploy**
   - App Platform automatically builds and deploys on push to main
   - Initialization runs automatically (downloads samples, ingests, loads data)
   - App is available at your assigned `.ondigitalocean.app` URL

### Configuration

The `.do/app.yaml` file configures:
- **Service**: Web service on port 7860
- **Instance**: basic-s (512MB RAM, 1 vCPU) - upgrade if needed
- **Auto-deploy**: Deploys on git push to main branch
- **Health checks**: Ensures service is running
- **Environment variables**: Claude API key and configuration

### How It Works

**During Docker Build** (one-time, baked into image):
1. Downloads 50 sample news articles from RSS feeds
2. Initializes SQLite database tables
3. Ingests articles into ChromaDB (without metadata extraction to save API calls)
4. Articles, embeddings, and database are baked into the container image

**During Container Startup** (~10-15 seconds):
1. Verifies prebaked data is present
2. Loads sample ads and events to SQLite
3. Starts Gradio chatbot on port 7860

**Why Prebaking?**
- Eliminates ~150 seconds of runtime initialization
- Saves Claude API calls (no metadata extraction needed during deployment)
- Faster container startup for demo purposes
- Health checks pass immediately

### Important Notes

**Ephemeral Storage**
- All data (ChromaDB vectors, SQLite database) is stored in-container
- Data is **reset on each deployment** - this is intentional for demos
- Startup time: ~10-15 seconds (articles are prebaked in the image)
- For persistent data, consider upgrading to managed Postgres + Spaces

**Scaling**
- Start with `basic-s` (512MB RAM)
- Upgrade to `basic-m` (1GB RAM) or higher if running out of memory
- ML models (sentence-transformers) can be memory-intensive

**Costs**
- `basic-s`: ~$5/month for 512MB RAM
- `basic-m`: ~$12/month for 1GB RAM
- Billed per second of usage

### Manual Deployment Steps

If not using the `.do/app.yaml`:

1. **Create App** in DigitalOcean console
2. **Connect GitHub** repository
3. **Configure Build**:
   - Type: Dockerfile
   - Dockerfile path: `Dockerfile`
   - HTTP port: `7860`
4. **Add Environment Variables**:
   - `ANTHROPIC_API_KEY` (secret)
5. **Deploy** and wait for build to complete

### Monitoring

```bash
# View logs
doctl apps logs <app-id> --type run

# List apps
doctl apps list

# Get app info
doctl apps get <app-id>
```

## Search Capabilities

The search agent has access to five tools and automatically selects the best one based on your query:

### 1. Semantic Search
Best for specific questions about article content.
- "What did the president say about the economy?"
- "Explain the new AI regulations"

### 2. Metadata Search
Filter articles by date, author, location, or subject.
- "Articles from last week"
- "News by John Smith"

### 3. Hybrid Search (Preferred for News)
Combines semantic search with metadata filters.
- "What's happening in politics?" → Uses subject filter
- "Technology news from yesterday"
- "News about Ukraine"

### 4. Advertisement Search
Find product deals, sales, and discounts.
- "Any deals?" / "What's on sale?"
- "Electronics under $100"
- "Fashion discounts"

### 5. Event Search
Find local events and activities.
- "Events this weekend"
- "Free concerts"
- "Sports events downtown"

## Example Queries

### News Queries
```
"What's happening in technology?"
"Tell me about recent political news"
"Any science articles from this week?"
"News about climate change"
```

### Shopping Queries
```
"What's on sale?"
"Electronics deals"
"Products under $50"
"Any discounts on headphones?"
```

### Event Queries
```
"What events are happening this weekend?"
"Free events downtown"
"Any concerts coming up?"
"Sports events this week"
"Food festivals near me"
```

### Follow-up Queries
The chatbot maintains conversation context:
```
User: "What's happening in technology?"
Bot: [Response about tech news]
User: "Tell me more about that"
Bot: [Expanded response using conversation context]
```

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic API key |
| `LLM_MODEL` | claude-sonnet-4-20250514 | Claude model to use |
| `LLM_TEMPERATURE` | 0.3 | Response temperature |
| `EMBEDDING_MODEL` | all-MiniLM-L6-v2 | Sentence transformer model |
| `CHUNK_SIZE` | 1024 | Tokens per chunk |
| `CHUNK_OVERLAP` | 200 | Overlap between chunks |
| `RETRIEVAL_TOP_K` | 5 | Number of chunks to retrieve |
| `SIMILARITY_THRESHOLD` | 0.7 | Minimum similarity score |

## Usage

### Adding Documents

Place your `.txt` and `.pdf` files in the `data/documents/` directory.

### Ingesting Documents

```bash
# Run ingestion (extracts rich metadata using Claude)
uv run python scripts/ingest.py

# Run ingestion without metadata extraction (no API calls)
uv run python scripts/ingest.py --no-metadata

# Check collection stats
uv run python scripts/ingest.py --stats
```

### Loading Sample Data

```bash
# Load sample advertisements
uv run python scripts/load_sample_ads.py

# Load sample events
uv run python scripts/load_sample_events.py
```

### Ingesting Ads from Files

Place ad files in `data/ads/` directory. Supports JSON, CSV, plain text, and HTML formats.

```bash
# Ingest all ads from data/ads/
uv run python scripts/ingest_ads.py

# Ingest a specific file
uv run python scripts/ingest_ads.py --file path/to/ad.txt

# Ingest without AI metadata extraction
uv run python scripts/ingest_ads.py --no-metadata
```

**Supported formats:**

**JSON** (`ads.json`):
```json
[
  {
    "product_name": "Winter Sale",
    "advertiser": "Local Hardware",
    "description": "20% off all tools",
    "price": 79.99,
    "original_price": 99.99,
    "valid_to": "2025-12-31"
  }
]
```

**CSV** (`ads.csv`):
```csv
product_name,advertiser,description,price,original_price,valid_to
Winter Sale,Local Hardware,20% off all tools,79.99,99.99,2025-12-31
```

**Plain Text** (`ad.txt`) - AI extracts all fields:
```
Joe's Diner Holiday Special!
Buy one get one free on all burgers.
Valid through December 25th.
$12.99
```

**HTML** (`ad.html`) - AI extracts all fields from rendered text:
```html
<div class="ad">
  <h2>Joe's Diner Holiday Special!</h2>
  <p>Buy one get one free on all burgers.</p>
  <p>Valid through December 25th. <strong>$12.99</strong></p>
</div>
```

### Ingesting Events from Files

Place event files in `data/events/` directory. Supports JSON, CSV, plain text, and HTML formats.

```bash
# Ingest all events from data/events/
uv run python scripts/ingest_events.py

# Ingest a specific file
uv run python scripts/ingest_events.py --file path/to/event.txt

# Ingest without AI metadata extraction
uv run python scripts/ingest_events.py --no-metadata
```

**Supported formats:**

**JSON** (`events.json`):
```json
[
  {
    "title": "Holiday Parade",
    "location": "Main Street",
    "address": "Pipestone, MN 56164",
    "event_date": "2025-12-15",
    "event_time": "14:00",
    "price": 0
  }
]
```

**CSV** (`events.csv`):
```csv
title,location,address,event_date,event_time,price
Holiday Parade,Main Street,Pipestone MN,2025-12-15,14:00,0
```

**Plain Text** (`event.txt`) - AI extracts all fields:
```
Pipestone Holiday Parade
Saturday, December 14th at 2:00 PM

Join us on Main Street for our annual holiday parade!
Free admission - bring the whole family!
```

**HTML** (`event.html`) - AI extracts all fields from rendered text:
```html
<article class="event">
  <h1>Pipestone Holiday Parade</h1>
  <time>Saturday, December 14th at 2:00 PM</time>
  <p>Join us on Main Street for our annual holiday parade!</p>
  <p class="price">Free admission - bring the whole family!</p>
</article>
```

### Launching the Chatbot

```bash
uv run python src/chatbot.py
```

### Resetting the Database

```bash
uv run python scripts/reset_db.py
```

### Analyzing Conversations

All user conversations are automatically logged to the database for analysis and service improvement.

**Option 1: Web Dashboard (Recommended)**

Launch the admin dashboard on port 7861:

```bash
# Set admin password (or use default 'admin')
export ADMIN_PASSWORD=your_secure_password

# Start admin dashboard
./scripts/run_admin.sh

# Or run directly
uv run python src/admin_dashboard.py

# Access at http://localhost:7861
# Username: admin
# Password: (from ADMIN_PASSWORD env var)
```

**Features:**
- 📊 Real-time statistics (total conversations, messages, averages)
- 📈 Query analytics (most common queries and words)
- 💬 Recent conversation viewer with previews
- 📥 Export to JSON

**Option 2: Command Line**

```bash
# View overall statistics
uv run python scripts/analyze_conversations.py

# Analyze query patterns and common words
uv run python scripts/analyze_conversations.py --analyze

# Show recent conversations
uv run python scripts/analyze_conversations.py --recent --limit 20

# Export conversations to JSON for external analysis
uv run python scripts/analyze_conversations.py --export conversations.json --limit 100
```

**Use cases:**
- Understand what topics users search for most
- Identify content gaps in your article collection
- Improve search relevance and response quality
- Track popular product categories and events

## Project Structure

```
publisher_rag_demo/
├── data/
│   ├── documents/          # Source documents (.txt, .pdf, .rtf)
│   ├── ads/                # Ad files for ingestion (.json, .csv, .txt)
│   ├── events/             # Event files for ingestion (.json, .csv, .txt)
│   ├── chroma_db/          # ChromaDB vector storage
│   ├── articles.db         # SQLite database
│   └── ingested_files.json # Deduplication tracking
├── src/
│   ├── config.py           # Configuration settings
│   ├── database.py         # SQLite database operations
│   ├── ingestion.py        # Document loading and indexing
│   ├── metadata_extractor.py # Claude-based metadata extraction for articles
│   ├── metadata_extractor_ads.py # Claude-based metadata extraction for ads
│   ├── metadata_extractor_events.py # Claude-based metadata extraction for events
│   ├── search_tools.py     # Search tool implementations
│   ├── search_agent.py     # Tool-using search agent
│   ├── query_engine.py     # Query processing and response generation
│   ├── chatbot.py          # Gradio chatbot interface (port 7860)
│   ├── admin_dashboard.py  # Admin analytics dashboard (port 7861)
│   ├── prompts.py          # Prompt templates
│   └── modules/
│       ├── articles/       # Article database operations
│       ├── advertisements/ # Ad database operations
│       ├── events/         # Event database operations
│       └── conversations/  # Conversation logging
├── scripts/
│   ├── ingest.py           # CLI for article ingestion
│   ├── ingest_ads.py       # CLI for ad ingestion from files
│   ├── ingest_events.py    # CLI for event ingestion from files
│   ├── download_samples.py # Download sample news articles
│   ├── load_sample_ads.py  # Load hardcoded sample advertisements
│   ├── load_sample_events.py # Load hardcoded sample events
│   ├── analyze_conversations.py # Analyze conversation logs (CLI)
│   ├── run_admin.sh        # Launch admin dashboard
│   └── reset_db.py         # Clear all databases
├── docs/
│   └── DATA_STRUCTURE.md   # Database schema documentation
└── tests/
```

## Development

```bash
# Run tests
uv run pytest

# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type check
uv run pyright
```

## Key Components

### Search Agent (`src/search_agent.py`)
Uses Claude to analyze queries and select the appropriate search tool. Includes date context (today, yesterday, this weekend, etc.) for time-based queries.

### Query Engine (`src/query_engine.py`)
Orchestrates the search process, formats context, calls Claude for response generation, and maintains conversation history (up to 10 turns).

### Database (`src/database.py`)
SQLite database with five tables:
- **articles** - News article metadata (title, author, date, location, subjects)
- **advertisements** - Product ads (name, price, discount, category)
- **events** - Local events (title, date, time, location, category, price)
- **conversations** - Chat session tracking (session_id, started_at, ended_at, message_count)
- **conversation_messages** - Individual message turns (conversation_id, role, content, timestamp, metadata)

### Search Tools (`src/search_tools.py`)
Implements five search methods:
- `semantic_search()` - Vector similarity search in ChromaDB
- `metadata_search()` - SQL filtering on article metadata
- `hybrid_search()` - Combined semantic + metadata search
- `search_advertisements()` - Filter ads by category, price, sale status
- `search_events()` - Filter events by category, location, date, price

## Developer Documentation

For detailed technical documentation, see the [docs/](docs/) directory:

- [Architecture & Overview](docs/README.md) - System architecture, request flow, project structure
- [API Reference](docs/API.md) - HTTP endpoints, request/response formats
- [Modules & Components](docs/MODULES.md) - Core components, content modules, database schemas
- [Frontend Guide](docs/FRONTEND.md) - Chat UI, Admin dashboard, Embeddable widget
- [CLI Scripts](docs/SCRIPTS.md) - Command-line tools and utilities
- [Configuration](docs/CONFIGURATION.md) - Environment variables and settings

## License

MIT
