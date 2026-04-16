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
from src.modules.editions.database import (
    mark_edition_current,
    mark_edition_current_if_latest,
    update_edition_status,
)
from src.modules.extraction.publish import generate_homepage_batch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Junk filter — drop non-editorial content before it reaches the DB
# ---------------------------------------------------------------------------
_CONTINUATION_RE = re.compile(r"(?i)^\s*from\s+page\s+\d+")
_AD_SECTION_TITLES = {
    "hvac", "plumbing", "hvac/plumbing", "accounting", "accounting/tax services",
    "classifieds", "legals", "public notices", "farm equip", "farm equip.",
    "auctions", "boats/motors", "rec. vehicles", "recreational vehicles",
    "help wanted", "services", "for sale", "real estate",
    "eeo/aa employer", "employment",
}


def _is_junk_article(headline: str, body_text: str) -> bool:
    """Return True for content that should never enter the articles table.

    Catches three categories:
    - Unstitched continuation stubs ("from page 13", "from page 2")
    - All-caps ad-section headers with ≤ 4 words ("HVAC/PLUMBING")
    - Known ad-section title strings (classifieds, legals, etc.)
    """
    title = (headline or "").strip()
    body = (body_text or "").strip()

    # 1. Continuation stub — title starts with "from page N"
    if _CONTINUATION_RE.match(title):
        return True

    # 2. Body too short after trimming (already checked at < 20 above, but
    #    raising bar to 80 chars catches partial/orphan ad blocks)
    if len(body) < 80:
        return True

    # 3. All-caps short headline — ad section header
    if title and title.isupper() and len(title.split()) <= 5:
        return True

    # 4. Known ad-section title (case-insensitive, exact)
    if title.lower().rstrip(".") in _AD_SECTION_TITLES:
        return True

    return False


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
    force_current: bool | None = None,
) -> dict:
    """Write StandardArticle dicts to all system destinations.

    Args:
        articles: List of StandardArticle dicts (headline, body_text, byline, etc.)
        edition_id: Edition ID in the editions table.
        publisher_id: Publisher ID.
        publisher_name: Publisher display name (e.g., "Cottonwood County Citizen").
        edition_date: Edition date in YYYY-MM-DD format.
        source_filename: Original filename for metadata.
        force_current: Tri-state flag controlling is_current behavior:
            - None (default): auto — promote to current only if edition_date is the
              newest for this publisher (via mark_edition_current_if_latest).
            - True: unconditionally promote this edition to current.
            - False: seed-as-historical — skip the mark-current step entirely so
              older/backfill editions do not displace the real current edition.
            In all three cases ChromaDB indexing and content_items writes still run
            in full — historical editions are fully retrievable by the chatbot,
            just without the ×1.5 current-edition boost.

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

        # v2 junk filter: drop obvious non-editorial content before it
        # hits the DB. Three categories:
        #   1. Unstitched continuation stubs ("from page 13") — orphan
        #      fragments that the jump matcher couldn't attach to a parent.
        #      They have no standalone value and clutter the review queue.
        #   2. All-caps short headlines (≤ 4 words) — ad-section headers
        #      like "HVAC/PLUMBING" or "ACCOUNTING/TAX SERVICES". Real
        #      headlines use title case.
        #   3. Short body text (< 80 chars) — fragment too small to be a
        #      real article (already caught partly by the < 20 check above,
        #      but raising the floor filters more stub content).
        if _is_junk_article(headline, body_text):
            logger.debug(
                f"Skipping junk article: title={headline!r:.60} "
                f"body_len={len(body_text)}"
            )
            continue

        doc_id = str(uuid.uuid4())
        start_page = art.get("start_page")
        jump_pages = art.get("jump_pages") or []
        content_type = art.get("content_type") or _infer_content_type(headline, body_text)
        subheadline = art.get("subheadline", "")
        # is_stitched: prefer explicit flag; fall back to "has jump pages" since
        # V2 pipeline's article dicts don't always carry is_stitched through. Any
        # article with non-empty jump_pages spans multiple pages = stitched.
        is_stitched = art.get("is_stitched") or bool(jump_pages)
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
            needs_review=False,
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

    # 4. Homepage batch scoring + mark edition current (tri-state)
    generate_homepage_batch(edition_id)
    if force_current is True:
        logger.info(
            f"write_articles_to_all: force_current=True → "
            f"unconditionally promoting edition {edition_id}"
        )
        mark_edition_current(edition_id, publisher_id)
    elif force_current is False:
        logger.info(
            f"write_articles_to_all: force_current=False → "
            f"seeding edition {edition_id} as historical "
            f"(is_current flag untouched)"
        )
    else:
        mark_edition_current_if_latest(edition_id, publisher_id)

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


def write_articles(
    articles: list[dict],
    publisher_name: str,
    edition_date: str | None = None,
    source_filename: str = "",
    mark_current: bool = False,
) -> dict:
    """High-level convenience wrapper around write_articles_to_all.

    Resolves publisher_name → publisher_id and creates an edition record
    automatically.  All three ingestion tiers (RSS, URL import, paste form)
    use this instead of calling write_articles_to_all directly.

    Args:
        articles: List of StandardArticle dicts.
        publisher_name: Publisher display name — must exist in the publishers table.
        edition_date: Edition date in YYYY-MM-DD format (used for edition record).
        source_filename: Source label for metadata (e.g. "rss:https://…", "paste_form").
        mark_current: If True, promote this edition to current after write.
            If False (default), seed as historical — no is_current change.

    Returns:
        Dict with counts: articles_written, content_items_written, chunks_indexed.

    Raises:
        ValueError: If publisher_name is not found in the publishers table.
    """
    from src.modules.publishers.database import get_publisher_by_name
    from src.modules.editions.database import insert_edition

    # 1. Resolve publisher
    pub = get_publisher_by_name(publisher_name)
    if pub is None:
        # Auto-register unknown publishers so ingestion always succeeds
        from src.modules.publishers.database import insert_publisher
        logger.warning(
            f"write_articles: publisher {publisher_name!r} not found — "
            f"auto-registering"
        )
        insert_publisher(name=publisher_name)
        pub = get_publisher_by_name(publisher_name)
        if pub is None:
            raise ValueError(
                f"Publisher {publisher_name!r} could not be created. "
                f"Check the publishers table."
            )

    publisher_id: int = pub["id"]
    resolved_source = source_filename or f"import:{publisher_name}"
    resolved_date = edition_date or ""

    # 2. Reuse an existing edition for the same publisher+date+source if one
    # exists (e.g. multiple pastes on the same day should land in the same
    # synthetic edition instead of creating a new "current" one each time
    # that overrides all earlier pastes).
    from src.core.database import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM editions
        WHERE publisher_id = ?
          AND edition_date = ?
          AND source_filename = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (publisher_id, resolved_date, resolved_source),
    )
    row = cur.fetchone()
    conn.close()

    if row is not None:
        edition_id = row[0] if not isinstance(row, dict) else row["id"]
        logger.info(
            f"write_articles: reusing existing edition {edition_id} "
            f"for publisher={publisher_name!r} date={resolved_date!r} "
            f"source={resolved_source!r}"
        )
    else:
        edition_id = insert_edition(
            source_filename=resolved_source,
            publisher_id=publisher_id,
            edition_date=resolved_date,
            upload_status="completed",
            extraction_status="completed",
        )

    # 3. Delegate to the full writer
    force_current: bool | None = True if mark_current else False
    return write_articles_to_all(
        articles=articles,
        edition_id=edition_id,
        publisher_id=publisher_id,
        publisher_name=publisher_name,
        edition_date=edition_date,
        source_filename=source_filename,
        force_current=force_current,
    )
