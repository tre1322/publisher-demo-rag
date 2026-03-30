"""Phase 6: Database write path — stores normalized content items.

Phase 7: Homepage batch generation — computes homepage_score and publishes.

Takes normalized articles from Phase 5 and writes them to content_items table.
Computes homepage scores and publishes content for homepage display.
"""

import logging
import time
from datetime import datetime, timedelta

from src.modules.editions.database import get_edition
from src.modules.extraction.extract_pages import ARTIFACTS_BASE
from src.modules.extraction.normalize import get_normalized
from src.modules.content_items.database import (
    delete_content_items_for_edition,
    get_content_items_for_edition,
    get_homepage_content,
    insert_content_item,
    publish_edition_content,
)
from src.core.database import get_connection

logger = logging.getLogger(__name__)


# ── Phase 6: Write to DB ──


def write_edition_to_db(edition_id: int) -> dict:
    """Write normalized articles to content_items table.

    Deletes any existing items for this edition first (idempotent re-run).

    Args:
        edition_id: Edition ID (must have completed Phase 5).

    Returns:
        Dict with write results.
    """
    start_time = time.time()
    result = {
        "success": False,
        "edition_id": edition_id,
        "items_written": 0,
        "items_deleted": 0,
        "error": None,
    }

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        return result

    normalized = get_normalized(publisher_id, edition_id)
    if not normalized:
        result["error"] = (
            f"Edition {edition_id} has no normalized data. "
            f"Run Phase 5 normalization first."
        )
        return result

    articles = normalized.get("articles", [])
    edition_date = edition.get("edition_date")

    # Delete existing items for idempotent re-run
    deleted = delete_content_items_for_edition(edition_id)
    result["items_deleted"] = deleted
    if deleted > 0:
        logger.info(f"Deleted {deleted} existing content items for edition {edition_id}")

    # Write each normalized article
    written = 0
    for art in articles:
        insert_content_item(
            edition_id=edition_id,
            publisher_id=publisher_id,
            content_type=art.get("content_type", "news"),
            headline=art.get("headline", ""),
            subheadline=art.get("subheadline", ""),
            byline=art.get("byline", ""),
            raw_text=art.get("raw_text", ""),
            cleaned_web_text=art.get("cleaned_web_text", ""),
            section=art.get("content_type", "news"),
            start_page=art.get("start_page"),
            end_page=art.get("end_page"),
            jump_pages=art.get("jump_pages", []),
            print_prominence_score=art.get("print_prominence_score", 0),
            extraction_confidence=art.get("extraction_confidence", 0),
            homepage_eligible=art.get("homepage_eligible", False),
            homepage_score=0,  # computed in Phase 7
            publish_status="draft",
            is_stitched=art.get("is_stitched", False),
            block_count=art.get("block_count", 0),
            column_id=art.get("column_id"),
            span_columns=art.get("span_columns", 1),
            bbox=art.get("bbox"),
            edition_date=edition_date,
        )
        written += 1

    result["items_written"] = written
    result["success"] = True

    logger.info(
        f"Phase 6 DB write complete: edition={edition_id}, "
        f"written={written}, deleted={deleted}"
    )

    return result


# ── Phase 7: Homepage Batch ──


def generate_homepage_batch(edition_id: int) -> dict:
    """Generate homepage batch: compute scores and publish.

    Homepage score = blend of:
    - print_prominence_score (40%)
    - freshness (30%) — all items from same edition get same freshness
    - content_type bonus (30%) — news/sports rank higher

    Args:
        edition_id: Edition ID (must have completed Phase 6).

    Returns:
        Dict with homepage batch results.
    """
    start_time = time.time()
    result = {
        "success": False,
        "edition_id": edition_id,
        "total_items": 0,
        "homepage_eligible": 0,
        "published": 0,
        "error": None,
    }

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    items = get_content_items_for_edition(edition_id)

    if not items:
        result["error"] = (
            f"Edition {edition_id} has no content items. "
            f"Run Phase 6 DB write first."
        )
        return result

    result["total_items"] = len(items)

    # Content type bonus weights
    TYPE_BONUS = {
        "news": 0.3,
        "sports": 0.25,
        "proceedings": 0.2,
        "opinion": 0.2,
        "community": 0.15,
        "police": 0.15,
        "obituary": 0.1,
        "legal": 0.05,
        "classifieds": 0.0,
    }

    conn = get_connection()
    cursor = conn.cursor()

    eligible_count = 0
    for item in items:
        if not item.get("homepage_eligible"):
            continue
        eligible_count += 1

        prominence = item.get("print_prominence_score", 0)
        type_bonus = TYPE_BONUS.get(item.get("content_type", "news"), 0.1)

        # Compute freshness based on edition_date age
        edition_date_str = edition.get("edition_date")
        freshness = 1.0
        if edition_date_str:
            try:
                ed = datetime.strptime(edition_date_str, "%Y-%m-%d")
                age_days = (datetime.now() - ed).days
                if age_days <= 7:
                    freshness = 1.0
                elif age_days <= 30:
                    freshness = 0.8
                elif age_days <= 90:
                    freshness = 0.5
                else:
                    freshness = 0.2
            except ValueError:
                freshness = 0.5  # unparseable date

        homepage_score = round(
            prominence * 0.4 + freshness * 0.3 + type_bonus * 0.3,
            4,
        )

        cursor.execute(
            "UPDATE content_items SET homepage_score = ? WHERE id = ?",
            (homepage_score, item["id"]),
        )

    conn.commit()
    conn.close()

    result["homepage_eligible"] = eligible_count

    # Publish all items
    published = publish_edition_content(edition_id)
    result["published"] = published
    result["success"] = True

    # Get top stories for the result
    top_stories = get_homepage_content(publisher_id, limit=10)
    result["top_stories"] = [
        {
            "id": s["id"],
            "headline": s.get("headline", "")[:80],
            "content_type": s.get("content_type"),
            "homepage_score": s.get("homepage_score"),
            "page": s.get("start_page"),
        }
        for s in top_stories
    ]

    elapsed = round(time.time() - start_time, 2)
    logger.info(
        f"Phase 7 homepage batch complete: edition={edition_id}, "
        f"items={len(items)}, eligible={eligible_count}, "
        f"published={published}, time={elapsed}s"
    )

    return result


# ── Full Pipeline ──


def run_full_pipeline(edition_id: int) -> dict:
    """Run the complete extraction-to-homepage pipeline (Phases 1-7).

    Convenience function that runs all phases in sequence.
    """
    from src.modules.extraction.extract_pages import extract_edition
    from src.modules.extraction.classify_blocks import enrich_edition
    from src.modules.extraction.assemble_articles import assemble_edition
    from src.modules.extraction.stitch_jumps import stitch_edition
    from src.modules.extraction.normalize import normalize_edition

    steps = [
        ("Phase 1: Extract", lambda: extract_edition(edition_id)),
        ("Phase 2: Enrich", lambda: enrich_edition(edition_id)),
        ("Phase 3: Assemble", lambda: assemble_edition(edition_id)),
        ("Phase 4: Stitch", lambda: stitch_edition(edition_id)),
        ("Phase 5: Normalize", lambda: normalize_edition(edition_id)),
        ("Phase 6: DB Write", lambda: write_edition_to_db(edition_id)),
        ("Phase 7: Homepage", lambda: generate_homepage_batch(edition_id)),
    ]

    results = {}
    for step_name, step_fn in steps:
        logger.info(f"Running {step_name} for edition {edition_id}")
        step_result = step_fn()
        results[step_name] = step_result
        if not step_result.get("success"):
            return {
                "success": False,
                "edition_id": edition_id,
                "failed_step": step_name,
                "error": step_result.get("error"),
                "results": results,
            }

    return {
        "success": True,
        "edition_id": edition_id,
        "results": results,
    }
