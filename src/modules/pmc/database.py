"""Database operations for Amplora PMC (W2 — voice interview agent).

Tables:
  - product_marketing_contexts: versioned per-org. status: draft|accepted|superseded.
    The currently-accepted row is the canonical input for every downstream agent.
  - pmc_interview_sessions: one row per interview attempt. Tracks voice provider,
    duration, transcript URL/text. Linked to the PMC row that resulted from it.

Conventions match the rest of the codebase:
  - try/except ALTER TABLE ADD COLUMN for idempotent migrations on Railway
  - get_connection() from src.core.database
  - ISO datetime strings (not unix timestamps)
  - Status alignment: PMC starts 'draft', flips to 'accepted' on owner accept,
    prior-accepted row flips to 'superseded' atomically.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.database import get_connection

logger = logging.getLogger(__name__)


KNOWN_PMC_STATUSES = {"draft", "accepted", "superseded"}
KNOWN_SESSION_STATUSES = {
    "scheduled",
    "in_progress",
    "completed",
    "failed",
    "transcript_pasted",  # W2.1 manual entry path
    "voice_awaiting",     # W2.2 — quantitative saved, owner about to start call
    "voice_in_progress",  # W2.2 — agent connected, call live
    "voice_completed",    # W2.2 — agent posted transcript back
    "voice_partial",      # W2.2 — owner dropped mid-call, partial transcript saved
}
KNOWN_VOICE_PROVIDERS = {"manual_paste", "twilio", "livekit"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Init ────────────────────────────────────────────────────────────


def init_table() -> None:
    """Create the two W2 PMC tables (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── pmc_interview_sessions ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pmc_interview_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            voice_provider TEXT,
            transcript_url TEXT,
            transcript_text TEXT,
            duration_seconds INTEGER,
            scheduled_at TEXT,
            started_at TEXT,
            ended_at TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        )
    """)
    for col, coltype in [
        # W2.2 — quantitative answers persisted on the session so they
        # survive the round trip while the owner is in the voice call.
        # The /voice/complete handler reads them back to feed
        # generate_pmc_from_transcript().
        ("quantitative_json", "TEXT"),
    ]:
        try:
            cursor.execute(
                f"ALTER TABLE pmc_interview_sessions ADD COLUMN {col} {coltype}"
            )
        except Exception:
            pass

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmc_sessions_org "
        "ON pmc_interview_sessions(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmc_sessions_status "
        "ON pmc_interview_sessions(status)"
    )

    # ── product_marketing_contexts ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_marketing_contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            quantitative_json TEXT,
            qualitative_md TEXT,
            transcript_text TEXT,
            interview_session_id INTEGER,
            generator_model TEXT,
            generator_prompt_version TEXT,
            script_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            accepted_at TEXT,
            superseded_at TEXT,
            created_by_user_id INTEGER,
            accepted_by_user_id INTEGER,
            FOREIGN KEY (organization_id) REFERENCES organizations(id),
            FOREIGN KEY (interview_session_id) REFERENCES pmc_interview_sessions(id)
        )
    """)
    for col, coltype in [
        # placeholder for future columns
    ]:
        try:
            cursor.execute(
                f"ALTER TABLE product_marketing_contexts ADD COLUMN {col} {coltype}"
            )
        except Exception:
            pass

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmc_org "
        "ON product_marketing_contexts(organization_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmc_status "
        "ON product_marketing_contexts(status)"
    )
    # Only one 'accepted' PMC per org at a time. Enforced by `accept_pmc()`
    # transactional logic; this index makes the invariant queryable cheaply.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pmc_org_accepted "
        "ON product_marketing_contexts(organization_id) "
        "WHERE status = 'accepted'"
    )

    conn.commit()
    conn.close()
    logger.info("PMC tables initialized (product_marketing_contexts, pmc_interview_sessions)")


# ── Interview sessions ──────────────────────────────────────────────


def create_session(
    organization_id: int,
    voice_provider: str = "manual_paste",
    notes: str | None = None,
) -> int:
    """Create a new interview session. Returns session id."""
    if voice_provider not in KNOWN_VOICE_PROVIDERS:
        raise ValueError(
            f"unknown voice_provider {voice_provider!r}; "
            f"expected one of {sorted(KNOWN_VOICE_PROVIDERS)}"
        )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pmc_interview_sessions
            (organization_id, status, voice_provider, scheduled_at, notes)
        VALUES (?, 'scheduled', ?, ?, ?)
        """,
        (organization_id, voice_provider, _now(), notes),
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def complete_session_with_transcript(
    session_id: int,
    transcript_text: str,
    duration_seconds: int | None = None,
    transcript_url: str | None = None,
) -> None:
    """Mark a session as having received a transcript (manual paste in W2.1)."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        """
        UPDATE pmc_interview_sessions
           SET status='transcript_pasted',
               transcript_text=?,
               transcript_url=?,
               duration_seconds=?,
               started_at=COALESCE(started_at, ?),
               ended_at=?,
               updated_at=?
         WHERE id=?
        """,
        (transcript_text, transcript_url, duration_seconds, now, now, now, session_id),
    )
    conn.commit()
    conn.close()


def get_session(session_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pmc_interview_sessions WHERE id=?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    # Decode quantitative_json eagerly so callers don't have to think about it.
    if d.get("quantitative_json"):
        try:
            d["quantitative"] = json.loads(d["quantitative_json"])
        except (TypeError, ValueError):
            d["quantitative"] = {}
    else:
        d["quantitative"] = {}
    return d


# ── W2.2 voice session helpers ──────────────────────────────────────


def save_session_quantitative(
    session_id: int, quantitative: dict[str, Any]
) -> None:
    """Stash the pre-interview form answers on the session.

    Called from /pmc/voice/start. The /pmc/voice/complete handler reads
    them back to feed generate_pmc_from_transcript().
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE pmc_interview_sessions "
        "SET quantitative_json=?, status='voice_awaiting', updated_at=? "
        "WHERE id=?",
        (json.dumps(quantitative), _now(), session_id),
    )
    conn.commit()
    conn.close()


def mark_session_voice_started(session_id: int) -> None:
    """Agent has joined the room and started the conversation."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        "UPDATE pmc_interview_sessions "
        "SET status='voice_in_progress', started_at=COALESCE(started_at, ?), updated_at=? "
        "WHERE id=?",
        (now, now, session_id),
    )
    conn.commit()
    conn.close()


def complete_voice_session(
    session_id: int,
    transcript_text: str,
    duration_seconds: int | None = None,
    recording_url: str | None = None,
    partial: bool = False,
) -> None:
    """Finalize a voice session after the agent posts the transcript back.

    `recording_url` is the LiveKit Egress output URL (DigitalOcean Spaces).
    `partial=True` records that the owner disconnected before the agent
    judged the interview complete; downstream PMC generation still runs.
    """
    status = "voice_partial" if partial else "voice_completed"
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    cursor.execute(
        """
        UPDATE pmc_interview_sessions
           SET status=?,
               transcript_text=?,
               transcript_url=?,
               duration_seconds=?,
               ended_at=?,
               updated_at=?
         WHERE id=?
        """,
        (
            status,
            transcript_text,
            recording_url,
            duration_seconds,
            now,
            now,
            session_id,
        ),
    )
    conn.commit()
    conn.close()


def get_session_for_org(session_id: int, organization_id: int) -> dict | None:
    """Same as get_session but enforces org ownership. Returns None on mismatch.

    Used by the /voice/complete callback to ensure the HMAC-signed session_id
    actually belongs to the org_id encoded in the same token (defense in depth).
    """
    s = get_session(session_id)
    if not s:
        return None
    if s["organization_id"] != organization_id:
        return None
    return s


# ── PMC drafts and acceptance ───────────────────────────────────────


def _next_version(cursor, organization_id: int) -> int:
    cursor.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM product_marketing_contexts "
        "WHERE organization_id=?",
        (organization_id,),
    )
    return cursor.fetchone()[0]


def create_pmc_draft(
    organization_id: int,
    qualitative_md: str,
    quantitative: dict[str, Any],
    transcript_text: str | None,
    interview_session_id: int | None,
    generator_model: str,
    generator_prompt_version: str,
    script_version: str,
    created_by_user_id: int | None = None,
) -> int:
    """Insert a new PMC row in 'draft' status. Returns pmc id."""
    conn = get_connection()
    cursor = conn.cursor()
    version = _next_version(cursor, organization_id)
    cursor.execute(
        """
        INSERT INTO product_marketing_contexts
            (organization_id, version, status, quantitative_json, qualitative_md,
             transcript_text, interview_session_id,
             generator_model, generator_prompt_version, script_version,
             created_by_user_id)
        VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            version,
            json.dumps(quantitative),
            qualitative_md,
            transcript_text,
            interview_session_id,
            generator_model,
            generator_prompt_version,
            script_version,
            created_by_user_id,
        ),
    )
    pmc_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pmc_id


def update_pmc_draft(
    pmc_id: int,
    qualitative_md: str | None = None,
    quantitative: dict[str, Any] | None = None,
) -> None:
    """Owner edits to a draft. Refuses to touch a non-draft row."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status FROM product_marketing_contexts WHERE id=?", (pmc_id,)
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"PMC {pmc_id} not found")
    if row["status"] != "draft":
        conn.close()
        raise ValueError(
            f"PMC {pmc_id} status is {row['status']!r}; "
            f"only 'draft' rows are editable. Re-run the interview to create a new draft."
        )

    fields = []
    params: list[Any] = []
    if qualitative_md is not None:
        fields.append("qualitative_md=?")
        params.append(qualitative_md)
    if quantitative is not None:
        fields.append("quantitative_json=?")
        params.append(json.dumps(quantitative))
    if not fields:
        conn.close()
        return
    fields.append("updated_at=?")
    params.append(_now())
    params.append(pmc_id)
    cursor.execute(
        f"UPDATE product_marketing_contexts SET {', '.join(fields)} WHERE id=?",
        params,
    )
    conn.commit()
    conn.close()


def accept_pmc(pmc_id: int, user_id: int | None = None) -> None:
    """Atomically: flip target draft → accepted, supersede any prior accepted PMC.

    Enforces the one-accepted-PMC-per-org invariant.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, organization_id, status FROM product_marketing_contexts WHERE id=?",
        (pmc_id,),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"PMC {pmc_id} not found")
    if row["status"] != "draft":
        conn.close()
        raise ValueError(
            f"PMC {pmc_id} status is {row['status']!r}; "
            f"only 'draft' rows can be accepted."
        )

    org_id = row["organization_id"]
    now = _now()
    try:
        cursor.execute("BEGIN")
        cursor.execute(
            """
            UPDATE product_marketing_contexts
               SET status='superseded', superseded_at=?, updated_at=?
             WHERE organization_id=? AND status='accepted'
            """,
            (now, now, org_id),
        )
        cursor.execute(
            """
            UPDATE product_marketing_contexts
               SET status='accepted',
                   accepted_at=?,
                   accepted_by_user_id=?,
                   updated_at=?
             WHERE id=?
            """,
            (now, user_id, now, pmc_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Reads ───────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("quantitative_json"):
        try:
            d["quantitative"] = json.loads(d["quantitative_json"])
        except (TypeError, ValueError):
            d["quantitative"] = {}
    else:
        d["quantitative"] = {}
    return d


def get_pmc(pmc_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM product_marketing_contexts WHERE id=?", (pmc_id,))
    row = cursor.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_canonical_pmc(organization_id: int) -> dict | None:
    """Currently-accepted PMC for an org. None if no version has been accepted yet."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM product_marketing_contexts "
        "WHERE organization_id=? AND status='accepted'",
        (organization_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_latest_draft(organization_id: int) -> dict | None:
    """Most recent 'draft' for the org — what the owner reviews."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM product_marketing_contexts "
        "WHERE organization_id=? AND status='draft' "
        "ORDER BY version DESC LIMIT 1",
        (organization_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_pmc_versions(organization_id: int) -> list[dict]:
    """All PMC versions for an org, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM product_marketing_contexts "
        "WHERE organization_id=? "
        "ORDER BY version DESC",
        (organization_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
