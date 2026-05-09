"""Publisher attribution at business signup.

Decides which publisher gets revenue credit when a new business signs up.
The result feeds publisher_revenue_share, which feeds the quarterly revenue
settlement that pays publishers their cut of subscription revenue.

Called from the business register flow (src/business_frontend/routes.py)
right after the org is created and the invite is marked used. NOT called
from the Stripe webhook — billing state and attribution are separate concerns.
"""

import logging

from src.business_frontend.auth import get_invite
from src.modules.publishers.database import (
    get_all_publishers_db,
    get_publisher_by_name,
)

logger = logging.getLogger(__name__)


# Initial Y1 share percentage per the partner brief. Renewal windows (Y2 40%,
# performance tier 60%) are opened by separate cron jobs / admin actions.
INITIAL_Y1_SHARE_PCT = 0.50


# ── Building blocks Trevor's policy can call ────────────────────────


def _get_publisher_from_invite(invite_code: str | None) -> dict | None:
    """Return the publisher row tied to this invite code, or None."""
    if not invite_code:
        return None
    inv = get_invite(invite_code)
    if not inv:
        return None
    pub_name = inv.get("publisher")
    if not pub_name:
        return None
    return get_publisher_by_name(pub_name)


def _get_publisher_by_territory(
    state: str | None, city: str | None
) -> dict | None:
    """Match by state, then prefer city substring match against publisher.market.

    Naive v1. Phase 2 will use ZIP polygons or drive-time radii.
    """
    if not state:
        return None
    candidates = [
        p for p in get_all_publishers_db(active_only=True) if p.get("state") == state
    ]
    if not candidates:
        return None
    if city:
        for p in candidates:
            market = (p.get("market") or "").lower()
            if city.lower() in market:
                return p
    return candidates[0]


def _get_default_publisher() -> dict | None:
    """Last-resort fallback. Returns the first active publisher, or None."""
    pubs = get_all_publishers_db(active_only=True)
    return pubs[0] if pubs else None


# ── The decision Trevor owns ────────────────────────────────────────


def attribute_publisher_at_signup(
    organization_id: int,
    invite_code: str | None = None,
    business_state: str | None = None,
    business_city: str | None = None,
    self_serve: bool = False,
) -> tuple[int, str]:
    """Decide which publisher gets revenue credit for this signup.

    v1 policy (decided 2026-05-08, pilot phase):

      INVITE-ONLY. Every signup path in v1 carries an invite_code (there is
      no self-serve registration page yet). If the invite does not resolve
      to an active row in `publishers`, raise — admin must fix the invite or
      activate the publisher before the signup can complete.

    Why these choices:
      - Invite-only matches the partner-brief promise: "you brought them,
        you get credit." When self-serve registration ships (Phase 1.5+),
        this function gets a new branch — not a rewrite.
      - Mismatch raises (vs. silently falling back to a default publisher)
        because invites are admin-curated. A mismatch means a typo or a
        deactivated publisher; both are bugs worth surfacing immediately.
      - Geography is intentionally not checked here. Long-term, publishers
        will license counties (see TODO below), and the invite system
        itself will enforce "you can only invite businesses in counties
        you're licensed for." Until that ships, invite alone is canonical.

    Args:
        organization_id: the freshly-created org row id (logging only).
        invite_code: invite the business used at signup. REQUIRED in v1.
        business_state: 2-letter state code (unused in v1 policy; passed
            through for the future county-license check).
        business_city: city from the registration form (same — future use).
        self_serve: must be False in v1; True will raise. Reserved for the
            Phase 1.5 generic-signup flow.

    Returns:
        (selling_publisher_id, "invite")

    Raises:
        ValueError: if invite_code is missing, or if the invite's publisher
            name does not match an active row in `publishers`.

    TODO (Phase 1.5+): publisher_county_licenses enforcement.

      When publishers start licensing by county, add a step here:
        county = resolve_county(business_state, business_city, business_zip)
        if not is_publisher_licensed_for(pub["id"], county):
            raise ValueError(
                f"Publisher {pub['name']} not licensed for {county}. "
                f"Either re-issue the invite under a licensed publisher "
                f"or sell the {county} license."
            )

      That requires:
        - new table: publisher_county_licenses (publisher_id, county_fips,
          license_start, license_end, license_status)
        - geocoding step: ZIP -> county_fips
        - admin UI for managing licenses
        - business address must include ZIP (v1 form already collects it)
    """
    if not invite_code:
        raise ValueError(
            "v1 attribution requires an invite_code. Self-serve signup is "
            "not yet supported (deferred to Phase 1.5+)."
        )
    if self_serve:
        raise ValueError(
            "self_serve=True is reserved for the Phase 1.5 generic-signup flow."
        )
    pub = _get_publisher_from_invite(invite_code)
    if not pub:
        raise ValueError(
            f"Invite {invite_code!r} does not resolve to an active publisher. "
            f"Fix the invite or activate the referenced publisher before "
            f"retrying signup. (org_id={organization_id})"
        )
    return pub["id"], "invite"
