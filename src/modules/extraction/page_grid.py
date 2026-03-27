"""Page partition grid: converts a page into cells using separator lines + column gutters.

This replaces flood-fill article assembly with a geometric partition approach.
The page is divided into rectangular cells by:
1. Column gutters (vertical boundaries from x-position clustering)
2. Horizontal separator lines (vector drawings from the PDF)
3. Prolonged separator lines (extended to form a complete grid)

Each text block is assigned to exactly one cell by containment.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Cut:
    """A boundary line (horizontal or vertical) that partitions the page."""
    axis: str  # "h" for horizontal, "v" for vertical
    position: float  # y for horizontal, x for vertical
    start: float  # x-start for horizontal, y-start for vertical
    end: float  # x-end for horizontal, y-end for vertical
    hard: bool = True  # hard = vector line or column gutter, soft = whitespace


@dataclass
class Cell:
    """A rectangular region on the page bounded by cuts."""
    cell_id: int
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    column_ids: tuple = ()  # which newspaper columns this cell spans
    block_indices: list = field(default_factory=list)  # indices into the page's block list
    kind: str = "unknown"  # title, body, caption, ad, furniture, jump_ref, continuation_header, unknown
    hard_top: bool = False
    hard_bottom: bool = False
    hard_left: bool = False
    hard_right: bool = False

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return self.width * self.height

    @property
    def center_x(self):
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self):
        return (self.y0 + self.y1) / 2


def build_page_grid(
    page_width: float,
    page_height: float,
    columns: list[dict],
    drawings: list[dict],
    blocks: list[dict],
    min_h_line_width: float = 80.0,
    min_v_line_height: float = 80.0,
    whitespace_gap_threshold: float = 30.0,
) -> list[Cell]:
    """Build a grid of cells from page geometry.

    Args:
        page_width: Page width in points.
        page_height: Page height in points.
        columns: Column definitions from Phase 2 (column_id, x_center, x_min, x_max).
        drawings: Raw drawings from Phase 1 (type, rect).
        blocks: Enriched blocks from Phase 2 (bbox, role, column_id, etc.).
        min_h_line_width: Minimum width for a horizontal line to be a separator.
        min_v_line_height: Minimum height for a vertical line to be a separator.
        whitespace_gap_threshold: Minimum vertical gap to create a soft horizontal cut.

    Returns:
        List of Cell objects with blocks assigned.
    """
    # Step 1: Collect hard cuts from column gutters
    v_cuts = _column_gutter_cuts(columns, page_height)

    # Step 2: Collect hard cuts from vector separator lines
    h_line_cuts, v_line_cuts = _separator_line_cuts(
        drawings, page_width, page_height, min_h_line_width, min_v_line_height
    )

    # Step 3: Add soft cuts from vertical whitespace gaps between blocks
    h_soft_cuts = _whitespace_gap_cuts(blocks, columns, whitespace_gap_threshold, page_width)

    # Step 4: Merge all horizontal cuts (hard lines + soft whitespace)
    all_h_cuts = h_line_cuts + h_soft_cuts
    all_v_cuts = v_cuts + v_line_cuts

    # Step 5: Prolong cuts to form a complete grid
    all_h_cuts = _prolong_horizontal_cuts(all_h_cuts, all_v_cuts, page_width)

    # Step 6: Build grid edges (sorted unique y and x positions)
    x_edges = sorted(set(
        [0.0, page_width] +
        [c.position for c in all_v_cuts]
    ))
    y_edges = sorted(set(
        [0.0, page_height] +
        [c.position for c in all_h_cuts]
    ))

    # Step 7: Create cells from grid intersections
    cells = []
    cell_id = 0
    for i in range(len(y_edges) - 1):
        for j in range(len(x_edges) - 1):
            y0, y1 = y_edges[i], y_edges[i + 1]
            x0, x1 = x_edges[j], x_edges[j + 1]

            # Skip tiny cells (< 10pt in either dimension)
            if (x1 - x0) < 10 or (y1 - y0) < 10:
                continue

            # Determine which columns this cell spans
            col_ids = _cell_column_ids(x0, x1, columns)

            # Check if boundaries are hard
            hard_top = any(
                c.hard and abs(c.position - y0) < 2 and c.start <= x0 + 5 and c.end >= x1 - 5
                for c in all_h_cuts
            )
            hard_bottom = any(
                c.hard and abs(c.position - y1) < 2 and c.start <= x0 + 5 and c.end >= x1 - 5
                for c in all_h_cuts
            )
            hard_left = any(
                c.hard and abs(c.position - x0) < 2
                for c in all_v_cuts
            )
            hard_right = any(
                c.hard and abs(c.position - x1) < 2
                for c in all_v_cuts
            )

            cell = Cell(
                cell_id=cell_id,
                page=0,  # set by caller
                x0=x0, y0=y0, x1=x1, y1=y1,
                column_ids=tuple(col_ids),
                hard_top=hard_top,
                hard_bottom=hard_bottom,
                hard_left=hard_left,
                hard_right=hard_right,
            )
            cells.append(cell)
            cell_id += 1

    # Step 8: Assign blocks to cells by containment
    _assign_blocks_to_cells(blocks, cells)

    # Step 9: Classify cells by their dominant block role
    _classify_cells(cells, blocks)

    # Remove empty cells (no blocks assigned)
    cells = [c for c in cells if c.block_indices]

    logger.debug(
        f"Page grid: {len(x_edges)-1}x{len(y_edges)-1} grid, "
        f"{len(cells)} non-empty cells from "
        f"{len(all_h_cuts)} h-cuts + {len(all_v_cuts)} v-cuts"
    )

    return cells


def _column_gutter_cuts(columns: list[dict], page_height: float) -> list[Cut]:
    """Create vertical cuts from column gutters (spaces between columns)."""
    if len(columns) < 2:
        return []

    cuts = []
    sorted_cols = sorted(columns, key=lambda c: c["x_center"])

    for i in range(len(sorted_cols) - 1):
        right_of_left = sorted_cols[i]["x_max"]
        left_of_right = sorted_cols[i + 1]["x_min"]
        gutter_center = (right_of_left + left_of_right) / 2

        cuts.append(Cut(
            axis="v",
            position=round(gutter_center, 1),
            start=0.0,
            end=page_height,
            hard=True,
        ))

    return cuts


def _separator_line_cuts(
    drawings: list[dict],
    page_width: float,
    page_height: float,
    min_h_width: float,
    min_v_height: float,
) -> tuple[list[Cut], list[Cut]]:
    """Extract horizontal and vertical separator cuts from vector drawings."""
    h_cuts = []
    v_cuts = []

    for d in drawings:
        rect = d.get("rect", [0, 0, 0, 0])
        dtype = d.get("type", "")

        if dtype == "horizontal_line" or (dtype == "rectangle" and abs(rect[3] - rect[1]) < 3):
            width = rect[2] - rect[0]
            if width >= min_h_width:
                h_cuts.append(Cut(
                    axis="h",
                    position=round((rect[1] + rect[3]) / 2, 1),
                    start=round(rect[0], 1),
                    end=round(rect[2], 1),
                    hard=True,
                ))

        elif dtype == "vertical_line" or (dtype == "rectangle" and abs(rect[2] - rect[0]) < 3):
            height = rect[3] - rect[1]
            if height >= min_v_height:
                v_cuts.append(Cut(
                    axis="v",
                    position=round((rect[0] + rect[2]) / 2, 1),
                    start=round(rect[1], 1),
                    end=round(rect[3], 1),
                    hard=True,
                ))

    return h_cuts, v_cuts


def _whitespace_gap_cuts(
    blocks: list[dict],
    columns: list[dict],
    threshold: float,
    page_width: float,
) -> list[Cut]:
    """Detect large vertical whitespace gaps between blocks as soft horizontal cuts."""
    if not blocks or not columns:
        return []

    cuts = []

    # Group blocks by column
    col_blocks: dict[int, list[dict]] = {}
    for b in blocks:
        col_id = b.get("column_id", 0)
        col_blocks.setdefault(col_id, []).append(b)

    for col_id, cblocks in col_blocks.items():
        # Sort by y-position
        sorted_blocks = sorted(cblocks, key=lambda b: b["bbox"][1])

        for i in range(len(sorted_blocks) - 1):
            curr_bottom = sorted_blocks[i]["bbox"][3]
            next_top = sorted_blocks[i + 1]["bbox"][1]
            gap = next_top - curr_bottom

            if gap > threshold:
                # Check if there's a font size change too (stronger signal)
                curr_size = sorted_blocks[i].get("font_size", 9)
                next_size = sorted_blocks[i + 1].get("font_size", 9)
                next_bold = sorted_blocks[i + 1].get("is_bold", False)

                # Stronger soft cut if there's also a typography change
                is_strong = (
                    next_bold and not sorted_blocks[i].get("is_bold", False)
                ) or (next_size > curr_size * 1.3)

                # Get the column's x range for the cut
                col_def = next((c for c in columns if c["column_id"] == col_id), None)
                if col_def:
                    cut_x0 = col_def["x_min"] - 5
                    cut_x1 = col_def["x_max"] + 5
                else:
                    cut_x0 = 0
                    cut_x1 = page_width

                gap_center = (curr_bottom + next_top) / 2
                cuts.append(Cut(
                    axis="h",
                    position=round(gap_center, 1),
                    start=round(cut_x0, 1),
                    end=round(cut_x1, 1),
                    hard=False,
                ))

    return cuts


def _prolong_horizontal_cuts(
    h_cuts: list[Cut],
    v_cuts: list[Cut],
    page_width: float,
) -> list[Cut]:
    """Prolong horizontal cuts to span between vertical cuts, forming a complete grid.

    A horizontal cut is extended leftward/rightward until it hits a vertical cut
    or the page boundary.
    """
    if not h_cuts:
        return h_cuts

    # Sort vertical cut positions
    v_positions = sorted(set(c.position for c in v_cuts)) if v_cuts else []

    prolonged = []
    for h in h_cuts:
        if not h.hard:
            # Don't prolong soft cuts — they're column-local
            prolonged.append(h)
            continue

        # Find the nearest vertical cuts to the left and right
        new_start = h.start
        new_end = h.end

        # Extend left: find leftmost v-cut that the h-cut could reach
        left_candidates = [vp for vp in v_positions if vp <= h.start + 5]
        if left_candidates:
            new_start = min(new_start, max(left_candidates))
        else:
            new_start = 0.0

        # Extend right: find rightmost v-cut that the h-cut could reach
        right_candidates = [vp for vp in v_positions if vp >= h.end - 5]
        if right_candidates:
            new_end = max(new_end, min(right_candidates))
        else:
            new_end = page_width

        prolonged.append(Cut(
            axis="h",
            position=h.position,
            start=round(new_start, 1),
            end=round(new_end, 1),
            hard=h.hard,
        ))

    return prolonged


def _cell_column_ids(x0: float, x1: float, columns: list[dict]) -> list[int]:
    """Determine which newspaper columns a cell spans."""
    col_ids = []
    for col in columns:
        # Cell overlaps this column if there's horizontal intersection
        col_left = col["x_min"] - 10
        col_right = col["x_max"] + 10
        if x0 < col_right and x1 > col_left:
            col_ids.append(col["column_id"])
    return sorted(col_ids) if col_ids else [0]


def _assign_blocks_to_cells(blocks: list[dict], cells: list[Cell]) -> None:
    """Assign each block to the cell that best contains it."""
    for bi, block in enumerate(blocks):
        bx0, by0, bx1, by1 = block["bbox"]
        bcx = (bx0 + bx1) / 2
        bcy = (by0 + by1) / 2

        best_cell = None
        best_overlap = -1

        for cell in cells:
            # Check if block center is inside cell
            if cell.x0 <= bcx <= cell.x1 and cell.y0 <= bcy <= cell.y1:
                # Compute overlap area
                ox0 = max(bx0, cell.x0)
                oy0 = max(by0, cell.y0)
                ox1 = min(bx1, cell.x1)
                oy1 = min(by1, cell.y1)
                overlap = max(0, ox1 - ox0) * max(0, oy1 - oy0)

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_cell = cell

        if best_cell is not None:
            best_cell.block_indices.append(bi)


def _classify_cells(cells: list[Cell], blocks: list[dict]) -> None:
    """Classify each cell by its dominant block role."""
    for cell in cells:
        if not cell.block_indices:
            cell.kind = "empty"
            continue

        roles = [blocks[bi].get("role", "unknown") for bi in cell.block_indices]

        # Priority: if any headline, it's a title cell
        if "headline" in roles:
            cell.kind = "title"
        elif "continuation_header" in roles:
            cell.kind = "continuation_header"
        elif "jump_ref" in roles:
            cell.kind = "jump_ref"
        elif "byline" in roles:
            cell.kind = "body"  # byline cells are part of article body
        elif "caption" in roles:
            cell.kind = "caption"
        elif "furniture" in roles and all(r in ("furniture", "unknown") for r in roles):
            cell.kind = "furniture"
        else:
            cell.kind = "body"


def build_cell_adjacency(cells: list[Cell]) -> dict[int, list[int]]:
    """Build adjacency graph between cells.

    Two cells are adjacent if they share a boundary edge (horizontal or vertical).
    """
    adjacency: dict[int, list[int]] = {c.cell_id: [] for c in cells}
    tolerance = 5.0  # points

    for i, a in enumerate(cells):
        for j, b in enumerate(cells):
            if i >= j:
                continue

            # Check vertical adjacency (a above b or b above a)
            v_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            if v_overlap > tolerance:
                if abs(a.y1 - b.y0) < tolerance:  # a directly above b
                    adjacency[a.cell_id].append(b.cell_id)
                    adjacency[b.cell_id].append(a.cell_id)
                elif abs(b.y1 - a.y0) < tolerance:  # b directly above a
                    adjacency[a.cell_id].append(b.cell_id)
                    adjacency[b.cell_id].append(a.cell_id)

            # Check horizontal adjacency (a left of b or b left of a)
            h_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            if h_overlap > tolerance:
                if abs(a.x1 - b.x0) < tolerance:  # a directly left of b
                    adjacency[a.cell_id].append(b.cell_id)
                    adjacency[b.cell_id].append(a.cell_id)
                elif abs(b.x1 - a.x0) < tolerance:  # b directly left of a
                    adjacency[a.cell_id].append(b.cell_id)
                    adjacency[b.cell_id].append(a.cell_id)

    return adjacency
