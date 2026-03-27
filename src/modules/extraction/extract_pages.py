"""Phase 1: Raw page-level PDF extraction using PyMuPDF.

Extracts text blocks and drawing/separator lines from each page of an
uploaded edition PDF.  Saves per-page JSON artifacts and updates the
edition's extraction_status.

Does NOT implement:
- column detection
- block classification (headline/body/etc.)
- article assembly or grouping
- jump stitching
- homepage generation
- Chroma indexing
"""

import json
import logging
import time
from pathlib import Path

import fitz  # PyMuPDF

from src.core.config import DATA_DIR
from src.modules.editions.database import get_edition, update_edition_status

logger = logging.getLogger(__name__)

# Base directory for extraction artifacts
ARTIFACTS_BASE = DATA_DIR / "extraction_artifacts"


def _get_artifacts_dir(publisher_id: int, edition_id: int) -> Path:
    """Return the tenant-aware artifacts directory for an edition.

    Structure: data/extraction_artifacts/publisher_{id}/edition_{id}/
    """
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def _extract_text_blocks(page: fitz.Page) -> list[dict]:
    """Extract text blocks from a single PDF page.

    Each block includes: text, bounding box, block index, and font
    metadata when available from spans.
    """
    blocks = []
    raw_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block_idx, block in enumerate(raw_dict.get("blocks", [])):
        if block.get("type") != 0:
            # type 0 = text, type 1 = image — skip images for Phase 1
            continue

        bbox = block.get("bbox", [0, 0, 0, 0])
        # Collect text and font info from spans
        full_text_parts = []
        font_sizes = []
        font_names = set()
        is_bold = False

        for line in block.get("lines", []):
            line_text_parts = []
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                line_text_parts.append(span_text)

                size = span.get("size", 0)
                if size > 0:
                    font_sizes.append(round(size, 1))

                font_name = span.get("font", "")
                if font_name:
                    font_names.add(font_name)
                    if "bold" in font_name.lower() or "heavy" in font_name.lower():
                        is_bold = True

                # Check flags for bold
                flags = span.get("flags", 0)
                if flags & (1 << 4):  # bit 4 = bold
                    is_bold = True

            full_text_parts.append("".join(line_text_parts))

        text = "\n".join(full_text_parts).strip()
        if not text:
            continue

        # Dominant font size (most common)
        dominant_size = 0
        if font_sizes:
            from collections import Counter
            dominant_size = Counter(font_sizes).most_common(1)[0][0]

        blocks.append({
            "block_index": block_idx,
            "bbox": [round(v, 2) for v in bbox],
            "text": text,
            "font_size": dominant_size,
            "font_names": sorted(font_names),
            "is_bold": is_bold,
            "line_count": len(block.get("lines", [])),
            "char_count": len(text),
        })

    return blocks


def _extract_drawings(page: fitz.Page) -> list[dict]:
    """Extract drawing/line elements from a PDF page.

    These are used later (Phase 2+) as separator line boundaries
    between articles. For now, we just capture them.
    """
    drawings = []

    for draw_idx, drawing in enumerate(page.get_drawings()):
        items = drawing.get("items", [])
        rect = drawing.get("rect")

        # Classify as horizontal line, vertical line, or other
        if rect:
            x0, y0, x1, y1 = rect
            width = abs(x1 - x0)
            height = abs(y1 - y0)

            if height < 3 and width > 20:
                draw_type = "horizontal_line"
            elif width < 3 and height > 20:
                draw_type = "vertical_line"
            elif width > 20 and height > 20:
                draw_type = "rectangle"
            else:
                draw_type = "other"
        else:
            draw_type = "other"
            x0 = y0 = x1 = y1 = 0

        drawings.append({
            "drawing_index": draw_idx,
            "type": draw_type,
            "rect": [round(v, 2) for v in (rect or [0, 0, 0, 0])],
            "item_count": len(items),
            "color": drawing.get("color"),
            "fill": drawing.get("fill"),
            "width": drawing.get("width"),
        })

    return drawings


def extract_edition(edition_id: int) -> dict:
    """Run Phase 1 extraction on an edition.

    Loads the edition record, opens its PDF, extracts text blocks
    and drawings from each page, saves per-page JSON artifacts,
    and updates the edition's extraction_status.

    Args:
        edition_id: ID of the edition to extract.

    Returns:
        Dict with extraction results:
        - success: bool
        - edition_id: int
        - page_count: int
        - total_blocks: int
        - total_drawings: int
        - artifacts_dir: str
        - error: str or None
        - pages: list of per-page summaries
    """
    start_time = time.time()
    result = {
        "success": False,
        "edition_id": edition_id,
        "page_count": 0,
        "total_blocks": 0,
        "total_drawings": 0,
        "artifacts_dir": None,
        "error": None,
        "pages": [],
    }

    # ── Load edition record ──
    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        logger.error(result["error"])
        return result

    pdf_path = edition.get("pdf_path")
    publisher_id = edition.get("publisher_id")

    if not pdf_path:
        result["error"] = f"Edition {edition_id} has no pdf_path"
        logger.error(result["error"])
        _update_extraction_status(edition_id, "failed", error=result["error"])
        return result

    if not Path(pdf_path).exists():
        result["error"] = f"PDF file not found: {pdf_path}"
        logger.error(result["error"])
        _update_extraction_status(edition_id, "failed", error=result["error"])
        return result

    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        logger.error(result["error"])
        _update_extraction_status(edition_id, "failed", error=result["error"])
        return result

    logger.info(
        f"Phase 1 extraction starting: edition={edition_id}, "
        f"publisher={publisher_id}, pdf={pdf_path}"
    )

    # ── Update status to processing ──
    _update_extraction_status(edition_id, "processing")

    try:
        # ── Open PDF ──
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        result["page_count"] = page_count

        logger.info(f"Edition {edition_id}: opened PDF, {page_count} pages")

        # ── Create artifacts directory ──
        artifacts_dir = _get_artifacts_dir(publisher_id, edition_id)
        result["artifacts_dir"] = str(artifacts_dir)

        total_blocks = 0
        total_drawings = 0

        # ── Process each page ──
        for page_num in range(page_count):
            page = doc[page_num]
            page_label = page_num + 1  # 1-indexed for display

            # Extract blocks and drawings
            blocks = _extract_text_blocks(page)
            drawings = _extract_drawings(page)

            page_blocks = len(blocks)
            page_drawings = len(drawings)
            total_blocks += page_blocks
            total_drawings += page_drawings

            # Build page artifact
            page_artifact = {
                "edition_id": edition_id,
                "publisher_id": publisher_id,
                "page_number": page_label,
                "page_width": round(page.rect.width, 2),
                "page_height": round(page.rect.height, 2),
                "block_count": page_blocks,
                "drawing_count": page_drawings,
                "blocks": blocks,
                "drawings": drawings,
            }

            # Save artifact
            artifact_path = artifacts_dir / f"page_{page_label:03d}.json"
            with open(artifact_path, "w", encoding="utf-8") as f:
                json.dump(page_artifact, f, indent=2, ensure_ascii=False)

            page_summary = {
                "page": page_label,
                "blocks": page_blocks,
                "drawings": page_drawings,
                "artifact": str(artifact_path),
            }
            result["pages"].append(page_summary)

            logger.info(
                f"  Page {page_label}/{page_count}: "
                f"{page_blocks} blocks, {page_drawings} drawings"
            )

        doc.close()

        result["total_blocks"] = total_blocks
        result["total_drawings"] = total_drawings
        result["success"] = True

        # ── Save edition-level summary ──
        summary = {
            "edition_id": edition_id,
            "publisher_id": publisher_id,
            "pdf_path": pdf_path,
            "page_count": page_count,
            "total_blocks": total_blocks,
            "total_drawings": total_drawings,
            "extraction_time_seconds": round(time.time() - start_time, 2),
            "pages": result["pages"],
        }
        summary_path = artifacts_dir / "extraction_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # ── Update edition status ──
        _update_extraction_status(edition_id, "extracted")
        update_edition_status(
            edition_id,
            status="extracted",
            page_count=page_count,
        )

        elapsed = round(time.time() - start_time, 2)
        logger.info(
            f"Phase 1 extraction complete: edition={edition_id}, "
            f"pages={page_count}, blocks={total_blocks}, "
            f"drawings={total_drawings}, time={elapsed}s"
        )

    except Exception as e:
        result["error"] = str(e)
        _update_extraction_status(edition_id, "failed", error=str(e))
        logger.error(
            f"Phase 1 extraction failed: edition={edition_id}, error={e}",
            exc_info=True,
        )

    return result


def get_page_artifact(publisher_id: int, edition_id: int, page_number: int) -> dict | None:
    """Load a single page's extraction artifact.

    Args:
        publisher_id: Publisher ID.
        edition_id: Edition ID.
        page_number: 1-indexed page number.

    Returns:
        Page artifact dict or None if not found.
    """
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    artifact_path = artifacts_dir / f"page_{page_number:03d}.json"

    if not artifact_path.exists():
        return None

    with open(artifact_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_extraction_summary(publisher_id: int, edition_id: int) -> dict | None:
    """Load the extraction summary for an edition.

    Args:
        publisher_id: Publisher ID.
        edition_id: Edition ID.

    Returns:
        Summary dict or None if not found.
    """
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    summary_path = artifacts_dir / "extraction_summary.json"

    if not summary_path.exists():
        return None

    with open(summary_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _update_extraction_status(
    edition_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """Update the extraction_status column on the edition row."""
    from src.core.database import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    if error:
        cursor.execute(
            "UPDATE editions SET extraction_status = ?, processing_notes = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, error, edition_id),
        )
    else:
        cursor.execute(
            "UPDATE editions SET extraction_status = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, edition_id),
        )
    conn.commit()
    conn.close()
    logger.info(f"Edition {edition_id} extraction_status → {status}")
