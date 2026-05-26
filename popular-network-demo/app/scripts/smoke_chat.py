"""Phase C.1 smoke — exercise POST /api/chat/turn end-to-end.

Mocks the Anthropic client so the test runs offline. Verifies:
  - 200 on a real turn, response carries ownerTurn + agentTurn
  - Both turns persist as ChatTurn rows
  - /api/bootstrap reflects the new turns
  - System prompt is built from live DB state (presence of biz name + plan text)
  - Empty / oversized payloads are rejected with 422
  - Missing ANTHROPIC_API_KEY returns 503

Run with:  uv run python -m app.scripts.smoke_chat
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_c1_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


class _FakeMessages:
    """Captures the args Claude was called with so we can assert on them."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            usage=SimpleNamespace(
                input_tokens=42,
                output_tokens=17,
                cache_creation_input_tokens=42,
                cache_read_input_tokens=0,
            ),
        )


class _FakeAnthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _FAKE_MESSAGES_SINGLETON


_FAKE_MESSAGES_SINGLETON = _FakeMessages(
    reply="That's a fair question. Memorial Day in Westbrook is lake-driven — **pre-trip safety** is the wedge. Want me to draft a Saturday post pushing the $59 inspection?"
)


def main() -> None:
    import shutil

    # Ensure the chat router thinks an API key exists for the happy path.
    os.environ["ANTHROPIC_API_KEY"] = "test-key-for-smoke-only"

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import ChatTurn

    with TestClient(app) as client:
        _run_assertions(client, SessionLocal, ChatTurn)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase C.1 (Chat) smoke green ✓")


def _run_assertions(client, SessionLocal, ChatTurn) -> None:
    # --- baseline: chat thread is empty on Day 1 (Quadd.ai seed) ---
    boot_before = client.get("/api/bootstrap").json()
    chat_before = boot_before["chat"]
    _ok(f"baseline: {len(chat_before)} seeded chat turns (Day-1 seed expects 0)")

    # --- happy path: send a message ---
    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/chat/turn", json={"message": "What about Father's Day weekend?"})
    if r.status_code != 200:
        _fail(f"POST /api/chat/turn → HTTP {r.status_code} {r.text}")
    data = r.json()
    for key in ("ok", "ownerTurn", "agentTurn"):
        if key not in data:
            _fail(f"response missing {key}: {data}")
    if data["ownerTurn"]["who"] != "owner" or data["agentTurn"]["who"] != "agent":
        _fail(f"who fields wrong: {data}")
    if data["ownerTurn"]["text"] != "What about Father's Day weekend?":
        _fail(f"owner text not echoed: {data['ownerTurn']['text']!r}")
    if "pre-trip safety" not in data["agentTurn"]["text"]:
        _fail(f"agent text doesn't carry mocked content: {data['agentTurn']['text']!r}")
    _ok("POST /api/chat/turn → 200 with ownerTurn + agentTurn")

    # --- system prompt built from live DB state ---
    sent = _FAKE_MESSAGES_SINGLETON.last_kwargs
    if not sent:
        _fail("Anthropic mock was never called")
    system = sent["system"]
    if not isinstance(system, list) or not system:
        _fail(f"system should be a list of blocks: {system!r}")
    sys_text = system[0]["text"]
    if "Quadd.ai" not in sys_text:
        _fail(f"system prompt missing business name (substring 'Quadd.ai'). Got first 400 chars:\n{sys_text[:400]}")
    if "Marketing plan" not in sys_text:
        _fail("system prompt missing marketing-plan section")
    if "Recent posts" not in sys_text:
        _fail("system prompt missing recent-posts section")
    # Voice brief should be loaded for quadd_ai slug (data/voice-brief/quadd_ai.json exists)
    if "Voice brief" not in sys_text:
        _fail(f"voice brief section missing. First 500 chars:\n{sys_text[:500]}")
    if "AMPLIFY" not in sys_text:
        _fail("voice brief AMPLIFY bucket missing — brief failed to load")
    if system[0].get("cache_control", {}).get("type") != "ephemeral":
        _fail(f"system block missing cache_control: {system[0]!r}")
    _ok("system prompt has biz name + marketing plan + recent posts + voice brief + cache_control")

    # --- messages array carries seed history + new user message ---
    msgs = sent["messages"]
    if not msgs or msgs[-1]["role"] != "user" or msgs[-1]["content"] != "What about Father's Day weekend?":
        _fail(f"last message wrong: {msgs[-1] if msgs else None}")
    if len(msgs) != len(chat_before) + 1:
        _fail(f"messages count: expected {len(chat_before)+1}, got {len(msgs)}")
    # Alternating roles starting with user
    if msgs[0]["role"] != "user":
        _fail(f"first message must be user, got {msgs[0]['role']}")
    _ok(f"messages carries seed history + new turn ({len(msgs)} total)")

    # --- both turns persisted ---
    with SessionLocal() as s:
        live = s.query(ChatTurn).filter(ChatTurn.conversation_id == "seed", ChatTurn.business_id == 1).all()
        # seed = 6 turns, plus owner + agent we just added
        if len(live) != len(chat_before) + 2:
            _fail(f"DB has {len(live)} turns, expected {len(chat_before) + 2}")
    _ok("both ChatTurn rows persisted")

    # --- bootstrap reflects the new turns ---
    boot_after = client.get("/api/bootstrap").json()
    if len(boot_after["chat"]) != len(chat_before) + 2:
        _fail(f"bootstrap chat count {len(boot_after['chat'])}, expected {len(chat_before) + 2}")
    if boot_after["chat"][-1]["who"] != "agent":
        _fail(f"last bootstrap turn should be agent, got {boot_after['chat'][-1]}")
    _ok("/api/bootstrap reflects new turns (owner→agent appended)")

    # --- empty message → 422 ---
    r = client.post("/api/chat/turn", json={"message": ""})
    if r.status_code != 422:
        _fail(f"empty message expected 422, got {r.status_code}")
    _ok("empty message → 422")

    # --- oversized message → 422 ---
    r = client.post("/api/chat/turn", json={"message": "x" * 5000})
    if r.status_code != 422:
        _fail(f"oversized message expected 422, got {r.status_code}")
    _ok("oversized message → 422")

    # --- missing API key → 503 ---
    saved = os.environ.pop("ANTHROPIC_API_KEY")
    try:
        with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
            r = client.post("/api/chat/turn", json={"message": "hello"})
        if r.status_code != 503:
            _fail(f"missing API key expected 503, got {r.status_code}")
    finally:
        os.environ["ANTHROPIC_API_KEY"] = saved
    _ok("missing ANTHROPIC_API_KEY → 503")


if __name__ == "__main__":
    main()
