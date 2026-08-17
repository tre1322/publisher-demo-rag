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

Idempotency: a row whose decision is already non-null returns 409 Conflict.
That keeps optimistic-UI rollback honest — a double-click can't silently
duplicate a Post.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.deps import get_tenant_id
from ..db import get_db
from ..models import AdCampaign, Approval, Post, Review

router = APIRouter()


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
    if a.decision is not None:
        raise HTTPException(
            status_code=409,
            detail=f"approval {approval_id} already decided ({a.decision})",
        )
    if body.decision == "edit" and not (body.edited_draft and body.edited_draft.strip()):
        raise HTTPException(status_code=422, detail="edit requires a non-empty edited_draft")

    now = datetime.utcnow()
    a.decided_at = now

    if body.decision == "reject":
        a.decision = "rejected"
        # For boost-kind, cancel the linked AdCampaign so we don't leave
        # a zombie pending_approval row.
        if a.kind == "boost":
            campaign = _find_campaign_for_boost_approval(db, a)
            if campaign is not None and campaign.status == "pending_approval":
                campaign.status = "cancelled"
                campaign.ended_at = now
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
            date=now.strftime("%Y-%m-%d"),
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

    # kind == "boost" — advance the linked AdCampaign to scheduled
    # (the campaign was already created with status='pending_approval' by
    # /api/ads/campaigns when origin=agent_proposed).
    campaign = _find_campaign_for_boost_approval(db, a)
    if campaign is not None and campaign.status == "pending_approval":
        campaign.status = "scheduled"
        campaign.approved_by = "owner"
        campaign.scheduled_for = now
        if not campaign.external_campaign_id:
            from .ads import _mock_external_id
            campaign.external_campaign_id = _mock_external_id(campaign.platform)
    db.commit()
    return {
        "ok": True,
        "approval": _approval_to_payload(a),
        "campaign": {
            "id":                 campaign.id if campaign else None,
            "status":             campaign.status if campaign else None,
            "externalCampaignId": campaign.external_campaign_id if campaign else None,
        } if campaign else None,
    }


def _find_campaign_for_boost_approval(db: Session, a: Approval) -> AdCampaign | None:
    """Find the AdCampaign matching a boost-kind approval.

    Boost approvals are created by POST /api/ads/campaigns w/
    origin=agent_proposed; the Approval's external_id is set to
    'boost-{campaign_id}'. Parse that suffix back out to find the row.
    """
    if a.kind != "boost" or not a.external_id or not a.external_id.startswith("boost-"):
        return None
    try:
        campaign_id = int(a.external_id.split("-", 1)[1])
    except (ValueError, IndexError):
        return None
    return db.get(AdCampaign, campaign_id)
