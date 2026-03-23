"""CLI for ingesting newspaper edition PDFs.

Usage:
    # Ingest a single PDF
    uv run python scripts/ingest_edition.py path/to/newspaper.pdf --publisher "Pipestone Star"

    # Ingest multiple PDFs
    uv run python scripts/ingest_edition.py editions/*.pdf --publisher "Pipestone Star"

    # With edition date
    uv run python scripts/ingest_edition.py paper.pdf --publisher "Pipestone Star" --date 2024-03-15

    # Ingest all PDFs in a directory
    uv run python scripts/ingest_edition.py --dir data/editions/ --publisher "Pipestone Star"
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import init_all_tables
from src.edition_ingestion import EditionIngester

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest newspaper edition PDFs into the system."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="PDF file(s) to ingest",
    )
    parser.add_argument(
        "--dir",
        type=str,
        help="Directory containing PDF files to ingest",
    )
    parser.add_argument(
        "--publisher",
        type=str,
        default=None,
        help="Publisher name (e.g., 'Pipestone Star')",
    )
    parser.add_argument(
        "--org",
        type=str,
        default=None,
        help="Organization name (auto-creates if needed)",
    )
    parser.add_argument(
        "--publication",
        type=str,
        default=None,
        help="Publication name (defaults to publisher name)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Edition date (YYYY-MM-DD)",
    )

    args = parser.parse_args()

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

    print(f"\nFound {len(pdf_paths)} PDF(s) to process")
    print(f"Publisher: {args.publisher}")
    if args.date:
        print(f"Edition date: {args.date}")
    print()

    # Initialize database tables
    init_all_tables()

    # Auto-create org/publication if names provided
    organization_id = None
    publication_id = None

    org_name = args.org or args.publisher
    pub_name = args.publication or args.publisher

    if org_name:
        from src.modules.organizations import insert_organization, insert_publication
        organization_id = insert_organization(org_name)
        if pub_name:
            publication_id = insert_publication(
                organization_id=organization_id,
                name=pub_name,
                market=None,
            )

    # Run ingestion
    ingester = EditionIngester(
        publisher=args.publisher,
        publication_name=pub_name,
        organization_id=organization_id,
        publication_id=publication_id,
    )

    results = ingester.ingest_bulk(pdf_paths, edition_date=args.date)

    # Print summary
    print("\n" + "=" * 60)
    print("INGESTION SUMMARY")
    print("=" * 60)

    total_articles = 0
    total_ads = 0
    total_chunks = 0
    failures = 0

    for r in results:
        status = "OK" if not r.get("error") else f"FAILED: {r['error']}"
        print(f"\n  {r['pdf']}: {status}")
        if not r.get("error"):
            print(f"    Articles: {r['articles']}, Ads: {r['ads']}, Chunks: {r['chunks_indexed']}")
            total_articles += r["articles"]
            total_ads += r["ads"]
            total_chunks += r["chunks_indexed"]
        else:
            failures += 1

        warnings = r.get("warnings", [])
        for w in warnings:
            print(f"    WARNING: {w}")

    print(f"\n  TOTAL: {total_articles} articles, {total_ads} ads, {total_chunks} chunks")
    if failures:
        print(f"  FAILURES: {failures}")
    print()


if __name__ == "__main__":
    main()
