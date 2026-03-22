#!/usr/bin/env python
"""Initialize database tables for the Publisher RAG Demo."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import and run each table init directly
from src.core.database import DATABASE_PATH, get_connection
from src.modules.advertisements.database import init_table as ads_init
from src.modules.analytics.database import init_table as analytics_init
from src.modules.articles.database import init_table as articles_init
from src.modules.conversations.database import init_table as conv_init
from src.modules.events.database import init_table as events_init


def main() -> None:
    """Initialize all database tables."""
    print(f"Initializing database tables at {DATABASE_PATH}...")
    try:
        ads_init()
        print("✓ advertisements table initialized")
        articles_init()
        print("✓ articles table initialized")
        events_init()
        print("✓ events table initialized")
        conv_init()
        print("✓ conversations table initialized")
        analytics_init()
        print("✓ analytics table initialized")
        print("✓ All database tables initialized successfully")
    except Exception as e:
        import traceback
        print(f"✗ Failed to initialize database: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
