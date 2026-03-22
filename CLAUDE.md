# Publisher RAG Demo - Development Guidelines

## Project Overview

RAG-based chatbot for querying a local publisher's news articles and editorials. Uses LlamaIndex for orchestration, ChromaDB for vector storage, and Gradio for the chat interface.

## Project Structure

```
publisher_rag_demo/
├── data/
│   ├── documents/          # Source documents (.txt, .pdf)
│   ├── chroma_db/          # ChromaDB storage
│   └── ingested_files.json # Deduplication tracking
├── src/
│   ├── __init__.py
│   ├── config.py           # Configuration
│   ├── ingestion.py        # Document loading and indexing
│   ├── query_engine.py     # Query processing
│   ├── chatbot.py          # Gradio interface
│   └── prompts.py          # Prompt templates
├── scripts/
│   ├── ingest.py           # CLI for batch ingestion
│   └── reset_db.py         # Clear database
├── tests/
├── pyproject.toml
├── CLAUDE.md
└── README.md
```

## Development Commands

- **Run chatbot**: `uv run python src/chatbot.py`
- **Ingest documents**: `uv run python scripts/ingest.py`
- **Reset database**: `uv run python scripts/reset_db.py`
- **Run tests**: `uv run pytest`
- **Format code**: `uv run ruff format .`
- **Lint code**: `uv run ruff check .`
- **Type check**: `uv run pyright`

## Key Dependencies

```bash
uv add llama-index chromadb anthropic sentence-transformers gradio pdfplumber python-dotenv
```

## Configuration

Environment variables in `.env`:

```bash
# Required
ANTHROPIC_API_KEY=your_api_key_here

# Optional - defaults shown
LLM_MODEL=claude-sonnet-4-20250514
LLM_TEMPERATURE=0.3
EMBEDDING_MODEL=all-MiniLM-L6-v2
CHROMA_PERSIST_DIR=./data/chroma_db
CHUNK_SIZE=1024
CHUNK_OVERLAP=200
RETRIEVAL_TOP_K=5
```

## Core Parameters

- **Chunk size**: 1024 tokens
- **Chunk overlap**: 200 tokens
- **Retrieval top-k**: 5
- **Similarity threshold**: 0.7
- **Max context**: 8000 tokens
- **Temperature**: 0.3
- **Collection name**: `publisher_main`

## ChromaDB Metadata Schema

Each chunk stores:
- `doc_id`: Unique document identifier
- `title`: Document title
- `publish_date`: ISO date (YYYY-MM-DD)
- `author`: Author name
- `source_file`: Original filename
- `chunk_index`: Position within document

## Implementation Guidelines

1. **Document Processing**
   - Use pdfplumber for PDF text extraction
   - Use LlamaIndex SentenceSplitter for chunking
   - Track ingested files in `ingested_files.json` for deduplication
   - Log failures and continue processing batch

2. **Query Processing**
   - Retrieve top 5 chunks
   - Truncate to 8000 tokens if exceeded (keep at least top 2)
   - Include source attribution with title, date, relevance score

3. **Error Handling**
   - Use try/except gracefully
   - Log failures, don't crash on single document errors
   - If answer not in context, respond "I don't have information about that"

4. **Code Style**
   - Use type hints throughout
   - Use pathlib for file paths (cross-platform)
   - Keep functions focused and small
   - Add TODO comments for future improvements

## Testing Requirements

- Ingest sample documents successfully
- Query for specific article content (verify accuracy)
- Query for non-existent info (verify appropriate response)
- Verify source citations are correct
- Test both text and PDF document types
- Response time < 3 seconds for typical queries

## Success Criteria

1. Successfully ingest 50+ documents
2. Answer factual queries accurately
3. Sources correctly cited
4. Chat interface functional and responsive
5. System stable for demo purposes
