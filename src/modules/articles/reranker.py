"""Cross-encoder reranker for Phase 5 of the v2 rebuild.

Problem this solves:
    Dense bi-encoders (MiniLM) encode query and document SEPARATELY, so they
    can't see interactions between specific query terms and specific document
    phrases. BM25 can, but it's lexical-only. A cross-encoder reads the query
    and document TOGETHER and outputs a single relevance score — it can tell
    that "Did Kameron Koerner make state wrestling?" is a very strong match
    for "Koerner finishes 2-2 at state" and a weak match for "Koerner sets
    sights on state medal" (a pre-tournament preview), even though both
    articles mention Koerner + state + wrestling.

    Used as the SECOND pass after hybrid RRF retrieval. Hybrid gives us a
    solid top-20 candidate pool across semantic + lexical; the cross-encoder
    reorders that pool into a precision-optimized top-5.

Cost:
    ~120ms per query for 20 candidates on CPU. Free (runs locally). We already
    have sentence-transformers installed for MiniLM — the CrossEncoder API
    ships with it.

Model:
    cross-encoder/ms-marco-MiniLM-L-12-v2 — the 12-layer version of the MS-
    MARCO-trained reranker. ~30% smaller than the L-6 variant with better
    NDCG on news-ish domains. If eval ever shows a latency problem, switch to
    L-6 or move to a hosted reranker (Cohere Rerank v3, Voyage rerank-2).

Design choices:
    - Lazy model load. First retrieve() call pays the ~500ms init cost; every
      subsequent call is fast. We don't block QueryEngine startup on this.
    - Graceful fallback. If the model can't load (offline, disk full, HF
      rate limit, etc.), log a warning and return candidates unchanged.
      Reranker is a quality improvement, not a correctness requirement.
    - Reranker DOES NOT touch the RRF score. It computes its own
      `rerank_score` and sorts by that. Keeping both scores on the chunk
      makes eval-time debugging ("why did this move?") tractable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-12-v2"
_model: CrossEncoder | None = None  # type: ignore[assignment]
_load_failed = False


def _get_model() -> CrossEncoder | None:  # type: ignore[return-value]
    """Lazy-load the cross-encoder. Returns None if unavailable."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    try:
        from sentence_transformers import CrossEncoder
        logger.info(f"Loading cross-encoder reranker: {_MODEL_NAME}")
        _model = CrossEncoder(_MODEL_NAME)
        logger.info("Cross-encoder loaded")
        return _model
    except Exception as e:
        logger.warning(
            f"Cross-encoder load failed ({type(e).__name__}: {e}); "
            "skipping rerank step. Hybrid (dense+BM25) results will be used as-is."
        )
        _load_failed = True
        return None


def rerank(query: str, candidates: list[dict], top_k: int | None = None) -> list[dict]:
    """Rerank retrieval candidates using a cross-encoder.

    Args:
        query: the user's query as-entered.
        candidates: chunks from hybrid retrieval. Must have a "text" field.
        top_k: optional trim after rerank. None returns all candidates in
            reranked order.

    Returns:
        Same chunks in rerank order, each annotated with `rerank_score`.
        If the model can't load, returns candidates unchanged.
    """
    if not candidates:
        return candidates

    model = _get_model()
    if model is None:
        return candidates

    # Truncate each candidate text to ~400 words. Cross-encoders have a
    # 512-token limit (split between query and doc). News articles often
    # blow past that; the first 400 words cover the headline + nut graf,
    # which is where relevance is concentrated anyway.
    pairs: list[tuple[str, str]] = []
    for c in candidates:
        text = str(c.get("text", "") or "")
        # Prepend title so the cross-encoder sees it even if a body chunk
        # doesn't mention the main subject by name.
        title = str((c.get("metadata", {}) or {}).get("title", "") or "")
        doc_for_model = f"{title}\n\n{text}" if title else text
        # Truncate by words — rough proxy for tokens, close enough for 512 limit
        doc_for_model = " ".join(doc_for_model.split()[:400])
        pairs.append((query, doc_for_model))

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning(f"Cross-encoder predict failed: {e}; returning unchanged")
        return candidates

    for chunk, s in zip(candidates, scores):
        chunk["rerank_score"] = float(s)

    reranked = sorted(candidates, key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    if top_k is not None:
        reranked = reranked[:top_k]
    return reranked
