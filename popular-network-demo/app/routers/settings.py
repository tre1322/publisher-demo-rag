"""Settings router — Phase B.4.

PUT /api/settings/notifications
  body: { cadence: 'each'|'weekly'|'auto',
          notifications: [{key, label, on, via, muted?}] }

PUT /api/settings/ad-autonomy
  body: { enabled: bool }
  Owner-only switch that lets the AI agent spend within owner-set caps on its
  own (app/agent/spend_policy.py). Was browser localStorage until 2026-10-01,
  which the server never read.

Every route here is scoped to the signed-in session's business. Until
2026-10-01 the business came from the request body (default 1), so any
signed-in user could edit any business's settings.

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..auth.deps import get_tenant_id, require_capability
from ..data_lifecycle import export_business
from ..auth.permissions import can
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
    # A business_id sent by older clients is ignored (pydantic drops unknown
    # fields); the business always comes from the session.
    cadence: Optional[Literal["each", "weekly", "auto"]] = None
    notifications: Optional[list[NotificationPref]] = None

    def has_any_change(self) -> bool:
        return self.cadence is not None or self.notifications is not None


class AdAutonomyRequest(BaseModel):
    enabled: bool


# Registered BEFORE /settings/{section} so that route doesn't swallow it.
@router.put("/settings/ad-autonomy")
def update_ad_autonomy(
    body: AdAutonomyRequest,
    request: Request,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    is_super = bool(getattr(request.state, "is_superuser", False))
    role = getattr(request.state, "user_role", None) or ""
    if not is_super and not can(role, "authorize_ad_autonomy"):
        raise HTTPException(status_code=403, detail="Only the business owner can change autonomous ad spend.")
    row = db.get(SettingsRow, business_id)
    if row is None:
        raise HTTPException(status_code=404, detail="settings not found for this business")
    row.ad_autonomy_enabled = body.enabled
    db.commit()
    return {"ok": True, "adAutonomyEnabled": bool(row.ad_autonomy_enabled)}


@router.put("/settings/{section}", dependencies=[Depends(require_capability("manage_settings"))])
def update_settings(
    section: str,
    body: UpdateNotificationsRequest,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if section != "notifications":
        raise HTTPException(
            status_code=404,
            detail=f"settings section '{section}' is not editable in this phase (only 'notifications')",
        )
    if not body.has_any_change():
        raise HTTPException(status_code=422, detail="request must include cadence or notifications")

    row = db.get(SettingsRow, business_id)
    if row is None:
        raise HTTPException(status_code=404, detail="settings not found for this business")

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
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    esc = Escalation(
        business_id=business_id,
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


@router.get("/account/export", dependencies=[Depends(require_capability("manage_settings"))])
def export_my_data(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> JSONResponse:
    """The owner downloads a copy of everything Amplafai holds for their
    business (privacy policy: data on request). Owner-only."""
    data = export_business(db, business_id)
    slug = data["business"].get("slug") or f"business-{business_id}"
    filename = f"{slug}-export-{datetime.utcnow():%Y-%m-%d}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
