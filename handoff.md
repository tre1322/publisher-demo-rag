# Amplora — Phase 1 handoff (next session entry point)

**Last touched:** 2026-05-11
**Branch:** `claude/vibrant-wing-11040d` (W2 branch, built on top of W1)
**Worktree:** `C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d`
**Plan file:** `~/.claude/plans/for-project-amplora-we-cozy-corbato.md`

---

## TL;DR

W1 (billing) + W2.1 PMC text pipeline shipped, then hardened from a real-LLM click-through (Westbrook Auto, 2026-05-11) into script v1.3.0 + prompt v4. Two iteration commits encoded a real prompt-engineering pattern: **ASK the owner when they're reliable narrators (services), OBSERVE the transcript when they're systematically biased (voice).** PMC contract for downstream agents is now 14 strategic fields, internally consistent. Local app scoped to Amplora-only.

**Next move:** W3 Marketing Plan generator. The STRATEGIC SUMMARY contract is now mature enough to read from. ~1-2 days per the plan.

---

## Commits on this branch (newest first)

| Commit | What | Lines |
|---|---|---|
| `ac94c5b` | PMC v4: VOICE field + observe-not-ask rule for owner self-description bias | +37 / -3 |
| `d819f51` | PMC v3: MAINTAIN bucket + owner-driven AMPLIFY/MAINTAIN/MUTE classification | +60 / -14 |
| `756018d` | Scope down to Amplora-only (RAG/ingestion/news removed) | -37,897 net |
| `4bcf546` | W2.1 PMC pipeline (interview script v1.2.0, prompt v2) — **superseded by v3+v4** | +2,224 |
| `cf42f41` | W1 multi-tenant billing foundation | +2,195 |

Plus an uncommitted dotenv `override=True` fix in `src/core/config.py:9` (will land with this handoff update). `bfc1ea2` = master tip we branched from.

---

## How to boot + click through

```powershell
cd C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d
uv run python src/chatbot.py
```

App boots in ~1s, binds `0.0.0.0:8080`. `.env` must have `ANTHROPIC_API_KEY` for live PMC generation; the rest is optional.

**Click path:**

1. `http://localhost:8080/admin/cottonwood/main-street` → basic auth `admin` / `admin` → publisher-scoped admin.
2. Create invite (Westbrook Auto, tier `growth`) → copy the invite link.
3. Open invite link in **incognito** → register a business (any email + password).
4. After register, lands on `/business/pmc/` — the Marketing Profile page.
5. Fill the 23-field form (5 sections), paste a transcript at the bottom, submit.
6. PMC draft renders → review/edit → accept.

For the admin URL with Pipestone instead: `/admin/pipestone/main-street`.

---

## What's working (verified)

- ✅ W1 + W2 smoke tests green (19/19 + 38/38) across v2 → v3 → v4
- ✅ `ruff check` clean
- ✅ App boots, all surviving routes return correct codes
- ✅ End-to-end click-through (admin invite → register → PMC page)
- ✅ Form renders 23 fields grouped into 5 sections
- ✅ PMC schema invariants (one accepted per org, supersession atomic)
- ✅ **Real-LLM click-through verified** (Westbrook Auto mock transcript through v4) — 4 PMC artifacts saved to `C:\Users\trevo\AppData\Local\Temp\westbrook_pmc_*.md`
- ✅ **Decisiveness on AMPLIFY/MAINTAIN/MUTE** — v3 owner-classification working
- ✅ **Voice gap detection** — v4 catches owner self-description vs. transcript evidence mismatches
- ✅ **[NEEDS REVIEW] calibration** — fires on silence (e.g. seasonality skipped by agent) without fabrication
- ✅ **Dotenv override** — `load_dotenv(override=True)` fixes the silent-shell-leak failure mode

---

## What's open

### 1. (MEDIUM) CHANNEL PRIORITY field still hedges

v3 + v4 testing observed CHANNEL PRIORITY producing soft synthesis like "Facebook unclear ROI but maintained." This is the same failure mode v3 fixed for services — owners can sort their channels into the same three piles (more spend / fine where they are / cut). Likely fix: add a three-way channel question to the interview script (mirror the `priority_services` pattern) + use owner's classification in the prompt. ~1 hour of work; can be done before W3 starts so W3 inherits a sturdier CHANNEL PRIORITY field.

### 2. (LOW) W1 past-due policy constants still None

`src/modules/billing/policy.py:32, 43` — `PAST_DUE_GRACE_DAYS` and `PAST_DUE_DOWNGRADE_MODE` are `None`. Until set, `assert_past_due_policy()` raises (blocks any cron sweeper). Webhook itself unaffected.

Recommended defaults: `7` + `"freeze"`. ~2-minute fix.

### 3. (LOW) Manual Stripe test-mode click-through never run

Full W1 round-trip with real Stripe test keys. Documented in W1 ship log at `~/.claude/projects/C--Users-trevo-publisher-demo-rag/memory/project_amplora_w1_shipped.md`. ~10 min if Stripe test account is ready.

---

## What to do next (priority order)

1. **W3 — Marketing plan generator.** Reads STRATEGIC SUMMARY (now 14 fields, owner-validated where it matters) → produces Marketing Plan view (audience, value prop, channel goals, monthly themes, switching incentives). 1-2 days. **This is the next big workstream.** When designing W3 prompts, apply the ask-vs-observe heuristic from `feedback_amplora_prompt_patterns.md` per field.
2. **CHANNEL PRIORITY owner-classification.** Apply the v3 priority_services pattern to channel mix. ~1 hour; do this before W3 starts so the synthesis field W3 reads is decisive.
3. **W2.2 — Voice integration** (Twilio or LiveKit). Best after #1 so we know what the plan consumes.
4. Past-due policy constants (`policy.py`).
5. W4 — Approval queue + scheduler (depends on W3).

---

## Critical files / locations

**Plan + strategy:**
- `~/.claude/plans/for-project-amplora-we-cozy-corbato.md` — canonical Phase 1 plan
- `popular-network-demo/docs/amplora_business_plan.md`
- `popular-network-demo/docs/amplora_phases.md`
- `popular-network-demo/docs/amplora_partner_brief.md`
- `popular-network-demo/docs/amplora_pitch_script.md`

**W1 (billing):**
- `src/modules/billing/database.py` — schema + CRUD
- `src/modules/billing/stripe_webhook.py` — webhook
- `src/modules/billing/stripe_checkout.py` — checkout session
- `src/modules/billing/attribution.py` — invite-only attribution
- `src/modules/billing/policy.py` — **past-due policy: Trevor's open slot**
- `scripts/smoke_w1_billing.py` — 19 hermetic assertions

**W2.1 (PMC):**
- `src/modules/pmc/interview_script.py` — **interview script v1.2.0** (21 voice + 23 form questions, 5 form sections, 8-decision plan framework). Trevor's CMO contributions encoded.
- `src/modules/pmc/transcript_to_pmc.py` — prompt v2 with STRATEGIC SUMMARY block (12 fields)
- `src/modules/pmc/database.py` — schema + CRUD + supersession state machine
- `src/business_frontend/templates/pmc_prep.html` — 5-section form + transcript paste
- `src/business_frontend/templates/pmc_review.html` — review/edit/accept
- `scripts/smoke_w2_pmc.py` — 38 hermetic assertions

**App entry + routing:**
- `src/chatbot.py` — FastAPI app (93 lines, Amplora-only)
- `src/core/database.py` — `init_all_tables` (Amplora tables only)
- `src/admin_frontend/routes.py` — invite creation + billing audit
- `src/business_frontend/routes.py` — login/register/billing/pmc/settings
- `src/business_frontend/templates/base.html` — 3-item nav (Marketing Profile / Billing / Settings)

---

## Ground rules (Trevor's non-negotiables — from global CLAUDE.md)

- **Test before handoff.** Smoke + real path before declaring done. `python -c 'import x'` is not testing.
- **Verify the user hits the file I edited.** Multi-host deploys / env-var defaults / filename mismatch can route around your edit.
- **Disabled-visible > hidden-until-ready** for UI chrome. Failures should be diagnosable, not invisible.

---

## Decisions encoded (don't relitigate without cause)

- **Decision 1 (billing processor):** Stripe.
- **Decision 2 (interview tone):** warm-personal.
- **Decision 3 (interview length cap):** adaptive, 35m target, 60m hard cap.
- **Tier names:** `starter` / `growth` / `concierge`. Stripe Price `metadata.tier` MUST match.
- **Attribution policy v1:** invite-only. Self-serve raises ValueError. Mismatch raises.
- **Geography enforcement:** deferred to future `publisher_county_licenses` schema.
- **Pre-interview prep:** required (Trevor 2026-05-09 — "we will definitely let them know them before they start").
- **PMC has two layers:** STRATEGIC SUMMARY (14 fields as of v4, decisive synthesis) + question-by-question sections (with AGENT NOTE on each). Summary is the contract with W3 plan generator.
- **PMC field synthesis pattern is per-field ASK vs OBSERVE** (v3 + v4, 2026-05-11). Owners reliable narrators on services → ASK (priority_services classification used verbatim). Owners systematically biased on voice → OBSERVE (transcript evidence wins; gap flagged in AGENT NOTE). See `feedback_amplora_prompt_patterns.md` for the cheat sheet — apply to every new W3+ prompt that synthesizes from interview text.

---

## How to verify the world hasn't rotted (5 minute check)

```bash
# Smoke
uv run python scripts/smoke_w1_billing.py     # expect 19/19 PASSED
uv run python scripts/smoke_w2_pmc.py         # expect 38/38 PASSED

# Lint
uv run ruff check src/ scripts/               # expect: All checks passed

# Boot
uv run python src/chatbot.py                  # expect: Uvicorn on 0.0.0.0:8080 in ~1s

# Hit the routes
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/                 # 303
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/business/login   # 200
curl -s -o /dev/null -w "%{http_code}\n" -u admin:admin http://localhost:8080/admin/main-street  # 200
```

---

## Memory entries that document this work

- `project_amplora.md` — strategic context (Amplora rebrand, three-product platform)
- `project_amplora_implementation_plan.md` — Phase 1 plan + workstream status table
- `project_amplora_w1_shipped.md` — W1 ship log
- `project_amplora_w2_1_shipped.md` — W2.1 ship log
- `project_amplora_scope_reduction.md` — this latest commit (756018d)

Read those before assuming things, especially the W2.1 entry — it documents the interview script v1.2.0 design decisions in full (8-decision plan framework, anti-customer phrasing, offer_boundaries, STRATEGIC SUMMARY structure).
