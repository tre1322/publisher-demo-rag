#!/usr/bin/env python
"""CLI script to reset the ChromaDB database."""

import argparse
import shutil
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import CHROMA_PERSIST_DIR, DATA_DIR, INGESTED_FILES_PATH

SQLITE_DB_PATH = DATA_DIR / "articles.db"


def main() -> None:
    """Reset the ChromaDB database."""
    parser = argparse.ArgumentParser(
        description="Reset the ChromaDB database and ingestion tracking"
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    print("Publisher RAG Demo - Database Reset")
    print("=" * 40)

    if not args.force:
        print("\nThis will delete:")
        print(f"  - ChromaDB database: {CHROMA_PERSIST_DIR}")
        print(f"  - SQLite metadata: {SQLITE_DB_PATH}")
        print(f"  - Ingestion tracking: {INGESTED_FILES_PATH}")
        print("\nThis action cannot be undone.")

        confirm = input("\nAre you sure you want to continue? (yes/no): ")
        if confirm.lower() not in ["yes", "y"]:
            print("Aborted.")
            sys.exit(0)

    # Remove ChromaDB directory
    if CHROMA_PERSIST_DIR.exists():
        shutil.rmtree(CHROMA_PERSIST_DIR)
        print(f"Removed: {CHROMA_PERSIST_DIR}")
    else:
        print(f"Not found: {CHROMA_PERSIST_DIR}")

    # Remove SQLite database
    if SQLITE_DB_PATH.exists():
        SQLITE_DB_PATH.unlink()
        print(f"Removed: {SQLITE_DB_PATH}")
    else:
        print(f"Not found: {SQLITE_DB_PATH}")

    # Remove ingested files tracking
    if INGESTED_FILES_PATH.exists():
        INGESTED_FILES_PATH.unlink()
        print(f"Removed: {INGESTED_FILES_PATH}")
    else:
        print(f"Not found: {INGESTED_FILES_PATH}")

    print("\nDatabase reset complete!")
    print("Run ingestion again to rebuild the index.")


if __name__ == "__main__":
    main()
