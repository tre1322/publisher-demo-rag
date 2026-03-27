"""Public news routes: homepage feed and article pages.

These are unauthenticated public endpoints that render:
- /news/ — Homepage with top stories and section feeds
- /news/{item_id} — Individual article page
- /news/section/{section} — Section-filtered feed
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.modules.content_items.database import (
    get_content_item,
    get_content_items_for_edition,
    get_homepage_content,
)
from src.modules.publishers.database import get_all_publishers_db as get_all_publishers
from src.core.database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["public-news"])


def _get_latest_edition_id(publisher_id: int) -> int | None:
    """Get the most recent edition ID for a publisher."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM editions WHERE publisher_id = ? ORDER BY id DESC LIMIT 1",
        (publisher_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def _get_section_counts(edition_id: int) -> dict:
    """Get content type counts for an edition."""
    items = get_content_items_for_edition(edition_id)
    counts = {}
    for item in items:
        ct = item.get("content_type", "news")
        if item.get("homepage_eligible") and item.get("publish_status") == "published":
            counts[ct] = counts.get(ct, 0) + 1
    return counts


# ── CSS ──

PAGE_CSS = """
:root {
    --bg: #0f172a;
    --surface: #1e293b;
    --surface-hover: #334155;
    --border: #334155;
    --text: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent: #f59e0b;
    --accent-hover: #d97706;
    --link: #38bdf8;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    font-size: 15px;
}

.container { max-width: 900px; margin: 0 auto; padding: 0 20px; }

/* Header */
.site-header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 16px 0;
    position: sticky; top: 0; z-index: 100;
}
.site-header .container {
    display: flex; align-items: center; justify-content: space-between;
}
.site-title {
    font-size: 20px; font-weight: 700; color: var(--accent);
    text-decoration: none;
}
.site-title:hover { color: var(--accent-hover); }
.header-meta { font-size: 13px; color: var(--text-muted); }

/* Section Nav */
.section-nav {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 10px 0;
    overflow-x: auto;
}
.section-nav .container { display: flex; gap: 6px; flex-wrap: wrap; }
.section-pill {
    padding: 5px 14px; border-radius: 20px;
    background: var(--bg); color: var(--text-secondary);
    text-decoration: none; font-size: 13px; font-weight: 500;
    border: 1px solid var(--border);
    transition: all 150ms;
    white-space: nowrap;
}
.section-pill:hover, .section-pill.active {
    background: var(--accent); color: var(--bg);
    border-color: var(--accent);
}
.section-pill .count {
    font-size: 11px; color: var(--text-muted);
    margin-left: 4px;
}
.section-pill:hover .count, .section-pill.active .count { color: var(--bg); }

/* Story Cards */
.stories { padding: 24px 0; }
.section-label {
    font-size: 12px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--accent);
    margin-bottom: 16px; padding-bottom: 8px;
    border-bottom: 2px solid var(--accent);
}
.story-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px; padding: 20px;
    margin-bottom: 12px;
    transition: all 150ms;
    text-decoration: none; display: block; color: inherit;
}
.story-card:hover {
    border-color: var(--accent);
    background: var(--surface-hover);
    transform: translateY(-1px);
}
.story-headline {
    font-size: 18px; font-weight: 700; color: var(--text);
    margin-bottom: 6px; line-height: 1.3;
}
.story-card.featured .story-headline { font-size: 22px; }
.story-meta {
    font-size: 13px; color: var(--text-muted);
    display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px;
}
.story-meta .tag {
    background: var(--bg); padding: 2px 8px; border-radius: 4px;
    font-size: 11px; text-transform: uppercase; font-weight: 600;
    letter-spacing: 0.04em;
}
.story-meta .tag.news { color: #38bdf8; }
.story-meta .tag.sports { color: #4ade80; }
.story-meta .tag.police { color: #f87171; }
.story-meta .tag.legal { color: #a78bfa; }
.story-meta .tag.community { color: #fb923c; }
.story-meta .tag.opinion { color: #e879f9; }
.story-meta .tag.proceedings { color: #2dd4bf; }
.story-meta .tag.obituary { color: #94a3b8; }
.story-preview {
    font-size: 14px; color: var(--text-secondary);
    line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3;
    -webkit-box-orient: vertical; overflow: hidden;
}

/* Article Page */
.article-page { padding: 32px 0; }
.article-back {
    display: inline-flex; align-items: center; gap: 6px;
    color: var(--text-muted); font-size: 14px;
    text-decoration: none; margin-bottom: 20px;
}
.article-back:hover { color: var(--accent); }
.article-header { margin-bottom: 24px; }
.article-type {
    font-size: 12px; text-transform: uppercase; font-weight: 600;
    letter-spacing: 0.06em; color: var(--accent);
    margin-bottom: 8px;
}
.article-title {
    font-size: 28px; font-weight: 700; line-height: 1.2;
    margin-bottom: 10px;
}
.article-byline { font-size: 14px; color: var(--text-secondary); margin-bottom: 4px; }
.article-page-info { font-size: 13px; color: var(--text-muted); }
.article-body {
    font-size: 16px; line-height: 1.8; color: var(--text);
    max-width: 680px;
}
.article-body p { margin-bottom: 16px; }

/* Footer */
.site-footer {
    border-top: 1px solid var(--border);
    padding: 24px 0; margin-top: 40px;
    text-align: center; font-size: 13px; color: var(--text-muted);
}

/* Empty State */
.empty-state {
    text-align: center; padding: 60px 20px;
    color: var(--text-muted); font-size: 16px;
}
"""


# ── HTML Templates ──


def _page_wrapper(title: str, content: str, publisher_name: str = "Local News") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | {publisher_name}</title>
    <style>{PAGE_CSS}</style>
</head>
<body>
    <header class="site-header">
        <div class="container">
            <a href="/news/" class="site-title">{publisher_name}</a>
            <span class="header-meta">{datetime.now().strftime('%B %d, %Y')}</span>
        </div>
    </header>
    {content}
    <footer class="site-footer">
        <div class="container">
            Powered by Popular Network
        </div>
    </footer>
</body>
</html>"""


def _story_card_html(item: dict, featured: bool = False) -> str:
    item_id = item.get("id", 0)
    headline = item.get("headline", "Untitled")
    content_type = item.get("content_type", "news")
    byline = item.get("byline", "")
    page = item.get("start_page", "")
    body = item.get("cleaned_web_text", "") or item.get("raw_text", "")

    # Preview: first 200 chars of body (skip headline part)
    lines = body.split("\n\n", 1)
    preview = lines[1][:200] if len(lines) > 1 else body[:200]
    preview = preview.replace("<", "&lt;").replace(">", "&gt;")

    featured_class = " featured" if featured else ""
    byline_html = f'<span>{byline}</span>' if byline else ''
    page_html = f'<span>Page {page}</span>' if page else ''

    return f"""
    <a href="/news/{item_id}" class="story-card{featured_class}">
        <div class="story-headline">{headline}</div>
        <div class="story-meta">
            <span class="tag {content_type}">{content_type}</span>
            {byline_html}
            {page_html}
        </div>
        <div class="story-preview">{preview}</div>
    </a>"""


# ── Routes ──


@router.get("/", response_class=HTMLResponse)
async def news_homepage():
    """Public news homepage with top stories."""
    publishers = get_all_publishers()
    if not publishers:
        return HTMLResponse(_page_wrapper("News", '<div class="empty-state">No publishers configured.</div>'))

    # Find the publisher with the latest edition that has published content
    publisher = None
    for pub in publishers:
        eid = _get_latest_edition_id(pub["id"])
        if eid:
            stories = get_homepage_content(pub["id"], limit=1)
            if stories:
                publisher = pub
                break

    if not publisher:
        publisher = publishers[0]

    publisher_id = publisher["id"]
    publisher_name = publisher.get("name", "Local News")

    edition_id = _get_latest_edition_id(publisher_id)
    if not edition_id:
        return HTMLResponse(_page_wrapper("News", '<div class="empty-state">No editions published yet.</div>', publisher_name))

    # Get section counts for nav
    section_counts = _get_section_counts(edition_id)
    total = sum(section_counts.values())

    # Section nav
    nav_html = f'<a href="/news/" class="section-pill active">All <span class="count">{total}</span></a>'
    for section, count in sorted(section_counts.items(), key=lambda x: -x[1]):
        nav_html += f'<a href="/news/section/{section}" class="section-pill">{section.title()} <span class="count">{count}</span></a>'

    section_nav = f'<nav class="section-nav"><div class="container">{nav_html}</div></nav>'

    # Top stories
    stories = get_homepage_content(publisher_id, limit=30)
    if not stories:
        content = section_nav + '<div class="empty-state">No stories published yet. Upload an edition to get started.</div>'
        return HTMLResponse(_page_wrapper("News", content, publisher_name))

    cards_html = ""
    for i, story in enumerate(stories):
        cards_html += _story_card_html(story, featured=(i < 3))

    content = f"""
    {section_nav}
    <div class="stories">
        <div class="container">
            <div class="section-label">Latest Stories</div>
            {cards_html}
        </div>
    </div>"""

    return HTMLResponse(_page_wrapper("News", content, publisher_name))


@router.get("/section/{section}", response_class=HTMLResponse)
async def news_section(section: str):
    """Section-filtered news feed."""
    publishers = get_all_publishers()
    if not publishers:
        return HTMLResponse(_page_wrapper("News", '<div class="empty-state">No publishers.</div>'))

    publisher = publishers[0]
    publisher_id = publisher["id"]
    publisher_name = publisher.get("name", "Local News")

    edition_id = _get_latest_edition_id(publisher_id)
    if not edition_id:
        return HTMLResponse(_page_wrapper(section.title(), '<div class="empty-state">No editions.</div>', publisher_name))

    section_counts = _get_section_counts(edition_id)
    total = sum(section_counts.values())

    nav_html = f'<a href="/news/" class="section-pill">All <span class="count">{total}</span></a>'
    for sec, count in sorted(section_counts.items(), key=lambda x: -x[1]):
        active = " active" if sec == section else ""
        nav_html += f'<a href="/news/section/{sec}" class="section-pill{active}">{sec.title()} <span class="count">{count}</span></a>'
    section_nav = f'<nav class="section-nav"><div class="container">{nav_html}</div></nav>'

    items = get_content_items_for_edition(edition_id)
    filtered = [i for i in items if i.get("content_type") == section and i.get("homepage_eligible") and i.get("publish_status") == "published"]
    filtered.sort(key=lambda x: x.get("homepage_score", 0), reverse=True)

    if not filtered:
        content = section_nav + f'<div class="empty-state">No {section} stories found.</div>'
        return HTMLResponse(_page_wrapper(section.title(), content, publisher_name))

    cards_html = "".join(_story_card_html(s) for s in filtered)
    content = f"""
    {section_nav}
    <div class="stories">
        <div class="container">
            <div class="section-label">{section.title()}</div>
            {cards_html}
        </div>
    </div>"""

    return HTMLResponse(_page_wrapper(section.title(), content, publisher_name))


@router.get("/{item_id}", response_class=HTMLResponse)
async def article_page(item_id: int):
    """Public article page."""
    item = get_content_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Article not found")

    headline = item.get("headline", "Untitled")
    content_type = item.get("content_type", "news")
    byline = item.get("byline", "")
    start_page = item.get("start_page", "")
    end_page = item.get("end_page", "")
    body = item.get("cleaned_web_text", "") or item.get("raw_text", "")

    # Convert body to paragraphs
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    # Skip the first paragraph if it's the same as the headline
    if paragraphs and paragraphs[0].strip() == headline.strip():
        paragraphs = paragraphs[1:]

    body_html = "".join(f"<p>{p.replace(chr(10), ' ')}</p>" for p in paragraphs)

    byline_html = f'<div class="article-byline">{byline}</div>' if byline else ""
    page_info = f"Page {start_page}"
    if end_page and end_page != start_page:
        page_info += f", continued on page {end_page}"

    # Get publisher name from the content item's publisher_id
    publishers = get_all_publishers()
    pub_map = {p["id"]: p for p in publishers}
    pub = pub_map.get(item.get("publisher_id"), {})
    publisher_name = pub.get("name", "Local News")

    content = f"""
    <div class="article-page">
        <div class="container">
            <a href="/news/" class="article-back">&larr; Back to News</a>
            <article class="article-header">
                <div class="article-type">{content_type}</div>
                <h1 class="article-title">{headline}</h1>
                {byline_html}
                <div class="article-page-info">{page_info}</div>
            </article>
            <div class="article-body">
                {body_html}
            </div>
        </div>
    </div>"""

    return HTMLResponse(_page_wrapper(headline, content, publisher_name))
