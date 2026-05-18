"""Admin console routes — Amplora only.

Mounted at /admin on the main FastAPI app. Basic-auth gated
(admin / $ADMIN_PASSWORD; default password is 'admin').

This file used to host the RAG chatbot's article-review, edition-upload,
ad-purge, RSS-feed, homepage-pin, costs, directory, and observability
endpoints. As of 2026-05-10 those moved to a separate server. What
remains is the Amplora admin surface:

  - Main Street OS invite creation + business / invite listing
  - Per-org billing audit (Amplora W1)
"""

import logging
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from src.core.database import get_connection

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/admin_frontend/templates")
security = HTTPBasic()


# ── Auth ─────────────────────────────────────────────────────────────


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth (admin / $ADMIN_PASSWORD). Default password 'admin'."""
    admin_password = os.getenv("ADMIN_PASSWORD", "admin")
    is_user = secrets.compare_digest(credentials.username, "admin")
    is_pw = secrets.compare_digest(credentials.password, admin_password)
    if not (is_user and is_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# Publisher slug → display name. The slug appears in URLs like
# /admin/cottonwood/main-street so each publisher can have its own
# bookmarked admin view.
_PUBLISHER_SLUGS: dict[str, str] = {
    "cottonwood": "Cottonwood County Citizen",
    "pipestone": "Pipestone Star",
}


def _publisher_context(request: Request, publisher_slug: str) -> dict:
    pub_name = _PUBLISHER_SLUGS.get(publisher_slug, "")
    return {
        "request": request,
        "publisher": pub_name,
        "publisher_slug": publisher_slug,
    }


# ── Landing ──────────────────────────────────────────────────────────


@router.get("", include_in_schema=False)
async def _admin_root(_username: str = Depends(verify_credentials)):
    """Admin root → bounce to the Main Street OS page."""
    return RedirectResponse(url="/admin/main-street", status_code=303)


@router.get("/main-street", response_class=HTMLResponse)
async def main_street_admin(
    request: Request, _username: str = Depends(verify_credentials),
) -> HTMLResponse:
    """Network-wide Main Street OS admin view."""
    return templates.TemplateResponse(
        request=request,
        name="main_street.html",
        context={"request": request, "publisher": "", "publisher_slug": ""},
    )


@router.get("/{publisher_slug}/main-street", response_class=HTMLResponse)
async def main_street_admin_for_publisher(
    request: Request,
    publisher_slug: str,
    _username: str = Depends(verify_credentials),
) -> HTMLResponse:
    """Publisher-scoped Main Street OS admin view (cottonwood / pipestone)."""
    if publisher_slug not in _PUBLISHER_SLUGS:
        return templates.TemplateResponse(
            request=request,
            name="main_street.html",
            context={"request": request, "publisher": "", "publisher_slug": ""},
        )
    return templates.TemplateResponse(
        request=request,
        name="main_street.html",
        context=_publisher_context(request, publisher_slug),
    )


# ── Publishers (read-only — seeded at boot, used by invite form) ────


@router.get("/api/publishers")
async def list_publishers(_username: str = Depends(verify_credentials)) -> JSONResponse:
    """List all publishers in the network. Drives the invite-creation dropdown."""
    from src.modules.publishers.database import get_all_publishers
    return JSONResponse(content={"publishers": get_all_publishers()})


# ── Main Street OS — invites + enrolled businesses ──────────────────


@router.post("/api/main-street/invite")
async def create_main_street_invite(
    request: Request, _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Create an invite link for a new Amplora business.

    Body: { business_name, publisher, tier (starter|growth|concierge), note? }
    Returns: { success, code, link }  — the link is what the publisher's
    sales rep texts/emails the business owner.
    """
    from src.business_frontend.auth import create_invite

    data = await request.json()
    business_name = (data.get("business_name") or "").strip()
    publisher = (data.get("publisher") or "").strip()
    tier = data.get("tier", "growth")
    note = (data.get("note") or "").strip()

    if not business_name:
        return JSONResponse(
            content={"success": False, "error": "Business name is required"},
            status_code=400,
        )
    if not publisher:
        return JSONResponse(
            content={
                "success": False,
                "error": "Publisher is required (open this page from a publisher-scoped URL)",
            },
            status_code=400,
        )
    if tier not in ("starter", "growth", "concierge"):
        return JSONResponse(
            content={
                "success": False,
                "error": "Tier must be starter, growth, or concierge",
            },
            status_code=400,
        )

    code = create_invite(
        business_name=business_name, publisher=publisher, tier=tier, note=note,
    )
    base = str(request.base_url).rstrip("/")
    link = f"{base}/business/register?invite={code}"
    return JSONResponse(content={"success": True, "code": code, "link": link})


@router.get("/api/main-street/invites")
async def list_main_street_invites(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List invites, optionally filtered to one publisher."""
    from src.business_frontend.auth import get_invites_for_publisher
    return JSONResponse(content={"invites": get_invites_for_publisher(publisher)})


@router.get("/api/main-street/businesses")
async def list_enrolled_businesses(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List enrolled Amplora businesses, optionally filtered by publisher."""
    conn = get_connection()
    cursor = conn.cursor()
    base_select = """
        SELECT bu.id as user_id, bu.email, bu.name as owner_name,
               bu.last_login, bu.created_at as enrolled_at,
               o.id as org_id, o.name as business_name, o.tier,
               o.city, o.state, o.phone, o.publisher
        FROM business_users bu
        JOIN organizations o ON bu.organization_id = o.id
        WHERE bu.is_active = 1
    """
    if publisher:
        cursor.execute(
            base_select + " AND o.publisher = ? ORDER BY bu.created_at DESC",
            (publisher,),
        )
    else:
        cursor.execute(base_select + " ORDER BY bu.created_at DESC")
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return JSONResponse(content={"businesses": rows})


# ── Delete actions (admin cleanup) ──────────────────────────────────


# Tables referencing organizations.id, in delete order. Children first
# so the org row is the last thing to go. Add new org-scoped tables here.
# (SQLite doesn't enforce FKs by default; we cascade by hand to keep
# behavior predictable regardless of PRAGMA settings.)
_ORG_CHILD_TABLES: tuple[tuple[str, str], ...] = (
    ("product_marketing_contexts", "organization_id"),
    ("pmc_interview_sessions", "organization_id"),
    ("subscriptions", "organization_id"),
    ("tier_history", "organization_id"),
    ("publisher_revenue_share", "organization_id"),
    ("publications", "organization_id"),
    ("business_users", "organization_id"),
    # business_invites is unlinked rather than deleted — see the route.
)


def _delete_business_cascade(org_id: int) -> dict[str, int]:
    """Hard-delete an org and every row that references it.

    Returns a per-table count of deleted rows so the admin UI can confirm
    what got cleaned up (and so we have a paper trail in the logs).

    Recordings already written to DigitalOcean Spaces are NOT deleted —
    they outlive the row that pointed at them and age out via the
    bucket's 30-day lifecycle rule. Stripe-side subscriptions are also
    NOT cancelled here; cancel via the Stripe dashboard or API
    separately if needed.
    """
    conn = get_connection()
    cursor = conn.cursor()
    counts: dict[str, int] = {}
    try:
        for table, col in _ORG_CHILD_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE {col} = ?", (org_id,))
            counts[table] = cursor.rowcount
        # Unlink invites that pointed at any of the deleted users. The
        # invite history stays so we can see "this invite was redeemed
        # but the business was later cleaned up."
        cursor.execute(
            "UPDATE business_invites SET used_by_user_id = NULL "
            "WHERE used_by_user_id IN ("
            "  SELECT id FROM business_users WHERE organization_id = ?"
            ")",
            (org_id,),
        )
        # Finally the org row itself.
        cursor.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
        counts["organizations"] = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info("Deleted business org=%s cascade counts=%s", org_id, counts)
    return counts


@router.delete("/api/main-street/invites/{code}")
async def delete_main_street_invite(
    code: str, _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Delete an invite row. Returns 404 if no such invite.

    Does NOT cascade to a redeemed business — if the invite was used,
    the enrolled org keeps working. Use DELETE /businesses/{org_id} to
    fully tear down a redeemed business.
    """
    from src.business_frontend.auth import delete_invite

    if not delete_invite(code):
        return JSONResponse(
            content={"success": False, "error": "Invite not found"},
            status_code=404,
        )
    return JSONResponse(content={"success": True})


@router.delete("/api/main-street/businesses/{org_id}")
async def delete_main_street_business(
    org_id: int, _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Cascade-delete an enrolled business and everything that references it.

    Returns per-table deletion counts so the admin UI can show what was
    removed. See `_delete_business_cascade` for the cascade order and
    the Spaces/Stripe caveats.
    """
    from src.modules.organizations.database import get_organization

    org = get_organization(org_id)
    if not org:
        return JSONResponse(
            content={"success": False, "error": f"Org {org_id} not found"},
            status_code=404,
        )
    try:
        counts = _delete_business_cascade(org_id)
    except Exception as e:
        logger.exception("Failed to delete business org=%s: %s", org_id, e)
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500,
        )
    return JSONResponse(
        content={
            "success": True,
            "org_id": org_id,
            "business_name": org.get("name"),
            "counts": counts,
        }
    )


# ═══════════════════════════════════════════════════════════════════
#  AMPLORA BILLING (W1) — admin audit view
# ═══════════════════════════════════════════════════════════════════


@router.get("/billing/{org_id}", response_class=HTMLResponse)
async def admin_billing_detail(
    request: Request, org_id: int, _username: str = Depends(verify_credentials),
):
    """Full billing state for one org: subscription + revenue share + tier history.

    Used for support, audits, and the day-30/60/90 partner check-ins.
    """
    from src.modules.billing.database import (
        get_active_subscription,
        get_current_revenue_share,
        get_revenue_share_history,
        get_tier_history,
    )
    from src.modules.organizations.database import get_organization

    org = get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail=f"org {org_id} not found")

    sub = get_active_subscription(org_id)
    share = get_current_revenue_share(org_id)
    tier_log = get_tier_history(org_id)
    share_log = get_revenue_share_history(org_id)

    return templates.TemplateResponse(
        request=request,
        name="billing_detail.html",
        context={
            "request": request, "org": org, "sub": sub, "share": share,
            "tier_log": tier_log, "share_log": share_log,
        },
    )


@router.get("/api/billing/{org_id}")
async def admin_billing_api(
    org_id: int, _username: str = Depends(verify_credentials),
):
    """JSON view of the same data — for ops scripts and CSV exports."""
    from src.modules.billing.database import (
        get_active_subscription,
        get_current_revenue_share,
        get_revenue_share_history,
        get_tier_history,
    )
    from src.modules.organizations.database import get_organization

    org = get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail=f"org {org_id} not found")

    return JSONResponse(content={
        "org": org,
        "subscription": get_active_subscription(org_id),
        "current_revenue_share": get_current_revenue_share(org_id),
        "tier_history": get_tier_history(org_id),
        "revenue_share_history": get_revenue_share_history(org_id),
    })
