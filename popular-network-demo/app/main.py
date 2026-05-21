"""FastAPI app for the Popular Network marketing dashboard.

Layout:
- /api/*          → JSON endpoints (bootstrap + per-tab CRUD)
- everything else → static files (dashboard.html, future assets/)

Run with `python -m app.main` (or `uv run python -m app.main`). The original
`serve.py` is preserved as a thin shim that delegates here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from .db import init_db
from .routers import approvals, bootstrap, chat, performance, posts, reviews, settings
from .seed import seed_if_empty

ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("popular_network")

app = FastAPI(title="Popular Network — Marketing Dashboard", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    inserted = seed_if_empty()
    if inserted:
        log.info("Seeded Westbrook Auto & Tire (business_id=1)")
    else:
        log.info("DB already seeded — skipping")


@app.middleware("http")
async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Preserve the no-cache semantics of the original serve.py.

    Browsers and preview panels otherwise hold stale dashboard.html and silently
    confuse Trevor about which version they're seeing.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


app.include_router(bootstrap.router, prefix="/api", tags=["bootstrap"])
app.include_router(posts.router, prefix="/api", tags=["posts"])
app.include_router(approvals.router, prefix="/api", tags=["approvals"])
app.include_router(reviews.router, prefix="/api", tags=["reviews"])
app.include_router(performance.router, prefix="/api", tags=["performance"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(ROOT / "dashboard.html")


# Static files: serve dashboard.html and (future) assets/ at the root.
# This MUST be last so /api/* routes win.
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")


def _main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
