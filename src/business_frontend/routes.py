"""Business console routes for Main Street OS.

Mounted at /business on the main FastAPI app in src/chatbot.py.
All auth routes use session-based cookies (see auth.py); data routes
are scoped to the authenticated user's organization_id — NEVER accept
org_id from a URL parameter.
"""

import logging
from datetime import datetime, timedelta

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
async def dashboard(request: Request, user: dict = Depends(require_auth)):
    org = _get_org(user["organization_id"])
    stats = _get_summary_stats(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"user": user, "org": org, "stats": stats},
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, user: dict = Depends(require_auth)):
    org = _get_org(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={"user": user, "org": org},
    )


@router.get("/ads", response_class=HTMLResponse)
async def ads_page(request: Request, user: dict = Depends(require_auth)):
    org = _get_org(user["organization_id"])
    ads = _get_org_ads(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="ads.html",
        context={"user": user, "org": org, "ads": ads},
    )


@router.get("/sponsored", response_class=HTMLResponse)
async def sponsored_page(request: Request, user: dict = Depends(require_auth)):
    org = _get_org(user["organization_id"])
    return templates.TemplateResponse(
        request=request,
        name="sponsored.html",
        context={"user": user, "org": org},
    )


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


@router.get("/api/analytics")
async def api_analytics(
    request: Request,
    user: dict = Depends(require_auth),
    date_from: str | None = None,
    date_to: str | None = None,
):
    org_id = user["organization_id"]
    if not date_from:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()
    dir_content_id = f"dir_{org_id}"

    # Ad impressions by day
    cursor.execute(
        """
        SELECT DATE(ci.shown_at) as date, COUNT(*) as count
        FROM content_impressions ci
        JOIN advertisements a ON ci.content_id = CAST(a.ad_id AS TEXT)
        WHERE ci.content_type = 'advertisement'
          AND a.organization_id = ?
          AND DATE(ci.shown_at) BETWEEN ? AND ?
        GROUP BY DATE(ci.shown_at) ORDER BY date
    """,
        (org_id, date_from, date_to),
    )
    ad_impr_daily = [dict(r) for r in cursor.fetchall()]

    # Ad clicks by day
    cursor.execute(
        """
        SELECT DATE(uc.clicked_at) as date, COUNT(*) as count
        FROM url_clicks uc
        JOIN advertisements a ON uc.content_id = CAST(a.ad_id AS TEXT)
        WHERE uc.content_type = 'advertisement'
          AND a.organization_id = ?
          AND DATE(uc.clicked_at) BETWEEN ? AND ?
        GROUP BY DATE(uc.clicked_at) ORDER BY date
    """,
        (org_id, date_from, date_to),
    )
    ad_clicks_daily = [dict(r) for r in cursor.fetchall()]

    # Directory impressions by day (content_id = 'dir_{org_id}')
    cursor.execute(
        """
        SELECT DATE(shown_at) as date, COUNT(*) as count
        FROM content_impressions
        WHERE content_type = 'directory'
          AND content_id = ?
          AND DATE(shown_at) BETWEEN ? AND ?
        GROUP BY DATE(shown_at) ORDER BY date
    """,
        (dir_content_id, date_from, date_to),
    )
    dir_impr_daily = [dict(r) for r in cursor.fetchall()]

    # Totals
    cursor.execute(
        """
        SELECT COUNT(*) as total FROM content_impressions ci
        JOIN advertisements a ON ci.content_id = CAST(a.ad_id AS TEXT)
        WHERE ci.content_type = 'advertisement' AND a.organization_id = ?
          AND DATE(ci.shown_at) BETWEEN ? AND ?
    """,
        (org_id, date_from, date_to),
    )
    total_ad_impr = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT COUNT(*) as total FROM url_clicks uc
        JOIN advertisements a ON uc.content_id = CAST(a.ad_id AS TEXT)
        WHERE uc.content_type = 'advertisement' AND a.organization_id = ?
          AND DATE(uc.clicked_at) BETWEEN ? AND ?
    """,
        (org_id, date_from, date_to),
    )
    total_ad_clicks = cursor.fetchone()["total"]

    cursor.execute(
        """
        SELECT COUNT(*) as total FROM content_impressions
        WHERE content_type = 'directory' AND content_id = ?
          AND DATE(shown_at) BETWEEN ? AND ?
    """,
        (dir_content_id, date_from, date_to),
    )
    total_dir_impr = cursor.fetchone()["total"]

    # Top queries
    cursor.execute(
        """
        SELECT cm.content as query, COUNT(*) as count
        FROM content_impressions ci
        JOIN conversation_messages cm ON ci.conversation_id = cm.conversation_id
        LEFT JOIN advertisements a ON ci.content_id = CAST(a.ad_id AS TEXT)
            AND ci.content_type = 'advertisement'
        WHERE cm.role = 'user'
          AND (
            (ci.content_type = 'advertisement' AND a.organization_id = ?)
            OR (ci.content_type = 'directory' AND ci.content_id = ?)
          )
          AND DATE(ci.shown_at) BETWEEN ? AND ?
        GROUP BY cm.content ORDER BY count DESC LIMIT 20
    """,
        (org_id, dir_content_id, date_from, date_to),
    )
    top_queries = [dict(r) for r in cursor.fetchall()]

    # Per-ad performance
    cursor.execute(
        """
        SELECT a.ad_id, a.product_name, a.headline,
               COUNT(DISTINCT ci.id) as impressions,
               COUNT(DISTINCT uc.id) as clicks
        FROM advertisements a
        LEFT JOIN content_impressions ci
            ON ci.content_id = CAST(a.ad_id AS TEXT) AND ci.content_type = 'advertisement'
            AND DATE(ci.shown_at) BETWEEN ? AND ?
        LEFT JOIN url_clicks uc
            ON uc.content_id = CAST(a.ad_id AS TEXT) AND uc.content_type = 'advertisement'
            AND DATE(uc.clicked_at) BETWEEN ? AND ?
        WHERE a.organization_id = ?
        GROUP BY a.ad_id ORDER BY impressions DESC
    """,
        (date_from, date_to, date_from, date_to, org_id),
    )
    per_ad = [dict(r) for r in cursor.fetchall()]

    conn.close()

    total_impressions = total_ad_impr + total_dir_impr
    ctr = round(total_ad_clicks / total_ad_impr * 100, 1) if total_ad_impr > 0 else 0

    return JSONResponse(
        {
            "summary": {
                "total_impressions": total_impressions,
                "total_clicks": total_ad_clicks,
                "ctr_percent": ctr,
                "directory_mentions": total_dir_impr,
                "period_start": date_from,
                "period_end": date_to,
            },
            "daily": {
                "ad_impressions": ad_impr_daily,
                "ad_clicks": ad_clicks_daily,
                "dir_impressions": dir_impr_daily,
            },
            "top_queries": top_queries,
            "top_content": per_ad,
        }
    )


@router.put("/api/settings")
async def api_update_settings(request: Request, user: dict = Depends(require_auth)):
    data = await request.json()
    org_id = user["organization_id"]

    allowed = {
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
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return JSONResponse({"error": "No valid fields to update"}, status_code=400)

    conn = get_connection()
    cursor = conn.cursor()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [org_id]
    cursor.execute(
        f"UPDATE organizations SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})


@router.get("/api/sponsored")
async def api_list_sponsored(request: Request, user: dict = Depends(require_auth)):
    from src.modules.sponsored.database import get_sponsored_answers_for_org

    org = _get_org(user["organization_id"])
    answers = get_sponsored_answers_for_org(user["organization_id"])
    tier = (org or {}).get("tier", "starter")
    limits = {"starter": 0, "growth": 20, "premium": 100}
    return JSONResponse(
        {
            "answers": answers,
            "tier": tier,
            "impressions_limit": limits.get(tier, 0),
        }
    )


@router.post("/api/sponsored")
async def api_create_sponsored(request: Request, user: dict = Depends(require_auth)):
    from src.modules.sponsored.database import create_sponsored_answer

    org = _get_org(user["organization_id"])
    tier = (org or {}).get("tier", "starter")
    if tier == "starter":
        return JSONResponse(
            {"error": "Upgrade your plan to create sponsored answers"}, status_code=403
        )

    data = await request.json()
    category = (data.get("category") or "").strip()
    answer_text = (data.get("answer_text") or "").strip()
    if not category or not answer_text:
        return JSONResponse(
            {"error": "Category and answer text are required"}, status_code=400
        )
    if len(answer_text) > 500:
        return JSONResponse(
            {"error": "Answer text must be 500 characters or less"}, status_code=400
        )

    limits = {"growth": 20, "premium": 100}
    new_id = create_sponsored_answer(
        org_id=user["organization_id"],
        category=category,
        answer_text=answer_text,
        impressions_limit=limits.get(tier, 0),
        tier=tier,
    )
    return JSONResponse({"ok": True, "id": new_id})


@router.put("/api/sponsored/{answer_id}")
async def api_update_sponsored(
    answer_id: int, request: Request, user: dict = Depends(require_auth)
):
    from src.modules.sponsored.database import update_sponsored_answer

    data = await request.json()
    update_sponsored_answer(
        answer_id,
        org_id=user["organization_id"],
        answer_text=data.get("answer_text"),
        category=data.get("category"),
    )
    return JSONResponse({"ok": True})


@router.delete("/api/sponsored/{answer_id}")
async def api_delete_sponsored(
    answer_id: int, request: Request, user: dict = Depends(require_auth)
):
    from src.modules.sponsored.database import deactivate_sponsored_answer

    deactivate_sponsored_answer(answer_id, org_id=user["organization_id"])
    return JSONResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════
#  Internal helpers
# ═══════════════════════════════════════════════════════════════════


def _get_summary_stats(org_id: int) -> dict:
    """Last-30-days summary for dashboard cards."""
    conn = get_connection()
    cursor = conn.cursor()
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    dir_content_id = f"dir_{org_id}"

    cursor.execute(
        """
        SELECT COUNT(*) as c FROM content_impressions ci
        JOIN advertisements a ON ci.content_id = CAST(a.ad_id AS TEXT)
        WHERE ci.content_type = 'advertisement' AND a.organization_id = ?
          AND DATE(ci.shown_at) >= ?
    """,
        (org_id, since),
    )
    ad_impr = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT COUNT(*) as c FROM url_clicks uc
        JOIN advertisements a ON uc.content_id = CAST(a.ad_id AS TEXT)
        WHERE uc.content_type = 'advertisement' AND a.organization_id = ?
          AND DATE(uc.clicked_at) >= ?
    """,
        (org_id, since),
    )
    ad_clicks = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT COUNT(*) as c FROM content_impressions
        WHERE content_type = 'directory' AND content_id = ?
          AND DATE(shown_at) >= ?
    """,
        (dir_content_id, since),
    )
    dir_impr = cursor.fetchone()["c"]

    conn.close()
    total = ad_impr + dir_impr
    ctr = round(ad_clicks / ad_impr * 100, 1) if ad_impr > 0 else 0
    return {
        "total_impressions": total,
        "ad_impressions": ad_impr,
        "clicks": ad_clicks,
        "ctr": ctr,
        "directory_mentions": dir_impr,
    }


def _get_org_ads(org_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM advertisements WHERE organization_id = ? ORDER BY valid_to DESC",
        (org_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
