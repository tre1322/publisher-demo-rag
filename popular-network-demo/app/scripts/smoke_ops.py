"""Phase 1d smoke — the uptime check and error alerts work.

Run with:  uv run python -m app.scripts.smoke_ops

  A. /healthz answers 200 {"ok": true} without signing in (for the uptime
     monitor), and never exposes anything else
  B. An unhandled error returns a plain 500 (no traceback) and emails
     ALERT_EMAIL once; repeats within the window are counted, not sent
  C. No ALERT_EMAIL → nothing is sent
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_ops_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")
os.environ["POPULAR_PURGE_LOOP"] = "0"

SENT: list[dict] = []


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def _capture(to_email, subject, html_body, text_body, *, kind):
    SENT.append({"to": to_email, "subject": subject, "text": text_body, "kind": kind})
    return {"sent": True, "messageId": "smoke"}


def _wait_for(n: int) -> None:
    deadline = time.time() + 5
    while len(SENT) < n and time.time() < deadline:
        time.sleep(0.05)


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient

    import app.alerts as alerts
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login

    os.environ.pop("POSTMARK_API_KEY", None)

    def _boom():
        raise RuntimeError("smoke-induced failure")

    app.add_api_route("/api/__smoke_boom", _boom, methods=["GET"])

    with patch.object(alerts, "_send", _capture), \
         TestClient(app, follow_redirects=False, raise_server_exceptions=False) as client:
        print("\nA. uptime check")
        r = client.get("/healthz")
        check("A1 /healthz → 200 ok without a session", r.status_code == 200 and r.json() == {"ok": True, "db": "ok"},
              (r.status_code, r.text))

        bootstrap_login(client)
        print("\nB. error alerts")
        os.environ["ALERT_EMAIL"] = "ops@example.com, second@example.com"
        alerts._last_sent = 0.0
        alerts._suppressed = 0
        r = client.get("/api/__smoke_boom")
        check("B1 unhandled error → plain 500", r.status_code == 500 and "Traceback" not in r.text
              and "smoke-induced" not in r.text, r.text[:200])
        _wait_for(2)
        check("B2 one alert per recipient", sorted(s["to"] for s in SENT) == ["ops@example.com", "second@example.com"], SENT)
        check("B3 alert names the error and the path",
              "RuntimeError" in SENT[0]["subject"] and "/api/__smoke_boom" in SENT[0]["subject"]
              and "smoke-induced failure" in SENT[0]["text"], SENT[0]["subject"])
        client.get("/api/__smoke_boom")
        client.get("/api/__smoke_boom")
        time.sleep(0.5)
        check("B4 repeats inside the window aren't emailed", len(SENT) == 2, len(SENT))
        alerts._last_sent = 0.0  # window passes
        client.get("/api/__smoke_boom")
        _wait_for(4)
        check("B5 next alert counts the suppressed ones", len(SENT) == 4 and "2 more since the last alert" in SENT[2]["subject"],
              [s["subject"] for s in SENT[2:]])

        print("\nC. alerts off")
        os.environ.pop("ALERT_EMAIL", None)
        alerts._last_sent = 0.0
        client.get("/api/__smoke_boom")
        time.sleep(0.5)
        check("C1 no ALERT_EMAIL → nothing sent", len(SENT) == 4, len(SENT))
        r = client.get("/api/bootstrap")
        check("C2 app still healthy after the errors", r.status_code == 200, r.status_code)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase 1d ops smoke green ✓")


if __name__ == "__main__":
    main()
