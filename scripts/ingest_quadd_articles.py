"""Bridge script: Ingest articles from the quadd extraction pipeline into publisher-demo-rag.

Reads assembled/stitched articles from quadd's SQLite database and:
1. Inserts them into publisher-demo-rag's articles table
2. Chunks and embeds them into ChromaDB for semantic search

Usage:
    python scripts/ingest_quadd_articles.py [--quadd-db PATH] [--edition-id ID] [--publisher NAME]

Examples:
    # Ingest all articles from quadd for edition 31
    python scripts/ingest_quadd_articles.py --edition-id 31

    # Ingest from a specific quadd database path
    python scripts/ingest_quadd_articles.py --quadd-db C:/Users/trevo/quadd/data/articles.db --edition-id 31
"""

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentence_transformers import SentenceTransformer

from src.core.config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL

# Create tables directly to avoid circular import issues
def _ensure_tables():
    """Create core tables without triggering module-level imports."""
    from src.core.database import get_connection
    conn = get_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS articles (
        doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT,
        publish_date TEXT, source_file TEXT NOT NULL, location TEXT,
        subjects TEXT, summary TEXT, url TEXT, publisher TEXT,
        edition_id INTEGER, section TEXT, start_page INTEGER,
        continuation_pages TEXT, full_text TEXT, cleaned_text TEXT,
        subheadline TEXT, organization_id INTEGER, publication_id INTEGER,
        status TEXT DEFAULT 'parsed', duplicate_flag INTEGER DEFAULT 0,
        needs_review INTEGER DEFAULT 1, parse_metadata_json TEXT,
        updated_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS advertisements (
        ad_id TEXT PRIMARY KEY, product_name TEXT, advertiser TEXT,
        description TEXT, category TEXT, price TEXT, original_price TEXT,
        discount_percent REAL, valid_from TEXT, valid_to TEXT, url TEXT,
        raw_text TEXT, publisher TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        edition_id INTEGER, organization_id INTEGER, publication_id INTEGER,
        page INTEGER, headline TEXT, cleaned_text TEXT, status TEXT,
        checksum TEXT, parse_metadata_json TEXT, ocr_text TEXT,
        embedding_text TEXT, ad_category TEXT, location TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY, title TEXT, description TEXT,
        location TEXT, address TEXT, event_date TEXT, event_time TEXT,
        end_date TEXT, end_time TEXT, category TEXT, price TEXT, url TEXT,
        raw_text TEXT, publisher TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

_ensure_tables()

from src.core.vector_store import get_articles_collection  # noqa: E402
from src.modules.articles.database import insert_edition_article  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Default quadd database path
DEFAULT_QUADD_DB = Path("C:/Users/trevo/quadd/data/articles.db")


def get_quadd_articles(db_path: Path, edition_id: int | None = None, min_body_len: int = 100) -> list[dict]:
    """Read articles from the quadd extraction database (content_items table).

    Args:
        db_path: Path to quadd's SQLite database.
        edition_id: Optional edition ID to filter by.
        min_body_len: Minimum body text length to include.

    Returns:
        List of article dicts.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT id, edition_id, publisher_id, headline, byline, cleaned_web_text as body_text,
               start_page, jump_pages_json as jump_pages, section, content_type,
               is_stitched, publish_status
        FROM content_items
        WHERE cleaned_web_text IS NOT NULL
          AND length(cleaned_web_text) >= ?
          AND headline IS NOT NULL
          AND headline != '?'
    """
    params: list = [min_body_len]

    if edition_id:
        query += " AND edition_id = ?"
        params.append(edition_id)

    query += " ORDER BY edition_id, start_page, id"

    cursor = conn.execute(query, params)
    articles = [dict(row) for row in cursor.fetchall()]
    conn.close()

    logger.info(f"Found {len(articles)} articles in quadd DB" +
                (f" for edition {edition_id}" if edition_id else ""))
    return articles


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping word-based chunks.

    Matches the chunking strategy used by publisher-demo-rag's DocumentIngester.
    """
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        if end >= len(words):
            break
        start = end - overlap

    return chunks


def ingest_articles(
    articles: list[dict],
    publisher: str = "Observer/Advocate",
    source_file: str = "quadd_extraction",
    embedding_model_name: str = EMBEDDING_MODEL,
) -> dict:
    """Ingest quadd articles into publisher-demo-rag's database and vector store.

    Args:
        articles: List of article dicts from quadd.
        publisher: Publisher name.
        source_file: Source identifier.
        embedding_model_name: SentenceTransformer model name.

    Returns:
        Summary of ingestion results.
    """
    results = {
        "total_articles": len(articles),
        "ingested": 0,
        "skipped": 0,
        "total_chunks": 0,
        "errors": [],
    }

    if not articles:
        logger.warning("No articles to ingest")
        return results

    # Initialize embedding model and ChromaDB collection
    logger.info(f"Loading embedding model: {embedding_model_name}")
    model = SentenceTransformer(embedding_model_name)
    collection = get_articles_collection()

    logger.info(f"ChromaDB collection has {collection.count()} existing chunks")

    for article in articles:
        try:
            headline = article.get("headline", "").strip()
            body = article.get("body_text", "").strip()

            if not headline or not body:
                logger.warning(f"Skipping article with no headline/body: {article.get('id')}")
                results["skipped"] += 1
                continue

            # Skip very short articles (likely noise)
            if len(body) < 50:
                logger.warning(f"Skipping short article ({len(body)} chars): {headline[:50]}")
                results["skipped"] += 1
                continue

            # Generate a stable doc_id from edition + headline
            edition_id = article.get("edition_id", 0)
            doc_id = f"quadd_{edition_id}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{edition_id}_{headline}')}"

            # Extract metadata from quadd article
            byline = article.get("byline", None)
            start_page = article.get("start_page", None)
            jump_pages = None
            if article.get("jump_pages"):
                try:
                    jp = json.loads(article["jump_pages"]) if isinstance(article["jump_pages"], str) else article["jump_pages"]
                    if jp:
                        jump_pages = jp
                except (json.JSONDecodeError, TypeError):
                    pass

            section = article.get("section", None)
            publish_date = article.get("publish_date", "2026-01-28")

            # Determine publisher and location from edition
            publisher_id = article.get("publisher_id", None)
            if publisher_id == 2:
                publisher = "Pipestone County Star"
                location = "Pipestone, MN"
                publish_date = "2026-01-08"
            else:
                publisher = "Observer/Advocate"
                publish_date = "2026-01-28"
                headline_lower = headline.lower()
                if "butterfield" in headline_lower:
                    location = "Butterfield, MN"
                elif "bingham lake" in headline_lower or "sokolofsky" in headline_lower:
                    location = "Bingham Lake, MN"
                elif "mt. lake" in headline_lower or "mt lake" in headline_lower:
                    location = "Mountain Lake, MN"
                elif "pipestone" in headline_lower:
                    location = "Pipestone, MN"
                else:
                    location = "Cottonwood County, MN"

            # Insert into SQLite
            insert_edition_article(
                doc_id=doc_id,
                title=headline,
                edition_id=edition_id,
                source_file=source_file,
                full_text=body,
                cleaned_text=body,
                author=byline,
                publish_date=publish_date,
                section=section,
                start_page=start_page,
                continuation_pages=jump_pages,
                publisher=publisher,
                location=location,
                needs_review=False,
            )

            # Chunk and embed
            chunks = chunk_text(body)
            if not chunks:
                logger.warning(f"No chunks for: {headline[:50]}")
                results["skipped"] += 1
                continue

            embeddings = model.encode(chunks).tolist()

            chunk_ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "doc_id": doc_id,
                    "title": headline,
                    "author": byline or "Staff",
                    "publish_date": publish_date or "",
                    "source_file": source_file,
                    "chunk_index": i,
                    "location": location or "",
                    "subjects": section or "",
                    "edition_id": str(edition_id) if edition_id else "",
                    "publisher": publisher,
                }
                for i in range(len(chunks))
            ]

            # Upsert into ChromaDB (handles duplicates)
            collection.upsert(
                ids=chunk_ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )

            results["ingested"] += 1
            results["total_chunks"] += len(chunks)

            logger.info(
                f"  [{results['ingested']}/{results['total_articles']}] "
                f"{headline[:60]} — {len(chunks)} chunks, {len(body)} chars"
            )

        except Exception as e:
            logger.error(f"Failed to ingest article: {e}", exc_info=True)
            results["errors"].append(str(e))

    logger.info(
        f"\nIngestion complete: "
        f"{results['ingested']}/{results['total_articles']} articles, "
        f"{results['total_chunks']} chunks, "
        f"{results['skipped']} skipped, "
        f"{len(results['errors'])} errors"
    )
    logger.info(f"ChromaDB collection now has {collection.count()} total chunks")

    return results


def main():
    parser = argparse.ArgumentParser(description="Ingest quadd extraction articles into publisher-demo-rag")
    parser.add_argument("--quadd-db", type=Path, default=DEFAULT_QUADD_DB,
                        help="Path to quadd's articles.db")
    parser.add_argument("--edition-id", type=int, default=None,
                        help="Only ingest articles from this edition")
    parser.add_argument("--publisher", type=str, default="Observer/Advocate",
                        help="Publisher name")
    args = parser.parse_args()

    if not args.quadd_db.exists():
        logger.error(f"Quadd database not found: {args.quadd_db}")
        sys.exit(1)

    # Tables already initialized at module level via _ensure_tables()

    # Read articles from quadd
    articles = get_quadd_articles(args.quadd_db, args.edition_id)

    if not articles:
        logger.warning("No articles found to ingest")
        sys.exit(0)

    # Ingest
    results = ingest_articles(articles, publisher=args.publisher)

    if results["errors"]:
        logger.warning(f"Errors encountered: {results['errors']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
