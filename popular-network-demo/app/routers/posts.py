"""Posts router — Phase B will wire create/edit/reschedule from Compose + Calendar."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/posts")
def create_post() -> dict:
    raise HTTPException(status_code=501, detail="Phase B — Compose wiring not implemented yet")


@router.put("/posts/{post_id}")
def update_post(post_id: int) -> dict:
    raise HTTPException(status_code=501, detail="Phase B — post editing not implemented yet")
