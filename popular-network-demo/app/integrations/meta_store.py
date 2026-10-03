"""DB glue between AdConnection rows and the pure `meta` API package.

A client's Meta connection is a link, not a sign-in: Amplafai staff enter the
client's ad account (and Facebook Page) in the admin console after the client
has added Amplafai's business as a partner on it. The server checks the
account with Amplafai's own token and stores what campaigns need on the
AdConnection row (platform "fb_ig"):

    external_account_id   act_<id>
    account_label         the ad account's name in Meta
    connected_user_name   who at Amplafai linked it
    config_json           {pageId, pageName, instagramId, geo, currency, timezone, spendCapCents}

No per-client token is stored; oauth_token stays empty.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import AdConnection, Business
from . import meta

PLATFORM = "fb_ig"
DEFAULT_RADIUS_MILES = 15


class LinkProblem(Exception):
    """The link can't be made as asked. Message is staff-safe; `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def get_connection(db: Session, business_id: int) -> Optional[AdConnection]:
    return (db.query(AdConnection)
            .filter(AdConnection.business_id == business_id, AdConnection.platform == PLATFORM)
            .one_or_none())


def connection_is_live(conn: Optional[AdConnection]) -> bool:
    """Linked through the API (a real act_ id and its settings), not a demo connection."""
    return bool(conn is not None and conn.status == "connected"
                and (conn.external_account_id or "").startswith("act_") and conn.config_json)


def summary(conn: Optional[AdConnection]) -> Optional[dict[str, Any]]:
    if not connection_is_live(conn):
        return None
    cfg = conn.config_json or {}
    return {"accountId": conn.external_account_id, "accountName": conn.account_label,
            "pageId": cfg.get("pageId"), "pageName": cfg.get("pageName"),
            "instagramId": cfg.get("instagramId"), "geo": cfg.get("geo"),
            "currency": cfg.get("currency"), "timezone": cfg.get("timezone"),
            "spendCapCents": cfg.get("spendCapCents") or 0,
            "linkedBy": conn.connected_user_name,
            "linkedAt": conn.last_synced_at.isoformat() if conn.last_synced_at else None}


def _digits(raw: Optional[str], what: str) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    s = re.sub(r"[\s-]", "", str(raw).strip())
    if s.startswith("act_"):
        s = s[4:]
    if not s.isdigit():
        raise LinkProblem(f"The {what} should be a number (Meta shows it in Business settings).")
    return s


def link(db: Session, biz: Business, *, ad_account_id: str, page_id: str, instagram_id: Optional[str] = None,
         zip_code: Optional[str] = None, radius_miles: Optional[int] = None, by: str,
         client: Optional["meta.MetaClient"] = None) -> AdConnection:
    """Check the account with Amplafai's token and store the link. Never commits."""
    if not meta.is_live():
        raise LinkProblem("Meta's API isn't switched on for this server yet (META_ACCESS_TOKEN and "
                          "META_APP_SECRET). Until then, run this client's Meta campaigns by hand.", 409)
    if biz.is_demo:
        raise LinkProblem("Demo accounts use the simulator, not a real ad account.", 409)
    act = "act_" + (_digits(ad_account_id, "ad account ID") or "")
    if act == "act_":
        raise LinkProblem("Enter the client's ad account ID.")
    page = _digits(page_id, "Facebook Page ID")
    if not page:
        raise LinkProblem("Enter the client's Facebook Page ID; their boosted posts run as that Page.")
    ig = _digits(instagram_id, "Instagram account ID")
    zip_code = (zip_code or "").strip() or None
    if zip_code and not re.fullmatch(r"\d{5}", zip_code):
        raise LinkProblem("The ZIP code should be 5 digits.")
    radius = max(meta.MIN_RADIUS_MILES, min(meta.MAX_RADIUS_MILES, int(radius_miles or DEFAULT_RADIUS_MILES)))

    own = client is None
    c = client or meta.MetaClient()
    try:
        try:
            account = meta.read_account(c, act)
            page_info = meta.read_page(c, page)
            geo = ({"zip": zip_code} if zip_code else meta.find_city(c, biz.location or ""))
        except meta.MetaAPIError as e:
            # 100 / 803: "object does not exist or can't be loaded due to missing permissions".
            if e.kind == "permission" or e.code in (100, 803):
                raise LinkProblem("Meta wouldn't show Amplafai that ad account or Page. Check both IDs, and that the "
                                  "client added Amplafai's business as a partner with permission to manage "
                                  f"campaigns. (Meta said: {e.raw_message})", 409)
            raise LinkProblem(str(e), 502)
        except meta.MetaError as e:
            raise LinkProblem(str(e), 502)
    finally:
        if own:
            c.close()

    if account["currency"] != "USD":
        raise LinkProblem(f"That ad account bills in {account['currency']}. Amplafai's budgets and caps are in "
                          "US dollars, so only USD ad accounts can be linked.", 409)
    if account["status"] != 1:
        raise LinkProblem(f"That ad account is {account['statusLabel']} in Meta. It has to be active to link it.", 409)
    if geo is None:
        raise LinkProblem(f"Couldn't find \"{biz.location}\" on Meta's map. Enter the business's ZIP code instead.")
    if "zip" not in geo:
        geo["radiusMiles"] = radius

    conn = get_connection(db, biz.id)
    if conn is None:
        conn = AdConnection(business_id=biz.id, platform=PLATFORM, account_label=account["name"])
        db.add(conn)
    conn.status = "connected"
    conn.external_account_id = act
    conn.account_label = account["name"][:160] or act
    conn.oauth_token = None
    conn.connected_user_name = by[:120]
    conn.last_synced_at = datetime.utcnow()
    conn.config_json = {"pageId": page, "pageName": page_info.get("name"), "instagramId": ig, "geo": geo,
                        "currency": account["currency"], "timezone": account.get("timezone"),
                        "spendCapCents": account.get("spendCapCents") or 0}
    db.flush()
    return conn


def unlink(db: Session, conn: AdConnection) -> None:
    """Stop using the API for this client (Meta itself is untouched). Never commits."""
    conn.status = "disconnected"
    conn.external_account_id = None
    conn.oauth_token = None
    conn.config_json = None
    conn.connected_user_name = None
