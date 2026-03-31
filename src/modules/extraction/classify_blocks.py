"""Phase 2: Column detection, block classification, and jump hint pre-tagging.

Rewritten based on the Article View System Architecture document.
Key improvements over previous version:
- Adaptive font thresholds (median-based, not hardcoded)
- seq_in_column for guaranteed reading order
- Jump hint pre-tagging on raw blocks BEFORE assembly
- Better headline detection using relative font size

Does NOT implement article grouping (Phase 3) or stitching (Phase 4).
"""

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from statistics import median

from src.core.config import DATA_DIR
from src.modules.editions.database import get_edition
from src.modules.extraction.extract_pages import (
    ARTIFACTS_BASE,
    get_extraction_summary,
    get_page_artifact,
)

logger = logging.getLogger(__name__)

# ── Column Detection ──

COLUMN_SNAP_TOLERANCE = 20.0  # points — blocks within 20pt share a column
MIN_COLUMN_BLOCKS = 3


def detect_columns(blocks: list[dict], page_width: float) -> list[dict]:
    """Detect newspaper columns by clustering block left-edge x-positions.

    After initial clustering, merges adjacent columns that are too close
    together to be separate physical newspaper columns (gap < 40% of the
    median inter-column gap). This prevents degenerate 0-width columns
    from splitting a single column into two.
    """
    if not blocks:
        return []

    x_positions = [(i, b["bbox"][0]) for i, b in enumerate(blocks)]
    x_positions.sort(key=lambda t: t[1])

    clusters: list[list[tuple[int, float]]] = []
    current_cluster: list[tuple[int, float]] = [x_positions[0]]

    for i in range(1, len(x_positions)):
        _, prev_x = current_cluster[-1]
        _, curr_x = x_positions[i]
        if curr_x - prev_x <= COLUMN_SNAP_TOLERANCE:
            current_cluster.append(x_positions[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [x_positions[i]]
    clusters.append(current_cluster)

    columns = []
    col_id = 0
    for cluster in clusters:
        if len(cluster) < MIN_COLUMN_BLOCKS:
            continue
        xs = [x for _, x in cluster]
        columns.append({
            "column_id": col_id,
            "x_center": round(sum(xs) / len(xs), 1),
            "x_min": round(min(xs), 1),
            "x_max": round(max(xs), 1),
            "block_count": len(cluster),
        })
        col_id += 1

    # Post-merge: merge adjacent columns whose gap is much smaller than
    # the median gap. This handles degenerate columns (e.g. a 0-width
    # cluster at x=732 next to a real column at x=765) that arise when
    # blocks in the same physical column have slightly different x offsets.
    columns = _merge_close_columns(columns)

    return columns


def _merge_close_columns(columns: list[dict]) -> list[dict]:
    """Merge adjacent columns that are too close to be separate newspaper columns.

    Merges happen when either:
    1. A column is degenerate (width < 15pt) and its gap to the nearest
       neighbor is less than the median inter-column gap. This catches the
       common case where blocks in one physical column have slightly different
       x offsets, creating a spurious 0-width cluster.
    2. Two adjacent columns have a gap less than 40% of the median gap.
    """
    if len(columns) < 3:
        return columns

    sorted_cols = sorted(columns, key=lambda c: c["x_center"])
    centers = [c["x_center"] for c in sorted_cols]
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]

    if not gaps:
        return columns

    median_gap = sorted(gaps)[len(gaps) // 2]

    merged = [sorted_cols[0]]
    for i in range(1, len(sorted_cols)):
        prev = merged[-1]
        curr = sorted_cols[i]
        gap = curr["x_center"] - prev["x_center"]

        prev_width = prev["x_max"] - prev["x_min"]
        curr_width = curr["x_max"] - curr["x_min"]

        should_merge = False
        # Rule 1: degenerate column (< 15pt wide) near a neighbor
        if (prev_width < 15 or curr_width < 15) and gap < median_gap:
            should_merge = True
        # Rule 2: very close columns (gap < 40% of median)
        if gap < median_gap * 0.4:
            should_merge = True

        if should_merge:
            all_xs_min = min(prev["x_min"], curr["x_min"])
            all_xs_max = max(prev["x_max"], curr["x_max"])
            total_blocks = prev["block_count"] + curr["block_count"]
            weighted_center = (
                prev["x_center"] * prev["block_count"]
                + curr["x_center"] * curr["block_count"]
            ) / total_blocks
            merged[-1] = {
                "column_id": prev["column_id"],
                "x_center": round(weighted_center, 1),
                "x_min": round(all_xs_min, 1),
                "x_max": round(all_xs_max, 1),
                "block_count": total_blocks,
            }
            logger.debug(
                f"Merged column {curr['column_id']} (x={curr['x_center']:.0f}, w={curr_width:.0f}) "
                f"into column {prev['column_id']} (x={prev['x_center']:.0f}, w={prev_width:.0f}), "
                f"gap={gap:.0f}, median_gap={median_gap:.0f}"
            )
        else:
            merged.append(curr)

    # Re-number column IDs
    for i, col in enumerate(merged):
        col["column_id"] = i

    return merged


def assign_column_ids(blocks: list[dict], columns: list[dict]) -> list[dict]:
    """Assign column_id, span_columns, and seq_in_column to each block."""
    if not columns:
        for i, b in enumerate(blocks):
            b["column_id"] = 0
            b["span_columns"] = 1
            b["seq_in_column"] = i
        return blocks

    col_centers = [(c["column_id"], c["x_center"]) for c in columns]
    avg_col_width = _avg_column_width(columns) if len(columns) >= 2 else 0

    for block in blocks:
        x0 = block["bbox"][0]
        x1 = block["bbox"][2]
        block_width = x1 - x0

        best_col = min(col_centers, key=lambda c: abs(c[1] - x0))
        block["column_id"] = best_col[0]

        if avg_col_width > 0:
            block["span_columns"] = max(1, round(block_width / avg_col_width))
        else:
            block["span_columns"] = 1

    # Assign seq_in_column: sort blocks within each column by y-position
    col_blocks: dict[int, list[dict]] = {}
    for b in blocks:
        col_blocks.setdefault(b["column_id"], []).append(b)

    for col_id, cblocks in col_blocks.items():
        cblocks.sort(key=lambda b: b["bbox"][1])
        for seq, b in enumerate(cblocks):
            b["seq_in_column"] = seq

    return blocks


def _avg_column_width(columns: list[dict]) -> float:
    if len(columns) < 2:
        return 0
    centers = sorted(c["x_center"] for c in columns)
    gaps = [centers[i + 1] - centers[i] for i in range(len(centers) - 1)]
    return sum(gaps) / len(gaps) if gaps else 0


# ── Adaptive Font Analysis ──


def _compute_font_stats(blocks: list[dict]) -> dict:
    """Compute adaptive font size thresholds from the actual blocks."""
    sizes = [b["font_size"] for b in blocks if b.get("font_size", 0) > 0]
    if not sizes:
        return {"median": 9.0, "headline_min": 16.0, "subheadline_min": 12.0}

    med = median(sizes)
    return {
        "median": med,
        "headline_min": med * 1.5,       # 1.5x median = headline
        "subheadline_min": med * 1.25,    # 1.25x median = subheadline
    }


# ── Jump Hint Pre-Tagging ──

# Comprehensive jump patterns — applied to EVERY block before assembly
JUMP_OUT_PATTERNS = [
    # "SEE COUNCIL • BACK PAGE" — normal spacing
    re.compile(r"SEE\s+(\w+)\s*[•·\uf06e]\s*(?:BACK\s+)?PAGE\s*(\d*)", re.IGNORECASE),
    # "S E E  FUNDING • B A C K  PA G E" — letter-spaced
    re.compile(r"S\s*E\s*E\s+(\w+)\s*[•·\u2009]\s*(?:B\s*A\s*C\s*K\s*)?P\s*A\s*G\s*E", re.IGNORECASE),
    # "TURBINES • PAGE 7" or "JASPER• PAGE 10" — keyword+bullet+page (no SEE prefix, Pipestone Star format)
    re.compile(r"^([A-Z]{3,})\s*[•·\uf06e\u2022]\s*PAGE\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE),
    # "S E E COUNTY" — letter-spaced SEE without page ref (Cottonwood Citizen format)
    re.compile(r"^S\s+E\s+E\s+([A-Z]{3,})\s*$", re.IGNORECASE | re.MULTILINE),
    # "• Page 4" or "■ Page 5" — bullet + page ref
    re.compile(r"[\uf06e■]\s*Page\s*(\d+)", re.IGNORECASE),
    # "Continued on page 8"
    re.compile(r"[Cc]ontinued\s+on\s+[Pp]age\s*(\d+)"),
    # "See KEYWORD on page N"
    re.compile(r"See\s+(\w+)\s+on\s+[Pp]age\s*(\d+)", re.IGNORECASE),
]

JUMP_IN_PATTERNS = [
    # "COUNCIL/" or "FUNDING/" at start of text
    re.compile(r"^([A-Z]{2,})\s*/\s*", re.MULTILINE),
    # "FROM PAGE 1" or "F R O M  P A G E  1"
    re.compile(r"FROM\s+PAGE\s*(\d+)", re.IGNORECASE),
    re.compile(r"F\s*R\s*O\s*M\s+P\s*A\s*G\s*E\s*(\d+)", re.IGNORECASE),
    # "Continued from page 1"
    re.compile(r"[Cc]ontinued\s+from\s+[Pp]age\s*(\d+)"),
]


def _tag_jump_hints(block: dict) -> dict:
    """Scan a single block for jump reference patterns and tag it."""
    text = block.get("text", "")
    hints = []

    # Check outgoing patterns
    for pat in JUMP_OUT_PATTERNS:
        for match in pat.finditer(text):
            groups = match.groups()
            keyword = None
            target_page = None

            if len(groups) >= 1 and groups[0]:
                # First group could be keyword or page number
                if groups[0].isdigit():
                    target_page = int(groups[0])
                else:
                    keyword = groups[0].upper()
            if len(groups) >= 2 and groups[1] and groups[1].isdigit():
                target_page = int(groups[1])

            hints.append({
                "direction": "out",
                "keyword": keyword,
                "target_page": target_page,
                "match_text": match.group(0)[:60],
            })

    # Check incoming patterns
    for pat in JUMP_IN_PATTERNS:
        for match in pat.finditer(text[:200]):  # only check start of block
            groups = match.groups()
            keyword = None
            source_page = None

            if len(groups) >= 1 and groups[0]:
                if groups[0].isdigit():
                    source_page = int(groups[0])
                else:
                    keyword = groups[0].upper()

            hints.append({
                "direction": "in",
                "keyword": keyword,
                "source_page": source_page,
                "match_text": match.group(0)[:60],
            })

    block["jump_hints"] = hints
    return block


# ── Block Role Classification ──

# Patterns
BYLINE_PATTERN = re.compile(
    r"^(By |BY |by )[A-Z][A-Za-z\s\.\-']+$", re.MULTILINE
)
FURNITURE_PATTERNS = [
    re.compile(r"^OUR \d+\w* YEAR", re.IGNORECASE),
    re.compile(r"^VOL(UME)?\.?\s*\d+", re.IGNORECASE),
    re.compile(r"WWW\.\w+\.(COM|NET|ORG)", re.IGNORECASE),
    re.compile(r"^\$\d+\.\d{2}$"),
    re.compile(r"^\d[\s\d]+\d$"),
    re.compile(r"^Page \d+", re.IGNORECASE),
    re.compile(r"^Observer/Advocate", re.IGNORECASE),
    re.compile(r"^Wednesday,|^Thursday,|^Friday,", re.IGNORECASE),
    # Page index bar: "Briefly 2 | Classifieds 7 | Sports 5-6"
    re.compile(r"Briefly\s+\d|Classifieds\s+\d", re.IGNORECASE),
    # Letter-spaced mastheads: "C O T T O N W O O D  C O U N T Y" or "W E D N E S D A Y"
    # Detected by 4+ uppercase letters each separated by a space
    re.compile(r"[A-Z] [A-Z] [A-Z] [A-Z]"),
]
CAPTION_PATTERNS = [
    re.compile(r"^(Photo|PHOTO)\s*(by|BY|courtesy|COURTESY)", re.IGNORECASE),
    re.compile(r"^(Above|Left|Right|Below)\s*:", re.IGNORECASE),
    re.compile(r"^(Submitted|Contributed|Staff)\s*(photo|image)", re.IGNORECASE),
    re.compile(r"^(SUBMITTED|JOEL ALVSTAD|KAY GOHR|BAILEY)", re.IGNORECASE),
]
SECTION_HEADER_PATTERNS = [
    re.compile(r"^(SPORTS?|OPINION|COMMUNITY|LEGALS?|OBITUAR|CLASSIFIEDS?|BRIEFS?)$", re.IGNORECASE),
    re.compile(r"^(MT\.?\s*LAKE|WATONWAN|COTTONWOOD)\s*/\s*", re.IGNORECASE),
    re.compile(r"^official\s+proceedings", re.IGNORECASE),
]


def classify_block(block: dict, font_stats: dict) -> str:
    """Classify a block's role using adaptive font thresholds."""
    text = block.get("text", "").strip()
    font_size = block.get("font_size", 0)
    is_bold = block.get("is_bold", False)
    char_count = block.get("char_count", 0)
    hints = block.get("jump_hints", [])

    # Very short or empty
    if char_count < 3:
        return "furniture"

    # Blocks that are purely jump references
    if hints and all(h["direction"] == "out" for h in hints) and char_count < 40:
        return "jump_ref"

    # Furniture patterns
    for pat in FURNITURE_PATTERNS:
        if pat.search(text):
            return "furniture"

    # Caption patterns
    first_line = text.split("\n")[0].strip()
    for pat in CAPTION_PATTERNS:
        if pat.search(first_line):
            return "caption"

    # Byline: "By FIRSTNAME LASTNAME"
    if BYLINE_PATTERN.match(first_line) and char_count < 60:
        return "byline"

    # Continuation header: "KEYWORD/ subtitle" or "KEYWORD\nFROM PAGE N"
    flat = text.replace("\n", " ").strip()
    if re.match(r"^[A-Z]{2,}\s*/\s*", flat):
        return "continuation_header"
    # Also detect "KEYWORD FROM PAGE N" format (e.g. "PLAY\nFROM PAGE 1")
    # _tag_jump_hints runs before this, so we can use its results directly
    jump_hints = block.get("jump_hints", [])
    if any(h.get("direction") == "in" and h.get("source_page") is not None for h in jump_hints):
        return "continuation_header"

    # Section header patterns
    for pat in SECTION_HEADER_PATTERNS:
        if pat.match(first_line):
            return "section_header"

    # Headline: font_size > median * 1.5 (adaptive)
    headline_min = font_stats.get("headline_min", 16.0)
    subheadline_min = font_stats.get("subheadline_min", 12.0)

    if font_size >= headline_min:
        if is_bold or font_size >= headline_min * 1.2:
            return "headline"
        return "subheadline"

    # Subheadline: medium-large bold
    if font_size >= subheadline_min and is_bold:
        if char_count <= 50:
            return "section_header"
        return "subheadline"

    # Kicker/summary: bullet-prefixed bold text
    if is_bold and text.startswith("\uf06e") and char_count < 200:
        return "kicker"

    # Bold text at section-header size, short
    if is_bold and font_size >= font_stats["median"] * 1.1 and char_count <= 40:
        return "section_header"

    # Pull quote attribution: short bold text like "TOM APPEL\nCounty Board Chair"
    # or "— TOM APPEL" that attributes a pull quote. These are decorative,
    # not article body text.
    if is_bold and char_count <= 80:
        flat = text.replace("\n", " ").strip()
        # Attribution pattern: ALL-CAPS name followed by a title
        if re.match(r"^[A-Z]{2,}\s+[A-Z]{2,}", flat) and any(
            w in flat.lower() for w in ("chair", "director", "chief", "mayor",
            "commissioner", "superintendent", "president", "manager", "sheriff",
            "attorney", "officer", "editor", "pastor", "reverend", "dr.",
            "coach", "principal", "administrator", "coordinator", "board")
        ):
            return "furniture"

    # Short bold text that looks like a photo label (e.g. "Michelle Larson")
    # These are names/captions under photos, not body text.
    if is_bold and char_count <= 30 and not text.startswith("\uf06e"):
        # Looks like a proper name (1-3 capitalized words, no punctuation)
        flat = text.replace("\n", " ").strip()
        words = flat.split()
        if 1 <= len(words) <= 4 and all(w[0].isupper() for w in words if w):
            # No sentence-ending punctuation → likely a label
            if not any(flat.endswith(c) for c in (".", "!", "?", ":")):
                return "caption"

    # Default body
    return "body"


def classify_page_blocks(blocks: list[dict], font_stats: dict) -> list[dict]:
    """Classify all blocks on a page."""
    for block in blocks:
        block["role"] = classify_block(block, font_stats)
    return blocks


# ── Full Page Enrichment ──


def enrich_page(page_artifact: dict) -> dict:
    """Run column detection, classification, and jump tagging on a page."""
    blocks = page_artifact.get("blocks", [])
    page_width = page_artifact.get("page_width", 900)
    page_height = page_artifact.get("page_height", 1638)

    # Compute adaptive font stats
    font_stats = _compute_font_stats(blocks)

    # Step 1: Detect columns
    columns = detect_columns(blocks, page_width)

    # Step 2: Assign column IDs + seq_in_column
    assign_column_ids(blocks, columns)

    # Step 3: Tag jump hints on every block
    for b in blocks:
        _tag_jump_hints(b)

    # Step 4: Classify roles using adaptive thresholds
    classify_page_blocks(blocks, font_stats)

    # Build summaries
    role_counts = Counter(b.get("role", "unknown") for b in blocks)
    jump_tagged = sum(1 for b in blocks if b.get("jump_hints"))

    enriched = {
        **page_artifact,
        "columns": columns,
        "column_count": len(columns),
        "font_stats": font_stats,
        "role_summary": dict(role_counts),
        "jump_tagged_blocks": jump_tagged,
        "blocks": blocks,
    }

    return enriched


# ── Edition-Level Enrichment ──


def enrich_edition(edition_id: int) -> dict:
    """Run Phase 2 enrichment on all pages of an extracted edition."""
    start_time = time.time()
    result = {
        "success": False,
        "edition_id": edition_id,
        "page_count": 0,
        "error": None,
        "pages": [],
    }

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        logger.error(result["error"])
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        logger.error(result["error"])
        return result

    summary = get_extraction_summary(publisher_id, edition_id)
    if not summary:
        result["error"] = (
            f"Edition {edition_id} has no extraction artifacts. "
            f"Run Phase 1 extraction first."
        )
        logger.error(result["error"])
        return result

    page_count = summary["page_count"]
    result["page_count"] = page_count

    logger.info(
        f"Phase 2 enrichment starting: edition={edition_id}, "
        f"publisher={publisher_id}, pages={page_count}"
    )

    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    total_role_counts: Counter = Counter()

    for page_num in range(1, page_count + 1):
        page_artifact = get_page_artifact(publisher_id, edition_id, page_num)
        if not page_artifact:
            logger.warning(f"  Page {page_num}: artifact not found, skipping")
            continue

        enriched = enrich_page(page_artifact)

        enriched_path = artifacts_dir / f"page_{page_num:03d}_enriched.json"
        with open(enriched_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2, ensure_ascii=False)

        page_summary = {
            "page": page_num,
            "columns": enriched["column_count"],
            "role_summary": enriched["role_summary"],
            "jump_tagged": enriched["jump_tagged_blocks"],
        }
        result["pages"].append(page_summary)
        total_role_counts.update(enriched["role_summary"])

        logger.info(
            f"  Page {page_num}/{page_count}: "
            f"{enriched['column_count']} cols, "
            f"roles={dict(enriched['role_summary'])}, "
            f"jump_hints={enriched['jump_tagged_blocks']}"
        )

    enrichment_summary = {
        "edition_id": edition_id,
        "publisher_id": publisher_id,
        "page_count": page_count,
        "total_role_counts": dict(total_role_counts),
        "enrichment_time_seconds": round(time.time() - start_time, 2),
        "pages": result["pages"],
    }
    summary_path = artifacts_dir / "enrichment_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(enrichment_summary, f, indent=2, ensure_ascii=False)

    result["success"] = True
    result["total_role_counts"] = dict(total_role_counts)
    result["artifacts_dir"] = str(artifacts_dir)

    elapsed = round(time.time() - start_time, 2)
    logger.info(
        f"Phase 2 enrichment complete: edition={edition_id}, "
        f"pages={page_count}, roles={dict(total_role_counts)}, "
        f"time={elapsed}s"
    )

    return result


def get_enriched_page(publisher_id: int, edition_id: int, page_number: int) -> dict | None:
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    path = artifacts_dir / f"page_{page_number:03d}_enriched.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_enrichment_summary(publisher_id: int, edition_id: int) -> dict | None:
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    path = artifacts_dir / "enrichment_summary.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
