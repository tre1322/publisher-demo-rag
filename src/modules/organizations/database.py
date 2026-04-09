"""Database operations for organizations and publications."""

import logging
import re
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def init_table() -> None:
    """Initialize organizations and publications tables."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add columns if they don't exist (migration for existing DBs).
    # `keywords` in particular is written by directory enrichment, read by the
    # admin directory editor, business-detail template, and searched by
    # SearchTools.search_directory — fresh DBs need it to exist.
    for col, coltype in [
        ("address", "TEXT"),
        ("city", "TEXT"),
        ("state", "TEXT"),
        ("phone", "TEXT"),
        ("email", "TEXT"),
        ("website", "TEXT"),
        ("social_json", "TEXT"),
        ("hours_json", "TEXT"),
        ("category", "TEXT"),
        ("description", "TEXT"),
        ("services", "TEXT"),
        ("keywords", "TEXT"),
        ("publisher", "TEXT"),
        ("enrichment_status", "TEXT DEFAULT 'pending'"),
        ("enrichment_error", "TEXT"),
        ("last_enriched_at", "TEXT"),
        ("last_advertised_at", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE organizations ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    cursor.execute("""
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

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publications_org ON publications(organization_id)"
    )

    conn.commit()
    conn.close()
    logger.info("Organizations and publications tables initialized")


def insert_organization(name: str, slug: str | None = None) -> int:
    """Insert a new organization. Returns existing ID if slug already exists."""
    slug = slug or _slugify(name)
    conn = get_connection()
    cursor = conn.cursor()

    # Check for existing
    cursor.execute("SELECT id FROM organizations WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row["id"]

    cursor.execute(
        "INSERT INTO organizations (name, slug) VALUES (?, ?)",
        (name, slug),
    )
    org_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return org_id


def insert_publication(
    organization_id: int,
    name: str,
    slug: str | None = None,
    market: str | None = None,
    state: str | None = None,
    timezone: str | None = None,
) -> int:
    """Insert a new publication. Returns existing ID if slug already exists."""
    slug = slug or _slugify(name)
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM publications WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row["id"]

    cursor.execute(
        """INSERT INTO publications (organization_id, name, slug, market, state, timezone)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (organization_id, name, slug, market, state, timezone),
    )
    pub_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pub_id


def get_organization(org_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_organization_by_slug(slug: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM organizations WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_organizations() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM organizations ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_publication(pub_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM publications WHERE id = ?", (pub_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_publications_for_org(org_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM publications WHERE organization_id = ? ORDER BY name",
        (org_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_publications() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.*, o.name as organization_name
        FROM publications p
        JOIN organizations o ON p.organization_id = o.id
        ORDER BY o.name, p.name
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
