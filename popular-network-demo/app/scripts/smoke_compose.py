"""Phase B.3 smoke — exercise POST /api/posts end-to-end.

Run with:  uv run python -m app.scripts.smoke_compose
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b3_"))
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
    from app.models import Approval, Post

    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client, SessionLocal, Approval, Post)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.3 (Compose) smoke green ✓")


def _run_assertions(client, SessionLocal, Approval, Post) -> None:
    boot_before = client.get("/api/bootstrap").json()
    posts_before = len(boot_before["posts"])
    approvals_before = len(boot_before["approvals"])

    # --- happy path: create a draft on fb ---
    r = client.post("/api/posts", json={
        "platform": "fb",
        "status": "draft",
        "title": "Test draft from Compose",
        "draft": "This is a Compose-created draft for the FB platform.",
    })
    if r.status_code != 200:
        _fail(f"POST draft → HTTP {r.status_code} {r.text}")
    data = r.json()
    if not data.get("ok") or not data.get("post"):
        _fail(f"POST draft missing ok/post: {data}")
    if data["post"]["status"] != "draft" or data["post"]["platform"] != "fb":
        _fail(f"POST draft wrong fields: {data['post']}")
    if "approval" in data:
        _fail("POST draft should NOT create an approval row")
    draft_post_id = data["post"]["internalId"]
    with SessionLocal() as s:
        p = s.get(Post, draft_post_id)
        if p is None or p.status != "draft" or p.title != "Test draft from Compose":
            _fail(f"draft post not persisted correctly: {p}")
    _ok(f"POST draft → Post created (id={draft_post_id}, status=draft, no approval)")

    # --- happy path: create a pending post on ig (with approval) ---
    r = client.post("/api/posts", json={
        "platform": "ig",
        "status": "pending",
        "title": "Test pending from Compose",
        "draft": "Pending IG post draft.",
        "reasoning": "Compose-supplied agent reasoning.",
    })
    if r.status_code != 200:
        _fail(f"POST pending → HTTP {r.status_code} {r.text}")
    data = r.json()
    if not data.get("approval"):
        _fail(f"POST pending must include approval in response: {data}")
    pending_post_id = data["post"]["internalId"]
    approval_id = data["approval"]["internalId"]
    with SessionLocal() as s:
        p = s.get(Post, pending_post_id)
        a = s.get(Approval, approval_id)
        if p.status != "pending":
            _fail(f"pending post wrong status: {p.status}")
        if a.kind != "post" or a.post_id != p.id or a.platform != "ig":
            _fail(f"approval not linked: kind={a.kind} post_id={a.post_id} platform={a.platform}")
        if a.note != "Compose-supplied agent reasoning.":
            _fail(f"approval.note didn't carry from reasoning: {a.note!r}")
    _ok(f"POST pending → Post id={pending_post_id} + linked Approval id={approval_id}")

    # --- web platform accepted (blog post) ---
    r = client.post("/api/posts", json={
        "platform": "web",
        "status": "draft",
        "title": "Test blog post",
        "draft": "Long-form blog content here.",
    })
    if r.status_code != 200:
        _fail(f"POST web draft → HTTP {r.status_code} {r.text}")
    _ok("POST web platform → accepted")

    # --- 422 on bogus platform ---
    r = client.post("/api/posts", json={
        "platform": "tiktok", "status": "draft", "title": "x", "draft": "y",
    })
    if r.status_code != 422:
        _fail(f"bogus platform expected 422, got {r.status_code}")
    _ok("POST platform='tiktok' → 422")

    # --- 422 on disallowed status ---
    r = client.post("/api/posts", json={
        "platform": "fb", "status": "approved", "title": "x", "draft": "y",
    })
    if r.status_code != 422:
        _fail(f"status='approved' on create expected 422, got {r.status_code}")
    _ok("POST status='approved' on create → 422 (lifecycle owned by approvals)")

    # --- 422 on empty title ---
    r = client.post("/api/posts", json={
        "platform": "fb", "status": "draft", "title": "  ", "draft": "y",
    })
    if r.status_code != 422:
        _fail(f"empty title expected 422, got {r.status_code}")
    _ok("POST title='  ' → 422")

    # --- 422 on bogus date ---
    r = client.post("/api/posts", json={
        "platform": "fb", "status": "draft", "title": "x", "draft": "y", "date": "yesterday",
    })
    if r.status_code != 422:
        _fail(f"bogus date expected 422, got {r.status_code}")
    _ok("POST date='yesterday' → 422")

    # --- bootstrap reflects the new posts + approval ---
    boot_after = client.get("/api/bootstrap").json()
    expected_posts = posts_before + 3   # fb draft + ig pending + web draft
    if len(boot_after["posts"]) != expected_posts:
        _fail(f"bootstrap posts: {len(boot_after['posts'])} (expected {expected_posts})")
    if len(boot_after["approvals"]) != approvals_before + 1:
        _fail(
            f"bootstrap approvals: {len(boot_after['approvals'])} "
            f"(expected {approvals_before + 1})"
        )
    _ok(
        f"/api/bootstrap → posts {posts_before}→{len(boot_after['posts'])}, "
        f"approvals {approvals_before}→{len(boot_after['approvals'])}"
    )

    # --- create_approval=False on pending suppresses approval row ---
    r = client.post("/api/posts", json={
        "platform": "gbp",
        "status": "pending",
        "title": "Pending without approval shim",
        "draft": "If create_approval is false, no Approval row is created.",
        "create_approval": False,
    })
    if r.status_code != 200:
        _fail(f"create_approval=false pending → HTTP {r.status_code} {r.text}")
    if "approval" in r.json():
        _fail(f"create_approval=false should not return approval: {r.json()}")
    _ok("POST pending with create_approval=false → no approval row")


if __name__ == "__main__":
    main()
