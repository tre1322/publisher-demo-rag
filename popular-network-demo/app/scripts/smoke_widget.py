"""H.2.2 smoke — public widget chat backend.

Verifies:
  1. POST /api/widget/chat with valid business returns reply + sessionId
  2. Subsequent POST with same sessionId continues the same conversation
  3. ChatbotConversation row appears in the DB (source='ingested')
  4. Invalid business_id -> 404
  5. Missing ANTHROPIC_API_KEY -> 503 (skipped if mock injected)
  6. CORS:
     a. No allowed_origins_json on Business + any Origin -> echoed allow
     b. allowed_origins_json populated + matching Origin -> echoed allow
     c. allowed_origins_json populated + non-matching Origin -> 403
  7. Preflight OPTIONS returns CORS headers and 204
  8. IP rate limit: hitting WIDGET_MAX_PER_IP_PER_HOUR returns 429
  9. Daily token cap: filling the bucket returns 429

Run with:  uv run python -m app.scripts.smoke_widget
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

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_widget_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-smoke-only")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Business, ChatbotConversation  # noqa: E402


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


# --- mock Anthropic ---
class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply_text = "Thanks for asking! Quadd.ai helps publishers."
        self.input_tokens = 80
        self.output_tokens = 40

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=self.reply_text)],
            usage=SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens),
        )


class _FakeAnthropic:
    _shared = _FakeMessages()
    def __init__(self, *a, **kw) -> None:
        self.messages = _FakeAnthropic._shared


_FAKE = _FakeAnthropic._shared


def main() -> int:
    print("smoke_widget -- Phase H.2.2 widget backend\n")

    with TestClient(app) as client:
        with patch("app.routers.widget.Anthropic", _FakeAnthropic, create=False):
            # 1. Basic chat (no Origin header, no allowed_origins on Business 1).
            _FAKE.calls.clear()
            r = client.post("/api/widget/chat", json={
                "business_id": 1,
                "message": "What does Quadd do?",
            })
            check("1. POST /widget/chat -> 200", r.status_code == 200, r.text[:300])
            data = r.json()
            check("1. response has sessionId", "sessionId" in data, str(data)[:300])
            check("1. response has reply", data.get("reply", "").startswith("Thanks"), str(data)[:300])
            session_id = data["sessionId"]

            # 2. Continue same session
            r = client.post("/api/widget/chat", json={
                "business_id": 1,
                "session_id": session_id,
                "message": "Tell me more about pricing.",
            })
            check("2. continue session -> 200", r.status_code == 200, r.text[:300])
            check("2. reply has both turns + 2 bots = 4",
                  len(r.json().get("transcript", [])) == 4, str(r.json().get("transcript")))

            # 3. DB row exists
            with SessionLocal() as db:
                convo = (
                    db.query(ChatbotConversation)
                    .filter(
                        ChatbotConversation.external_id == session_id,
                        ChatbotConversation.business_id == 1,
                    )
                    .first()
                )
                check("3. ChatbotConversation row exists", convo is not None, "")
                if convo:
                    check("3. source='ingested'", convo.source == "ingested", convo.source)
                    check("3. turn_count == 4", convo.turn_count == 4, str(convo.turn_count))

            # 4. Invalid business -> 404
            r = client.post("/api/widget/chat", json={"business_id": 9999, "message": "hi"})
            check("4. invalid business -> 404", r.status_code == 404, r.text[:200])

            # 6a. CORS — no allowlist, any Origin echoed
            r = client.post(
                "/api/widget/chat",
                json={"business_id": 1, "message": "test"},
                headers={"Origin": "https://random.example.com"},
            )
            check("6a. no allowlist, any Origin -> 200", r.status_code == 200, r.text[:200])
            check("6a. ACAO echoes Origin",
                  r.headers.get("access-control-allow-origin") == "https://random.example.com",
                  r.headers.get("access-control-allow-origin"))

            # 6b. Set allowlist on biz 1; matching origin allowed
            with SessionLocal() as db:
                biz = db.get(Business, 1)
                biz.allowed_origins_json = ["https://cottonwoodcountycitizen.com"]
                db.commit()

            r = client.post(
                "/api/widget/chat",
                json={"business_id": 1, "message": "test"},
                headers={"Origin": "https://cottonwoodcountycitizen.com"},
            )
            check("6b. matching allowlist -> 200", r.status_code == 200, r.text[:200])
            check("6b. ACAO echoes matching Origin",
                  r.headers.get("access-control-allow-origin") == "https://cottonwoodcountycitizen.com",
                  r.headers.get("access-control-allow-origin"))

            # 6c. Non-matching origin -> 403
            r = client.post(
                "/api/widget/chat",
                json={"business_id": 1, "message": "test"},
                headers={"Origin": "https://attacker.example"},
            )
            check("6c. non-matching origin -> 403", r.status_code == 403, r.text[:200])

            # Restore wide-open for the rest of the tests
            with SessionLocal() as db:
                biz = db.get(Business, 1)
                biz.allowed_origins_json = None
                db.commit()

            # 7. Preflight OPTIONS
            r = client.options("/api/widget/chat", headers={
                "Origin": "https://cottonwoodcountycitizen.com",
                "X-Business-Id": "1",
                "Access-Control-Request-Method": "POST",
            })
            check("7. OPTIONS -> 204", r.status_code == 204, f"got {r.status_code}")
            check("7. OPTIONS has ACAO",
                  r.headers.get("access-control-allow-origin") is not None,
                  str(dict(r.headers)))

            # 8. IP rate limit
            from app.routers.widget import (
                WIDGET_MAX_PER_IP_PER_HOUR,
                _BIZ_TOKENS,
                _IP_HITS,
            )
            _IP_HITS.clear()
            _BIZ_TOKENS.clear()
            last_status = None
            # First WIDGET_MAX_PER_IP_PER_HOUR should pass; next one 429.
            for i in range(WIDGET_MAX_PER_IP_PER_HOUR + 1):
                r = client.post("/api/widget/chat", json={"business_id": 1, "message": f"msg{i}"})
                last_status = r.status_code
                if last_status == 429:
                    break
            check("8. IP rate-limit triggers 429", last_status == 429, f"last={last_status}")

            # 9. Daily token cap
            _IP_HITS.clear()
            _BIZ_TOKENS.clear()
            # Force token cap to be tiny by directly pre-filling the bucket.
            from datetime import date
            from app.routers.widget import WIDGET_DAILY_TOKEN_CAP
            _BIZ_TOKENS[1] = (date.today().isoformat(), WIDGET_DAILY_TOKEN_CAP + 1)
            r = client.post("/api/widget/chat", json={"business_id": 1, "message": "after cap"})
            check("9. daily token cap -> 429", r.status_code == 429, f"got {r.status_code}: {r.text[:200]}")

        # 10. /static/widget.js serves publicly (no auth, no mock)
        r = client.get("/static/widget.js")
        check("10. /static/widget.js -> 200", r.status_code == 200, f"got {r.status_code}")
        check("10. widget.js has init() export",
              b"PopularNetworkWidget" in r.content and b"function init" in r.content,
              "expected PopularNetworkWidget.init in source")

        # 11. /widget-test.html serves publicly
        r = client.get("/widget-test.html")
        check("11. /widget-test.html -> 200", r.status_code == 200, f"got {r.status_code}")
        check("11. test page wires up the widget",
              b"PopularNetworkWidget.init" in r.content,
              "expected init() call on the test page")

    print(f"\n{len(PASSED)} pass / {len(FAILED)} fail")
    for label, detail in FAILED:
        print(f"  FAIL: {label} -- {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
