"""Smoke for app.email.send_invite_email — exercises both branches.

Run with:  uv run python -m app.scripts.smoke_invite_email

1. POSTMARK_API_KEY unset → returns {"sent": False, "reason": "no_api_key"}.
2. POSTMARK_API_KEY set + mocked httpx response 200 → returns
   {"sent": True, "messageId": ...} AND the POST payload has the
   expected From/To/Subject/HtmlBody/TextBody/MessageStream fields.
3. POSTMARK_API_KEY set + mocked 422 response → returns
   {"sent": False, "reason": "postmark_422"} (no exception bubbles up).
4. Subject + body include business name, role, and absolutized claim_url.
"""
from __future__ import annotations

import io
import os
import sys
from unittest.mock import MagicMock, patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _fails.append(label)


def _mock_resp(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = str(body or "")
    resp.json.return_value = body or {}
    return resp


def main() -> int:
    # Force a known env shape — tests must not depend on the shell.
    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.pop("APP_BASE_URL", None)

    from app.email import send_invite_email

    # 1. No API key → no-op.
    out = send_invite_email(
        to_email="alice@example.com",
        claim_url="/invite?token=abc",
        role="owner",
        business_name="Quadd.ai",
    )
    check("1. unset key → sent=False", out.get("sent") is False, str(out))
    check("1. reason=no_api_key", out.get("reason") == "no_api_key", str(out))

    # 2. Set key + happy path.
    os.environ["POSTMARK_API_KEY"] = "test-key-not-real"
    os.environ["APP_BASE_URL"] = "https://dashboard.amplafai.com"

    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _mock_resp(200, {"MessageID": "msg-123", "ErrorCode": 0})

    with patch("app.email.httpx.Client", _FakeClient):
        out = send_invite_email(
            to_email="bob@example.com",
            claim_url="/invite?token=xyz",
            role="editor",
            business_name="Quadd.ai",
        )

    check("2. happy path → sent=True", out.get("sent") is True, str(out))
    check("2. messageId surfaced", out.get("messageId") == "msg-123", str(out))
    check("2. POSTed to Postmark endpoint", captured.get("url") == "https://api.postmarkapp.com/email", str(captured.get("url")))
    payload = captured.get("json") or {}
    check("2. From header includes default sender", "Amplafai" in payload.get("From", ""), payload.get("From"))
    check("2. To = bob@example.com", payload.get("To") == "bob@example.com", payload.get("To"))
    check("2. Subject mentions business name", "Quadd.ai" in payload.get("Subject", ""), payload.get("Subject"))
    check("2. HtmlBody mentions role", "editor" in payload.get("HtmlBody", ""), payload.get("HtmlBody", "")[:80])
    check(
        "2. HtmlBody has absolutized claim URL",
        "https://dashboard.amplafai.com/invite?token=xyz" in payload.get("HtmlBody", ""),
        payload.get("HtmlBody", "")[:200],
    )
    check("2. TextBody fallback present", "Quadd.ai" in payload.get("TextBody", ""), payload.get("TextBody", "")[:80])
    check("2. MessageStream defaults to outbound", payload.get("MessageStream") == "outbound", payload.get("MessageStream"))
    check(
        "2. X-Postmark-Server-Token header set",
        (captured.get("headers") or {}).get("X-Postmark-Server-Token") == "test-key-not-real",
        str(captured.get("headers")),
    )

    # 3. Postmark error → no exception, sent=False with reason.
    class _FakeClient422(_FakeClient):
        def post(self, url, json=None, headers=None):
            return _mock_resp(422, {"ErrorCode": 405, "Message": "Sender not verified"})

    with patch("app.email.httpx.Client", _FakeClient422):
        out = send_invite_email(
            to_email="bob@example.com",
            claim_url="/invite?token=xyz",
            role="editor",
            business_name="Quadd.ai",
        )
    check("3. 422 → sent=False", out.get("sent") is False, str(out))
    check("3. reason=postmark_422", out.get("reason") == "postmark_422", str(out))

    # 4. Relative URL stays relative if APP_BASE_URL is unset.
    os.environ.pop("APP_BASE_URL", None)
    captured.clear()
    with patch("app.email.httpx.Client", _FakeClient):
        send_invite_email(
            to_email="bob@example.com",
            claim_url="/invite?token=q",
            role="owner",
            business_name="Quadd.ai",
        )
    payload = captured.get("json") or {}
    check(
        "4. relative URL stays relative when APP_BASE_URL unset",
        "/invite?token=q" in payload.get("HtmlBody", "") and "https://" not in payload.get("HtmlBody", "").split("/invite")[0][-15:],
        payload.get("HtmlBody", "")[:200],
    )

    # Cleanup.
    os.environ.pop("POSTMARK_API_KEY", None)
    os.environ.pop("APP_BASE_URL", None)

    print()
    total = 18
    if _fails:
        print(f"FAIL — {len(_fails)} of {total} checks failed")
        return 1
    print(f"{total} pass / 0 fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
