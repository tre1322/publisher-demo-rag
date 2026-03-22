#!/usr/bin/env python
"""Initialize database tables for the Publisher RAG Demo."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import init_all_tables


def main() -> None:
    """Initialize all database tables."""
    print("Initializing database tables...")
    try:
        init_all_tables()
        print("✓ Database tables initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize database: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
