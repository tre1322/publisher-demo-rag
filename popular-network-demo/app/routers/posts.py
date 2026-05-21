"""Posts router — Phases B.2 + B.3.

PUT /api/posts/{id}      — B.2 partial update (title, draft, date, platform, reasoning)
POST /api/posts          — B.3 create from Compose. status='draft' or 'pending'.
                           When 'pending', also creates an Approval row so the
                           post lands in the Approvals queue.

Mutable fields on PUT: title, draft, date, platform, reasoning.
Immutable: status (lifecycle), id, business_id, external_id, timestamps.

Status changes flow through the approval / publish lifecycle (B.1 decide
endpoint), not through PUT.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Approval, Post

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# "web" added in B.3 for blog posts composed via the Web tab in ComposeView.
_PLATFORMS = {"fb", "ig", "gbp", "meta", "google", "web"}


class UpdatePostRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=280)
    draft: Optional[str] = Field(default=None, max_length=5000)
    date: Optional[str] = Field(default=None)
    platform: Optional[str] = Field(default=None)
    reasoning: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _DATE_RE.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"invalid date: {e}") from e
        return v

    @field_validator("platform")
    @classmethod
    def _check_platform(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in _PLATFORMS:
            raise ValueError(f"platform must be one of {sorted(_PLATFORMS)}")
        return v

    def has_any_change(self) -> bool:
        return any(
            getattr(self, f) is not None
            for f in ("title", "draft", "date", "platform", "reasoning")
        )


def _post_payload(p: Post) -> dict[str, Any]:
    return {
        "id": p.external_id or f"p{p.id}",
        "internalId": p.id,
        "date": p.date,
        "platform": p.platform,
        "status": p.status,
        "title": p.title,
        "draft": p.draft,
        "reasoning": p.reasoning,
    }


class CreatePostRequest(BaseModel):
    business_id: int = Field(default=1, ge=1)
    platform: str
    status: Literal["draft", "pending"]
    title: str = Field(max_length=280)
    draft: str = Field(max_length=5000)
    date: Optional[str] = Field(default=None)
    reasoning: Optional[str] = Field(default=None, max_length=5000)
    # When status='pending', whether to also create an Approval row.
    # Default True — that's the whole point of "Send to approval queue".
    create_approval: bool = True

    @field_validator("platform")
    @classmethod
    def _check_platform(cls, v: str) -> str:
        if v not in _PLATFORMS:
            raise ValueError(f"platform must be one of {sorted(_PLATFORMS)}")
        return v

    @field_validator("date")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _DATE_RE.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"invalid date: {e}") from e
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v.strip()

    @field_validator("draft")
    @classmethod
    def _check_draft(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("draft cannot be empty")
        return v.strip()


@router.post("/posts")
def create_post(body: CreatePostRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    now = datetime.utcnow()
    post = Post(
        business_id=body.business_id,
        date=body.date or now.strftime("%Y-%m-%d"),
        platform=body.platform,
        status=body.status,
        title=body.title,
        draft=body.draft,
        reasoning=(body.reasoning.strip() if body.reasoning else None) or None,
        created_at=now,
    )
    db.add(post)
    db.flush()  # populate post.id

    approval = None
    if body.status == "pending" and body.create_approval:
        approval = Approval(
            business_id=body.business_id,
            post_id=post.id,
            kind="post",
            platform=body.platform,
            title=body.title,
            draft=body.draft,
            note=body.reasoning,
            created_at=now,
        )
        db.add(approval)
        db.flush()

    db.commit()

    out: dict[str, Any] = {
        "ok": True,
        "post": _post_payload(post),
    }
    if approval is not None:
        out["approval"] = {
            "id": approval.external_id or f"a{approval.id}",
            "internalId": approval.id,
            "kind": approval.kind,
            "platform": approval.platform,
            "title": approval.title,
            "draft": approval.draft,
            "note": approval.note,
            "postId": approval.post_id,
        }
    return out


@router.put("/posts/{post_id}")
def update_post(
    post_id: int,
    body: UpdatePostRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not body.has_any_change():
        raise HTTPException(status_code=422, detail="request body must include at least one field to update")

    p = db.get(Post, post_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"post {post_id} not found")
    if p.status == "published":
        # Don't let typo fixes silently rewrite live content. If the owner
        # really wants to amend a published post, that's a different flow
        # (and we'll need to record the change for transparency).
        raise HTTPException(
            status_code=409,
            detail=f"post {post_id} is published — published posts are immutable from the edit endpoint",
        )

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail="title cannot be empty")
        p.title = body.title.strip()
    if body.draft is not None:
        if not body.draft.strip():
            raise HTTPException(status_code=422, detail="draft cannot be empty")
        p.draft = body.draft.strip()
    if body.date is not None:
        p.date = body.date
    if body.platform is not None:
        p.platform = body.platform
    if body.reasoning is not None:
        # Reasoning CAN be cleared to empty (owner removes the agent's note).
        p.reasoning = body.reasoning.strip() or None

    db.commit()
    return {"ok": True, "post": _post_payload(p)}
