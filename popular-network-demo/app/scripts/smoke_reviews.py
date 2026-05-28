"""Phase B.5 smoke — exercise POST /api/reviews/{id}/respond end-to-end.

Run with:  uv run python -m app.scripts.smoke_reviews
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b5_"))
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
    from app.scripts._auth_helper import bootstrap_login
    from app.db import SessionLocal
    from app.models import Review

    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client, SessionLocal, Review)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.5 (Reviews) smoke green ✓")


def _run_assertions(client, SessionLocal, Review) -> None:
    boot = client.get("/api/bootstrap").json()
    pinned = boot["reviews"]["pinned"]
    if pinned is None or pinned.get("author") != "Citizen Publishing (early user)":
        _fail(f"pinned review should be Citizen Publishing testimonial, got {pinned}")
    if "internalId" not in pinned:
        _fail("bootstrap pinned review missing internalId")
    pinned_id = pinned["internalId"]
    _ok(f"bootstrap → pinned review Citizen Publishing (internalId={pinned_id})")

    # --- send action: marks approved + sets sent_at ---
    final_reply = "Thanks so much — mind if I write back to confirm permission to quote you? — Trevor"
    r = client.post(f"/api/reviews/{pinned_id}/respond", json={
        "response": final_reply, "action": "send",
    })
    if r.status_code != 200:
        _fail(f"send → HTTP {r.status_code} {r.text}")
    data = r.json()
    if data["review"]["responseStatus"] != "approved":
        _fail(f"send didn't set status approved: {data['review']}")
    if data["review"]["responseSentAt"] is None:
        _fail("send didn't set responseSentAt")
    with SessionLocal() as s:
        rev = s.get(Review, pinned_id)
        if rev.owner_response != final_reply:
            _fail(f"DB owner_response: {rev.owner_response!r}")
        if rev.response_status != "approved":
            _fail(f"DB response_status: {rev.response_status}")
        if rev.response_sent_at is None:
            _fail("DB response_sent_at not set")
    _ok("POST send → response_status='approved', sent_at set, response persisted")

    # --- save action (downgrade): clears approved, keeps sent_at history ---
    draft_reply = "DRAFT EDIT: working on the next version..."
    r = client.post(f"/api/reviews/{pinned_id}/respond", json={
        "response": draft_reply, "action": "save",
    })
    if r.status_code != 200:
        _fail(f"save → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        rev = s.get(Review, pinned_id)
        if rev.response_status != "draft":
            _fail(f"save didn't downgrade status: {rev.response_status}")
        if rev.owner_response != draft_reply:
            _fail(f"save didn't update response: {rev.owner_response!r}")
        if rev.response_sent_at is None:
            _fail("save shouldn't clear response_sent_at — preserves history")
    _ok("POST save → status='draft', response updated, sent_at preserved")

    # --- 422 on empty response ---
    r = client.post(f"/api/reviews/{pinned_id}/respond", json={"response": "  ", "action": "send"})
    if r.status_code != 422:
        _fail(f"empty response expected 422, got {r.status_code}")
    _ok("POST response='   ' → 422")

    # --- 422 on bogus action ---
    r = client.post(f"/api/reviews/{pinned_id}/respond", json={"response": "ok", "action": "delete"})
    if r.status_code != 422:
        _fail(f"bogus action expected 422, got {r.status_code}")
    _ok("POST action='delete' → 422")

    # --- 404 on bogus id ---
    r = client.post("/api/reviews/99999/respond", json={"response": "x", "action": "send"})
    if r.status_code != 404:
        _fail(f"bogus id expected 404, got {r.status_code}")
    _ok("POST unknown review id → 404")

    # --- bootstrap reflects current state (draft) ---
    boot2 = client.get("/api/bootstrap").json()
    pinned2 = boot2["reviews"]["pinned"]
    if pinned2["responseStatus"] != "draft":
        _fail(f"bootstrap pinned responseStatus: {pinned2['responseStatus']}")
    _ok("/api/bootstrap → pinned responseStatus='draft' visible after save")


if __name__ == "__main__":
    main()
