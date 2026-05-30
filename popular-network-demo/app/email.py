"""Transactional email — currently Postmark-only.

Single public function: send_invite_email(). Returns a dict so the caller
can log the outcome without raising. We never want a failing email to
roll back the DB-side invite mint.

Env vars:
    POSTMARK_API_KEY      — required to actually send. If unset, function
                            is a no-op and returns {"sent": False, "reason": "no_api_key"}.
    INVITE_FROM_EMAIL     — sender email. Default: invites@amplafai.com.
                            MUST be a verified sender signature in Postmark.
    INVITE_FROM_NAME      — display name. Default: "Amplafai".
    APP_BASE_URL          — public dashboard URL (e.g. https://dashboard.amplafai.com).
                            Used to absolutize the claim URL if it's relative.
                            Default: derived from request when possible; otherwise omitted.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("popular_network.email")

POSTMARK_ENDPOINT = "https://api.postmarkapp.com/email"
DEFAULT_FROM_EMAIL = "invites@amplafai.com"
DEFAULT_FROM_NAME = "Amplafai"


def _absolutize(claim_url: str, base_url: Optional[str]) -> str:
    if claim_url.startswith(("http://", "https://")):
        return claim_url
    if not base_url:
        return claim_url  # relative URL — recipient's email client probably won't render it
    return f"{base_url.rstrip('/')}{claim_url}"


# ----------------------------------------------------------------------------
# Template
#
# TODO(trevor): the body below is functional but plain. The first email a new
# pilot ever gets from Amplafai is the highest-leverage piece of activation
# copy you have — it competes against every other invite/SaaS email in their
# inbox. Shape this in your voice. Things to consider:
#   - Subject line: "Trevor invited you..." vs "Quadd.ai is using Amplafai..."
#   - Body opening: founder-letter style vs corporate-transactional style
#   - The "what is Amplafai" framing for someone who's never heard of it
#   - Whether to include a 1-2 line "what they should expect after they click"
#
# To customize: edit `_build_subject()` and `_build_html_body()` / `_build_text_body()`.
# Keep the {{role}}, {{business_name}}, {{claim_url}}, {{from_name}} placeholders.
# ----------------------------------------------------------------------------
def _build_subject(business_name: str, from_name: str) -> str:
    return f"You're invited to {business_name} on {from_name}"


def _build_html_body(business_name: str, role: str, claim_url: str, from_name: str) -> str:
    return (
        f"<p>You've been invited to join <strong>{business_name}</strong> as <strong>{role}</strong> on {from_name}.</p>"
        f"<p>Click the link below to set your password and get started:</p>"
        f"<p><a href=\"{claim_url}\">{claim_url}</a></p>"
        f"<p>This invitation expires in 14 days.</p>"
        f"<p>— {from_name}</p>"
    )


def _build_text_body(business_name: str, role: str, claim_url: str, from_name: str) -> str:
    return (
        f"You've been invited to join {business_name} as {role} on {from_name}.\n\n"
        f"Set your password and get started: {claim_url}\n\n"
        f"This invitation expires in 14 days.\n\n"
        f"— {from_name}\n"
    )


def send_invite_email(
    to_email: str,
    claim_url: str,
    role: str,
    business_name: str,
    *,
    base_url: Optional[str] = None,
) -> dict:
    """Send the invite email via Postmark. Never raises.

    Returns:
        {"sent": True, "messageId": "..."} on success.
        {"sent": False, "reason": "..."} on any failure or skip.
    """
    api_key = os.getenv("POSTMARK_API_KEY")
    if not api_key:
        # Local dev / unconfigured prod: log + bail. Caller still returns the
        # claim URL in the API response so the owner can copy/paste manually.
        log.info("send_invite_email skipped (POSTMARK_API_KEY unset) — to=%s", to_email)
        return {"sent": False, "reason": "no_api_key"}

    from_email = os.getenv("INVITE_FROM_EMAIL", DEFAULT_FROM_EMAIL)
    from_name = os.getenv("INVITE_FROM_NAME", DEFAULT_FROM_NAME)
    if base_url is None:
        base_url = os.getenv("APP_BASE_URL")

    abs_claim_url = _absolutize(claim_url, base_url)

    payload = {
        "From": f"{from_name} <{from_email}>",
        "To": to_email,
        "Subject": _build_subject(business_name, from_name),
        "HtmlBody": _build_html_body(business_name, role, abs_claim_url, from_name),
        "TextBody": _build_text_body(business_name, role, abs_claim_url, from_name),
        "MessageStream": os.getenv("POSTMARK_MESSAGE_STREAM", "outbound"),
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                POSTMARK_ENDPOINT,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": api_key,
                },
            )
    except httpx.HTTPError as exc:
        log.warning("Postmark request failed for to=%s: %s", to_email, exc)
        return {"sent": False, "reason": f"http_error: {exc}"}

    if resp.status_code >= 400:
        log.warning(
            "Postmark returned %s for to=%s: %s", resp.status_code, to_email, resp.text[:200]
        )
        return {"sent": False, "reason": f"postmark_{resp.status_code}"}

    try:
        body = resp.json()
        message_id = body.get("MessageID")
    except Exception:
        message_id = None

    log.info("Invite email sent — to=%s messageId=%s", to_email, message_id)
    return {"sent": True, "messageId": message_id}
