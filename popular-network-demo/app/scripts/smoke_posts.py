"""Phase B.2 smoke — exercise PUT /api/posts/{id} end-to-end.

Run with:  uv run python -m app.scripts.smoke_posts
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

# Isolate from the dev DB — point at a fresh temp file BEFORE importing the app.
_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b2_"))
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
    from app.db import SessionLocal
    from app.models import Post

    with TestClient(app) as client:
        _run_assertions(client, SessionLocal, Post)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.2 (Posts edit-in-place) smoke green ✓")


def _run_assertions(client, SessionLocal, Post) -> None:
    boot = client.get("/api/bootstrap").json()
    posts = boot["posts"]
    if len(posts) < 5:
        _fail(f"expected >= 5 seeded posts, got {len(posts)}")
    # Confirm bootstrap exposes internalId for posts
    if "internalId" not in posts[0]:
        _fail("bootstrap post payload missing internalId")
    _ok(f"bootstrap → {len(posts)} posts with internalId exposed")

    # Find a draft post we can mutate
    draft = next((p for p in posts if p["status"] == "draft"), None)
    if draft is None:
        _fail("no draft post in seed — smoke needs a non-published row")
    draft_id = draft["internalId"]
    original_title = draft["title"]
    original_draft = draft["draft"]
    original_date = draft["date"]

    # --- happy path: edit title + draft + date ---
    new_title = "EDITED " + original_title
    new_draft = "EDITED " + original_draft
    new_date = "2026-06-15"
    r = client.put(
        f"/api/posts/{draft_id}",
        json={"title": new_title, "draft": new_draft, "date": new_date},
    )
    if r.status_code != 200:
        _fail(f"PUT happy path → HTTP {r.status_code} {r.text}")
    body = r.json()
    if not body.get("ok") or not body.get("post"):
        _fail(f"happy path response missing ok/post: {body}")
    if body["post"]["title"] != new_title:
        _fail(f"server response title mismatch: {body['post']['title']!r}")
    with SessionLocal() as s:
        p = s.get(Post, draft_id)
        if p.title != new_title or p.draft != new_draft or p.date != new_date:
            _fail(f"DB state didn't persist update: title={p.title!r} date={p.date!r}")
    _ok(f"PUT title+draft+date → row persisted ({original_title[:30]} → {new_title[:30]})")

    # --- partial update: only date ---
    r = client.put(f"/api/posts/{draft_id}", json={"date": "2026-06-20"})
    if r.status_code != 200:
        _fail(f"partial date update → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        p = s.get(Post, draft_id)
        if p.date != "2026-06-20":
            _fail(f"partial date update didn't stick: {p.date}")
        if p.title != new_title:
            _fail(f"partial update wiped title — should have left it: {p.title}")
    _ok("PUT date-only → leaves other fields intact")

    # --- 422 on empty body ---
    r = client.put(f"/api/posts/{draft_id}", json={})
    if r.status_code != 422:
        _fail(f"empty body expected 422, got {r.status_code}")
    _ok("PUT empty body → 422")

    # --- 422 on bogus date ---
    r = client.put(f"/api/posts/{draft_id}", json={"date": "not-a-date"})
    if r.status_code != 422:
        _fail(f"bogus date expected 422, got {r.status_code}")
    _ok("PUT date='not-a-date' → 422")

    # --- 422 on impossible date (Feb 30) ---
    r = client.put(f"/api/posts/{draft_id}", json={"date": "2026-02-30"})
    if r.status_code != 422:
        _fail(f"impossible date expected 422, got {r.status_code}")
    _ok("PUT date='2026-02-30' → 422")

    # --- 422 on bogus platform ---
    r = client.put(f"/api/posts/{draft_id}", json={"platform": "tiktok"})
    if r.status_code != 422:
        _fail(f"bogus platform expected 422, got {r.status_code}")
    _ok("PUT platform='tiktok' → 422 (not in allowed set)")

    # --- 422 on empty title string ---
    r = client.put(f"/api/posts/{draft_id}", json={"title": "   "})
    if r.status_code != 422:
        _fail(f"whitespace-only title expected 422, got {r.status_code}")
    _ok("PUT title='   ' → 422")

    # --- 404 on bogus id ---
    r = client.put("/api/posts/99999", json={"title": "ghost"})
    if r.status_code != 404:
        _fail(f"bogus id expected 404, got {r.status_code}")
    _ok("PUT unknown id → 404")

    # --- 409 on attempting to edit a published post ---
    published = next((p for p in posts if p["status"] == "published"), None)
    if published is None:
        _fail("no published post in seed — smoke needs one to test immutability")
    r = client.put(
        f"/api/posts/{published['internalId']}",
        json={"title": "trying to rewrite history"},
    )
    if r.status_code != 409:
        _fail(f"editing published post expected 409, got {r.status_code} {r.text}")
    _ok(f"PUT published post → 409 Conflict (immutability guard)")

    # --- bootstrap reflects the update ---
    boot2 = client.get("/api/bootstrap").json()
    updated = next((p for p in boot2["posts"] if p["internalId"] == draft_id), None)
    if updated is None:
        _fail("updated post fell out of bootstrap")
    if updated["title"] != new_title:
        _fail(f"bootstrap title not updated: {updated['title']!r}")
    if updated["date"] != "2026-06-20":
        _fail(f"bootstrap date not updated: {updated['date']!r}")
    _ok(f"/api/bootstrap → updated post visible ({updated['title'][:30]} @ {updated['date']})")


if __name__ == "__main__":
    main()
