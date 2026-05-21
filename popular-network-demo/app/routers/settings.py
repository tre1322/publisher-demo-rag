"""Settings router — Phase B will implement per-section PUTs + escalations POST."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.put("/settings/{section}")
def update_settings(section: str) -> dict:
    raise HTTPException(status_code=501, detail="Phase B — settings updates not wired yet")


@router.post("/escalations")
def create_escalation() -> dict:
    raise HTTPException(status_code=501, detail="Phase B — escalations not wired yet")
