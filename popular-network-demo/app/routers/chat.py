"""Chat router — Phase C will wire live Claude-backed conversation + tool use.

The seeded conversation is delivered as part of /api/bootstrap; this router
will replace that with a streaming endpoint once we're ready to talk to Claude.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/chat/turn")
def take_turn() -> dict:
    raise HTTPException(status_code=501, detail="Phase C — live chat not implemented yet")
