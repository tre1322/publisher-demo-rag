"""Routes for mock publisher integration demos."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.modules.articles import get_recent_articles

router = APIRouter(prefix="/demo", tags=["demo"])
templates = Jinja2Templates(directory="src/mock_integrations/templates")


@router.get("/newspaper", response_class=HTMLResponse)
async def newspaper_demo(request: Request) -> HTMLResponse:
    """Render newspaper mockup with embedded chatbot."""
    articles = get_recent_articles(limit=10)
    return templates.TemplateResponse(
        "newspaper.html",
        {
            "request": request,
            "articles": articles,
        },
    )
