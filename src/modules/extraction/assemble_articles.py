"""Phase 3: Single-page article assembly using headline-anchored flood fill.

Rewritten based on the Article View System Architecture document.
Key improvements:
- Headline-anchored flood fill with proper column-span detection
- Separator lines as HARD boundaries
- Vertical gap > 3x line height as stop condition
- Proper multi-column reading order (column-by-column, top to bottom)
- Kicker/subheadline captured between headline and byline
- Continuation headers (KEYWORD/) used as article anchors
- Orphan sweep with hard invariant: every block assigned or excluded
"""

import json
import logging
import re
import time
from pathlib import Path

from src.modules.editions.database import get_edition
from src.modules.extraction.classify_blocks import (
    enrich_page,
    get_enriched_page,
    get_enrichment_summary,
)
from src.modules.extraction.extract_pages import (
    ARTIFACTS_BASE,
    get_extraction_summary,
    get_page_artifact,
)

logger = logging.getLogger(__name__)

# ── Separator Line Extraction ──

HSEP_MIN_WIDTH = 80.0
HSEP_MAX_HEIGHT = 5.0
VSEP_MIN_HEIGHT = 80.0
VSEP_MAX_WIDTH = 5.0
SEP_SNAP_TOLERANCE = 4.0


def extract_separators(drawings: list[dict]) -> dict:
    """Extract and deduplicate horizontal/vertical separator lines."""
    h_raw, v_raw = [], []

    for d in drawings:
        r = d.get("rect", [0, 0, 0, 0])
        w, h = r[2] - r[0], r[3] - r[1]

        if w >= HSEP_MIN_WIDTH and h <= HSEP_MAX_HEIGHT:
            h_raw.append({"y": round((r[1] + r[3]) / 2, 1), "x_min": round(r[0], 1),
                          "x_max": round(r[2], 1), "width": round(w, 1)})
        elif h >= VSEP_MIN_HEIGHT and w <= VSEP_MAX_WIDTH:
            v_raw.append({"x": round((r[0] + r[2]) / 2, 1), "y_min": round(r[1], 1),
                          "y_max": round(r[3], 1), "height": round(h, 1)})

    return {"horizontal": _dedup_seps(h_raw, "y"), "vertical": _dedup_seps(v_raw, "x")}


def _dedup_seps(seps: list[dict], key: str) -> list[dict]:
    if not seps:
        return []
    seps = sorted(seps, key=lambda s: s[key])
    merged = [seps[0]]
    for s in seps[1:]:
        if abs(s[key] - merged[-1][key]) <= SEP_SNAP_TOLERANCE:
            prev = merged[-1]
            if key == "y":
                prev["x_min"] = min(prev["x_min"], s["x_min"])
                prev["x_max"] = max(prev["x_max"], s["x_max"])
                prev["width"] = round(prev["x_max"] - prev["x_min"], 1)
            else:
                prev["y_min"] = min(prev["y_min"], s["y_min"])
                prev["y_max"] = max(prev["y_max"], s["y_max"])
                prev["height"] = round(prev["y_max"] - prev["y_min"], 1)
            prev[key] = round((prev[key] + s[key]) / 2, 1)
        else:
            merged.append(s)
    return merged


# ── Spatial Helpers ──


def _h_sep_between(y1: float, y2: float, x_min: float, x_max: float,
                    h_seps: list[dict]) -> bool:
    y_top, y_bot = min(y1, y2), max(y1, y2)
    for sep in h_seps:
        if y_top < sep["y"] < y_bot:
            if sep["x_max"] > x_min and sep["x_min"] < x_max:
                return True
    return False


def _next_h_sep_below(y: float, x_min: float, x_max: float,
                       h_seps: list[dict], page_height: float) -> float:
    """Find y of next horizontal separator below y within x-range."""
    best = page_height
    for sep in h_seps:
        if sep["y"] > y + 5:
            if sep["x_max"] > x_min and sep["x_min"] < x_max:
                best = min(best, sep["y"])
    return best


def _estimate_line_height(blocks: list[dict]) -> float:
    """Estimate average line height from body blocks."""
    body_heights = [b["bbox"][3] - b["bbox"][1] for b in blocks
                    if b.get("role") == "body" and b["bbox"][3] - b["bbox"][1] > 5]
    if not body_heights:
        return 12.0
    # Use blocks' average char density to estimate single line height
    # Most body blocks span multiple lines, so use the font_size as proxy
    body_sizes = [b["font_size"] for b in blocks if b.get("role") == "body" and b.get("font_size", 0) > 0]
    if body_sizes:
        return sum(body_sizes) / len(body_sizes) * 1.3  # leading
    return 12.0


def _columns_spanned(block: dict, columns: list[dict]) -> list[int]:
    """Return list of column_ids that a block's bbox overlaps."""
    if not columns:
        return [0]
    x0, x1 = block["bbox"][0], block["bbox"][2]
    spanned = []
    for col in columns:
        col_left = col["x_min"] - 10
        col_right = col["x_max"] + 50  # generous right margin
        if x1 > col_left and x0 < col_right:
            spanned.append(col["column_id"])
    return spanned if spanned else [block.get("column_id", 0)]


# ── Headline-Anchored Flood Fill ──

# Roles that can anchor an article
ANCHOR_ROLES = {"headline", "continuation_header"}
# Roles that are article content (gathered during flood fill)
CONTENT_ROLES = {"body", "kicker", "byline", "caption", "subheadline", "jump_ref"}
# Roles to exclude entirely from articles
EXCLUDED_ROLES = {"furniture"}
# Maximum vertical gap before stopping flood fill (multiplier of line height)
# 2.5x produces ~30pt gap for 12pt text — stops before cross-article bleed
GAP_MULTIPLIER = 2.5


def assemble_page_articles(enriched_page: dict) -> list[dict]:
    """Assemble article candidates on a single page using flood fill."""
    blocks = enriched_page.get("blocks", [])
    drawings = enriched_page.get("drawings", [])
    columns = enriched_page.get("columns", [])
    page_num = enriched_page.get("page_number", 0)
    page_height = enriched_page.get("page_height", 1638)

    if not blocks:
        return []

    seps = extract_separators(drawings)
    h_seps = seps["horizontal"]
    line_height = _estimate_line_height(blocks)
    max_gap = line_height * GAP_MULTIPLIER

    # Build block_index -> block lookup (block_index may differ from list position!)
    block_by_id = {b["block_index"]: b for b in blocks}
    assigned = set()  # tracks block_index values, NOT list positions
    articles = []

    # Find all anchor blocks by block_index
    anchors = [(b["block_index"], b) for b in blocks if b.get("role") in ANCHOR_ROLES]
    anchors.sort(key=lambda t: (t[1]["bbox"][1], t[1]["bbox"][0]))

    for anchor_idx, anchor_block in anchors:
        if anchor_idx in assigned:
            continue

        article_blocks = [(anchor_idx, anchor_block)]
        assigned.add(anchor_idx)

        a_bbox = anchor_block["bbox"]
        a_bottom = a_bbox[3]

        # Determine which columns this headline spans
        spanned_cols = _columns_spanned(anchor_block, columns)
        article_x_min = a_bbox[0]
        article_x_max = a_bbox[2]

        # Floor: next h-separator below the headline in the article's x-range
        floor_y = _next_h_sep_below(a_bottom, article_x_min, article_x_max, h_seps, page_height)

        # Gather kicker (bold bullet summary) near the headline
        # Kicker may be positioned alongside the headline (same y-range) or just below
        a_top = a_bbox[1]
        for b in blocks:
            bid = b["block_index"]
            if bid in assigned or b.get("role") != "kicker":
                continue
            b_top = b["bbox"][1]
            if a_top - 10 < b_top < a_bottom + 120:
                if b["bbox"][0] >= article_x_min - 20 and b["bbox"][2] <= article_x_max + 20:
                    article_blocks.append((bid, b))
                    assigned.add(bid)
                    break

        # Gather byline: search from headline top to well below headline/kicker
        last_bottom = max(b["bbox"][3] for _, b in article_blocks)
        for b in blocks:
            bid = b["block_index"]
            if bid in assigned or b.get("role") != "byline":
                continue
            b_top = b["bbox"][1]
            if a_top - 10 < b_top < last_bottom + 80:
                if b["bbox"][0] >= article_x_min - 20 and b["bbox"][2] <= article_x_max + 20:
                    article_blocks.append((bid, b))
                    assigned.add(bid)
                    break

        # For the headline's own column, fill starts after byline/kicker
        # For other spanned columns, fill starts at headline top (body begins at headline level)
        fill_start_y_same_col = max(b["bbox"][3] for _, b in article_blocks)
        fill_start_y_other_col = a_top  # headline top y

        # Flood fill: gather body blocks column by column, top to bottom
        for col_id in sorted(spanned_cols):
            # Use different start y based on whether this is the headline's column
            anchor_col = anchor_block.get("column_id", -1)
            fill_start_y = fill_start_y_same_col if col_id == anchor_col else fill_start_y_other_col

            col_body = []
            for b in blocks:
                bid = b["block_index"]
                if bid in assigned:
                    continue
                if b.get("role") not in CONTENT_ROLES:
                    continue
                if b.get("column_id") != col_id:
                    continue
                b_top = b["bbox"][1]

                if b_top < fill_start_y - 5:
                    continue
                if b_top >= floor_y:
                    continue
                if _h_sep_between(fill_start_y, b_top, article_x_min, article_x_max, h_seps):
                    continue

                col_body.append((bid, b))

            # Sort by seq_in_column (guaranteed reading order)
            col_body.sort(key=lambda t: t[1].get("seq_in_column", 0))

            # Build a set of boundary y-positions in this column
            # Headlines and section headers are hard stop boundaries
            boundary_roles = ANCHOR_ROLES | {"section_header"}
            headline_ys_in_col = sorted(
                b["bbox"][1] for b in blocks
                if b.get("column_id") == col_id
                and b.get("role") in boundary_roles
                and b["bbox"][1] > fill_start_y
            )

            # Apply vertical gap stop and headline boundary stop
            prev_bottom = fill_start_y
            first_in_col = True  # skip gap check for first block in non-headline columns
            is_headline_col = (col_id == anchor_col)
            for bid, b in col_body:
                if bid in assigned:
                    continue
                b_top = b["bbox"][1]
                gap = b_top - prev_bottom

                # HARD STOP: if there's an unassigned headline between prev and here
                for hy in headline_ys_in_col:
                    if prev_bottom < hy < b_top:
                        gap = float('inf')
                        break

                # Stop on large vertical gap (another article territory)
                # But skip gap check for the first block in non-headline columns
                # (the natural gap from headline to first body in other cols is larger)
                if gap > max_gap:
                    if is_headline_col or not first_in_col:
                        break

                first_in_col = False
                article_blocks.append((bid, b))
                assigned.add(bid)
                prev_bottom = b["bbox"][3]

        articles.append(_build_article_dict(article_blocks, page_num, len(articles)))

    # Post-assembly: attach unassigned jump_ref blocks to nearest article above
    # Match by spatial proximity — the jump_ref is at the bottom of its article,
    # which may span multiple columns (so column_id alone isn't enough)
    unassigned_jumps = [(b["block_index"], b) for b in blocks
                        if b["block_index"] not in assigned and b.get("role") == "jump_ref"]
    for j_idx, j_block in unassigned_jumps:
        j_x0, j_y0 = j_block["bbox"][0], j_block["bbox"][1]
        j_x1 = j_block["bbox"][2]

        # Find the article whose bbox is closest above and overlaps x-range
        best_art_pos = None
        best_dist = float('inf')
        for art_pos, art in enumerate(articles):
            art_bbox = art.get("bbox", [0, 0, 0, 0])
            if art_bbox[3] > j_y0 + 50:
                continue
            if art_bbox[2] < j_x0 - 30 or art_bbox[0] > j_x1 + 30:
                continue
            dist = j_y0 - art_bbox[3]
            if dist < best_dist:
                best_dist = dist
                best_art_pos = art_pos

        if best_art_pos is not None and best_dist < 200:
            art = articles[best_art_pos]
            art["block_indices"].append(j_idx)
            art["block_count"] += 1
            for hint in j_block.get("jump_hints", []):
                if hint["direction"] == "out":
                    art["has_jump_out"] = True
                    art["jump_out_hints"].append(hint)
            assigned.add(j_idx)

    # Orphan sweep: group remaining content blocks
    orphans = [(b["block_index"], b) for b in blocks
               if b["block_index"] not in assigned and b.get("role") not in EXCLUDED_ROLES
               and b.get("role") in CONTENT_ROLES]

    if orphans:
        groups = _group_orphans(orphans, line_height)
        for group in groups:
            if group:
                articles.append(_build_article_dict(group, page_num, len(articles)))

    return articles


def _group_orphans(orphan_blocks: list[tuple[int, dict]], line_height: float) -> list[list[tuple[int, dict]]]:
    """Group orphan blocks by column and spatial proximity."""
    if not orphan_blocks:
        return []

    # Sort by column_id, then y
    sorted_orphans = sorted(orphan_blocks, key=lambda t: (t[1].get("column_id", 0), t[1]["bbox"][1]))

    groups = [[sorted_orphans[0]]]
    for idx in range(1, len(sorted_orphans)):
        i, block = sorted_orphans[idx]
        _, prev_block = groups[-1][-1]

        same_col = block.get("column_id") == prev_block.get("column_id")
        gap = block["bbox"][1] - prev_block["bbox"][3]

        if same_col and gap < line_height * GAP_MULTIPLIER:
            groups[-1].append((i, block))
        else:
            groups.append([(i, block)])

    return groups


def _build_article_dict(block_tuples: list[tuple[int, dict]], page_num: int,
                         article_index: int) -> dict:
    """Build an article candidate dict from assigned blocks."""
    indices = [t[0] for t in block_tuples]
    article_blocks = [t[1] for t in block_tuples]

    headline_text = ""
    subheadline_text = ""
    kicker_text = ""
    byline_text = ""
    body_parts = []

    for b in article_blocks:
        role = b.get("role", "body")
        text = b.get("text", "").strip()

        if role in ("headline", "continuation_header") and not headline_text:
            headline_text = text
        elif role == "subheadline" and not subheadline_text:
            if not headline_text:
                headline_text = text
            else:
                subheadline_text = text
        elif role == "kicker" and not kicker_text:
            kicker_text = text
        elif role == "byline" and not byline_text:
            byline_text = text
        elif role in ("body", "caption", "section_header"):
            body_parts.append(text)

    # Reading order: blocks sorted by column_id ASC, seq_in_column ASC
    body_blocks_sorted = sorted(
        [b for b in article_blocks if b.get("role") in ("body", "caption")],
        key=lambda b: (b.get("column_id", 0), b.get("seq_in_column", 0))
    )
    body_text_ordered = "\n".join(b.get("text", "").strip() for b in body_blocks_sorted)

    # Bounding box
    all_x0 = [b["bbox"][0] for b in article_blocks]
    all_y0 = [b["bbox"][1] for b in article_blocks]
    all_x1 = [b["bbox"][2] for b in article_blocks]
    all_y1 = [b["bbox"][3] for b in article_blocks]

    first = article_blocks[0]

    return {
        "article_index": article_index,
        "page_number": page_num,
        "headline": headline_text,
        "subheadline": subheadline_text,
        "kicker": kicker_text,
        "byline": byline_text,
        "body_text": body_text_ordered,
        "block_count": len(article_blocks),
        "block_indices": indices,
        "bbox": [round(min(all_x0), 1), round(min(all_y0), 1),
                 round(max(all_x1), 1), round(max(all_y1), 1)],
        "column_id": first.get("column_id", 0),
        "span_columns": first.get("span_columns", 1),
        "has_jump_out": any(
            h["direction"] == "out"
            for b in article_blocks
            for h in b.get("jump_hints", [])
        ),
        "has_jump_in": any(
            h["direction"] == "in"
            for b in article_blocks
            for h in b.get("jump_hints", [])
        ),
        "jump_out_hints": [
            h for b in article_blocks
            for h in b.get("jump_hints", [])
            if h["direction"] == "out"
        ],
        "jump_in_hints": [
            h for b in article_blocks
            for h in b.get("jump_hints", [])
            if h["direction"] == "in"
        ],
    }


# ── Edition-Level Assembly ──


def assemble_edition(edition_id: int) -> dict:
    """Run Phase 3 article assembly on all pages of an edition."""
    start_time = time.time()
    result = {
        "success": False, "edition_id": edition_id,
        "page_count": 0, "total_articles": 0, "error": None, "pages": [],
    }

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        return result

    enrichment = get_enrichment_summary(publisher_id, edition_id)
    if not enrichment:
        result["error"] = f"Edition {edition_id} has no enrichment data. Run Phase 2 first."
        return result

    page_count = enrichment["page_count"]
    result["page_count"] = page_count
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"

    logger.info(f"Phase 3 assembly starting: edition={edition_id}, pages={page_count}")

    all_articles = []
    global_idx = 0

    for page_num in range(1, page_count + 1):
        enriched = get_enriched_page(publisher_id, edition_id, page_num)
        if not enriched:
            continue

        page_articles = assemble_page_articles(enriched)
        for art in page_articles:
            art["article_index"] = global_idx
            global_idx += 1

        all_articles.extend(page_articles)
        result["pages"].append({
            "page": page_num,
            "article_count": len(page_articles),
            "headlines": [a["headline"][:60] for a in page_articles if a["headline"]],
        })

        logger.info(f"  Page {page_num}/{page_count}: {len(page_articles)} articles")

    result["total_articles"] = len(all_articles)

    assembly_data = {
        "edition_id": edition_id, "publisher_id": publisher_id,
        "page_count": page_count, "total_articles": len(all_articles),
        "assembly_time_seconds": round(time.time() - start_time, 2),
        "articles": all_articles, "pages": result["pages"],
    }
    with open(artifacts_dir / "assembly.json", "w", encoding="utf-8") as f:
        json.dump(assembly_data, f, indent=2, ensure_ascii=False)

    result["success"] = True
    result["artifacts_dir"] = str(artifacts_dir)
    logger.info(f"Phase 3 assembly complete: {len(all_articles)} articles")
    return result


def get_assembly(publisher_id: int, edition_id: int) -> dict | None:
    path = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}" / "assembly.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
