# Handoff — popular-network-demo
*Last updated: 2026-05-26 (end of Phase C.3 — tool use)*

## TL;DR for the next conversation

`popular-network-demo/` is a real-backend marketing dashboard at `http://localhost:8765`. The demo business is **Quadd.ai** — Trevor Slette's real B2B SaaS for newspaper publishers. The AI Agent (sidebar → "AI Agent") uses Claude Sonnet 4.6 with a voice brief synthesized from Trevor's real 23-minute voice interview, and now fires **4 tools** via a multi-step Anthropic tool_use loop (cap=4). All 9 smokes green (16 assertions in `smoke_chat` alone). Ready for **C.2 (streaming)** next.

## What Phase C.3 added (this session, 2026-05-26)

- **4 tools** in `app/agent/tools.py`: `draft_post`, `propose_boost`, `draft_review_response`, `regenerate_insights`. Each has an Anthropic-format JSON schema + a Python executor that mutates the DB and returns a `(text_for_model, attachment_for_frontend)` ToolResult.
- **Multi-step loop** in `app/routers/chat.py` with `MAX_TOOL_ITERATIONS=4` cap. Each iteration: call Claude → if `stop_reason=tool_use` execute the tools + feed `tool_result` back → repeat → break on `end_turn` or cap.
- **`tool_choice` routing** in `chat.py` — detects "draft me / write me / give me a draft / compose / put together a draft" patterns in the user message via `_DRAFT_POST_TRIGGER_RE` and forces `tool_choice={"type":"tool","name":"draft_post"}` on iteration 0. Subsequent iterations stay `auto`. **Critical** — without this, Sonnet 4.6's RLHF caution refuses to fire draft_post even with CRITICAL system-prompt directives. See "Lesson learned" below.
- **5 inline card components** in `dashboard.html` (`ToolCard` dispatcher + DraftPostCard / ReviewResponseCard / InsightsRefreshedCard / ToolErrorCard, plus existing BoostCard reused). New `attachment.items[]` shape for multi-tool turns; legacy seeded `attachment.kind` shape still renders BoostCard for backwards compat.
- **System-prompt tool-use section** in `app/agent/system_prompt.py` — calm declarative directive; the heavy-lifting is in the routing, not the prompt yelling.
- **smoke_chat.py extended** to 16 assertions including tool-use loop, multi-step round-trip, cap enforcement, routing trigger variants (7 positives + 4 negatives).

### Lesson learned — burned into memory

Sonnet 4.6 is RLHF-tuned to be conversational/collaborative on creative tasks. With a DB-mutating tool like `draft_post`, the model **refuses to fire even with `CRITICAL` + `WRONG vs RIGHT example` system-prompt directives**. We saw 5 live API calls in a row where the model drafted inline as text despite explicit instructions. Conversation-history examples (each prior inline draft) reinforce the refusal pattern.

**Fix:** push the policy into code (regex + `tool_choice` forcing) instead of prompt yelling. Prompts describe intent; code enforces.

This pattern generalizes to any future tool where Sonnet's bias conflicts with product policy.

## Start the dashboard

```bash
cd C:/Users/trevo/publisher-demo-rag/popular-network-demo
uv run python -m app.main
# or: ./start.bat
```

Then open `http://localhost:8765`.

## Where things are

```
popular-network-demo/
├── app/
│   ├── main.py                ← FastAPI app + uvicorn entry. load_dotenv override=True.
│   ├── seed.py                ← Quadd.ai Day-1 seed
│   ├── agent/
│   │   ├── __init__.py
│   │   └── system_prompt.py   ← Assembles role + voice brief + DB context for Claude
│   ├── routers/
│   │   ├── bootstrap.py       ← /api/bootstrap, includes voiceBrief field
│   │   ├── chat.py            ← /api/chat/turn (blocking, Phase C.1)
│   │   └── ...
│   └── scripts/
│       ├── smoke_chat.py      ← Phase C.1 smoke (9 assertions, mocks Anthropic SDK)
│       └── smoke_*.py         ← All 8 prior smokes updated for Quadd seed
├── scripts/
│   ├── transcribe_voice_brief.py    ← faster-whisper local transcription
│   └── synthesize_voice_brief.py    ← Claude synthesis of PMC v4-shape brief
├── voice-briefs/
│   └── quadd_ai.json          ← The synthesized brief (COMMITTED, source of truth)
├── data/
│   ├── popular_network.db     ← SQLite, gitignored
│   └── voice-brief/           ← .ogg + transcripts (GITIGNORED, sensitive PII)
├── dashboard.html             ← Single-file React/CDN (BUILD_VERSION at line 127)
├── pyproject.toml             ← uv-managed; anthropic, python-dotenv, fastapi
└── handoff.md                 ← (this file)
```

## What shipped in this session

Three commits on master:

| SHA | What |
|-----|------|
| `db0e5e9` | Phase C.1 + Quadd.ai pivot (chat endpoint, system prompt, frontend wiring, reseed, recovery scripts) |
| `2da543d` | Voice brief surfaced in AI Agent side panel + Settings cleanup |
| `2114262` | Voice Brief viewer at top of Marketing Plan (full AMPLIFY/MAINTAIN/MUTE viewer) |

`f182c66` was the Phase B end-point on master.

## Architecture decisions worth respecting

1. **Voice carries via two mechanisms, both load-bearing.** The seeded ChatTurn history (when present) acts as few-shot via the `messages` parameter, AND the `_voice_brief_section()` is inlined into the system prompt with `cache_control: ephemeral`. Don't replace these with hardcoded "voice rules" in Python — Trevor pushed back hard on that approach early in the C.1 session.

2. **`voiceBrief` is exposed through /api/bootstrap.** The frontend reads it as `bootstrap.voiceBrief`. Don't add a separate `/voice-briefs/{slug}.json` fetch from the browser — single source of truth, single auth boundary later.

3. **File layout split.** `voice-briefs/{slug}.json` is committed (source-controlled, deployable artifact). `data/voice-brief/*.ogg / *_transcript.txt / *_segments.json` is gitignored (sensitive raw materials, PII). This is the pattern Amplora's real pipeline should also adopt.

4. **Quadd seed is Day-1 honest.** No fabricated history. Empty chat thread, 0 published-post performance, 0 review aggregate, $0 spend. The dashboard handles empty states gracefully via the new shape-tolerant smoke assertions.

5. **Prompt caching.** The system prompt is sent in a single block with `cache_control: ephemeral`. First turn pays the full input-token cost; subsequent turns in the same session pay ~10%. Verified working: `cache_create=2403` on first turn from the live test.

## Where the voice brief is visible in the dashboard

1. **AI Agent → right side panel** ("What the agent knows") — summary: voice quote + bucket counts + top AMPLIFY item.
2. **Marketing Plan → top section** (violet-themed Card, collapsible) — full structured brief: VOICE blockquote, every AMPLIFY/MAINTAIN/MUTE item with label+detail, customer language, proof points, constraints, notes, provenance footer.
3. **Raw JSON** at `voice-briefs/quadd_ai.json`.

## How the AI Agent works end-to-end

1. Owner types message in `ChatView` (dashboard.html ~3225).
2. Frontend optimistically appends owner turn, POSTs to `/api/chat/turn`.
3. Backend (`app/routers/chat.py`) loads all prior `ChatTurn` rows for this business, converts to `messages` array.
4. Builds system prompt via `app/agent/system_prompt.py:build_system_prompt()` — assembles role + voice brief + marketing plan + recent posts + insights + pinned review + behavior rules.
5. Calls `anthropic.Anthropic().messages.create()` with `model="claude-sonnet-4-6"`, `system=[{"type":"text", "text":..., "cache_control":{"type":"ephemeral"}}]`, max_tokens=1024.
6. Persists owner + agent turns to ChatTurn table.
7. Returns `{ok, ownerTurn, agentTurn}` — frontend swaps optimistic owner turn with canonical pair.

## Known issues carried forward

1. **Droplet's PMC pipeline stalled silently** during Trevor's interview — agent stopped responding at "~3 questions left." Likely CoverageTracker (W2.2 Day 3) race or queue-stall. When investigating, grep `amplafai-prod` for `mark_question_covered` state transitions and check LiveKit logs for room `pmc-2` around 2026-05-26 09:55-10:01 UTC.
2. **Admin invite-form tier mismatch** at `app.amplafai.com/admin/{publisher}/main-street` — UI offers `growth`/`premium` values, backend validates `starter`/`growth`/`concierge`. Picking "Premium" in the UI returns 400.
3. **Stale "Goal hero" card** in Marketing Plan (~line 3037 in dashboard.html) — still has hardcoded "Ten new customers a month" + "Set with John Draper on March 19" Westbrook narrative. Not blocking. Could be regenerated from voice brief on demand or made editable.
4. **No "Start your voice interview" CTA** on the business dashboard after registration. Trevor had to navigate to `/business/pmc/interview` manually.

## Reusable lessons banked this session (in memory + this section)

- **`load_dotenv(override=True)`** — Trevor's shell exports `ANTHROPIC_API_KEY=""` somewhere. The default `override=False` treats empty as "already set" and skips the .env value. Returns True, but value stays empty. Always use `override=True` for project-owned config. See `feedback_dotenv_override` in memory.
- **JSX arrow-to-block refactor footgun** — when converting `() => (...)` to `() => { ...; return (...); }`, the original closing `);` becomes a stray `);` that Babel-CDN parse-fails on silently. Blank page, no console error. Always grep around the function boundary after the close. See `feedback_jsx_arrow_refactor` in memory.
- **PMC recovery pipeline** — when the droplet's PMC synthesis stalls, pull .ogg from DO Spaces with admin creds + run `scripts/transcribe_voice_brief.py` + `scripts/synthesize_voice_brief.py` locally. See `reference_pmc_recovery_pipeline` in memory.

## Verification

```bash
cd popular-network-demo
for s in smoke_bootstrap smoke_approvals smoke_posts smoke_compose \
         smoke_marketing_plan smoke_performance smoke_reviews \
         smoke_settings smoke_chat; do
  uv run python -m app.scripts.$s
done
```

Should print `PASS ...` for all 9.

## What's next (in priority order, Trevor's call)

### C.2 — Streaming (~30 min)
Swap `POST /api/chat/turn` to `StreamingResponse` returning SSE chunks. Frontend's `send()` swaps `resp.json()` for a chunked-reader loop, mutating the agent turn's text as each chunk arrives. Eliminates the 2-4s silent wait between message sent and agent reply.

**Backend changes**: `app/routers/chat.py` — wrap response in `anthropic.messages.stream(...)`, yield SSE-formatted chunks (`data: {"type":"delta", "text": "..."}\n\n`), persist both turns after stream completes. Note: tool_use blocks complicate streaming — tool inputs arrive as `input_json_delta` chunks and must be accumulated before executing the tool. The multi-step loop also means N model calls per owner message, each with its own stream. Plan to flush text first, then tool-use, per iteration.
**Frontend changes**: `dashboard.html` ChatView's `send()` — replace `await resp.json()` with `ReadableStream` reading + per-chunk `mutate(prev => ...)` to update the agent turn incrementally.
**Smoke**: `smoke_chat.py` needs a chunked-reading helper to consume the stream and assert the final text matches what the mock returned.

### C.3 — Tools (~1-2 hr) — SHIPPED 2026-05-26
~~Wire the four tools…~~ All four tools shipped. See "What Phase C.3 added" section above.

### Other carried-forward work (lower priority)
- Replace the stale "Goal hero" Westbrook narrative in Marketing Plan
- "Start your voice interview" CTA on the business dashboard
- Compose Memorial Day mock-defaults swap (the "Re-draft from brief" button)
- Phase D-F surfaces from the plan at `~/.claude/plans/ok-we-need-a-tender-pillow.md`

## Key external URLs / refs

- **App.amplafai.com admin console**: `https://app.amplafai.com/admin/cottonwood/main-street` (HTTP Basic, `admin` / `ADMIN_PASSWORD` from `~/amplafai-railway-env.txt`)
- **DO Spaces bucket**: `amplora-pmc-recordings`, endpoint `https://nyc3.digitaloceanspaces.com` (creds in same file)
- **Plan file**: `~/.claude/plans/ok-we-need-a-tender-pillow.md`
- **Quadd.ai business in admin**: org_id=2 (because the parent's data dir had org_id=1 already from earlier seeds — note: in *this demo*, the Quadd seed uses business_id=1 since it's a fresh local DB)

## Quick-reference: stale-Westbrook-leftover whack-a-mole

Throughout this session I kept finding hardcoded Westbrook strings in dashboard.html that the C.1 reseed missed. Fixed during this session: `<title>`, BUILD_VERSION, FALLBACK_DATA mock-data block, "What the agent knows" panel, sidebar user-card tier, Settings "Your plan" card + onboarding milestones, GBPPreview defaults. **Remaining**: "Goal hero" card narrative (~line 3037), "John Draper, your rep" reference (~line 3606), possibly more — when in doubt: `grep -niE "westbrook|dale henderson|karen b|memorial day|pipestone county|john draper" dashboard.html`.
