#!/usr/bin/env python
"""Initialize and migrate database tables for the Publisher RAG Demo.

This is the single authoritative schema definition for production deployments.
It creates tables if missing and adds new columns to existing tables via
ALTER TABLE (safe for SQLite — no data loss).
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "articles.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "editions").mkdir(exist_ok=True)


def _add_column_if_missing(cursor: sqlite3.Cursor, table: str, column: str, coltype: str) -> None:
    """Add a column to a table if it doesn't already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"  + Added {table}.{column}")


def main() -> None:
    print(f"Initializing database at {DATABASE_PATH}...")

    conn = sqlite3.connect(str(DATABASE_PATH))
    cur = conn.cursor()

    # ── Organizations ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("OK: organizations")

    # ── Publications ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            market TEXT,
            state TEXT,
            timezone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_publications_org ON publications(organization_id)")
    print("OK: publications")

    # ── Articles ──
    cur.execute("""
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
    # Migrate: add all new columns
    for col, coltype in [
        ("edition_id", "INTEGER"),
        ("organization_id", "INTEGER"),
        ("publication_id", "INTEGER"),
        ("section", "TEXT"),
        ("start_page", "INTEGER"),
        ("continuation_pages", "TEXT"),
        ("full_text", "TEXT"),
        ("cleaned_text", "TEXT"),
        ("subheadline", "TEXT"),
        ("status", "TEXT DEFAULT 'parsed'"),
        ("duplicate_flag", "INTEGER DEFAULT 0"),
        ("needs_review", "INTEGER DEFAULT 1"),
        ("parse_metadata_json", "TEXT"),
        ("updated_at", "TEXT"),
    ]:
        _add_column_if_missing(cur, "articles", col, coltype)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_author ON articles(author)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_location ON articles(location)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_edition ON articles(edition_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_org ON articles(organization_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(publication_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_review ON articles(needs_review)")
    print("OK: articles")

    # ── Advertisements ──
    cur.execute("""
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
    for col, coltype in [
        ("edition_id", "INTEGER"),
        ("organization_id", "INTEGER"),
        ("publication_id", "INTEGER"),
        ("page", "INTEGER"),
        ("headline", "TEXT"),
        ("cleaned_text", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("checksum", "TEXT"),
        ("parse_metadata_json", "TEXT"),
        ("ocr_text", "TEXT"),
        ("embedding_text", "TEXT"),
        ("ad_category", "TEXT"),
        ("location", "TEXT"),
    ]:
        _add_column_if_missing(cur, "advertisements", col, coltype)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_category ON advertisements(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ad_valid_to ON advertisements(valid_to)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_edition ON advertisements(edition_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_org ON advertisements(organization_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_checksum ON advertisements(checksum)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ads_ad_category ON advertisements(ad_category)")
    print("OK: advertisements")

    # ── Events ──
    cur.execute("""
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_category ON events(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_event_date ON events(event_date)")
    print("OK: events")

    # ── Editions ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS editions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publication_id INTEGER,
            edition_date TEXT,
            issue_label TEXT,
            source_filename TEXT NOT NULL DEFAULT '',
            checksum TEXT,
            page_count INTEGER,
            article_count INTEGER DEFAULT 0,
            ad_count INTEGER DEFAULT 0,
            processing_status TEXT DEFAULT 'pending',
            processing_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col, coltype in [
        ("publication_id", "INTEGER"),
        ("issue_label", "TEXT"),
        ("checksum", "TEXT"),
        ("processing_notes", "TEXT"),
        ("source_filename", "TEXT DEFAULT ''"),
        ("publisher_id", "INTEGER"),
        ("pdf_path", "TEXT"),
        ("upload_status", "TEXT DEFAULT 'pending'"),
        ("extraction_status", "TEXT DEFAULT 'not_started'"),
        ("homepage_batch_status", "TEXT DEFAULT 'not_started'"),
        ("is_current", "INTEGER DEFAULT 0"),
    ]:
        _add_column_if_missing(cur, "editions", col, coltype)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_editions_pub ON editions(publication_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_editions_publisher ON editions(publisher_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_editions_date ON editions(edition_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_editions_status ON editions(processing_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_editions_checksum ON editions(checksum)")
    print("OK: editions")

    # ── Conversations ──
    # Fix old schema: if conversations has conversation_id TEXT PK, recreate
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'")
    schema_row = cur.fetchone()
    if schema_row and schema_row[0] and "conversation_id TEXT PRIMARY KEY" in schema_row[0]:
        cur.execute("DROP TABLE IF EXISTS conversation_messages")
        cur.execute("DROP TABLE IF EXISTS conversations")
        print("  Migrated conversations from old schema")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT,
            message_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conversations_started ON conversations(started_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON conversation_messages(conversation_id)")
    print("OK: conversations")

    # ── Analytics ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            user_query TEXT,
            response_text TEXT,
            sources_used TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            content_type TEXT,
            content_id TEXT,
            shown_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS url_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            content_type TEXT,
            content_id TEXT,
            url TEXT,
            clicked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            user_agent TEXT
        )
    """)
    print("OK: analytics")

    # ── Page Regions ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS page_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edition_id INTEGER NOT NULL,
            article_id TEXT,
            page_number INTEGER NOT NULL,
            region_type TEXT NOT NULL,
            bbox_json TEXT,
            raw_text TEXT,
            role TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (edition_id) REFERENCES editions(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_page_regions_edition ON page_regions(edition_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_page_regions_article ON page_regions(article_id)")
    print("OK: page_regions")

    # ── Review Actions ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS review_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            user_identifier TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_review_actions_article ON review_actions(article_id)")
    print("OK: review_actions")

    # ── Publishers ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS publishers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            market TEXT,
            state TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_publishers_slug ON publishers(slug)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_publishers_active ON publishers(active)")
    print("OK: publishers")

    # ── Content Items (skeleton) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edition_id INTEGER,
            publisher_id INTEGER,
            content_type TEXT NOT NULL DEFAULT 'article',
            title TEXT,
            raw_text TEXT,
            cleaned_text TEXT,
            page_number INTEGER,
            status TEXT DEFAULT 'pending',
            extraction_method TEXT,
            source_region_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (edition_id) REFERENCES editions(id),
            FOREIGN KEY (publisher_id) REFERENCES publishers(id)
        )
    """)
    # Migrations for content_items columns used by shared_write_layer / publish
    for col, coltype in [
        ("headline", "TEXT"),
        ("subheadline", "TEXT"),
        ("byline", "TEXT"),
        ("cleaned_web_text", "TEXT"),
        ("section", "TEXT"),
        ("start_page", "INTEGER"),
        ("end_page", "INTEGER"),
        ("jump_pages_json", "TEXT"),
        ("print_prominence_score", "REAL DEFAULT 0"),
        ("extraction_confidence", "REAL DEFAULT 0"),
        ("homepage_eligible", "INTEGER DEFAULT 0"),
        ("homepage_score", "REAL DEFAULT 0"),
        ("publish_status", "TEXT DEFAULT 'draft'"),
        ("is_stitched", "INTEGER DEFAULT 0"),
        ("block_count", "INTEGER DEFAULT 0"),
        ("column_id", "INTEGER"),
        ("span_columns", "INTEGER DEFAULT 1"),
        ("bbox_json", "TEXT"),
        ("edition_date", "TEXT"),
    ]:
        _add_column_if_missing(cur, "content_items", col, coltype)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_items_edition ON content_items(edition_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_items_publisher ON content_items(publisher_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_items_type ON content_items(content_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_content_items_status ON content_items(status)")
    print("OK: content_items")

    conn.commit()
    conn.close()
    print("OK: All tables initialized and migrated")


if __name__ == "__main__":
    main()
