"""Phase C.3 smoke — exercise POST /api/chat/turn end-to-end with tool use.

Mocks the Anthropic client so the test runs offline. Verifies:
  - C.1 surface still passes (200, persistence, system prompt, validation, 503)
  - C.3 tool-use loop fires draft_post → tool_result → end_turn
  - Post + Approval rows are created by the tool
  - ChatTurn.attachment_json carries the {items:[...]} payload
  - Cap is enforced (loop bails after MAX_TOOL_ITERATIONS even if model keeps asking)

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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_c3_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


# --------------------------------------------------------------------------- #
# Scripted Anthropic mock — a queue of responses, popped per messages.create.
# --------------------------------------------------------------------------- #


class _FakeMessages:
    def __init__(self) -> None:
        self.queue: list = []
        self.calls: list[dict] = []

    def queue_text(self, text: str) -> None:
        self.queue.append(
            SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(
                    input_tokens=42, output_tokens=17,
                    cache_creation_input_tokens=42, cache_read_input_tokens=0,
                ),
            )
        )

    def queue_tool_use(self, preamble_text: str, tool_name: str, tool_id: str, tool_input: dict) -> None:
        self.queue.append(
            SimpleNamespace(
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
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.queue:
            raise AssertionError("FakeMessages: queue empty but create() was called")
        return self.queue.pop(0)


_FAKE = _FakeMessages()


class _FakeAnthropic:
    def __init__(self, api_key: str | None = None) -> None:
        self.messages = _FAKE


def main() -> None:
    import shutil

    os.environ["ANTHROPIC_API_KEY"] = "test-key-for-smoke-only"

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Approval, ChatTurn, Post

    with TestClient(app) as client:
        _run_c1_surface(client, SessionLocal, ChatTurn)
        _run_c3_tool_use(client, SessionLocal, ChatTurn, Post, Approval)
        _run_c3_iteration_cap(client, SessionLocal)
        _run_c3_tool_choice_routing(client)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase C.3 (Chat + tool use) smoke green ✓")


def _run_c1_surface(client, SessionLocal, ChatTurn) -> None:
    """Carries forward the C.1 assertions so we don't regress that surface."""
    boot_before = client.get("/api/bootstrap").json()
    chat_before = boot_before["chat"]
    _ok(f"baseline: {len(chat_before)} seeded chat turns")

    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_text(
        "That's a fair question. Memorial Day in Westbrook is lake-driven — **pre-trip safety** is the wedge."
    )

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/chat/turn", json={"message": "What about Father's Day weekend?"})
    if r.status_code != 200:
        _fail(f"POST /api/chat/turn → HTTP {r.status_code} {r.text}")
    data = r.json()
    for key in ("ok", "ownerTurn", "agentTurn"):
        if key not in data:
            _fail(f"response missing {key}: {data}")
    if "pre-trip safety" not in data["agentTurn"]["text"]:
        _fail(f"agent text doesn't carry mocked content: {data['agentTurn']['text']!r}")
    _ok("POST /api/chat/turn (text-only) → 200 with ownerTurn + agentTurn")

    sent = _FAKE.calls[0]
    if "tools" not in sent:
        _fail("tools kwarg missing from messages.create — multi-step loop won't function")
    if not isinstance(sent["tools"], list) or len(sent["tools"]) != 4:
        _fail(f"tools should be a list of 4 schemas, got {sent['tools']!r}")
    tool_names = {t["name"] for t in sent["tools"]}
    if tool_names != {"draft_post", "propose_boost", "draft_review_response", "regenerate_insights"}:
        _fail(f"tool name set wrong: {tool_names}")
    _ok("tools=[draft_post, propose_boost, draft_review_response, regenerate_insights] passed on every call")

    system = sent["system"]
    sys_text = system[0]["text"]
    if "Quadd.ai" not in sys_text or "Marketing plan" not in sys_text or "Voice brief" not in sys_text:
        _fail(f"system prompt missing key sections; first 400 chars:\n{sys_text[:400]}")
    if system[0].get("cache_control", {}).get("type") != "ephemeral":
        _fail("system block missing cache_control: ephemeral")
    _ok("system prompt + cache_control intact")

    # Validation paths
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


def _run_c3_tool_use(client, SessionLocal, ChatTurn, Post, Approval) -> None:
    """Tool-use loop: model says 'drafting…' + tool_use → server runs draft_post →
    model receives tool_result and emits final end_turn text."""
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    # Step 1: model emits draft_post tool_use
    _FAKE.queue_tool_use(
        preamble_text="Sure — drafting that now.",
        tool_name="draft_post",
        tool_id="tool_use_001",
        tool_input={
            "platform": "linkedin",
            "topic": "Why I built Quadd.ai for newspaper publishers",
            "brief": (
                "Spent 15 years inside a community newsroom watching good papers buckle "
                "under ad ops. Quadd.ai is the back-office layer they were never going to "
                "build themselves. If you run a paper, let's talk."
            ),
        },
    )
    # Step 2: after tool_result, model says we're done
    _FAKE.queue_text("Done — draft is in your Approvals queue. Want me to draft one for Facebook too?")

    posts_before = 0
    approvals_before = 0
    with SessionLocal() as s:
        posts_before = s.query(Post).count()
        approvals_before = s.query(Approval).count()

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/chat/turn", json={"message": "Draft me a LinkedIn post for Quadd"})
    if r.status_code != 200:
        _fail(f"tool-use turn HTTP {r.status_code}: {r.text}")

    data = r.json()
    if "Done" not in data["agentTurn"]["text"]:
        _fail(f"final agent text missing 'Done': {data['agentTurn']['text']!r}")
    if "drafting" not in data["agentTurn"]["text"].lower():
        _fail(f"agent text should also carry the preamble 'drafting' line: {data['agentTurn']['text']!r}")
    _ok("agent text concatenates preamble + final ('drafting…' + 'Done…')")

    att = data["agentTurn"].get("attachment")
    if not att or not isinstance(att.get("items"), list) or len(att["items"]) != 1:
        _fail(f"agentTurn.attachment should be {{items:[...]}}, got {att!r}")
    card = att["items"][0]
    if card.get("kind") != "draft-post-card":
        _fail(f"first attachment card kind wrong: {card!r}")
    if card.get("platform") != "linkedin":
        _fail(f"card platform wrong: {card!r}")
    if "post_id" not in card or "approval_id" not in card:
        _fail(f"card missing post_id/approval_id: {card!r}")
    _ok("attachment.items[0] = draft-post-card with post_id + approval_id")

    # Two model calls expected (tool_use then end_turn)
    if len(_FAKE.calls) != 2:
        _fail(f"expected 2 messages.create calls (tool_use → end_turn), got {len(_FAKE.calls)}")
    second_call = _FAKE.calls[1]
    last_msg = second_call["messages"][-1]
    if last_msg["role"] != "user" or not isinstance(last_msg["content"], list):
        _fail(f"second call's last message should be user with content blocks: {last_msg!r}")
    tool_results = [b for b in last_msg["content"] if b.get("type") == "tool_result"]
    if len(tool_results) != 1 or tool_results[0]["tool_use_id"] != "tool_use_001":
        _fail(f"tool_result block missing or wrong id: {last_msg!r}")
    _ok("second model call has tool_result block with matching tool_use_id")

    # DB side effects
    with SessionLocal() as s:
        posts_after = s.query(Post).count()
        approvals_after = s.query(Approval).count()
        if posts_after != posts_before + 1:
            _fail(f"Post count: expected {posts_before+1}, got {posts_after}")
        if approvals_after != approvals_before + 1:
            _fail(f"Approval count: expected {approvals_before+1}, got {approvals_after}")
        post = s.query(Post).order_by(Post.id.desc()).first()
        if post.platform != "linkedin" or post.status != "draft":
            _fail(f"new post wrong: platform={post.platform} status={post.status}")
        appr = s.query(Approval).order_by(Approval.id.desc()).first()
        if appr.post_id != post.id or appr.kind != "post":
            _fail(f"approval not linked to post: appr={appr.__dict__}")
    _ok("draft_post created Post + linked Approval (status='draft')")

    # ChatTurn persistence with attachment_json
    with SessionLocal() as s:
        last_two = s.query(ChatTurn).order_by(ChatTurn.id.desc()).limit(2).all()
        # Most-recent is the agent turn
        agent_row = last_two[0]
        owner_row = last_two[1]
        if agent_row.who != "agent" or owner_row.who != "owner":
            _fail(f"persisted order wrong: {[(t.who, t.id) for t in last_two]}")
        if not isinstance(agent_row.attachment_json, dict) or "items" not in agent_row.attachment_json:
            _fail(f"agent ChatTurn missing attachment_json.items: {agent_row.attachment_json!r}")
    _ok("ChatTurn rows persisted with attachment_json carrying items[]")


def _run_c3_iteration_cap(client, SessionLocal) -> None:
    """If the model keeps emitting tool_use, the loop bails at MAX_TOOL_ITERATIONS."""
    from app.agent.tools import MAX_TOOL_ITERATIONS

    _FAKE.queue.clear()
    _FAKE.calls.clear()
    # Queue cap+1 tool-use responses — loop should stop before consuming the last one.
    for i in range(MAX_TOOL_ITERATIONS + 1):
        _FAKE.queue_tool_use(
            preamble_text=f"step {i}",
            tool_name="regenerate_insights",
            tool_id=f"tool_use_cap_{i}",
            tool_input={},
        )

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/chat/turn", json={"message": "spin forever please"})
    if r.status_code != 200:
        _fail(f"cap-test turn HTTP {r.status_code}: {r.text}")

    # We expect MAX_TOOL_ITERATIONS create() calls — the cap fires before the next.
    if len(_FAKE.calls) != MAX_TOOL_ITERATIONS:
        _fail(
            f"iteration cap not enforced: expected {MAX_TOOL_ITERATIONS} model calls, "
            f"got {len(_FAKE.calls)}"
        )
    _ok(f"MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} cap enforced (model called exactly that many times)")


def _run_c3_tool_choice_routing(client) -> None:
    """When user message matches the draft_post trigger regex, the first model
    call should pass tool_choice={'type':'tool','name':'draft_post'}.
    On a no-match message, tool_choice should be {'type':'auto'}."""
    # --- trigger phrase forces draft_post on iter 0 ---
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
        r = client.post("/api/chat/turn", json={"message": "Draft me a Facebook post about the trial"})
    if r.status_code != 200:
        _fail(f"routing trigger HTTP {r.status_code}: {r.text}")
    first_call = _FAKE.calls[0]
    if first_call.get("tool_choice") != {"type": "tool", "name": "draft_post"}:
        _fail(f"trigger phrase should force draft_post, got tool_choice={first_call.get('tool_choice')!r}")
    second_call = _FAKE.calls[1]
    if second_call.get("tool_choice") != {"type": "auto"}:
        _fail(f"second iteration should be auto, got tool_choice={second_call.get('tool_choice')!r}")
    _ok("trigger phrase 'Draft me a' → tool_choice forced on iter 0, auto on iter 1+")

    # --- speculative phrasing leaves tool_choice=auto ---
    _FAKE.queue.clear()
    _FAKE.calls.clear()
    _FAKE.queue_text("I'd hold off on that — ad ops isn't in your voice brief.")

    with patch("app.routers.chat.Anthropic", _FakeAnthropic, create=False):
        r = client.post("/api/chat/turn", json={"message": "We could maybe post about ad ops?"})
    if r.status_code != 200:
        _fail(f"non-trigger HTTP {r.status_code}: {r.text}")
    if _FAKE.calls[0].get("tool_choice") != {"type": "auto"}:
        _fail(
            f"non-trigger message should stay auto, got "
            f"tool_choice={_FAKE.calls[0].get('tool_choice')!r}"
        )
    _ok("non-trigger phrase ('We could maybe post…') → tool_choice stays auto")

    # --- several trigger variants all match ---
    variants = [
        "Draft me a post about X",
        "Draft a quick LinkedIn post on X",
        "Write me a Facebook post for next week",
        "Write a caption for the Mike testimonial",
        "Give me a draft about court documents",
        "Compose a post about the free trial",
        "Put together a draft for Wednesday",
    ]
    from app.routers.chat import _detect_forced_tool
    for v in variants:
        if _detect_forced_tool(v) != "draft_post":
            _fail(f"trigger variant should match: {v!r} → got {_detect_forced_tool(v)!r}")
    _ok(f"all {len(variants)} natural-language draft-trigger variants matched")

    # --- a few negatives ---
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
