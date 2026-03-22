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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create indexes for common queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publish_date ON articles(publish_date)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_author ON articles(author)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_location ON articles(location)")

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
