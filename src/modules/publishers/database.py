"""Database operations for publishers (newspaper tenants)."""

import logging
import re

from src.core.database import get_connection

logger = logging.getLogger(__name__)

# Default publishers to seed on first run
_DEFAULT_PUBLISHERS = [
    {"name": "Cottonwood County Citizen", "market": "Windom, MN", "state": "MN"},
    {"name": "Pipestone Star", "market": "Pipestone, MN", "state": "MN"},
]


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def init_table() -> None:
    """Initialize the publishers table."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
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

    # Migrations for future columns
    for col, coltype in [
        ("active", "INTEGER DEFAULT 1"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE publishers ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publishers_slug ON publishers(slug)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publishers_active ON publishers(active)"
    )

    conn.commit()
    conn.close()
    logger.info("Publishers table initialized")


def insert_publisher(
    name: str,
    slug: str | None = None,
    market: str | None = None,
    state: str | None = None,
) -> int:
    """Insert a publisher, returning its ID. Skips if slug already exists.

    Args:
        name: Publisher name (e.g., "Cottonwood County Citizen").
        slug: URL-safe slug (auto-generated if not provided).
        market: Market area (e.g., "Windom, MN").
        state: State code (e.g., "MN").

    Returns:
        Publisher ID (existing or newly created).
    """
    if not slug:
        slug = _slugify(name)

    conn = get_connection()
    cursor = conn.cursor()

    # Check if exists
    cursor.execute("SELECT id FROM publishers WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row["id"]

    cursor.execute(
        """
        INSERT INTO publishers (name, slug, market, state)
        VALUES (?, ?, ?, ?)
        """,
        (name, slug, market, state),
    )
    pub_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Publisher created: '{name}' (id={pub_id}, slug={slug})")
    return pub_id


def seed_publishers() -> None:
    """Seed default publishers if they don't exist. Idempotent."""
    for pub in _DEFAULT_PUBLISHERS:
        insert_publisher(
            name=pub["name"],
            market=pub.get("market"),
            state=pub.get("state"),
        )
    logger.info(f"Publisher seeding complete ({len(_DEFAULT_PUBLISHERS)} checked)")


def get_publisher(pub_id: int) -> dict | None:
    """Get a publisher by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publishers WHERE id = ?", (pub_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_publisher_by_name(name: str) -> dict | None:
    """Get a publisher by name (case-insensitive)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM publishers WHERE LOWER(name) = LOWER(?)", (name,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_publisher_by_slug(slug: str) -> dict | None:
    """Get a publisher by slug."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publishers WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_publishers_db(active_only: bool = True) -> list[dict]:
    """Get all publishers."""
    conn = get_connection()
    cursor = conn.cursor()
    if active_only:
        cursor.execute(
            "SELECT * FROM publishers WHERE active = 1 ORDER BY name"
        )
    else:
        cursor.execute("SELECT * FROM publishers ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
