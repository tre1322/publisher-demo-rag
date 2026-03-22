"""Core module for shared functionality."""

from src.core.config import (
    ANTHROPIC_API_KEY,
    CHROMA_PERSIST_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    DATA_DIR,
    DOCUMENTS_DIR,
    EMBEDDING_MODEL,
    INGESTED_FILES_PATH,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_CONTEXT_TOKENS,
    PROJECT_ROOT,
    RETRIEVAL_TOP_K,
    SIMILARITY_THRESHOLD,
)
from src.core.database import get_connection, init_all_tables

__all__ = [
    "ANTHROPIC_API_KEY",
    "CHROMA_PERSIST_DIR",
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "COLLECTION_NAME",
    "DATA_DIR",
    "DOCUMENTS_DIR",
    "EMBEDDING_MODEL",
    "INGESTED_FILES_PATH",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "MAX_CONTEXT_TOKENS",
    "PROJECT_ROOT",
    "RETRIEVAL_TOP_K",
    "SIMILARITY_THRESHOLD",
    "get_connection",
    "init_all_tables",
]
