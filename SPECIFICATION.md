# Local Publisher Chatbot - Technical Specification

## Version 1.0 - Single Publisher MVP

## Overview

RAG-based chatbot for querying a local publisher's news articles and editorials. Uses LlamaIndex for orchestration, ChromaDB for vector storage, and Gradio for the chat interface.

## Core Objectives

1. Ingest text and PDF documents from a single publisher
2. Enable natural language queries about content
3. Provide accurate responses with source citations
4. Simple Gradio chat interface

## Architecture

```
┌─────────────────┐
│     Gradio      │
│  Chat Interface │
└────────┬────────┘
         │
┌────────▼────────┐
│  Query Engine   │
│  (LlamaIndex)   │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
┌───▼──┐  ┌──▼─────┐
│Claude│  │Chroma  │
│ API  │  │  DB    │
└──────┘  └────────┘
```

## Technology Stack

- Python 3.11+
- LlamaIndex
- ChromaDB (persistent local storage)
- Anthropic Claude API (claude-sonnet-4-20250514)
- sentence-transformers (all-MiniLM-L6-v2)
- Gradio for chat UI
- pdfplumber for PDF extraction
- uv for package management

## Data Model

### ChromaDB Metadata (per chunk)

- `doc_id`: Unique document identifier
- `title`: Document title
- `publish_date`: ISO date (YYYY-MM-DD)
- `author`: Author name
- `source_file`: Original filename
- `chunk_index`: Position within document

**Collection name:** `publisher_main`

## Core Components

### 1. Document Ingestion

**Input:** Text files (.txt) and PDFs (.pdf) from `./data/documents/`

**Process:**

1. Extract text from PDFs using pdfplumber
2. Chunk documents (1024 tokens, 200 overlap)
3. Generate embeddings with sentence-transformers
4. Store in ChromaDB with metadata
5. Track ingested files to prevent duplicates

**Config:**

- Chunk size: 1024 tokens
- Chunk overlap: 200 tokens
- Embedding model: all-MiniLM-L6-v2

### 2. Query Engine

**Process:**

1. Convert query to embedding
2. Retrieve top 5 similar chunks from ChromaDB
3. Build prompt: system instructions + context + query
4. Call Claude API
5. Return response with source citations

**Config:**

- Retrieval top-k: 5
- Similarity threshold: 0.7
- Max context: 8000 tokens
- LLM model: claude-sonnet-4-20250514
- Temperature: 0.3

### 3. Gradio Chat Interface

**Features:**

- Simple chat interface using `gr.ChatInterface`
- Message history handled automatically
- Display sources below each response
- Shows article titles, dates, relevance scores

**Implementation:**

```python
def respond(message, history):
    response = query_engine.query(message)
    return format_response_with_sources(response)

demo = gr.ChatInterface(
    fn=respond,
    title="Publisher News Assistant",
    description="Ask questions about our articles"
)
demo.launch()
```

## File Structure

```
local-publisher-chatbot/
├── README.md
├── pyproject.toml          # uv project config
├── .env
├── .gitignore
│
├── data/
│   ├── documents/          # Source documents
│   ├── chroma_db/          # ChromaDB storage
│   └── ingested_files.json # Deduplication tracking
│
├── src/
│   ├── __init__.py
│   ├── config.py           # Configuration
│   ├── ingestion.py        # Document loading and indexing
│   ├── query_engine.py     # Query processing
│   ├── chatbot.py          # Gradio interface
│   └── prompts.py          # Prompt templates
│
└── scripts/
    ├── ingest.py           # CLI for batch ingestion
    └── reset_db.py         # Clear database
```

## Configuration (.env)

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

## Implementation Details

### Document Processing

1. **PDF extraction:** Use pdfplumber for text extraction
2. **Chunking:** LlamaIndex SentenceSplitter with 1024/200 tokens
3. **Deduplication:** Track filenames in ingested_files.json
4. **Error handling:** Log failures, continue processing batch

### Query Processing

**Prompt template:**

```
You are a helpful assistant for a local news publisher. Answer questions based on the provided article excerpts.

Rules:
- Only use information from the provided context
- If answer isn't in context, say "I don't have information about that"
- Cite sources by mentioning article titles
- Be concise but complete

Context:
{context}

Question: {question}

Answer:
```

**Context management:**

- Retrieve top 5 chunks
- If exceeds 8000 tokens, truncate oldest chunks
- Always include at least top 2 chunks

### Source Attribution

Include for each response:

- Article title
- Publication date
- Relevance score
- Brief excerpt from retrieved chunk

## Setup and Installation

### Prerequisites

- Python 3.11+
- uv package manager ([install](https://github.com/astral-sh/uv))
- Anthropic API key

### Installation

```bash
# Clone/create project directory
mkdir local-publisher-chatbot
cd local-publisher-chatbot

# Initialize uv project
uv init

# Add dependencies
uv add llama-index chromadb anthropic sentence-transformers gradio pdfplumber python-dotenv

# Create directory structure
mkdir -p data/documents data/chroma_db src scripts

# Create .env file with API key
echo "ANTHROPIC_API_KEY=your_key_here" > .env

# Place documents in data/documents/

# Run ingestion
uv run python scripts/ingest.py

# Launch chatbot
uv run python src/chatbot.py
```

### Key Dependencies

- llama-index
- chromadb
- anthropic
- sentence-transformers
- gradio
- pdfplumber
- python-dotenv

## Testing

### Basic Tests

- Ingest 10+ sample documents successfully
- Query for specific article content (verify accuracy)
- Query for non-existent info (should say "I don't have information")
- Verify source citations are correct
- Test on different document types (text and PDF)

### Manual Validation

- Response time < 3 seconds for typical queries
- Accurate answers for factual questions
- Proper source attribution
- Gradio interface functional

## Success Criteria

1. Successfully ingest 50+ documents
2. Answer factual queries accurately (manual evaluation)
3. Sources correctly cited
4. Chat interface functional and responsive
5. System stable for demo purposes

## Implementation Priority

**Week 1:**

- Set up project with uv
- Implement document ingestion (text + PDF)
- Create ChromaDB collection and test indexing

**Week 2:**

- Build query engine with LlamaIndex
- Integrate Claude API
- Create Gradio chat interface

**Week 3:**

- Source attribution and formatting
- Error handling and logging
- Testing with sample data

**Week 4:**

- Bug fixes and polish
- Documentation
- Prepare demo

---

## Notes for Implementation

- Use type hints throughout
- Handle errors gracefully with try/except
- Test components independently before integration
- Keep functions focused and small
- Add TODO comments for future improvements
- Use pathlib for file paths (cross-platform)
