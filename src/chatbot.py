"""Amplora FastAPI app — Phase 1 multi-tenant SaaS for Main Street businesses.

This file used to be a Gradio RAG chatbot wired to article/edition/ad
ingestion. As of 2026-05-10 the chatbot, ingestion, vision pipeline,
and publisher-newspaper features were moved to a separate server.
What remains is the Amplora implementation:

  W1 — Multi-tenant billing (Stripe + tier provisioning + publisher
       attribution + revenue share). Owner-facing at /business/billing,
       admin audit at /admin/billing/{org_id}, webhook at /webhooks/stripe.

  W2 — Product Marketing Context (PMC) pipeline. Owner-facing at
       /business/pmc — pre-interview form + transcript paste +
       review/edit/accept.

  Admin — Main Street OS invite creation + billing audit at /admin/*.

Boot:
  uv run python src/chatbot.py
  Defaults to PORT 8080 (Railway-friendly). Override via PORT env var.
"""

import logging
import os
import sys
from pathlib import Path

# Allow `python src/chatbot.py` to find the `src` package. When you run a
# file directly, Python only puts its directory on sys.path, not the project
# root — so `import src.core.config` would fail. Inserting the parent of
# `src/` fixes it for both `python src/chatbot.py` and `python -m src.chatbot`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import src.core.config  # noqa: E402, F401  (load .env via dotenv side-effect)
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from src.core.database import init_all_tables  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and return the Amplora FastAPI app."""
    init_all_tables()

    app = FastAPI(title="Amplafai", description="Main Street marketing OS")

    # Root → bounce to /business/ so visitors land on the owner console.
    @app.get("/", include_in_schema=False)
    async def _root():
        return RedirectResponse(url="/business/", status_code=303)

    # ── Admin console (basic-auth — invite creation, billing audit) ──
    from src.admin_frontend import router as admin_router
    app.include_router(admin_router)
    logger.info("Admin routes mounted at /admin")

    # ── Business owner console (session-auth — Amplora user surface) ──
    from src.business_frontend import router as business_router
    from src.business_frontend.auth import AuthRequired

    app.include_router(business_router)

    @app.exception_handler(AuthRequired)
    async def _biz_auth_redirect(request, exc):  # noqa: ARG001
        return RedirectResponse(url="/business/login", status_code=303)

    logger.info("Business console mounted at /business/")

    # ── W1 Stripe webhook (subscription lifecycle) ──
    # The route verifies Stripe-Signature against STRIPE_WEBHOOK_SECRET;
    # tests bypass by calling apply_event() directly. Optional at boot —
    # missing env vars don't crash the app, the route just returns 503.
    try:
        from src.modules.billing.stripe_webhook import router as stripe_router
        app.include_router(stripe_router)
        logger.info("Stripe webhook mounted at /webhooks/stripe")
    except Exception as e:
        logger.warning(f"Stripe webhook NOT mounted: {e}")

    # ── W2.2 — static assets for the voice interview client ──────────
    # Browser-side JS for pmc_interview.html lives in business_frontend/static.
    # Mount last so it doesn't shadow any /business or /admin route. If the
    # directory doesn't exist (fresh checkout pre-W2.2), skip rather than crash.
    static_dir = _PROJECT_ROOT / "src" / "business_frontend" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        logger.info("Static assets mounted at /static (dir=%s)", static_dir)
    else:
        logger.warning("Static dir missing: %s — voice interview JS won't load", static_dir)

    return app


def main() -> None:
    # Railway / Docker expects PORT from env; local default 8080.
    port = int(os.environ.get("PORT", "8080"))
    print(f"Starting Amplafai on port {port}", flush=True)

    app = create_app()

    import uvicorn

    # Behind Caddy (TLS terminated at the proxy, app reached over plain
    # HTTP on the private compose network). Trust X-Forwarded-Proto/For
    # so request.base_url is https:// — invite/registration links that
    # publishers send to owners must not say http://. Only Caddy can
    # reach this port (web has no published port), so allow-ips="*".
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
