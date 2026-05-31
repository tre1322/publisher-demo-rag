"""RequireBusinessMiddleware — populates request.state from the session cookie.

After this middleware runs, request.state has:
  - user_id          : int | None
  - business_id      : int | None  (active business for the session)
  - user_role        : str | None  (BusinessUser.role for the active business)
  - is_superuser     : bool

Routers read `request.state.business_id` instead of hardcoding `business_id=1`.

Routes can opt out of the auth requirement via the EXEMPT_PATHS prefix list
below — kept tight (only login, widget, static, root HTML, OpenAPI docs).
"""
from __future__ import annotations

from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from ..db import SessionLocal, current_tenant_id
from ..models import Business, BusinessUser, User
from .sessions import COOKIE_NAME, lookup_session, touch_session

# Paths that bypass the auth check entirely (truly public).
# /api/auth/logout is here so a stale/expired session can still call it to
# clear the cookie — the endpoint handles the no-cookie case gracefully.
#
# Note: /login (HTML page) is NOT exempt. We want the middleware to populate
# request.state.user_id so the route handler can redirect logged-in users
# AWAY from /login back to /. Unauthed users still get through (the
# "no /api/ + no cookie → call_next" branch below handles HTML pages).
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/logout",            # both /logout and /logout-all match — see AUTH_NO_BIZ to require auth for -all
    "/api/auth/invites/lookup",    # public — preview invite for the claim page
    "/api/auth/invites/claim",     # public — accept invite + mint session
    "/api/widget/",                # public widget chat (H.2)
    "/static/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon",
)

# Prefixes that require auth but DON'T require an active business
# (e.g. /me right after login when the user hasn't picked a business yet).
AUTH_BUT_NO_BUSINESS_PREFIXES: tuple[str, ...] = (
    "/api/auth/me",
    "/api/auth/businesses",  # list the user's businesses to pick from
    "/api/auth/switch",      # change active business
    "/api/auth/logout-all",  # nuke every session for current user
)


class RequireBusinessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Initialize state defaults so downstream code can read them unconditionally.
        request.state.user_id = None
        request.state.business_id = None
        request.state.user_role = None
        request.state.is_superuser = False

        path = request.url.path

        # Bypass auth entirely for exempt prefixes (login, widget, static, etc).
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        # Resolve session from cookie.
        token = request.cookies.get(COOKIE_NAME)
        if token is None:
            # No cookie: deny API calls, let HTML pages render so login.html
            # can prompt — the static handler returns dashboard.html which
            # will detect /api/auth/me 401 and redirect client-side.
            if path.startswith("/api/"):
                return JSONResponse({"detail": "not_authenticated"}, status_code=401)
            return await call_next(request)

        # DB lookup. One session per request — kept local to the middleware.
        with SessionLocal() as db:
            session = lookup_session(db, token)
            if session is None:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "session_expired"}, status_code=401)
                return await call_next(request)

            user = db.get(User, session.user_id)
            if user is None or not user.is_active:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "user_disabled"}, status_code=401)
                return await call_next(request)

            request.state.user_id = user.id
            request.state.is_superuser = user.is_superuser

            # Active business + role.
            if session.active_business_id is not None:
                bu = (
                    db.query(BusinessUser)
                    .filter(
                        BusinessUser.user_id == user.id,
                        BusinessUser.business_id == session.active_business_id,
                    )
                    .first()
                )
                if bu is not None:
                    request.state.business_id = bu.business_id
                    request.state.user_role = bu.role

            # Superuser fallback: if no business is set yet, default to the
            # first business in the DB so the platform operator lands on a
            # usable dashboard immediately. Future work: per-superuser session
            # remembers the last-selected tenant; eventual ops UI lets them
            # mint invites + switch tenants without ever needing a BusinessUser
            # row. For pilot-#1 (one tenant), defaulting is correct.
            if request.state.business_id is None and user.is_superuser:
                first_biz = (
                    db.query(Business).order_by(Business.id.asc()).first()
                )
                if first_biz is not None:
                    request.state.business_id = first_biz.id
                    request.state.user_role = "owner"

            # If the route needs a business and we don't have one → 409 (not 401:
            # the user IS authed, just hasn't picked a business yet).
            needs_business = path.startswith("/api/") and not any(
                path.startswith(p) for p in AUTH_BUT_NO_BUSINESS_PREFIXES
            )
            if needs_business and request.state.business_id is None and not user.is_superuser:
                return JSONResponse({"detail": "no_active_business"}, status_code=409)

            touch_session(db, session)
            db.commit()

        # Set the tenant ContextVar for db.py's auto-filter. Superusers leave
        # it None (so admin routes can read across all businesses by default
        # — they can still scope manually by passing business_id explicitly).
        token_handle = None
        if request.state.business_id is not None and not request.state.is_superuser:
            token_handle = current_tenant_id.set(request.state.business_id)
        try:
            return await call_next(request)
        finally:
            if token_handle is not None:
                current_tenant_id.reset(token_handle)
