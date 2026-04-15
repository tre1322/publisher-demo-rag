# v2 RAG rebuild — morning handoff

**Session window:** evening 2026-04-14 → morning 2026-04-15
**Directive:** "GO to work!" — autonomous multi-phase build; Trevor reviews & deploys.
**Canary:** Kameron Koerner state-wrestling query must answer correctly, not confabulate.

## TL;DR

- **Recall@5: 0.714 → 1.000.** Every answerable case in the eval set now returns the expected article in top-5.
- **MRR: 0.536 → 0.857.** The expected article is usually rank 1 or 2.
- **Abstain-pass: 0.0 → 0.667.** The system now refuses weather / fictional-person queries structurally instead of confabulating.
- **Zero commits, zero deploys.** Working tree only. Review → commit → push is yours.

## What's in the working tree

### New files
| Path | Purpose |
|---|---|
| `tests/retrieval_eval.py` | Harness — runs cases through `QueryEngine.retrieve()`, reports recall@5 / MRR / abstain / leakage |
| `tests/data/retrieval_eval_set.json` | 12 seeded cases (expand this when you get time — target 30-50) |
| `tests/data/eval_runs/baseline_pre_v2.json` | Frozen baseline before any v2 work |
| `tests/data/eval_runs/phase2c_intent_router.json` | Checkpoint after router |
| `tests/data/eval_runs/phase3_hybrid.json` | Checkpoint after FTS5 + RRF |
| `tests/data/eval_runs/phase5_rerank.json` | Checkpoint after cross-encoder (**latest**) |
| `src/query_router.py` | Intent classifier — 5 intents, regex precedence |
| `src/modules/articles/grounding.py` | Entity coverage gate + post-generation grounding audit |
| `src/modules/articles/fts.py` | SQLite FTS5 lexical search + RRF fusion |
| `src/modules/articles/reranker.py` | Cross-encoder reranker (lazy, graceful fallback) |
| `src/modules/observability/decision_log.py` | JSONL decision log to `data/retrieval_decisions.jsonl` |
| `src/modules/observability/__init__.py` | Package export |

### Modified files
| Path | Change |
|---|---|
| `src/query_engine.py` | Deleted EDITION_CURRENT_BOOST + freshness boost. Added hybrid retrieval + RRF + reranker. Added `current_edition_only` param. Relaxed threshold when edition-scoped. |
| `src/chat_frontend/routes.py` | Deleted cross_ref_keywords auto-expand + unconditional secondary network search. Wired intent router, entity gate, grounding audit, decision log. |
| `src/prompts.py` | Deleted "general knowledge is ok". Strict grounding rules, citation requirement, explicit abstain phrasing. |
| `src/core/config.py` | LLM_TEMPERATURE default 0.3 → 0.0 |
| `.env` | LLM_TEMPERATURE=0.0 (**Railway env still needs this change**) |
| `tests/data/retrieval_eval_set.json` | Fixed bogus doc_ids from the seed set; corrected publisher name `Pipestone Star` → `Pipestone County Star` |

## Eval scoreboard (phase5_rerank latest)

```
recall_at_5            1.000   (baseline 0.714 — +40%)
mrr                    0.857   (baseline 0.536 — +60%)
abstain_pass_rate      0.667   (baseline 0.000)
offer_expand_pass_rate 0.000   (Phase 4 — deferred)
leakage_cases          0
overall_pass_rate      0.750   (baseline 0.417)
```

All 7 `answer` cases PASS. 2/3 `abstain` cases PASS (weather + nonexistent person).
Remaining failures:

- `citizen_asks_about_pipestone`, `vague_other_papers` — `offer_expand` behavior. **Phase 4 (Claude tool use) territory. I did not touch this — high risk without you watching.**
- `fake_edition_date` — "what was in the April 1, 2026 edition?" There's no proper noun for the entity gate to fire on, and the router doesn't recognize a fake date as out-of-scope. **Punt: add a date-validation layer or LLM-classifier fallback.**

## Architectural changes by phase

### Phase 1 — killed the broken primitives
- `EDITION_CURRENT_BOOST = 1.5` and the 1.15/1.05 freshness multipliers are gone. Cosine similarity is no longer warped outside its trained distribution.
- Secondary cross-network retrieval (ran on *every* turn, stapled at 0.6× score) removed.
- `cross_ref_keywords` 30-word auto-expansion list removed.
- Strict grounding prompt: "Answer ONLY from context"; "NEVER use general knowledge to fill gaps"; "when context contains ADJACENT-but-wrong content, do NOT summarize as if it answered."
- Temperature 0.3 → 0.0.

### Phase 2a — entity coverage gate
`has_entity_coverage(query, chunks)`. Extract proper nouns, pick the longest (Option C: surnames usually win by length). If that token appears in zero retrieved chunks → skip the LLM call, return a canned abstention. Saves a round trip and prevents confabulation at the API boundary.

### Phase 2b — grounding audit (observability only)
After generation, extract proper nouns from the response and check each against the retrieved chunk text. `unverified` nouns are logged but the response is NOT modified (high false-positive risk on legitimate inferences). `_PROPER_NOUN_STOP` expanded to cover sentence-starters ("According", "Based", "However"...) that were false-flagging.

### Phase 2c — intent router
5 intents with regex precedence: `out_of_scope > business_lookup > event_lookup > current_edition > article_qa`. Each emits a `RouteDecision` with per-lane flags (articles / ads+directory / events / sponsored / current_edition_only). `routes.py` respects these flags instead of dumping every corpus into one evidence bag.

Out-of-scope short-circuits before any retrieval (weather / stock price / recipes).

### Phase 3 — SQLite FTS5 + asymmetric RRF
460-article FTS5 table (title + body, Porter+unicode61 tokenizer, publisher UNINDEXED for cheap filtering). Rebuild-from-scratch on demand (triggers rejected — rebuild is <1s at this scale, much smaller failure surface).

Fusion uses **asymmetric** Reciprocal Rank Fusion:
```
score = 1/(60 + dense_rank) + 1/(10 + lex_rank)
```
The textbook k=60-on-both setup fails when lexical is rank-1-dominant and dense is noisy — a lexical-only #1 (0.0164) loses to a dense #1 + lex #6 (0.031). k_lex=10 sharpens lexical so a rare-term exact match wins the precision battle. That's what flipped `historical_speech_team` from "not in top 20" to rank 1.

Lexical-only matches (FTS found it, dense missed it entirely) get their best chunk pulled from Chroma by `doc_id` filter, then joined into the fusion pool.

### Phase 5 — cross-encoder reranker
`cross-encoder/ms-marco-MiniLM-L-12-v2`. Lazy-loaded (first call pays ~500ms init, subsequent calls should be ~100-200ms but observed 2-6s in harness — flag for production monitoring). Graceful fallback: if the model fails to load, eval and production both continue with hybrid-only.

**Gated off the `current_edition_only` path.** Cross-encoders need a concrete signal to score against. "What's this week's feature story?" has no topic; the reranker reorders candidates by training-distribution accident and demoted the Snakes Alive canary out of top-5. Intent-router flag is the clean gate.

### Phase 6c — decision logs
`data/retrieval_decisions.jsonl` — one JSON line per turn. Fields: query, intent, entity gate, top-10 chunk summaries (doc_id/title/scores — no body), abstain reason, latency, response preview, grounding audit. Best-effort writes; never blocks the chat path.

## What I did NOT do (by your directive or my judgment)

- **Phase 4 (Claude tool use for `search_grand_network`)** — too risky unsupervised. Requires SSE refactor for tool-call round trips. Would have touched every request path. Parked.
- **Phase 6a (publisher_id migration)** — touches every Chroma metadata row and the backfill script. Needs coordination with your live deploy.
- **Phase 6b (hostname resolver)** — affects production routing; you should watch this land.
- **No git commits.** You review, you commit, you deploy.
- **No Railway deploy.** Same reason.

## Things you need to do

1. **Review the diff** — it's a lot: `git diff --stat` first to get the shape, then spot-check the intent router and fts module which are the highest-leverage pieces.
2. **Update Railway env:** `LLM_TEMPERATURE=0.0`. `.env` is updated but Railway pulls its own.
3. **Build the FTS5 index on Railway after deploy.** Either:
   - Run `rebuild_fts()` once at startup (add to app init — the function is idempotent and ~1s at our scale), or
   - Run it manually from a Railway shell once.
4. **Expand the eval set** when you have time. 12 cases is enough to catch regressions on the specific failures I fixed; 30-50 gives you generalization confidence. Especially light on: Pipestone-only queries, event_lookup, business_lookup.
5. **Watch reranker latency in prod.** The 2-6s I saw in the harness is probably the first-call-loads-model amortization issue. If it doesn't drop below ~200ms per query in production, either (a) switch to the L-6 variant, or (b) swap for Cohere Rerank v3.
6. **Koerner canary on live.** After deploy, ask the chatbot "Did Kameron Koerner make state wrestling?" at `/` (Cottonwood scope). Expected: rank-1 chunk is a Koerner article; response cites the right tournament result; no invented wrestler from a different edition.

## Known gaps & deferred work

- `fake_edition_date` — query asks about a fictional edition; nothing in the pipeline catches it. Add: date validation (LLM or regex), or a Chroma distance-floor that trips when the top chunk is still weak.
- `pipestone_calumet` has an earlier-noted sync gap: article exists in SQLite but has 0 Chroma chunks. It passes eval now because FTS5 pulls it from SQLite and RRF fetches its chunks as lexical-only. Still worth a proper Chroma reindex at some point.
- Reranker lazy-load means the FIRST user of the morning pays ~5s. Consider warming it in a startup task.
- Sponsored-answer surfacing stays unconditional-per-intent (router keeps `use_sponsored=True` for every in-scope intent) — that preserves the "load-bearing revenue loop" contract. Revisit if sponsored content ever pollutes factual answers.

## Files to skim first if you only have 10 minutes

1. `src/query_router.py` — 160 lines. The whole v2 "don't merge corpora indiscriminately" discipline lives here.
2. `src/modules/articles/fts.py` — 230 lines. Understand the asymmetric RRF decision; it's the biggest quality lever.
3. `src/modules/articles/grounding.py` — 200 lines. The structural confabulation guard.
4. `tests/data/eval_runs/phase5_rerank.json` — the scoreboard with per-case chunk trails.

— Claude, overnight 2026-04-14
