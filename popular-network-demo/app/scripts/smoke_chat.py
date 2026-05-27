"""Phase C.2 + C.3 smoke — exercise POST /api/chat/turn SSE stream end-to-end.

Mocks the Anthropic client so the test runs offline. Verifies:
  - Stream emits delta / tool_start / tool_result / done events in order
  - C.3 tool-use loop fires draft_post → tool_result → end_turn via streaming
  - Post + Approval rows are created by the tool
  - ChatTurn.attachment_json carries the {items:[...]} payload
  - Cap is enforced (loop bails after MAX_TOOL_ITERATIONS even if model keeps asking)
  - tool_choice routing — trigger phrase forces draft_post on iter 0
  - Validation: empty/oversized → 422; missing key → 503

Run with:  uv run python -m app.scripts.smoke_chat
"""
from __future__ import annotations

import io
import json
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_c2_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


# --------------------------------------------------------------------------- #
# Scripted Anthropic mock — a queue of "final messages," popped per stream().
# stream() returns a context manager that yields content_block_* events
# synthesized from the queued final-message content.
# --------------------------------------------------------------------------- #


class _FakeStream:
    """Context-manager mock matching anthropic.messages.stream()'s interface."""

    def __init__(self, final_message) -> None:
        self.final = final_message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        # Replay content blocks as content_block_* events. For text blocks we
        # emit one content_block_delta with text_delta; for tool_use blocks
        # we emit content_block_start (the router doesn't read input_json
        # deltas — it reads tool inputs from get_final_message()).
        for block in self.final.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=block.text),
                )
            elif btype == "tool_use":
                yield SimpleNamespace(
                    type="content_block_start",
                    content_block=SimpleNamespace(type="tool_use", name=block.name),
                )

    def get_final_message(self):
        return self.final


class _FakeMessages:
    def __init__(self) -> None:
        self.queue: list = []
        self.calls: list[dict] = []

    def _make_text_message(self, text: str):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(
                input_tokens=42, output_tokens=17,
                cache_creation_input_tokens=42, cache_read_input_tokens=0,
            ),
        )

    def _make_tool_use_message(self, preamble_text: str, tool_name: str, tool_id: str, tool_input: dict):
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="text", text=preamble_text),
                SimpleNamespace(type="tool_use", id=tool_id, name=tool_name, input=tool_input),
            ],
            usage=SimpleNamespace(
                input_tokens=42, output_tokens=17,
                cache_creation_input_tokens=0, cache_read_input_tokens=42,
            ),
        )

    def queue_text(self, text: str) -> None:
        self.queue.append(self._make_text_message(text))

    def queue_tool_use(self, preamble_text: str, tool_name: str, tool_id: str, tool_input: dict) -> None:
        self.queue.append(self._make_tool_use_message(preamble_text, tool_name, tool_id, tool_input))

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queue:
            raise AssertionError("FakeMessages: queue empty but stream() was called")
        return _FakeStream(self.queue.pop(0))


_FAKE = _FakeMessages()


class _FakeAnthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _FAKE


# --------------------------------------------------------------------------- #
# SSE consumption helpers
# --------------------------------------------------------------------------- #


def _consume_sse(client, message: str) -> list[dict]:
    """POST a message and consume the SSE stream into a list of {event, data} dicts."""
    events: list[dict] = []
    with client.stream(
        "POST", "/api/chat/turn",
        json={"message": message},
        headers={"Accept": "text/event-stream"},
    ) as resp:
        if resp.status_code != 200:
            body = resp.read().decode("utf-8", errors="replace")
            raise AssertionError(f"stream HTTP {resp.status_code}: {body}")
        buffer = ""
        for chunk in resp.iter_text():
            buffer += chunk
            while True:
                sep = buffer.find("\n\n")
                if sep < 0:
                    break
                block = buffer[:sep]
                buffer = buffer[sep + 2:]
                event_name = "message"
                data_str = ""
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        event_name = line[7:].strip()
                    elif line.startswith("data: "):
                        data_str += line[6:]
                if data_str:
                    try:
                        events.append({"event": event_name, "data": json.loads(data_str)})
                    except json.JSONDecodeError:
                        events.append({"event": event_name, "data": {"_raw": data_str}})
    return events


def main() -> None:
    import shutil

    os.environ["ANTHROPIC_API_KEY"] = "test-key-for-smoke-only"

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Approval, ChatTurn, Post

    with TestClient(app) as client:
        _run_validation_surface(client)
        _run_text_only_stream(client, SessionLocal, ChatTurn)
        _run_tool_use_stream(client, SessionLocal, ChatTurn, Post, Approval)
        _run_iteration_cap(client)
        _run_tool_choice_routing(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase C.2 + C.3 (Chat streaming + tool use) smoke green ✓")


def _run_validation_surface(client) -> None:
    # Empty + oversized still 422 (Pydantic validation before stream starts).
    r = client.post("/api/chat/turn", json={"message": ""})
    if r.status_code != 422:
        _fail(f"empty message expected 422, got {r.status_code}")
    r = client.post("/api/chat/turn", json={"message": "x" * 5000})
    if r.status_code != 422:
        _fail(f"oversized message expected 422, got {r.status_code}")
    _ok("validation: empty / oversized → 422")

    saved = os.environ.pop("ANTHROPIC_API_KEY")
    try:
        with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
            r = client.post("/api/chat/turn", json={"message": "hello"})
        if r.status_code != 503:
            _fail(f"missing API key expected 503, got {r.status_code}")
    finally:
        os.environ["ANTHROPIC_API_KEY"] = saved
    _ok("missing ANTHROPIC_API_KEY → 503")


def _run_text_only_stream(client, SessionLocal, ChatTurn) -> None:
    """Plain text response, no tools — should emit deltas then a done event."""
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_text("Memorial Day in Westbrook is lake-driven — **pre-trip safety** is the wedge.")

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        events = _consume_sse(client, "What about Father's Day weekend?")

    event_names = [e["event"] for e in events]
    if "delta" not in event_names:
        _fail(f"expected delta events, got {event_names}")
    if event_names[-1] != "done":
        _fail(f"last event should be 'done', got {event_names[-1]!r}")
    if any(e["event"] == "tool_start" or e["event"] == "tool_result" for e in events):
        _fail(f"text-only turn shouldn't emit tool events, got {event_names}")
    _ok(f"text-only stream emits delta(s) → done ({event_names.count('delta')} delta, 1 done)")

    # Concatenated deltas match the mocked text
    full_text = "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")
    if "pre-trip safety" not in full_text:
        _fail(f"concatenated deltas don't carry mocked content: {full_text!r}")
    _ok("concatenated deltas reconstruct the full agent text")

    # done event carries canonical ownerTurn + agentTurn
    done = events[-1]["data"]
    if "ownerTurn" not in done or "agentTurn" not in done:
        _fail(f"done event missing ownerTurn/agentTurn: {done!r}")
    if done["agentTurn"]["text"] != full_text:
        _fail(f"done.agentTurn.text doesn't match concatenated deltas: {done['agentTurn']['text']!r} vs {full_text!r}")
    _ok("done event carries ownerTurn + agentTurn matching the streamed text")

    # System + tools were passed; tool_choice=auto on a non-trigger message
    sent = _FAKE.calls[0]
    # E.4 added 3 ad-spend tools to the original 4 (draft_post, propose_boost,
    # draft_review_response, regenerate_insights). Total = 7. The exact count
    # matters less than "all schema tools are passed" — assert ≥ 7 so future
    # tool additions don't break this.
    if "tools" not in sent or len(sent["tools"]) < 7:
        _fail(f"tools should be passed (≥7 of them) on every stream call, got {len(sent.get('tools', []))}")
    if sent.get("tool_choice") != {"type": "auto"}:
        _fail(f"non-trigger should be tool_choice=auto, got {sent.get('tool_choice')!r}")
    if sent["system"][0].get("cache_control", {}).get("type") != "ephemeral":
        _fail("system block missing cache_control: ephemeral")
    _ok(f"call kwargs: tools=[{len(sent['tools'])}], tool_choice=auto, cache_control=ephemeral")

    # Both turns persisted
    with SessionLocal() as s:
        turns = s.query(ChatTurn).all()
        if len(turns) != 2:
            _fail(f"expected 2 turns persisted after first stream, got {len(turns)}")
    _ok("both ChatTurn rows persisted after stream completes")


def _run_tool_use_stream(client, SessionLocal, ChatTurn, Post, Approval) -> None:
    """Multi-step tool_use stream: tool_use → tool_result → end_turn."""
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_tool_use(
        preamble_text="Sure — drafting that now.",
        tool_name="draft_post",
        tool_id="tool_use_001",
        tool_input={
            "platform": "linkedin",
            "topic": "Why I built Quadd.ai",
            "brief": "Spent 15 years in a newsroom. Built the tool we needed.",
        },
    )
    _FAKE.queue_text("Drafted — card's below. Want me to tighten the closing?")

    with SessionLocal() as s:
        posts_before = s.query(Post).count()
        approvals_before = s.query(Approval).count()

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        events = _consume_sse(client, "Draft me a LinkedIn post for Quadd")

    event_names = [e["event"] for e in events]
    # Expected: delta (preamble), tool_start, tool_result, delta (final), done
    if "tool_start" not in event_names:
        _fail(f"expected tool_start event, got {event_names}")
    if "tool_result" not in event_names:
        _fail(f"expected tool_result event, got {event_names}")
    if event_names[-1] != "done":
        _fail(f"last event must be done, got {event_names[-1]}")
    # tool_start should fire before tool_result, which fires before the final done
    if event_names.index("tool_start") >= event_names.index("tool_result"):
        _fail(f"tool_start should precede tool_result, got {event_names}")
    _ok(f"tool-use stream emits delta → tool_start → tool_result → delta → done")

    tool_start = next(e for e in events if e["event"] == "tool_start")
    if tool_start["data"].get("name") != "draft_post":
        _fail(f"tool_start.name wrong: {tool_start!r}")
    _ok("tool_start carries name='draft_post'")

    tool_result = next(e for e in events if e["event"] == "tool_result")
    att = tool_result["data"].get("attachment", {})
    if att.get("kind") != "draft-post-card":
        _fail(f"tool_result.attachment.kind wrong: {att!r}")
    if "post_id" not in att or "approval_id" not in att:
        _fail(f"tool_result.attachment missing post_id/approval_id: {att!r}")
    _ok("tool_result carries draft-post-card with post_id + approval_id")

    # Final agent text concatenates BOTH deltas across iterations
    full_text = "".join(e["data"].get("text", "") for e in events if e["event"] == "delta")
    if "Drafted" not in full_text or "drafting" not in full_text.lower():
        _fail(f"final text should contain both preamble + post-tool reply, got {full_text!r}")
    _ok("concatenated deltas span both pre-tool and post-tool model calls")

    done = events[-1]["data"]
    agent_att = done["agentTurn"].get("attachment")
    if not agent_att or len(agent_att.get("items", [])) != 1:
        _fail(f"done.agentTurn.attachment.items should have 1 card, got {agent_att!r}")
    _ok("done.agentTurn.attachment.items contains 1 card (draft-post-card)")

    # Two model calls were made (tool_use → end_turn)
    if len(_FAKE.calls) != 2:
        _fail(f"expected 2 stream() calls for the tool loop, got {len(_FAKE.calls)}")
    second_call = _FAKE.calls[1]
    last_msg = second_call["messages"][-1]
    if last_msg["role"] != "user" or not isinstance(last_msg["content"], list):
        _fail(f"second call should have a user message with tool_result blocks: {last_msg!r}")
    tool_results = [b for b in last_msg["content"] if b.get("type") == "tool_result"]
    if len(tool_results) != 1 or tool_results[0]["tool_use_id"] != "tool_use_001":
        _fail(f"tool_result block missing or id wrong: {last_msg!r}")
    _ok("second model call carries tool_result with matching tool_use_id")

    # DB side effects
    with SessionLocal() as s:
        posts_after = s.query(Post).count()
        approvals_after = s.query(Approval).count()
        if posts_after != posts_before + 1:
            _fail(f"Post count expected {posts_before+1}, got {posts_after}")
        if approvals_after != approvals_before + 1:
            _fail(f"Approval count expected {approvals_before+1}, got {approvals_after}")
        latest_post = s.query(Post).order_by(Post.id.desc()).first()
        if latest_post.platform != "linkedin" or latest_post.status != "draft":
            _fail(f"new post wrong: platform={latest_post.platform} status={latest_post.status}")
    _ok("draft_post created Post + linked Approval (status='draft') during stream")

    # ChatTurn persistence
    with SessionLocal() as s:
        last_two = s.query(ChatTurn).order_by(ChatTurn.id.desc()).limit(2).all()
        agent_row = last_two[0]
        if agent_row.who != "agent":
            _fail(f"newest turn should be agent: {agent_row.who}")
        if not isinstance(agent_row.attachment_json, dict) or "items" not in agent_row.attachment_json:
            _fail(f"agent ChatTurn missing attachment_json.items: {agent_row.attachment_json!r}")
    _ok("ChatTurn rows persisted after stream with attachment_json carrying items[]")


def _run_iteration_cap(client) -> None:
    from app.agent.tools import MAX_TOOL_ITERATIONS

    _FAKE.queue.clear()
    _FAKE.calls.clear()
    for i in range(MAX_TOOL_ITERATIONS + 1):
        _FAKE.queue_tool_use(
            preamble_text=f"step {i}",
            tool_name="regenerate_insights",
            tool_id=f"tool_use_cap_{i}",
            tool_input={},
        )

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        events = _consume_sse(client, "spin forever please")

    if events[-1]["event"] != "done":
        _fail(f"cap test should still end with done event, got {events[-1]['event']}")
    if len(_FAKE.calls) != MAX_TOOL_ITERATIONS:
        _fail(
            f"iteration cap not enforced: expected {MAX_TOOL_ITERATIONS} stream() calls, "
            f"got {len(_FAKE.calls)}"
        )
    _ok(f"MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} cap enforced (stream called exactly that many times)")


def _run_tool_choice_routing(client) -> None:
    """When user message matches the draft_post trigger regex, iter 0 must
    pass tool_choice={'type':'tool','name':'draft_post'}."""
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_tool_use(
        preamble_text="Drafting.",
        tool_name="draft_post",
        tool_id="tool_use_routing_001",
        tool_input={"platform": "facebook", "topic": "Free trial", "brief": "Body."},
    )
    _FAKE.queue_text("Drafted — card's below.")

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        events = _consume_sse(client, "Draft me a Facebook post about the trial")

    first_call = _FAKE.calls[0]
    if first_call.get("tool_choice") != {"type": "tool", "name": "draft_post"}:
        _fail(f"trigger phrase should force draft_post, got tool_choice={first_call.get('tool_choice')!r}")
    second_call = _FAKE.calls[1]
    if second_call.get("tool_choice") != {"type": "auto"}:
        _fail(f"second iteration should be auto, got tool_choice={second_call.get('tool_choice')!r}")
    if events[-1]["event"] != "done":
        _fail(f"routing test should end with done event, got {events[-1]['event']}")
    _ok("trigger phrase 'Draft me a' → tool_choice forced on iter 0, auto on iter 1+ (via stream)")

    # --- speculative phrasing leaves tool_choice=auto ---
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_text("I'd hold off on that — ad ops isn't in your voice brief.")

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        events = _consume_sse(client, "We could maybe post about ad ops?")

    if _FAKE.calls[0].get("tool_choice") != {"type": "auto"}:
        _fail(f"non-trigger should be auto, got {_FAKE.calls[0].get('tool_choice')!r}")
    _ok("non-trigger phrase 'We could maybe post…' → tool_choice stays auto")

    # --- regex-only assertion (no API calls) ---
    from app.routers.chat import _detect_forced_tool
    variants = [
        "Draft me a post about X",
        "Draft a quick LinkedIn post on X",
        "Write me a Facebook post for next week",
        "Write a caption for the Mike testimonial",
        "Give me a draft about court documents",
        "Compose a post about the free trial",
        "Put together a draft for Wednesday",
    ]
    for v in variants:
        if _detect_forced_tool(v) != "draft_post":
            _fail(f"trigger variant should match: {v!r} → got {_detect_forced_tool(v)!r}")
    _ok(f"all {len(variants)} natural-language draft-trigger variants matched")

    negatives = [
        "What should I post about this week?",
        "We should post about the trial soon",
        "Any thoughts on the latest performance numbers?",
        "Maybe post something Friday?",
    ]
    for v in negatives:
        if _detect_forced_tool(v) is not None:
            _fail(f"non-trigger should not match: {v!r} → got {_detect_forced_tool(v)!r}")
    _ok(f"all {len(negatives)} non-trigger phrasings correctly skipped")


if __name__ == "__main__":
    main()
