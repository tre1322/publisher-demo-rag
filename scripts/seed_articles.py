"""Seed articles from the baked quadd_articles.db into the main articles.db.

This runs at startup to ensure articles are in the main database.
It reads from data/quadd_articles.db (baked into Docker image) and
inserts into data/articles.db (the main app database).

No embedding model needed — ChromaDB is also baked into the image.
"""
import sqlite3
import json
import uuid
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
QUADD_DB = PROJECT_ROOT / "data" / "quadd_articles.db"
MAIN_DB = PROJECT_ROOT / "data" / "articles.db"


def seed():
    if not QUADD_DB.exists():
        print(f"[SEED] No quadd DB at {QUADD_DB}, skipping")
        return 0

    # Read articles from quadd content_items
    qconn = sqlite3.connect(str(QUADD_DB))
    qconn.row_factory = sqlite3.Row
    rows = qconn.execute("""
        SELECT id, edition_id, publisher_id, headline, byline, cleaned_web_text as body_text,
               start_page, jump_pages_json, section, content_type
        FROM content_items
        WHERE cleaned_web_text IS NOT NULL
          AND length(cleaned_web_text) >= 100
          AND headline IS NOT NULL
          AND headline != '?'
          AND edition_id IN (31, 1312)
        ORDER BY edition_id, start_page, id
    """).fetchall()
    qconn.close()

    if not rows:
        print("[SEED] No articles found in quadd DB")
        return 0

    # Insert into main articles.db
    conn = sqlite3.connect(str(MAIN_DB))
    cur = conn.cursor()

    count = 0
    for r in rows:
        r = dict(r)
        headline = (r.get("headline") or "").strip()
        body = (r.get("body_text") or "").strip()
        if not headline or not body or len(body) < 50:
            continue

        edition_id = r.get("edition_id", 0)
        doc_id = f"quadd_{edition_id}_{uuid.uuid5(uuid.NAMESPACE_DNS, f'{edition_id}_{headline}')}"

        byline = r.get("byline")
        section = r.get("section")
        start_page = r.get("start_page")
        jump_pages = r.get("jump_pages_json")

        # Determine publisher and location from publisher_id
        publisher_id = r.get("publisher_id")
        if publisher_id == 2:
            publisher = "Pipestone Star"
            location = "Pipestone, MN"
            publish_date = "2026-01-08"
        else:
            publisher = "Observer/Advocate"
            publish_date = "2026-01-28"
            location = "Cottonwood County, MN"
            hl = headline.lower()
            if "butterfield" in hl:
                location = "Butterfield, MN"
            elif "bingham lake" in hl or "sokolofsky" in hl:
                location = "Bingham Lake, MN"
            elif "mt. lake" in hl or "mt lake" in hl:
                location = "Mountain Lake, MN"

        cur.execute("""
            INSERT OR REPLACE INTO articles
            (doc_id, title, author, publish_date, source_file, location,
             publisher, edition_id, section, start_page, continuation_pages,
             full_text, cleaned_text, needs_review, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'parsed', CURRENT_TIMESTAMP)
        """, (
            doc_id, headline, byline, publish_date, "quadd_extraction",
            location, publisher, edition_id, section, start_page,
            jump_pages, body, body,
        ))
        count += 1

    conn.commit()
    conn.close()
    print(f"[SEED] Inserted {count} articles from quadd DB")
    return count


if __name__ == "__main__":
    seed()
