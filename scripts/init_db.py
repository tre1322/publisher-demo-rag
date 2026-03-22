#!/usr/bin/env python
"""Initialize database tables for the Publisher RAG Demo - standalone version."""

import sqlite3
import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "articles.db"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    """Initialize all database tables."""
    print(f"Initializing database tables at {DATABASE_PATH}...")
    
    conn = sqlite3.connect(str(DATABASE_PATH))
    cursor = conn.cursor()
    
    # Create advertisements table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS advertisements (
            ad_id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            advertiser TEXT NOT NULL,
            description TEXT,
            category TEXT,
            price REAL,
            original_price REAL,
            discount_percent REAL,
            valid_from TEXT,
            valid_to TEXT,
            url TEXT,
            raw_text TEXT,
            publisher TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✓ advertisements table initialized")
    
    # Create articles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            doc_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            publish_date TEXT,
            source_file TEXT NOT NULL,
            location TEXT,
            subjects TEXT,
            summary TEXT,
            url TEXT,
            publisher TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_author ON articles(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_location ON articles(location)")
    print("✓ articles table initialized")
    
    # Create events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            address TEXT,
            event_date TEXT,
            event_time TEXT,
            end_date TEXT,
            end_time TEXT,
            category TEXT,
            price REAL,
            url TEXT,
            raw_text TEXT,
            publisher TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_category ON events(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_date ON events(event_date)")
    print("✓ events table initialized")
    
    # Create conversations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT,
            publisher TEXT,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT,
            message_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
        )
    """)
    print("✓ conversations table initialized")
    
    # Create analytics table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            user_query TEXT,
            response_text TEXT,
            sources_used TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✓ analytics table initialized")
    
    conn.commit()
    conn.close()
    print("✓ All database tables initialized successfully")


if __name__ == "__main__":
    main()
