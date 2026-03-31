"""Universal CLI for processing newspaper edition PDFs via the V2 pipeline.

Handles the full flow: upload → V2 extraction (page grid + cell claiming +
bipartite jump matching) → write to content_items + legacy articles table →
index in ChromaDB → homepage batch.

Usage:
    # Process a single edition (auto-detects date from filename like 03-25-26.pdf)
    uv run python scripts/process_edition.py "/path/to/03-25-26.pdf" --publisher "Cottonwood County Citizen"

    # Process all PDFs in a directory
    uv run python scripts/process_edition.py --dir "/path/to/editions/" --publisher "Cottonwood County Citizen"

    # Override the auto-detected date
    uv run python scripts/process_edition.py "/path/to/edition.pdf" --publisher "Cottonwood County Citizen" --date 2026-03-25

    # List available publishers
    uv run python scripts/process_edition.py --list-publishers
"""

import argparse
import logging
import re
import sys
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import init_all_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_date_from_filename(filename: str) -> str | None:
    """Extract edition date from filename.

    Supports formats:
        MM-DD-YY.pdf     → 20YY-MM-DD  (e.g. 03-25-26.pdf → 2026-03-25)
        MM-DD-YYYY.pdf   → YYYY-MM-DD
        YYYY-MM-DD.pdf   → YYYY-MM-DD
        OA-YYYY-MM-DD.pdf → YYYY-MM-DD (prefixed filenames)
        PCS-YYYY-MM-DD.pdf → YYYY-MM-DD

    Returns:
        ISO date string (YYYY-MM-DD) or None if no date found.
    """
    stem = Path(filename).stem

    # Try MM-DD-YY (most common for the Citizen editions)
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2})$", stem)
    if m:
        month, day, year = m.groups()
        full_year = 2000 + int(year) if int(year) < 100 else int(year)
        return f"{full_year:04d}-{int(month):02d}-{int(day):02d}"

    # Try MM-DD-YYYY
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})$", stem)
    if m:
        month, day, year = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # Try YYYY-MM-DD (possibly prefixed)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if m:
        year, month, day = m.groups()
        return f"{year}-{month}-{day}"

    return None


def list_publishers() -> None:
    """Print all registered publishers."""
    init_all_tables()
    from src.modules.publishers.database import get_all_publishers_db

    publishers = get_all_publishers_db(active_only=False)
    if not publishers:
        print("No publishers found. The app will seed defaults on first run.")
        return

    print("\nRegistered publishers:")
    print("-" * 50)
    for p in publishers:
        active = "active" if p.get("active") else "inactive"
        print(f"  [{p['id']}] {p['name']} ({p.get('market', 'N/A')}) — {active}")
    print()


def process_single_edition(
    pdf_path: Path,
    publisher_name: str,
    edition_date: str | None = None,
) -> dict:
    """Process a single edition PDF through the full V2 pipeline.

    Args:
        pdf_path: Path to the PDF file.
        publisher_name: Publisher name (must exist in DB).
        edition_date: Override date (YYYY-MM-DD). Auto-detected from filename if None.

    Returns:
        Dict with processing results.
    """
    from sentence_transformers import SentenceTransformer

    from src.core.config import CHUNK_OVERLAP, CHUNK_SIZE, EMBEDDING_MODEL
    from src.core.vector_store import get_articles_collection
    from src.modules.articles import insert_edition_article
    from src.modules.editions.database import mark_edition_current
    from src.modules.extraction.pipeline_v2 import run_v2_pipeline
    from src.modules.extraction.publish import generate_homepage_batch, write_edition_to_db
    from src.modules.publishers.database import get_publisher_by_name
    from src.modules.publishers.uploads import upload_edition

    result = {
        "filename": pdf_path.name,
        "edition_id": None,
        "edition_date": None,
        "articles": 0,
        "stitched": 0,
        "chunks_indexed": 0,
        "error": None,
    }

    # Resolve publisher
    pub_record = get_publisher_by_name(publisher_name)
    if not pub_record:
        result["error"] = f"Unknown publisher: '{publisher_name}'. Use --list-publishers to see options."
        return result
    publisher_id = pub_record["id"]

    # Auto-detect edition date from filename if not provided
    if not edition_date:
        edition_date = parse_date_from_filename(pdf_path.name)
        if edition_date:
            logger.info(f"Auto-detected edition date: {edition_date} from {pdf_path.name}")
        else:
            logger.warning(f"Could not detect date from filename '{pdf_path.name}'. Use --date to specify.")

    result["edition_date"] = edition_date

    # Step 1: Tenant-aware upload
    logger.info(f"Step 1: Uploading {pdf_path.name} for publisher '{publisher_name}'...")
    file_data = pdf_path.read_bytes()
    upload_result = upload_edition(
        publisher_id=publisher_id,
        data=file_data,
        filename=pdf_path.name,
        edition_date=edition_date,
        issue_label=None,
    )

    if upload_result.get("error") and not upload_result.get("edition_id"):
        result["error"] = upload_result["error"]
        return result

    edition_id = upload_result["edition_id"]
    result["edition_id"] = edition_id

    if upload_result.get("duplicate") and upload_result.get("error"):
        logger.info(f"Edition already exists (id={edition_id}), re-processing...")

    # Step 2: V2 pipeline
    logger.info(f"Step 2: Running V2 pipeline for edition {edition_id}...")
    v2_result = run_v2_pipeline(edition_id)

    if not v2_result["success"]:
        result["error"] = f"V2 pipeline failed: {v2_result.get('error')}"
        return result

    v2_articles = v2_result["articles"]
    result["articles"] = len(v2_articles)
    result["stitched"] = v2_result["stitched_count"]

    # Step 3: Write to content_items table (Phase 6)
    logger.info("Step 3: Writing to content_items (Phase 6)...")
    db_result = write_edition_to_db(edition_id)
    if not db_result["success"]:
        logger.warning(f"Phase 6 DB write warning: {db_result.get('error')}")

    # Step 4: Insert into legacy articles table + index in ChromaDB
    logger.info("Step 4: Indexing in articles table + ChromaDB...")
    embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    collection = get_articles_collection()
    total_chunks = 0

    for art in v2_articles:
        headline = art.get("headline", "")
        body_text = art.get("body_text", "")
        byline = art.get("byline", "")

        if not body_text or len(body_text) < 20:
            continue

        doc_id = str(uuid.uuid4())

        insert_edition_article(
            doc_id=doc_id,
            title=headline,
            edition_id=edition_id,
            source_file=pdf_path.name,
            full_text=body_text,
            cleaned_text=body_text,
            author=byline or None,
            publish_date=edition_date or None,
            section=None,
            start_page=art.get("start_page"),
            continuation_pages=art.get("jump_pages") or None,
            publisher=publisher_name,
            organization_id=None,
            publication_id=None,
            needs_review=True,
        )

        # Chunk and index in ChromaDB
        words = body_text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + CHUNK_SIZE
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            if end >= len(words):
                break
            start = end - CHUNK_OVERLAP

        if chunks:
            embeddings = embedding_model.encode(chunks).tolist()
            ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "doc_id": doc_id,
                    "title": headline[:200],
                    "publish_date": edition_date or "",
                    "edition_date": edition_date or "",
                    "author": byline or "Unknown",
                    "source_file": pdf_path.name,
                    "chunk_index": i,
                    "location": "",
                    "subjects": "",
                    "edition_id": str(edition_id),
                    "content_type": "article",
                    "publisher": publisher_name,
                }
                for i in range(len(chunks))
            ]
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
            total_chunks += len(chunks)

    result["chunks_indexed"] = total_chunks

    # Step 5: Homepage batch (Phase 7)
    logger.info("Step 5: Generating homepage batch (Phase 7)...")
    generate_homepage_batch(edition_id)

    # Step 6: Mark as current edition for this publisher
    mark_edition_current(edition_id, publisher_id)

    logger.info(
        f"Edition {edition_id} fully processed: "
        f"{result['articles']} articles, "
        f"{result['stitched']} stitched, "
        f"{total_chunks} chunks indexed"
    )

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process newspaper edition PDFs via the V2 extraction pipeline.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="PDF file(s) to process",
    )
    parser.add_argument(
        "--dir",
        type=str,
        help="Directory containing PDF files to process",
    )
    parser.add_argument(
        "--publisher",
        type=str,
        default=None,
        help="Publisher name (e.g., 'Cottonwood County Citizen')",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Edition date (YYYY-MM-DD). Auto-detected from filename if omitted.",
    )
    parser.add_argument(
        "--list-publishers",
        action="store_true",
        help="List all registered publishers and exit",
    )

    args = parser.parse_args()

    # Initialize database
    init_all_tables()

    # Seed default publishers
    from src.modules.publishers.database import seed_publishers
    seed_publishers()

    if args.list_publishers:
        list_publishers()
        return

    if not args.publisher:
        print("ERROR: --publisher is required. Use --list-publishers to see options.")
        sys.exit(1)

    # Collect PDF paths
    pdf_paths: list[Path] = []

    if args.files:
        for f in args.files:
            p = Path(f)
            if p.is_file() and p.suffix.lower() == ".pdf":
                pdf_paths.append(p)
            elif p.is_file():
                logger.warning(f"Skipping non-PDF file: {p}")
            else:
                logger.warning(f"File not found: {p}")

    if args.dir:
        dir_path = Path(args.dir)
        if dir_path.is_dir():
            pdf_paths.extend(sorted(dir_path.glob("*.pdf")))
        else:
            logger.error(f"Directory not found: {args.dir}")
            sys.exit(1)

    if not pdf_paths:
        logger.error("No PDF files found. Provide file paths or use --dir.")
        parser.print_help()
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"EDITION PROCESSOR (V2 Pipeline)")
    print(f"{'=' * 60}")
    print(f"  Publisher: {args.publisher}")
    print(f"  PDFs to process: {len(pdf_paths)}")
    if args.date:
        print(f"  Date override: {args.date}")
    print()

    # Process each PDF
    results = []
    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"\n[{i}/{len(pdf_paths)}] Processing: {pdf_path.name}")
        print("-" * 40)

        result = process_single_edition(
            pdf_path=pdf_path,
            publisher_name=args.publisher,
            edition_date=args.date,
        )
        results.append(result)

        if result.get("error"):
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  Edition ID: {result['edition_id']}")
            print(f"  Date: {result['edition_date']}")
            print(f"  Articles: {result['articles']}")
            print(f"  Stitched: {result['stitched']}")
            print(f"  Chunks indexed: {result['chunks_indexed']}")

    # Summary
    print(f"\n{'=' * 60}")
    print("PROCESSING SUMMARY")
    print(f"{'=' * 60}")

    total_articles = sum(r.get("articles", 0) for r in results)
    total_stitched = sum(r.get("stitched", 0) for r in results)
    total_chunks = sum(r.get("chunks_indexed", 0) for r in results)
    failures = sum(1 for r in results if r.get("error"))

    print(f"  Files processed: {len(results) - failures}/{len(results)}")
    print(f"  Total articles: {total_articles}")
    print(f"  Total stitched: {total_stitched}")
    print(f"  Total chunks: {total_chunks}")
    if failures:
        print(f"  FAILURES: {failures}")
        for r in results:
            if r.get("error"):
                print(f"    - {r['filename']}: {r['error']}")
    print()


if __name__ == "__main__":
    main()
