"""Reviews router — Phase B.5.

POST /api/reviews/{review_id}/respond
  body: { response: str, action: 'save' | 'send' }

  - save : updates owner_response, response_status='draft' (owner is iterating).
  - send : updates owner_response, response_status='approved',
           response_sent_at=now (response is locked in, ready to post publicly).

Approved/sent reviews can still be edited via save (downgrades to 'draft')
or re-sent. There's no destructive operation on a Review row from this
endpoint — review bodies stay immutable (only the OWNER'S response is
editable).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Review

router = APIRouter()


class RespondRequest(BaseModel):
    response: str = Field(max_length=5000)
    action: Literal["save", "send"] = "send"

    @field_validator("response")
    @classmethod
    def _check_response(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("response cannot be empty")
        return v.strip()


def _review_payload(r: Review) -> dict[str, Any]:
    return {
        "id": r.external_id or f"r{r.id}",
        "internalId": r.id,
        "platform": r.platform,
        "stars": r.stars,
        "when": r.when_label,
        "author": r.author,
        "body": r.body,
        "response": r.owner_response,
        "responseStatus": r.response_status,
        "responseSentAt": r.response_sent_at.isoformat() if r.response_sent_at else None,
    }


@router.post("/reviews/{review_id}/respond")
def respond(
    review_id: int,
    body: RespondRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    r = db.get(Review, review_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"review {review_id} not found")

    r.owner_response = body.response
    if body.action == "send":
        r.response_status = "approved"
        r.response_sent_at = datetime.utcnow()
    else:  # save
        r.response_status = "draft"
        # Don't clear response_sent_at — preserves "this was once sent" history
        # if the owner downgrades a sent reply back to draft for tweaking.

    db.commit()
    return {"ok": True, "review": _review_payload(r)}
