"""Posts router — Phase B.2.

PUT /api/posts/{id}
  body: { title?, draft?, date?, platform?, reasoning? }   (partial update)

Mutable fields:
  - title       (max 280)
  - draft       (max 5000)
  - date        ('YYYY-MM-DD', validated)
  - platform    (one of fb|ig|gbp|meta|google)
  - reasoning   (max 5000, may be null)

Immutable: status, id, business_id, external_id, created_at, decided_at,
published_at. Status changes flow through the approval / publish lifecycle,
not through ad-hoc edits — keeping that boundary out of the edit endpoint
prevents accidental "drafts that got pushed live via a typo fix."

Phase B will add POST /api/posts (Compose) in B.3.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Post

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLATFORMS = {"fb", "ig", "gbp", "meta", "google"}


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


@router.post("/posts")
def create_post() -> dict:
    # Phase B.3 (Compose) will fill this in.
    raise HTTPException(status_code=501, detail="Phase B.3 — Compose wiring not implemented yet")


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
