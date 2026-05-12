# Amplora — Phase 1 handoff (next session entry point)

**Last touched:** 2026-05-12
**Branch:** `claude/vibrant-wing-11040d`
**Worktree:** `C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d`
**Plan file:** `~/.claude/plans/yes-ticklish-sparkle.md` (W2.2 plan, approved 2026-05-11)
**Companion plan:** `~/.claude/plans/for-project-amplora-we-cozy-corbato.md` (W1–W6 overall)

---

## TL;DR

W1 (billing) + W2.1 (PMC text pipeline) + **W2.2 voice interview Day 1+2** all working end-to-end. Real voice call verified 2026-05-12: browser → LiveKit room → Claude-driven conversation → transcript → existing `generate_pmc_from_transcript` pipeline → PMC draft → review page redirect. Cartesia voice is the default warm baseline; Trevor is auditioning replacements at https://play.cartesia.ai/voices.

**Day 3 (state machine + pacing + recording) and Day 4 (smoke + polish) are the next workstreams.** All W2.2 changes are uncommitted — Trevor will review the diff and commit before Day 3 starts.

**Next move:** read this file, run `uv run python scripts/verify_voice_config.py` to confirm providers are still green, then start Day 3 from the "What to do next" section.

---

## Commits + uncommitted state

| Commit | What | Lines |
|---|---|---|
| (UNCOMMITTED) | W2.2 voice interview Day 1+2 — LiveKit Cloud + Deepgram + Claude + Cartesia + DO Spaces, fully working voice agent + supporting scripts | ~2000 |
| `033820e` | Wrap up v4 session: dotenv override + handoff refresh | — |
| `ac94c5b` | PMC v4: VOICE field + observe-not-ask rule | +37 / -3 |
| `d819f51` | PMC v3: MAINTAIN bucket + owner-driven AMPLIFY/MAINTAIN/MUTE | +60 / -14 |
| `756018d` | Scope down to Amplora-only | -37,897 net |
| `4bcf546` | W2.1 PMC pipeline | +2,224 |
| `cf42f41` | W1 multi-tenant billing foundation | +2,195 |

`bfc1ea2` = master tip we branched from.

---

## What got built in W2.2 Day 1+2 (uncommitted)

### New files

| Path | Purpose |
|---|---|
| `src/modules/pmc/voice_agent.py` | LiveKit agent worker. Builds system prompt from INTERVIEW_SCRIPT, wires Deepgram→Claude→Cartesia, accumulates transcript via `conversation_item_added` events, POSTs to /voice/complete with HMAC token. Run as `python -m src.modules.pmc.voice_agent dev`. |
| `src/modules/pmc/voice_callback_auth.py` | HMAC mint/verify via `itsdangerous` reusing `BUSINESS_SESSION_SECRET` with a distinct salt. 90-min TTL. |
| `src/modules/pmc/voice_provisioning.py` | LiveKit room create + participant token mint + explicit agent dispatch. Only place that imports `livekit.api`. |
| `src/business_frontend/templates/pmc_interview.html` | Tabler-styled interview page: recording disclosure banner, mic meter, transcript ticker, coverage dots, End button. Disabled-visible chrome throughout. |
| `src/business_frontend/static/js/pmc_interview.js` | Vanilla JS LiveKit client. Connect, publish mic, subscribe to agent audio, listen for data messages, watchdog poll, mic meter. |
| `scripts/verify_voice_config.py` | Hits all 4 providers (LiveKit token mint, Deepgram key check, Cartesia key check, Spaces put/get/delete roundtrip). Run after any `.env` change. |
| `scripts/setup_spaces_lifecycle.py` | One-shot script to apply the 30-day auto-delete lifecycle rule on the Spaces bucket (DO web UI doesn't expose this). |

### Modified files

| Path | Change |
|---|---|
| `pyproject.toml` | +6 deps: `livekit-api`, `livekit-agents`, `livekit-plugins-anthropic`, `livekit-plugins-deepgram`, `livekit-plugins-cartesia`, `livekit-plugins-silero` (all >=1.0.0). 24 transitive packages via `uv sync`. |
| `src/core/config.py` | +13 env-var slots: LIVEKIT_*, DEEPGRAM_API_KEY, CARTESIA_API_KEY/VOICE_ID, PMC_INTERVIEW_TARGET_SECONDS, PMC_AGENT_NAME, PMC_VOICE_CALLBACK_BASE_URL, PMC_VOICE_RECORDING_ENABLED, SPACES_* (5). |
| `.env.example` | Documented new env vars with provider signup URLs. |
| `src/modules/pmc/database.py` | +`quantitative_json` column on `pmc_interview_sessions` via `_add_col_if_missing`. +4 voice statuses (`voice_awaiting/in_progress/completed/partial`). +4 helpers: `save_session_quantitative`, `mark_session_voice_started`, `complete_voice_session`, `get_session_for_org`. |
| `src/business_frontend/templates/pmc_prep.html` | Removed Step 2 textarea; form action → `/business/pmc/voice/start`; button label "Save and start interview"; mic-not-working escape-hatch copy. |
| `src/business_frontend/routes.py` | +4 routes: `POST /pmc/voice/start`, `GET /pmc/interview`, `POST /pmc/voice/complete` (HMAC-auth), `GET /pmc/voice/status` (watchdog poll). |
| `src/chatbot.py` | StaticFiles mounted at `/static`. |

### Not touched

- `src/modules/pmc/interview_script.py` — already W2.2-ready per its header comment. The 21 qualitative questions + follow_up_hints + weights are what the voice agent reads.
- `src/modules/pmc/transcript_to_pmc.py` — voice produces the same `transcript: str` blob; signature + prompt unchanged.
- `scripts/smoke_w2_pmc.py` — 33 assertions stay green (regression preserved).

---

## How to boot + verify the world hasn't rotted

```powershell
cd C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d

# 1. Providers green? (5 sec)
uv run python scripts/verify_voice_config.py
# Expect: 4 PASS sections (LiveKit token mint + Deepgram + Cartesia + Spaces put/get/delete)
# 1 warning (BUSINESS_SESSION_SECRET dev default) — fine for local

# 2. Lint clean?
uv run ruff check src/ scripts/
# Expect: All checks passed!

# 3. Smoke tests green?
uv run python scripts/smoke_w1_billing.py    # expect 19/19
uv run python scripts/smoke_w2_pmc.py        # expect 33/33

# 4. App boots?
uv run python src/chatbot.py
# Expect: Uvicorn running on http://0.0.0.0:8080
```

## How to click through a real voice interview (Day 2 gate, already passing)

**Terminal A** — FastAPI:
```powershell
cd C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d
uv run python src/chatbot.py
```

**Terminal B** — agent worker (separate process, mandatory):
```powershell
cd C:\Users\trevo\publisher-demo-rag\popular-network-demo\.claude\worktrees\vibrant-wing-11040d
uv run python -m src.modules.pmc.voice_agent dev
```
Wait for `registered worker  agent_name='amplora-pmc-interviewer'`.

**Browser:**
1. `http://localhost:8080/admin/cottonwood/main-street` → basic auth `admin/admin` → create invite (tier=growth) → copy link
2. Open invite in incognito → register → land on `/business/pmc/`
3. Fill in at least business_name → "Save and start interview"
4. Tick the recording disclosure → click Start → grant mic
5. Agent greets within ~2 sec, conversation runs
6. Click "End interview" when you've heard enough
7. Browser redirects to `/business/pmc/` showing the PMC draft

---

## What's working (verified 2026-05-12 real call)

- ✅ Browser joins LiveKit room with server-issued participant token (90-min TTL)
- ✅ Agent worker dispatches via explicit `agent_name`, joins room, reads metadata, builds system prompt from INTERVIEW_SCRIPT
- ✅ Deepgram Nova-3 streaming STT → Claude Sonnet 4.6 → Cartesia Sonic-3 TTS, all wired through `AgentSession`
- ✅ `conversation_item_added` event handler captures user + agent turns
- ✅ Browser data-message listener handles `end_requested` from the End button
- ✅ Watchdog polling `/voice/status` works as fallback for missed redirect message
- ✅ HMAC callback token verified by `/voice/complete` (90-min TTL, namespaced salt)
- ✅ `generate_pmc_from_transcript` runs on the voice transcript identically to the W2.1 paste path
- ✅ Idempotency on `interview_session_id` prevents double-create on retry
- ✅ Agent publishes `{type:"redirect"}` data message before disconnecting; browser navigates client-side
- ✅ DO Spaces bucket has 30-day lifecycle rule applied (recordings WIRING is Day 3)
- ✅ All 4 providers green via `verify_voice_config.py`

---

## What's open

### Day 3 — state machine + pacing + recording (1–1.5 days)

1. **`mark_question_covered` tool.** Add as a `function_tool` Claude can call. External tracker in the agent loop accumulates which question keys are covered. Tracker state is what feeds the next system message (see #2).
2. **Pacing injection.** Per LLM turn, prepend system context with `{elapsed_seconds, weight3_remaining, weight2_remaining, target_duration_seconds}`. Encode the rule in the prompt: "When elapsed >= 0.75 * target AND weight3_remaining > 0, narrate pacing and offer to defer weight=2 items." Hard cap at 60 min per `INTERVIEW_LENGTH_CAP_MINUTES`.
3. **LiveKit Egress → DO Spaces recording.** The `AgentSession.start(record=...)` parameter takes `RecordingOptions`. Configure egress to write to the `amplora-pmc-recordings` bucket using SPACES_* creds. Persist the resulting URL on the session row (`transcript_url` column already exists). 30-day lifecycle is already enforced by the bucket.
4. **Progress dot updates via data messages.** Agent should publish `{type:"coverage", total, covered, current, weight3_remaining}` whenever a question is marked covered. The JS already has `renderProgressDots()` ready to receive these.

### Day 4 — smoke + polish (~½ day)

1. **`scripts/smoke_w2_2_voice.py`** — hermetic layer-1 smoke. Real Claude API, no LiveKit/STT/TTS. Asserts: prompt assembly, state-machine coverage, callback token mint/verify, idempotency, callback → draft PMC. ~15 assertions matching the v3/v4 testing pattern.
2. **Shorten `wait_for_participant` timeout** from 5 min to 30 sec, and surface the failure as a visible browser banner (currently the agent dies silently after 5 min if the browser never joins, like the SDK-404 incident).
3. **Vendor `livekit-client.umd.min.js` to `/static/js/`** instead of CDN — sovereign-agents thesis says critical paths shouldn't depend on a CDN.
4. **Disconnect detection.** If the owner closes the tab mid-call, agent should persist partial transcript with `voice_provider='livekit_partial'`, and `/business/pmc/` should show "interview interrupted, redo?" instead of nothing.
5. **Cartesia outage fallback.** Catch TTS plugin errors in the agent loop and end gracefully with a "we're having a technical issue" canned response.
6. **`handoff.md` ship log update** after Day 4 lands.

### Lower priority / future

- W1 past-due policy constants still `None` (`src/modules/billing/policy.py:32,43`). Set `7` + `"freeze"`. 2-min fix.
- Manual Stripe test-mode click-through never run (W1 documented in `project_amplora_w1_shipped.md`).
- Twilio phone fallback (deferred — W2.3 per 2026-05-11 decision).

---

## Critical files / locations

**Plan + strategy:**
- `~/.claude/plans/yes-ticklish-sparkle.md` — W2.2 plan, approved 2026-05-11
- `~/.claude/plans/for-project-amplora-we-cozy-corbato.md` — overall Phase 1 plan
- `popular-network-demo/docs/amplora_*.md` — strategic docs (partner brief, phases, pitch)

**W1 (billing):** unchanged from prior handoff — see `cf42f41` commit and `project_amplora_w1_shipped.md` memory.

**W2.1 (PMC text):** unchanged from prior handoff — see `4bcf546` → `ac94c5b` commits and `project_amplora_w2_1_shipped.md` memory.

**W2.2 (voice) — NEW:**
- `src/modules/pmc/voice_agent.py` — agent worker. System prompt assembly + Deepgram/Claude/Cartesia/Silero wiring + transcript accumulation + callback POST. **Day 3 changes land here** (tool + pacing).
- `src/modules/pmc/voice_provisioning.py` — LiveKit SDK isolation point. If the SDK churns, this is the single file to update.
- `src/modules/pmc/voice_callback_auth.py` — HMAC mint/verify.
- `src/business_frontend/routes.py:730+` — the 4 voice routes (start, interview, complete, status).
- `src/business_frontend/templates/pmc_interview.html` — interview page.
- `src/business_frontend/static/js/pmc_interview.js` — browser client. **Day 3 changes land here** (coverage data message handler is ready and waiting).
- `scripts/verify_voice_config.py` — first thing to run on any session start.

**App entry + routing:** unchanged — see prior handoff.

---

## Ground rules (Trevor's non-negotiables — global CLAUDE.md)

- **Test before handoff.** Smoke + real path before declaring done. `python -c 'import x'` is not testing.
- **Verify the user hits the file I edited.** Multi-host deploys / env-var defaults / filename mismatch can route around your edit.
- **Disabled-visible > hidden-until-ready** for UI chrome. Failures should be diagnosable, not invisible. (Already followed in `pmc_interview.html`.)
- **Introspect installed SDKs before guessing method names.** Caught the `with_ttl_seconds`/`with_ttl(timedelta)` mismatch in W2.2 only because of Trevor running the verify script. New rule in `feedback_sdk_verification.md`: `inspect.signature` BEFORE writing call sites.

---

## Decisions encoded (don't relitigate without cause)

- **Decision 1 (billing processor):** Stripe.
- **Decision 2 (interview tone):** warm-personal.
- **Decision 3 (interview length cap):** adaptive, 35m target, 60m hard cap.
- **Tier names:** `starter` / `growth` / `concierge`.
- **Attribution policy v1:** invite-only.
- **Pre-interview prep:** required (Trevor 2026-05-09).
- **PMC has two layers:** STRATEGIC SUMMARY (14 fields) + question-by-question sections.
- **PMC field synthesis pattern:** per-field ASK vs OBSERVE (`feedback_amplora_prompt_patterns.md`).
- **Voice stack (2026-05-11):** LiveKit Cloud + Deepgram Nova-3 + Claude Sonnet 4.6 + Cartesia Sonic-3 + Silero VAD. Sovereign-agents thesis: Claude drives the conversation, never a hosted competitor.
- **Recording (2026-05-11):** YES, DO Spaces, 30-day retention with lifecycle rule auto-enforcement. Disclosure banner on /interview page.
- **Mic fallback (2026-05-11):** voice-only for W2.2. Twilio phone fallback deferred to W2.3.
- **Start timing (2026-05-11):** immediate (click → call). No scheduling UI.
- **LiveKit agent dispatch mode:** explicit dispatch by `agent_name='amplora-pmc-interviewer'`. Worker registers with same name; auto-accept all jobs targeted at it.

---

## Memory entries that document this work

- `project_amplora.md` — strategic context (Amplora rebrand, three-product platform)
- `project_amplora_implementation_plan.md` — Phase 1 plan + workstream status table
- `project_amplora_w1_shipped.md` — W1 ship log
- `project_amplora_w2_1_shipped.md` — W2.1 ship log
- `project_amplora_w2_2_shipped.md` — **W2.2 Day 1+2 ship log (this work)**
- `project_amplora_scope_reduction.md` — Amplora-only scope cut
- `feedback_amplora_prompt_patterns.md` — ASK vs OBSERVE heuristic
- `feedback_sdk_verification.md` — **NEW: introspect SDKs before writing call sites**
