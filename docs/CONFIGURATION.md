# Configuration Reference

This document covers all configuration options for the Publisher RAG Demo.

## Environment Variables

Configuration is loaded from `.env` file or environment variables.

### Required

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key for Claude access |

### LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Claude model for responses |
| `LLM_TEMPERATURE` | `0.3` | Response temperature (0.0-1.0) |

**Model Options**:
- `claude-sonnet-4-20250514` - Balanced speed/quality (recommended)
- `claude-opus-4-20250514` - Highest quality, slower
- `claude-haiku-3-20240307` - Fastest, lower quality

**Temperature Guide**:
- `0.0-0.3` - More deterministic, factual responses
- `0.4-0.7` - Balanced creativity
- `0.8-1.0` - More creative, varied responses

### Embedding Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model |

**Model Options**:
- `all-MiniLM-L6-v2` - Fast, 384 dimensions (recommended)
- `all-mpnet-base-v2` - Higher quality, 768 dimensions
- `paraphrase-MiniLM-L6-v2` - Optimized for paraphrase detection

### Chunking Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_SIZE` | `1024` | Tokens per chunk |
| `CHUNK_OVERLAP` | `200` | Overlapping tokens between chunks |

**Guidelines**:
- Larger chunks = more context per retrieval, fewer chunks total
- Smaller chunks = more precise retrieval, may lose context
- Overlap prevents cutting sentences mid-thought

**Recommended Settings**:
| Use Case | Size | Overlap |
|----------|------|---------|
| News articles | 1024 | 200 |
| Technical docs | 512 | 100 |
| Long-form content | 2048 | 400 |

### Retrieval Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `RETRIEVAL_TOP_K` | `5` | Chunks to retrieve per query |
| `SIMILARITY_THRESHOLD` | `0.7` | Minimum similarity score (0-1) |
| `MAX_CONTEXT_TOKENS` | `8000` | Maximum context window size |

**Guidelines**:
- Higher `TOP_K` = more comprehensive, higher API costs
- Higher threshold = fewer but more relevant results
- Context is truncated if exceeds `MAX_CONTEXT_TOKENS`

### Query Transformation (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_QUERY_TRANSFORMATION` | `false` | Enable query expansion |
| `MAX_TRANSFORMED_QUERIES` | `3` | Max expanded queries |

When enabled, user queries are transformed into multiple search queries for broader coverage.

### ChromaDB Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROMA_PERSIST_DIR` | `./data/chroma_db` | Vector store location |
| `CHROMA_COLLECTION_NAME` | `publisher_main` | Collection name |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:7860` | Base URL for tracking redirects |
| `ADMIN_PASSWORD` | `admin` | Admin dashboard password |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level |

**Log Levels**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

---

## Example .env File

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-your-key-here

# LLM Configuration
LLM_MODEL=claude-sonnet-4-20250514
LLM_TEMPERATURE=0.3

# Embedding Model
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Document Chunking
CHUNK_SIZE=1024
CHUNK_OVERLAP=200

# Retrieval Settings
RETRIEVAL_TOP_K=5
SIMILARITY_THRESHOLD=0.7
MAX_CONTEXT_TOKENS=8000

# Query Transformation (optional)
ENABLE_QUERY_TRANSFORMATION=false
MAX_TRANSFORMED_QUERIES=3

# Storage
CHROMA_PERSIST_DIR=./data/chroma_db
CHROMA_COLLECTION_NAME=publisher_main

# Server
BASE_URL=http://localhost:7860
ADMIN_PASSWORD=your-secure-password

# Logging
LOG_LEVEL=INFO
```

---

## Path Constants

Defined in `src/core/config.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `PROJECT_ROOT` | Project directory | Absolute path to project |
| `DATA_DIR` | `{PROJECT_ROOT}/data` | Data storage directory |
| `DOCUMENTS_DIR` | `{DATA_DIR}/documents` | Source documents for ingestion |
| `CHROMA_PERSIST_DIR` | `{DATA_DIR}/chroma_db` | ChromaDB storage |
| `INGESTED_FILES_PATH` | `{DATA_DIR}/ingested_files.json` | Deduplication tracking |

---

## Database Configuration

### SQLite

- **Path**: `data/articles.db`
- **Row Factory**: `sqlite3.Row` (dict-like access)
- **Connection**: New connection per operation

### ChromaDB

- **Persist Directory**: Configured via `CHROMA_PERSIST_DIR`
- **Collection**: Single collection per deployment
- **Distance Function**: Cosine similarity (default)

---

## Conversation Limits

| Setting | Value | Description |
|---------|-------|-------------|
| `MAX_HISTORY_TURNS` | 10 | Conversation turns kept in context |
| Empty result threshold | 3 | Suggests help after N empty results |

---

## Production Recommendations

### Security

```bash
# Use strong admin password
ADMIN_PASSWORD=your-very-secure-password-here

# Set specific base URL
BASE_URL=https://your-domain.com
```

### Performance

```bash
# Balanced for production
LLM_TEMPERATURE=0.2
RETRIEVAL_TOP_K=5
MAX_CONTEXT_TOKENS=6000

# Faster model for high traffic
LLM_MODEL=claude-haiku-3-20240307
```

### Cost Optimization

```bash
# Reduce API calls
ENABLE_QUERY_TRANSFORMATION=false
RETRIEVAL_TOP_K=3
MAX_CONTEXT_TOKENS=4000

# Use smaller model
LLM_MODEL=claude-haiku-3-20240307
```

### Quality Focus

```bash
# Higher quality settings
LLM_MODEL=claude-sonnet-4-20250514
LLM_TEMPERATURE=0.3
RETRIEVAL_TOP_K=7
SIMILARITY_THRESHOLD=0.65
MAX_CONTEXT_TOKENS=10000
ENABLE_QUERY_TRANSFORMATION=true
```

---

## Docker Environment

When running in Docker, set environment variables via:

### docker-compose.yml

```yaml
services:
  chatbot:
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}
      - LOG_LEVEL=INFO
```

### Command Line

```bash
docker run -e ANTHROPIC_API_KEY=sk-ant-xxx \
           -e ADMIN_PASSWORD=secure123 \
           publisher-rag-demo
```

---

## Validation

On startup, the application validates:

1. `ANTHROPIC_API_KEY` is set
2. Numeric values are valid (temperature, top_k, etc.)
3. Paths exist or can be created
4. Embedding model is available

Invalid configuration raises `ValueError` with specific error message.
