"""Performance router — Phase B will add ?period= filtering + insight regen."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PerformanceSummary
from .bootstrap import _performance_payload

router = APIRouter()


@router.get("/performance")
def get_performance(business_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Mirror of the performance slice in /api/bootstrap. Useful for Phase B re-fetch."""
    perf = db.get(PerformanceSummary, business_id)
    return _performance_payload(perf) if perf else {}
