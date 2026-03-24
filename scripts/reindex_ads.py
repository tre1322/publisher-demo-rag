#!/usr/bin/env python
"""Reindex all advertisements from SQLite into the advertisements Chroma collection.

Reads existing ad rows from the DB and rebuilds the vector index.
Safe to rerun — uses stable IDs derived from ad_id so duplicates are overwritten.

Usage:
    python scripts/reindex_ads.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging

from sentence_transformers import SentenceTransformer

from src.core.config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL
from src.core.database import get_connection
from src.core.vector_store import get_ads_collection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Minimum text length to consider indexable
MIN_TEXT_LENGTH = 10


def get_best_text(ad: dict) -> str:
    """Get the best available text for an ad, in priority order.

    Priority:
    1. embedding_text (enriched)
    2. ocr_text
    3. cleaned_text
    4. raw_text
    5. description
    """
    for field in ("embedding_text", "ocr_text", "cleaned_text", "raw_text", "description"):
        val = ad.get(field)
        if val and len(val.strip()) >= MIN_TEXT_LENGTH:
            return val.strip()
    return ""


def chunk_text(text: str, advertiser: str = "") -> list[str]:
    """Chunk text with advertiser prefix, matching AdIngester.chunk_text()."""
    prefix = f"{advertiser} advertisement: " if advertiser else ""
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
    print("Advertisement Reindex: SQLite → Chroma")
    print("=" * 60)

    # Load all ads from DB
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM advertisements WHERE status = 'active' OR status IS NULL")
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    print(f"\nTotal ad rows in DB: {total}")

    if total == 0:
        print("No ads to reindex.")
        return

    # Initialize embedding model and collection
    print("Loading embedding model...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    collection = get_ads_collection()

    print(f"Ads collection has {collection.count()} existing chunks")
    print(f"\nProcessing {total} ads...\n")

    indexed = 0
    skipped = 0
    failed = 0

    for row in rows:
        ad = dict(row)
        ad_id = ad.get("ad_id", "")
        advertiser = ad.get("advertiser", "Unknown")

        # Get best available text
        text = get_best_text(ad)
        if not text:
            logger.info(f"  SKIP: {advertiser} ({ad_id[:8]}) — no usable text")
            skipped += 1
            continue

        # Build chunks
        chunks = chunk_text(text, advertiser=advertiser)
        if not chunks:
            skipped += 1
            continue

        try:
            # Generate embeddings
            embeddings = model.encode(chunks).tolist()

            # Stable IDs: ad_id + chunk index (idempotent — upserts on rerun)
            ids = [f"{ad_id}_{i}" for i in range(len(chunks))]

            metadatas = [
                {
                    "doc_id": ad_id,
                    "title": advertiser[:200],
                    "publish_date": "",
                    "author": advertiser,
                    "source_file": ad.get("source_file", ""),
                    "chunk_index": i,
                    "location": ad.get("location", "") or "",
                    "subjects": ad.get("ad_category", "") or "",
                    "content_type": "advertisement",
                }
                for i in range(len(chunks))
            ]

            # Upsert into collection (overwrites if IDs exist)
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )

            indexed += 1
            logger.info(
                f"  OK: {advertiser} ({ad_id[:8]}) — "
                f"{len(chunks)} chunks indexed"
            )

        except Exception as e:
            failed += 1
            logger.error(f"  FAIL: {advertiser} ({ad_id[:8]}) — {e}")

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
