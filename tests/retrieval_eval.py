"""Retrieval evaluation harness.

Runs the test cases in tests/data/retrieval_eval_set.json through the production
retrieval pipeline (QueryEngine.retrieve) and reports:

    - recall@5            : was an expected doc_id in the top-5 retrieved chunks?
    - MRR                 : mean reciprocal rank of the first expected chunk
    - abstain_respected   : for abstain cases, did we return <= 1 chunks? (proxy)
    - cross_pub_leakage   : for single-publisher cases, any chunk from another pub?

This is *retrieval-only* — no LLM is called. That keeps the harness fast,
deterministic, and reproducible. A separate generation-level eval (confabulation
rate on "abstain" cases with the real LLM) will live alongside this once the
entity gate lands in Phase 2a.

Usage:
    uv run python tests/retrieval_eval.py                    # print report
    uv run python tests/retrieval_eval.py --save baseline    # also save JSON
    uv run python tests/retrieval_eval.py --compare baseline # diff vs saved run
    uv run python tests/retrieval_eval.py --only koerner     # filter by id substring
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Silence noisy library logs so the report is readable
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
for noisy in ("src.query_engine", "src.search_agent", "chromadb", "httpx"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env *before* importing src.core.config, and force override so an empty
# ANTHROPIC_API_KEY in the ambient shell env (which is the default on Trevor's
# machine) doesn't shadow the real value in .env.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(REPO_ROOT / ".env", override=True)
except Exception:
    pass

EVAL_SET_PATH = REPO_ROOT / "tests" / "data" / "retrieval_eval_set.json"
BASELINE_DIR = REPO_ROOT / "tests" / "data" / "eval_runs"


def _doc_id_matches(actual: str, expected: str) -> bool:
    """Match by prefix. Test set may use short ids (8-char) or full UUIDs."""
    if not actual or not expected:
        return False
    return actual.startswith(expected) or expected.startswith(actual)


def _chunk_doc_ids(chunks: list[dict]) -> list[str]:
    """Extract doc_ids from retrieved chunks in rank order (dedup, keep first)."""
    seen: list[str] = []
    for c in chunks:
        md = c.get("metadata", {}) or {}
        doc_id = str(md.get("doc_id", ""))
        if doc_id and doc_id not in seen:
            seen.append(doc_id)
    return seen


def _first_expected_rank(retrieved_doc_ids: list[str], expected: list[str]) -> int | None:
    """Return 1-indexed rank of the first retrieved doc_id that matches any expected."""
    for rank, actual in enumerate(retrieved_doc_ids, start=1):
        if any(_doc_id_matches(actual, exp) for exp in expected):
            return rank
    return None


def _evaluate_case(engine, case: dict[str, Any], top_k_for_metrics: int = 5) -> dict[str, Any]:
    """Run a single case through retrieve() and compute its metrics row."""
    query = case["query"]
    publisher = case.get("publisher")
    expected_behavior = case.get("expected_behavior", "answer")
    expected_doc_ids = case.get("expected_doc_ids") or []

    # Phase 2c: run the intent router first. If it says out_of_scope, retrieval
    # is skipped entirely (same as routes.py behavior). Otherwise pass the
    # current_edition_only flag through to retrieve().
    try:
        from src.query_router import classify as classify_intent
        route = classify_intent(query)
    except Exception as exc:  # noqa: BLE001
        route = None
        router_err = f"{type(exc).__name__}: {exc}"
    else:
        router_err = None

    t0 = time.perf_counter()
    if route is not None and route.intent == "out_of_scope":
        # Router abstained before retrieval — no chunks by design.
        chunks: list[dict] = []
    else:
        try:
            retrieve_kwargs: dict = {"publisher": publisher}
            if route is not None and route.current_edition_only:
                retrieve_kwargs["current_edition_only"] = True
            if route is not None and not route.use_articles:
                # Router says no article retrieval for this intent. We still
                # return an empty list; supplemental corpora (ads/events) are
                # not part of the retrieval eval surface today.
                chunks = []
            else:
                chunks = engine.retrieve(query, **retrieve_kwargs)
        except Exception as exc:  # noqa: BLE001 — surface harness failures inline
            return {
                "id": case["id"],
                "query": query,
                "publisher": publisher,
                "expected_behavior": expected_behavior,
                "error": f"retrieve() raised {type(exc).__name__}: {exc}",
                "pass": False,
            }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    doc_ids = _chunk_doc_ids(chunks)
    top_doc_ids = doc_ids[:top_k_for_metrics]
    rank = _first_expected_rank(top_doc_ids, expected_doc_ids) if expected_doc_ids else None

    # Publisher leakage — any returned chunk belonging to a different publisher?
    leakage_publishers: list[str] = []
    for c in chunks:
        md = c.get("metadata", {}) or {}
        chunk_pub = str(md.get("publisher", ""))
        if publisher and chunk_pub and chunk_pub != publisher:
            leakage_publishers.append(chunk_pub)

    row: dict[str, Any] = {
        "id": case["id"],
        "query": query,
        "publisher": publisher,
        "expected_behavior": expected_behavior,
        "expected_doc_ids": expected_doc_ids,
        "retrieved_top5": top_doc_ids,
        "first_expected_rank": rank,
        "recall_at_5": bool(rank is not None and rank <= top_k_for_metrics),
        "reciprocal_rank": (1.0 / rank) if rank else 0.0,
        "n_chunks_retrieved": len(chunks),
        "leakage_publishers": sorted(set(leakage_publishers)),
        "elapsed_ms": elapsed_ms,
    }

    # Run the entity coverage gate against the retrieved chunks (Phase 2a).
    # This is what actually blocks confabulation on abstain cases — so it
    # belongs in the eval harness too, not just routes.py.
    try:
        from src.modules.articles.grounding import (
            has_entity_coverage, longest_proper_noun,
        )
        gate_token = longest_proper_noun(query)
        gate_ok, gate_missing = has_entity_coverage(query, chunks)
    except Exception as exc:  # noqa: BLE001
        gate_token = None
        gate_ok, gate_missing = True, None
        row_gate_err = f"{type(exc).__name__}: {exc}"
    else:
        row_gate_err = None
    row["gate_token"] = gate_token
    row["gate_fired"] = gate_token is not None and not gate_ok
    row["gate_missing"] = gate_missing
    if row_gate_err:
        row["gate_error"] = row_gate_err

    # Phase 2c: record router decision for diagnostics.
    if route is not None:
        row["intent"] = route.intent
        row["intent_reason"] = route.reason
        row["current_edition_only"] = route.current_edition_only
    else:
        row["intent"] = "router_error"
        row["intent_reason"] = router_err or "unknown"
        row["current_edition_only"] = False

    # Per-behavior pass/fail judgment
    if expected_behavior == "answer":
        row["pass"] = row["recall_at_5"]
    elif expected_behavior == "abstain":
        # Abstain cases PASS when the pipeline has a structural reason to
        # refuse: retrieval returned nothing, OR the entity gate fires
        # (proper-noun absent from all chunks), OR the intent router
        # classified the query as out_of_scope (weather/stock/recipes).
        row["pass"] = (
            (len(chunks) == 0)
            or row["gate_fired"]
            or row.get("intent") == "out_of_scope"
        )
    elif expected_behavior == "offer_expand":
        # Cross-publisher expansion is a Phase 4 (tool use) signal. Until that
        # lands, the retrieval-level proxy is: no in-scope chunks survived.
        row["pass"] = len(chunks) == 0
    else:
        row["pass"] = row["recall_at_5"]

    return row


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answer_rows = [r for r in rows if r.get("expected_behavior") == "answer"]
    abstain_rows = [r for r in rows if r.get("expected_behavior") == "abstain"]
    expand_rows = [r for r in rows if r.get("expected_behavior") == "offer_expand"]

    recall_5 = (
        sum(1 for r in answer_rows if r.get("recall_at_5")) / len(answer_rows)
        if answer_rows else 0.0
    )
    mrr = (
        sum(r.get("reciprocal_rank", 0.0) for r in answer_rows) / len(answer_rows)
        if answer_rows else 0.0
    )
    abstain_pass = (
        sum(1 for r in abstain_rows if r.get("pass")) / len(abstain_rows)
        if abstain_rows else 0.0
    )
    expand_pass = (
        sum(1 for r in expand_rows if r.get("pass")) / len(expand_rows)
        if expand_rows else 0.0
    )
    leakage_cases = sum(1 for r in rows if r.get("leakage_publishers"))

    return {
        "n_cases": len(rows),
        "n_answer": len(answer_rows),
        "n_abstain": len(abstain_rows),
        "n_offer_expand": len(expand_rows),
        "recall_at_5": round(recall_5, 3),
        "mrr": round(mrr, 3),
        "abstain_pass_rate": round(abstain_pass, 3),
        "offer_expand_pass_rate": round(expand_pass, 3),
        "leakage_cases": leakage_cases,
        "overall_pass_rate": round(
            sum(1 for r in rows if r.get("pass")) / len(rows), 3
        ) if rows else 0.0,
    }


def _print_report(rows: list[dict[str, Any]], agg: dict[str, Any]) -> None:
    print("\n" + "=" * 88)
    print("RETRIEVAL EVAL — per-case")
    print("=" * 88)
    for r in rows:
        status = "PASS" if r.get("pass") else "FAIL"
        if r.get("error"):
            line = f"[{status}] {r['id']:40s}  ERROR: {r['error']}"
        else:
            rank = r.get("first_expected_rank")
            rank_str = f"rank={rank}" if rank else "rank=—"
            behav = r["expected_behavior"]
            leak = f" leak={r['leakage_publishers']}" if r.get("leakage_publishers") else ""
            gate = ""
            if r.get("gate_fired"):
                gate = f" gate=FIRED({r.get('gate_missing')})"
            elif r.get("gate_token"):
                gate = f" gate=ok({r.get('gate_token')})"
            intent = r.get("intent") or ""
            intent_str = f" intent={intent}" if intent else ""
            line = (
                f"[{status}] {r['id']:40s}  "
                f"behav={behav:12s}  {rank_str:8s}  "
                f"n={r['n_chunks_retrieved']:2d}  {r['elapsed_ms']:4d}ms{leak}{gate}{intent_str}"
            )
        print(line)
    print("=" * 88)
    print("AGGREGATE")
    print("=" * 88)
    for k, v in agg.items():
        print(f"  {k:28s} {v}")
    print("=" * 88 + "\n")


def _diff_against(rows: list[dict[str, Any]], agg: dict[str, Any], baseline_name: str) -> int:
    """Compare current run against a saved baseline. Returns non-zero if regressed."""
    path = BASELINE_DIR / f"{baseline_name}.json"
    if not path.exists():
        print(f"[compare] no baseline at {path}; nothing to diff against.")
        return 0
    baseline = json.loads(path.read_text())
    base_rows = {r["id"]: r for r in baseline["rows"]}
    regressions: list[str] = []
    improvements: list[str] = []
    for r in rows:
        prior = base_rows.get(r["id"])
        if not prior:
            continue
        if prior.get("pass") and not r.get("pass"):
            regressions.append(r["id"])
        elif not prior.get("pass") and r.get("pass"):
            improvements.append(r["id"])
    print(f"\n[compare vs '{baseline_name}']")
    print(f"  baseline recall@5 = {baseline['agg']['recall_at_5']}, current = {agg['recall_at_5']}")
    print(f"  baseline MRR      = {baseline['agg']['mrr']},       current = {agg['mrr']}")
    if improvements:
        print(f"  IMPROVED ({len(improvements)}): {', '.join(improvements)}")
    if regressions:
        print(f"  REGRESSED ({len(regressions)}): {', '.join(regressions)}")
        return 1
    if not improvements and not regressions:
        print("  (no change)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval eval harness")
    parser.add_argument("--only", help="substring filter on case id", default=None)
    parser.add_argument("--save", help="save run as eval_runs/<name>.json", default=None)
    parser.add_argument("--compare", help="diff against saved baseline name", default=None)
    parser.add_argument("--skip-todo", action="store_true", default=True,
                        help="skip cases tagged TODO / PLEASE_FILL (default: on)")
    args = parser.parse_args()

    if not EVAL_SET_PATH.exists():
        print(f"ERROR: eval set not found at {EVAL_SET_PATH}")
        return 2
    data = json.loads(EVAL_SET_PATH.read_text())
    cases = data.get("cases", [])

    # Skip placeholder cases until Trevor fills them in
    def _keep(c: dict) -> bool:
        if args.only and args.only.lower() not in c["id"].lower():
            return False
        if args.skip_todo and (
            c["id"].startswith("PLEASE_FILL")
            or "TODO" in (c.get("tags") or [])
        ):
            return False
        return True

    cases = [c for c in cases if _keep(c)]
    if not cases:
        print("no cases match the given filters")
        return 0
    print(f"Running {len(cases)} cases...")

    # Import late so logging config applies first
    from src.query_engine import QueryEngine
    engine = QueryEngine()

    rows = [_evaluate_case(engine, c) for c in cases]
    agg = _aggregate(rows)
    _print_report(rows, agg)

    if args.save:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        out = BASELINE_DIR / f"{args.save}.json"
        out.write_text(json.dumps({"agg": agg, "rows": rows}, indent=2))
        print(f"[saved] {out}")

    if args.compare:
        return _diff_against(rows, agg, args.compare)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
