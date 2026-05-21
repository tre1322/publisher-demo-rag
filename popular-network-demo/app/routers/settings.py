"""Settings router — Phase B.4.

PUT /api/settings/notifications
  body: { cadence: 'each'|'weekly'|'auto',
          notifications: [{key, label, on, via, muted?}] }

POST /api/escalations
  body: { message: str, business_id?: int }
  Records a "Talk to a human" submission. Actual notification routing (email
  to publisher rep + Popular Network team) is Phase F.

Account and Connections sub-tabs of SettingsView are read-only in Phase B.4
(no editable state worth persisting yet — tier upgrade, OAuth account adds,
billing changes are Phase F territory).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Escalation, SettingsRow

router = APIRouter()


class NotificationPref(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    on: bool
    via: str = Field(default="", max_length=160)
    muted: Optional[bool] = None


class UpdateNotificationsRequest(BaseModel):
    cadence: Optional[Literal["each", "weekly", "auto"]] = None
    notifications: Optional[list[NotificationPref]] = None
    business_id: int = Field(default=1, ge=1)

    def has_any_change(self) -> bool:
        return self.cadence is not None or self.notifications is not None


@router.put("/settings/{section}")
def update_settings(
    section: str,
    body: UpdateNotificationsRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if section != "notifications":
        raise HTTPException(
            status_code=404,
            detail=f"settings section '{section}' is not editable in this phase (only 'notifications')",
        )
    if not body.has_any_change():
        raise HTTPException(status_code=422, detail="request must include cadence or notifications")

    row = db.get(SettingsRow, body.business_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"settings for business {body.business_id} not found")

    if body.cadence is not None:
        row.cadence = body.cadence
    if body.notifications is not None:
        # Strip None muted values so the stored JSON matches what the frontend
        # sends (no spurious "muted": null in non-muted rows).
        row.notifications_json = [
            {k: v for k, v in pref.model_dump().items() if not (k == "muted" and v is None)}
            for pref in body.notifications
        ]

    db.commit()
    return {
        "ok": True,
        "settings": {
            "cadence": row.cadence,
            "notifications": row.notifications_json or [],
        },
    }


class CreateEscalationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    business_id: int = Field(default=1, ge=1)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        return v


@router.post("/escalations")
def create_escalation(
    body: CreateEscalationRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    esc = Escalation(
        business_id=body.business_id,
        message=body.message,
        created_at=datetime.utcnow(),
    )
    db.add(esc)
    db.commit()
    return {
        "ok": True,
        "escalation": {
            "id": esc.id,
            "createdAt": esc.created_at.isoformat(),
            "businessId": esc.business_id,
        },
    }
