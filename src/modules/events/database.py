"""Database operations for events."""

import logging

from src.core.database import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the events table."""
    conn = get_connection()
    cursor = conn.cursor()

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

    # Add columns if they don't exist (migration for existing DBs)
    for col, coltype in [
        ("end_date", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE events ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    # Create indexes for event queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_category ON events(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_date ON events(event_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_location ON events(location)")

    conn.commit()
    conn.close()
    logger.info("Events table initialized")


def insert_event(
    event_id: str,
    title: str,
    description: str | None = None,
    location: str | None = None,
    address: str | None = None,
    event_date: str | None = None,
    event_time: str | None = None,
    end_time: str | None = None,
    category: str | None = None,
    price: float | None = None,
    url: str | None = None,
    raw_text: str | None = None,
    publisher: str | None = None,
) -> None:
    """Insert or update an event in the database.

    Args:
        event_id: Unique event identifier.
        title: Event title.
        description: Event description.
        location: Venue/place name.
        address: Full address.
        event_date: Date (YYYY-MM-DD).
        event_time: Start time (HH:MM).
        end_time: End time (HH:MM).
        category: Event category.
        price: Ticket price (None for free).
        url: Event URL.
        raw_text: Original source text.
        publisher: Name of the publishing newspaper.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO events
        (event_id, title, description, location, address, event_date,
         event_time, end_time, category, price, url, raw_text, publisher)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            title,
            description,
            location,
            address,
            event_date,
            event_time,
            end_time,
            category,
            price,
            url,
            raw_text,
            publisher,
        ),
    )

    conn.commit()
    conn.close()


def search_events(
    category: str | None = None,
    location: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_price: float | None = None,
    free_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Search events by filters.

    Args:
        category: Event category to filter by.
        location: Location/venue to filter by (partial match).
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        max_price: Maximum price filter.
        free_only: Only return free events.
        limit: Maximum results.

    Returns:
        List of matching events.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM events WHERE 1=1"
    params: list = []

    if category:
        query += " AND category LIKE ?"
        params.append(f"%{category}%")

    if location:
        query += " AND (location LIKE ? OR address LIKE ?)"
        params.append(f"%{location}%")
        params.append(f"%{location}%")

    if date_from:
        query += " AND event_date >= ?"
        params.append(date_from)

    if date_to:
        query += " AND event_date <= ?"
        params.append(date_to)

    if max_price is not None:
        query += " AND (price <= ? OR price IS NULL)"
        params.append(max_price)

    if free_only:
        query += " AND (price IS NULL OR price = 0)"

    query += " ORDER BY event_date ASC, event_time ASC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_event_by_id(event_id: str) -> dict | None:
    """Get an event by its ID.

    Args:
        event_id: Event identifier.

    Returns:
        Event data or None.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_all_event_categories() -> list[str]:
    """Get all unique event categories.

    Returns:
        List of unique categories.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT category FROM events WHERE category IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    return sorted([row["category"] for row in rows])


def get_event_count() -> int:
    """Get total number of events in the database.

    Returns:
        Event count.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM events")
    count = cursor.fetchone()[0]
    conn.close()

    return count


def clear_events() -> None:
    """Clear all events from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events")
    conn.commit()
    conn.close()
    logger.info("Events cleared")
