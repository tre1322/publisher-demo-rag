#!/usr/bin/env python
"""Reindex all articles from SQLite into the articles Chroma collection.

Reads existing article rows from the DB and rebuilds the vector index.
Safe to rerun — uses stable IDs derived from doc_id so duplicates are overwritten.

Usage:
    python scripts/reindex_articles.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

from sentence_transformers import SentenceTransformer

from src.core.config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL
from src.core.database import get_connection
from src.core.vector_store import get_articles_collection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def chunk_text(text: str, title: str = "") -> list[str]:
    """Chunk article text with title prefix for better search context."""
    prefix = f"{title}: " if title else ""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunk = prefix + " ".join(words[start:end])
        chunks.append(chunk)
        if end >= len(words):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def main() -> None:
    print("=" * 60)
    print("Article Reindex: SQLite → Chroma")
    print("=" * 60)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT doc_id, title, author, publish_date, source_file, location,
               section, cleaned_text, full_text, publisher
        FROM articles
        WHERE (cleaned_text IS NOT NULL AND length(cleaned_text) > 50)
           OR (full_text IS NOT NULL AND length(full_text) > 50)
    """)
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    print(f"\nTotal article rows in DB: {total}")

    if total == 0:
        print("No articles to reindex.")
        return

    print("Loading embedding model...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    collection = get_articles_collection()

    print(f"Articles collection has {collection.count()} existing chunks")
    print(f"\nProcessing {total} articles...\n")

    indexed = 0
    skipped = 0
    failed = 0

    for row in rows:
        article = dict(row)
        doc_id = article.get("doc_id", "")
        title = article.get("title", "Unknown")

        text = (article.get("cleaned_text") or article.get("full_text") or "").strip()
        if not text or len(text) < 50:
            logger.info(f"  SKIP: {title[:40]} ({doc_id[:12]}) — no usable text")
            skipped += 1
            continue

        chunks = chunk_text(text, title=title)
        if not chunks:
            skipped += 1
            continue

        try:
            embeddings = model.encode(chunks).tolist()

            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]

            metadatas = [
                {
                    "doc_id": doc_id,
                    "title": title[:200],
                    "publish_date": article.get("publish_date", "") or "",
                    "edition_date": article.get("publish_date", "") or "",
                    "author": article.get("author", "") or "Staff",
                    "source_file": article.get("source_file", "") or "",
                    "chunk_index": i,
                    "location": article.get("location", "") or "",
                    "subjects": article.get("section", "") or "",
                    "publisher": article.get("publisher", "") or "",
                }
                for i in range(len(chunks))
            ]

            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )

            indexed += 1
            logger.info(f"  OK: {title[:50]} — {len(chunks)} chunks")

        except Exception as e:
            failed += 1
            logger.error(f"  FAIL: {title[:40]} — {e}")

    print(f"\n{'=' * 60}")
    print(f"Reindex complete:")
    print(f"  Total scanned: {total}")
    print(f"  Indexed:       {indexed}")
    print(f"  Skipped:       {skipped}")
    print(f"  Failed:        {failed}")
    print(f"  Collection now has {collection.count()} chunks")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
