"""Database operations for advertisements."""

import logging

from src.core.database import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the advertisements table."""
    conn = get_connection()
    cursor = conn.cursor()

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
            edition_id INTEGER,
            page INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add columns if they don't exist (migration for existing DBs)
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
    ]:
        try:
            cursor.execute(f"ALTER TABLE advertisements ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    # Create indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ad_category ON advertisements(category)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ad_valid_to ON advertisements(valid_to)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_edition ON advertisements(edition_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_org ON advertisements(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_checksum ON advertisements(checksum)"
    )

    conn.commit()
    conn.close()
    logger.info("Advertisements table initialized")


def insert_advertisement(
    ad_id: str,
    product_name: str,
    advertiser: str,
    description: str | None = None,
    category: str | None = None,
    price: float | None = None,
    original_price: float | None = None,
    discount_percent: float | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    url: str | None = None,
    raw_text: str | None = None,
    publisher: str | None = None,
) -> None:
    """Insert or update an advertisement in the database.

    Args:
        ad_id: Unique advertisement identifier.
        product_name: Name of the product/service.
        advertiser: Company/brand name.
        description: Ad description/copy.
        category: Product category.
        price: Current price.
        original_price: Price before discount.
        discount_percent: Discount percentage.
        valid_from: Start date (YYYY-MM-DD).
        valid_to: End date (YYYY-MM-DD).
        url: Link to product/offer.
        raw_text: Original source text.
        publisher: Name of the publishing newspaper.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO advertisements
        (ad_id, product_name, advertiser, description, category, price,
         original_price, discount_percent, valid_from, valid_to, url, raw_text, publisher)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ad_id,
            product_name,
            advertiser,
            description,
            category,
            price,
            original_price,
            discount_percent,
            valid_from,
            valid_to,
            url,
            raw_text,
            publisher,
        ),
    )

    conn.commit()
    conn.close()


def insert_edition_advertisement(
    ad_id: str,
    advertiser_name: str,
    extracted_text: str,
    edition_id: int | None = None,
    page: int | None = None,
    category: str | None = None,
    publisher: str | None = None,
    organization_id: int | None = None,
    publication_id: int | None = None,
    headline: str | None = None,
    checksum: str | None = None,
    source_filename: str | None = None,
) -> None:
    """Insert an advertisement (from edition or standalone upload)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO advertisements
        (ad_id, product_name, advertiser, raw_text, edition_id, page, category,
         publisher, organization_id, publication_id, headline, checksum,
         cleaned_text, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            ad_id, advertiser_name, advertiser_name, extracted_text,
            edition_id, page, category, publisher, organization_id,
            publication_id, headline, checksum, extracted_text,
        ),
    )

    conn.commit()
    conn.close()


def get_ad_by_checksum(checksum: str) -> dict | None:
    """Check if an ad with this checksum already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM advertisements WHERE checksum = ?", (checksum,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def search_advertisements(
    category: str | None = None,
    max_price: float | None = None,
    on_sale_only: bool = False,
    active_only: bool = True,
    limit: int = 20,
) -> list[dict]:
    """Search advertisements by filters.

    Args:
        category: Product category to filter by.
        max_price: Maximum price filter.
        on_sale_only: Only return items on sale.
        active_only: Only return currently active ads.
        limit: Maximum results.

    Returns:
        List of matching advertisements.
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM advertisements WHERE 1=1"
    params: list = []

    if category:
        query += " AND category LIKE ?"
        params.append(f"%{category}%")

    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)

    if on_sale_only:
        query += " AND discount_percent > 0"

    if active_only:
        query += " AND (valid_to IS NULL OR valid_to >= date('now'))"

    query += " ORDER BY discount_percent DESC NULLS LAST LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_random_advertisements(limit: int = 2) -> list[dict]:
    """Get random advertisements with best discounts.

    Args:
        limit: Maximum number of ads to return.

    Returns:
        List of advertisement dictionaries.
    """
    return search_advertisements(on_sale_only=True, active_only=True, limit=limit)


def get_advertisement_by_id(ad_id: str) -> dict | None:
    """Get an advertisement by its ID.

    Args:
        ad_id: Advertisement identifier.

    Returns:
        Advertisement data or None.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM advertisements WHERE ad_id = ?", (ad_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_all_ad_categories() -> list[str]:
    """Get all unique advertisement categories.

    Returns:
        List of unique categories.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT DISTINCT category FROM advertisements WHERE category IS NOT NULL"
    )
    rows = cursor.fetchall()
    conn.close()

    return sorted([row["category"] for row in rows])


def get_advertisement_count() -> int:
    """Get total number of advertisements in the database.

    Returns:
        Advertisement count.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM advertisements")
    count = cursor.fetchone()[0]
    conn.close()

    return count


def clear_advertisements() -> None:
    """Clear all advertisements from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM advertisements")
    conn.commit()
    conn.close()
    logger.info("Advertisements cleared")
