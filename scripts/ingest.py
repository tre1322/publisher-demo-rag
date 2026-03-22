#!/usr/bin/env python
"""CLI script for document ingestion."""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import DOCUMENTS_DIR
from src.ingestion import DocumentIngester


def main() -> None:
    """Run document ingestion from command line."""
    parser = argparse.ArgumentParser(description="Ingest documents into the RAG system")
    parser.add_argument(
        "--directory",
        "-d",
        type=Path,
        default=DOCUMENTS_DIR,
        help=f"Directory containing documents (default: {DOCUMENTS_DIR})",
    )
    parser.add_argument(
        "--stats",
        "-s",
        action="store_true",
        help="Show collection statistics only",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Skip rich metadata extraction (no Claude API calls)",
    )
    parser.add_argument(
        "--publisher",
        "-p",
        type=str,
        help="Name of the publishing newspaper",
    )

    args = parser.parse_args()

    print("Publisher RAG Demo - Document Ingestion")
    print("=" * 40)

    ingester = DocumentIngester(
        extract_metadata=not args.no_metadata,
        publisher=args.publisher,
    )

    if args.stats:
        stats = ingester.get_collection_stats()
        print(f"Total chunks in collection: {stats['total_chunks']}")
        print(f"Files tracked as ingested: {stats['ingested_files']}")
        return

    if not args.directory.exists():
        print(f"Error: Directory not found: {args.directory}")
        print("Please create the directory and add documents to it.")
        sys.exit(1)

    # Check for documents
    files = list(args.directory.glob("*.pdf")) + list(args.directory.glob("*.txt"))
    if not files:
        print(f"No documents found in: {args.directory}")
        print("Please add .pdf or .txt files to the directory.")
        sys.exit(0)

    print(f"Found {len(files)} documents in {args.directory}")
    print("Starting ingestion...\n")

    results = ingester.ingest_all(args.directory)

    print("\n" + "=" * 40)
    print("Ingestion Complete!")
    print("=" * 40)
    print(f"Total files found:    {results['total_files']}")
    print(f"Files ingested:       {results['ingested_files']}")
    print(f"Files skipped:        {results['skipped_files']}")
    print(f"Files failed:         {results['failed_files']}")
    print(f"Total chunks created: {results['total_chunks']}")

    if results["errors"]:
        print("\nErrors encountered:")
        for error in results["errors"]:
            print(f"  - {error}")

    # Show final stats
    stats = ingester.get_collection_stats()
    print(f"\nCollection now contains {stats['total_chunks']} total chunks")


if __name__ == "__main__":
    main()
