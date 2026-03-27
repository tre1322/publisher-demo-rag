"""V2 extraction pipeline: page grid + cell claiming + bipartite jump matching.

Replaces the flood-fill based pipeline (assemble_articles.py + stitch_jumps.py
+ normalize.py) with the new architecture.

Pipeline stages:
1. Phase 1: Raw extraction (extract_pages.py) — unchanged
2. Phase 2: Column detection + classification + jump hints (classify_blocks.py) — unchanged
3. Phase 3: Page grid → cell claiming → article fragments (NEW)
4. Phase 4: Bipartite jump matching → stitched articles (NEW)
5. Phase 5: Text normalization (NEW)
6. Phase 6: Write to database
"""

import json
import logging
import time
from pathlib import Path

from src.modules.editions.database import get_edition
from src.modules.extraction.extract_pages import (
    ARTIFACTS_BASE,
    extract_edition,
    get_extraction_summary,
    get_page_artifact,
)
from src.modules.extraction.classify_blocks import (
    enrich_edition,
    get_enriched_page,
    get_enrichment_summary,
)
from src.modules.extraction.cell_claiming import assemble_page, ArticleFragment
from src.modules.extraction.jump_matcher import match_jumps, stitch_fragments, merge_continuation_columns
from src.modules.extraction.text_normalizer import normalize_all_articles

logger = logging.getLogger(__name__)


def run_v2_pipeline(edition_id: int) -> dict:
    """Run the complete V2 extraction pipeline on an edition.

    Args:
        edition_id: Edition ID to process.

    Returns:
        Dict with pipeline results including extracted articles.
    """
    start_time = time.time()
    result = {
        "success": False,
        "edition_id": edition_id,
        "page_count": 0,
        "article_count": 0,
        "stitched_count": 0,
        "error": None,
        "articles": [],
    }

    # Validate edition
    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        return result

    # Phase 1: Raw extraction (if not already done)
    extraction = get_extraction_summary(publisher_id, edition_id)
    if not extraction:
        logger.info(f"Running Phase 1 extraction for edition {edition_id}...")
        ext_result = extract_edition(edition_id)
        if not ext_result["success"]:
            result["error"] = f"Phase 1 failed: {ext_result.get('error')}"
            return result
        extraction = get_extraction_summary(publisher_id, edition_id)

    page_count = extraction["page_count"]
    result["page_count"] = page_count

    # Phase 2: Enrichment (if not already done)
    enrichment = get_enrichment_summary(publisher_id, edition_id)
    if not enrichment:
        logger.info(f"Running Phase 2 enrichment for edition {edition_id}...")
        enr_result = enrich_edition(edition_id)
        if not enr_result["success"]:
            result["error"] = f"Phase 2 failed: {enr_result.get('error')}"
            return result

    # Phase 3: Page grid + cell claiming for each page
    logger.info(f"Phase 3: Assembling articles from {page_count} pages...")
    all_fragments: dict[int, list[ArticleFragment]] = {}

    for page_num in range(1, page_count + 1):
        enriched = get_enriched_page(publisher_id, edition_id, page_num)
        raw = get_page_artifact(publisher_id, edition_id, page_num)

        if not enriched or not raw:
            logger.warning(f"  Page {page_num}: missing artifacts, skipping")
            continue

        fragments = assemble_page(page_num, enriched, raw)
        all_fragments[page_num] = fragments

        titles = [f.headline[:30] for f in fragments if f.headline]
        conts = [f.label for f in fragments if f.kind == "continuation_header"]
        logger.info(
            f"  Page {page_num}: {len(fragments)} fragments "
            f"(titles={titles}, continuations={conts})"
        )

    # Phase 3.5: Merge multi-column continuations
    # Back-page continuations often span multiple newspaper columns. Cell claiming
    # may create separate fragments per column — merge orphan body fragments into
    # their parent continuation_header fragment before jump matching.
    logger.info("Phase 3.5: Merging multi-column continuations...")
    all_fragments = merge_continuation_columns(all_fragments)

    # Phase 4: Bipartite jump matching
    logger.info("Phase 4: Jump matching...")
    edges = match_jumps(all_fragments)

    # Stitch matched fragments into articles
    articles = stitch_fragments(all_fragments, edges)
    logger.info(f"  {len(articles)} articles after stitching ({len(edges)} jumps)")

    # Phase 5: Text normalization
    logger.info("Phase 5: Text normalization...")
    articles = normalize_all_articles(articles)

    # Filter out very short or empty articles
    articles = [a for a in articles if len(a.get("body_text", "")) >= 50 or a.get("headline")]

    result["success"] = True
    result["article_count"] = len(articles)
    result["stitched_count"] = sum(1 for a in articles if a.get("has_jumps"))
    result["articles"] = articles

    elapsed = round(time.time() - start_time, 2)
    logger.info(
        f"V2 pipeline complete: edition={edition_id}, "
        f"pages={page_count}, articles={len(articles)}, "
        f"stitched={result['stitched_count']}, time={elapsed}s"
    )

    # Save articles to JSON for inspection
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    output_path = artifacts_dir / "articles_v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)
    logger.info(f"  Articles saved to {output_path}")

    # Also write normalized.json in the format expected by Phase 6 DB write.
    # This bridges V2 output → legacy DB write path.
    normalized_articles = []
    for i, art in enumerate(articles):
        body = art.get("body_text", "")
        headline = art.get("headline", "")
        kind = art.get("kind", "")

        # Infer content type from kind and headline
        content_type = "news"
        hl_lower = headline.lower()
        if kind == "continuation_header":
            content_type = "news"
        elif any(w in hl_lower for w in ("wrestling", "basketball", "hockey", "football", "athlete", "tourney", "snap skid")):
            content_type = "sports"
        elif any(w in hl_lower for w in ("sheriff", "police", "court")):
            content_type = "police"
        elif any(w in hl_lower for w in ("obituar", "death")):
            content_type = "obituary"

        # Prominence: front page articles get higher scores
        start_page = art.get("start_page", 1)
        prominence = max(0, 1.0 - (start_page - 1) * 0.1) if start_page else 0.5

        normalized_articles.append({
            "article_index": i,
            "page_number": start_page,
            "headline": headline,
            "subheadline": "",
            "kicker": "",
            "byline": art.get("byline", ""),
            "raw_text": body,
            "cleaned_web_text": body,
            "content_type": content_type,
            "print_prominence_score": round(prominence, 2),
            "extraction_confidence": 0.9,
            "homepage_eligible": bool(headline) and len(body) >= 100 and content_type in ("news", "sports"),
            "is_stitched": art.get("has_jumps", False),
            "jump_pages": art.get("jump_pages", []),
            "start_page": start_page,
            "end_page": max(art.get("jump_pages", [start_page]) + [start_page]),
            "block_count": body.count("\n\n") + 1,
            "column_id": None,
            "span_columns": 1,
            "bbox": None,
        })

    normalized_path = artifacts_dir / "normalized.json"
    with open(normalized_path, "w", encoding="utf-8") as f:
        json.dump({"articles": normalized_articles}, f, indent=2, ensure_ascii=False)
    logger.info(f"  Normalized articles saved to {normalized_path}")

    return result
