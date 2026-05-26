"""Phase B.1 smoke — exercise POST /api/approvals/{id}/decide end-to-end.

Per global CLAUDE.md test-before-handoff: this hits the real code path with
TestClient, asserts row-level state changes, and confirms /api/bootstrap
excludes decided rows.

Run with:  uv run python -m app.scripts.smoke_approvals

The smoke uses a temp SQLite file so it doesn't pollute the dev DB.
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


# Point the app at a temp DB BEFORE importing it, so seed runs into the temp.
_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def main() -> None:
    # Late imports — POPULAR_DB_PATH was set at module top, so importing now
    # picks up the temp file as the DB target.
    import shutil

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Approval, Post, Review

    # Context-manager form triggers FastAPI's startup hook → init_db + seed.
    with TestClient(app) as client:
        _run_assertions(client, SessionLocal, Approval, Post, Review)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.1 (Approvals) smoke green ✓")


def _run_assertions(client, SessionLocal, Approval, Post, Review) -> None:
    # /api/bootstrap should seed + return 4 approvals
    boot = client.get("/api/bootstrap").json()
    if len(boot["approvals"]) != 4:
        _fail(f"expected 4 seeded approvals, got {len(boot['approvals'])}")
    _ok(f"seeded approvals: {[a['id'] for a in boot['approvals']]}")

    # Map external→internal id by re-reading from DB (bootstrap exposes both)
    by_ext: dict[str, int] = {a["id"]: a["internalId"] for a in boot["approvals"]}
    posts_before = len(boot["posts"])

    # --- approve a1 (post-kind) → new Post materialized, status='approved' ---
    r = client.post(f"/api/approvals/{by_ext['a1']}/decide", json={"decision": "approve"})
    if r.status_code != 200:
        _fail(f"approve a1 → HTTP {r.status_code} {r.text}")
    data = r.json()
    if not data.get("ok") or not data.get("post"):
        _fail(f"approve a1 → missing post in response: {data}")
    new_post_id = data["post"]["internalId"]
    with SessionLocal() as s:
        p = s.get(Post, new_post_id)
        a = s.get(Approval, by_ext["a1"])
        if p is None or p.status != "approved":
            _fail(f"a1 → Post not created with status=approved (got {p})")
        if a.decision != "approved" or a.post_id != p.id:
            _fail(f"a1 → Approval row not flipped (decision={a.decision}, post_id={a.post_id})")
    _ok(f"approve a1 → new post '{data['post']['title']}' (id={new_post_id}), approval decision='approved'")

    # --- edit a2 (post-kind) → Post.draft uses edited text ---
    edited = "EDITED: 5 hours back per writer per week. Built by a publisher, for publishers. Quadd.ai."
    r = client.post(
        f"/api/approvals/{by_ext['a2']}/decide",
        json={"decision": "edit", "edited_draft": edited},
    )
    if r.status_code != 200:
        _fail(f"edit a2 → HTTP {r.status_code} {r.text}")
    p_id = r.json()["post"]["internalId"]
    with SessionLocal() as s:
        p = s.get(Post, p_id)
        a = s.get(Approval, by_ext["a2"])
        if p.draft != edited:
            _fail(f"a2 → Post.draft not edited (got {p.draft!r})")
        if a.decision != "edited" or a.draft != edited:
            _fail(f"a2 → Approval not marked edited (decision={a.decision}, draft={a.draft!r})")
    _ok(f"edit a2 → Post.draft + Approval.draft both rewritten")

    # --- edit a2 missing edited_draft → 422 ---
    # (a2 already decided, so use a fresh approval. Hit the validation before
    #  the already-decided check by using a3 with no edited_draft.)
    r = client.post(f"/api/approvals/{by_ext['a3']}/decide", json={"decision": "edit"})
    if r.status_code != 422:
        _fail(f"edit-without-draft expected 422, got {r.status_code} {r.text}")
    _ok("edit-without-edited_draft → 422")

    # --- reject a3 → no Post created, recoverable ---
    r = client.post(f"/api/approvals/{by_ext['a3']}/decide", json={"decision": "reject"})
    if r.status_code != 200:
        _fail(f"reject a3 → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        a = s.get(Approval, by_ext["a3"])
        if a.decision != "rejected":
            _fail(f"a3 → decision should be rejected, got {a.decision}")
        if a.post_id is not None:
            _fail(f"a3 → no Post should be linked on reject (got post_id={a.post_id})")
    _ok("reject a3 → no Post created, decision='rejected'")

    # --- approve a4 (review-kind) → linked Review.owner_response set ---
    r = client.post(f"/api/approvals/{by_ext['a4']}/decide", json={"decision": "approve"})
    if r.status_code != 200:
        _fail(f"approve a4 → HTTP {r.status_code} {r.text}")
    data = r.json()
    if not data.get("review"):
        _fail(f"a4 → missing review in response: {data}")
    with SessionLocal() as s:
        a = s.get(Approval, by_ext["a4"])
        review = s.get(Review, a.review_id) if a.review_id else None
        if review is None:
            _fail("a4 → review_id never linked")
        if review.response_status != "approved":
            _fail(f"a4 → review.response_status={review.response_status}")
        if review.author != "Citizen Publishing (early user)":
            _fail(f"a4 → linked to wrong review ({review.author})")
        if review.response_sent_at is None:
            _fail("a4 → review.response_sent_at not set")
    _ok(f"approve a4 → Citizen Publishing review marked approved + response saved")

    # --- 409 on second decide of already-decided row ---
    r = client.post(f"/api/approvals/{by_ext['a1']}/decide", json={"decision": "approve"})
    if r.status_code != 409:
        _fail(f"double-decide expected 409, got {r.status_code} {r.text}")
    _ok("re-decide same approval → 409 Conflict (idempotency guard)")

    # --- 404 on bogus id ---
    r = client.post("/api/approvals/99999/decide", json={"decision": "reject"})
    if r.status_code != 404:
        _fail(f"bogus id expected 404, got {r.status_code}")
    _ok("unknown approval id → 404")

    # --- 422 on bogus decision string ---
    r = client.post(f"/api/approvals/{by_ext['a1']}/decide", json={"decision": "yolo"})
    if r.status_code != 422:
        _fail(f"bogus decision expected 422, got {r.status_code}")
    _ok("invalid decision string → 422")

    # --- /api/bootstrap now excludes decided rows ---
    boot2 = client.get("/api/bootstrap").json()
    if boot2["approvals"]:
        _fail(f"bootstrap should have 0 pending approvals after all decided, got {len(boot2['approvals'])}")
    if len(boot2["posts"]) != posts_before + 2:
        _fail(
            f"posts should have grown by 2 (a1 approved + a2 edited), "
            f"got {len(boot2['posts'])} (was {posts_before})"
        )
    _ok(f"/api/bootstrap → 0 pending approvals, posts grew {posts_before} → {len(boot2['posts'])}")


if __name__ == "__main__":
    main()
