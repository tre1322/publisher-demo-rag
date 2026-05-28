"""Smoke for POST /api/compose/redraft — "Re-draft from brief" wiring.

Mocks the Anthropic client so the test runs offline. Verifies:
  - Forced tool_choice round-trip returns drafts for each requested platform
  - Server filters out platforms the model returned but the owner didn't ask for
  - Validation: empty platforms → 422, unknown platform → 422, missing topic → 422
  - Missing ANTHROPIC_API_KEY → 503
  - Model emitting no tool_use block → 502
  - System prompt (voice brief) flows through the messages.create call

Run with:  uv run python -m app.scripts.smoke_compose_redraft
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_compose_redraft_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "smoke-test-key")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


# --------------------------------------------------------------------------- #
# Anthropic mock — queue of pre-baked responses for messages.create.
# --------------------------------------------------------------------------- #


class _FakeMessages:
    def __init__(self) -> None:
        self.queue: list = []
        self.calls: list[dict] = []

    def queue_tool_use(self, tool_input: dict) -> None:
        msg = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="compose_drafts",
                    id="toolu_smoke_1",
                    input=tool_input,
                )
            ],
            usage=SimpleNamespace(input_tokens=42, output_tokens=200),
        )
        self.queue.append(msg)

    def queue_text_only(self, text: str) -> None:
        """Model emitted no tool_use block — endpoint should 502."""
        msg = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=42, output_tokens=10),
        )
        self.queue.append(msg)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queue:
            raise AssertionError("FakeMessages.create called with empty queue")
        return self.queue.pop(0)


class _FakeAnthropic:
    _shared = _FakeMessages()

    def __init__(self, *a, **kw) -> None:
        self.messages = _FakeAnthropic._shared


_FAKE = _FakeAnthropic._shared


def _quadd_drafts() -> dict:
    """A plausible Quadd response across all four platforms."""
    return {
        "fb": {
            "text": "A Citizen Publishing writer is saving 2-3 hours every week with Quadd.ai. Universal Document Extractor. 7-day free trial.",
            "cta": "Start free trial",
        },
        "ig": {
            "caption": "Re-typing court documents? Built by a publisher, for publishers. Quadd.ai gives newsroom staff back 2-3 hours every week.",
            "hashtags": "#NewsroomTools #JournalismLife #LocalNews",
        },
        "gbp": {
            "headline": "Quadd.ai · 7-day free trial",
            "text": "AI tools built for newspaper publishers, by a publisher.",
            "cta": "Learn more",
        },
        "web": {
            "title": "Why publishers love the Universal Document Extractor",
            "subtitle": "Built by a 28-year publisher who got tired of re-typing court documents.",
            "body": "A writer at Citizen Publishing put the Universal Document Extractor to work and got 2-3 hours every week back.\n\nThe 38-page court filing problem becomes a non-event.\n\n7-day free trial, no card.",
            "slug": "/blog/universal-document-extractor",
            "readTime": "3 min",
        },
    }


def main() -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.scripts._auth_helper import bootstrap_login
    from app.db import SessionLocal
    from app.models import Business

    # Context-manager form triggers FastAPI startup → seed_if_empty().
    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client, SessionLocal, Business)


def _run_assertions(client, SessionLocal, Business) -> None:
    # --- 1. Happy path — all four platforms requested, all four returned ---
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_tool_use(_quadd_drafts())

    with patch("app.routers.compose.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/compose/redraft", json={
            "topic": "Why publishers love the universal document extractor",
            "notes": "Lead with the Citizen Publishing time-savings testimonial.",
            "tone": "friendly",
            "image_mode": "owner",
            "platforms": ["fb", "ig", "gbp", "web"],
        })
    if r.status_code != 200:
        _fail(f"happy path returned {r.status_code}: {r.text}")
    body = r.json()
    if not body.get("ok"):
        _fail(f"happy path missing ok=True: {body}")
    drafts = body.get("drafts", {})
    if set(drafts.keys()) != {"fb", "ig", "gbp", "web"}:
        _fail(f"expected all four platforms in drafts, got {set(drafts.keys())}")
    if not drafts["fb"]["text"] or "Quadd" not in drafts["fb"]["text"]:
        _fail(f"fb draft text looks empty/wrong: {drafts['fb']}")
    if not drafts["web"]["body"]:
        _fail(f"web draft body empty: {drafts['web']}")
    _ok("happy path returns drafts for all four requested platforms")

    # System prompt was passed and includes the voice-brief AMPLIFY section.
    if not _FAKE.calls:
        _fail("FakeAnthropic.create wasn't called")
    call = _FAKE.calls[-1]
    system_blocks = call.get("system") or []
    system_text = "".join(b.get("text", "") for b in system_blocks if isinstance(b, dict))
    if "AMPLIFY" not in system_text.upper() and "Quadd" not in system_text:
        _fail(f"system prompt missing voice-brief content: {system_text[:200]!r}")
    _ok("system prompt carries voice brief into messages.create")

    # tool_choice was forced to compose_drafts.
    tc = call.get("tool_choice") or {}
    if tc.get("type") != "tool" or tc.get("name") != "compose_drafts":
        _fail(f"tool_choice not forced to compose_drafts: {tc!r}")
    _ok("tool_choice forced to compose_drafts (Sonnet 4.6 reliability)")

    # --- 2. Filter — model returns all four, owner only asked for fb+ig ---
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_tool_use(_quadd_drafts())
    with patch("app.routers.compose.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/compose/redraft", json={
            "topic": "Trial signup push",
            "platforms": ["fb", "ig"],
        })
    if r.status_code != 200:
        _fail(f"filtered request returned {r.status_code}: {r.text}")
    drafts = r.json().get("drafts", {})
    if set(drafts.keys()) != {"fb", "ig"}:
        _fail(f"server didn't filter to requested platforms — got {set(drafts.keys())}")
    _ok("server filters drafts to the platforms the owner requested")

    # --- 3. Validation — empty platforms ---
    r = client.post("/api/compose/redraft", json={
        "topic": "anything",
        "platforms": [],
    })
    if r.status_code != 422:
        _fail(f"empty platforms expected 422, got {r.status_code}: {r.text}")
    _ok("empty platforms → 422")

    # --- 4. Validation — unknown platform ---
    r = client.post("/api/compose/redraft", json={
        "topic": "anything",
        "platforms": ["fb", "snapchat"],
    })
    if r.status_code != 422:
        _fail(f"unknown platform expected 422, got {r.status_code}: {r.text}")
    _ok("unknown platform → 422")

    # --- 5. Validation — empty topic ---
    r = client.post("/api/compose/redraft", json={
        "topic": "",
        "platforms": ["fb"],
    })
    if r.status_code != 422:
        _fail(f"empty topic expected 422, got {r.status_code}: {r.text}")
    _ok("empty topic → 422")

    # --- 6. Missing API key → 503 ---
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        r = client.post("/api/compose/redraft", json={
            "topic": "x",
            "platforms": ["fb"],
        })
        if r.status_code != 503:
            _fail(f"missing API key expected 503, got {r.status_code}: {r.text}")
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
        else:
            os.environ["ANTHROPIC_API_KEY"] = "smoke-test-key"
    _ok("missing ANTHROPIC_API_KEY → 503")

    # --- 7. Model returns no tool_use block → 502 ---
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_text_only("I'd rather just chat about this.")
    with patch("app.routers.compose.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/compose/redraft", json={
            "topic": "test",
            "platforms": ["fb"],
        })
    if r.status_code != 502:
        _fail(f"no tool_use expected 502, got {r.status_code}: {r.text}")
    _ok("model with no tool_use block → 502")

    # Sanity — the seeded Quadd business still exists in the smoke DB.
    with SessionLocal() as db:
        biz = db.get(Business, 1)
        if biz is None or "quadd" not in (biz.slug or "").lower():
            _fail(f"smoke DB doesn't have Quadd seeded (biz={biz})")
    _ok(f"smoke DB has Quadd seeded (business_id=1, slug={biz.slug})")

    print("\nPASS smoke_compose_redraft: 9/9")


if __name__ == "__main__":
    main()
