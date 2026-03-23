"""Database operations for articles."""

import json
import logging

from src.core.database import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the articles table."""
    conn = get_connection()
    cursor = conn.cursor()

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
            edition_id INTEGER,
            section TEXT,
            start_page INTEGER,
            continuation_pages TEXT,
            full_text TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add columns if they don't exist (migration for existing DBs)
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
        try:
            cursor.execute(f"ALTER TABLE articles ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    # Create indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_author ON articles(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_location ON articles(location)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_edition ON articles(edition_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_org ON articles(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(publication_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_review ON articles(needs_review)"
    )

    conn.commit()
    conn.close()
    logger.info("Articles table initialized")


def insert_article(
    doc_id: str,
    title: str,
    author: str | None,
    publish_date: str | None,
    source_file: str,
    location: str | None = None,
    subjects: list[str] | None = None,
    summary: str | None = None,
    url: str | None = None,
    publisher: str | None = None,
) -> None:
    """Insert or update an article in the database.

    Args:
        doc_id: Unique document identifier.
        title: Article title.
        author: Author name.
        publish_date: Publication date (YYYY-MM-DD).
        source_file: Original filename.
        location: Extracted location (country, city).
        subjects: List of subjects/topics.
        summary: Brief summary.
        url: Article URL if available.
        publisher: Name of the publishing newspaper.
    """
    conn = get_connection()
    cursor = conn.cursor()

    subjects_json = json.dumps(subjects) if subjects else None

    cursor.execute(
        """
        INSERT OR REPLACE INTO articles
        (doc_id, title, author, publish_date, source_file, location, subjects, summary, url, publisher)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            title,
            author,
            publish_date,
            source_file,
            location,
            subjects_json,
            summary,
            url,
            publisher,
        ),
    )

    conn.commit()
    conn.close()


def insert_edition_article(
    doc_id: str,
    title: str,
    edition_id: int,
    source_file: str,
    full_text: str,
    cleaned_text: str | None = None,
    author: str | None = None,
    publish_date: str | None = None,
    section: str | None = None,
    start_page: int | None = None,
    continuation_pages: list[int] | None = None,
    subheadline: str | None = None,
    location: str | None = None,
    subjects: list[str] | None = None,
    summary: str | None = None,
    publisher: str | None = None,
    organization_id: int | None = None,
    publication_id: int | None = None,
    parse_metadata_json: dict | None = None,
    needs_review: bool = True,
) -> None:
    """Insert an article extracted from a newspaper edition."""
    conn = get_connection()
    cursor = conn.cursor()

    subjects_json = json.dumps(subjects) if subjects else None
    cont_pages_json = json.dumps(continuation_pages) if continuation_pages else None
    parse_meta = json.dumps(parse_metadata_json) if parse_metadata_json else None

    cursor.execute(
        """
        INSERT OR REPLACE INTO articles
        (doc_id, title, author, publish_date, source_file, location, subjects,
         summary, publisher, edition_id, section, start_page, continuation_pages,
         full_text, cleaned_text, subheadline, organization_id, publication_id,
         parse_metadata_json, needs_review, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsed', CURRENT_TIMESTAMP)
        """,
        (
            doc_id, title, author, publish_date, source_file, location,
            subjects_json, summary, publisher, edition_id, section, start_page,
            cont_pages_json, full_text, cleaned_text or full_text, subheadline,
            organization_id, publication_id, parse_meta,
            1 if needs_review else 0,
        ),
    )

    conn.commit()
    conn.close()


def update_article(
    doc_id: str,
    title: str | None = None,
    author: str | None = None,
    cleaned_text: str | None = None,
    subheadline: str | None = None,
    status: str | None = None,
    needs_review: bool | None = None,
) -> None:
    """Update editable fields on an article."""
    conn = get_connection()
    cursor = conn.cursor()

    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params: list = []

    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if author is not None:
        updates.append("author = ?")
        params.append(author)
    if cleaned_text is not None:
        updates.append("cleaned_text = ?")
        params.append(cleaned_text)
    if subheadline is not None:
        updates.append("subheadline = ?")
        params.append(subheadline)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if needs_review is not None:
        updates.append("needs_review = ?")
        params.append(1 if needs_review else 0)

    params.append(doc_id)
    cursor.execute(
        f"UPDATE articles SET {', '.join(updates)} WHERE doc_id = ?",
        params,
    )
    conn.commit()
    conn.close()


def get_articles_for_edition(edition_id: int) -> list[dict]:
    """Get all articles belonging to an edition."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM articles WHERE edition_id = ? ORDER BY start_page, title",
        (edition_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_articles_needing_review(limit: int = 50) -> list[dict]:
    """Get articles that need manual review."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM articles WHERE needs_review = 1 ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def search_by_metadata(
    date_from: str | None = None,
    date_to: str | None = None,
    author: str | None = None,
    location: str | None = None,
    subject: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search articles by metadata filters.

    Args:
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        author: Author name (partial match).
        location: Location (partial match).
        subject: Subject/topic (partial match in JSON).
        limit: Maximum results.

    Returns:
        List of matching articles.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM articles WHERE 1=1"
    params: list = []

    if date_from:
        query += " AND publish_date >= ?"
        params.append(date_from)

    if date_to:
        query += " AND publish_date <= ?"
        params.append(date_to)

    if author:
        query += " AND author LIKE ?"
        params.append(f"%{author}%")

    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")

    if subject:
        query += " AND subjects LIKE ?"
        params.append(f"%{subject}%")

    query += " ORDER BY publish_date DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        article = dict(row)
        # Parse subjects JSON
        if article.get("subjects"):
            article["subjects"] = json.loads(article["subjects"])
        results.append(article)

    return results


def get_recent_articles(limit: int = 5) -> list[dict]:
    """Get most recent articles ordered by publish date.

    Args:
        limit: Maximum number of articles to return.

    Returns:
        List of article dictionaries.
    """
    return search_by_metadata(limit=limit)


def get_article_by_id(doc_id: str) -> dict | None:
    """Get an article by its document ID.

    Args:
        doc_id: Document identifier.

    Returns:
        Article data or None.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM articles WHERE doc_id = ?", (doc_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        article = dict(row)
        if article.get("subjects"):
            article["subjects"] = json.loads(article["subjects"])
        return article
    return None


def get_all_subjects() -> list[str]:
    """Get all unique subjects in the database.

    Returns:
        List of unique subjects.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT subjects FROM articles WHERE subjects IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    all_subjects = set()
    for row in rows:
        subjects = json.loads(row["subjects"])
        all_subjects.update(subjects)

    return sorted(all_subjects)


def get_all_locations() -> list[str]:
    """Get all unique locations in the database.

    Returns:
        List of unique locations.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT location FROM articles WHERE location IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    return sorted([row["location"] for row in rows])


def get_date_range() -> tuple[str | None, str | None]:
    """Get the date range of articles in the database.

    Returns:
        Tuple of (min_date, max_date).
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT MIN(publish_date), MAX(publish_date) FROM articles")
    row = cursor.fetchone()
    conn.close()

    return (row[0], row[1]) if row else (None, None)


def get_article_count() -> int:
    """Get total number of articles in the database.

    Returns:
        Article count.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM articles")
    count = cursor.fetchone()[0]
    conn.close()

    return count


def clear_articles() -> None:
    """Clear all articles from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM articles")
    conn.commit()
    conn.close()
    logger.info("Articles cleared")
