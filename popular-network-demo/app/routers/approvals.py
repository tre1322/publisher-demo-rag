"""Approvals router — Phase B.1.

POST /api/approvals/{id}/decide
  body: { decision: "approve" | "edit" | "reject", edited_draft?: str }

Cascade rules (decided 2026-05-21):

- approve  → approval.decision='approved', decided_at=now.
             post-kind  → materialize a new Post (status='approved', draft=approval.draft)
                         and link approval.post_id back. Approvals are proposals;
                         they become Posts only when accepted.
             review-kind → find the linked Review (FK if set, else match on
                          original_review_text==review.body), set
                          review.owner_response = approval.draft,
                          response_status='approved', response_sent_at=now.

- edit     → same as approve but the new draft text comes from the request,
             and approval.draft is updated to match (so the queue's audit
             trail reflects what the owner actually shipped). decision='edited'.

- reject   → approval.decision='rejected'. NOTHING materializes. The linked
             review (if any) stays in 'draft' so the owner can craft a
             different response later. Recoverable, not destructive.

- boost-kind (agent ad proposals) → _decide_ad_proposal. Approving applies
             exactly what was proposed: boost-* launches the campaign through
             the same real-platform path as /ads/campaigns/{id}/approve,
             pause-* pauses it, allocate-* sets the cap (and the owner
             ceiling) from payload_json. A platform failure returns 502 with
             the approval left undecided. Pause/budget proposals can't be edited.

Idempotency: a row whose decision is already non-null returns 409 Conflict.
That keeps optimistic-UI rollback honest — a double-click can't silently
duplicate a Post.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.spend_policy import budget_row, current_month_year
from ..auth.deps import get_tenant_id, require_capability
from ..db import get_db
from ..models import AdCampaign, AdPlatformBudget, Approval, Business, Post, Review
from .. import managed_ads
from .ads import AD_PLATFORMS, actor, budget_payload, schedule_approved_campaign

router = APIRouter()


# Deciding an item needs the same permission as doing the thing directly:
# approving a spend proposal is spending, approving a review reply is replying.
_DECIDE_CAPABILITY = {
    "post": "publish_post",
    "review": "respond_to_review",
    "boost": "manage_ads",
}


def _post_date(a: Approval, now: datetime) -> str:
    """The calendar day an approved post lands on: the day it was planned for
    (first-week drafts carry one), unless that day has already passed."""
    today = now.strftime("%Y-%m-%d")
    planned = str((a.payload_json or {}).get("plannedDate") or "")
    if len(planned) == 10 and planned > today:
        try:
            datetime.strptime(planned, "%Y-%m-%d")
            return planned
        except ValueError:
            pass
    return today


class DecideRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_draft: Optional[str] = Field(default=None, max_length=5000)


def _approval_to_payload(a: Approval) -> dict[str, Any]:
    return {
        "id": a.external_id or f"a{a.id}",
        "internalId": a.id,
        "kind": a.kind,
        "platform": a.platform,
        "title": a.title,
        "draft": a.draft,
        "decision": a.decision,
        "decidedAt": a.decided_at.isoformat() if a.decided_at else None,
        "postId": a.post_id,
        "reviewId": a.review_id,
    }


def _find_review_for_approval(db: Session, a: Approval) -> Review | None:
    if a.review_id is not None:
        return db.get(Review, a.review_id)
    if not a.original_review_text:
        return None
    return (
        db.query(Review)
        .filter(Review.business_id == a.business_id, Review.body == a.original_review_text)
        .first()
    )


@router.post("/approvals/{approval_id}/decide")
def decide(
    approval_id: int,
    body: DecideRequest,
    request: Request,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # Tenant-scoped lookup: an approval belonging to another business is
    # indistinguishable from a nonexistent one (404 before the 409 check —
    # no cross-tenant existence oracle).
    a = (
        db.query(Approval)
        .filter(Approval.id == approval_id, Approval.business_id == business_id)
        .first()
    )
    if a is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    # Unknown kinds fall back to the strictest gate rather than none.
    require_capability(_DECIDE_CAPABILITY.get(a.kind or "post", "manage_settings"))(request)
    if a.decision is not None:
        raise HTTPException(
            status_code=409,
            detail=f"approval {approval_id} already decided ({a.decision})",
        )
    if body.decision == "edit" and not (body.edited_draft and body.edited_draft.strip()):
        raise HTTPException(status_code=422, detail="edit requires a non-empty edited_draft")

    # Ad proposals (boost / pause / budget) have their own path: each one must
    # actually DO what the owner approved, and a failed platform call must
    # leave the approval undecided.
    if a.kind == "boost":
        return _decide_ad_proposal(db, a, body, by=actor(db, request))

    now = datetime.utcnow()
    a.decided_at = now

    if body.decision == "reject":
        a.decision = "rejected"
        db.commit()
        return {"ok": True, "approval": _approval_to_payload(a)}

    # approve or edit: choose which text wins
    final_draft = body.edited_draft.strip() if body.decision == "edit" else a.draft
    if body.decision == "edit":
        a.draft = final_draft
    a.decision = "edited" if body.decision == "edit" else "approved"

    if a.kind == "post":
        new_post = Post(
            business_id=a.business_id,
            date=_post_date(a, now),
            platform=a.platform,
            status="approved",
            title=a.title,
            draft=final_draft,
            reasoning=a.note,
            created_at=now,
            decided_at=now,
        )
        db.add(new_post)
        db.flush()  # populate new_post.id
        a.post_id = new_post.id
        # Phase 5b: Tier 2+ with linked accounts → it publishes on its day.
        from .. import auto_posting

        auto_posting.queue(db, db.get(Business, a.business_id), new_post)
        db.commit()
        return {
            "ok": True,
            "approval": _approval_to_payload(a),
            "post": {
                "id": new_post.external_id or f"p{new_post.id}",
                "internalId": new_post.id,
                "date": new_post.date,
                "platform": new_post.platform,
                "status": new_post.status,
                "title": new_post.title,
                "draft": new_post.draft,
                "reasoning": new_post.reasoning,
            },
        }

    if a.kind == "review":
        review = _find_review_for_approval(db, a)
        if review is not None:
            review.owner_response = final_draft
            review.response_status = "approved"
            review.response_sent_at = now
            a.review_id = review.id
        db.commit()
        return {
            "ok": True,
            "approval": _approval_to_payload(a),
            "review": {
                "id": review.external_id or f"r{review.id}" if review else None,
                "responseStatus": review.response_status if review else None,
                "ownerResponse": review.owner_response if review else None,
            } if review else None,
        }

    db.commit()
    return {"ok": True, "approval": _approval_to_payload(a)}


# --------------------------------------------------------------------------- #
# Ad proposals — kind='boost' rows carry three different agent actions:
#   boost-{campaign_id}         launch a pending campaign
#   pause-{campaign_id}         pause a running campaign
#   allocate-{platform}-{ts}    set a platform's monthly cap
# Before 2026-10-01 only boost-* did anything when approved; pause and
# allocate approvals were recorded and silently ignored.
# --------------------------------------------------------------------------- #

_AD_ACTIONS = ("boost", "pause", "allocate")


def _ad_proposal_action(a: Approval) -> str:
    action = (a.payload_json or {}).get("action")
    if action in _AD_ACTIONS:
        return action
    ext = a.external_id or ""
    for prefix in _AD_ACTIONS:  # legacy rows that pre-date payload_json
        if ext.startswith(prefix + "-"):
            return prefix
    return "unknown"


def _campaign_for_proposal(db: Session, a: Approval) -> AdCampaign | None:
    """The campaign a boost-/pause- proposal points at, same business only."""
    campaign_id = (a.payload_json or {}).get("campaign_id")
    if campaign_id is None and a.external_id and "-" in a.external_id:
        try:
            campaign_id = int(a.external_id.split("-", 1)[1])
        except ValueError:
            return None
    if campaign_id is None:
        return None
    c = db.get(AdCampaign, int(campaign_id))
    if c is None or c.business_id != a.business_id:
        return None
    return c


def _campaign_brief(c: AdCampaign | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": c.id, "status": c.status, "externalCampaignId": c.external_campaign_id}


def _decide_ad_proposal(db: Session, a: Approval, body: DecideRequest, *, by: str = "owner") -> dict[str, Any]:
    action = _ad_proposal_action(a)
    now = datetime.utcnow()

    if body.decision == "edit" and action in ("pause", "allocate"):
        raise HTTPException(
            status_code=422,
            detail="This proposal can't be edited. Approve or reject it, or ask the agent for a different amount.",
        )

    if body.decision == "reject":
        campaign = _campaign_for_proposal(db, a) if action == "boost" else None
        # Cancel the pending campaign so it doesn't linger as a zombie.
        if campaign is not None and campaign.status == "pending_approval":
            campaign.status = "cancelled"
            campaign.ended_at = now
        a.decision = "rejected"
        a.decided_at = now
        db.commit()
        return {"ok": True, "approval": _approval_to_payload(a), "campaign": _campaign_brief(campaign)}

    result: dict[str, Any] = {}
    if action == "boost":
        campaign = _campaign_for_proposal(db, a)
        if campaign is not None and campaign.status == "pending_approval":
            # Same path as POST /ads/campaigns/{id}/approve. Raises 502 on a
            # platform failure BEFORE anything below is mutated.
            schedule_approved_campaign(db, a.business_id, campaign, by=f"{by}, approving the agent's proposal")
        result["campaign"] = _campaign_brief(campaign)
    elif action == "pause":
        campaign = _campaign_for_proposal(db, a)
        if campaign is not None and campaign.status in ("active", "scheduled"):
            # Demo: pauses at once. Real client: Amplafai pauses it on the
            # platform, so this becomes a pause request (app/managed_ads.py).
            biz = db.get(Business, a.business_id)
            try:
                result["message"] = managed_ads.change(
                    db, biz, campaign, "pause", by=f"{by}, approving the agent's proposal", source="agent",
                )["message"]
            except managed_ads.ManagedAdsError as e:
                result["message"] = str(e)
        result["campaign"] = _campaign_brief(campaign)
    elif action == "allocate":
        payload = a.payload_json or {}
        platform = payload.get("platform")
        cents = payload.get("monthly_cents")
        if platform not in AD_PLATFORMS or not isinstance(cents, int) or cents < 0:
            raise HTTPException(
                status_code=409,
                detail="This older budget proposal has no amount attached, so it can't be applied. Reject it and ask the agent again.",
            )
        row = budget_row(db, a.business_id, platform)
        if row is None:
            row = AdPlatformBudget(
                business_id=a.business_id, platform=platform, month_year=current_month_year(),
                monthly_cap_cents=cents, owner_cap_cents=cents, spend_cents=0, status="active",
            )
            db.add(row)
        else:
            # Approving IS the owner's authorization, so it moves the ceiling too.
            row.monthly_cap_cents = cents
            row.owner_cap_cents = cents
            row.updated_at = now
        db.flush()
        result["budget"] = budget_payload(row)
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Unrecognized ad proposal '{a.external_id}'. Reject it and ask the agent again.",
        )

    if body.decision == "edit":
        a.draft = body.edited_draft.strip()
    a.decision = "edited" if body.decision == "edit" else "approved"
    a.decided_at = now
    db.commit()
    return {"ok": True, "approval": _approval_to_payload(a), **result}
