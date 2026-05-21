"""Performance router — Phase B.6.

GET  /api/performance?period=30|prev30|ytd
POST /api/performance/regenerate-insights

The period query returns the same shape as the bootstrap performance slice
but scaled for the requested window. For the demo, scaling is deterministic
(no granular daily history yet — that lands when amplification stats are
real in Phase D).

The regenerate-insights endpoint rotates the insights JSON to a fresh slate
from a small canned palette. In production this would be triggered nightly
by an analytics job; the manual button is a stand-in for the demo.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PerformanceSummary
from .bootstrap import _performance_payload

router = APIRouter()


# Per-window scaling factors. Multiplies the seeded "current period" baseline.
# - 30      : 1.00  (no scaling — the seeded value)
# - prev30  : surface the prior-period numbers as the "current" + synthesize
#             a still-earlier "prev" for the delta. Inverts the delta direction.
# - ytd     : roughly Jan-through-today (~5 months in the seed timeframe) →
#             5× volume metrics, same ratio metrics.
_WINDOW_FACTORS: dict[str, dict[str, float]] = {
    "30":     {"current": 1.0,  "prev": 1.0},
    "prev30": {"current": 0.83, "prev": 0.71},   # last period stats become the new "current"
    "ytd":    {"current": 5.0,  "prev": 4.2},
}


def _scale_int(value: int, factor: float) -> int:
    return max(0, int(round(value * factor)))


def _scaled_perf(perf: PerformanceSummary, period: str) -> dict[str, Any]:
    f = _WINDOW_FACTORS.get(period, _WINDOW_FACTORS["30"])
    fc = f["current"]
    fp = f["prev"]

    reach_val = _scale_int(perf.reach_value, fc)
    reach_prev = _scale_int(perf.reach_prev, fp)
    eng_val = _scale_int(perf.engagement_value, fc)
    eng_prev = _scale_int(perf.engagement_prev, fp)

    def delta_pct(curr: int, prev: int) -> str:
        if prev == 0:
            return "+∞%" if curr > 0 else "0%"
        pct = round((curr - prev) / prev * 100)
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct}%"

    return {
        "reach":      {"value": reach_val,  "prev": reach_prev, "delta": delta_pct(reach_val, reach_prev), "positive": reach_val >= reach_prev},
        "engagement": {"value": eng_val,    "prev": eng_prev,   "delta": delta_pct(eng_val, eng_prev),    "positive": eng_val >= eng_prev},
        "followers":  {"value": perf.followers_value, "prev": perf.followers_prev, "delta": perf.followers_delta, "positive": True, "helper": "net new this period"},
        "ctr":        {"value": perf.ctr_value, "prev": perf.ctr_prev, "delta": perf.ctr_delta, "positive": True, "helper": "paid social"},
        "channelMix": perf.channel_mix_json,
        "topPosts":   perf.top_posts_json,
        "insights":   perf.insights_json,
        "dailyReachCurrent": perf.daily_reach_current_json,
        "dailyReachPrev":    perf.daily_reach_prev_json,
        "period": period,
    }


@router.get("/performance")
def get_performance(
    period: Literal["30", "prev30", "ytd"] = Query("30"),
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    perf = db.get(PerformanceSummary, business_id)
    if perf is None:
        raise HTTPException(status_code=404, detail=f"no performance data for business {business_id}")
    return _scaled_perf(perf, period)


# Canned insights for regen demo. Each call picks 4 random entries from this
# palette. Real production would synthesize from the last 30 days of metrics.
_INSIGHTS_POOL = [
    {"kind": "win",  "title": "Friday 8–10am posts outperformed the week's average by 41%",
     "body": "Saturday brake-check post hit 1.8k reach. Saturday-morning windows continue to dominate engagement for your audience — keep posting there."},
    {"kind": "win",  "title": "Behind-the-scenes reels are still your strongest IG format",
     "body": "Mike-narrated reels averaged 2.3× the engagement of static posts. Worth shooting one per month minimum."},
    {"kind": "win",  "title": "GBP holiday-hours posts pulled 4× tap-to-call rate",
     "body": "Memorial Day weekend hours post hit 87 calls in 3 days. Repeat the pattern for July 4th + Labor Day."},
    {"kind": "lose", "title": "Wednesday afternoon FB posts under-performed",
     "body": "Mid-week engagement was 38% below your average. Shift the slow-day reminder posts to Tuesday or Thursday morning instead."},
    {"kind": "lose", "title": "Caption-only IG posts plateaued at ~120 reach",
     "body": "Three caption-only posts this period hit identical low ceilings. Pair every IG post with an owner photo or a quick reel going forward."},
    {"kind": "win",  "title": "Your reviews are still your strongest organic asset",
     "body": "Mike P.'s 'honest mechanic' review reposted as a quote-card pulled 2.4k reach. Repurpose the next 5-star verbatim."},
    {"kind": "lose", "title": "Paid CTR dipped on the Memorial Day boost",
     "body": "CTR dropped from 3.4% to 2.1% on the Memorial Day boost — the audience window was probably too broad. Tighten to 15-mile radius next time."},
    {"kind": "win",  "title": "Phone-forward CTAs continue to beat 'Learn More'",
     "body": "Posts ending with (507) 555-0143 converted 1.6× higher than posts with a link CTA. Lead with the phone number on safety/repair posts."},
]


@router.post("/performance/regenerate-insights")
def regenerate_insights(business_id: int = 1, db: Session = Depends(get_db)) -> dict[str, Any]:
    perf = db.get(PerformanceSummary, business_id)
    if perf is None:
        raise HTTPException(status_code=404, detail=f"no performance data for business {business_id}")

    # Pick 4 random insights, mixed wins/loses. Mark with a freshly minted
    # generated_at so the UI can show "generated X minutes ago" if it wants.
    chosen = random.sample(_INSIGHTS_POOL, k=4)
    generated_at = datetime.utcnow().isoformat()
    perf.insights_json = [{**i, "generated_at": generated_at} for i in chosen]
    db.commit()
    return {
        "ok": True,
        "insights": perf.insights_json,
        "generatedAt": generated_at,
    }
