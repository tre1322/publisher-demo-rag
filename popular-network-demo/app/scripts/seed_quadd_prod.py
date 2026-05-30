"""Bootstrap a production DB for Quadd.ai pilot deploy.

Run on the droplet AFTER the app has booted once (so init_db() has created
the schema) but BEFORE the first user registers. Idempotent — safe to re-run.

Usage:
    uv run python -m app.scripts.seed_quadd_prod

What it does:
1. Calls seed.seed_if_empty() to insert the Quadd business + voice brief +
   Day-1 fixtures (no-op if a business already exists).
2. Sets Business.allowed_origins_json = ['https://quadd.ai', 'https://www.quadd.ai']
   so the public chatbot widget enforces CORS to Quadd's marketing domain.
3. Mints a ChatbotIngestionKey for Quadd labeled "Quadd.ai — production"
   (only if no live key exists). The raw key is printed ONCE to stdout —
   capture it for the publisher's .env if you'll be ingesting historical
   chat transcripts.
4. Prints a runbook of the next manual step (registering the Amplafai
   superuser via /api/auth/register).

Deliberately does NOT create any User rows. The first POST /api/auth/register
hits the `is_bootstrap = db.query(User).count() == 0` branch and grants
is_superuser=True. Seeding a user here would silently break that path.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import sys
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# Import order matters: init_db must run before any session opens, but the
# app startup hook (app/main.py) already does that on first boot. This script
# assumes you've booted the app at least once.
from ..db import SessionLocal, init_db
from ..models import Business, ChatbotIngestionKey
from ..seed import seed_if_empty

QUADD_ALLOWED_ORIGINS = [
    "https://quadd.ai",
    "https://www.quadd.ai",
]
INGESTION_KEY_LABEL = "Quadd.ai — production"


def _mint_ingestion_key() -> tuple[str, str, str]:
    """Same shape as app.routers.chatbot._mint_key — kept inline to avoid
    importing the router (which imports the Anthropic SDK at module load)."""
    body = secrets.token_hex(16)
    raw = f"cbk_live_{body}"
    prefix = raw[:12]
    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, prefix, hashed


def _print_step(n: int, msg: str) -> None:
    print(f"[{n}] {msg}")


def _print_runbook(superuser_needed: bool) -> None:
    print()
    print("=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    if superuser_needed:
        print(
            "Bootstrap the Amplafai superuser by POSTing to /api/auth/register\n"
            "with no business_id. First user in an empty-users DB becomes\n"
            "is_superuser=True automatically:\n"
            "\n"
            "  curl -X POST https://dashboard.amplafai.com/api/auth/register \\\n"
            "    -H 'Content-Type: application/json' \\\n"
            "    -d '{\"email\":\"trevor@amplafai.com\",\"password\":\"<long-random>\"}'\n"
        )
        print(
            "Then log in at https://dashboard.amplafai.com/login and mint an\n"
            "invite to your Quadd-owner email (Settings → Invites — or POST\n"
            "/api/auth/invites with role='owner', business_id=1)."
        )
    else:
        print(
            "Users table is already populated — superuser is already bootstrapped.\n"
            "If you need a NEW invite, log in as superuser and mint one via\n"
            "POST /api/auth/invites."
        )
    print()


def main() -> int:
    print("Quadd.ai production seed — start")
    print("-" * 70)

    # Belt-and-suspenders: init_db is idempotent, safe to call here in case
    # the script runs before the app has ever booted.
    init_db()

    # Step 1 — insert the Quadd business if not present.
    inserted = seed_if_empty()
    if inserted:
        _print_step(1, "Inserted Quadd.ai business (id=1) + voice brief + Day-1 fixtures.")
    else:
        _print_step(1, "Quadd.ai business already exists — skipping seed_if_empty().")

    # Step 2-4 happen in a single session.
    with SessionLocal() as db:
        biz: Optional[Business] = db.query(Business).filter(Business.slug == "quadd_ai").first()
        if biz is None:
            print("FAIL  could not locate quadd_ai business after seed_if_empty()")
            return 1

        # Step 2 — lock the widget CORS to Quadd's marketing domains.
        current = biz.allowed_origins_json or []
        if set(current) != set(QUADD_ALLOWED_ORIGINS):
            biz.allowed_origins_json = QUADD_ALLOWED_ORIGINS
            db.commit()
            _print_step(2, f"Set allowed_origins_json = {QUADD_ALLOWED_ORIGINS}")
        else:
            _print_step(2, f"allowed_origins_json already set to {QUADD_ALLOWED_ORIGINS} — skipping.")

        # Step 3 — mint a chatbot ingestion key if none exists yet.
        live_key = (
            db.query(ChatbotIngestionKey)
            .filter(
                ChatbotIngestionKey.business_id == biz.id,
                ChatbotIngestionKey.revoked_at.is_(None),
            )
            .first()
        )
        if live_key is None:
            raw, prefix, hashed = _mint_ingestion_key()
            row = ChatbotIngestionKey(
                business_id=biz.id,
                label=INGESTION_KEY_LABEL,
                key_prefix=prefix,
                key_hash=hashed,
            )
            db.add(row)
            db.commit()
            _print_step(3, f"Minted ingestion key '{INGESTION_KEY_LABEL}'.")
            print()
            print("  RAW KEY (shown ONCE — copy now):")
            print(f"    {raw}")
            print()
            print("  Store this in your publisher-side .env as AMPLAFAI_INGEST_KEY")
            print("  if you'll be POSTing historical chat transcripts to")
            print("  /api/chatbot/ingest. The widget endpoint (/api/widget/chat)")
            print("  does NOT need this — it's anonymous + CORS-gated.")
        else:
            _print_step(3, f"Live ingestion key already exists (prefix={live_key.key_prefix}) — skipping mint.")

        # Step 4 — runbook for superuser bootstrap.
        from ..models import User
        superuser_needed = db.query(User).count() == 0

    print("-" * 70)
    print("Seed complete.")
    _print_runbook(superuser_needed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
