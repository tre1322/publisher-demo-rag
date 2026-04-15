"""SQLite FTS5 lexical search for articles (Phase 3 — hybrid retrieval).

Why this exists:
    Dense embeddings (all-MiniLM-L6-v2) smear rare proper nouns into "similar-
    sounding" space. "Marshall Invite" (a specific speech-team competition)
    gets projected near other generic competition language, so the right
    article ranks at 20+ while irrelevant chunks crowd the top. BM25 over
    full article text recovers rare-term matches that dense loses.

    The v2 retrieval pipeline runs BOTH retrievers and fuses their rankings
    with Reciprocal Rank Fusion (RRF). RRF doesn't need score calibration
    between retrievers — it works purely on rank position, which is why it
    shows up repeatedly in recent retrieval literature as the "just-use-this"
    default.

Design choices:
    - Standalone FTS5 table (not `content=` + content_rowid), because the
      articles table uses TEXT doc_id as primary key, not an integer rowid.
      Trade-off: we duplicate the text into the FTS index. Cost at 460 rows
      is negligible; at 50K it's still under 1 GB.
    - Title is weighted (stored as its own column) so "exactly the title"
      matches rank higher than "mentioned in body". FTS5 column-weighted bm25
      handles this automatically.
    - Publisher is stored as an unindexed column so we can filter results by
      publisher without running a second join. Saves a JOIN per query.
    - Rebuild-from-scratch is the supported sync path. Incremental triggers
      are nice but add a failure surface (trigger on article insert during
      ingestion — if FTS fails, does the article fail too?). Rebuild is
      called at startup and after each ingestion batch; takes <1s at current
      scale.

Returned rank is 1-indexed to match how `_first_expected_rank` reports in
the eval harness. Score (BM25) is also returned for debug logging but NOT
used in RRF — RRF operates on rank position only.
"""

from __future__ import annotations

import logging
import sqlite3

from src.modules.articles.database import get_connection

logger = logging.getLogger(__name__)

_FTS_TABLE = "articles_fts"


def ensure_fts_table(conn: sqlite3.Connection | None = None) -> None:
    """Create the FTS5 virtual table if missing. Idempotent."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
                doc_id UNINDEXED,
                publisher UNINDEXED,
                title,
                body,
                tokenize = 'porter unicode61'
            )
            """
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


def rebuild_fts(conn: sqlite3.Connection | None = None) -> int:
    """Wipe and rebuild the FTS index from the canonical articles table.

    Returns the number of rows indexed. Called from:
        - application startup (lazy, once)
        - ingestion completion (after a batch of new articles lands)

    Safe to call repeatedly; completes in well under a second at 460 rows.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        ensure_fts_table(conn)
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {_FTS_TABLE}")
        # Prefer cleaned_text (post-OCR cleanup) when available; fall back to
        # full_text. If both are empty, skip — indexing an empty doc adds noise.
        cur.execute(
            """
            SELECT doc_id,
                   COALESCE(publisher, ''),
                   COALESCE(title, ''),
                   COALESCE(NULLIF(cleaned_text, ''), NULLIF(full_text, ''), '')
            FROM articles
            """
        )
        rows = cur.fetchall()
        inserted = 0
        for doc_id, publisher, title, body in rows:
            if not (title or body):
                continue
            cur.execute(
                f"INSERT INTO {_FTS_TABLE} (doc_id, publisher, title, body) "
                "VALUES (?, ?, ?, ?)",
                (doc_id, publisher, title, body),
            )
            inserted += 1
        conn.commit()
        logger.info(f"FTS5 rebuild complete: {inserted} articles indexed")
        return inserted
    finally:
        if owns_conn:
            conn.close()


def _sanitize_fts_query(query: str) -> str:
    """Turn a natural-language query into an FTS5 MATCH expression.

    FTS5 MATCH is not free-form — unquoted bareword queries work BUT special
    characters like apostrophes, quotes, parens, and dashes break the parser.
    The safe default is to extract alphanumeric tokens, then OR them together.
    "Did Kameron Koerner make state?" -> Kameron OR Koerner OR make OR state

    We drop 1-2 character tokens (noise: "a", "I", "to", "in") to keep the
    match precise without building a full stoplist.
    """
    import re
    tokens = re.findall(r"[A-Za-z0-9]+", query or "")
    kept = [t for t in tokens if len(t) >= 3]
    if not kept:
        return ""
    # Quote each token so any that happen to be FTS5 reserved words (e.g.
    # "NEAR", "AND", "OR" in lowercase slip past but caps don't) are literal.
    return " OR ".join(f'"{t}"' for t in kept)


def lexical_search(
    query: str,
    publisher: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Run BM25 over the FTS5 index. Returns ranked doc_ids with scores.

    Args:
        query: Natural-language user query. Sanitized to FTS5 syntax.
        publisher: Optional exact-match publisher filter.
        limit: Max results.

    Returns:
        [{"doc_id": str, "rank": int (1-indexed), "score": float, "title": str}, ...]
    """
    match_expr = _sanitize_fts_query(query)
    if not match_expr:
        return []

    conn = get_connection()
    try:
        ensure_fts_table(conn)
        cur = conn.cursor()
        sql = (
            f"SELECT doc_id, title, bm25({_FTS_TABLE}) AS score "
            f"FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ?"
        )
        params: list = [match_expr]
        if publisher:
            sql += " AND publisher = ?"
            params.append(publisher)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        try:
            cur.execute(sql, params)
        except sqlite3.OperationalError as e:
            # Malformed MATCH — degrade gracefully. Common when user types
            # something the sanitizer couldn't fully tame.
            logger.warning(f"FTS5 query failed (MATCH={match_expr!r}): {e}")
            return []
        results = []
        for rank, (doc_id, title, score) in enumerate(cur.fetchall(), start=1):
            results.append({
                "doc_id": doc_id,
                "title": title,
                "score": float(score),
                "rank": rank,
            })
        return results
    finally:
        conn.close()


def reciprocal_rank_fusion(
    dense_chunks: list[dict],
    lexical_doc_ids_ranked: list[dict],
    k_dense: int = 60,
    k_lex: int = 10,
) -> list[dict]:
    """Fuse dense-chunk ranking and lexical-article ranking with asymmetric RRF.

    RRF score for each chunk = 1/(k_dense + dense_rank) + 1/(k_lex + lex_rank).

    Why asymmetric k? The textbook RRF paper uses one k=60 shared by both
    retrievers. That works when both retrievers return comparably-ranked
    lists. In a news-archive product it does not:

        - Dense (MiniLM) returns 20 items with noisy "similar-ish" chunks.
          Its top-1 is often a near-match; the tail is padding.
        - BM25 over full article text is rank-1-dominant: when it finds a
          real keyword hit, the top article is almost always the right one.
          Lower ranks drop off fast.

    With k=60 everywhere, a lexical rank-1 article that dense missed gets
    1/61 = 0.0164. But a dense rank-1 + lex rank-6 doc gets
    1/61 + 1/66 = 0.031. The "dense-agrees-slightly" chunk beats the
    "lexical-absolutely-certain" chunk, and we lose the exact article the
    user asked about. That's the Marshall Invite failure mode.

    k_lex=10 sharpens the lexical curve — rank-1 is now worth 1/11 = 0.091,
    enough to overcome dense-dominant middle ranks. Rank-10 is still just
    1/20 = 0.05, so spurious low-rank BM25 hits don't swamp the list.

    Dense and lexical operate at different granularities:
        - Dense returns CHUNKS (a chunk is a sub-section of an article).
        - Lexical returns ARTICLES (indexed whole article body + title).

    We fuse at the chunk level: a lexical match on an article contributes its
    rank to every chunk of that article.
    """
    # doc_id -> lexical rank (1-indexed)
    lex_rank_by_doc: dict[str, int] = {}
    for item in lexical_doc_ids_ranked:
        did = item.get("doc_id")
        if did and did not in lex_rank_by_doc:
            lex_rank_by_doc[did] = item["rank"]

    # Score each dense chunk
    fused: list[dict] = []
    seen_dense_doc_chunks: set[tuple[str, int]] = set()
    for dense_rank, chunk in enumerate(dense_chunks, start=1):
        md = chunk.get("metadata", {}) or {}
        doc_id = str(md.get("doc_id", "") or "")
        chunk_index = int(md.get("chunk_index", 0) or 0)
        key = (doc_id, chunk_index)
        if key in seen_dense_doc_chunks:
            continue
        seen_dense_doc_chunks.add(key)

        score = 1.0 / (k_dense + dense_rank)
        lex_rank = lex_rank_by_doc.get(doc_id)
        if lex_rank is not None:
            score += 1.0 / (k_lex + lex_rank)

        # Clone with an added rrf_score so downstream observability logs
        # can see why a chunk ranked where it did.
        new_chunk = dict(chunk)
        new_chunk["rrf_score"] = score
        new_chunk["dense_rank"] = dense_rank
        new_chunk["lex_rank"] = lex_rank  # None if not matched lexically
        fused.append(new_chunk)

    fused.sort(key=lambda c: c["rrf_score"], reverse=True)
    return fused


def fetch_lexical_only_chunks(
    dense_chunks: list[dict],
    lexical_doc_ids_ranked: list[dict],
    collection,
    query_embedding: list[float],
    publisher: str | None = None,
    k_lex: int = 10,
    max_lexical_only: int = 5,
) -> list[dict]:
    """Pull chunks for FTS-matched doc_ids that dense missed entirely.

    This is the mechanism that makes Phase 3 fix `historical_speech_team`:
    "WAS speech team places fifth at Marshall Invite" isn't in dense top-20
    because the embedder smears "Marshall Invite" into generic-competition
    space. BM25 finds it at rank 1. This function reaches into Chroma and
    pulls that article's best chunk so RRF has something to fuse.

    Args:
        dense_chunks: already-retrieved dense chunks.
        lexical_doc_ids_ranked: FTS5 result rows (dicts with doc_id, rank).
        collection: ChromaDB articles collection.
        query_embedding: the query embedding (for ranking chunks-within-doc).
        publisher: optional filter — don't cross publisher scope here.
        k: RRF constant, passed through so synthesized chunks get a
           consistent rrf_score shape.
        max_lexical_only: cap to avoid blowing up retrieval latency when
           FTS is too generous.

    Returns:
        List of chunks with `rrf_score`, `dense_rank=None`, `lex_rank=N`
        ready to merge into the fused pool.
    """
    dense_doc_ids = {
        str((c.get("metadata", {}) or {}).get("doc_id", "") or "")
        for c in dense_chunks
    }
    # Lexical matches the dense retriever didn't surface
    missing = [
        item for item in lexical_doc_ids_ranked
        if item["doc_id"] and item["doc_id"] not in dense_doc_ids
    ][:max_lexical_only]
    if not missing:
        return []

    out: list[dict] = []
    for item in missing:
        doc_id = item["doc_id"]
        where: dict = {"doc_id": doc_id}
        if publisher:
            # Chroma requires $and for multiple predicates
            where = {"$and": [{"doc_id": doc_id}, {"publisher": publisher}]}
        try:
            res = collection.query(
                query_embeddings=[query_embedding],
                n_results=1,  # just the best chunk per article
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning(f"lexical-only Chroma fetch failed for {doc_id}: {e}")
            continue
        if not res or not res.get("documents") or not res["documents"][0]:
            # Article is in SQLite but has no chunks in Chroma — a sync
            # gap worth logging but not worth crashing over.
            logger.warning(
                f"FTS matched {doc_id} but Chroma has no chunks — possible "
                "ingestion sync gap"
            )
            continue
        doc = res["documents"][0][0]
        md = res["metadatas"][0][0] if res["metadatas"] else {}
        dist = res["distances"][0][0] if res["distances"] else 1.0
        sim = 1 - dist
        rrf = 1.0 / (k_lex + item["rank"])
        out.append({
            "text": doc,
            "metadata": md,
            "score": sim,
            "rrf_score": rrf,
            "dense_rank": None,
            "lex_rank": item["rank"],
        })
    return out
