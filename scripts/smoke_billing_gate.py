"""Smoke test for the BILLING_ENABLED pilot kill-switch.

Exercises the real HTTP routes with a TestClient + authed session:

  1. BILLING_ENABLED=false (pilot default):
       GET  /business/billing            -> 200, shows the pilot panel,
                                            no checkout <form>
       POST /business/billing/checkout   -> 303 back to /business/billing
                                            (no Stripe call attempted)
  2. BILLING_ENABLED=true:
       GET  /business/billing            -> 200, tier buttons + checkout
                                            form rendered

Hermetic — tmp DB, never touches data/articles.db. No Stripe keys needed.

Run: uv run python scripts/smoke_billing_gate.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp(prefix="amplafai_billing_gate_")) / "articles.db"

import src.core.database as core_db  # noqa: E402

core_db.DATABASE_PATH = _TMP_DB

import src.core.config as cfg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.business_frontend.auth import (  # noqa: E402
    COOKIE_NAME,
    create_business_user,
    create_session,
    get_user_by_id,
)
from src.chatbot import create_app  # noqa: E402
from src.core.database import init_all_tables  # noqa: E402
from src.modules.organizations.database import insert_organization  # noqa: E402

_passed = 0


def check(label: str, cond: bool) -> None:
    global _passed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        raise SystemExit(1)


init_all_tables()
org_id = insert_organization("Pilot Test Biz")
uid = create_business_user("owner@pilot.test", "pw-123456", "Pilot Owner", org_id)
user = get_user_by_id(uid)
assert user is not None
token = create_session(user)

app = create_app()
client = TestClient(app)
client.cookies.set(COOKIE_NAME, token)

print("-- Case A: BILLING_ENABLED=false (pilot default) --")
check("config default is false", cfg.BILLING_ENABLED is False)

r = client.get("/business/billing")
check("GET /business/billing -> 200", r.status_code == 200)
check("pilot panel shown", "Amplafai pilot" in r.text)
check(
    "no checkout form rendered",
    'action="/business/billing/checkout"' not in r.text,
)

r2 = client.post(
    "/business/billing/checkout",
    data={"tier": "growth"},
    follow_redirects=False,
)
check("checkout POST -> 303", r2.status_code == 303)
check(
    "checkout redirects back to /business/billing",
    r2.headers.get("location") == "/business/billing",
)

print("-- Case B: BILLING_ENABLED=true --")
cfg.BILLING_ENABLED = True
r3 = client.get("/business/billing")
check("GET /business/billing -> 200", r3.status_code == 200)
check(
    "checkout form rendered when enabled",
    'action="/business/billing/checkout"' in r3.text,
)
check(
    "tier picker heading shown",
    ("Pick your plan" in r3.text) or ("Change your plan" in r3.text),
)

print(f"\n=== Billing-gate smoke PASSED ({_passed} checks) ===")
