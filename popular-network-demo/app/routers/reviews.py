"""Reviews router — Phase B will implement respond/edit."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/reviews/{review_id}/respond")
def respond(review_id: int) -> dict:
    raise HTTPException(status_code=501, detail="Phase B — review responses not wired yet")
