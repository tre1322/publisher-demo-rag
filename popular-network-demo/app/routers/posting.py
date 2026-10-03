"""Phase 5b — automatic posting endpoints.

    GET  /api/posting/status          is it on for this business; which accounts are linked
    POST /api/posting/link            a one-time page where the owner links their accounts
    POST /api/posts/{id}/publish      post an approved post now (also "Try again")

Dormant until AYRSHARE_API_KEY is set; Tier 2 and up (app/auto_posting.py).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auto_posting
from .. import posting_service as ps
from ..auth.deps import get_tenant_id, require_capability
from ..db import get_db
from ..models import Business, Post

router = APIRouter()


@router.get("/posting/status")
def posting_status(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = db.get(Business, business_id)
    out: dict[str, Any] = {
        "configured": ps.is_configured(),
        "planAllows": auto_posting.plan_allows(biz),
        "planMessage": auto_posting.PLAN_MESSAGE,
        "isDemo": bool(biz and biz.is_demo),
        "hasProfile": bool(biz and biz.posting_profile_key),
        "linked": [],
        "error": None,
    }
    if auto_posting.available(biz) and biz.posting_profile_key:
        try:
            out["linked"] = auto_posting.linked_accounts(biz)
        except ps.PostingError as e:
            out["error"] = str(e)
    return out


@router.post("/posting/link", dependencies=[Depends(require_capability("manage_settings"))])
def posting_link(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    from ..notifications import base_url

    biz = db.get(Business, business_id)
    if not ps.is_configured():
        raise HTTPException(status_code=409, detail="Automatic posting isn't switched on yet. Amplafai is setting it up.")
    if not auto_posting.plan_allows(biz):
        raise HTTPException(status_code=403, detail=auto_posting.PLAN_MESSAGE)
    try:
        session = auto_posting.link_url(db, biz, redirect=f"{base_url()}/?tab=settings&b={business_id}")
    except ps.PostingError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"url": session["url"], "expiresAt": session.get("expiresAt")}


@router.post("/posts/{post_id}/publish", dependencies=[Depends(require_capability("publish_post"))])
def publish_now(post_id: int, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    post = db.get(Post, post_id)
    if post is None or post.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"post {post_id} not found")
    if post.status not in ("approved", "published") or post.publish_state == "posted":
        raise HTTPException(status_code=409, detail="Only an approved post that hasn't been posted can be posted from here.")
    biz = db.get(Business, business_id)
    if not auto_posting.available(biz):
        raise HTTPException(status_code=409, detail=auto_posting.PLAN_MESSAGE if ps.is_configured()
                            else "Automatic posting isn't switched on yet; copy it by hand.")
    auto_posting.publish(db, biz, post)
    db.commit()
    db.refresh(post)
    return {"ok": post.publish_state in ("posted", "partial"), "postId": post.id, "status": post.status,
            "publish": auto_posting.payload(post)}
