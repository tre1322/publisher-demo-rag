"""Shared database utilities — Amplora only.

Single SQLite file at DATA_DIR/articles.db (filename retained for
historical reasons + Railway volume mount path; the data is now
Amplora-only: orgs / business_users / billing / PMC).

Each module owns its own table(s) and exposes an idempotent
init_table() that's called from init_all_tables() below.
"""

import logging
import sqlite3

from src.core.config import DATA_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_PATH = DATA_DIR / "articles.db"


def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with Row factory enabled."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_all_tables() -> None:
    """Initialize all Amplora database tables.

    Single authoritative runtime init path — each module's init_table()
    handles CREATE TABLE IF NOT EXISTS + ALTER TABLE migrations. Order
    matters because of FK references:

      1. organizations        (referenced by ~everything)
      2. publishers           (referenced by billing.publisher_revenue_share)
      3. business_users + invites  (auth side of the org)
      4. billing              (W1 — subscriptions, tier_history, revenue_share)
      5. pmc                  (W2 — product_marketing_contexts + interview_sessions)
    """
    logger.info(f"Initializing Amplora database at {DATABASE_PATH}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    from src.business_frontend import auth as biz_auth
    from src.modules.billing import database as billing_db
    from src.modules.organizations import database as orgs_db
    from src.modules.pmc import database as pmc_db
    from src.modules.publishers import database as publishers_db

    orgs_db.init_table()
    publishers_db.init_table()
    biz_auth.init_tables()

    # W1: Amplora multi-tenant billing — subscriptions, tier_history,
    # publisher_revenue_share. AFTER organizations + publishers (FK refs).
    billing_db.init_table()

    # W2: Amplora PMC — product_marketing_contexts + pmc_interview_sessions.
    # AFTER organizations (FK ref).
    pmc_db.init_table()

    # Seed default publishers (Cottonwood County Citizen, Pipestone Star).
    # Idempotent — safe to call on every boot.
    publishers_db.seed_publishers()

    logger.info(f"Amplora tables initialized at {DATABASE_PATH}")
