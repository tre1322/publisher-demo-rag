"""Smoke test for W2.1 (PMC pipeline + interview-script + acceptance).

Exercises:
  1. interview_script: SCRIPT_VERSION + decision constants encoded;
     quantitative/qualitative split non-empty; topic_areas() renders
  2. pre_interview_brief() returns text mentioning target + cap minutes
  3. pmc.init_table creates product_marketing_contexts + pmc_interview_sessions
     (idempotent — second call is a no-op)
  4. pmc_db.create_session + complete_session_with_transcript lifecycle
  5. transcript_to_pmc.generate_pmc_from_transcript with a fake Anthropic
     client returns (markdown, metadata) shape
  6. transcript_to_pmc rejects empty transcripts
  7. create_pmc_draft inserts row with version=1, status='draft'
  8. update_pmc_draft on a draft works
  9. update_pmc_draft refuses on a non-draft (status='accepted')
 10. accept_pmc flips status -> 'accepted', records timestamp + user
 11. Re-running interview supersedes prior accepted PMC atomically
 12. get_canonical_pmc returns only the accepted PMC; never the draft or superseded
 13. The one-accepted-per-org invariant: unique index enforces it

Hermetic — uses a tmp DB, never touches data/articles.db.

Run: uv run python scripts/smoke_w2_pmc.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pre-import patch: redirect DATABASE_PATH to a tmp file BEFORE any module
# that touches the DB at import time gets imported.
_TMP_DIR = tempfile.mkdtemp(prefix="amplora_w2_smoke_")
_TMP_DB = Path(_TMP_DIR) / "articles.db"

import src.core.database as core_db  # noqa: E402

core_db.DATABASE_PATH = _TMP_DB

# Now safe to import everything else
from src.core.database import get_connection  # noqa: E402
from src.modules.organizations import database as orgs_db  # noqa: E402
from src.modules.organizations.database import insert_organization  # noqa: E402
from src.modules.pmc import database as pmc_db  # noqa: E402
from src.modules.pmc.database import (  # noqa: E402
    accept_pmc,
    complete_session_with_transcript,
    create_pmc_draft,
    create_session,
    get_canonical_pmc,
    get_pmc,
    get_session,
    list_pmc_versions,
    update_pmc_draft,
)
from src.modules.pmc.interview_script import (  # noqa: E402
    FORM_SECTIONS,
    INTERVIEW_LENGTH_CAP_MINUTES,
    INTERVIEW_TARGET_MINUTES,
    INTERVIEW_TONE,
    SCRIPT_VERSION,
    Category,
    pre_interview_brief,
    qualitative_questions,
    quantitative_by_section,
    quantitative_questions,
    topic_areas,
)
from src.modules.pmc.transcript_to_pmc import (  # noqa: E402
    GENERATOR_PROMPT_VERSION,
    STRATEGIC_SUMMARY_FIELDS,
    generate_pmc_from_transcript,
)


def init_w2_tables_only() -> None:
    """Init just the tables W2 needs (orgs + pmc).

    Skip full init_all_tables() — same fresh-DB import-time bug that
    smoke_w1_billing.py works around (advertisements/search.py:340).
    """
    orgs_db.init_table()
    pmc_db.init_table()


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


# ── Fake LLM client: deterministic markdown for hermetic smoke ─────


class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeContent(text)]


class FakeAnthropicClient:
    """Stand-in for anthropic.Anthropic. Returns a fixed markdown blob,
    captures the most recent call args for inspection."""

    def __init__(self, text: str):
        self._text = text
        self.messages = self
        self.last_call: dict | None = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeMessage(self._text)


_FAKE_PMC_MARKDOWN = """# Product Marketing Context

**Business:** Westbrook Auto
**Address:** 142 Main St, Westbrook, MN
**Hours:** Mon-Fri 7-6, Sat 8-12

## origin_story
*"I started this in '02 after twenty years at the dealership."* Owner Dale Whittle wanted to be his own boss.
AGENT NOTE: write in first person; Dale uses "I" and "we" interchangeably.

## ideal_customer
Local farmers and commuters. *"They show up, they want it done right, they don't want a song and dance."*
AGENT NOTE: avoid corporate phrasing; owner is allergic to upsells.

## differentiation
Honest pricing, no upsells. ASE certified. Farm service calls since 2014.
AGENT NOTE: lead with honesty + speed in posts.
"""


def main() -> int:
    print(f"Tmp DB: {_TMP_DB}")

    # ── Step 1: interview script encoded decisions ────────────────
    section("Step 1: interview_script encodes decisions 2 + 3")

    if INTERVIEW_TONE != "warm-personal":
        fail(f"INTERVIEW_TONE expected 'warm-personal', got {INTERVIEW_TONE!r}")
    ok("INTERVIEW_TONE = 'warm-personal' (Decision 2)")

    if INTERVIEW_TARGET_MINUTES > INTERVIEW_LENGTH_CAP_MINUTES:
        fail("target minutes greater than cap — that's wrong")
    ok(f"target {INTERVIEW_TARGET_MINUTES}m, cap {INTERVIEW_LENGTH_CAP_MINUTES}m (Decision 3 adaptive)")

    if SCRIPT_VERSION != "1.3.0":
        fail(f"SCRIPT_VERSION expected '1.3.0', got {SCRIPT_VERSION!r}")
    ok(f"SCRIPT_VERSION = {SCRIPT_VERSION!r}")

    quant = quantitative_questions()
    qual = qualitative_questions()
    if not quant:
        fail("no quantitative questions in script")
    if not qual:
        fail("no qualitative questions in script")
    if any(q.quantitative for q in qual):
        fail("qualitative_questions() leaked a quantitative=True row")
    ok(f"split: {len(quant)} quantitative + {len(qual)} qualitative questions")

    keys_seen: set[str] = set()
    for q in quant + qual:
        if q.key in keys_seen:
            fail(f"duplicate question key: {q.key}")
        keys_seen.add(q.key)
    ok(f"all {len(keys_seen)} question keys unique")

    cats_in_script = {q.category for q in qual}
    if Category.IDENTITY not in cats_in_script or Category.DIFFERENTIATION not in cats_in_script:
        fail("script missing IDENTITY or DIFFERENTIATION category — those drive the marketing voice")
    ok(f"qualitative covers {len(cats_in_script)} categories including IDENTITY + DIFFERENTIATION")

    # v1.2.0 must-have additions
    qual_keys = {q.key for q in qual}
    required_v12 = {
        "anti_customer", "offer_boundaries", "priority_services",
        "profitability_direction", "capacity_and_bottlenecks",
        "marketing_history", "lead_handling", "marketing_success_definition",
        "proof_and_credibility", "brand_guardrails",
        "differentiation_and_positioning", "switching_incentive_and_lead_magnet",
    }
    missing_q = required_v12 - qual_keys
    if missing_q:
        fail(f"v1.2.0 voice questions missing: {missing_q}")
    ok("all 12 v1.2.0 voice questions present (incl. anti_customer + offer_boundaries)")

    quant_keys = {q.key for q in quant}
    required_form = {
        "service_area", "website_url", "google_business_profile",
        "facebook_handle", "instagram_handle", "preferred_contact_methods",
        "priority_services_quick", "current_offers", "financing_options",
        "top_3_competitors", "current_marketing_spend",
        "marketing_history_quick", "owner_channel_comfort",
        "marketing_decision_authority", "photos_and_assets",
        "marketing_permissions",
    }
    missing_f = required_form - quant_keys
    if missing_f:
        fail(f"v1.2.0 form fields missing: {missing_f}")
    ok("all 16 v1.2.0 form fields present (incl. competitors + spend + comfort)")

    # Form sectioning — every quantitative question must declare a form_section
    # that's in FORM_SECTIONS, otherwise the template falls into "Other".
    for q in quant:
        if q.form_section not in FORM_SECTIONS:
            fail(f"form question {q.key!r} has form_section={q.form_section!r}, expected one of FORM_SECTIONS")
    ok(f"every form field maps to one of {len(FORM_SECTIONS)} sections")

    sections = quantitative_by_section()
    section_titles = [s for s, _ in sections]
    if section_titles != FORM_SECTIONS:
        fail(f"section render order wrong: {section_titles} vs {FORM_SECTIONS}")
    ok(f"sections render in declared order: {section_titles}")

    # Voice questions never declare form_section
    for q in qual:
        if q.form_section is not None:
            fail(f"voice question {q.key!r} accidentally has form_section={q.form_section!r}")
    ok("voice questions don't leak form_section")

    # weight=3 must-cover floor — agent never drops these even when running long
    weight_3_count = sum(1 for q in qual if q.weight == 3)
    if weight_3_count < 12:
        fail(f"only {weight_3_count} weight=3 voice questions; expected ≥12 for plan-decision coverage")
    ok(f"{weight_3_count} weight=3 voice questions (must-cover floor)")

    # ── Step 2: pre_interview_brief renders ───────────────────────
    section("Step 2: pre_interview_brief renders the owner-facing brief")

    brief = pre_interview_brief()
    if str(INTERVIEW_TARGET_MINUTES) not in brief:
        fail("brief doesn't mention target minutes")
    if str(INTERVIEW_LENGTH_CAP_MINUTES) not in brief:
        fail("brief doesn't mention cap minutes")
    ok("brief mentions both target and cap minutes")

    topics = topic_areas()
    if not topics:
        fail("topic_areas() returned empty")
    ok(f"topic_areas() = {topics}")

    # ── Step 3: schema init ───────────────────────────────────────
    section("Step 3: pmc.init_table creates W2 tables on a fresh DB")

    init_w2_tables_only()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}
    conn.close()

    for t in ("product_marketing_contexts", "pmc_interview_sessions"):
        if t not in tables:
            fail(f"missing table: {t}")
        ok(f"table exists: {t}")

    # Idempotent: a second call must not raise
    pmc_db.init_table()
    ok("init_table() is idempotent (second call OK)")

    # ── Step 4: org seed ──────────────────────────────────────────
    section("Step 4: seed an org for the rest of the smoke")

    org_id = insert_organization("Westbrook Auto")
    if not org_id:
        fail("insert_organization returned no id")
    ok(f"org seeded: id={org_id}")

    # ── Step 5: session lifecycle ─────────────────────────────────
    section("Step 5: pmc_interview_sessions lifecycle")

    session_id = create_session(org_id, voice_provider="manual_paste")
    if not session_id:
        fail("create_session returned no id")
    s = get_session(session_id)
    if s is None or s["status"] != "scheduled":
        fail(f"session status expected 'scheduled', got {s and s['status']!r}")
    ok(f"session {session_id} created with status='scheduled'")

    transcript = "Q: Tell me about the business.\nA: We've been here 22 years..."
    complete_session_with_transcript(session_id, transcript, duration_seconds=2100)
    s = get_session(session_id)
    if s["status"] != "transcript_pasted":
        fail(f"session status expected 'transcript_pasted', got {s['status']!r}")
    if s["transcript_text"] != transcript:
        fail("transcript_text mismatch on session")
    ok("complete_session_with_transcript flips status + persists transcript")

    try:
        create_session(org_id, voice_provider="bogus")
        fail("create_session accepted bogus voice_provider")
    except ValueError as e:
        if "voice_provider" not in str(e):
            fail(f"unexpected error message: {e}")
        ok(f"unknown voice_provider rejected: {e}")

    # ── Step 6: transcript -> PMC pipeline ────────────────────────
    section("Step 6: generate_pmc_from_transcript with a fake LLM client")

    fake = FakeAnthropicClient(_FAKE_PMC_MARKDOWN)
    quantitative = {
        "business_name": "Westbrook Auto",
        "address": "142 Main St, Westbrook, MN",
        "hours": "Mon-Fri 7-6, Sat 8-12",
        "services_and_prices": "Oil change $45, brake job $200-400, ...",
        "payment_methods": "Cash, check, card",
        "years_in_business": "22",
    }
    qualitative_md, meta = generate_pmc_from_transcript(
        quantitative, transcript, _client=fake
    )
    if "Westbrook" not in qualitative_md:
        fail("LLM output missing expected business name")
    if meta["script_version"] != SCRIPT_VERSION:
        fail(f"meta.script_version mismatch: {meta['script_version']} vs {SCRIPT_VERSION}")
    if meta["prompt_version"] != GENERATOR_PROMPT_VERSION:
        fail("meta.prompt_version mismatch")
    ok("generate_pmc_from_transcript returns (markdown, meta) with version info")

    # The fake captured the actual prompt — verify the script was materialized
    prompt_text = fake.last_call["messages"][0]["content"]
    if "origin_story" not in prompt_text:
        fail("script not materialized into prompt — origin_story key missing")
    ok("interview script materialized into the LLM prompt verbatim")

    # v2 prompt requires STRATEGIC SUMMARY block
    if "STRATEGIC SUMMARY" not in prompt_text:
        fail("v2 prompt missing STRATEGIC SUMMARY block instruction")
    for f in STRATEGIC_SUMMARY_FIELDS:
        if f not in prompt_text:
            fail(f"STRATEGIC SUMMARY field {f!r} not in prompt")
    ok(f"v2 prompt instructs all {len(STRATEGIC_SUMMARY_FIELDS)} STRATEGIC SUMMARY fields")

    if GENERATOR_PROMPT_VERSION != "v4":
        fail(f"GENERATOR_PROMPT_VERSION expected 'v4', got {GENERATOR_PROMPT_VERSION!r}")
    ok(f"GENERATOR_PROMPT_VERSION = {GENERATOR_PROMPT_VERSION!r}")

    # ── Step 7: empty transcript rejected ─────────────────────────
    section("Step 7: empty transcript rejected")

    try:
        generate_pmc_from_transcript({}, "", _client=fake)
        fail("empty transcript accepted; expected ValueError")
    except ValueError as e:
        ok(f"empty transcript correctly raised: {e}")

    # ── Step 8: create_pmc_draft ──────────────────────────────────
    section("Step 8: create_pmc_draft inserts a draft row")

    pmc_id = create_pmc_draft(
        organization_id=org_id,
        qualitative_md=qualitative_md,
        quantitative=quantitative,
        transcript_text=transcript,
        interview_session_id=session_id,
        generator_model=meta["model"],
        generator_prompt_version=meta["prompt_version"],
        script_version=meta["script_version"],
        created_by_user_id=99,
    )
    pmc = get_pmc(pmc_id)
    if pmc is None:
        fail("PMC not found after create")
    if pmc["version"] != 1:
        fail(f"first PMC for org should be version 1, got {pmc['version']}")
    if pmc["status"] != "draft":
        fail(f"PMC should start as draft, got {pmc['status']!r}")
    if pmc["quantitative"]["business_name"] != "Westbrook Auto":
        fail("quantitative_json round-trip failed")
    ok(f"draft PMC {pmc_id} created with version=1, status='draft'")

    # ── Step 9: update draft ──────────────────────────────────────
    section("Step 9: update_pmc_draft on a draft is allowed")

    update_pmc_draft(pmc_id, qualitative_md=qualitative_md + "\n\n## anything_else\nAdded by owner.\n")
    pmc = get_pmc(pmc_id)
    if "Added by owner" not in pmc["qualitative_md"]:
        fail("update did not persist")
    ok("draft is editable")

    # ── Step 10: accept ───────────────────────────────────────────
    section("Step 10: accept_pmc flips status atomically")

    accept_pmc(pmc_id, user_id=99)
    pmc = get_pmc(pmc_id)
    if pmc["status"] != "accepted":
        fail(f"expected status=accepted, got {pmc['status']!r}")
    if not pmc["accepted_at"]:
        fail("accepted_at timestamp not set")
    if pmc["accepted_by_user_id"] != 99:
        fail("accepted_by_user_id not set")
    ok(f"PMC {pmc_id} now accepted")

    canonical = get_canonical_pmc(org_id)
    if canonical is None or canonical["id"] != pmc_id:
        fail("get_canonical_pmc didn't return the just-accepted PMC")
    ok("get_canonical_pmc returns the accepted PMC")

    # ── Step 11: cannot edit accepted ─────────────────────────────
    section("Step 11: update_pmc_draft refuses to touch a non-draft")

    try:
        update_pmc_draft(pmc_id, qualitative_md="hostile rewrite")
        fail("update_pmc_draft accepted an edit to an accepted row")
    except ValueError as e:
        if "draft" not in str(e).lower():
            fail(f"unexpected error: {e}")
        ok(f"non-draft edit correctly rejected: {e}")

    # ── Step 12: cannot accept twice ──────────────────────────────
    section("Step 12: accept_pmc refuses to re-accept an already-accepted PMC")

    try:
        accept_pmc(pmc_id, user_id=99)
        fail("re-accepting an accepted PMC was allowed")
    except ValueError as e:
        ok(f"double-accept rejected: {e}")

    # ── Step 13: re-running interview supersedes ──────────────────
    section("Step 13: re-running the interview supersedes the prior canonical")

    session_id_2 = create_session(org_id)
    complete_session_with_transcript(session_id_2, transcript + "\n\n(updated)")
    qualitative_md_2, meta_2 = generate_pmc_from_transcript(
        quantitative, transcript + "\n\n(updated)", _client=fake
    )
    pmc_id_2 = create_pmc_draft(
        organization_id=org_id,
        qualitative_md=qualitative_md_2,
        quantitative=quantitative,
        transcript_text=transcript + "\n\n(updated)",
        interview_session_id=session_id_2,
        generator_model=meta_2["model"],
        generator_prompt_version=meta_2["prompt_version"],
        script_version=meta_2["script_version"],
    )
    pmc_v2 = get_pmc(pmc_id_2)
    if pmc_v2["version"] != 2:
        fail(f"second PMC for org should be version 2, got {pmc_v2['version']}")
    if pmc_v2["status"] != "draft":
        fail("second PMC should start as draft")
    ok(f"new draft PMC {pmc_id_2} version=2 (canonical {pmc_id} still accepted)")

    accept_pmc(pmc_id_2, user_id=99)
    canonical = get_canonical_pmc(org_id)
    if canonical["id"] != pmc_id_2:
        fail("after acceptance v2, canonical should be v2")
    pmc_v1_after = get_pmc(pmc_id)
    if pmc_v1_after["status"] != "superseded":
        fail(f"v1 should be 'superseded', got {pmc_v1_after['status']!r}")
    if not pmc_v1_after["superseded_at"]:
        fail("superseded_at timestamp not set on v1")
    ok("v1 -> superseded; v2 -> accepted; canonical = v2")

    # ── Step 14: invariant — exactly one accepted PMC per org ─────
    section("Step 14: unique-index invariant: at most one accepted PMC per org")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) c FROM product_marketing_contexts "
        "WHERE organization_id=? AND status='accepted'",
        (org_id,),
    )
    n_accepted = cursor.fetchone()["c"]
    conn.close()
    if n_accepted != 1:
        fail(f"expected exactly 1 accepted PMC, found {n_accepted}")
    ok(f"exactly {n_accepted} accepted PMC for org {org_id}")

    # ── Step 15: list_pmc_versions ────────────────────────────────
    section("Step 15: list_pmc_versions returns history newest-first")

    versions = list_pmc_versions(org_id)
    if [v["version"] for v in versions] != [2, 1]:
        fail(f"expected versions [2, 1], got {[v['version'] for v in versions]}")
    if versions[0]["status"] != "accepted" or versions[1]["status"] != "superseded":
        fail("status order wrong")
    ok("history: v2 accepted, v1 superseded")

    # ── Cleanup ───────────────────────────────────────────────────
    section("Cleanup")

    import shutil

    shutil.rmtree(_TMP_DIR, ignore_errors=True)
    ok(f"removed tmp DB at {_TMP_DIR}")

    print(f"\n{GREEN}=== W2 smoke PASSED ==={RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
