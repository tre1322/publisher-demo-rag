"""Routes for HTML admin dashboard."""

import json
import logging
import os
import secrets
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from src.core.config import DATA_DIR
from src.core.database import get_connection
from src.modules.analytics import get_click_stats, get_impression_stats
from src.modules.conversations.database import (
    get_all_conversations,
    get_conversation_messages,
    get_conversation_stats,
)
from src.modules.articles import (
    get_article_by_id,
    get_articles_for_edition,
    get_articles_needing_review,
    update_article,
)
from src.modules.editions import (
    get_all_editions,
    get_edition_count,
    get_regions_for_article,
    get_review_actions_for_article,
    insert_review_action,
)
from src.modules.organizations import (
    get_all_organizations,
    get_all_publications,
    insert_organization,
    insert_publication,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/admin_frontend/templates")

# Security
security = HTTPBasic()

# Database browser constants
BROWSABLE_TABLES = [
    "articles",
    "advertisements",
    "events",
    "editions",
    "organizations",
    "publications",
    "page_regions",
    "review_actions",
    "conversations",
    "conversation_messages",
    "content_impressions",
    "url_clicks",
]

# Edition PDF upload directory
EDITIONS_DIR = DATA_DIR / "editions"
EDITIONS_DIR.mkdir(parents=True, exist_ok=True)
TRUNCATE_COLUMNS = {"raw_text", "content", "summary", "description", "subjects"}
TRUNCATE_LENGTH = 100


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth credentials.

    Args:
        credentials: HTTP Basic credentials.

    Returns:
        Username if valid.

    Raises:
        HTTPException: If credentials are invalid.
    """
    admin_password = os.getenv("ADMIN_PASSWORD", "admin")

    is_username_correct = secrets.compare_digest(credentials.username, "admin")
    is_password_correct = secrets.compare_digest(credentials.password, admin_password)

    if not (is_username_correct and is_password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# Page route
@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render admin dashboard page."""
    return templates.TemplateResponse("admin.html", {"request": request})


# API routes
@router.get("/api/stats")
async def get_stats(_username: str = Depends(verify_credentials)) -> JSONResponse:
    """Get conversation statistics."""
    try:
        stats = get_conversation_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return JSONResponse(
            content={"error": str(e), "total_conversations": 0, "total_messages": 0},
            status_code=200,
        )


@router.get("/api/queries")
async def get_queries(
    limit: int = 100, top_n: int = 20, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Get most common queries."""
    conversations = get_all_conversations(limit=limit)
    all_queries = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        user_messages = [m for m in messages if m["role"] == "user"]
        all_queries.extend([m["content"] for m in user_messages])

    query_counts = Counter(all_queries)
    data = [
        {"query": query, "count": count}
        for query, count in query_counts.most_common(top_n)
    ]

    return JSONResponse(content=data)


@router.get("/api/words")
async def get_words(
    limit: int = 100, top_n: int = 30, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Get most common words in queries."""
    conversations = get_all_conversations(limit=limit)
    all_words = []

    stop_words = {
        "what", "whats", "about", "this", "that", "with", "from",
        "have", "there", "them", "they",
    }

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        user_messages = [m for m in messages if m["role"] == "user"]

        for msg in user_messages:
            words = [
                w.lower().strip("?.,!")
                for w in msg["content"].split()
                if len(w) > 3 and w.lower() not in stop_words
            ]
            all_words.extend(words)

    word_counts = Counter(all_words)
    data = [
        {"word": word, "count": count}
        for word, count in word_counts.most_common(top_n)
    ]

    return JSONResponse(content=data)


def _calculate_duration(started_at: str, ended_at: str | None) -> str:
    """Calculate conversation duration."""
    if not ended_at:
        return "In progress"

    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        duration = end - start

        if duration.total_seconds() < 60:
            return f"{int(duration.total_seconds())}s"
        elif duration.total_seconds() < 3600:
            return f"{int(duration.total_seconds() / 60)}m"
        else:
            return f"{duration.total_seconds() / 3600:.1f}h"
    except Exception:
        return "Unknown"


@router.get("/api/conversations")
async def get_conversations_list(
    limit: int = 10, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Get recent conversations with details."""
    conversations = get_all_conversations(limit=limit)
    results = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])

        # Format conversation preview
        preview_lines = []
        for msg in messages[:4]:
            role_emoji = "👤" if msg["role"] == "user" else "🤖"
            content = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
            preview_lines.append(f"{role_emoji} {content}")

        results.append({
            "session_id": conv["session_id"][:8] + "...",
            "started_at": conv["started_at"],
            "message_count": conv["message_count"],
            "duration": _calculate_duration(conv["started_at"], conv["ended_at"]),
            "preview": "\n".join(preview_lines),
        })

    return JSONResponse(content=results)


@router.get("/api/engagement")
async def get_engagement(_username: str = Depends(verify_credentials)) -> JSONResponse:
    """Get engagement analytics."""
    impression_stats = get_impression_stats()
    click_stats = get_click_stats()

    # Calculate totals
    total_impressions = sum(impression_stats.get("by_type", {}).values())
    total_clicks = click_stats.get("total_clicks", 0)
    overall_ctr = f"{(total_clicks / total_impressions * 100):.1f}%" if total_impressions > 0 else "0%"

    # CTR by type
    ctr_by_type = []
    for content_type, stats in click_stats.get("ctr_by_type", {}).items():
        ctr_by_type.append({
            "type": content_type,
            "shown": stats["shown"],
            "clicked": stats["clicked"],
            "ctr": f"{stats['ctr_percent']}%",
        })

    # Top clicked
    top_clicked = [
        {"type": item["content_type"], "content_id": item["content_id"], "clicks": item["clicks"]}
        for item in click_stats.get("top_clicked", [])[:10]
    ]

    # Top shown
    top_shown = [
        {"type": item["content_type"], "content_id": item["content_id"], "impressions": item["impressions"]}
        for item in impression_stats.get("top_content", [])[:10]
    ]

    return JSONResponse(content={
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "overall_ctr": overall_ctr,
        "ctr_by_type": ctr_by_type,
        "top_clicked": top_clicked,
        "top_shown": top_shown,
    })


@router.get("/api/table/{table_name}")
async def get_table(
    table_name: str,
    page: int = 1,
    page_size: int = 25,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get paginated table data."""
    if table_name not in BROWSABLE_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid table: {table_name}")

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        total_count = cursor.fetchone()[0]

        # Get paginated data
        offset = (page - 1) * page_size
        cursor.execute(
            f"SELECT * FROM {table_name} LIMIT ? OFFSET ?",  # noqa: S608
            (page_size, offset),
        )
        rows = cursor.fetchall()

        # Get column names
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()

        # Convert to list of dicts and truncate long text
        data = []
        for row in rows:
            row_dict = dict(row)
            for col in TRUNCATE_COLUMNS:
                if col in row_dict and row_dict[col] and len(str(row_dict[col])) > TRUNCATE_LENGTH:
                    row_dict[col] = str(row_dict[col])[:TRUNCATE_LENGTH] + "..."
            data.append(row_dict)

        total_pages = max(1, (total_count + page_size - 1) // page_size)

        return JSONResponse(content={
            "columns": columns,
            "rows": data,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_count": total_count,
        })
    except Exception as e:
        logger.error(f"Failed to browse table '{table_name}': {e}")
        return JSONResponse(
            content={"columns": [], "rows": [], "page": 1, "total_pages": 0,
                     "total_count": 0, "error": str(e)},
            status_code=200,
        )


@router.get("/api/export")
async def export_data(
    limit: int = 100, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Export conversations to JSON."""
    conversations = get_all_conversations(limit=limit)
    export_data = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        export_data.append({
            "session_id": conv["session_id"],
            "started_at": conv["started_at"],
            "ended_at": conv["ended_at"],
            "message_count": conv["message_count"],
            "messages": messages,
        })

    # Save to file
    output_path = "data/conversations_export.json"
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2)

    return JSONResponse(content={
        "success": True,
        "path": output_path,
        "count": len(export_data),
    })


@router.get("/api/tables")
async def list_tables(_username: str = Depends(verify_credentials)) -> JSONResponse:
    """List available tables for browsing."""
    return JSONResponse(content={"tables": BROWSABLE_TABLES})


# ── Edition upload endpoints ──


@router.get("/api/editions")
async def get_editions_list(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get all editions with their status."""
    editions = get_all_editions(limit=50)
    return JSONResponse(content={"editions": editions, "total": get_edition_count()})


@router.post("/api/editions/upload")
async def upload_editions(
    files: list[UploadFile] = File(...),
    publisher: str = Form(...),
    organization_name: str = Form(""),
    publication_name: str = Form(""),
    edition_date: str = Form(""),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload and process newspaper edition PDFs."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Auto-create org/pub
    org_name = organization_name or publisher
    pub_name = publication_name or publisher
    organization_id = insert_organization(org_name)
    publication_id = insert_publication(organization_id=organization_id, name=pub_name)

    # Save uploaded files
    saved_paths: list[Path] = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            logger.warning(f"Skipping non-PDF upload: {file.filename}")
            continue

        dest = EDITIONS_DIR / file.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        saved_paths.append(dest)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="No valid PDF files in upload")

    try:
        from src.edition_ingestion import EditionIngester

        ingester = EditionIngester(
            publisher=publisher,
            publication_name=pub_name,
            organization_id=organization_id,
            publication_id=publication_id,
        )
    except Exception as e:
        logger.error(f"Edition ingester initialization failed: {e}")
        return JSONResponse(
            content={"success": False, "error": f"Ingester init failed: {e}"},
            status_code=500,
        )

    results = ingester.ingest_bulk(saved_paths, edition_date=edition_date or None)

    total_articles = sum(r.get("articles", 0) for r in results)
    total_ads = sum(r.get("ads", 0) for r in results)
    failures = sum(1 for r in results if r.get("error"))

    return JSONResponse(content={
        "success": True,
        "files_processed": len(saved_paths),
        "total_articles": total_articles,
        "total_ads": total_ads,
        "failures": failures,
        "details": results,
    })


# ── Ad upload endpoints (Track 1) ──


@router.post("/api/ads/upload")
async def upload_ads(
    files: list[UploadFile] = File(...),
    publisher: str = Form(...),
    organization_name: str = Form(""),
    publication_name: str = Form(""),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload individual ad PDFs with checksum dedup."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    org_name = organization_name or publisher
    pub_name = publication_name or publisher
    organization_id = insert_organization(org_name)
    publication_id = insert_publication(organization_id=organization_id, name=pub_name)

    try:
        from src.ad_ingestion import AdIngester

        ingester = AdIngester()
    except Exception as e:
        logger.error(f"Ad ingester initialization failed: {e}")
        return JSONResponse(
            content={"success": False, "error": f"Ingester init failed: {e}"},
            status_code=500,
        )
    results = []

    from src.ad_processing import is_image_file

    SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}

    for file in files:
        if not file.filename:
            results.append({"filename": file.filename, "error": "No filename"})
            continue

        ext = file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
        if f".{ext}" not in SUPPORTED_EXTENSIONS:
            results.append({"filename": file.filename, "error": f"Unsupported file type: .{ext}"})
            continue

        data = await file.read()

        if is_image_file(file.filename):
            result = ingester.ingest_ad_image_bytes(
                data=data,
                filename=file.filename,
                organization_id=organization_id,
                publication_id=publication_id,
                publisher=publisher,
            )
        else:
            result = ingester.ingest_ad_bytes(
                data=data,
                filename=file.filename,
                organization_id=organization_id,
                publication_id=publication_id,
                publisher=publisher,
            )
        results.append(result)

    ingested = sum(1 for r in results if r.get("ad_id") and not r.get("error"))
    duplicates = sum(1 for r in results if r.get("duplicate"))
    failures = sum(1 for r in results if r.get("error") and not r.get("duplicate"))
    warnings = sum(1 for r in results if r.get("warning"))

    return JSONResponse(content={
        "success": True,
        "files_received": len(files),
        "ingested": ingested,
        "duplicates_rejected": duplicates,
        "failures": failures,
        "indexing_warnings": warnings,
        "details": results,
    })


# ── Organization/Publication endpoints ──


@router.get("/api/organizations")
async def list_organizations(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    return JSONResponse(content={"organizations": get_all_organizations()})


@router.get("/api/publications")
async def list_publications(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    return JSONResponse(content={"publications": get_all_publications()})


# ── Publisher endpoints ──


@router.get("/api/publishers")
async def list_publishers(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List all publishers."""
    from src.modules.publishers import get_all_publishers_db

    try:
        publishers = get_all_publishers_db()
        return JSONResponse(content={"publishers": publishers})
    except Exception as e:
        logger.error(f"Failed to list publishers: {e}")
        return JSONResponse(content={"publishers": [], "error": str(e)})


@router.post("/api/publishers/{publisher_id}/editions/upload")
async def upload_publisher_edition(
    publisher_id: int,
    files: list[UploadFile] = File(...),
    edition_date: str = Form(""),
    issue_label: str = Form(""),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload edition PDFs for a specific publisher (tenant-aware)."""
    from src.modules.publishers.uploads import upload_edition

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    results = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            results.append({"filename": file.filename, "error": "Not a PDF"})
            continue

        data = await file.read()
        result = upload_edition(
            publisher_id=publisher_id,
            data=data,
            filename=file.filename,
            edition_date=edition_date or None,
            issue_label=issue_label or None,
        )
        results.append(result)

    uploaded = sum(1 for r in results if r.get("edition_id") and not r.get("error"))
    duplicates = sum(1 for r in results if r.get("duplicate"))
    failures = sum(1 for r in results if r.get("error") and not r.get("duplicate"))

    return JSONResponse(content={
        "success": True,
        "publisher_id": publisher_id,
        "files_received": len(files),
        "uploaded": uploaded,
        "duplicates_rejected": duplicates,
        "failures": failures,
        "details": results,
    })


# ── Article review/edit endpoints ──


@router.get("/api/articles")
async def list_articles(
    edition_id: int | None = None,
    needs_review: bool | None = None,
    limit: int = 50,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List articles with optional filters."""
    if edition_id:
        articles = get_articles_for_edition(edition_id)
    elif needs_review:
        articles = get_articles_needing_review(limit=limit)
    else:
        articles = get_articles_needing_review(limit=limit)
    return JSONResponse(content={"articles": articles})


@router.get("/api/articles/{doc_id}")
async def get_article_detail(
    doc_id: str,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get full article detail including regions and review history."""
    article = get_article_by_id(doc_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    regions = get_regions_for_article(doc_id)
    review_history = get_review_actions_for_article(doc_id)

    return JSONResponse(content={
        "article": article,
        "regions": regions,
        "review_history": review_history,
    })


@router.put("/api/articles/{doc_id}")
async def edit_article(
    doc_id: str,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Edit article fields (headline, byline, text, status)."""
    article = get_article_by_id(doc_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    body = await request.json()

    # Record before state for audit
    before = {
        "title": article.get("title"),
        "author": article.get("author"),
        "cleaned_text": (article.get("cleaned_text") or "")[:200],
        "subheadline": article.get("subheadline"),
    }

    update_article(
        doc_id=doc_id,
        title=body.get("title"),
        author=body.get("author"),
        cleaned_text=body.get("cleaned_text"),
        subheadline=body.get("subheadline"),
        status=body.get("status"),
        needs_review=body.get("needs_review"),
    )

    after = {k: body.get(k) for k in ["title", "author", "cleaned_text", "subheadline", "status"] if body.get(k) is not None}
    insert_review_action(
        article_id=doc_id,
        action_type="edit",
        before_json=before,
        after_json=after,
    )

    return JSONResponse(content={"success": True})


@router.post("/api/articles/{doc_id}/approve")
async def approve_article(
    doc_id: str,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Mark article as reviewed/approved."""
    article = get_article_by_id(doc_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    update_article(doc_id=doc_id, status="approved", needs_review=False)
    insert_review_action(article_id=doc_id, action_type="approve")

    return JSONResponse(content={"success": True})


@router.post("/api/articles/{doc_id}/flag")
async def flag_article(
    doc_id: str,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Flag article as problematic."""
    article = get_article_by_id(doc_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    body = await request.json()
    reason = body.get("reason", "")

    update_article(doc_id=doc_id, status="flagged", needs_review=True)
    insert_review_action(
        article_id=doc_id,
        action_type="flag",
        after_json={"reason": reason},
    )

    return JSONResponse(content={"success": True})


# ── Review page ──


@router.get("/review", response_class=HTMLResponse)
async def review_page(
    request: Request, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render the article review page."""
    return templates.TemplateResponse("review.html", {"request": request})


# ── Phase 1: Extraction endpoints ──


@router.post("/api/editions/{edition_id}/extract")
async def trigger_extraction(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 1 raw block extraction on an edition.

    Extracts text blocks and drawings from each page of the edition's PDF
    and saves per-page JSON artifacts.
    """
    from src.modules.extraction import extract_edition

    try:
        result = extract_edition(edition_id)
        status_code = 200 if result["success"] else 400
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error(f"Extraction trigger failed for edition {edition_id}: {e}", exc_info=True)
        return JSONResponse(
            content={"success": False, "edition_id": edition_id, "error": str(e)},
            status_code=500,
        )


@router.get("/api/editions/{edition_id}/extraction")
async def get_extraction_status(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get extraction status and summary for an edition."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.extract_pages import get_extraction_summary

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    summary = get_extraction_summary(publisher_id, edition_id) if publisher_id else None

    return JSONResponse(content={
        "edition_id": edition_id,
        "extraction_status": edition.get("extraction_status"),
        "processing_notes": edition.get("processing_notes"),
        "page_count": edition.get("page_count"),
        "has_artifacts": summary is not None,
        "summary": summary,
    })


@router.get("/api/editions/{edition_id}/pages/{page_number}")
async def get_page_extraction(
    edition_id: int,
    page_number: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get the raw extraction artifact for a single page.

    Returns all text blocks and drawings extracted from the specified page.
    """
    from src.modules.editions.database import get_edition
    from src.modules.extraction.extract_pages import get_page_artifact

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        raise HTTPException(status_code=400, detail=f"Edition {edition_id} has no publisher_id")

    artifact = get_page_artifact(publisher_id, edition_id, page_number)
    if not artifact:
        raise HTTPException(
            status_code=404,
            detail=f"No extraction artifact for edition {edition_id}, page {page_number}",
        )

    return JSONResponse(content=artifact)


# ── Phase 2: Column detection & block classification endpoints ──


@router.post("/api/editions/{edition_id}/enrich")
async def trigger_enrichment(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 2 column detection and block classification.

    Requires Phase 1 extraction to be completed first.
    """
    from src.modules.extraction.classify_blocks import enrich_edition

    try:
        result = enrich_edition(edition_id)
        status_code = 200 if result["success"] else 400
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error(f"Enrichment failed for edition {edition_id}: {e}", exc_info=True)
        return JSONResponse(
            content={"success": False, "edition_id": edition_id, "error": str(e)},
            status_code=500,
        )


@router.get("/api/editions/{edition_id}/enrichment")
async def get_enrichment_status(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get Phase 2 enrichment summary for an edition."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.classify_blocks import get_enrichment_summary

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    summary = get_enrichment_summary(publisher_id, edition_id) if publisher_id else None

    return JSONResponse(content={
        "edition_id": edition_id,
        "has_enrichment": summary is not None,
        "summary": summary,
    })


@router.get("/api/editions/{edition_id}/pages/{page_number}/enriched")
async def get_enriched_page_endpoint(
    edition_id: int,
    page_number: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get enriched page artifact with column IDs and block roles."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.classify_blocks import get_enriched_page

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        raise HTTPException(status_code=400, detail=f"Edition {edition_id} has no publisher_id")

    enriched = get_enriched_page(publisher_id, edition_id, page_number)
    if not enriched:
        raise HTTPException(
            status_code=404,
            detail=f"No enriched artifact for edition {edition_id}, page {page_number}. Run Phase 2 enrichment first.",
        )

    return JSONResponse(content=enriched)


# ── Phase 3: Article assembly endpoints ──


@router.post("/api/editions/{edition_id}/assemble")
async def trigger_assembly(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 3 single-page article assembly.

    Requires Phase 2 enrichment to be completed first.
    """
    from src.modules.extraction.assemble_articles import assemble_edition

    try:
        result = assemble_edition(edition_id)
        status_code = 200 if result["success"] else 400
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error(f"Assembly failed for edition {edition_id}: {e}", exc_info=True)
        return JSONResponse(
            content={"success": False, "edition_id": edition_id, "error": str(e)},
            status_code=500,
        )


@router.get("/api/editions/{edition_id}/assembly")
async def get_assembly_status(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get Phase 3 assembly results for an edition."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.assemble_articles import get_assembly

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    assembly = get_assembly(publisher_id, edition_id) if publisher_id else None

    if not assembly:
        return JSONResponse(content={
            "edition_id": edition_id,
            "has_assembly": False,
            "summary": None,
        })

    # Return summary without full article bodies (those can be large)
    summary = {
        "edition_id": assembly["edition_id"],
        "page_count": assembly["page_count"],
        "total_articles": assembly["total_articles"],
        "assembly_time_seconds": assembly.get("assembly_time_seconds"),
        "pages": assembly["pages"],
    }

    return JSONResponse(content={
        "edition_id": edition_id,
        "has_assembly": True,
        "summary": summary,
    })


@router.get("/api/editions/{edition_id}/articles")
async def get_edition_articles(
    edition_id: int,
    page: int = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get assembled articles, optionally filtered by page number."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.assemble_articles import get_assembly

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    assembly = get_assembly(publisher_id, edition_id) if publisher_id else None

    if not assembly:
        raise HTTPException(
            status_code=404,
            detail=f"No assembly for edition {edition_id}. Run Phase 3 assembly first.",
        )

    articles = assembly.get("articles", [])
    if page is not None:
        articles = [a for a in articles if a.get("page_number") == page]

    return JSONResponse(content={
        "edition_id": edition_id,
        "total": len(articles),
        "articles": articles,
    })


# ── Phase 4: Jump detection and stitching endpoints ──


@router.post("/api/editions/{edition_id}/stitch")
async def trigger_stitching(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 4 jump detection and cross-page stitching.

    Requires Phase 3 assembly to be completed first.
    """
    from src.modules.extraction.stitch_jumps import stitch_edition

    try:
        result = stitch_edition(edition_id)
        status_code = 200 if result["success"] else 400
        return JSONResponse(content=result, status_code=status_code)
    except Exception as e:
        logger.error(f"Stitching failed for edition {edition_id}: {e}", exc_info=True)
        return JSONResponse(
            content={"success": False, "edition_id": edition_id, "error": str(e)},
            status_code=500,
        )


@router.get("/api/editions/{edition_id}/stitched")
async def get_stitched_articles(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get stitched articles for an edition."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.stitch_jumps import get_stitched

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    stitched = get_stitched(publisher_id, edition_id) if publisher_id else None

    if not stitched:
        raise HTTPException(
            status_code=404,
            detail=f"No stitched data for edition {edition_id}. Run Phase 4 stitching first.",
        )

    return JSONResponse(content={
        "edition_id": edition_id,
        "total_articles_before": stitched["total_articles_before"],
        "total_articles_after": stitched["total_articles_after"],
        "stitches": stitched["stitches"],
        "articles": stitched["articles"],
    })


# ── Phase 5-7: Normalize, DB write, Homepage endpoints ──


@router.post("/api/editions/{edition_id}/normalize")
async def trigger_normalization(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 5 text cleanup and normalization."""
    from src.modules.extraction.normalize import normalize_edition
    try:
        result = normalize_edition(edition_id)
        return JSONResponse(content=result, status_code=200 if result["success"] else 400)
    except Exception as e:
        logger.error(f"Normalization failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/editions/{edition_id}/write-db")
async def trigger_db_write(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 6 DB write of normalized content items."""
    from src.modules.extraction.publish import write_edition_to_db
    try:
        result = write_edition_to_db(edition_id)
        return JSONResponse(content=result, status_code=200 if result["success"] else 400)
    except Exception as e:
        logger.error(f"DB write failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/editions/{edition_id}/homepage-batch")
async def trigger_homepage_batch(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Trigger Phase 7 homepage batch generation."""
    from src.modules.extraction.publish import generate_homepage_batch
    try:
        result = generate_homepage_batch(edition_id)
        return JSONResponse(content=result, status_code=200 if result["success"] else 400)
    except Exception as e:
        logger.error(f"Homepage batch failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/editions/{edition_id}/full-pipeline")
async def trigger_full_pipeline(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Run the complete Phases 1-7 pipeline in one call."""
    from src.modules.extraction.publish import run_full_pipeline
    try:
        result = run_full_pipeline(edition_id)
        return JSONResponse(content=result, status_code=200 if result["success"] else 400)
    except Exception as e:
        logger.error(f"Full pipeline failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get("/api/editions/{edition_id}/content-items")
async def get_edition_content_items(
    edition_id: int,
    content_type: str = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get content items for an edition from the database."""
    from src.modules.content_items.database import get_content_items_for_edition
    items = get_content_items_for_edition(edition_id)
    if content_type:
        items = [i for i in items if i.get("content_type") == content_type]
    return JSONResponse(content={"edition_id": edition_id, "total": len(items), "items": items})


@router.get("/api/content-items/{item_id}")
async def get_single_content_item(
    item_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get a single content item by ID."""
    from src.modules.content_items.database import get_content_item
    item = get_content_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Content item {item_id} not found")
    return JSONResponse(content=item)


@router.get("/api/publishers/{publisher_id}/homepage")
async def get_publisher_homepage(
    publisher_id: int,
    limit: int = 20,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get homepage content for a publisher."""
    from src.modules.content_items.database import get_homepage_content
    items = get_homepage_content(publisher_id, limit=limit)
    return JSONResponse(content={
        "publisher_id": publisher_id,
        "total": len(items),
        "stories": items,
    })
