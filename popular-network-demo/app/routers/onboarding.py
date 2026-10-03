"""Onboarding wizard API (Phase 2). See app/onboarding.py for the flow.

GET  /api/onboarding            state + the questions (any role)
PUT  /api/onboarding/answers    save progress (answers, website)
POST /api/onboarding/draft      Claude drafts the voice brief (background)
PUT  /api/onboarding/brief      save the owner's edits to the draft
POST /api/onboarding/finish     keep the brief, then plan the first week (background)
POST /api/onboarding/plan       retry a failed first-week plan
POST /api/onboarding/skip       "I'll do this later"
POST /api/onboarding/restart    redo setup after finishing or skipping

Every write needs edit_marketing_plan (owners and editors). Jobs report
back through GET: the page polls while status is drafting or planning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import onboarding as ob
from ..auth.deps import get_tenant_id, has_capability, require_capability
from ..db import get_db
from ..models import Business
from ..onboarding_questions import CHANNEL_CHOICES, QUESTIONS, STEPS
from ..voice_brief import validate_brief

router = APIRouter(prefix="/onboarding")

_EDIT = [Depends(require_capability("edit_marketing_plan"))]


class AnswersBody(BaseModel):
    answers: dict[str, Any] = {}
    website: Optional[str] = None


class BriefBody(BaseModel):
    brief: dict[str, Any]


class FinishBody(BaseModel):
    brief: Optional[dict[str, Any]] = None


def _biz(db: Session, business_id: int) -> Business:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail="business not found")
    return biz


def _reply(request: Request, biz: Business) -> dict[str, Any]:
    return {
        "ok": True,
        "onboarding": ob.public_state(biz, can_edit=has_capability(request, "edit_marketing_plan")),
    }


def _not_busy(state: dict[str, Any]) -> None:
    if state["status"] in ob.ACTIVE:
        what = "drafting your brief" if state["status"] == "drafting" else "planning your first week"
        raise HTTPException(status_code=409, detail=f"Still {what}. Give it a minute.")


def _checked_brief(brief: dict[str, Any], previous: Optional[dict[str, Any]]) -> dict[str, Any]:
    # Pipeline metadata ("_"-prefixed) rides along from the draft; the owner
    # edits only the visible fields.
    merged = {k: v for k, v in (previous or {}).items() if str(k).startswith("_")}
    merged.update({k: v for k, v in brief.items() if not str(k).startswith("_")})
    try:
        return validate_brief(merged)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("")
def get_onboarding(request: Request, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    return {
        **_reply(request, biz),
        "steps": STEPS,
        "questions": QUESTIONS,
        "channels": CHANNEL_CHOICES,
    }


@router.put("/answers", dependencies=_EDIT)
def save_answers(body: AnswersBody, request: Request, business_id: int = Depends(get_tenant_id),
                 db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    _not_busy(state)
    if state["status"] == "done":
        raise HTTPException(status_code=409, detail="Setup is finished. Choose Redo setup to change your answers.")
    try:
        cleaned = ob.clean_answers(body.answers)
        website = ob.normalize_website(body.website) if body.website is not None else state.get("website")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    answers = dict(state.get("answers") or {})
    for key in body.answers:  # a blanked answer clears the saved one
        answers.pop(key, None)
    answers.update(cleaned)
    state.update(answers=answers, website=website, error=None)
    if state["status"] in ("not_started", "skipped"):
        state["status"] = "answering"
    ob.save_state(biz, state)
    db.commit()
    return _reply(request, biz)


@router.post("/draft", dependencies=_EDIT)
def start_draft(request: Request, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    _not_busy(state)
    if state["status"] == "done":
        raise HTTPException(status_code=409, detail="Setup is finished. Choose Redo setup first.")
    missing = ob.missing_required(state.get("answers") or {})
    if missing:
        labels = [q["label"] for q in QUESTIONS if q["id"] in missing]
        raise HTTPException(status_code=422, detail="Answer these first: " + " / ".join(labels))
    state.update(status="drafting", error=None, jobStartedAt=datetime.utcnow().replace(microsecond=0).isoformat())
    ob.save_state(biz, state)
    db.commit()
    ob.start_job(ob.run_draft_job, biz.id)
    db.refresh(biz)  # the job writes through its own session
    return _reply(request, biz)


@router.put("/brief", dependencies=_EDIT)
def save_brief(body: BriefBody, request: Request, business_id: int = Depends(get_tenant_id),
               db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    if state["status"] not in ("review", "plan_failed"):
        raise HTTPException(status_code=409, detail="There's no draft brief to edit right now.")
    state.update(brief=_checked_brief(body.brief, state.get("brief")), error=None)
    ob.save_state(biz, state)
    db.commit()
    return _reply(request, biz)


def _start_plan(biz: Business, state: dict[str, Any]) -> None:
    state.update(status="planning", error=None, jobStartedAt=datetime.utcnow().replace(microsecond=0).isoformat())
    ob.save_state(biz, state)


@router.post("/finish", dependencies=_EDIT)
def finish(body: FinishBody, request: Request, business_id: int = Depends(get_tenant_id),
           db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    if state["status"] != "review" or not state.get("brief"):
        raise HTTPException(status_code=409, detail="Draft your brief first.")
    brief = _checked_brief(body.brief, state["brief"]) if body.brief is not None else state["brief"]
    state["brief"] = brief
    # The agent writes from this brief starting now, even while the first
    # week is still being planned.
    biz.voice_brief_json = brief
    if biz.voice_interview != "complete":
        biz.voice_interview = "wizard"
    _start_plan(biz, state)
    db.commit()
    ob.start_job(ob.run_plan_job, biz.id)
    db.refresh(biz)  # the job writes through its own session
    return _reply(request, biz)


@router.post("/plan", dependencies=_EDIT)
def retry_plan(request: Request, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    if state["status"] != "plan_failed":
        raise HTTPException(status_code=409, detail="There's no failed plan to retry.")
    if state.get("brief"):  # edits made while the plan was failed count too
        biz.voice_brief_json = state["brief"]
    _start_plan(biz, state)
    db.commit()
    ob.start_job(ob.run_plan_job, biz.id)
    db.refresh(biz)  # the job writes through its own session
    return _reply(request, biz)


@router.post("/skip", dependencies=_EDIT)
def skip(request: Request, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    _not_busy(state)
    if state["status"] == "done":
        raise HTTPException(status_code=409, detail="Setup is already finished.")
    if state.get("redo") and biz.voice_brief_json:
        # Leaving a redo keeps the brief that's already working.
        state.update(status="done", redo=False, error=None)
    else:
        state.update(status="skipped", error=None)
    ob.save_state(biz, state)
    db.commit()
    return _reply(request, biz)


@router.post("/restart", dependencies=_EDIT)
def restart(request: Request, business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)):
    biz = _biz(db, business_id)
    state = ob.get_state(biz)
    _not_busy(state)
    # Answers are kept so redoing setup means editing, not retyping. The
    # current voice brief keeps working until a new one is finished.
    state.update(status="answering", error=None, redo=bool(biz.voice_brief_json))
    ob.save_state(biz, state)
    db.commit()
    return _reply(request, biz)
