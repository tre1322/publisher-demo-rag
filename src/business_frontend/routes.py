"""Business console routes for Main Street OS.

Mounted at /business on the main FastAPI app in src/chatbot.py.
All auth routes use session-based cookies (see auth.py); data routes
are scoped to the authenticated user's organization_id — NEVER accept
org_id from a URL parameter.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.business_frontend.auth import (
    COOKIE_NAME,
    create_business_user,
    create_session,
    get_current_user,
    get_invite,
    get_user_by_email,
    mark_invite_used,
    require_auth,
    update_last_login,
    verify_password,
)
from src.core.database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/business", tags=["business"])
templates = Jinja2Templates(directory="src/business_frontend/templates")


# ── Helpers ─────────────────────────────────────────────────────────
def _get_org(org_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _set_session_cookie(response, user: dict) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session(user),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )


# ═══════════════════════════════════════════════════════════════════
#  REGISTRATION (invite-based)
# ═══════════════════════════════════════════════════════════════════


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, invite: str = ""):
    if not invite:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "No invite code provided. Contact your local newspaper for an invite link.",
                "invite": None,
            },
        )
    inv = get_invite(invite)
    if not inv:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "Invalid invite code.", "invite": None},
        )
    if inv.get("used_at"):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "This invite has already been used.", "invite": None},
        )
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"error": None, "invite": inv},
    )


@router.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    from src.modules.organizations.database import _slugify

    form = await request.form()

    def f(key: str) -> str:
        return (form.get(key) or "").strip()

    invite_code = f("invite_code")
    owner_name = f("owner_name")
    email = f("email").lower()
    password = form.get("password", "")
    business_name = f("business_name")
    phone = f("phone")
    address = f("address")
    city = f("city")
    state = f("state")
    website = f("website")
    description = f("description")
    services = f("services")

    inv = get_invite(invite_code)
    if not inv or inv.get("used_at"):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "Invalid or already-used invite.", "invite": None},
        )

    if not owner_name or not email or not password or not business_name:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "Your name, email, password, and business name are all required.",
                "invite": inv,
            },
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "Password must be at least 6 characters.",
                "invite": inv,
            },
        )
    if get_user_by_email(email):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "An account with this email already exists. Please sign in instead.",
                "invite": inv,
            },
        )

    # Create org record using invite's publisher + tier
    conn = get_connection()
    cursor = conn.cursor()
    slug_base = _slugify(business_name) or "business"
    # Ensure slug uniqueness — append -N if needed
    slug = slug_base
    n = 2
    while True:
        cursor.execute("SELECT id FROM organizations WHERE slug = ?", (slug,))
        if not cursor.fetchone():
            break
        slug = f"{slug_base}-{n}"
        n += 1

    cursor.execute(
        """
        INSERT INTO organizations
          (name, slug, phone, address, city, state, website, description, services,
           publisher, tier, enrichment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'main_street_os')
        """,
        (
            business_name,
            slug,
            phone,
            address,
            city,
            state,
            website,
            description,
            services,
            inv["publisher"],
            inv.get("tier", "growth"),
        ),
    )
    org_id = cursor.lastrowid
    conn.commit()
    conn.close()

    user_id = create_business_user(
        email=email,
        password=password,
        name=owner_name,
        organization_id=org_id,
    )
    mark_invite_used(invite_code, user_id)

    # W1: publisher attribution + initial revenue-share window + tier_history.
    # Policy is invite-only (decided 2026-05-08): if the invite doesn't
    # resolve to an active publisher, attribution raises ValueError and
    # registration is rolled back so admin can fix the invite/publisher and
    # the user retries cleanly.
    from src.modules.billing.attribution import (
        INITIAL_Y1_SHARE_PCT,
        attribute_publisher_at_signup,
    )
    from src.modules.billing.database import (
        log_tier_change,
        open_revenue_share_window,
    )

    try:
        pub_id, source = attribute_publisher_at_signup(
            organization_id=org_id,
            invite_code=invite_code,
            business_state=state or None,
            business_city=city or None,
            self_serve=False,
        )
    except ValueError as e:
        # Roll back the half-built signup so the user can retry once admin
        # fixes the publisher/invite.
        logger.error("Attribution failed for org %s: %s — rolling back", org_id, e)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM business_users WHERE id = ?", (user_id,))
        cur.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
        cur.execute(
            "UPDATE business_invites SET used_at = NULL, used_by_user_id = NULL "
            "WHERE invite_code = ?",
            (invite_code,),
        )
        conn.commit()
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": (
                    "We couldn't attribute this signup to an active publisher. "
                    "Please contact your local newspaper — they'll re-issue your "
                    "invite or activate their account."
                ),
                "invite": inv,
            },
        )

    open_revenue_share_window(
        organization_id=org_id,
        selling_publisher_id=pub_id,
        share_pct=INITIAL_Y1_SHARE_PCT,
        attribution_source=source,
        notes=f"Y1 share opened at signup (invite={invite_code})",
    )
    log_tier_change(
        organization_id=org_id,
        from_tier=None,
        to_tier=inv.get("tier", "growth"),
        changed_by=f"register:user_id={user_id}",
        reason=f"initial_signup invite={invite_code}",
    )

    user = get_user_by_email(email)
    update_last_login(user["id"])
    response = RedirectResponse(url="/business/", status_code=303)
    _set_session_cookie(response, user)
    return response


# ═══════════════════════════════════════════════════════════════════
#  LOGIN / LOGOUT
# ═══════════════════════════════════════════════════════════════════


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/business/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request, email: str = Form(...), password: str = Form(...)
):
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid email or password"},
        )
    update_last_login(user["id"])
    response = RedirectResponse(url="/business/", status_code=303)
    _set_session_cookie(response, user)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/business/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ═══════════════════════════════════════════════════════════════════
#  PAGES (authenticated)
# ═══════════════════════════════════════════════════════════════════


@router.get("/", response_class=HTMLResponse)
async def dashboard(user: dict = Depends(require_auth)):
    """Amplora business console root → bounce to the Marketing Profile.

    Phase 0 used to render a dashboard.html with chatbot impression
    stats + recent ads. Both are gone (moved to the publisher server);
    the owner's primary surface is now the PMC + billing pages.
    """
    return RedirectResponse(url="/business/pmc/", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict = Depends(require_auth)):
    org = _get_org(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"user": user, "org": org},
    )


# ═══════════════════════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════════════════════


@router.put("/api/settings")
async def api_update_settings(request: Request, user: dict = Depends(require_auth)):
    from src.modules.organizations.database import _slugify

    data = await request.json()
    org_id = user["organization_id"]

    allowed = {
        "name",
        "description",
        "services",
        "keywords",
        "phone",
        "email",
        "website",
        "address",
        "city",
        "state",
        "hours_json",
        "social_json",
    }
    updates = {
        k: (v or "").strip() if isinstance(v, str) else v
        for k, v in data.items()
        if k in allowed
    }

    # Validate name if being changed
    if "name" in updates:
        new_name = updates["name"]
        if not new_name:
            return JSONResponse(
                {"error": "Business name cannot be empty"}, status_code=400
            )
        if len(new_name) > 120:
            return JSONResponse(
                {"error": "Business name is too long (max 120 chars)"}, status_code=400
            )

    if not updates:
        return JSONResponse({"error": "No valid fields to update"}, status_code=400)

    conn = get_connection()
    cursor = conn.cursor()

    # If the name changed, regenerate a unique slug (same pattern as registration)
    if "name" in updates:
        slug_base = _slugify(updates["name"]) or "business"
        slug = slug_base
        n = 2
        while True:
            cursor.execute(
                "SELECT id FROM organizations WHERE slug = ? AND id != ?",
                (slug, org_id),
            )
            if not cursor.fetchone():
                break
            slug = f"{slug_base}-{n}"
            n += 1
        updates["slug"] = slug

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [org_id]
    cursor.execute(
        f"UPDATE organizations SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  BILLING (W1)
# ═══════════════════════════════════════════════════════════════════
#
# Flow:
#   1. /business/billing            -> page; shows current state + tier buttons
#   2. /business/billing/checkout   -> POST; creates Stripe Session, redirects
#   3. Stripe-hosted Checkout       -> user pays
#   4. Stripe webhook fires         -> /webhooks/stripe updates DB (separate)
#   5. /business/billing/success    -> landing; webhook may not have fired yet
#   6. /business/billing/cancel     -> user backed out; back to billing page


@router.get("/billing", response_class=HTMLResponse)
async def billing_page(request: Request, user: dict = Depends(require_auth)):
    from src.core.config import BILLING_ENABLED
    from src.modules.billing.database import (
        get_active_subscription,
        get_current_revenue_share,
        get_tier_history,
    )

    org = _get_org(user["organization_id"])
    sub = get_active_subscription(user["organization_id"])
    share = get_current_revenue_share(user["organization_id"])
    history = get_tier_history(user["organization_id"])

    # Tier catalog for the buy buttons. Display only — actual price comes
    # from the Stripe Price object, not this dict.
    tier_catalog = [
        {"id": "starter", "name": "Starter", "price_display": "$99/mo",
         "tagline": "Drafts only — review and post yourself."},
        {"id": "growth", "name": "Growth", "price_display": "$299/mo",
         "tagline": "We run social, GBP, reviews, and a website for you."},
        {"id": "concierge", "name": "Concierge", "price_display": "$499/mo",
         "tagline": "Growth + dedicated human review + monthly strategy call."},
    ]

    return templates.TemplateResponse(
        request=request,
        name="billing.html",
        context={
            "user": user, "org": org, "sub": sub, "share": share,
            "history": history, "tier_catalog": tier_catalog,
            "billing_enabled": BILLING_ENABLED,
            "active_page": "billing",
        },
    )


@router.post("/billing/checkout")
async def billing_checkout(
    request: Request, user: dict = Depends(require_auth), tier: str = Form(...)
):
    """Create a Stripe Checkout Session for the requested tier and redirect."""
    from src.core.config import BASE_URL, BILLING_ENABLED
    from src.modules.billing.database import get_active_subscription
    from src.modules.billing.stripe_checkout import create_checkout_session

    # Pilot kill-switch: payments closed → no-op back to the billing page
    # (which renders the "no charge during the pilot" panel) instead of
    # attempting a Stripe call that would 503 without keys.
    if not BILLING_ENABLED:
        logger.info(
            "Checkout requested for tier=%s while BILLING_ENABLED=false; no-op",
            tier,
        )
        return RedirectResponse(url="/business/billing", status_code=303)

    if tier not in ("starter", "growth", "concierge"):
        return JSONResponse(
            content={"error": f"unknown tier: {tier}"}, status_code=400
        )

    # If they already have a subscription, reuse the Stripe Customer so
    # the new sub attaches to the same payment method / invoice history.
    existing_sub = get_active_subscription(user["organization_id"])
    existing_customer_id = (
        existing_sub.get("processor_customer_id") if existing_sub else None
    )

    try:
        session = create_checkout_session(
            organization_id=user["organization_id"],
            tier=tier,
            customer_email=user["email"],
            base_url=BASE_URL,
            existing_customer_id=existing_customer_id,
        )
    except ValueError as e:
        # Missing env var (STRIPE_API_KEY / STRIPE_PRICE_*) — surface to admin.
        logger.error("Checkout session config error: %s", e)
        return JSONResponse(
            content={"error": "billing not fully configured", "detail": str(e)},
            status_code=503,
        )
    except Exception as e:
        logger.exception("Stripe checkout failed: %s", e)
        return JSONResponse(
            content={"error": "checkout failed", "detail": str(e)},
            status_code=502,
        )

    checkout_url = session.get("url")
    if not checkout_url:
        return JSONResponse(
            content={"error": "stripe returned no checkout url"}, status_code=502
        )
    return RedirectResponse(url=checkout_url, status_code=303)


@router.get("/billing/success", response_class=HTMLResponse)
async def billing_success(
    request: Request, user: dict = Depends(require_auth), session_id: str = ""
):
    """Landing page after Stripe redirects back. The webhook may not have
    fired yet (race), so we show 'pending' if the subscription row hasn't
    been created locally. Page auto-refreshes every 3s for ~30s.
    """
    from src.modules.billing.database import get_active_subscription

    sub = get_active_subscription(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="billing_success.html",
        context={
            "user": user, "org": _get_org(user["organization_id"]),
            "sub": sub, "session_id": session_id, "active_page": "billing",
        },
    )


@router.get("/billing/cancel")
async def billing_cancel(user: dict = Depends(require_auth)):
    return RedirectResponse(url="/business/billing", status_code=303)


# ═══════════════════════════════════════════════════════════════════
#  MARKETING PROFILE (W2 — Product Marketing Context)
# ═══════════════════════════════════════════════════════════════════
#
# Single-URL state machine at /business/pmc/:
#   - no row at all       -> show prep (quantitative form + transcript paste)
#   - row status='draft'  -> show review (editable + accept button)
#   - row status='accepted' -> show canonical (read-only + redo-interview link)
#
# W2.1 captures the transcript via paste. W2.2 will replace the paste
# block with a "schedule call" / live-agent integration; the rest of
# this flow is unchanged by that swap.


@router.get("/pmc/", response_class=HTMLResponse)
@router.get("/pmc", response_class=HTMLResponse)
async def pmc_landing(
    request: Request, user: dict = Depends(require_auth), redo: int = 0
):
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.interview_script import (
        INTERVIEW_LENGTH_CAP_MINUTES,
        INTERVIEW_TARGET_MINUTES,
        pre_interview_brief,
        quantitative_by_section,
        topic_areas,
    )

    org_id = user["organization_id"]
    accepted = pmc_db.get_canonical_pmc(org_id)
    draft = pmc_db.get_latest_draft(org_id)

    # ?redo=1 forces the prep page even when an accepted PMC exists, so the
    # owner can run a fresh interview. Submitting will create a new draft;
    # accepting that draft will supersede the current canonical.
    if redo:
        return templates.TemplateResponse(
            request=request,
            name="pmc_prep.html",
            context={
                "user": user, "org": _get_org(org_id),
                "brief": pre_interview_brief(),
                "topics": topic_areas(),
                "form_sections": quantitative_by_section(),
                "previous_quantitative": (accepted or {}).get("quantitative", {}),
                "target_minutes": INTERVIEW_TARGET_MINUTES,
                "cap_minutes": INTERVIEW_LENGTH_CAP_MINUTES,
                "active_page": "pmc",
                "is_redo": True,
            },
        )

    if draft:
        return templates.TemplateResponse(
            request=request,
            name="pmc_review.html",
            context={
                "user": user, "org": _get_org(org_id),
                "pmc": draft, "active_page": "pmc",
                "is_canonical": False,
            },
        )
    if accepted:
        return templates.TemplateResponse(
            request=request,
            name="pmc_review.html",
            context={
                "user": user, "org": _get_org(org_id),
                "pmc": accepted, "active_page": "pmc",
                "is_canonical": True,
            },
        )
    # No PMC yet — show prep page.
    return templates.TemplateResponse(
        request=request,
        name="pmc_prep.html",
        context={
            "user": user, "org": _get_org(org_id),
            "brief": pre_interview_brief(),
            "topics": topic_areas(),
            "form_sections": quantitative_by_section(),
            "target_minutes": INTERVIEW_TARGET_MINUTES,
            "cap_minutes": INTERVIEW_LENGTH_CAP_MINUTES,
            "active_page": "pmc",
        },
    )


@router.post("/pmc/submit", response_class=HTMLResponse)
async def pmc_submit(
    request: Request, user: dict = Depends(require_auth)
):
    """Accept a filled-in pre-interview form + a pasted transcript.

    Atomically:
      1. Create an interview session (status=transcript_pasted).
      2. Run generate_pmc_from_transcript() to produce the markdown.
      3. Insert a 'draft' PMC row pointing at the session.
      4. Redirect owner to /business/pmc/ for review.
    """
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.interview_script import quantitative_questions
    from src.modules.pmc.transcript_to_pmc import generate_pmc_from_transcript

    form = await request.form()
    transcript = (form.get("transcript") or "").strip()
    if not transcript:
        from src.modules.pmc.interview_script import quantitative_by_section
        return templates.TemplateResponse(
            request=request,
            name="pmc_prep.html",
            context={
                "user": user, "org": _get_org(user["organization_id"]),
                "error": "Paste the interview transcript before submitting.",
                "form_sections": quantitative_by_section(),
                "active_page": "pmc",
            },
            status_code=400,
        )

    quantitative = {
        q.key: (form.get(f"q_{q.key}") or "").strip()
        for q in quantitative_questions()
    }

    org_id = user["organization_id"]
    session_id = pmc_db.create_session(org_id, voice_provider="manual_paste")
    pmc_db.complete_session_with_transcript(session_id, transcript)

    qualitative_md, meta = generate_pmc_from_transcript(quantitative, transcript)
    pmc_id = pmc_db.create_pmc_draft(
        organization_id=org_id,
        qualitative_md=qualitative_md,
        quantitative=quantitative,
        transcript_text=transcript,
        interview_session_id=session_id,
        generator_model=meta["model"],
        generator_prompt_version=meta["prompt_version"],
        script_version=meta["script_version"],
        created_by_user_id=user.get("id"),
    )
    logger.info(f"PMC draft {pmc_id} created for org {org_id} via session {session_id}")
    return RedirectResponse(url="/business/pmc/", status_code=303)


@router.post("/pmc/save")
async def pmc_save(
    request: Request, user: dict = Depends(require_auth)
):
    """Owner edits the draft (qualitative_md inline + quantitative form fields)."""
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.interview_script import quantitative_questions

    form = await request.form()
    pmc_id = int(form.get("pmc_id") or 0)
    pmc = pmc_db.get_pmc(pmc_id)
    if not pmc or pmc["organization_id"] != user["organization_id"]:
        return JSONResponse({"error": "not_found"}, status_code=404)

    qualitative_md = form.get("qualitative_md")
    quantitative = {
        q.key: (form.get(f"q_{q.key}") or "").strip()
        for q in quantitative_questions()
    }
    pmc_db.update_pmc_draft(
        pmc_id, qualitative_md=qualitative_md, quantitative=quantitative
    )
    return RedirectResponse(url="/business/pmc/", status_code=303)


@router.post("/pmc/accept")
async def pmc_accept(
    request: Request, user: dict = Depends(require_auth)
):
    """Owner accepts the draft -> becomes canonical. Prior accepted PMC superseded."""
    from src.modules.pmc import database as pmc_db

    form = await request.form()
    pmc_id = int(form.get("pmc_id") or 0)
    pmc = pmc_db.get_pmc(pmc_id)
    if not pmc or pmc["organization_id"] != user["organization_id"]:
        return JSONResponse({"error": "not_found"}, status_code=404)

    pmc_db.accept_pmc(pmc_id, user_id=user.get("id"))
    logger.info(f"PMC {pmc_id} accepted by user {user.get('id')} for org {user['organization_id']}")
    return RedirectResponse(url="/business/pmc/", status_code=303)


@router.post("/pmc/restart")
async def pmc_restart(user: dict = Depends(require_auth)):
    """Owner wants to redo the interview. Doesn't delete the canonical PMC —
    redirects to the prep page (?redo=1 forces prep even when canonical exists).
    Submitting will create a new draft, which when accepted will supersede the
    current canonical."""
    return RedirectResponse(url="/business/pmc/?redo=1", status_code=303)


# ═══════════════════════════════════════════════════════════════════
#  VOICE INTERVIEW (W2.2 — LiveKit + Claude + Deepgram + Cartesia)
# ═══════════════════════════════════════════════════════════════════
#
# Flow (see plan: ~/.claude/plans/yes-ticklish-sparkle.md):
#   POST /pmc/voice/start
#       form data (q_*)  →  save quantitative on session
#                      →  create LiveKit room + dispatch agent
#                      →  redirect to /pmc/interview?sid=N
#
#   GET  /pmc/interview?sid=N
#                      →  re-mint participant token (cheap)
#                      →  render pmc_interview.html (browser joins room)
#
#   POST /pmc/voice/complete   (agent worker → server, HMAC-auth)
#                      →  verify X-Agent-Callback-Token
#                      →  generate_pmc_from_transcript()
#                      →  create_pmc_draft()
#                      →  return JSON; agent signals browser to redirect
#
#   GET  /pmc/voice/status?sid=N   (browser watchdog poll)
#                      →  read session status, return as JSON


@router.post("/pmc/voice/start")
async def pmc_voice_start(
    request: Request, user: dict = Depends(require_auth)
):
    """Step 1 form submit → create voice session → LiveKit room + dispatch agent.

    Replaces the old paste-transcript /pmc/submit path.
    """
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.interview_script import quantitative_questions
    from src.modules.pmc.voice_callback_auth import mint_callback_token
    from src.modules.pmc.voice_provisioning import (
        VoiceProvisioningError,
        is_configured,
        start_voice_session,
    )

    if not is_configured():
        logger.warning("Voice interview attempted but LiveKit not configured")
        return JSONResponse(
            {
                "error": "voice_unconfigured",
                "detail": (
                    "LiveKit isn't configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, "
                    "and LIVEKIT_API_SECRET in .env, then restart."
                ),
            },
            status_code=503,
        )

    form = await request.form()
    quantitative = {
        q.key: (form.get(f"q_{q.key}") or "").strip()
        for q in quantitative_questions()
    }
    # Sanity: require at least the business name. The pre-interview form
    # has weight=3 fields the owner shouldn't skip.
    if not quantitative.get("business_name"):
        from src.modules.pmc.interview_script import (
            INTERVIEW_LENGTH_CAP_MINUTES,
            INTERVIEW_TARGET_MINUTES,
            pre_interview_brief,
            quantitative_by_section,
            topic_areas,
        )
        return templates.TemplateResponse(
            request=request,
            name="pmc_prep.html",
            context={
                "user": user, "org": _get_org(user["organization_id"]),
                "error": "Please fill in your business name before starting the interview.",
                "brief": pre_interview_brief(),
                "topics": topic_areas(),
                "form_sections": quantitative_by_section(),
                "previous_quantitative": quantitative,
                "target_minutes": INTERVIEW_TARGET_MINUTES,
                "cap_minutes": INTERVIEW_LENGTH_CAP_MINUTES,
                "active_page": "pmc",
            },
            status_code=400,
        )

    org_id = user["organization_id"]
    org = _get_org(org_id)
    owner_name = user.get("name") or user.get("email") or "there"
    org_name = (org or {}).get("name") or quantitative.get("business_name") or "your business"

    # 1. Create session with voice_provider='livekit' and persist quantitative.
    session_id = pmc_db.create_session(org_id, voice_provider="livekit")
    pmc_db.save_session_quantitative(session_id, quantitative)

    # 2. Mint a callback token the agent will return on /voice/complete.
    callback_token = mint_callback_token(session_id, org_id)

    # 3. Create the LiveKit room with metadata + dispatch the agent.
    try:
        await start_voice_session(
            session_id=session_id,
            organization_id=org_id,
            owner_name=owner_name,
            org_name=org_name,
            callback_token=callback_token,
        )
    except VoiceProvisioningError as e:
        logger.error("Voice provisioning failed for session %s: %s", session_id, e)
        return JSONResponse(
            {"error": "voice_provisioning_failed", "detail": str(e)},
            status_code=503,
        )

    return RedirectResponse(
        url=f"/business/pmc/interview?sid={session_id}", status_code=303
    )


@router.get("/pmc/interview", response_class=HTMLResponse)
async def pmc_interview_page(
    request: Request, user: dict = Depends(require_auth), sid: int = 0
):
    """Render the voice interview page (mic prompt + transcript ticker)."""
    from src.core.config import LIVEKIT_URL
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.voice_provisioning import (
        VoiceProvisioningError,
        is_configured,
        mint_participant_token,
        room_name_for_session,
    )

    if not is_configured():
        return JSONResponse(
            {"error": "voice_unconfigured"}, status_code=503
        )
    if not sid:
        return RedirectResponse(url="/business/pmc/", status_code=303)

    session = pmc_db.get_session_for_org(sid, user["organization_id"])
    if not session:
        logger.info("Voice page denied: session=%s not found for org=%s",
                    sid, user["organization_id"])
        return RedirectResponse(url="/business/pmc/", status_code=303)
    # Allow voice_awaiting (first load) and voice_in_progress (refresh during call).
    # Completed sessions go back to /business/pmc/ where review/canonical renders.
    if session["status"] not in {"voice_awaiting", "voice_in_progress"}:
        return RedirectResponse(url="/business/pmc/", status_code=303)

    room_name = room_name_for_session(sid)
    identity = f"owner-{user['id']}-s{sid}"
    display_name = user.get("name") or user.get("email") or "Owner"
    try:
        token = mint_participant_token(room_name, identity, display_name)
    except VoiceProvisioningError as e:
        logger.error("Token mint failed for session %s: %s", sid, e)
        return JSONResponse(
            {"error": "voice_provisioning_failed", "detail": str(e)},
            status_code=503,
        )

    return templates.TemplateResponse(
        request=request,
        name="pmc_interview.html",
        context={
            "user": user,
            "org": _get_org(user["organization_id"]),
            "livekit_url": LIVEKIT_URL,
            "room_name": room_name,
            "participant_token": token,
            "session_id": sid,
            "active_page": "pmc",
        },
    )


@router.post("/pmc/voice/complete")
async def pmc_voice_complete(request: Request):
    """Agent worker callback — finalize the session and create the PMC draft.

    Auth: HMAC-signed X-Agent-Callback-Token header (NOT cookie auth).
    The token's payload determines which session this transcript belongs to.

    Idempotent on session_id: if a draft PMC already exists for this session,
    we return the existing draft id without re-running the LLM.
    """
    from src.modules.pmc import database as pmc_db
    from src.modules.pmc.transcript_to_pmc import generate_pmc_from_transcript
    from src.modules.pmc.voice_callback_auth import verify_callback_token

    token = request.headers.get("X-Agent-Callback-Token") or ""
    payload = verify_callback_token(token)
    if not payload:
        return JSONResponse(
            {"error": "invalid_token"}, status_code=401
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)

    transcript = (body.get("transcript") or "").strip()
    if not transcript:
        return JSONResponse({"error": "empty_transcript"}, status_code=400)

    duration_seconds = body.get("duration_seconds")
    recording_url = body.get("recording_url")
    partial = bool(body.get("partial", False))

    session = pmc_db.get_session_for_org(
        payload["session_id"], payload["org_id"]
    )
    if not session:
        # Token verified but session doesn't exist or org mismatch — defense
        # in depth. Don't leak whether the session existed.
        logger.warning(
            "voice/complete: token valid but session=%s for org=%s not found",
            payload["session_id"], payload["org_id"],
        )
        return JSONResponse({"error": "session_not_found"}, status_code=404)

    # Idempotency: if a draft already exists pointing at this session, return it.
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM product_marketing_contexts "
        "WHERE interview_session_id=? AND organization_id=?",
        (session["id"], session["organization_id"]),
    )
    existing = cursor.fetchone()
    conn.close()
    if existing:
        logger.info(
            "voice/complete: idempotent return — draft %s already exists for session %s",
            existing["id"], session["id"],
        )
        return JSONResponse(
            {"ok": True, "pmc_id": existing["id"], "redirect_to": "/business/pmc/"},
            status_code=200,
        )

    # Run the existing pipeline. The transcript: str blob contract is the
    # same as the W2.1 paste path — no prompt/template changes needed.
    quantitative = session.get("quantitative") or {}
    try:
        qualitative_md, meta = generate_pmc_from_transcript(quantitative, transcript)
    except Exception as e:
        logger.exception(
            "generate_pmc_from_transcript failed for session %s: %s",
            session["id"], e,
        )
        return JSONResponse(
            {"error": "pmc_generation_failed", "detail": str(e)},
            status_code=502,
        )

    pmc_db.complete_voice_session(
        session["id"],
        transcript_text=transcript,
        duration_seconds=duration_seconds,
        recording_url=recording_url,
        partial=partial,
    )
    pmc_id = pmc_db.create_pmc_draft(
        organization_id=session["organization_id"],
        qualitative_md=qualitative_md,
        quantitative=quantitative,
        transcript_text=transcript,
        interview_session_id=session["id"],
        generator_model=meta["model"],
        generator_prompt_version=meta["prompt_version"],
        script_version=meta["script_version"],
        created_by_user_id=None,  # callback is agent-initiated, not user-initiated
    )
    logger.info(
        "PMC draft %s created via voice session %s (org %s)",
        pmc_id, session["id"], session["organization_id"],
    )
    return JSONResponse(
        {"ok": True, "pmc_id": pmc_id, "redirect_to": "/business/pmc/"},
        status_code=201,
    )


@router.get("/pmc/voice/status")
async def pmc_voice_status(
    user: dict = Depends(require_auth), sid: int = 0
):
    """Browser watchdog poll — used if the agent's redirect data message is lost.

    Returns the session status + (if completed) the PMC draft id so the
    browser can navigate to the review page.
    """
    from src.modules.pmc import database as pmc_db

    if not sid:
        return JSONResponse({"error": "missing_sid"}, status_code=400)

    session = pmc_db.get_session_for_org(sid, user["organization_id"])
    if not session:
        return JSONResponse({"error": "not_found"}, status_code=404)

    pmc_id = None
    if session["status"] in {"voice_completed", "voice_partial"}:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM product_marketing_contexts "
            "WHERE interview_session_id=? AND organization_id=?",
            (session["id"], session["organization_id"]),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            pmc_id = row["id"]

    return JSONResponse(
        {
            "status": session["status"],
            "pmc_id": pmc_id,
            "redirect_to": "/business/pmc/" if pmc_id else None,
        },
        status_code=200,
    )
