"""Shared write layer: write StandardArticle dicts to all destinations.

All extraction pipelines (IDML, Vision, V2 PDF) produce a list of
StandardArticle dicts. This module writes them to every destination
the system needs:

1. articles table (legacy, used by chatbot RAG)
2. content_items table (modern, used by homepage/public frontend)
3. ChromaDB articles collection (vector embeddings for chatbot retrieval)
4. Homepage batch scoring (prominence + freshness scoring)

This replaces duplicated write logic that was previously copy-pasted in
admin_frontend/routes.py, scripts/process_edition.py, and idml_parser.py.
"""

import logging
import re
import uuid

from sentence_transformers import SentenceTransformer

from src.core.config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL
from src.core.vector_store import get_articles_collection
from src.modules.articles import insert_edition_article
from src.modules.content_items.database import insert_content_item
from src.modules.editions.database import mark_edition_current, update_edition_status
from src.modules.extraction.publish import generate_homepage_batch

logger = logging.getLogger(__name__)


def _infer_content_type(headline: str, body_text: str = "") -> str:
    """Infer content type from headline and body text keywords."""
    hl = (headline or "").lower()
    body_start = (body_text or "")[:500].lower()
    combined = hl + " " + body_start

    # Sports — check headline first, then body for strong signals
    sports_headline_words = (
        "wrestling", "basketball", "hockey", "football", "baseball",
        "softball", "golf ", "golfers", "tennis", "volleyball", "track ",
        "athlete", "tourney", "tournament", "cobras", "wolverines",
        "falcons", "spartans", "indians", "chargers", "season opener",
        "all-conference", "all-big south", "red rock boys", "red rock girls",
        "medalist", "pitcher", "coaching", "playoff",
        "sweep", "rally past", "inning", "opener monday",
    )
    if any(w in hl for w in sports_headline_words):
        return "sports"
    # Body-level sports detection (for headlines like "Big test right off the bat")
    # Use word-boundary regex to avoid "beginning" matching "inning" etc.
    sports_body_patterns = (
        r"\bscored\b", r"\binning\b", r"\bvarsity\b", r"\bpitcher\b",
        r"\bquarterback\b", r"\bhalftime\b", r"\bfree throw\b",
        r"\bthree-pointer\b", r"\btouchdown", r"\bhome run\b",
        r"\bstrikeout\b", r"\bpar putt\b", r"\btee off\b",
        r"\beagle softball\b", r"\beagle golf\b", r"\beagle baseball\b",
        r"\bconference squad\b", r"\ball-conference\b", r"\bseason record\b",
    )
    if any(re.search(p, body_start) for p in sports_body_patterns):
        return "sports"

    # Also classify by page number if available (sports section is typically pages 9-12)
    # This is handled at call site since we need start_page context

    if any(w in hl for w in ("sheriff", "police", "court", "arrest", "charged")):
        return "police"
    if any(w in combined for w in ("obituar", "passed away", "funeral", "memorial service",
                                    "celebration of life")):
        return "obituary"
    if any(w in hl for w in ("editorial", "opinion", "letter to")):
        return "opinion"
    return "news"


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end >= len(words):
            break
        start = end - CHUNK_OVERLAP

    return chunks


def write_articles_to_all(
    articles: list[dict],
    edition_id: int,
    publisher_id: int,
    publisher_name: str,
    edition_date: str | None = None,
    source_filename: str = "",
) -> dict:
    """Write StandardArticle dicts to all system destinations.

    Args:
        articles: List of StandardArticle dicts (headline, body_text, byline, etc.)
        edition_id: Edition ID in the editions table.
        publisher_id: Publisher ID.
        publisher_name: Publisher display name (e.g., "Cottonwood County Citizen").
        edition_date: Edition date in YYYY-MM-DD format.
        source_filename: Original filename for metadata.

    Returns:
        Dict with counts: articles_written, content_items_written, chunks_indexed.
    """
    embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    collection = get_articles_collection()

    articles_written = 0
    content_items_written = 0
    total_chunks = 0

    for art in articles:
        headline = art.get("headline", "")
        body_text = art.get("body_text", "")
        byline = art.get("byline", "")

        if not body_text or len(body_text) < 20:
            continue

        doc_id = str(uuid.uuid4())
        start_page = art.get("start_page")
        jump_pages = art.get("jump_pages") or []
        content_type = art.get("content_type") or _infer_content_type(headline, body_text)
        subheadline = art.get("subheadline", "")
        is_stitched = art.get("is_stitched", False)
        confidence = art.get("extraction_confidence", 0.9)

        # 1. Legacy articles table
        insert_edition_article(
            doc_id=doc_id,
            title=headline,
            edition_id=edition_id,
            source_file=source_filename,
            full_text=body_text,
            cleaned_text=body_text,
            author=byline or None,
            publish_date=edition_date,
            section=content_type,
            start_page=start_page,
            continuation_pages=jump_pages or None,
            subheadline=subheadline or None,
            publisher=publisher_name,
            needs_review=True,
        )
        articles_written += 1

        # 2. Content items table
        prominence = max(0, 1.0 - (start_page - 1) * 0.1) if start_page else 0.5
        end_page = max(jump_pages + [start_page or 1]) if jump_pages else start_page
        homepage_eligible = bool(headline) and len(body_text) >= 100 and content_type in ("news", "sports")

        content_item_id = insert_content_item(
            edition_id=edition_id,
            publisher_id=publisher_id,
            content_type=content_type,
            headline=headline,
            subheadline=subheadline,
            byline=byline,
            raw_text=body_text,
            cleaned_web_text=body_text,
            start_page=start_page,
            end_page=end_page,
            jump_pages=jump_pages,
            print_prominence_score=round(prominence, 2),
            extraction_confidence=confidence,
            homepage_eligible=homepage_eligible,
            is_stitched=is_stitched,
            block_count=body_text.count("\n\n") + 1,
            edition_date=edition_date,
        )
        content_items_written += 1
        story_url = f"/story/{content_item_id}" if content_item_id else ""

        # 3. ChromaDB vector embeddings
        chunks = _chunk_text(body_text)
        if chunks:
            embeddings = embedding_model.encode(chunks).tolist()
            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "doc_id": doc_id,
                    "title": headline[:200],
                    "publish_date": edition_date or "",
                    "edition_date": edition_date or "",
                    "author": byline or "Unknown",
                    "source_file": source_filename,
                    "chunk_index": i,
                    "location": "",
                    "subjects": "",
                    "edition_id": str(edition_id),
                    "content_type": content_type,
                    "publisher": publisher_name,
                    "url": story_url,
                }
                for i in range(len(chunks))
            ]
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
            total_chunks += len(chunks)

    # 4. Homepage batch scoring + mark edition current
    generate_homepage_batch(edition_id)
    mark_edition_current(edition_id, publisher_id)

    # Update edition status
    update_edition_status(
        edition_id,
        status="completed",
        article_count=articles_written,
    )

    logger.info(
        f"write_articles_to_all: edition={edition_id}, "
        f"articles={articles_written}, content_items={content_items_written}, "
        f"chunks={total_chunks}"
    )

    return {
        "articles_written": articles_written,
        "content_items_written": content_items_written,
        "chunks_indexed": total_chunks,
    }
