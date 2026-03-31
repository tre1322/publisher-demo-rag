#!/usr/bin/env python3
"""Batch process legacy PDF editions using Claude Vision extraction.

Usage:
    # Estimate cost (no API calls)
    uv run python scripts/batch_vision.py \
        --dir "/path/to/pdfs/" \
        --publisher "Cottonwood County Citizen" \
        --dry-run

    # Process first 5 editions
    uv run python scripts/batch_vision.py \
        --dir "/path/to/pdfs/" \
        --publisher "Cottonwood County Citizen" \
        --limit 5

    # Full batch with resume (skip already-processed)
    uv run python scripts/batch_vision.py \
        --dir "/path/to/pdfs/" \
        --publisher "Cottonwood County Citizen" \
        --resume
"""

import argparse
import hashlib
import logging
import re
import sys
import time
from pathlib import Path

import fitz

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import VISION_COST_PER_PAGE  # noqa: E402
from src.modules.editions.database import get_edition_by_checksum, insert_edition  # noqa: E402
from src.modules.extraction.pipeline_vision import run_vision_pipeline  # noqa: E402
from src.modules.extraction.shared_write_layer import write_articles_to_all  # noqa: E402
from src.modules.publishers.database import get_publisher_by_name  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _detect_edition_date(filename: str) -> str | None:
    """Try to extract edition date from filename like '03-25-26.pdf'."""
    m = re.match(r"(\d{2})-(\d{2})-(\d{2})", filename)
    if m:
        month, day, year = m.groups()
        full_year = f"20{year}" if int(year) < 50 else f"19{year}"
        return f"{full_year}-{month}-{day}"
    return None


def _file_checksum(path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Batch process legacy PDFs via Claude Vision")
    parser.add_argument("--dir", required=True, help="Directory containing PDF files")
    parser.add_argument("--publisher", required=True, help="Publisher name")
    parser.add_argument("--dry-run", action="store_true", help="Show cost estimate only")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N editions")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed editions")
    parser.add_argument("--edition-date", default=None, help="Override edition date (YYYY-MM-DD)")
    args = parser.parse_args()

    pdf_dir = Path(args.dir)
    if not pdf_dir.is_dir():
        print(f"Error: {pdf_dir} is not a directory")
        sys.exit(1)

    # Collect PDF files
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        sys.exit(1)

    if args.limit > 0:
        pdf_files = pdf_files[:args.limit]

    # Resolve publisher
    pub_record = get_publisher_by_name(args.publisher)
    if not pub_record:
        print(f"Error: Unknown publisher '{args.publisher}'")
        sys.exit(1)
    publisher_id = pub_record["id"]

    # Count pages for cost estimate
    total_pages = 0
    file_pages = []
    for pdf_path in pdf_files:
        try:
            doc = fitz.open(str(pdf_path))
            pages = len(doc)
            doc.close()
            file_pages.append((pdf_path, pages))
            total_pages += pages
        except Exception as e:
            logger.warning(f"Could not open {pdf_path.name}: {e}")
            file_pages.append((pdf_path, 0))

    est_cost = total_pages * VISION_COST_PER_PAGE
    est_time_min = total_pages * 1.5  # ~90s per page

    print(f"\n{'='*60}")
    print(f"Batch Vision Processing")
    print(f"{'='*60}")
    print(f"Directory:  {pdf_dir}")
    print(f"Publisher:  {args.publisher} (id={publisher_id})")
    print(f"PDF files:  {len(pdf_files)}")
    print(f"Total pages: {total_pages}")
    print(f"Est. cost:  ${est_cost:.2f}")
    print(f"Est. time:  {est_time_min:.0f} minutes ({est_time_min/60:.1f} hours)")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("DRY RUN — no processing. Remove --dry-run to execute.\n")
        for pdf_path, pages in file_pages:
            date = _detect_edition_date(pdf_path.name) or "?"
            print(f"  {pdf_path.name:30s}  {pages:3d} pages  date={date}")
        return

    # Process each edition
    processed = 0
    skipped = 0
    failed = 0
    total_articles = 0
    running_cost = 0.0
    start_time = time.time()

    for i, (pdf_path, page_count) in enumerate(file_pages):
        if page_count == 0:
            failed += 1
            continue

        edition_date = args.edition_date or _detect_edition_date(pdf_path.name)
        checksum = _file_checksum(pdf_path)

        # Resume: skip if already processed
        if args.resume:
            existing = get_edition_by_checksum(checksum, publication_id=None)
            if existing:
                logger.info(f"Skipping {pdf_path.name} (already processed, edition {existing['id']})")
                skipped += 1
                continue

        print(f"\n[{i+1}/{len(file_pages)}] Processing {pdf_path.name} ({page_count} pages, date={edition_date})...")

        try:
            # Create edition record
            edition_id = insert_edition(
                source_filename=pdf_path.name,
                publisher_id=publisher_id,
                edition_date=edition_date,
                checksum=checksum,
                page_count=page_count,
                pdf_path=str(pdf_path),
                upload_status="uploaded",
                extraction_status="processing",
            )

            # Run vision pipeline
            vision_result = run_vision_pipeline(
                pdf_path=str(pdf_path),
                edition_id=edition_id,
                publisher_id=publisher_id,
            )

            if not vision_result["success"]:
                logger.error(f"  Vision failed: {vision_result.get('error')}")
                failed += 1
                continue

            # Write to all destinations
            write_result = write_articles_to_all(
                articles=vision_result["articles"],
                edition_id=edition_id,
                publisher_id=publisher_id,
                publisher_name=args.publisher,
                edition_date=edition_date,
                source_filename=pdf_path.name,
            )

            article_count = write_result["articles_written"]
            stitched = sum(1 for a in vision_result["articles"] if a.get("is_stitched"))
            page_cost = vision_result.get("cost_usd", 0)
            running_cost += page_cost
            total_articles += article_count
            processed += 1

            elapsed = time.time() - start_time
            remaining = len(file_pages) - (i + 1)
            rate = elapsed / (i + 1) if i > 0 else 60
            eta_min = (remaining * rate) / 60

            print(
                f"  Done: {article_count} articles ({stitched} stitched), "
                f"cost=${page_cost:.2f}, running=${running_cost:.2f}, "
                f"ETA={eta_min:.0f}min"
            )

        except Exception as e:
            logger.error(f"  Failed: {e}", exc_info=True)
            failed += 1

    # Summary
    elapsed_total = (time.time() - start_time) / 60
    print(f"\n{'='*60}")
    print(f"Batch Complete")
    print(f"{'='*60}")
    print(f"Processed:  {processed}")
    print(f"Skipped:    {skipped}")
    print(f"Failed:     {failed}")
    print(f"Articles:   {total_articles}")
    print(f"Total cost: ${running_cost:.2f}")
    print(f"Total time: {elapsed_total:.1f} minutes")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
