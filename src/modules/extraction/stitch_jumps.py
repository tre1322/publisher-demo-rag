"""Phase 4: Jump detection and cross-page stitching.

Rewritten to use pre-tagged jump hints from Phase 2 (on blocks) and
Phase 3 (on articles). Much simpler and more robust than pattern-matching
after assembly.

Algorithm:
1. Identify articles with has_jump_out=True (front-page fragments)
2. Identify articles with has_jump_in=True (continuation fragments)
3. Match by keyword (COUNCIL -> COUNCIL/, FUNDING -> FUNDING/)
4. Merge matched fragments into single articles
5. AI fallback for ambiguous cases (future)
"""

import json
import logging
import time
from pathlib import Path

from src.modules.editions.database import get_edition
from src.modules.extraction.assemble_articles import get_assembly
from src.modules.extraction.extract_pages import ARTIFACTS_BASE

logger = logging.getLogger(__name__)


def match_jumps(articles: list[dict]) -> list[dict]:
    """Match jump-out articles with jump-in continuations using pre-tagged hints.

    This is much simpler than the previous regex-based approach because
    Phase 2 already tagged every block with jump_hints, and Phase 3
    propagated those to has_jump_out/has_jump_in on each article.
    """
    # Separate sources (jump out) and targets (jump in)
    sources = [a for a in articles if a.get("has_jump_out")]
    targets = [a for a in articles if a.get("has_jump_in")]

    # Index targets by keyword
    targets_by_keyword: dict[str, list[dict]] = {}
    for tgt in targets:
        for hint in tgt.get("jump_in_hints", []):
            kw = hint.get("keyword")
            if kw:
                targets_by_keyword.setdefault(kw, []).append(tgt)

    stitches = []
    used_targets = set()

    for src in sources:
        src_idx = src["article_index"]

        for hint in src.get("jump_out_hints", []):
            keyword = hint.get("keyword")
            target_page = hint.get("target_page")

            if not keyword:
                continue

            # Find matching target by keyword
            candidates = targets_by_keyword.get(keyword, [])

            # Filter by target page if specified
            if target_page:
                candidates = [c for c in candidates if c.get("page_number") == target_page]

            # Filter out already-used targets
            candidates = [c for c in candidates if c["article_index"] not in used_targets]

            if not candidates:
                # Fallback: search ALL later-page articles for keyword in headline
                src_page = src.get("page_number", 0)
                for art in articles:
                    if art["article_index"] in used_targets:
                        continue
                    if art.get("page_number", 0) <= src_page:
                        continue
                    headline = art.get("headline", "").upper().replace("\n", " ")
                    if keyword in headline:
                        candidates = [art]
                        break

            if len(candidates) == 1:
                tgt = candidates[0]
                used_targets.add(tgt["article_index"])
                stitches.append({
                    "source_article": src_idx,
                    "source_page": src.get("page_number"),
                    "source_headline": src.get("headline", "")[:80],
                    "target_article": tgt["article_index"],
                    "target_page": tgt.get("page_number"),
                    "target_headline": tgt.get("headline", "")[:80],
                    "confidence": 0.95,
                    "match_method": "keyword",
                    "keyword": keyword,
                })
            elif len(candidates) > 1:
                # Ambiguous — take the one on the expected target page, or first match
                best = candidates[0]
                if target_page:
                    page_matches = [c for c in candidates if c.get("page_number") == target_page]
                    if page_matches:
                        best = page_matches[0]
                used_targets.add(best["article_index"])
                stitches.append({
                    "source_article": src_idx,
                    "source_page": src.get("page_number"),
                    "source_headline": src.get("headline", "")[:80],
                    "target_article": best["article_index"],
                    "target_page": best.get("page_number"),
                    "target_headline": best.get("headline", "")[:80],
                    "confidence": 0.7,
                    "match_method": "keyword_ambiguous",
                    "keyword": keyword,
                })

    # Reverse pass: unmatched continuation headers, search earlier pages
    for tgt in targets:
        tgt_idx = tgt["article_index"]
        if tgt_idx in used_targets:
            continue

        for hint in tgt.get("jump_in_hints", []):
            keyword = hint.get("keyword")
            source_page = hint.get("source_page")
            if not keyword:
                continue

            tgt_page = tgt.get("page_number", 0)

            # Search earlier pages for article with this keyword in headline/body
            best_match = None
            best_confidence = 0.0

            for src in articles:
                if src.get("page_number", 0) >= tgt_page:
                    continue
                if src["article_index"] in [s["source_article"] for s in stitches]:
                    continue
                if source_page and src.get("page_number") != source_page:
                    continue

                src_headline = src.get("headline", "").upper().replace("\n", " ")
                src_body = src.get("body_text", "").upper()[:300]

                if keyword in src_headline:
                    if best_confidence < 0.8:
                        best_match = src
                        best_confidence = 0.8
                elif keyword in src_body:
                    if best_confidence < 0.6:
                        best_match = src
                        best_confidence = 0.6

            if best_match:
                used_targets.add(tgt_idx)
                stitches.append({
                    "source_article": best_match["article_index"],
                    "source_page": best_match.get("page_number"),
                    "source_headline": best_match.get("headline", "")[:80],
                    "target_article": tgt_idx,
                    "target_page": tgt_page,
                    "target_headline": tgt.get("headline", "")[:80],
                    "confidence": best_confidence,
                    "match_method": "reverse_keyword",
                    "keyword": keyword,
                })
                break

    return stitches


def merge_stitched_articles(articles: list[dict], stitches: list[dict]) -> list[dict]:
    """Merge stitched article pairs. Source keeps headline/byline, target body appended."""
    art_by_idx = {a["article_index"]: a for a in articles}
    consumed = set()
    merged_sources = {}

    for stitch in stitches:
        src_idx = stitch["source_article"]
        tgt_idx = stitch["target_article"]

        src = art_by_idx.get(src_idx)
        tgt = art_by_idx.get(tgt_idx)
        if not src or not tgt:
            continue

        base = merged_sources.get(src_idx, {**src})

        # Append continuation body in reading order (already ordered by column+seq)
        merged_body = base.get("body_text", "") + "\n" + tgt.get("body_text", "")
        base["body_text"] = merged_body.strip()
        base["block_count"] = base.get("block_count", 0) + tgt.get("block_count", 0)

        jump_pages = base.get("jump_pages", [])
        jump_pages.append(tgt.get("page_number"))
        base["jump_pages"] = jump_pages
        base["is_stitched"] = True
        base["stitch_confidence"] = stitch["confidence"]
        base["stitch_method"] = stitch["match_method"]

        merged_sources[src_idx] = base
        consumed.add(tgt_idx)

    result = []
    for art in articles:
        idx = art["article_index"]
        if idx in consumed:
            continue
        if idx in merged_sources:
            result.append(merged_sources[idx])
        else:
            result.append({**art, "is_stitched": False, "jump_pages": []})

    return result


def stitch_edition(edition_id: int) -> dict:
    """Run Phase 4 jump stitching on an edition."""
    start_time = time.time()
    result = {
        "success": False, "edition_id": edition_id,
        "total_articles_before": 0, "total_articles_after": 0,
        "stitches_found": 0, "error": None,
    }

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        return result

    assembly = get_assembly(publisher_id, edition_id)
    if not assembly:
        result["error"] = f"Edition {edition_id} has no assembly data. Run Phase 3 first."
        return result

    articles = assembly.get("articles", [])
    result["total_articles_before"] = len(articles)

    logger.info(f"Phase 4 stitching: edition={edition_id}, articles={len(articles)}")

    stitches = match_jumps(articles)
    result["stitches_found"] = len(stitches)

    for s in stitches:
        logger.info(
            f"  Stitch: p{s['source_page']} [{s['source_headline'][:30]}] "
            f"-> p{s['target_page']} [{s['target_headline'][:30]}] "
            f"[{s['match_method']}, kw={s['keyword']}]"
        )

    merged = merge_stitched_articles(articles, stitches)
    result["total_articles_after"] = len(merged)

    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    stitched_data = {
        "edition_id": edition_id, "publisher_id": publisher_id,
        "total_articles_before": len(articles),
        "total_articles_after": len(merged),
        "stitches": stitches,
        "stitch_time_seconds": round(time.time() - start_time, 2),
        "articles": merged,
    }
    with open(artifacts_dir / "stitched.json", "w", encoding="utf-8") as f:
        json.dump(stitched_data, f, indent=2, ensure_ascii=False)

    result["success"] = True
    result["artifacts_dir"] = str(artifacts_dir)
    result["stitches"] = stitches
    logger.info(f"Phase 4 complete: {len(stitches)} stitches, {len(articles)}->{len(merged)} articles")
    return result


def get_stitched(publisher_id: int, edition_id: int) -> dict | None:
    path = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}" / "stitched.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
