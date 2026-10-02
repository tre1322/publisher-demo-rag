"""Error alerts by email (Phase 1).

An unhandled exception in a request emails ALERT_EMAIL (comma-separated
list) through the same Postmark sender as invites. At most one email per
ALERT_EVERY_SECS; errors in between are counted and summarised in the next
one, so a crash loop can't flood the inbox or burn the Postmark quota.

Unset ALERT_EMAIL → alerts are off (the error is still logged).
Uptime ("is the site answering at all?") is a separate, external monitor
pointed at /healthz — an app that is down can't email about itself.
"""
from __future__ import annotations

import html
import logging
import os
import threading
import time
import traceback

from .email import _send

log = logging.getLogger("popular_network.alerts")

ALERT_EVERY_SECS = 10 * 60

_lock = threading.Lock()
_last_sent = 0.0
_suppressed = 0


def _recipients() -> list[str]:
    return [a.strip() for a in os.getenv("ALERT_EMAIL", "").split(",") if a.strip()]


def notify_error(exc: BaseException, *, method: str, path: str) -> dict:
    """Email an alert for `exc` unless rate-limited or unconfigured. Never raises."""
    global _last_sent, _suppressed
    to = _recipients()
    if not to:
        return {"sent": False, "reason": "no_alert_email"}
    with _lock:
        now = time.time()
        if now - _last_sent < ALERT_EVERY_SECS:
            _suppressed += 1
            return {"sent": False, "reason": "rate_limited"}
        extra, _suppressed, _last_sent = _suppressed, 0, now
    try:
        where = f"{method} {path}"
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        site = os.getenv("APP_BASE_URL", "the dashboard")
        more = f" ({extra} more since the last alert)" if extra else ""
        subject = f"[Amplafai dashboard] Error: {type(exc).__name__} on {path}{more}"
        text = (
            f"An unhandled error happened on {site}.\n\n"
            f"Request: {where}\nError: {type(exc).__name__}: {exc}\n"
            f"Errors not emailed since the last alert: {extra}\n\n{tb}\n"
            f"Alerts are limited to one every {ALERT_EVERY_SECS // 60} minutes.\n"
        )
        body = (
            f"<p>An unhandled error happened on {html.escape(site)}.</p>"
            f"<p><b>Request:</b> {html.escape(where)}<br><b>Error:</b> {html.escape(type(exc).__name__)}: "
            f"{html.escape(str(exc))}<br><b>Errors not emailed since the last alert:</b> {extra}</p>"
            f"<pre style='font-size:12px'>{html.escape(tb)}</pre>"
            f"<p>Alerts are limited to one every {ALERT_EVERY_SECS // 60} minutes.</p>"
        )
        results = [_send(addr, subject, body, text, kind="error-alert") for addr in to]
        return {"sent": any(r.get("sent") for r in results), "results": results}
    except Exception:  # an alert must never cause a second failure
        log.exception("error alert could not be sent")
        return {"sent": False, "reason": "alert_failed"}
