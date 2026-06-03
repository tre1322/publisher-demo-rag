"""Create / pause LinkedIn sponsored campaigns.

LinkedIn models paid ads as a hierarchy:
    ad account → campaign group → campaign → creative

`create_boost_campaign` orchestrates the minimum of that hierarchy to get a
live, spending campaign: it creates a one-off campaign group to hold the
campaign, then the campaign itself. It returns the campaign URN
(urn:li:sponsoredCampaign:NNN) which we persist as
AdCampaign.external_campaign_id — the same slot the mock fills with
`mock_linkedin_NNNN`. That symmetry is what makes the mock→real swap a no-op
for every downstream reader.

⚠️  UNTESTED AGAINST THE LIVE API. Written ahead of MDP approval against the
v202405 docs. Field shapes (dailyBudget as a decimal string, runSchedule.start
as epoch-ms, targetingCriteria's include/and/or nesting) are the documented
ones, but the first real call must be validated against a LinkedIn test ad
account before we trust autonomous spend. Tracked in the integration plan.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .client import LinkedInClient
from .errors import LinkedInAPIError, LinkedInProvisioningError

# Default geo target if the caller supplies no targeting hint: United States.
# Real targeting will be owner-configurable UI work in a later pass.
_DEFAULT_GEO_URN = "urn:li:geo:103644278"  # United States


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _cents_to_amount(cents: int) -> str:
    """LinkedIn money fields are decimal strings, e.g. 1000c → '10.00'."""
    return f"{cents / 100:.2f}"


def create_boost_campaign(
    client: LinkedInClient,
    *,
    account_urn: str,
    name: str,
    daily_budget_cents: int,
    duration_days: int,
    currency: str = "USD",
    geo_urns: Optional[list[str]] = None,
    started_at: Optional[datetime] = None,
) -> str:
    """Create a sponsored campaign and return its URN. Raises
    LinkedInProvisioningError on any API failure so the caller can surface it
    rather than silently mock."""
    start = started_at or datetime.utcnow()
    end = start + timedelta(days=duration_days)
    geos = geo_urns or [_DEFAULT_GEO_URN]

    try:
        group_urn = client.create(
            "/rest/adCampaignGroups",
            urn_prefix="urn:li:sponsoredCampaignGroup",
            json={
                "account": account_urn,
                "name": f"{name} (group)",
                "status": "ACTIVE",
                "runSchedule": {"start": _epoch_ms(start), "end": _epoch_ms(end)},
            },
        )

        campaign_urn = client.create(
            "/rest/adCampaigns",
            urn_prefix="urn:li:sponsoredCampaign",
            json={
                "account": account_urn,
                "campaignGroup": group_urn,
                "name": name,
                "type": "SPONSORED_UPDATES",
                "costType": "CPM",
                "dailyBudget": {"amount": _cents_to_amount(daily_budget_cents), "currencyCode": currency},
                "locale": {"country": "US", "language": "en"},
                "runSchedule": {"start": _epoch_ms(start), "end": _epoch_ms(end)},
                # Minimal valid targeting: members located in the geo(s).
                "targetingCriteria": {
                    "include": {
                        "and": [
                            {"or": {"urn:li:adTargetingFacet:locations": geos}},
                        ]
                    }
                },
                # Created PAUSED would be safer, but we want the boost live on
                # schedule. The owner's monthly cap + tick/poll still bound spend.
                "status": "ACTIVE",
            },
        )
        return campaign_urn
    except LinkedInAPIError as e:
        raise LinkedInProvisioningError(f"campaign create failed: {e}") from e


def pause_campaign(client: LinkedInClient, campaign_urn: str) -> None:
    """Pause a live campaign. campaign_urn → urn:li:sponsoredCampaign:NNN."""
    campaign_id = campaign_urn.rsplit(":", 1)[-1]
    try:
        client.post(
            f"/rest/adCampaigns/{campaign_id}",
            headers={"X-RestLi-Method": "PARTIAL_UPDATE"},
            json={"patch": {"$set": {"status": "PAUSED"}}},
        )
    except LinkedInAPIError as e:
        raise LinkedInProvisioningError(f"campaign pause failed: {e}") from e


def list_campaigns(client: LinkedInClient, account_urn: str) -> list[dict]:
    """All campaigns under an ad account (for reconciliation / status views)."""
    account_id = account_urn.rsplit(":", 1)[-1]
    data = client.get(
        "/rest/adCampaigns",
        params={"q": "search", "search.account.values[0]": account_urn, "_account": account_id},
    )
    return data.get("elements", [])
