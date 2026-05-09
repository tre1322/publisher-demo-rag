"""Database operations for Amplora billing (W1 — multi-tenant foundation).

Tables:
  - subscriptions: one row per Stripe subscription_id (cancel + resub = new row)
  - tier_history: append-only log; every tier change writes a row
  - publisher_revenue_share: open-ended windows of (org → publisher, share_pct)

Conventions match the rest of the codebase:
  - try/except ALTER TABLE ADD COLUMN for idempotent migrations on Railway
  - get_connection() from src.core.database
  - ISO datetime strings (not unix timestamps)
"""

import logging
from datetime import datetime, timezone

from src.core.database import get_connection

logger = logging.getLogger(__name__)


# Stripe-aligned subscription statuses. Treat these as the canonical set;
# any incoming status not in this set is logged + stored as-is so we don't
# silently drop a future Stripe addition.
KNOWN_STATUSES = {
    "trialing",
    "active",
    "past_due",
    "canceled",
    "unpaid",
    "incomplete",
    "incomplete_expired",
    "paused",
}

# Amplora tier names. Matches existing organizations.tier ('starter' default)
# and business_invites.tier ('growth' default). Stripe Price metadata.tier
# must use these strings.
KNOWN_TIERS = {"starter", "growth", "concierge"}


# ── Init ────────────────────────────────────────────────────────────


def init_table() -> None:
    """Create the three W1 billing tables (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── subscriptions ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            tier TEXT NOT NULL,
            status TEXT NOT NULL,
            processor TEXT NOT NULL DEFAULT 'stripe',
            processor_customer_id TEXT,
            processor_subscription_id TEXT,
            current_period_start TEXT,
            current_period_end TEXT,
            canceled_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        )
    """)
    # Future-proof: ALTER ADD COLUMN here for any new column added later.
    for col, coltype in [
        # placeholder for future columns; keeps the migration discipline visible
    ]:
        try:
            cursor.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_subs_org ON subscriptions(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_subs_status ON subscriptions(status)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subs_processor_sub "
        "ON subscriptions(processor, processor_subscription_id) "
        "WHERE processor_subscription_id IS NOT NULL"
    )

    # ── tier_history ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tier_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            subscription_id INTEGER,
            from_tier TEXT,
            to_tier TEXT NOT NULL,
            effective_at TEXT NOT NULL,
            changed_by TEXT,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tier_history_org "
        "ON tier_history(organization_id)"
    )

    # ── publisher_revenue_share ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS publisher_revenue_share (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            selling_publisher_id INTEGER NOT NULL,
            share_pct REAL NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT,
            attribution_source TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id),
            FOREIGN KEY (selling_publisher_id) REFERENCES publishers(id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_rev_share_org "
        "ON publisher_revenue_share(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_rev_share_pub "
        "ON publisher_revenue_share(selling_publisher_id)"
    )

    conn.commit()
    conn.close()
    logger.info("Billing tables initialized (subscriptions, tier_history, publisher_revenue_share)")


# ── Subscription CRUD ───────────────────────────────────────────────


def upsert_subscription(
    organization_id: int,
    tier: str,
    status: str,
    processor: str = "stripe",
    processor_customer_id: str | None = None,
    processor_subscription_id: str | None = None,
    current_period_start: str | None = None,
    current_period_end: str | None = None,
    canceled_at: str | None = None,
) -> int:
    """Insert or update a subscription row. Keyed on (processor, processor_subscription_id).

    Returns:
        The subscription row id.
    """
    if status not in KNOWN_STATUSES:
        logger.warning("Unknown subscription status %r — storing anyway", status)
    if tier not in KNOWN_TIERS:
        logger.warning("Unknown tier %r — storing anyway", tier)

    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    if processor_subscription_id:
        cursor.execute(
            "SELECT id FROM subscriptions "
            "WHERE processor = ? AND processor_subscription_id = ?",
            (processor, processor_subscription_id),
        )
        existing = cursor.fetchone()
    else:
        existing = None

    if existing:
        sub_id = existing["id"]
        cursor.execute(
            """
            UPDATE subscriptions SET
                tier = ?,
                status = ?,
                processor_customer_id = COALESCE(?, processor_customer_id),
                current_period_start = COALESCE(?, current_period_start),
                current_period_end = COALESCE(?, current_period_end),
                canceled_at = COALESCE(?, canceled_at),
                updated_at = ?
            WHERE id = ?
            """,
            (
                tier,
                status,
                processor_customer_id,
                current_period_start,
                current_period_end,
                canceled_at,
                now,
                sub_id,
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO subscriptions
                (organization_id, tier, status, processor,
                 processor_customer_id, processor_subscription_id,
                 current_period_start, current_period_end, canceled_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                tier,
                status,
                processor,
                processor_customer_id,
                processor_subscription_id,
                current_period_start,
                current_period_end,
                canceled_at,
                now,
                now,
            ),
        )
        sub_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return sub_id


def get_active_subscription(organization_id: int) -> dict | None:
    """Return the currently-billing subscription for an org, if any.

    'Currently-billing' = status in (active, trialing, past_due). Picks the
    most-recently-updated row if multiple match (shouldn't happen but be safe).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM subscriptions
        WHERE organization_id = ?
          AND status IN ('active', 'trialing', 'past_due')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (organization_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_subscription_by_processor_id(
    processor_subscription_id: str, processor: str = "stripe"
) -> dict | None:
    """Look up a subscription by its processor-side ID (e.g. Stripe sub_*)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM subscriptions "
        "WHERE processor = ? AND processor_subscription_id = ?",
        (processor, processor_subscription_id),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Tier history ────────────────────────────────────────────────────


def log_tier_change(
    organization_id: int,
    to_tier: str,
    from_tier: str | None = None,
    subscription_id: int | None = None,
    changed_by: str = "system",
    reason: str | None = None,
    effective_at: str | None = None,
) -> int:
    """Append a row to tier_history. Always called when tier moves."""
    effective_at = effective_at or datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tier_history
            (organization_id, subscription_id, from_tier, to_tier,
             effective_at, changed_by, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            subscription_id,
            from_tier,
            to_tier,
            effective_at,
            changed_by,
            reason,
        ),
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_tier_history(organization_id: int) -> list[dict]:
    """Return all tier-change rows for an org, oldest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM tier_history WHERE organization_id = ? "
        "ORDER BY effective_at ASC, id ASC",
        (organization_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ── Publisher revenue share ─────────────────────────────────────────


def open_revenue_share_window(
    organization_id: int,
    selling_publisher_id: int,
    share_pct: float,
    attribution_source: str,
    window_start: str | None = None,
    notes: str | None = None,
) -> int:
    """Open a new revenue-share window for an org.

    Closes any currently-open window for the same org first (sets window_end
    to now), so there's only ever one open window per org at a time.
    """
    window_start = window_start or datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()

    # Close any open window
    cursor.execute(
        """
        UPDATE publisher_revenue_share
        SET window_end = ?
        WHERE organization_id = ? AND window_end IS NULL
        """,
        (window_start, organization_id),
    )

    cursor.execute(
        """
        INSERT INTO publisher_revenue_share
            (organization_id, selling_publisher_id, share_pct,
             window_start, attribution_source, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            selling_publisher_id,
            share_pct,
            window_start,
            attribution_source,
            notes,
        ),
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_current_revenue_share(organization_id: int) -> dict | None:
    """Return the currently-open share window for an org, if any."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM publisher_revenue_share
        WHERE organization_id = ? AND window_end IS NULL
        ORDER BY window_start DESC
        LIMIT 1
        """,
        (organization_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_revenue_share_history(organization_id: int) -> list[dict]:
    """All share windows for an org, oldest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM publisher_revenue_share WHERE organization_id = ? "
        "ORDER BY window_start ASC",
        (organization_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
