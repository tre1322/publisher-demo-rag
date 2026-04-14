"""Competitive cell claiming: article seeds grow through cells to form article fragments.

Each article seed (headline, continuation header, or orphan body) competes for
ownership of adjacent cells using a deterministic scoring system. This replaces
the flood-fill algorithm with a more robust approach that prevents articles from
accidentally absorbing their neighbors.

Reading order is lane-based: within each article fragment, cells are grouped by
column (lane) and read top-to-bottom within each lane before moving to the next.
"""

import logging
from dataclasses import dataclass, field

from src.modules.extraction.page_grid import Cell, build_cell_adjacency

logger = logging.getLogger(__name__)


@dataclass
class ArticleSeed:
    """An anchor point for an article on a page."""
    seed_id: int
    page: int
    cell_id: int
    kind: str  # "title", "continuation_header", "orphan_body"
    label: str | None = None  # keyword for continuation matching
    headline_text: str = ""
    confidence: float = 1.0
    column_ids: tuple = ()  # columns this seed's headline spans


@dataclass
class ArticleFragment:
    """A single-page article fragment built from claimed cells."""
    seed_id: int
    page: int
    cell_ids: list = field(default_factory=list)
    lanes: list = field(default_factory=list)  # list of (column_id, [cell_ids]) ordered
    headline: str = ""
    byline: str = ""
    kind: str = ""  # "title", "continuation_header", "orphan_body"
    label: str | None = None
    jump_out_keyword: str | None = None  # if this fragment has a jump-out reference
    jump_out_target_page: int | None = None
    body_text: str = ""  # assembled after lane ordering
    top_y: float = 0.0  # top-most y of all claimed cells
    bottom_y: float = 0.0  # bottom-most y of all claimed cells
    bbox: tuple = ()  # (x0, y0, x1, y1) bounding box of all claimed cells


def create_seeds(cells: list[Cell], blocks: list[dict], page_num: int) -> list[ArticleSeed]:
    """Create article seeds from title cells and continuation header cells.

    Seeds are ranked by confidence:
    1. Explicit headline cells (highest)
    2. Continuation header cells
    3. Orphan body cells at top of columns (lowest)
    """
    seeds = []
    seed_id = 0

    # Sort cells by y-position then x-position for deterministic ordering
    sorted_cells = sorted(cells, key=lambda c: (c.y0, c.x0))

    for cell in sorted_cells:
        if cell.kind == "title":
            # Find the headline text and determine column span from block bbox
            headline_blocks = [blocks[bi] for bi in cell.block_indices
                             if blocks[bi].get("role") == "headline"]
            headline_text = " ".join(b["text"].replace("\n", " ").strip()
                                   for b in headline_blocks)[:200]

            # Use the cell's column_ids (derived from the cell's actual bbox by
            # page_grid._cell_column_ids).  This is the most reliable source for
            # multi-column headlines because the cell already spans every column
            # that the headline occupies.
            #
            # Fall back to the block's span_columns estimate ONLY when the cell
            # reports a single column — that can happen when a wide headline
            # block sits inside a narrow cell that wasn't split by the grid.
            seed_col_ids = tuple(cell.column_ids)
            if headline_blocks and len(seed_col_ids) <= 1:
                hb = headline_blocks[0]
                span = hb.get("span_columns", 1)
                base_col = hb.get("column_id", cell.column_ids[0] if cell.column_ids else 0)
                span_cols = tuple(range(base_col, base_col + span))
                if len(span_cols) > len(seed_col_ids):
                    seed_col_ids = span_cols

            # Wider headlines (more columns) indicate more prominent articles
            # that own more of the page. Give them higher confidence so they
            # win cell competition against smaller neighboring headlines.
            span_confidence = 1.0 + len(seed_col_ids) * 0.1

            seeds.append(ArticleSeed(
                seed_id=seed_id,
                page=page_num,
                cell_id=cell.cell_id,
                kind="title",
                headline_text=headline_text,
                confidence=span_confidence,
                column_ids=seed_col_ids,
            ))
            seed_id += 1

        elif cell.kind == "continuation_header":
            # Extract the keyword from continuation header blocks
            cont_blocks = [blocks[bi] for bi in cell.block_indices
                         if blocks[bi].get("role") == "continuation_header"]
            if cont_blocks:
                import re
                text = cont_blocks[0]["text"].replace("\n", " ").strip()
                # Match "KEYWORD/" format or "KEYWORD FROM PAGE N" format
                match = re.match(r"^([A-Z]{2,})\s*/", text) or re.match(r"^([A-Z]{2,})\s+FROM\s+PAGE", text, re.IGNORECASE)
                label = match.group(1).upper() if match else re.sub(r"\s*FROM\s+PAGE.*", "", text, flags=re.IGNORECASE).strip()[:20].upper()
            else:
                label = None

            seeds.append(ArticleSeed(
                seed_id=seed_id,
                page=page_num,
                cell_id=cell.cell_id,
                kind="continuation_header",
                label=label,
                headline_text=cont_blocks[0]["text"][:100] if cont_blocks else "",
                confidence=0.9,
                column_ids=cell.column_ids,
            ))
            seed_id += 1

    # Check for orphan body cells at top of columns (no seed above them)
    claimed_columns = set()
    for s in seeds:
        for col in s.column_ids:
            claimed_columns.add(col)

    for cell in sorted_cells:
        if cell.kind == "body" and cell.y0 < 200:  # near top of page
            for col in cell.column_ids:
                if col not in claimed_columns:
                    seeds.append(ArticleSeed(
                        seed_id=seed_id,
                        page=page_num,
                        cell_id=cell.cell_id,
                        kind="orphan_body",
                        confidence=0.5,
                        column_ids=cell.column_ids,
                    ))
                    claimed_columns.add(col)
                    seed_id += 1

    return seeds


def score_cell_for_seed(
    seed: ArticleSeed,
    cell: Cell,
    prev_cell: Cell | None,
    blocks: list[dict],
    owned_cells: set[int],
) -> float:
    """Score how well a cell fits with a seed's article.

    Returns a score where higher = better fit. Negative = shouldn't claim.
    """
    score = 0.0

    # Same column as previous cell (+8)
    if prev_cell and cell.column_ids and prev_cell.column_ids:
        if set(cell.column_ids) & set(prev_cell.column_ids):
            score += 8.0

    # Directly below previous cell (+7)
    if prev_cell and abs(cell.y0 - prev_cell.y1) < 10:
        if set(cell.column_ids) & set(prev_cell.column_ids):
            score += 7.0

    # Cell is in seed's owned columns (+5)
    if seed.column_ids and set(cell.column_ids) & set(seed.column_ids):
        score += 5.0

    # Jump_ref cells belong to the article above them (+6)
    if cell.kind == "jump_ref":
        score += 6.0

    # Typography similarity — check if block font sizes match body text (+4)
    cell_sizes = [blocks[bi].get("font_size", 9) for bi in cell.block_indices]
    if cell_sizes:
        avg_size = sum(cell_sizes) / len(cell_sizes)
        if 7 <= avg_size <= 12:  # body text range
            score += 4.0

    # PENALTIES

    # Crossing a hard separator (-9)
    if prev_cell:
        if cell.hard_top and prev_cell.hard_bottom:
            # Both have hard boundaries between them — but NOT for jump_ref cells
            if cell.kind != "jump_ref":
                score -= 9.0

    # Cell contains a title/headline for a DIFFERENT article (-100)
    # Headlines are DEFINITIVE article boundaries — never cross them
    if cell.kind == "title" and cell.cell_id != seed.cell_id:
        score -= 100.0

    # Cell is a continuation header for a DIFFERENT article (-100)
    # Continuation headers are DEFINITIVE article boundaries — never cross them
    if cell.kind == "continuation_header" and cell.cell_id != seed.cell_id:
        score -= 100.0

    # Cell contains furniture/ads (-6)
    if cell.kind in ("furniture", "ad"):
        score -= 6.0

    # Cell is a photo caption (-15)
    # Captions belong to photos, not adjacent articles. The positional bonuses
    # (same column +8, directly below +7, in span +5) total +20, so -15 isn't
    # enough on its own — but combined with any other penalty it blocks claiming.
    # Captions are also filtered in build_fragments(), so this is defense in depth.
    if cell.kind == "caption":
        score -= 15.0

    # Cell is outside seed's column span — penalty scales with distance
    if seed.column_ids and cell.column_ids:
        # Check if cell is within the seed's headline column span
        seed_min_col = min(seed.column_ids)
        seed_max_col = max(seed.column_ids)
        cell_min_col = min(cell.column_ids)

        if seed_min_col <= cell_min_col <= seed_max_col:
            # Cell is within headline span — no penalty
            pass
        elif cell_min_col == seed_max_col + 1:
            # Cell is one column to the right of span.
            # Jump_ref cells get a small penalty (they legitimately appear
            # at article boundaries). Body cells get a strong penalty to
            # prevent bleeding into adjacent articles' columns.
            if cell.kind == "jump_ref":
                score -= 3.0
            else:
                score -= 20.0
        else:
            # Cell is far outside span — hard penalty
            min_dist = min(abs(sc - cc) for sc in seed.column_ids for cc in cell.column_ids)
            score -= 15.0 * min_dist

    return score


def claim_cells(
    seeds: list[ArticleSeed],
    cells: list[Cell],
    adjacency: dict[int, list[int]],
    blocks: list[dict],
) -> dict[int, list[int]]:
    """Competitive cell claiming: seeds grow through adjacent cells.

    Each seed starts at its anchor cell and grows downward/rightward through
    adjacent cells. When two seeds compete for the same cell, the one with
    the higher score wins.

    Args:
        seeds: Article seeds, sorted by confidence descending.
        cells: Page cells from the grid.
        adjacency: Cell adjacency graph.
        blocks: Enriched blocks.

    Returns:
        Dict mapping seed_id -> list of claimed cell_ids.
    """
    cell_map = {c.cell_id: c for c in cells}
    ownership: dict[int, int] = {}  # cell_id -> seed_id
    seed_cells: dict[int, list[int]] = {s.seed_id: [] for s in seeds}

    # Sort seeds by confidence descending
    sorted_seeds = sorted(seeds, key=lambda s: -s.confidence)

    for seed in sorted_seeds:
        if seed.cell_id not in cell_map:
            continue

        # Start with the seed's anchor cell
        frontier = [seed.cell_id]
        visited = set()
        prev_cell = None

        while frontier:
            current_id = frontier.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            current_cell = cell_map.get(current_id)
            if current_cell is None:
                continue

            # Score this cell for this seed
            score = score_cell_for_seed(
                seed, current_cell, prev_cell, blocks,
                set(seed_cells[seed.seed_id])
            )

            # If seed's own anchor cell, always claim it
            if current_id == seed.cell_id:
                score = 100.0

            if score <= 0:
                continue

            # Check if another seed already owns this cell
            if current_id in ownership:
                existing_seed_id = ownership[current_id]
                # Don't steal from higher-confidence seeds
                existing_seed = next((s for s in seeds if s.seed_id == existing_seed_id), None)
                if existing_seed and existing_seed.confidence >= seed.confidence:
                    continue

            # Claim the cell
            if current_id in ownership:
                old_owner = ownership[current_id]
                seed_cells[old_owner].remove(current_id)
            ownership[current_id] = seed.seed_id
            seed_cells[seed.seed_id].append(current_id)
            prev_cell = current_cell

            # Add adjacent cells to frontier (only downward and rightward)
            for neighbor_id in adjacency.get(current_id, []):
                if neighbor_id in visited:
                    continue
                neighbor = cell_map.get(neighbor_id)
                if neighbor is None:
                    continue

                # Only grow downward or rightward (not upward or leftward)
                if neighbor.y0 >= current_cell.y0 - 5 or neighbor.x0 >= current_cell.x1 - 5:
                    frontier.append(neighbor_id)

    # NOTE: jump_ref cells are intentionally left unclaimed.
    # Jump keywords are collected from ALL blocks on the page in build_fragments()
    # and matched to continuations during the stitching phase.

    # Fallback: seeds that only claimed their headline cell (no body)
    # Try to find body cells directly below in their column span
    for seed in sorted_seeds:
        claimed = seed_cells[seed.seed_id]
        if len(claimed) <= 1:
            anchor = cell_map.get(seed.cell_id)
            if anchor is None:
                continue

            # Find unclaimed body cells below the anchor in the same column range
            seed_cols = set(seed.column_ids)
            for cell in cells:
                if cell.cell_id in ownership:
                    continue
                if cell.kind in ("title", "continuation_header", "furniture", "jump_ref"):
                    continue
                # Must be below anchor
                if cell.y0 < anchor.y1 - 5:
                    continue
                # Must share at least one column with seed
                if not (set(cell.column_ids) & seed_cols):
                    continue
                # Must not be too far below (within 500pt)
                if cell.y0 - anchor.y1 > 500:
                    continue
                # Claim it
                ownership[cell.cell_id] = seed.seed_id
                seed_cells[seed.seed_id].append(cell.cell_id)

    # Second pass: extend continuation_header seeds to claim adjacent unclaimed
    # body cells. Back-page continuations often span more text than the initial
    # BFS reaches (separator lines or other continuation headers can block
    # downward growth in the seed's column). Use adjacency-based BFS that
    # allows growth into adjacent columns (±1) since continuation text on
    # back pages typically spans multiple columns.
    #
    # Key constraint: don't grow below the y-position of other continuation
    # headers on the same page — those mark the start of a different article.
    for seed in sorted_seeds:
        if seed.kind != "continuation_header":
            continue

        claimed = seed_cells[seed.seed_id]
        if not claimed:
            continue

        # Compute y_max: the y-position of the next continuation header or
        # title below this seed. This prevents SCHOOL (y=412) from growing
        # into MAYOR's region (y=655+) regardless of column. On back pages,
        # continuations are stacked vertically and their body text spans
        # multiple columns, so a simple y cutoff is more reliable than
        # column-aware boundaries.
        anchor = cell_map.get(seed.cell_id)
        if anchor is None:
            continue
        seed_y = anchor.y0

        y_max = float("inf")
        for other_seed in seeds:
            if other_seed.seed_id == seed.seed_id:
                continue
            if other_seed.kind in ("continuation_header", "title"):
                other_cell = cell_map.get(other_seed.cell_id)
                if other_cell and other_cell.y0 > seed_y + 5:
                    y_max = min(y_max, other_cell.y0)

        # BFS from already-claimed cells through adjacency graph
        frontier = list(claimed)
        visited = set(claimed)

        while frontier:
            current_id = frontier.pop(0)
            current_cell = cell_map.get(current_id)
            if current_cell is None:
                continue

            for neighbor_id in adjacency.get(current_id, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                neighbor = cell_map.get(neighbor_id)
                if neighbor is None:
                    continue

                # Skip already-owned cells
                if neighbor_id in ownership:
                    continue

                # Stop at article boundaries
                if neighbor.kind in ("title", "continuation_header", "furniture", "ad"):
                    continue

                # Don't grow below the next continuation header or title
                if neighbor.y0 >= y_max - 5:
                    continue

                # Only grow downward or sideways (not upward)
                if neighbor.y1 < current_cell.y0 - 5:
                    continue

                # Allow growth into columns within ±1 of any claimed column
                claimed_cols = set()
                for cid in seed_cells[seed.seed_id]:
                    c = cell_map.get(cid)
                    if c:
                        for col in c.column_ids:
                            claimed_cols.add(col)
                            claimed_cols.add(col - 1)
                            claimed_cols.add(col + 1)
                if not (set(neighbor.column_ids) & claimed_cols):
                    continue

                # Claim it and continue growing from this cell
                ownership[neighbor_id] = seed.seed_id
                seed_cells[seed.seed_id].append(neighbor_id)
                frontier.append(neighbor_id)

    return seed_cells


def build_fragments(
    seeds: list[ArticleSeed],
    seed_cells: dict[int, list[int]],
    cells: list[Cell],
    blocks: list[dict],
    page_num: int,
) -> list[ArticleFragment]:
    """Build article fragments from seeds and their claimed cells.

    Each fragment has lane-based reading order: cells are grouped by column
    and read top-to-bottom within each column before moving to the next.
    """
    cell_map = {c.cell_id: c for c in cells}
    fragments = []

    for seed in seeds:
        claimed = seed_cells.get(seed.seed_id, [])
        if not claimed:
            continue

        # Build lanes: group claimed cells by column, sort by y within each column
        lane_dict: dict[int, list[Cell]] = {}
        for cid in claimed:
            cell = cell_map.get(cid)
            if cell is None:
                continue
            # Use the first column_id for lane assignment
            col = cell.column_ids[0] if cell.column_ids else 0
            lane_dict.setdefault(col, []).append(cell)

        # Sort lanes by column_id (left to right)
        # Sort cells within each lane by y (top to bottom)
        lanes = []
        for col_id in sorted(lane_dict.keys()):
            lane_cells = sorted(lane_dict[col_id], key=lambda c: c.y0)
            lanes.append((col_id, [c.cell_id for c in lane_cells]))

        # Extract headline text
        headline = seed.headline_text
        if not headline and seed.kind == "title":
            for cid in claimed:
                cell = cell_map.get(cid)
                if cell and cell.kind == "title":
                    for bi in cell.block_indices:
                        if blocks[bi].get("role") == "headline":
                            headline = blocks[bi]["text"].replace("\n", " ").strip()
                            break
                    if headline:
                        break

        # Extract byline
        byline = ""
        for cid in claimed:
            cell = cell_map.get(cid)
            if cell:
                for bi in cell.block_indices:
                    if blocks[bi].get("role") == "byline":
                        import re
                        raw = blocks[bi]["text"].replace("\n", " ").strip()
                        match = re.match(r"^[Bb]y\s+(.+)", raw)
                        byline = match.group(1).strip() if match else raw
                        break
            if byline:
                break

        # Detect jump-out references from claimed cells
        jump_out_keyword = None
        jump_out_target = None
        for cid in claimed:
            cell = cell_map.get(cid)
            if cell:
                for bi in cell.block_indices:
                    hints = blocks[bi].get("jump_hints", [])
                    for h in hints:
                        if h.get("direction") == "out":
                            jump_out_keyword = h.get("keyword")
                            jump_out_target = h.get("target_page")

        # Assemble body text in newspaper reading order: column by column
        # (left to right), top to bottom within each column. This matches
        # how newspaper articles flow — down one column, then continue at
        # the top of the next column.
        #
        # We use the block's physical x-position (quantized to ~100pt bands)
        # instead of the extraction column_id.  On back pages, continuation
        # articles often span many physical newspaper columns, and two
        # adjacent newspaper columns can be assigned the same extraction
        # column_id because their x-ranges are close.  Sorting by extraction
        # column_id would interleave blocks from different newspaper columns
        # by y-position, scrambling the reading order.  Using the actual
        # x-midpoint avoids this.
        _COL_BAND = 100  # ~1.4 inches — safely narrower than newspaper columns
        body_blocks = []
        for cid in claimed:
            cell = cell_map.get(cid)
            if cell is None:
                continue
            for bi in cell.block_indices:
                role = blocks[bi].get("role", "body")
                # Skip non-body blocks (headlines, furniture, jump refs, cont headers)
                if role in ("headline", "furniture", "jump_ref", "continuation_header", "caption", "kicker", "section_header", "subheadline"):
                    continue
                text = blocks[bi]["text"].strip()
                if text:
                    bbox = blocks[bi]["bbox"]
                    y = bbox[1]
                    # Use x0 (left edge) for column banding — x_mid varies with
                    # line length and causes blocks from the same column to land
                    # in different bands (short lines get smaller x_mid)
                    x0 = bbox[0]
                    body_blocks.append((x0, y, text))

        # Sort by physical column band (left to right), then y (top to bottom)
        body_blocks.sort(key=lambda b: (int(b[0] // _COL_BAND), b[1]))
        body_parts = [text for _, _, text in body_blocks]

        # Compute spatial bounds
        claimed_cells = [cell_map[cid] for cid in claimed if cid in cell_map]
        top_y = min(c.y0 for c in claimed_cells) if claimed_cells else 0
        bottom_y = max(c.y1 for c in claimed_cells) if claimed_cells else 0
        bbox = (
            min(c.x0 for c in claimed_cells),
            top_y,
            max(c.x1 for c in claimed_cells),
            bottom_y,
        ) if claimed_cells else ()

        fragment = ArticleFragment(
            seed_id=seed.seed_id,
            page=page_num,
            cell_ids=claimed,
            lanes=lanes,
            headline=headline,
            byline=byline,
            kind=seed.kind,
            label=seed.label,
            jump_out_keyword=jump_out_keyword,
            jump_out_target_page=jump_out_target,
            body_text="\n\n".join(body_parts),
            top_y=top_y,
            bottom_y=bottom_y,
            bbox=bbox,
        )
        fragments.append(fragment)

    # Collect ALL jump-out hints from ALL blocks on the page (not just claimed cells)
    # These will be used during cross-page stitching
    page_jump_outs = []
    for cell in cells:
        for bi in cell.block_indices:
            hints = blocks[bi].get("jump_hints", [])
            for h in hints:
                if h.get("direction") == "out" and h.get("keyword"):
                    page_jump_outs.append({
                        "keyword": h["keyword"],
                        "target_page": h.get("target_page"),
                        "block_y": blocks[bi]["bbox"][1],
                        "block_col": blocks[bi].get("column_id"),
                    })

    # Attach page-level jump-out list to fragments for the stitcher to use
    for frag in fragments:
        frag._page_jump_outs = page_jump_outs

    return fragments


def _sweep_unclaimed_into_continuations(
    fragments: list[ArticleFragment],
    seed_cells: dict[int, list[int]],
    cells: list[Cell],
    blocks: list[dict],
) -> None:
    """Sweep unclaimed body cells into continuation_header fragments.

    On back pages, continuation articles often span 3-5 physical newspaper
    columns. Cell claiming grows ±1 column from the seed, leaving body
    cells in distant columns unclaimed. This function finds those unclaimed
    body cells and appends their text to the nearest continuation_header
    fragment in the same y-band.
    """
    cell_map = {c.cell_id: c for c in cells}
    owned_cells = set()
    for cids in seed_cells.values():
        owned_cells.update(cids)

    # Collect unclaimed body cells
    unclaimed = []
    for c in cells:
        if c.cell_id in owned_cells:
            continue
        if c.kind in ("title", "continuation_header", "furniture", "ad", "jump_ref", "caption"):
            continue
        # Must contain at least one body block
        has_body = any(blocks[bi].get("role") == "body" for bi in c.block_indices)
        if has_body:
            unclaimed.append(c)

    if not unclaimed:
        return

    # Find continuation_header fragments and their y-bands
    cont_frags = [f for f in fragments if f.kind == "continuation_header"]
    if not cont_frags:
        return

    # Collect all boundary positions with their x-ranges, so y-bands
    # are column-aware. A SCHOOL header at x=594 shouldn't constrain
    # a COUNTY continuation's body at x=312.
    boundaries = []
    for f in fragments:
        if f.kind in ("continuation_header", "title"):
            x_min = f.bbox[0] if f.bbox else 0
            x_max = f.bbox[2] if f.bbox else 9999
            boundaries.append((f.top_y, x_min, x_max))

    _COL_BAND = 100

    for cont in cont_frags:
        # Find unclaimed cells and compute per-cell y_max based on
        # boundaries in nearby columns only
        swept_blocks = []
        for cell in unclaimed:
            if cell.y0 < cont.top_y - 30:
                continue

            # Column-aware y_max: only consider boundaries whose x-range
            # overlaps with this cell's x-range (within 1 column band)
            cell_x_mid = (cell.x0 + cell.x1) / 2
            y_max = float("inf")
            for by, bx_min, bx_max in sorted(boundaries):
                if by <= cont.top_y + 5:
                    continue
                # Check if this boundary is in a nearby column
                bx_mid = (bx_min + bx_max) / 2
                if abs(cell_x_mid - bx_mid) < _COL_BAND * 1.5:
                    y_max = by
                    break

            if cell.y0 >= y_max - 5:
                continue

            for bi in cell.block_indices:
                role = blocks[bi].get("role", "body")
                if role in ("headline", "furniture", "jump_ref", "continuation_header",
                            "caption", "kicker", "section_header", "subheadline"):
                    continue
                text = blocks[bi]["text"].strip()
                if text:
                    bbox = blocks[bi]["bbox"]
                    x0 = bbox[0]
                    y = bbox[1]
                    swept_blocks.append((x0, y, text))

        if not swept_blocks:
            continue

        # Rebuild the fragment's body text from ALL blocks (original + swept)
        # sorted together, so the swept text interleaves at the correct
        # reading position rather than being appended at the end.
        original_blocks = []
        for cid in cont.cell_ids:
            c = cell_map.get(cid)
            if c is None:
                continue
            for bi in c.block_indices:
                role = blocks[bi].get("role", "body")
                if role in ("headline", "furniture", "jump_ref", "continuation_header",
                            "caption", "kicker", "section_header", "subheadline"):
                    continue
                text = blocks[bi]["text"].strip()
                if text:
                    original_blocks.append((blocks[bi]["bbox"][0], blocks[bi]["bbox"][1], text))

        all_blocks = original_blocks + swept_blocks
        all_blocks.sort(key=lambda b: (int(b[0] // _COL_BAND), b[1]))
        cont.body_text = "\n\n".join(t for _, _, t in all_blocks)
        # If no unclaimed cells fall in the vertical range, leave bottom_y unchanged
        # (default=cont.bottom_y makes the inner max a no-op in that case).
        cont.bottom_y = max(
            cont.bottom_y,
            max((c.y1 for c in unclaimed if cont.top_y - 30 <= c.y0 < y_max - 5), default=cont.bottom_y),
        )

        logger.info(
            f"  Swept {len(swept_blocks)} unclaimed blocks into continuation "
            f"'{cont.label}' (total {len(all_blocks)} blocks)"
        )


def assemble_page(
    page_num: int,
    enriched_page: dict,
    raw_page: dict,
) -> list[ArticleFragment]:
    """Full pipeline: page grid → seeds → competitive claiming → fragments.

    Args:
        page_num: 1-indexed page number.
        enriched_page: Phase 2 enriched page artifact.
        raw_page: Phase 1 raw page artifact (for drawings).

    Returns:
        List of ArticleFragment objects for this page.
    """
    from src.modules.extraction.page_grid import build_page_grid, build_cell_adjacency

    blocks = enriched_page.get("blocks", [])
    columns = enriched_page.get("columns", [])
    drawings = raw_page.get("drawings", [])
    page_width = enriched_page.get("page_width", 900)
    page_height = enriched_page.get("page_height", 1638)

    # Step 1: Build page grid
    cells = build_page_grid(
        page_width=page_width,
        page_height=page_height,
        columns=columns,
        drawings=drawings,
        blocks=blocks,
    )

    if not cells:
        logger.warning(f"Page {page_num}: no cells created")
        return []

    # Step 2: Build adjacency
    adjacency = build_cell_adjacency(cells)

    # Step 3: Create seeds
    seeds = create_seeds(cells, blocks, page_num)

    if not seeds:
        logger.warning(f"Page {page_num}: no seeds found")
        return []

    # Step 4: Competitive cell claiming
    seed_cells = claim_cells(seeds, cells, adjacency, blocks)

    # Step 5: Build fragments
    fragments = build_fragments(seeds, seed_cells, cells, blocks, page_num)

    # Step 6: Sweep unclaimed body cells into continuation_header fragments.
    # On back pages, continuation articles often span 3-5 newspaper columns.
    # Cell claiming can only reach ±1 column from the seed, leaving body cells
    # in distant columns unclaimed.  This step finds unclaimed body cells in
    # the same y-band as a continuation_header and appends their text.
    _sweep_unclaimed_into_continuations(fragments, seed_cells, cells, blocks)

    logger.info(
        f"Page {page_num}: {len(cells)} cells, {len(seeds)} seeds, "
        f"{len(fragments)} fragments"
    )

    return fragments
