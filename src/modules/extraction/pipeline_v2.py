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


def _merge_sentence_bridges(articles: list[dict]) -> list[dict]:
    """Merge small sentence-fragment articles into their parent article.

    When a column break splits a sentence, cell claiming may put the tail
    of the sentence into a separate article (often under a neighboring
    headline). This detects fragments whose body starts with lowercase
    text (mid-sentence continuation, < 200 chars) and finds the same-page
    article that has a paragraph ending with the start of that sentence.

    Uses word-overlap matching: the last 2-4 words of a broken paragraph
    should share words with the first few words of the fragment when they
    belong together (e.g., "passengers—a husband" + "and wife and two of
    their daughters").
    """
    if len(articles) < 2:
        return articles

    # Find fragment candidates: short body starting with lowercase
    fragment_indices = []
    for i, art in enumerate(articles):
        body = art.get("body_text", "").strip()
        if not body or len(body) > 200:
            continue
        for line in body.split("\n"):
            line = line.strip()
            if line and line[0].islower():
                fragment_indices.append((i, line))
                break

    if not fragment_indices:
        return articles

    merged = set()
    for fi, frag_content in fragment_indices:
        frag = articles[fi]
        frag_page = frag.get("start_page", 0)
        frag_words = set(w.lower().strip(".,;:—-\"'()") for w in frag_content.split()[:6])

        best_match = None
        best_para_idx = -1
        best_score = 0

        for j, art in enumerate(articles):
            if j == fi or j in merged:
                continue
            if art.get("start_page", 0) != frag_page:
                continue
            art_body = art.get("body_text", "").strip()
            if not art_body or len(art_body) < 100:
                continue

            paragraphs = art_body.split("\n\n")
            for pi, para in enumerate(paragraphs):
                para_stripped = para.rstrip()
                if not para_stripped:
                    continue
                last_char = para_stripped[-1]
                if last_char in ".!?\"')":
                    continue
                # Get last 4 words of the broken paragraph
                tail_words = set(w.lower().strip(".,;:—-\"'()") for w in para_stripped.split()[-4:])
                # Score by word overlap: shared words suggest these belong together
                # e.g., "passengers — a husband" shares context with "and wife and two of their daughters"
                # No direct overlap in this case, but the article size acts as tiebreaker
                overlap = len(tail_words & frag_words)
                # Use article length as primary score (the biggest article on the page
                # that has a matching break is most likely the parent), with overlap as bonus
                score = len(art_body) + overlap * 10000
                if score > best_score:
                    best_score = score
                    best_match = j
                    best_para_idx = pi

        if best_match is not None:
            parent = articles[best_match]
            paragraphs = parent["body_text"].split("\n\n")
            paragraphs[best_para_idx] = paragraphs[best_para_idx].rstrip() + " " + frag_content
            parent["body_text"] = "\n\n".join(paragraphs)
            merged.add(fi)
            logger.info(
                f"  Merged sentence fragment '{frag_content[:40]}...' "
                f"into '{parent.get('headline', '')[:40]}' (para {best_para_idx})"
            )

    if merged:
        articles = [a for i, a in enumerate(articles) if i not in merged]

    return articles


def _apply_fragment_edits(
    edition_id: int,
    all_fragments: dict[int, list[ArticleFragment]],
) -> None:
    """Apply user text edits from the fragment_edits table to in-memory fragments."""
    from src.modules.editions.database import get_fragment_edits

    edits = get_fragment_edits(edition_id)
    if not edits:
        return

    applied = 0
    for page_num, frags in all_fragments.items():
        for frag in frags:
            frag_id = f"p{page_num}_s{frag.seed_id}"
            if frag_id in edits:
                edit = edits[frag_id]
                if edit.get("edited_body_text") is not None:
                    frag.body_text = edit["edited_body_text"]
                if edit.get("edited_headline") is not None:
                    frag.headline = edit["edited_headline"]
                applied += 1

    if applied:
        logger.info(f"  Applied {applied} fragment text edit(s)")


def _resolve_source_fragment(
    src_page: int,
    keyword: str,
    all_fragments: dict[int, list[ArticleFragment]],
) -> ArticleFragment | None:
    """Find the front-page fragment that owns a jump-out keyword by y-proximity.

    Same logic as stitch_fragments uses: find the jump-out block's y-position,
    then score fragments by column containment and proximity.
    """
    src_frags = all_fragments.get(src_page, [])
    if not src_frags:
        return None

    # Find the y-position and column of the jump-out block
    jump_block_y = None
    jump_ref_col = None
    for frag in src_frags:
        pjo = getattr(frag, "_page_jump_outs", [])
        for jo in pjo:
            if jo.get("keyword") and jo["keyword"].upper() == keyword.upper():
                jump_block_y = jo.get("block_y", 0)
                jump_ref_col = jo.get("block_col")
                break
        if jump_block_y is not None:
            break

    if jump_block_y is None:
        # Fallback: pick the largest title fragment on the page
        titles = [f for f in src_frags if f.kind == "title" and f.body_text]
        return max(titles, key=lambda f: len(f.body_text)) if titles else None

    best_frag = None
    best_score = -float("inf")
    for frag in src_frags:
        if frag.kind in ("continuation_header", "orphan_body"):
            continue
        if not frag.body_text:
            continue

        score = 0.0
        frag_cols = set(l[0] for l in frag.lanes) if frag.lanes else set()

        if jump_ref_col is not None and jump_ref_col in frag_cols:
            score += 10000
        if frag.top_y <= jump_block_y <= frag.bottom_y + 200:
            score += 5000
        score -= abs(frag.bottom_y - jump_block_y)
        if frag.top_y > jump_block_y + 50:
            score -= 20000

        if score > best_score:
            best_score = score
            best_frag = frag

    return best_frag


def _apply_jump_overrides(
    edition_id: int,
    edges: list,
    all_fragments: dict[int, list[ArticleFragment]],
) -> list:
    """Apply manual jump overrides from the database.

    - force_unlink: remove edges matching the override's src/dst
    - force_match: add a new edge between the specified fragments
    """
    from src.modules.editions.database import get_jump_overrides
    from src.modules.extraction.jump_matcher import JumpEdge

    overrides = get_jump_overrides(edition_id)
    if not overrides:
        return edges

    logger.info(f"  Applying {len(overrides)} jump override(s)...")

    for ov in overrides:
        src_frag_id = ov["src_fragment_id"]
        dst_frag_id = ov["dst_fragment_id"]

        if ov["action"] == "force_unlink":
            before = len(edges)

            def _edge_matches_unlink(e):
                if e.src_page != ov["src_page"] or e.dst_page != ov["dst_page"]:
                    return False
                if f"p{e.dst_page}_s{e.dst_seed_id}" != dst_frag_id:
                    return False
                # Check src match: direct seed_id, inferred frag, or resolved via y-proximity
                if f"p{e.src_page}_s{e.src_seed_id}" == src_frag_id:
                    return True
                if hasattr(e, "_inferred_frag") and e._inferred_frag:
                    if f"p{e.src_page}_s{e._inferred_frag.seed_id}" == src_frag_id:
                        return True
                # Resolve by y-proximity (same as review artifact does)
                if e.src_seed_id == -1:
                    resolved = _resolve_source_fragment(e.src_page, e.src_headline, all_fragments)
                    if resolved and f"p{e.src_page}_s{resolved.seed_id}" == src_frag_id:
                        return True
                return False

            edges = [e for e in edges if not _edge_matches_unlink(e)]
            removed = before - len(edges)
            logger.info(f"    force_unlink: removed {removed} edge(s) {src_frag_id} -> {dst_frag_id}")

        elif ov["action"] == "force_match":
            # Parse fragment IDs: "p1_s0" -> page=1, seed_id=0
            import re
            src_m = re.match(r"p(\d+)_s(\d+)", src_frag_id)
            dst_m = re.match(r"p(\d+)_s(\d+)", dst_frag_id)
            if not src_m or not dst_m:
                logger.warning(f"    force_match: invalid fragment IDs {src_frag_id}, {dst_frag_id}")
                continue

            src_page = int(src_m.group(1))
            src_seed = int(src_m.group(2))
            dst_page = int(dst_m.group(1))
            dst_seed = int(dst_m.group(2))

            # Find the source and destination fragments
            src_frag = None
            for f in all_fragments.get(src_page, []):
                if f.seed_id == src_seed:
                    src_frag = f
                    break
            dst_frag = None
            for f in all_fragments.get(dst_page, []):
                if f.seed_id == dst_seed:
                    dst_frag = f
                    break

            if not src_frag or not dst_frag:
                logger.warning(f"    force_match: fragments not found {src_frag_id}, {dst_frag_id}")
                continue

            # Remove any existing edges for this destination (it can only match one source)
            edges = [e for e in edges if not (e.dst_page == dst_page and e.dst_seed_id == dst_seed)]

            edge = JumpEdge(
                src_page=src_page,
                src_seed_id=src_seed,
                src_headline=src_frag.headline or src_frag.label or "",
                dst_page=dst_page,
                dst_seed_id=dst_seed,
                dst_label=dst_frag.label or dst_frag.headline or "",
                score=999.0,
                match_reasons=["manual_override"],
            )
            edge._inferred_frag = src_frag
            edges.append(edge)
            logger.info(f"    force_match: added edge {src_frag_id} -> {dst_frag_id}")

    return edges


def _save_jump_review_artifact(
    publisher_id: int,
    edition_id: int,
    all_fragments: dict[int, list[ArticleFragment]],
    edges: list,
    page_count: int,
) -> None:
    """Save jump review data for the visual review UI.

    Creates a JSON artifact with fragment bboxes, jump edges, and
    unmatched items so the admin UI can render an interactive overlay.
    Fragment body text is normalized (column newlines collapsed, soft
    hyphens rejoined) so the review UI shows readable article text.
    """
    from src.modules.extraction.text_normalizer import normalize_article

    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"

    # Collect page dimensions from enriched artifacts
    pages_data = {}
    for page_num in range(1, page_count + 1):
        enriched = get_enriched_page(publisher_id, edition_id, page_num)
        page_w = enriched.get("page_width", 900) if enriched else 900
        page_h = enriched.get("page_height", 1638) if enriched else 1638

        frags = all_fragments.get(page_num, [])
        frag_list = []
        for f in frags:
            # Normalize fragment text for readable display in the review UI
            cleaned = normalize_article({
                "body_text": f.body_text or "",
                "headline": f.headline or "",
                "byline": f.byline or "",
            })
            frag_list.append({
                "id": f"p{page_num}_s{f.seed_id}",
                "seed_id": f.seed_id,
                "kind": f.kind,
                "headline": cleaned["headline"][:200],
                "byline": cleaned["byline"],
                "label": f.label,
                "bbox": list(f.bbox) if f.bbox else [],
                "jump_out_keyword": f.jump_out_keyword,
                "jump_out_target_page": f.jump_out_target_page,
                "body_preview": cleaned["body_text"][:200],
                "body_text": cleaned["body_text"],
            })

        pages_data[str(page_num)] = {
            "width": page_w,
            "height": page_h,
            "fragments": frag_list,
        }

    # Serialize edges — resolve unresolved src_seed_id (-1) using y-proximity
    edge_list = []
    matched_src = set()
    matched_dst = set()
    for e in edges:
        src_id = f"p{e.src_page}_s{e.src_seed_id}"

        # Resolve src_seed_id if it's -1 (unresolved during match_jumps)
        if e.src_seed_id == -1:
            if hasattr(e, "_inferred_frag") and e._inferred_frag:
                src_id = f"p{e.src_page}_s{e._inferred_frag.seed_id}"
            else:
                # Find the source fragment by y-proximity to the jump-out block
                resolved = _resolve_source_fragment(
                    e.src_page, e.src_headline, all_fragments
                )
                if resolved:
                    src_id = f"p{e.src_page}_s{resolved.seed_id}"

        dst_id = f"p{e.dst_page}_s{e.dst_seed_id}"

        edge_list.append({
            "src_page": e.src_page,
            "src_fragment": src_id,
            "src_headline": e.src_headline,
            "dst_page": e.dst_page,
            "dst_fragment": dst_id,
            "dst_label": e.dst_label,
            "score": round(e.score, 1),
            "reasons": e.match_reasons,
        })
        matched_src.add((e.src_page, e.src_headline.upper()))
        matched_dst.add((e.dst_page, e.dst_seed_id))

    # Collect unmatched jump-outs and continuations
    from src.modules.extraction.jump_matcher import collect_jump_outs, collect_continuations
    all_jump_outs = collect_jump_outs(all_fragments)
    all_conts = collect_continuations(all_fragments)

    unmatched_outs = []
    for jo in all_jump_outs:
        key = (jo.get("source_page", 0), jo["keyword"].upper())
        if key not in matched_src:
            unmatched_outs.append({
                "keyword": jo["keyword"],
                "target_page": jo.get("target_page"),
                "source_page": jo.get("source_page", 0),
            })

    unmatched_conts = []
    for c in all_conts:
        key = (c["page"], c["fragment"].seed_id)
        if key not in matched_dst:
            unmatched_conts.append({
                "id": f"p{c['page']}_s{c['fragment'].seed_id}",
                "label": c["label"],
                "page": c["page"],
                "headline": c["fragment"].headline[:200] if c["fragment"].headline else "",
                "bbox": list(c["fragment"].bbox) if c["fragment"].bbox else [],
            })

    review_data = {
        "edition_id": edition_id,
        "publisher_id": publisher_id,
        "page_count": page_count,
        "pages": pages_data,
        "edges": edge_list,
        "unmatched_jump_outs": unmatched_outs,
        "unmatched_continuations": unmatched_conts,
    }

    output_path = artifacts_dir / "jump_review.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(review_data, f, indent=2, ensure_ascii=False)
    logger.info(f"  Jump review artifact saved to {output_path}")


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

    # Phase 2: Enrichment — always re-run to pick up latest classify_blocks logic.
    # Enrichment is fast (~0.3s for 12 pages) and must reflect current code.
    logger.info(f"Phase 2: Enriching {page_count} pages for edition {edition_id}...")
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

    # Phase 3.6: Apply manual fragment text edits from the database.
    # Users can edit fragment headline/body text in the jump review UI.
    # Apply those edits to the in-memory fragments before jump matching
    # so the corrected text flows through stitching and normalization.
    _apply_fragment_edits(edition_id, all_fragments)

    # Phase 4: Bipartite jump matching
    logger.info("Phase 4: Jump matching...")
    edges = match_jumps(all_fragments)

    # Apply manual jump overrides from the database
    edges = _apply_jump_overrides(edition_id, edges, all_fragments)

    # Save jump review artifact for the visual review UI
    _save_jump_review_artifact(
        publisher_id, edition_id, all_fragments, edges, page_count,
    )

    # Stitch matched fragments into articles
    articles = stitch_fragments(all_fragments, edges)
    logger.info(f"  {len(articles)} articles after stitching ({len(edges)} jumps)")

    # Phase 5: Text normalization
    logger.info("Phase 5: Text normalization...")
    articles = normalize_all_articles(articles)

    # Filter out very short or empty articles, and orphaned sentence fragments.
    # Orphans are tiny articles (< 200 chars) whose body starts lowercase —
    # these are tails of sentences broken by column boundaries that ended up
    # in a neighboring article's cell claiming region.
    filtered = []
    for a in articles:
        body = a.get("body_text", "").strip()
        headline = a.get("headline", "").strip()

        # Drop empty articles
        if not body and not headline:
            continue
        if len(body) < 50 and not headline:
            continue

        # Drop orphaned sentence fragments
        if len(body) < 200 and body:
            # Check if the substantive text starts lowercase (mid-sentence)
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            content_line = None
            for l in lines:
                if l[0].islower():
                    content_line = l
                    break
            if content_line and len(content_line) > len(body) * 0.5:
                logger.debug(f"Dropping orphan sentence fragment: '{body[:60]}...'")
                continue

        filtered.append(a)
    articles = filtered

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
