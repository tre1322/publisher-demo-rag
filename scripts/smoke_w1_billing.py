"""Smoke test for W1 (multi-tenant billing foundation).

Exercises:
  1. init_all_tables creates subscriptions / tier_history / publisher_revenue_share
  2. apply_event(customer.subscription.created) inserts a subscriptions row
  3. apply_event(customer.subscription.updated) at a new tier appends a tier_history row
  4. apply_event(customer.subscription.deleted) flips status to canceled
  5. open_revenue_share_window twice closes the prior window
  6. attribute_publisher_at_signup() raises NotImplementedError (until Trevor implements it)

Hermetic — uses a tmp DB, never touches data/articles.db.

Run: uv run python scripts/smoke_w1_billing.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pre-import patch: redirect DATABASE_PATH to a tmp file BEFORE any module
# that touches the DB at import time gets imported.
_TMP_DIR = tempfile.mkdtemp(prefix="amplora_w1_smoke_")
_TMP_DB = Path(_TMP_DIR) / "articles.db"

import src.core.database as core_db  # noqa: E402

core_db.DATABASE_PATH = _TMP_DB

# Now safe to import everything else
from src.core.database import get_connection  # noqa: E402
from src.modules.billing import database as billing_db  # noqa: E402
from src.modules.billing.database import (  # noqa: E402
    KNOWN_STATUSES,
    KNOWN_TIERS,
    get_active_subscription,
    get_current_revenue_share,
    get_revenue_share_history,
    get_subscription_by_processor_id,
    get_tier_history,
    open_revenue_share_window,
)
from src.modules.billing.stripe_webhook import apply_event  # noqa: E402
from src.modules.organizations import database as orgs_db  # noqa: E402
from src.modules.organizations.database import insert_organization  # noqa: E402
from src.modules.publishers import database as publishers_db  # noqa: E402
from src.modules.publishers.database import insert_publisher  # noqa: E402


def init_w1_tables_only() -> None:
    """Init just the tables W1 needs.

    NOTE: We skip the full init_all_tables() because importing
    src.modules.advertisements triggers a DB query at module-level
    (search.py:340 — get_ad_tools_schema is called at import) before
    the advertisements table exists. That's a latent bug for fresh-DB
    bootstraps; tracked separately. W1 only needs orgs/publishers/billing.
    """
    orgs_db.init_table()
    publishers_db.init_table()
    billing_db.init_table()


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL] {msg}{RESET}")
    raise AssertionError(msg)


def section(msg: str) -> None:
    print(f"\n{YELLOW}-- {msg} {'-' * max(0, 60 - len(msg))}{RESET}")


def make_event(event_type: str, sub_id: str, org_id: int, tier: str,
               status: str, customer_id: str = "cus_smoke",
               canceled: bool = False) -> dict:
    """Build a minimal Stripe event dict matching webhook expectations."""
    return {
        "id": f"evt_{event_type}_{sub_id}",
        "type": event_type,
        "data": {
            "object": {
                "id": sub_id,
                "customer": customer_id,
                "status": status,
                "current_period_start": 1_700_000_000,
                "current_period_end": 1_702_592_000,
                "canceled_at": 1_702_700_000 if canceled else None,
                "metadata": {"organization_id": str(org_id)},
                "items": {
                    "data": [
                        {
                            "price": {
                                "id": f"price_{tier}",
                                "metadata": {"tier": tier},
                            }
                        }
                    ]
                },
            }
        },
    }


def main() -> int:
    print(f"Tmp DB: {_TMP_DB}")

    # ── Step 1: init schema ───────────────────────────────────────
    section("Step 1: billing.init_table creates W1 tables on a fresh DB")
    init_w1_tables_only()
    conn = get_connection()
    cur = conn.cursor()
    expected = {"subscriptions", "tier_history", "publisher_revenue_share"}
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('subscriptions', 'tier_history', 'publisher_revenue_share')"
    )
    found = {r[0] for r in cur.fetchall()}
    conn.close()
    if found != expected:
        fail(f"missing W1 tables: expected {expected}, got {found}")
    ok(f"all W1 tables created: {sorted(found)}")
    ok(f"KNOWN_STATUSES has {len(KNOWN_STATUSES)} entries; KNOWN_TIERS has {len(KNOWN_TIERS)}")

    # ── Step 2: fixture — org + publisher ──────────────────────────
    section("Step 2: fixture setup (publisher + org)")
    pub_id = insert_publisher(
        name="Smoke Test Star", market="Smoketown, MN", state="MN"
    )
    org_id = insert_organization(name="Smoke Auto Repair")
    ok(f"publisher_id={pub_id}, org_id={org_id}")

    # ── Step 3: subscription.created ───────────────────────────────
    section("Step 3: customer.subscription.created -> subscriptions row")
    evt = make_event(
        "customer.subscription.created",
        sub_id="sub_smoke_001", org_id=org_id, tier="growth", status="active",
    )
    summary = apply_event(evt)
    if summary.get("action") != "applied":
        fail(f"expected action=applied, got {summary}")
    ok(f"event applied: {summary}")
    sub = get_subscription_by_processor_id("sub_smoke_001")
    if not sub:
        fail("subscription row missing after .created")
    if sub["tier"] != "growth" or sub["status"] != "active":
        fail(f"sub row wrong: {sub}")
    ok(f"sub row: tier={sub['tier']} status={sub['status']}")

    # First-creation tier_history row should have from_tier=NULL (pre-existed
    # nothing) — apply_event only logs when prior_tier != tier; here prior was
    # None so we expect a row with from_tier=NULL.
    history = get_tier_history(org_id)
    if len(history) != 1:
        fail(f"expected 1 tier_history row, got {len(history)}: {history}")
    if history[0]["from_tier"] is not None or history[0]["to_tier"] != "growth":
        fail(f"tier_history wrong: {history[0]}")
    ok(f"tier_history seeded: NULL -> {history[0]['to_tier']}")

    # Step 4: subscription.updated -> tier change
    section("Step 4: customer.subscription.updated -> tier upgrade logged")
    evt2 = make_event(
        "customer.subscription.updated",
        sub_id="sub_smoke_001", org_id=org_id, tier="concierge", status="active",
    )
    summary = apply_event(evt2)
    if not summary.get("tier_changed"):
        fail(f"expected tier_changed=True, got {summary}")
    ok(f"tier change detected: {summary}")
    history = get_tier_history(org_id)
    if len(history) != 2:
        fail(f"expected 2 tier_history rows, got {len(history)}")
    last = history[-1]
    if last["from_tier"] != "growth" or last["to_tier"] != "concierge":
        fail(f"tier transition wrong: {last}")
    ok(f"tier_history: {last['from_tier']} -> {last['to_tier']}")

    # Re-applying the SAME tier should NOT add another history row
    evt2_again = make_event(
        "customer.subscription.updated",
        sub_id="sub_smoke_001", org_id=org_id, tier="concierge", status="active",
    )
    apply_event(evt2_again)
    history = get_tier_history(org_id)
    if len(history) != 2:
        fail(f"idempotency broken: history grew on no-op update: {len(history)}")
    ok("re-applying same tier did not duplicate tier_history (idempotent)")

    # ── Step 5: subscription.deleted ───────────────────────────────
    section("Step 5: customer.subscription.deleted -> status=canceled")
    evt3 = make_event(
        "customer.subscription.deleted",
        sub_id="sub_smoke_001", org_id=org_id, tier="concierge",
        status="canceled", canceled=True,
    )
    apply_event(evt3)
    sub = get_subscription_by_processor_id("sub_smoke_001")
    if sub["status"] != "canceled":
        fail(f"expected status=canceled, got {sub['status']}")
    if not sub["canceled_at"]:
        fail("canceled_at not set")
    ok(f"sub canceled at {sub['canceled_at']}")
    if get_active_subscription(org_id):
        fail("get_active_subscription returned a canceled sub")
    ok("get_active_subscription correctly returns None for canceled org")

    # ── Step 6: revenue_share window rotation ──────────────────────
    section("Step 6: open_revenue_share_window closes prior window")
    rs1 = open_revenue_share_window(
        organization_id=org_id, selling_publisher_id=pub_id,
        share_pct=0.50, attribution_source="invite", notes="Y1 share",
    )
    cur1 = get_current_revenue_share(org_id)
    if not cur1 or cur1["id"] != rs1 or cur1["share_pct"] != 0.50:
        fail(f"first window wrong: {cur1}")
    ok(f"Y1 window opened (id={rs1}, share=0.50)")

    rs2 = open_revenue_share_window(
        organization_id=org_id, selling_publisher_id=pub_id,
        share_pct=0.40, attribution_source="invite", notes="Y2 renewal share",
    )
    cur2 = get_current_revenue_share(org_id)
    if not cur2 or cur2["id"] != rs2 or cur2["share_pct"] != 0.40:
        fail(f"second window wrong: {cur2}")
    ok(f"Y2 window opened (id={rs2}, share=0.40)")

    history = get_revenue_share_history(org_id)
    if len(history) != 2:
        fail(f"expected 2 share rows, got {len(history)}")
    if history[0]["window_end"] is None:
        fail("first window's window_end was not closed when second opened")
    if history[1]["window_end"] is not None:
        fail("second window should be open (window_end IS NULL)")
    ok("prior window closed automatically; current window open")

    # Step 7: attribute_publisher_at_signup (invite-only policy)
    section("Step 7: attribute_publisher_at_signup honors invite-only policy")
    from src.modules.billing.attribution import attribute_publisher_at_signup
    from src.business_frontend.auth import create_invite, init_tables as init_biz

    init_biz()  # business_invites table

    # 7a: happy path — invite resolves to the seeded publisher
    code_good = create_invite(
        business_name="Smoke Auto",
        publisher="Smoke Test Star",  # matches the publisher we inserted in Step 2
        tier="growth",
    )
    pub_id_returned, source = attribute_publisher_at_signup(
        organization_id=org_id, invite_code=code_good, business_state="MN"
    )
    if pub_id_returned != pub_id or source != "invite":
        fail(f"expected ({pub_id}, 'invite'), got ({pub_id_returned}, {source!r})")
    ok(f"invite -> publisher_id={pub_id_returned}, source={source!r}")

    # 7b: missing invite raises
    try:
        attribute_publisher_at_signup(organization_id=org_id, invite_code=None)
        fail("expected ValueError for missing invite, got success")
    except ValueError as e:
        ok(f"missing invite correctly raised: {str(e)[:50]}...")

    # 7c: self_serve=True raises (reserved for Phase 1.5)
    try:
        attribute_publisher_at_signup(
            organization_id=org_id, invite_code=code_good, self_serve=True,
        )
        fail("expected ValueError for self_serve=True, got success")
    except ValueError as e:
        ok(f"self_serve=True correctly raised: {str(e)[:50]}...")

    # 7d: invite naming a non-existent publisher raises (Q2 policy: option A)
    code_bad = create_invite(
        business_name="Ghost Co",
        publisher="Publisher That Does Not Exist",
        tier="growth",
    )
    try:
        attribute_publisher_at_signup(
            organization_id=org_id, invite_code=code_bad, business_state="MN"
        )
        fail("expected ValueError for unknown publisher, got success")
    except ValueError as e:
        ok(f"unknown-publisher invite correctly raised: {str(e)[:50]}...")

    # Step 8a: build_checkout_session_params shape + validation
    section("Step 8a: build_checkout_session_params produces correct shape")
    from src.modules.billing.stripe_checkout import build_checkout_session_params

    # Stripe Price IDs aren't set in test env, so set them locally
    os.environ["STRIPE_PRICE_STARTER"] = "price_starter_test"
    os.environ["STRIPE_PRICE_GROWTH"] = "price_growth_test"
    os.environ["STRIPE_PRICE_CONCIERGE"] = "price_concierge_test"

    params = build_checkout_session_params(
        organization_id=42,
        tier="growth",
        customer_email="dale@westbrookauto.example",
        base_url="https://app.amplora.com/",
    )
    if params["mode"] != "subscription":
        fail(f"expected mode=subscription, got {params['mode']!r}")
    if params["line_items"][0]["price"] != "price_growth_test":
        fail(f"wrong price_id: {params['line_items'][0]}")
    if params["metadata"]["organization_id"] != "42":
        fail(f"metadata.organization_id missing: {params['metadata']}")
    if params["subscription_data"]["metadata"]["tier"] != "growth":
        fail(f"subscription_data.metadata.tier missing: {params['subscription_data']}")
    if "{CHECKOUT_SESSION_ID}" not in params["success_url"]:
        fail(f"success_url missing session_id placeholder: {params['success_url']}")
    if params["customer_email"] != "dale@westbrookauto.example":
        fail(f"customer_email not threaded through: {params}")
    ok("growth-tier params: subscription mode, correct price, metadata, success_url")

    # 8b: existing customer reuses cus_*
    params2 = build_checkout_session_params(
        organization_id=42, tier="concierge",
        customer_email="dale@westbrookauto.example",
        base_url="https://app.amplora.com",
        existing_customer_id="cus_existing_xyz",
    )
    if params2.get("customer") != "cus_existing_xyz":
        fail(f"existing customer_id not propagated: {params2}")
    if "customer_email" in params2:
        fail("customer_email should be omitted when customer is set")
    ok("existing customer_id reused; customer_email correctly omitted")

    # 8c: bad tier raises
    try:
        build_checkout_session_params(
            organization_id=1, tier="enterprise",
            customer_email="x@y.z", base_url="https://x",
        )
        fail("expected ValueError for unknown tier")
    except ValueError as e:
        ok(f"unknown tier rejected: {str(e)[:50]}...")

    # 8d: missing price env raises
    del os.environ["STRIPE_PRICE_GROWTH"]
    try:
        build_checkout_session_params(
            organization_id=1, tier="growth",
            customer_email="x@y.z", base_url="https://x",
        )
        fail("expected ValueError when STRIPE_PRICE_GROWTH unset")
    except ValueError as e:
        ok(f"missing STRIPE_PRICE_GROWTH rejected: {str(e)[:50]}...")
    os.environ["STRIPE_PRICE_GROWTH"] = "price_growth_test"  # restore

    # ── Step 8e: past-due policy is currently UNSET (Trevor's contribution slot)
    section("Step 8e: past-due policy is unset until Trevor fills it in")
    from src.modules.billing.policy import (
        assert_past_due_policy,
        is_past_due_policy_set,
    )
    if is_past_due_policy_set():
        fail("policy unexpectedly set already (test invariant)")
    try:
        assert_past_due_policy()
        fail("expected RuntimeError, got silence")
    except RuntimeError as e:
        ok(f"policy guard correctly raises: {str(e)[:50]}...")

    # ── Step 9: missing org_id metadata is a no-op (no crash) ──────
    section("Step 9: webhook handles missing organization_id metadata gracefully")
    bad = {
        "id": "evt_bad",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_orphan", "status": "active", "metadata": {},
            "items": {"data": [{"price": {"metadata": {"tier": "starter"}}}]},
        }},
    }
    summary = apply_event(bad)
    if summary.get("action") != "skipped_missing_org":
        fail(f"expected skipped_missing_org, got {summary}")
    ok("orphan webhook event correctly skipped")

    # ── Cleanup ────────────────────────────────────────────────────
    section("Cleanup")
    import shutil
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
    ok(f"removed tmp DB at {_TMP_DIR}")

    print(f"\n{GREEN}=== W1 smoke PASSED ==={RESET}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n{RED}=== W1 smoke FAILED: {e} ==={RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}=== W1 smoke CRASHED: {type(e).__name__}: {e} ==={RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
