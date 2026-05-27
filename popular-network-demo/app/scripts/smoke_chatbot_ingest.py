"""Phase G smoke — publisher chatbot ingestion pipeline.

Run with:  uv run python -m app.scripts.smoke_chatbot_ingest

Covers:
1. Mint key via owner-side POST /api/chatbot/keys → raw key returned ONCE.
2. POST /api/chatbot/ingest with bad/missing key → 401.
3. POST /api/chatbot/ingest with good key → 200, row created, source='ingested'.
4. Re-POST same external_session_id → dedup (same conversationId, no dup row).
5. Auto-extractors: extracted topic mirrors the first consumer turn.
6. Sentiment: positive transcript → 'positive', negative → 'negative', neutral → 'neutral'.
7. Escalation: 'talk to a human' transcript → escalationFlag=True.
8. List keys shows the minted key + bumped useCount + lastUsedAt after ingests.
9. Revoke key → subsequent ingest with that key returns 401.
10. Tier-3 gate: a synthetic business at tier 2 with a key → 403 from /ingest.
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_g_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


# Sample transcripts used in the assertions.
_POSITIVE_TRANSCRIPT = [
    {"who": "consumer", "text": "Hi — does Universal Document Extractor handle court PDFs?"},
    {"who": "bot",      "text": "Yes — PDFs are the most common input."},
    {"who": "consumer", "text": "Perfect — that's exactly what I needed. Thanks!"},
]

_NEGATIVE_TRANSCRIPT = [
    {"who": "consumer", "text": "Honestly this is broken — I want a refund."},
    {"who": "bot",      "text": "I'm sorry to hear that."},
    {"who": "consumer", "text": "This is terrible, I want to cancel my subscription."},
]

_NEUTRAL_TRANSCRIPT = [
    {"who": "consumer", "text": "What's the pricing for a small weekly paper?"},
    {"who": "bot",      "text": "$99/month for the full bundle."},
    {"who": "consumer", "text": "OK, what's included at that price?"},
]

_HUMAN_REQUEST_TRANSCRIPT = [
    {"who": "consumer", "text": "I'd like to talk to a human about a custom pricing plan."},
    {"who": "bot",      "text": "Let me flag this for owner outreach."},
]


def main() -> None:
    import shutil
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        _run_assertions(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase G (chatbot ingestion) smoke green ✓")


def _run_assertions(client) -> None:
    # ---- 1. Mint key ----
    r = client.post("/api/chatbot/keys", json={"label": "Cottonwood County Citizen — production"})
    if r.status_code != 200:
        _fail(f"POST /api/chatbot/keys → {r.status_code} {r.text}")
    minted = r.json()
    for k in ("id", "keyPrefix", "rawKey"):
        if k not in minted:
            _fail(f"/api/chatbot/keys response missing '{k}'")
    raw_key = minted["rawKey"]
    if not raw_key.startswith("cbk_live_"):
        _fail(f"rawKey doesn't have expected prefix: {raw_key[:16]}…")
    if minted["keyPrefix"] != raw_key[:12]:
        _fail(f"keyPrefix mismatch: {minted['keyPrefix']} vs raw[:12]={raw_key[:12]}")
    _ok(f"Minted key id={minted['id']} prefix='{minted['keyPrefix']}'")

    # ---- 2. Auth failures ----
    r = client.post("/api/chatbot/ingest", json={"external_session_id": "sess-auth-1", "transcript": _POSITIVE_TRANSCRIPT})
    if r.status_code != 401:
        _fail(f"Missing header should 401, got {r.status_code}: {r.text}")

    r = client.post("/api/chatbot/ingest",
                    headers={"X-Amplafai-Key": "wrong_key_format"},
                    json={"external_session_id": "sess-auth-2", "transcript": _POSITIVE_TRANSCRIPT})
    if r.status_code != 401:
        _fail(f"Malformed key should 401, got {r.status_code}: {r.text}")

    r = client.post("/api/chatbot/ingest",
                    headers={"X-Amplafai-Key": "cbk_live_deadbeef" + "0" * 24},
                    json={"external_session_id": "sess-auth-3", "transcript": _POSITIVE_TRANSCRIPT})
    if r.status_code != 401:
        _fail(f"Unknown key should 401, got {r.status_code}: {r.text}")
    _ok("Auth: missing/malformed/unknown all → 401")

    # ---- 3. Happy-path ingest ----
    r = client.post("/api/chatbot/ingest",
                    headers={"X-Amplafai-Key": raw_key},
                    json={
                        "external_session_id": "sess-pos-1",
                        "transcript": _POSITIVE_TRANSCRIPT,
                        "referrer_label": "cottonwoodccitizen.com → article",
                        "consumer_label": "anonymous · iPhone Safari",
                    })
    if r.status_code != 200:
        _fail(f"Happy-path ingest → {r.status_code}: {r.text}")
    posted = r.json()
    if not posted["ok"] or not posted["created"]:
        _fail(f"Expected ok+created on first ingest: {posted}")
    pos_conv_id = posted["conversationId"]
    _ok(f"Ingest created conversationId={pos_conv_id} sentiment={posted['sentiment']}")

    # Verify the row reads back with source='ingested'.
    convo = client.get(f"/api/chatbot/conversations/{pos_conv_id}").json()
    if convo.get("source") != "ingested":
        _fail(f"Expected source='ingested', got {convo.get('source')}")
    if convo["sentiment"] != "positive":
        _fail(f"Expected sentiment=positive on positive transcript, got {convo['sentiment']}")
    _ok(f"Round-trip: source='ingested', sentiment='positive' on positive transcript")

    # ---- 4. Dedup ----
    r2 = client.post("/api/chatbot/ingest",
                     headers={"X-Amplafai-Key": raw_key},
                     json={"external_session_id": "sess-pos-1", "transcript": _POSITIVE_TRANSCRIPT})
    if r2.status_code != 200:
        _fail(f"Re-ingest → {r2.status_code}: {r2.text}")
    second = r2.json()
    if second["created"]:
        _fail(f"Re-ingest should not set created=True: {second}")
    if second["conversationId"] != pos_conv_id:
        _fail(f"Re-ingest returned different id: {second['conversationId']} vs {pos_conv_id}")
    _ok(f"Dedup: re-POST same external_session_id → same conversationId (no duplicate row)")

    # ---- 5 + 6. Sentiment matrix ----
    neg_r = client.post("/api/chatbot/ingest",
                        headers={"X-Amplafai-Key": raw_key},
                        json={"external_session_id": "sess-neg-1", "transcript": _NEGATIVE_TRANSCRIPT})
    neu_r = client.post("/api/chatbot/ingest",
                        headers={"X-Amplafai-Key": raw_key},
                        json={"external_session_id": "sess-neu-1", "transcript": _NEUTRAL_TRANSCRIPT})
    if neg_r.json()["sentiment"] != "negative":
        _fail(f"Negative transcript → expected 'negative', got {neg_r.json()['sentiment']}")
    if neu_r.json()["sentiment"] != "neutral":
        _fail(f"Neutral transcript → expected 'neutral', got {neu_r.json()['sentiment']}")
    _ok(f"Sentiment matrix: pos/neg/neu transcripts → expected labels")

    # ---- 7. Escalation ----
    esc_r = client.post("/api/chatbot/ingest",
                        headers={"X-Amplafai-Key": raw_key},
                        json={"external_session_id": "sess-esc-1", "transcript": _HUMAN_REQUEST_TRANSCRIPT})
    if not esc_r.json().get("escalationFlag"):
        _fail(f"'talk to a human' should fire escalation: {esc_r.json()}")
    if not esc_r.json().get("escalationReason"):
        _fail(f"Escalation should include a human-readable reason: {esc_r.json()}")
    _ok(f"Escalation: 'talk to a human' → flag=True, reason='{esc_r.json()['escalationReason']}'")

    # Topic — first consumer turn becomes the topic label.
    pos_convo = client.get(f"/api/chatbot/conversations/{pos_conv_id}").json()
    if "Universal Document Extractor" not in pos_convo["topicLabel"]:
        _fail(f"Topic should mirror first consumer turn, got '{pos_convo['topicLabel']}'")
    _ok(f"Topic extracted from first consumer turn: '{pos_convo['topicLabel'][:60]}…'")

    # ---- 8. List keys shows bumped useCount + lastUsedAt ----
    keys = client.get("/api/chatbot/keys").json()
    if len(keys) != 1:
        _fail(f"Expected 1 key, got {len(keys)}")
    key_row = keys[0]
    if key_row["useCount"] < 5:  # 1 dedup + 1 + 1 + 1 + 1 = 5 successful ingests
        _fail(f"useCount should reflect successful ingests, got {key_row['useCount']}")
    if not key_row["lastUsedAt"]:
        _fail(f"lastUsedAt should be set after ingests")
    _ok(f"Key list: useCount={key_row['useCount']}, lastUsedAt set")

    # ---- 9. Revoke key ----
    r = client.delete(f"/api/chatbot/keys/{minted['id']}")
    if r.status_code != 200:
        _fail(f"DELETE /api/chatbot/keys/{minted['id']} → {r.status_code}: {r.text}")
    r = client.post("/api/chatbot/ingest",
                    headers={"X-Amplafai-Key": raw_key},
                    json={"external_session_id": "sess-after-revoke", "transcript": _POSITIVE_TRANSCRIPT})
    if r.status_code != 401:
        _fail(f"Ingest with revoked key should 401, got {r.status_code}: {r.text}")
    _ok(f"Revoke: key revoked → subsequent ingest 401")

    # ---- 10. Tier-3 gate ----
    # Mint a new key for a synthetic Tier 2 business and confirm /ingest returns 403.
    from app.db import SessionLocal
    from app.models import Business, ChatbotIngestionKey
    import hashlib, secrets

    raw2 = "cbk_live_" + secrets.token_hex(16)
    prefix2 = raw2[:12]
    hash2 = hashlib.sha256(raw2.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        biz = Business(
            slug="tier2_test", name="Tier 2 test biz", owner="t", owner_initials="t",
            location="x", publisher="x", phone="x", tier=2, tier_label="Tier 2",
            monthly_price=75, joined_days_ago=0, joined_date="today", voice_interview="—",
        )
        db.add(biz)
        db.flush()
        db.add(ChatbotIngestionKey(business_id=biz.id, label="tier2 key",
                                   key_prefix=prefix2, key_hash=hash2))
        db.commit()
        tier2_biz_id = biz.id

    r = client.post("/api/chatbot/ingest",
                    headers={"X-Amplafai-Key": raw2},
                    json={"external_session_id": "tier2-s1", "transcript": _POSITIVE_TRANSCRIPT})
    if r.status_code != 403:
        _fail(f"Tier 2 ingest should 403, got {r.status_code}: {r.text}")
    _ok(f"Tier gate: tier-2 business ingest → 403")


if __name__ == "__main__":
    main()
