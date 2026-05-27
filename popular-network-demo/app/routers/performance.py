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
    {"kind": "win",  "title": "LinkedIn extractor-demo posts outperformed thought-leadership by 3.2×",
     "body": "Posts featuring an actual screenshot of the Universal Document Extractor in action pulled 3.2× the engagement of generic 'AI in publishing' commentary. Show the tool working — don't talk around it."},
    {"kind": "win",  "title": "The 38-page court-document origin story converts at 47%",
     "body": "Webinar registrants who saw the founder's origin-story slide (the ChatGPT-can't-do-this moment) converted to free-trial signup at 47%. Lead every demo with the origin story."},
    {"kind": "win",  "title": "'Saves 2–3 hours a week' beat 'AI for publishers' headline 2.8× on CTR",
     "body": "Industry-newsletter sponsorships with the concrete hours-saved headline drove 2.8× the click-through of the abstract AI framing. Trade in vague AI claims for the time-savings number every time."},
    {"kind": "lose", "title": "Tuesday afternoon LinkedIn posts under-performed",
     "body": "Mid-week afternoon publisher-feed activity was 36% below average. Shift the publishing window to Monday morning or Thursday morning when newsroom decision-makers are at their desks."},
    {"kind": "lose", "title": "AP Style Proofer-led cold emails had 40% lower demo-book rate",
     "body": "Cold outreach leading with the proofer converted to booked demos at 0.9% — extractor-led emails hit 1.5%. Keep the extractor as the lead hook; sell the proofer in the bundle once they're on the call."},
    {"kind": "win",  "title": "Citizen Publishing writer testimonial pulled 2.1k LinkedIn impressions",
     "body": "The 'saving two to three hours each week, no question' quote-card was the strongest organic asset this period. Get the 21-paper customer on record next — that proof point would carry."},
    {"kind": "lose", "title": "Short-form video tests on LinkedIn fell flat",
     "body": "15-second clip variants averaged 220 impressions vs 1.4k for longer value-prop posts. Publisher decision-makers want the value spelled out — TikTok-style pacing doesn't translate to this audience."},
    {"kind": "win",  "title": "Free-trial signups peaked the week INMA content went out",
     "body": "Co-marketing with industry events (INMA conference week) drove the strongest trial pipeline of the period. Sync the next campaign push with the LMA/E&P calendar."},
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
