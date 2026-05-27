"""Phase F.3 smoke — Tier 3+ Chatbot Preview backend.

Run with:  uv run python -m app.scripts.smoke_chatbot

Covers:
1. /api/chatbot returns empty Day-1 shape (Quadd just enrolled).
2. POST /api/chatbot/seed-fixtures creates ~10 fixture conversations.
3. /api/chatbot reflects the seeded count + escalation count + sentiment counts.
4. /api/chatbot/conversations filters: sentiment, escalation_only, search.
5. /api/chatbot/conversations/{id} returns full transcript w/ turns.
6. Re-seeding is idempotent (wipes prior fixture rows).
7. DELETE /api/chatbot/fixtures clears seeded rows.
8. Top-topics list returned non-empty after seed.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_f3_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def main() -> None:
    import shutil
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        _run_assertions(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase F.3 (Chatbot Preview) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- 1. Day-1 empty shape ----
    r = client.get("/api/chatbot")
    if r.status_code != 200:
        _fail(f"GET /api/chatbot → {r.status_code} {r.text}")
    cb = r.json()
    for k in ("tier", "tierEligible", "conversations", "totals", "topTopics", "hasAnyConversation"):
        if k not in cb:
            _fail(f"/api/chatbot missing '{k}'")
    if not cb["tierEligible"]:
        _fail(f"Quadd at tier 4 → tierEligible should be True: {cb}")
    if cb["hasAnyConversation"] or cb["totals"]["count"] != 0:
        _fail(f"Day-1 chatbot should be empty: {cb['totals']}")
    _ok(f"Day-1 /api/chatbot → tier 4 eligible, 0 conversations (empty state)")

    # ---- 2. Seed fixtures ----
    r = client.post("/api/chatbot/seed-fixtures")
    if r.status_code != 200:
        _fail(f"POST seed-fixtures → {r.status_code} {r.text}")
    seed = r.json()
    if seed["createdCount"] < 8:
        _fail(f"Seed should create at least 8 fixtures, got {seed['createdCount']}")
    _ok(f"POST seed-fixtures → createdCount={seed['createdCount']}")

    # ---- 3. /api/chatbot reflects seeded state ----
    r = client.get("/api/chatbot").json()
    if r["totals"]["count"] != seed["createdCount"]:
        _fail(f"totals.count mismatch: {r['totals']['count']} vs {seed['createdCount']}")
    if r["totals"]["escalationCount"] < 2:
        _fail(f"Should have multiple escalation flags in fixtures: {r['totals']['escalationCount']}")
    sentiment = r["totals"]["sentimentCounts"]
    if sentiment.get("positive", 0) < 3 or sentiment.get("neutral", 0) < 1:
        _fail(f"Fixture sentiment distribution unexpected: {sentiment}")
    _ok(f"Seeded state: {r['totals']['count']} convos, {r['totals']['escalationCount']} flagged, sentiment={sentiment}")

    # ---- 4. Filters ----
    r = client.get("/api/chatbot/conversations?sentiment=positive").json()
    if not r or any(c["sentiment"] != "positive" for c in r):
        _fail(f"sentiment=positive filter broken: {[c['sentiment'] for c in r]}")

    r = client.get("/api/chatbot/conversations?escalation_only=true").json()
    if not r or any(not c["escalationFlag"] for c in r):
        _fail(f"escalation_only filter broken: {[c['escalationFlag'] for c in r]}")

    r = client.get("/api/chatbot/conversations?search=pricing").json()
    if not r:
        _fail(f"search=pricing should match the pricing fixture")
    _ok(f"Filters: sentiment, escalation_only, search all working")

    # ---- 5. Full conversation w/ transcript ----
    convos = client.get("/api/chatbot/conversations").json()
    convo_id = convos[0]["id"]
    r = client.get(f"/api/chatbot/conversations/{convo_id}").json()
    if "transcript" not in r or not r["transcript"]:
        _fail(f"Detail endpoint should return non-empty transcript: {r}")
    first_turn = r["transcript"][0]
    for k in ("who", "text", "at"):
        if k not in first_turn:
            _fail(f"Transcript turn missing '{k}': {first_turn}")
    _ok(f"GET /api/chatbot/conversations/{convo_id} → {len(r['transcript'])} turns, topic='{r['topicLabel'][:48]}'")

    # ---- 6. Re-seed is idempotent ----
    r = client.post("/api/chatbot/seed-fixtures").json()
    after = client.get("/api/chatbot").json()
    if after["totals"]["count"] != r["createdCount"]:
        _fail(f"Re-seed didn't wipe-and-re-import: now={after['totals']['count']} vs createdCount={r['createdCount']}")
    _ok(f"Re-seed idempotent (count stays at {r['createdCount']}, not doubled)")

    # ---- 7. DELETE fixtures ----
    r = client.delete("/api/chatbot/fixtures")
    if r.status_code != 200 or r.json()["removedCount"] != after["totals"]["count"]:
        _fail(f"DELETE fixtures didn't remove all: {r.json()}")
    r = client.get("/api/chatbot").json()
    if r["totals"]["count"] != 0:
        _fail(f"After DELETE fixtures, count should be 0: {r['totals']}")
    _ok(f"DELETE fixtures → wiped (count back to 0)")

    # ---- 8. Top topics non-empty after re-seed ----
    client.post("/api/chatbot/seed-fixtures")
    r = client.get("/api/chatbot").json()
    if not r["topTopics"]:
        _fail("topTopics should be non-empty after seed")
    _ok(f"topTopics → {len(r['topTopics'])} entries (top: '{r['topTopics'][0]['label'][:48]}')")


if __name__ == "__main__":
    main()
