"""Database operations for sponsored answers.

Sponsored answers are business-created content that appears in chatbot
responses when a user asks about a matching category. Each answer has
an impression budget controlled by the business's tier.

Cross-publisher by design: businesses from either publication appear in
both chatbots (commuting-distance shared directory), so no publisher
filter here — only organization_id ownership matters.
"""

import logging
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Create sponsored_answers table (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sponsored_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            impressions_used INTEGER DEFAULT 0,
            impressions_limit INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            tier TEXT DEFAULT 'growth',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sponsored_org ON sponsored_answers(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sponsored_category "
        "ON sponsored_answers(category, status)"
    )
    conn.commit()
    conn.close()
    logger.info("sponsored_answers table ready")


def create_sponsored_answer(
    org_id: int,
    category: str,
    answer_text: str,
    impressions_limit: int,
    tier: str = "growth",
) -> int:
    """Create a sponsored answer. Returns new row id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sponsored_answers "
        "(organization_id, category, answer_text, impressions_limit, tier) "
        "VALUES (?, ?, ?, ?, ?)",
        (org_id, category, answer_text, impressions_limit, tier),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_sponsored_answers_for_org(org_id: int) -> list[dict]:
    """Get all sponsored answers for an organization (any status)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sponsored_answers WHERE organization_id = ? ORDER BY created_at DESC",
        (org_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# Stop words to skip when breaking a query into match keywords.
# Includes grammatical fillers AND generic temporal/common words that
# frequently appear in ANY sponsored answer text (e.g. "today" in the
# phrase "today's digital world") and would cause false positives.
_STOP_WORDS = frozenset(
    """
    a an the is are was were be been being have has had do does did
    will would could should can may might shall need want like get got
    some any where what which who how that this those these there here
    to for of in on at by with from about into and or but not no so if
    then than very just also too up out it its i me my we our you your
    mine ours

    today tomorrow yesterday now day week month year time morning
    evening afternoon night tonight current currently recent recently
    business businesses service services company companies thing things
    stuff something someone somebody anyone anybody everyone everybody
    place places somewhere anywhere everywhere people person
    really actually basically literally probably usually generally
    good great best better nice much many more most less least few
    new old big small large tiny huge little
    please thanks thank hello hi hey
""".split()
)


def _extract_keywords(text: str) -> list[str]:
    """Pull keyword tokens from a query for sponsored-answer matching."""
    import re as _re

    if not text:
        return []
    words = _re.findall(r"[a-zA-Z]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _STOP_WORDS]


def find_matching_sponsored(
    query: str | None = None, category: str | None = None
) -> list[dict]:
    """Find active sponsored answers matching a query and/or category.

    Keyword-first match policy (per Trevor's choice for Main Street OS
    pilot): matches are surfaced whenever query keywords appear in the
    sponsored answer's text, category, or business name. Exact category
    match is also honored. Each row only appears once thanks to the OR
    query + LIMIT 3.
    """
    keywords = _extract_keywords(query or "")
    if not keywords and not category:
        return []

    clauses: list[str] = []
    params: list = []

    if category:
        clauses.append("LOWER(sa.category) = ?")
        params.append(category.lower())

    for word in keywords:
        clauses.append(
            "(LOWER(sa.answer_text) LIKE ? "
            "OR LOWER(sa.category) LIKE ? "
            "OR LOWER(o.name) LIKE ?)"
        )
        pat = f"%{word}%"
        params.extend([pat, pat, pat])

    if not clauses:
        return []

    sql = f"""
        SELECT sa.*, o.name as org_name, o.phone as org_phone, o.address as org_address
        FROM sponsored_answers sa
        JOIN organizations o ON sa.organization_id = o.id
        WHERE sa.status = 'active'
          AND sa.impressions_used < sa.impressions_limit
          AND ({" OR ".join(clauses)})
        ORDER BY sa.impressions_used ASC
        LIMIT 3
    """

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_active_sponsored_for_category(category: str) -> list[dict]:
    """Get active sponsored answers matching a category.

    Returns at most 3 answers still within their impression budget,
    ordered by impressions_used ascending (fairness: lighter-used
    answers get priority so everyone hits their budget).
    Joins to organizations to surface the business name/phone/address.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT sa.*, o.name as org_name, o.phone as org_phone, o.address as org_address
        FROM sponsored_answers sa
        JOIN organizations o ON sa.organization_id = o.id
        WHERE sa.status = 'active'
          AND sa.impressions_used < sa.impressions_limit
          AND sa.category = ?
        ORDER BY sa.impressions_used ASC
        LIMIT 3
    """,
        (category,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def increment_impression(sponsored_id: int) -> bool:
    """Increment impression count. Returns False if at limit or inactive."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sponsored_answers "
        "SET impressions_used = impressions_used + 1, updated_at = ? "
        "WHERE id = ? AND impressions_used < impressions_limit AND status = 'active'",
        (datetime.now().isoformat(), sponsored_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_sponsored_answer(
    answer_id: int,
    org_id: int,
    answer_text: str | None = None,
    category: str | None = None,
) -> None:
    """Update a sponsored answer. Ownership validated via org_id."""
    updates = []
    values = []
    if answer_text is not None:
        updates.append("answer_text = ?")
        values.append(answer_text)
    if category is not None:
        updates.append("category = ?")
        values.append(category)
    if not updates:
        return
    updates.append("updated_at = ?")
    values.append(datetime.now().isoformat())
    values.extend([answer_id, org_id])

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE sponsored_answers SET {', '.join(updates)} "
        "WHERE id = ? AND organization_id = ?",
        values,
    )
    conn.commit()
    conn.close()


def deactivate_sponsored_answer(answer_id: int, org_id: int) -> None:
    """Set answer status to inactive. Ownership validated via org_id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sponsored_answers SET status = 'inactive', updated_at = ? "
        "WHERE id = ? AND organization_id = ?",
        (datetime.now().isoformat(), answer_id, org_id),
    )
    conn.commit()
    conn.close()
