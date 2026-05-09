# Amplora W1 → W2 Handoff

**Branch / worktree:** `zealous-ride-704e86`
**As of:** 2026-05-09
**Plan reference:** `~/.claude/plans/for-project-amplora-we-cozy-corbato.md`

---

## TL;DR

W1 (multi-tenant foundation) is shipped and smoke-green. Two small finishing items remain (10-line policy decision + 10-minute manual Stripe click-through). W2 (voice interview agent) is the next workstream and is unblocked except for two design decisions Trevor still needs to make.

---

## What's done (W1)

The multi-tenant billing foundation. Three new SQLite tables, a Stripe webhook, an invite-only attribution policy, business-facing Stripe Checkout, and an admin billing audit view.

### Code (the file map)

```
src/modules/billing/
├── __init__.py                 # module marker
├── database.py                 # subscriptions / tier_history / publisher_revenue_share + CRUD
├── attribution.py              # attribute_publisher_at_signup() — invite-only policy
├── stripe_webhook.py           # /webhooks/stripe + testable apply_event(dict)
├── stripe_checkout.py          # build_checkout_session_params() (pure) + create_checkout_session()
└── policy.py                   # PAST_DUE_GRACE_DAYS / PAST_DUE_DOWNGRADE_MODE — TREVOR TO FILL

src/business_frontend/
├── routes.py                   # +/billing, /billing/checkout, /billing/success, /billing/cancel
└── templates/
    ├── base.html               # + Billing nav item
    ├── billing.html            # tier picker + current state + history
    └── billing_success.html    # auto-refreshes only while sub is None (race-tolerant)

src/admin_frontend/
├── routes.py                   # +/admin/billing/{org_id} (HTML) + /admin/api/billing/{org_id} (JSON)
└── templates/
    └── billing_detail.html     # full per-org audit view

scripts/
└── smoke_w1_billing.py         # 19 assertions, 9 sections, hermetic tmp DB
```

Touched but not new: `src/core/database.py` (init wiring), `src/chatbot.py` (router mount), `pyproject.toml` (`stripe>=11.0.0`), `.env.example` (5 new env vars).

### Decisions encoded in the code

| Decision | Choice | Where |
|---|---|---|
| Billing processor | **Stripe** | `pyproject.toml`, `stripe_webhook.py`, `stripe_checkout.py` |
| Tier names | `starter` / `growth` / `concierge` | `billing/database.py:KNOWN_TIERS`, Stripe Price metadata.tier MUST match |
| Attribution policy | invite-only; mismatch raises | `billing/attribution.py:75-138` |
| Geography enforcement | deferred to county-licensing | TODO block in `billing/attribution.py:99-128` |
| Past-due policy | **NOT YET** — see open items | `billing/policy.py:32, 43` |

### How to run the smoke test

```
uv run python scripts/smoke_w1_billing.py
```

Hermetic — uses a tmp DB, never touches `data/articles.db`. Should print `=== W1 smoke PASSED ===` after 19 green assertions across 9 sections.

### How to run the live app

```
uv run python src/chatbot.py
```

Boots at `http://localhost:7860`. The 7 W1 routes register automatically; you'll see `Stripe webhook mounted at /webhooks/stripe` in the logs. Without `STRIPE_*` env vars set, the webhook returns 503 (intentional) but the app boots fine.

---

## Open items on W1 (NOT blocking W2)

### 1. Trevor's contribution: past-due policy constants

**File:** `src/modules/billing/policy.py`
**Lines:** 32 (`PAST_DUE_GRACE_DAYS`) and 43 (`PAST_DUE_DOWNGRADE_MODE`)

```python
PAST_DUE_GRACE_DAYS: int | None = None       # set me
PAST_DUE_DOWNGRADE_MODE: str | None = None   # one of: "freeze" | "pause" | "cancel"
```

**Recommended defaults** (if you don't want to think about it now): `7` + `"freeze"`.

**Why it matters:** when a Stripe webhook reports `invoice.payment_failed`, the subscription rolls to `past_due`. The grace period decides how many days the business keeps service before we downgrade. The mode decides what "downgrade" means:
- `"freeze"` — stop drafting new posts, keep existing content + chatbot answers visible (network-friendly)
- `"pause"` — yank the business from chatbot answers entirely (revenue-hard, network-hostile)
- `"cancel"` — flip status to canceled (harshest, same UX as deletion)

**Until set:** `assert_past_due_policy()` raises `RuntimeError`, blocking any future cron/sweeper that acts on past-due subs. Webhook itself is unaffected.

### 2. Manual Stripe test-mode click-through

The smoke test verifies the parameter shape and webhook event handling, but nothing has yet exercised a real Stripe round-trip. To close the loop:

1. Create a Stripe **test-mode** account (or use an existing one) and get `sk_test_*`.
2. Create 3 recurring Prices in Stripe (for $99, $299, $499 — amounts arbitrary in test mode) and set `metadata.tier` on each to `starter`, `growth`, `concierge` respectively.
3. Set in `.env`:
   ```
   STRIPE_API_KEY=sk_test_...
   STRIPE_PRICE_STARTER=price_...
   STRIPE_PRICE_GROWTH=price_...
   STRIPE_PRICE_CONCIERGE=price_...
   ```
4. Run `stripe listen --forward-to localhost:7860/webhooks/stripe` in a side terminal — it prints a `whsec_*` for `STRIPE_WEBHOOK_SECRET`.
5. Boot the app: `uv run python src/chatbot.py`
6. Generate an invite: visit `/admin` (basic-auth `admin`/`$ADMIN_PASSWORD`) → Main Street OS section → create an invite under `Cottonwood County Citizen` or `Pipestone Star`.
7. Open the invite URL in an incognito window: `http://localhost:7860/business/register?invite=<code>` — register a test business.
8. After registration, visit `/business/billing` → click **Select** on the Growth tier.
9. Complete Stripe Checkout with `4242 4242 4242 4242` (test card).
10. Watch the `stripe listen` terminal — you should see `customer.subscription.created` + `invoice.payment_succeeded` forward to the webhook.
11. Refresh `/business/billing` — the page should show `Tier: Growth, Status: Active`.
12. Verify in admin: `/admin/billing/<org_id>` should show the subscription row, the revenue_share window, and the tier_history row(s).

**Estimated time:** 10 minutes if you have a Stripe test account ready, 20-30 if you have to create one.

---

## Latent bug found and queued

`src/modules/advertisements/search.py:340` — `AD_TOOLS_SCHEMA = get_ad_tools_schema()` runs at *import time* and queries the `advertisements` table. This crashes `init_all_tables()` on a truly empty DB because the import side-effect fires before `ads_db.init_table()`. Production has always had the table from prior deploys, masking it.

The W1 smoke worked around it by calling `orgs / publishers / billing` `init_table()` directly. A separate task chip is queued to fix via lazy initialization (defer schema computation until first use, or `try/except OperationalError` and return `[]`).

This is **not** a W1 regression — it's been latent the whole time. Just a heads-up that fresh-DB bootstrap currently requires this workaround.

---

## Next: W2 — Voice interview agent

### Goal

A 30-45 minute AI voice call produces the canonical *Product Marketing Context* (`pmc.md`) for each business. Every downstream agent (plan generator, content drafter, GBP manager, review responder) reads from `pmc.md`. Get it right, the system handles 5 customers the same way it handles 500.

### Scope (per the plan)

- Inbound voice call (Twilio or LiveKit) → streaming transcription → conversational LLM agent following an interview script
- Output: structured markdown file `pmc.md` per business — hours, services, prices, voice/tone, switching incentives, photos, owner story, target customers, geographic territory
- Owner reviews the draft in the dashboard, edits inline, accepts → file becomes canonical input

### Decisions you still need to make (blockers)

- **Decision 2 — Interview tone**: warm-personal ("Tell me how you got into the business — I want to get this right") or efficient-professional ("I have 12 questions about your operation; this should take 35 minutes")? Affects script + voice model choice.
- **Decision 3 — Interview length cap**: hard 45 min (forces brevity, frustrates verbose owners) or adaptive (capped at 60 min, agent narrates its own pacing)?

### Things W2 inherits from W1 (zero re-work)

- The `organizations` row is already created at registration (Main Street OS register flow). W2 reads it, calls the business owner, writes `pmc.md`.
- No schema dependency. W2 will likely add a `pmc_drafts` table or filesystem path; that's a W2 internal design decision.
- The auth + session cookie story is solved — W2's review-and-edit UI just needs `Depends(require_auth)` like the existing `/business/*` routes.

### Risk register copied from the plan

1. **Interview brittleness** — if the agent loses the thread, `pmc.md` is garbage and every downstream agent inherits it. Mitigation: scripted skeleton + LLM flexibility, NOT free-form LLM. Owner-review gate before `pmc.md` is canonical.
2. **Voice provider lock-in** — Twilio vs LiveKit pick. Twilio is the safer "boring" pick; LiveKit is faster and more modern. Probably want to prototype with whichever is faster to spin up.
3. **Cost per interview** — if streaming transcription + LLM round-trips run hot, a 45-minute call could be $5-10. At 5 pilot customers that's noise; at 500 customers it's $5k/mo and worth right-sizing.

---

## Other open W1 items (not blocking)

- Stripe Customer Portal integration — when a business needs to update their card or download invoices, they should hit `https://billing.stripe.com/p/login/...` rather than building it in-app. ~30 min add when you want it.
- Quarterly settlement script — there's no automation yet for "generate the publisher payout CSV at end of quarter." `/admin/api/billing/{org_id}` returns the JSON each org needs; a 50-line script in `scripts/generate_settlement_csv.py` would aggregate it. Q3 2026 problem.
- Past-due sweeper cron — once Trevor sets `policy.py`, write a cron job that selects past_due subs older than `PAST_DUE_GRACE_DAYS` and applies the chosen `PAST_DUE_DOWNGRADE_MODE`. Until then, intentionally absent.

---

## Files in this worktree changed but uncommitted

```
modified:   .env.example
modified:   pyproject.toml
modified:   src/admin_frontend/routes.py
modified:   src/business_frontend/routes.py
modified:   src/business_frontend/templates/base.html
modified:   src/chatbot.py
modified:   src/core/database.py

new file:   handoff.md  ← this file
new file:   scripts/smoke_w1_billing.py
new file:   src/admin_frontend/templates/billing_detail.html
new file:   src/business_frontend/templates/billing.html
new file:   src/business_frontend/templates/billing_success.html
new file:   src/modules/billing/__init__.py
new file:   src/modules/billing/attribution.py
new file:   src/modules/billing/database.py
new file:   src/modules/billing/policy.py
new file:   src/modules/billing/stripe_checkout.py
new file:   src/modules/billing/stripe_webhook.py
```

`uv.lock` is also touched because `stripe` was added.

---

## Where to look first when you come back

1. This file (`handoff.md`).
2. `~/.claude/plans/for-project-amplora-we-cozy-corbato.md` — the canonical plan.
3. `~/.claude/projects/C--Users-trevo-publisher-demo-rag/memory/project_amplora_w1_shipped.md` — full W1 ship log.
4. `scripts/smoke_w1_billing.py` — re-run to confirm nothing rotted.
5. `src/modules/billing/policy.py` — Trevor's open item.
