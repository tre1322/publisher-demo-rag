"""Marketing plan router — Phase B.8.

PUT /api/marketing-plan
  body: { audience?, valueProp?, customerLanguage?: list[str] }

Phase B.8 scope is intentionally narrow: text fields + the customer-language
chip list. The structured fields (switchingForces, proofPoints, channels,
q3Goals) stay read-only for now — they're shaped enough that owner editing
needs a structured UI, not a textarea.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import MarketingPlan

router = APIRouter()


class UpdateMarketingPlanRequest(BaseModel):
    audience: Optional[str] = Field(default=None, max_length=5000)
    valueProp: Optional[str] = Field(default=None, max_length=5000)
    customerLanguage: Optional[list[str]] = Field(default=None)
    business_id: int = Field(default=1, ge=1)

    @field_validator("audience")
    @classmethod
    def _check_audience(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("audience cannot be empty")
        return v

    @field_validator("valueProp")
    @classmethod
    def _check_value_prop(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("valueProp cannot be empty")
        return v

    @field_validator("customerLanguage")
    @classmethod
    def _check_customer_language(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        cleaned = [s.strip() for s in v if s and s.strip()]
        # Allow empty list (user clearing all chips) — only reject if input
        # contains non-string garbage at the type-validator stage above.
        return cleaned

    def has_any_change(self) -> bool:
        return any(getattr(self, f) is not None for f in ("audience", "valueProp", "customerLanguage"))


def _plan_payload(mp: MarketingPlan) -> dict[str, Any]:
    return {
        "audience": mp.audience,
        "valueProp": mp.value_prop,
        "switching": mp.switching_json,
        "customerLanguage": mp.customer_language_json,
        "proofPoints": mp.proof_points_json,
        "channels": mp.channels_json,
        "q3Goals": mp.q3_goals_json,
    }


@router.put("/marketing-plan")
def update_marketing_plan(
    body: UpdateMarketingPlanRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not body.has_any_change():
        raise HTTPException(status_code=422, detail="request must include at least one editable field")

    mp = db.get(MarketingPlan, body.business_id)
    if mp is None:
        raise HTTPException(status_code=404, detail=f"marketing plan for business {body.business_id} not found")

    if body.audience is not None:
        mp.audience = body.audience
    if body.valueProp is not None:
        mp.value_prop = body.valueProp
    if body.customerLanguage is not None:
        mp.customer_language_json = body.customerLanguage
    mp.updated_at = datetime.utcnow()

    db.commit()
    return {"ok": True, "marketingPlan": _plan_payload(mp)}
