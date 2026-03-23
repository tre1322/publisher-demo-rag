"""Centralized ChromaDB client and collection management.

Provides a single PersistentClient instance and named collection accessors
for articles, advertisements, and the legacy publisher_main collection.
"""

from __future__ import annotations

import logging

import chromadb

from src.core.config import (
    ADS_COLLECTION,
    ARTICLES_COLLECTION,
    CHROMA_PERSIST_DIR,
    COLLECTION_NAME,
)

logger = logging.getLogger(__name__)

_client: chromadb.PersistentClient | None = None

_COSINE_META = {"hnsw:space": "cosine"}


def get_chroma_client() -> chromadb.PersistentClient:
    """Get or create the singleton ChromaDB PersistentClient."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
        logger.info(f"ChromaDB client initialized at {CHROMA_PERSIST_DIR}")
    return _client


def get_articles_collection() -> chromadb.Collection:
    """Get or create the articles vector collection."""
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=ARTICLES_COLLECTION, metadata=_COSINE_META
    )
    logger.info(
        f"Articles collection '{ARTICLES_COLLECTION}' ready "
        f"with {collection.count()} chunks"
    )
    return collection


def get_ads_collection() -> chromadb.Collection:
    """Get or create the advertisements vector collection."""
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=ADS_COLLECTION, metadata=_COSINE_META
    )
    logger.info(
        f"Ads collection '{ADS_COLLECTION}' ready "
        f"with {collection.count()} chunks"
    )
    return collection


def get_legacy_collection() -> chromadb.Collection | None:
    """Get the legacy publisher_main collection for backward-compat reads.

    Returns None if the collection doesn't exist (fresh deploy).
    """
    client = get_chroma_client()
    try:
        collection = client.get_collection(name=COLLECTION_NAME)
        count = collection.count()
        if count > 0:
            logger.info(
                f"Legacy collection '{COLLECTION_NAME}' found with {count} chunks"
            )
            return collection
        return None
    except Exception:
        return None
