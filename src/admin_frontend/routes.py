"""Routes for HTML admin dashboard."""

import json
import logging
import os
import secrets
import shutil
import threading
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
    delete_jump_override,
    get_all_editions,
    get_edition_count,
    get_fragment_edits,
    get_jump_overrides,
    get_regions_for_article,
    get_review_actions_for_article,
    insert_jump_override,
    insert_review_action,
    upsert_fragment_edit,
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

# ── Background vision processing tracker ──
# Keyed by edition_id, stores progress for async vision uploads.
_vision_jobs: dict[int, dict] = {}
_vision_jobs_lock = threading.Lock()

# Security
security = HTTPBasic()

# Database browser constants
BROWSABLE_TABLES = [
    "articles",
    "advertisements",
    "content_items",
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


# Publisher slug → name mapping for scoped admin routes
_PUBLISHER_SLUGS = {
    "cottonwood": "Cottonwood County Citizen",
    "pipestone": "Pipestone Star",
}


# Page route
@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render admin dashboard page (network-wide)."""
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "publisher": "", "publisher_slug": ""})


def _publisher_context(request: Request, publisher_slug: str) -> dict:
    """Build template context for publisher-scoped pages."""
    pub_name = _PUBLISHER_SLUGS.get(publisher_slug, "")
    return {"request": request, "publisher": pub_name, "publisher_slug": publisher_slug}


# API routes
@router.get("/api/stats")
async def get_stats(
    publisher: str | None = None, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Get conversation statistics, optionally filtered by publisher."""
    try:
        stats = get_conversation_stats(publisher=publisher)
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return JSONResponse(
            content={"error": str(e), "total_conversations": 0, "total_messages": 0},
            status_code=200,
        )


@router.get("/api/queries")
async def get_queries(
    limit: int = 100, top_n: int = 20, publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get most common queries, optionally filtered by publisher."""
    conversations = get_all_conversations(limit=limit, publisher=publisher)
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
    limit: int = 100, top_n: int = 30, publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get most common words in queries, optionally filtered by publisher."""
    conversations = get_all_conversations(limit=limit, publisher=publisher)
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
    limit: int = 10, publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get recent conversations with details, optionally filtered by publisher."""
    conversations = get_all_conversations(limit=limit, publisher=publisher)
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


# ── Edition management endpoints ──


@router.delete("/api/editions/{edition_id}")
async def delete_edition(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Delete an edition and all associated data (articles, content_items, ChromaDB vectors)."""
    conn = get_connection()
    cursor = conn.cursor()

    # Check edition exists
    cursor.execute("SELECT id, source_filename FROM editions WHERE id = ?", (edition_id,))
    edition = cursor.fetchone()
    if not edition:
        conn.close()
        return JSONResponse(content={"success": False, "error": "Edition not found"}, status_code=404)

    deleted = {}

    # Cascade homepage_pins BEFORE content_items (the subquery needs the rows
    # to still exist). Otherwise pins would dangle with non-existent
    # content_item_ids — the exact bug Trevor hit tonight when 5-6 pastes
    # were wiped and the admin UI showed blank cards.
    cursor.execute(
        """
        DELETE FROM homepage_pins
        WHERE content_item_id IN (
            SELECT id FROM content_items WHERE edition_id = ?
        )
        """,
        (edition_id,),
    )
    deleted["homepage_pins"] = cursor.rowcount

    # Delete content_items
    cursor.execute("DELETE FROM content_items WHERE edition_id = ?", (edition_id,))
    deleted["content_items"] = cursor.rowcount

    # Delete articles
    cursor.execute("DELETE FROM articles WHERE edition_id = ?", (edition_id,))
    deleted["articles"] = cursor.rowcount

    # Delete page_regions
    cursor.execute("DELETE FROM page_regions WHERE edition_id = ?", (edition_id,))
    deleted["page_regions"] = cursor.rowcount

    # Delete review_actions for articles in this edition
    # (already deleted articles, so just clean up orphans)
    try:
        cursor.execute("DELETE FROM review_actions WHERE article_id NOT IN (SELECT doc_id FROM articles)")
        deleted["review_actions"] = cursor.rowcount
    except Exception:
        pass

    # Delete jump overrides and fragment edits
    try:
        cursor.execute("DELETE FROM jump_overrides WHERE edition_id = ?", (edition_id,))
    except Exception:
        pass
    try:
        cursor.execute("DELETE FROM fragment_edits WHERE edition_id = ?", (edition_id,))
    except Exception:
        pass

    # Delete advertisements tied to this edition (print classifieds inserted by
    # the IDML parser — see idml_parser.py:1108). Without this, clicking Delete
    # on an edition leaves the classifieds in the advertisements table where
    # the chatbot's ad search still returns them with publisher matching the
    # deleted edition.
    cursor.execute(
        "SELECT ad_id FROM advertisements WHERE edition_id = ?", (edition_id,)
    )
    ad_ids_for_edition = [row[0] for row in cursor.fetchall()]
    cursor.execute("DELETE FROM advertisements WHERE edition_id = ?", (edition_id,))
    deleted["advertisements"] = cursor.rowcount

    # Delete the edition itself
    cursor.execute("DELETE FROM editions WHERE id = ?", (edition_id,))
    deleted["editions"] = cursor.rowcount

    conn.commit()
    conn.close()

    # Delete ChromaDB vectors for this edition — BOTH collections.
    try:
        from src.modules.extraction.shared_write_layer import get_articles_collection
        collection = get_articles_collection()
        if collection and collection.count() > 0:
            results = collection.get(where={"edition_id": str(edition_id)})
            if results and results["ids"]:
                collection.delete(ids=results["ids"])
                deleted["vectors"] = len(results["ids"])
    except Exception as e:
        logger.warning(f"ChromaDB articles cleanup for edition {edition_id}: {e}")

    # Ads collection cleanup — classifieds from IDML aren't indexed into
    # Chroma today, but help-wanted and display ads tied to an edition are,
    # so clean defensively by ad_id.
    if ad_ids_for_edition:
        try:
            from src.core.vector_store import get_ads_collection
            ads_col = get_ads_collection()
            chunk_ids: list[str] = []
            for aid in ad_ids_for_edition:
                res = ads_col.get(where={"doc_id": aid})
                if res and res.get("ids"):
                    chunk_ids.extend(res["ids"])
            if chunk_ids:
                ads_col.delete(ids=chunk_ids)
                deleted["ad_vectors"] = len(chunk_ids)
        except Exception as e:
            logger.warning(f"ChromaDB ads cleanup for edition {edition_id}: {e}")

    logger.info(f"Deleted edition {edition_id}: {deleted}")
    return JSONResponse(content={"success": True, "deleted": deleted})


@router.get("/api/editions")
async def get_editions_list(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get all editions with their status, optionally filtered by publisher."""
    editions = get_all_editions(limit=50)
    if publisher:
        # Filter by publisher name → publisher_id
        from src.modules.publishers.database import get_publisher_by_name
        pub = get_publisher_by_name(publisher)
        if pub:
            editions = [e for e in editions if e.get("publisher_id") == pub["id"]]
        else:
            editions = []
    return JSONResponse(content={"editions": editions, "total": len(editions)})


@router.post("/api/editions/upload")
async def upload_editions(
    files: list[UploadFile] = File(...),
    publisher: str = Form(...),
    organization_name: str = Form(""),
    publication_name: str = Form(""),
    edition_date: str = Form(""),
    pipeline: str = Form("auto"),
    edition_mode: str = Form("auto"),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload and process newspaper editions (.pdf or .idml).

    Auto-detects file format and routes to the appropriate pipeline:
    - .idml → IDML parser (perfect text from InDesign source)
    - .pdf + pipeline=vision → Claude Vision extraction
    - .pdf + pipeline=v2 → V2 grid+cell claiming (legacy)
    - .pdf + pipeline=auto → defaults to V2
    """
    from src.modules.extraction.pipeline_v2 import run_v2_pipeline
    from src.modules.extraction.shared_write_layer import write_articles_to_all
    from src.modules.publishers.database import get_publisher_by_name
    from src.modules.publishers.uploads import upload_edition

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    pub_record = get_publisher_by_name(publisher)
    if not pub_record:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown publisher: '{publisher}'.",
        )
    publisher_id = pub_record["id"]

    # Convert edition_mode form value to tri-state force_current flag.
    # - "auto" (default): None → promote only if newest by edition_date
    # - "current": True → force as current (override date check)
    # - "historical": False → seed as historical (never promote)
    force_current: bool | None = None
    if edition_mode == "current":
        force_current = True
    elif edition_mode == "historical":
        force_current = False

    SUPPORTED_EXTENSIONS = (".pdf", ".idml")
    results = []

    for file in files:
        file_result = {
            "filename": file.filename,
            "pipeline": None,
            "articles": 0,
            "stitched": 0,
            "chunks_indexed": 0,
            "error": None,
        }

        if not file.filename:
            file_result["error"] = "No filename"
            results.append(file_result)
            continue

        ext = file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
        if f".{ext}" not in SUPPORTED_EXTENSIONS:
            file_result["error"] = f"Unsupported file type: .{ext} (use .pdf or .idml)"
            results.append(file_result)
            continue

        try:
            file_data = await file.read()

            # ── IDML path ──
            if ext == "idml":
                file_result["pipeline"] = "idml"
                import tempfile
                from src.modules.extraction.idml_parser import ingest_idml_edition

                with tempfile.NamedTemporaryFile(suffix=".idml", delete=False) as tmp:
                    tmp.write(file_data)
                    tmp_path = tmp.name

                idml_result = ingest_idml_edition(
                    idml_path=tmp_path,
                    publisher_name=publisher,
                    edition_date=edition_date or None,
                    force_current=force_current,
                )
                file_result["articles"] = idml_result["articles_inserted"]
                file_result["chunks_indexed"] = idml_result.get("chunks_indexed", 0)

                os.unlink(tmp_path)

            # ── PDF Vision path (async — returns immediately) ──
            elif ext == "pdf" and pipeline == "vision":
                file_result["pipeline"] = "vision"

                # Store PDF via tenant upload
                upload_result = upload_edition(
                    publisher_id=publisher_id, data=file_data,
                    filename=file.filename, edition_date=edition_date or None,
                )
                if upload_result.get("error") and not upload_result.get("edition_id"):
                    file_result["error"] = upload_result["error"]
                    results.append(file_result)
                    continue

                edition_id = upload_result["edition_id"]
                pdf_path = upload_result.get("pdf_path") or upload_result.get("file_path")

                # Initialize job tracker
                with _vision_jobs_lock:
                    _vision_jobs[edition_id] = {
                        "status": "processing",
                        "filename": file.filename,
                        "current_page": 0,
                        "total_pages": 0,
                        "articles": 0,
                        "stitched": 0,
                        "chunks_indexed": 0,
                        "error": None,
                    }

                # Launch background thread
                def _run_vision(eid, path, pub_id, pub_name, ed_date, fname, fc):
                    try:
                        from src.modules.extraction.pipeline_vision import run_vision_pipeline
                        from src.modules.extraction.shared_write_layer import write_articles_to_all as _write

                        def on_page(page_num, total, _data):
                            with _vision_jobs_lock:
                                if eid in _vision_jobs:
                                    _vision_jobs[eid]["current_page"] = page_num
                                    _vision_jobs[eid]["total_pages"] = total

                        vision_result = run_vision_pipeline(
                            pdf_path=path,
                            edition_id=eid,
                            publisher_id=pub_id,
                            on_page_complete=on_page,
                        )

                        if not vision_result["success"]:
                            with _vision_jobs_lock:
                                _vision_jobs[eid]["status"] = "failed"
                                _vision_jobs[eid]["error"] = vision_result.get("error", "Unknown error")
                            return

                        write_result = _write(
                            articles=vision_result["articles"],
                            edition_id=eid,
                            publisher_id=pub_id,
                            publisher_name=pub_name,
                            edition_date=ed_date or None,
                            source_filename=fname,
                            force_current=fc,
                        )

                        with _vision_jobs_lock:
                            _vision_jobs[eid]["status"] = "complete"
                            _vision_jobs[eid]["articles"] = write_result["articles_written"]
                            _vision_jobs[eid]["chunks_indexed"] = write_result["chunks_indexed"]
                            _vision_jobs[eid]["stitched"] = sum(
                                1 for a in vision_result["articles"] if a.get("is_stitched")
                            )

                        logger.info(f"Vision background job complete: edition={eid}, articles={write_result['articles_written']}")

                    except Exception as e:
                        logger.error(f"Vision background job failed: edition={eid}: {e}", exc_info=True)
                        with _vision_jobs_lock:
                            _vision_jobs[eid]["status"] = "failed"
                            _vision_jobs[eid]["error"] = str(e)

                thread = threading.Thread(
                    target=_run_vision,
                    args=(edition_id, pdf_path, publisher_id, publisher, edition_date, file.filename, force_current),
                    daemon=True,
                )
                thread.start()

                file_result["edition_id"] = edition_id
                file_result["async"] = True

            # ── PDF V2 path (default) ──
            else:
                file_result["pipeline"] = "v2_pdf"

                upload_result = upload_edition(
                    publisher_id=publisher_id, data=file_data,
                    filename=file.filename, edition_date=edition_date or None,
                )
                if upload_result.get("error") and not upload_result.get("edition_id"):
                    file_result["error"] = upload_result["error"]
                    results.append(file_result)
                    continue

                edition_id = upload_result["edition_id"]
                v2_result = run_v2_pipeline(edition_id)

                if not v2_result["success"]:
                    file_result["error"] = f"V2 pipeline failed: {v2_result.get('error')}"
                    results.append(file_result)
                    continue

                # Write to articles table + content_items + ChromaDB via shared layer.
                # NOTE: we no longer call write_edition_to_db() here — the shared
                # layer is the single writer for content_items (per its docstring).
                # Calling both caused duplicate rows (one stitched + one unstitched
                # copy of each article — see the duplicate-stitch bug fix).
                write_result = write_articles_to_all(
                    articles=v2_result["articles"],
                    edition_id=edition_id,
                    publisher_id=publisher_id,
                    publisher_name=publisher,
                    edition_date=edition_date or None,
                    source_filename=file.filename,
                    force_current=force_current,
                )
                file_result["articles"] = write_result["articles_written"]
                file_result["stitched"] = v2_result["stitched_count"]
                file_result["chunks_indexed"] = write_result["chunks_indexed"]

            logger.info(f"Edition processed via {file_result['pipeline']}: {file_result['articles']} articles")

        except Exception as e:
            logger.error(f"Edition upload failed for {file.filename}: {e}", exc_info=True)
            file_result["error"] = str(e)

        results.append(file_result)

    total_articles = sum(r.get("articles", 0) for r in results)
    failures = sum(1 for r in results if r.get("error"))

    return JSONResponse(content={
        "success": True,
        "files_processed": len([r for r in results if not r.get("error")]),
        "total_articles": total_articles,
        "failures": failures,
        "details": results,
    })


# ── Vision job status polling ──


@router.get("/api/editions/{edition_id}/vision-status")
async def vision_job_status(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Poll the status of a background vision processing job."""
    with _vision_jobs_lock:
        job = _vision_jobs.get(edition_id)
    if not job:
        return JSONResponse(content={"status": "not_found"}, status_code=404)
    return JSONResponse(content=job)


# ── Ad upload endpoints (Track 1) ──


@router.post("/api/ads/purge")
async def purge_ads(
    publisher: str = Form(""),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Purge non-directory ads for a publisher (weekly cleanup).

    Marks non-directory ads as 'expired' and removes them from ChromaDB.
    Directory ads (ad_type='directory') are exempt.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Get ad_ids to purge (non-directory, active, for this publisher)
    where = "status = 'active' AND (ad_type IS NULL OR ad_type != 'directory')"
    params: list = []
    if publisher:
        where += " AND publisher = ?"
        params.append(publisher)

    cur.execute(f"SELECT ad_id FROM advertisements WHERE {where}", params)
    ad_ids = [r[0] for r in cur.fetchall()]

    # Mark as expired
    cur.execute(f"UPDATE advertisements SET status = 'expired' WHERE {where}", params)
    purged_count = cur.rowcount
    conn.commit()
    conn.close()

    # Remove from ChromaDB
    vectors_removed = 0
    if ad_ids:
        try:
            from src.core.vector_store import get_ads_collection
            ads_col = get_ads_collection()
            # ChromaDB IDs are {ad_id}_{chunk_index}
            all_chunk_ids = []
            for ad_id in ad_ids:
                results = ads_col.get(where={"doc_id": ad_id})
                if results and results["ids"]:
                    all_chunk_ids.extend(results["ids"])
            if all_chunk_ids:
                ads_col.delete(ids=all_chunk_ids)
                vectors_removed = len(all_chunk_ids)
        except Exception as e:
            logger.warning(f"ChromaDB ad purge failed: {e}")

    logger.info(f"Ad purge: {purged_count} ads expired, {vectors_removed} vectors removed for publisher={publisher}")
    return JSONResponse(content={
        "success": True,
        "purged": purged_count,
        "vectors_removed": vectors_removed,
        "publisher": publisher,
    })


# ---------------------------------------------------------------------------
# Tier 1-3 ingestion endpoints
# ---------------------------------------------------------------------------

@router.get("/api/rss-feeds")
async def list_rss_feeds(
    publisher: str | None = None,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """List saved RSS feed configs."""
    from src.core.database import get_rss_feeds
    return JSONResponse(get_rss_feeds(publisher=publisher))


@router.post("/api/rss-feeds")
async def add_rss_feed(
    request: Request,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Save a new RSS feed config for a publisher."""
    from src.core.database import upsert_rss_feed
    body = await request.json()
    publisher = (body.get("publisher") or "").strip()
    rss_url = (body.get("rss_url") or "").strip()
    label = (body.get("label") or "").strip()
    if not publisher or not rss_url:
        raise HTTPException(status_code=400, detail="publisher and rss_url required")
    feed_id = upsert_rss_feed(publisher, rss_url, label)
    return JSONResponse({"id": feed_id, "publisher": publisher, "rss_url": rss_url})


@router.delete("/api/rss-feeds/{feed_id}")
async def remove_rss_feed(
    feed_id: int,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Delete a saved RSS feed config."""
    from src.core.database import delete_rss_feed
    delete_rss_feed(feed_id)
    return JSONResponse({"deleted": feed_id})


@router.post("/api/sync-rss/{feed_id}")
async def sync_rss_feed(
    feed_id: int,
    request: Request,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Pull articles from a saved RSS feed and ingest them."""
    from src.core.database import get_rss_feeds, mark_rss_synced
    from src.modules.ingestion.rss_ingestor import RSSIngestor
    from src.modules.extraction.shared_write_layer import write_articles

    feeds = get_rss_feeds()
    feed = next((f for f in feeds if f["id"] == feed_id), None)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    since = body.get("since")  # optional ISO date

    ingestor = RSSIngestor(feed["rss_url"])
    articles = ingestor.fetch(since=since)

    if not articles:
        return JSONResponse({"articles_written": 0, "message": "No new articles found"})

    result = write_articles(
        articles=articles,
        publisher_name=feed["publisher"],
        edition_date=articles[0].get("publish_date") or "",
        source_filename=f"rss:{feed['rss_url']}",
        mark_current=True,
    )
    mark_rss_synced(feed_id)

    # Rebuild FTS so new articles are lexically searchable
    try:
        from src.modules.articles.fts import rebuild_fts
        rebuild_fts()
    except Exception as e:
        logger.warning(f"FTS rebuild after RSS sync failed (non-fatal): {e}")

    return JSONResponse({
        "articles_written": result.get("articles_written", 0),
        "chunks_indexed": result.get("chunks_indexed", 0),
        "feed": feed["rss_url"],
        "publisher": feed["publisher"],
    })


@router.post("/api/import-urls")
async def import_urls(
    request: Request,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Tier 2: fetch and ingest articles from explicit URLs."""
    from src.modules.ingestion.url_ingestor import URLIngestor
    from src.modules.extraction.shared_write_layer import write_articles

    body = await request.json()
    publisher = (body.get("publisher") or "").strip()
    urls: list[str] = body.get("urls") or []
    edition_date = (body.get("edition_date") or "").strip() or None

    if not publisher:
        raise HTTPException(status_code=400, detail="publisher required")
    if not urls:
        raise HTTPException(status_code=400, detail="urls list required")

    ingestor = URLIngestor()
    articles = ingestor.fetch(urls)

    if not articles:
        return JSONResponse({"articles_written": 0, "message": "No articles extracted"})

    result = write_articles(
        articles=articles,
        publisher_name=publisher,
        edition_date=edition_date or (articles[0].get("publish_date") or ""),
        source_filename="url_import",
        mark_current=True,
    )

    try:
        from src.modules.articles.fts import rebuild_fts
        rebuild_fts()
    except Exception as e:
        logger.warning(f"FTS rebuild after URL import failed (non-fatal): {e}")

    return JSONResponse({
        "articles_written": result.get("articles_written", 0),
        "chunks_indexed": result.get("chunks_indexed", 0),
        "urls_submitted": len(urls),
    })


@router.post("/api/paste-article")
async def paste_article(
    request: Request,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Tier 3: ingest a single article from pasted text."""
    from src.modules.extraction.shared_write_layer import write_articles

    body = await request.json()
    publisher = (body.get("publisher") or "").strip()
    headline = (body.get("headline") or "").strip()
    body_text = (body.get("body_text") or "").strip()
    author = (body.get("author") or "").strip()
    publish_date = (body.get("publish_date") or "").strip()
    section = (body.get("section") or "news").strip()

    if not publisher:
        raise HTTPException(status_code=400, detail="publisher required")
    if not headline:
        raise HTTPException(status_code=400, detail="headline required")
    if not body_text or len(body_text) < 50:
        raise HTTPException(status_code=400, detail="body_text too short (min 50 chars)")

    articles = [{
        "headline": headline,
        "body_text": body_text,
        "byline": author,
        "publish_date": publish_date,
        "url": "",
        "content_type": section,
        "source_pipeline": "paste",
        "extraction_confidence": 1.0,
        "is_stitched": False,
        "jump_pages": [],
        "start_page": None,
    }]

    result = write_articles(
        articles=articles,
        publisher_name=publisher,
        edition_date=publish_date or "",
        source_filename="paste_form",
        mark_current=True,
    )

    try:
        from src.modules.articles.fts import rebuild_fts
        rebuild_fts()
    except Exception as e:
        logger.warning(f"FTS rebuild after paste failed (non-fatal): {e}")

    return JSONResponse({
        "articles_written": result.get("articles_written", 0),
        "chunks_indexed": result.get("chunks_indexed", 0),
    })


@router.post("/api/upload-article-file")
async def upload_article_file(
    file: UploadFile = File(...),
    publisher: str = Form(""),
    publish_date: str = Form(""),
    section: str = Form("news"),
    author: str = Form(""),
    headline_override: str = Form(""),
    dry_run: str = Form("false"),
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload a single .rtf or .txt file as one article.

    When dry_run=true, parses the file and returns {headline, body}
    without writing — used by the paste-form file-picker to prefill inputs.
    """
    from striprtf.striprtf import rtf_to_text as strip_rtf
    from src.modules.extraction.shared_write_layer import write_articles

    # Validate file extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".rtf", ".txt"}:
        raise HTTPException(status_code=400, detail="Only .rtf and .txt files accepted")

    raw = await file.read()

    # Decode file contents
    if ext == ".rtf":
        try:
            text = strip_rtf(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"RTF parsing failed: {e}")
    else:
        # .txt — try utf-8, fall back to cp1252 (Windows exports)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")

    # Parse: first non-empty line = headline, rest = body
    lines = text.strip().splitlines()
    headline = ""
    body = ""
    for i, line in enumerate(lines):
        if line.strip():
            headline = line.strip()
            body = "\n".join(lines[i + 1:]).strip()
            break

    # Allow headline override from the form
    if headline_override.strip():
        headline = headline_override.strip()

    is_dry_run = dry_run.lower() in ("true", "1", "yes")
    if is_dry_run:
        return JSONResponse({"headline": headline, "body": body})

    # Validate required fields for actual write
    if not publisher.strip():
        raise HTTPException(status_code=400, detail="publisher required")
    if not headline:
        raise HTTPException(status_code=400, detail="No headline found (file may be empty)")
    if len(body) < 50:
        raise HTTPException(status_code=400, detail="Article body too short (min 50 chars)")

    articles = [{
        "headline": headline,
        "body_text": body,
        "byline": author.strip(),
        "publish_date": publish_date.strip(),
        "url": "",
        "content_type": section.strip() or "news",
        "source_pipeline": "file_upload",
        "extraction_confidence": 1.0,
        "is_stitched": False,
        "jump_pages": [],
        "start_page": None,
    }]

    result = write_articles(
        articles=articles,
        publisher_name=publisher.strip(),
        edition_date=publish_date.strip() or "",
        source_filename=f"upload:{file.filename}",
        mark_current=True,
    )

    try:
        from src.modules.articles.fts import rebuild_fts
        rebuild_fts()
    except Exception as e:
        logger.warning(f"FTS rebuild after file upload failed (non-fatal): {e}")

    return JSONResponse({
        "articles_written": result.get("articles_written", 0),
        "chunks_indexed": result.get("chunks_indexed", 0),
        "filename": file.filename,
    })


# ── Homepage Pins (editor-curated homepage slots) ──────────────────────────
# These four endpoints back the "Homepage Layout" tab.
#
# Design decisions (confirmed with user):
#   • Drag-and-drop into exactly 4 slots per section (not a checkbox list).
#   • Strict mode: pinned = exactly the homepage for News & Sports. If a
#     publisher has NO pins in a section, the section goes empty on / —
#     auto-scoring does NOT fill the gap. Trevor wants total editorial
#     control, not a "smart fallback" that surprises him.
#   • Per-publisher scoping — the homepage_pins table uses publisher_id,
#     which is what get_homepage_content() already reads.
#   • Only News and Sports are wired for now; other sections fall through
#     to auto-scoring. (See content_items.database.get_homepage_content.)

_PIN_SECTIONS = {"news", "sports"}
_PIN_SLOTS = {1, 2, 3, 4}


def _resolve_publisher_id(publisher_name: str) -> int:
    """Resolve publisher name → id or raise 404."""
    from src.modules.publishers.database import get_publisher_by_name
    pub = get_publisher_by_name(publisher_name)
    if not pub:
        raise HTTPException(status_code=404, detail=f"Unknown publisher: {publisher_name}")
    return int(pub["id"])


@router.get("/api/homepage-pins")
async def list_homepage_pins(
    publisher: str,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Return all pins for a publisher across both sections.

    Shape: {"publisher": "...", "publisher_id": 1, "pins": {"news": [<=4], "sports": [<=4]}}
    Each pin carries enough fields to render its card in the UI without
    a second round-trip: headline, byline, edition_date, content_item_id, slot.
    """
    from src.core.database import get_homepage_pins
    publisher_id = _resolve_publisher_id(publisher)
    raw = get_homepage_pins(publisher_id)

    pins: dict[str, list[dict]] = {"news": [], "sports": []}
    for row in raw:
        section = (row.get("section") or "").lower()
        if section not in pins:
            continue
        pins[section].append({
            "slot": row["slot"],
            "content_item_id": row["content_item_id"],
            "headline": row.get("headline") or "",
            "byline": row.get("byline") or "",
            "edition_date": row.get("edition_date") or "",
            "content_type": row.get("content_type") or "",
        })
    # Sort each section by slot so the UI can render slot 1..4 in order
    for section in pins:
        pins[section].sort(key=lambda p: p["slot"])

    return JSONResponse({
        "publisher": publisher,
        "publisher_id": publisher_id,
        "pins": pins,
    })


@router.get("/api/homepage-pins/candidates")
async def list_pin_candidates(
    publisher: str,
    section: str = "news",
    limit: int = 50,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Return content_items eligible to be pinned for this publisher+section.

    Pulls from the CURRENT edition first (is_current=1). If no current edition
    exists, falls back to all published content for that publisher+section.
    This mirrors what get_homepage_content() does when there are no pins —
    the editor should see the same pool they'd otherwise auto-rank from.
    """
    section = (section or "news").lower()
    if section not in _PIN_SECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"section must be one of {sorted(_PIN_SECTIONS)}",
        )
    publisher_id = _resolve_publisher_id(publisher)

    conn = get_connection()
    cursor = conn.cursor()

    def _fetch(current_only: bool) -> list[dict]:
        where = [
            "ci.publisher_id = ?",
            "ci.content_type = ?",
            "ci.publish_status = 'published'",
        ]
        params: list = [publisher_id, section]
        if current_only:
            where.append("e.is_current = 1")
        cursor.execute(f"""
            SELECT ci.id, ci.headline, ci.byline, ci.edition_date,
                   ci.content_type, ci.start_page, ci.homepage_score,
                   ci.edition_id, e.is_current
            FROM content_items ci
            JOIN editions e ON ci.edition_id = e.id
            WHERE {" AND ".join(where)}
            ORDER BY ci.homepage_score DESC, ci.id DESC
            LIMIT ?
        """, params + [limit])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    rows = _fetch(current_only=True)
    if not rows:
        rows = _fetch(current_only=False)
    conn.close()

    return JSONResponse({
        "publisher": publisher,
        "section": section,
        "count": len(rows),
        "candidates": rows,
    })


@router.put("/api/homepage-pins")
async def put_homepage_pin(
    request: Request,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Set or replace the pin at (publisher, section, slot).

    Body JSON: {publisher, section, slot: 1-4, content_item_id}
    Idempotent — re-PUT overwrites the slot.
    """
    from src.core.database import upsert_homepage_pin
    body = await request.json()
    publisher = (body.get("publisher") or "").strip()
    section = (body.get("section") or "").strip().lower()
    slot = body.get("slot")
    content_item_id = body.get("content_item_id")

    if not publisher:
        raise HTTPException(status_code=400, detail="publisher required")
    if section not in _PIN_SECTIONS:
        raise HTTPException(status_code=400, detail=f"section must be one of {sorted(_PIN_SECTIONS)}")
    if slot not in _PIN_SLOTS:
        raise HTTPException(status_code=400, detail=f"slot must be one of {sorted(_PIN_SLOTS)}")
    if not isinstance(content_item_id, int):
        raise HTTPException(status_code=400, detail="content_item_id must be an integer")

    publisher_id = _resolve_publisher_id(publisher)

    # Sanity-check: the content item must belong to this publisher to prevent
    # cross-publisher pinning. Catching this at the edge is cheaper than
    # debugging a mystery homepage later.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT publisher_id FROM content_items WHERE id = ?",
        (content_item_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="content_item not found")
    if int(row[0]) != publisher_id:
        raise HTTPException(
            status_code=400,
            detail="content_item belongs to a different publisher",
        )

    pin_id = upsert_homepage_pin(publisher_id, section, slot, content_item_id)
    logger.info(
        f"Pin set: publisher_id={publisher_id} section={section} "
        f"slot={slot} -> content_item_id={content_item_id} (pin_id={pin_id})"
    )
    return JSONResponse({"pin_id": pin_id, "ok": True})


@router.delete("/api/homepage-pins")
async def clear_homepage_pin(
    publisher: str,
    section: str,
    slot: int,
    _: str = Depends(verify_credentials),
) -> JSONResponse:
    """Clear a single pin. Idempotent — clearing an empty slot is a no-op."""
    from src.core.database import delete_homepage_pin
    section = (section or "").strip().lower()
    if section not in _PIN_SECTIONS:
        raise HTTPException(status_code=400, detail=f"section must be one of {sorted(_PIN_SECTIONS)}")
    if slot not in _PIN_SLOTS:
        raise HTTPException(status_code=400, detail=f"slot must be one of {sorted(_PIN_SLOTS)}")
    publisher_id = _resolve_publisher_id(publisher)
    delete_homepage_pin(publisher_id, section, slot)
    logger.info(f"Pin cleared: publisher_id={publisher_id} section={section} slot={slot}")
    return JSONResponse({"ok": True})


@router.post("/api/ads/upload")
async def upload_ads(
    files: list[UploadFile] = File(...),
    publisher: str = Form(...),
    organization_name: str = Form(""),
    publication_name: str = Form(""),
    ad_type: str = Form(""),
    clear_previous: str = Form(""),
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Upload individual ad PDFs with checksum dedup."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # If clear_previous is checked, purge non-directory ads first
    purge_result = None
    if clear_previous == "true":
        conn = get_connection()
        cur = conn.cursor()
        where = "status = 'active' AND (ad_type IS NULL OR ad_type != 'directory')"
        params_purge: list = []
        if publisher:
            where += " AND publisher = ?"
            params_purge.append(publisher)
        cur.execute(f"SELECT ad_id FROM advertisements WHERE {where}", params_purge)
        ad_ids = [r[0] for r in cur.fetchall()]
        cur.execute(f"UPDATE advertisements SET status = 'expired' WHERE {where}", params_purge)
        purge_count = cur.rowcount
        conn.commit()
        conn.close()
        # Remove from ChromaDB
        if ad_ids:
            try:
                from src.core.vector_store import get_ads_collection
                ads_col = get_ads_collection()
                all_chunk_ids = []
                for ad_id in ad_ids:
                    results = ads_col.get(where={"doc_id": ad_id})
                    if results and results["ids"]:
                        all_chunk_ids.extend(results["ids"])
                if all_chunk_ids:
                    ads_col.delete(ids=all_chunk_ids)
            except Exception as e:
                logger.warning(f"ChromaDB purge during upload failed: {e}")
        purge_result = purge_count
        logger.info(f"Purged {purge_count} previous ads for {publisher} before upload")

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
                ad_type=ad_type or None,
            )
        else:
            result = ingester.ingest_ad_bytes(
                data=data,
                filename=file.filename,
                organization_id=organization_id,
                publication_id=publication_id,
                publisher=publisher,
                ad_type=ad_type or None,
            )
        results.append(result)

    ingested = sum(1 for r in results if r.get("ad_id") and not r.get("error"))
    duplicates = sum(1 for r in results if r.get("duplicate"))
    failures = sum(1 for r in results if r.get("error") and not r.get("duplicate"))
    warnings = sum(1 for r in results if r.get("warning"))

    # Trigger background business directory enrichment for new entries
    if ingested > 0:
        def _run_enrichment():
            try:
                from src.modules.directory.enrichment import enrich_pending_businesses
                enrich_result = enrich_pending_businesses()
                logger.info(f"Background enrichment: {enrich_result}")
            except Exception as e:
                logger.error(f"Background enrichment failed: {e}")

        enrichment_thread = threading.Thread(target=_run_enrichment, daemon=True)
        enrichment_thread.start()

    return JSONResponse(content={
        "success": True,
        "files_received": len(files),
        "ingested": ingested,
        "duplicates_rejected": duplicates,
        "failures": failures,
        "indexing_warnings": warnings,
        "purged_previous": purge_result,
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
    publisher: str | None = None,
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

    # Filter by publisher if specified
    if publisher and articles:
        articles = [a for a in articles if a.get("publisher") == publisher]

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
        full_text=body.get("full_text"),
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


# ── Advertisement editing ──


@router.get("/api/advertisements/{ad_id}")
async def get_single_advertisement(
    ad_id: str,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get a single advertisement by ID."""
    from src.modules.advertisements.database import get_advertisement_by_id
    ad = get_advertisement_by_id(ad_id)
    if not ad:
        raise HTTPException(status_code=404, detail=f"Advertisement {ad_id} not found")
    return JSONResponse(content=ad)


@router.put("/api/advertisements/{ad_id}")
async def edit_advertisement(
    ad_id: str,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Edit advertisement fields."""
    from src.modules.advertisements.database import get_advertisement_by_id, update_advertisement
    ad = get_advertisement_by_id(ad_id)
    if not ad:
        raise HTTPException(status_code=404, detail="Advertisement not found")

    body = await request.json()
    update_advertisement(
        ad_id=ad_id,
        product_name=body.get("product_name"),
        advertiser=body.get("advertiser"),
        description=body.get("description"),
        category=body.get("category"),
        price=body.get("price"),
        raw_text=body.get("raw_text"),
        cleaned_text=body.get("cleaned_text"),
        status=body.get("status"),
    )
    return JSONResponse(content={"success": True})


@router.delete("/api/advertisements/{ad_id}")
async def delete_advertisement(
    ad_id: str,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Delete a single advertisement and its ChromaDB vectors."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ad_id FROM advertisements WHERE ad_id = ?", (ad_id,))
    if not cursor.fetchone():
        conn.close()
        return JSONResponse(content={"success": False, "error": "Ad not found"}, status_code=404)

    cursor.execute("DELETE FROM advertisements WHERE ad_id = ?", (ad_id,))
    conn.commit()
    conn.close()

    # Remove from ChromaDB ads collection
    try:
        from src.modules.extraction.shared_write_layer import get_ads_collection
        ads_col = get_ads_collection()
        if ads_col:
            results = ads_col.get(where={"doc_id": ad_id})
            if results and results["ids"]:
                ads_col.delete(ids=results["ids"])
    except Exception as e:
        logger.warning(f"ChromaDB ad cleanup for {ad_id}: {e}")

    return JSONResponse(content={"success": True})


# ── Review page ──


@router.get("/review", response_class=HTMLResponse)
async def review_page(
    request: Request, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render the article review page."""
    return templates.TemplateResponse(request=request, name="review.html")


# ── API Costs Admin ──


@router.get("/costs", response_class=HTMLResponse)
async def costs_admin(
    request: Request,
    _username: str = Depends(verify_credentials),
) -> HTMLResponse:
    """Render the API costs dashboard (network-wide)."""
    return templates.TemplateResponse(request=request, name="costs.html", context={"request": request, "publisher": "", "publisher_slug": ""})


@router.get("/api/costs/summary")
async def costs_summary(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get cost summary grouped by provider and purpose, optionally filtered by publisher."""
    from src.modules.costs.tracker import get_cost_summary
    return JSONResponse(content=get_cost_summary(publisher=publisher))


@router.get("/api/costs/history")
async def costs_history(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get daily cost history for the last 30 days, optionally filtered by publisher."""
    from src.modules.costs.tracker import get_cost_history
    return JSONResponse(content={"history": get_cost_history(30, publisher=publisher)})


# ── Main Street OS Admin (business console onboarding) ──


@router.get("/main-street", response_class=HTMLResponse)
async def main_street_admin(
    request: Request, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render the Main Street OS admin page (network-wide view)."""
    return templates.TemplateResponse(
        request=request,
        name="main_street.html",
        context={"request": request, "publisher": "", "publisher_slug": ""},
    )


@router.post("/api/main-street/invite")
async def create_main_street_invite(
    request: Request, _username: str = Depends(verify_credentials)
) -> JSONResponse:
    """Create an invite link for a business. Publisher is passed from the
    scoped admin page (derived from URL slug client-side)."""
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
            content={"success": False, "error": "Publisher is required (open this page from a publisher-scoped URL)"},
            status_code=400,
        )
    if tier not in ("growth", "premium"):
        return JSONResponse(
            content={"success": False, "error": "Tier must be growth or premium"},
            status_code=400,
        )

    code = create_invite(business_name=business_name, publisher=publisher, tier=tier, note=note)
    base = str(request.base_url).rstrip("/")
    link = f"{base}/business/register?invite={code}"
    return JSONResponse(content={"success": True, "code": code, "link": link})


@router.get("/api/main-street/invites")
async def list_main_street_invites(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List invites, optionally filtered to a single publisher."""
    from src.business_frontend.auth import get_invites_for_publisher

    return JSONResponse(content={"invites": get_invites_for_publisher(publisher)})


@router.get("/api/main-street/businesses")
async def list_enrolled_businesses(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List enrolled Main Street OS businesses, optionally filtered by publisher."""
    conn = get_connection()
    cursor = conn.cursor()
    if publisher:
        cursor.execute(
            """
            SELECT bu.id as user_id, bu.email, bu.name as owner_name,
                   bu.last_login, bu.created_at as enrolled_at,
                   o.id as org_id, o.name as business_name, o.tier,
                   o.city, o.state, o.phone, o.publisher
            FROM business_users bu
            JOIN organizations o ON bu.organization_id = o.id
            WHERE bu.is_active = 1 AND o.publisher = ?
            ORDER BY bu.created_at DESC
            """,
            (publisher,),
        )
    else:
        cursor.execute(
            """
            SELECT bu.id as user_id, bu.email, bu.name as owner_name,
                   bu.last_login, bu.created_at as enrolled_at,
                   o.id as org_id, o.name as business_name, o.tier,
                   o.city, o.state, o.phone, o.publisher
            FROM business_users bu
            JOIN organizations o ON bu.organization_id = o.id
            WHERE bu.is_active = 1
            ORDER BY bu.created_at DESC
            """
        )
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return JSONResponse(content={"businesses": rows})


# ── Business Directory Admin ──


@router.get("/directory", response_class=HTMLResponse)
async def directory_admin(
    request: Request,
    _username: str = Depends(verify_credentials),
) -> HTMLResponse:
    """Render the business directory admin page (network-wide)."""
    return templates.TemplateResponse(request=request, name="directory.html", context={"request": request, "publisher": "", "publisher_slug": ""})


@router.get("/api/directory")
async def list_directory_admin(
    publisher: str | None = None,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List all business directory entries for admin, optionally filtered by publisher."""
    conn = get_connection()
    cursor = conn.cursor()
    # Ensure enrichment_error column exists (safe migration)
    cursor.execute("PRAGMA table_info(organizations)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "enrichment_error" not in existing_cols:
        cursor.execute("ALTER TABLE organizations ADD COLUMN enrichment_error TEXT")
        conn.commit()

    if publisher:
        cursor.execute(
            """SELECT id, name, slug, address, city, state, phone, email, website,
               category, description, services, keywords, hours_json, social_json,
               publisher, enrichment_status, last_enriched_at, last_advertised_at,
               enrichment_error, created_at, updated_at
            FROM organizations WHERE publisher = ? ORDER BY last_advertised_at DESC NULLS LAST""",
            (publisher,),
        )
    else:
        cursor.execute(
            """SELECT id, name, slug, address, city, state, phone, email, website,
               category, description, services, keywords, hours_json, social_json,
               publisher, enrichment_status, last_enriched_at, last_advertised_at,
               enrichment_error, created_at, updated_at
            FROM organizations ORDER BY last_advertised_at DESC NULLS LAST"""
        )
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return JSONResponse(content={"businesses": rows})


@router.put("/api/directory/{org_id}")
async def update_directory_entry(
    org_id: int,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Update a business directory entry."""
    data = await request.json()

    # Allowed fields to update
    allowed = {
        "name", "address", "city", "state", "phone", "email", "website",
        "category", "description", "services", "keywords", "hours_json",
        "social_json", "publisher",
    }
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return JSONResponse(content={"success": False, "error": "No valid fields to update"})

    conn = get_connection()
    cursor = conn.cursor()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [org_id]

    cursor.execute(
        f"UPDATE organizations SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        params,
    )
    conn.commit()

    # Also update slug if name changed
    if "name" in updates:
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", updates["name"].lower()).strip("-")
        cursor.execute("UPDATE organizations SET slug = ? WHERE id = ?", (slug, org_id))
        conn.commit()

    conn.close()
    logger.info(f"Directory entry {org_id} updated: {list(updates.keys())}")
    return JSONResponse(content={"success": True})


@router.post("/api/directory/{org_id}/enrich")
async def enrich_directory_entry(
    org_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Re-trigger enrichment for a single business."""
    try:
        # Reset status to pending first
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE organizations SET enrichment_status = 'pending' WHERE id = ?",
            (org_id,),
        )
        cursor.execute("SELECT name FROM organizations WHERE id = ?", (org_id,))
        row = cursor.fetchone()
        name = row[0] if row else "Unknown"
        conn.commit()
        conn.close()

        from src.modules.directory.enrichment import enrich_business
        success = enrich_business(org_id)

        return JSONResponse(content={
            "success": success,
            "name": name,
        })
    except Exception as e:
        logger.error(f"Enrichment failed for org {org_id}: {e}")
        return JSONResponse(content={"success": False, "error": str(e)})


@router.post("/api/directory/enrich-all")
async def enrich_all_pending(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Enrich all businesses with pending status."""
    try:
        from src.modules.directory.enrichment import enrich_pending_businesses
        result = enrich_pending_businesses()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Bulk enrichment failed: {e}")
        return JSONResponse(content={"success": False, "error": str(e)})


@router.post("/api/directory/retry-failed")
async def retry_failed_enrichments(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Reset all failed businesses to pending and re-enrich them."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE organizations SET enrichment_status = 'pending', enrichment_error = NULL WHERE enrichment_status = 'failed'"
        )
        reset_count = cursor.rowcount
        conn.commit()
        conn.close()

        if reset_count == 0:
            return JSONResponse(content={"total": 0, "enriched": 0, "failed": 0})

        from src.modules.directory.enrichment import enrich_pending_businesses
        result = enrich_pending_businesses()
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Retry failed enrichments error: {e}")
        return JSONResponse(content={"success": False, "error": str(e)})


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


@router.post("/api/editions/{edition_id}/full-pipeline-v2")
async def trigger_full_pipeline_v2(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Run the V2 pipeline (Phases 1-5) + DB write (Phase 6) + Homepage (Phase 7).

    Uses the new cell-claiming + bipartite jump matching architecture with
    multi-column continuation merging.
    """
    from src.modules.extraction.pipeline_v2 import run_v2_pipeline
    from src.modules.extraction.publish import write_edition_to_db, generate_homepage_batch
    try:
        # Run V2 pipeline (writes normalized.json)
        result = run_v2_pipeline(edition_id)
        if not result["success"]:
            return JSONResponse(content=result, status_code=400)

        # DB write (reads from normalized.json)
        db_result = write_edition_to_db(edition_id)
        if not db_result["success"]:
            return JSONResponse(content={"success": False, "error": db_result.get("error")}, status_code=400)

        # Homepage batch
        hp_result = generate_homepage_batch(edition_id)

        return JSONResponse(content={
            "success": True,
            "edition_id": edition_id,
            "article_count": result["article_count"],
            "stitched_count": result["stitched_count"],
            "items_written": db_result["items_written"],
            "homepage_published": hp_result.get("published", 0),
        })
    except Exception as e:
        logger.error(f"V2 full pipeline failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/editions/{edition_id}/v2-sync-articles")
async def v2_sync_articles_to_legacy(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Run V2 pipeline and sync results into the legacy articles table.

    This bridges V2 extraction into the existing articles table so the
    chatbot, landing page, and article detail page all show V2-quality text.
    Matches by headline (case-insensitive) and updates cleaned_text + full_text.
    """
    from src.modules.extraction.pipeline_v2 import run_v2_pipeline
    try:
        # Run V2 pipeline
        result = run_v2_pipeline(edition_id)
        if not result["success"]:
            return JSONResponse(content=result, status_code=400)

        # Build V2 headline → body map
        v2_map = {}
        for art in result["articles"]:
            hl = (art.get("headline") or "").strip().lower()
            if hl:
                v2_map[hl] = art.get("body_text", "")

        # Update legacy articles table
        from src.modules.articles.database import get_articles_for_edition, update_article
        legacy = get_articles_for_edition(edition_id)
        updated = 0
        for art in legacy:
            title_key = (art.get("title") or "").strip().lower()
            if title_key in v2_map and v2_map[title_key]:
                update_article(
                    doc_id=art["doc_id"],
                    cleaned_text=v2_map[title_key],
                    full_text=v2_map[title_key],
                )
                updated += 1

        return JSONResponse(content={
            "success": True,
            "edition_id": edition_id,
            "v2_articles": result["article_count"],
            "v2_stitched": result["stitched_count"],
            "legacy_matched": updated,
            "legacy_total": len(legacy),
        })
    except Exception as e:
        logger.error(f"V2 sync failed: {e}", exc_info=True)
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


# ── Jump Review endpoints ──


@router.get("/editions/{edition_id}/jumps", response_class=HTMLResponse)
async def jump_review_page(
    edition_id: int,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> HTMLResponse:
    """Render the jump review page for an edition."""
    from src.modules.editions.database import get_edition
    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")
    return templates.TemplateResponse(
        request=request, name="jump_review.html",
        context={"edition_id": edition_id, "edition": edition},
    )


@router.get("/api/editions/{edition_id}/jump-review")
async def get_jump_review_data(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Get the jump review artifact (fragments, edges, unmatched items)."""
    from src.modules.editions.database import get_edition
    from src.modules.extraction.extract_pages import ARTIFACTS_BASE

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        raise HTTPException(status_code=400, detail="Edition has no publisher_id")

    artifact_path = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}" / "jump_review.json"
    if not artifact_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Jump review data not available. Re-run the V2 pipeline first.",
        )

    with open(artifact_path, encoding="utf-8") as f:
        data = json.load(f)

    # Merge in any manual overrides and fragment edits from the database
    overrides = get_jump_overrides(edition_id)
    data["overrides"] = overrides

    edits = get_fragment_edits(edition_id)
    data["fragment_edits"] = {fid: {"headline": e.get("edited_headline"), "body_text": e.get("edited_body_text")} for fid, e in edits.items()}

    return JSONResponse(content=data)


@router.get("/api/editions/{edition_id}/pages/{page_number}/image")
async def get_page_image(
    edition_id: int,
    page_number: int,
    dpi: int = 150,
    _username: str = Depends(verify_credentials),
):
    """Render a PDF page as a PNG image for visual review."""
    import fitz
    from fastapi.responses import Response

    from src.modules.editions.database import get_edition

    edition = get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail=f"Edition {edition_id} not found")

    pdf_path = edition.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF file not found")

    try:
        doc = fitz.open(pdf_path)
        if page_number < 1 or page_number > len(doc):
            doc.close()
            raise HTTPException(status_code=404, detail=f"Page {page_number} out of range")

        page = doc[page_number - 1]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        png_data = pix.tobytes("png")
        doc.close()

        return Response(content=png_data, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Page image rendering failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/editions/{edition_id}/jump-overrides")
async def list_jump_overrides(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """List all manual jump overrides for an edition."""
    overrides = get_jump_overrides(edition_id)
    return JSONResponse(content={"overrides": overrides})


@router.post("/api/editions/{edition_id}/jump-overrides")
async def create_jump_override(
    edition_id: int,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Create a manual jump override (force_match or force_unlink)."""
    body = await request.json()
    action = body.get("action")
    if action not in ("force_match", "force_unlink"):
        raise HTTPException(status_code=400, detail="action must be 'force_match' or 'force_unlink'")

    override_id = insert_jump_override(
        edition_id=edition_id,
        action=action,
        src_page=body.get("src_page", 0),
        src_fragment_id=body.get("src_fragment_id", ""),
        dst_page=body.get("dst_page", 0),
        dst_fragment_id=body.get("dst_fragment_id", ""),
        reason=body.get("reason"),
    )
    return JSONResponse(content={"success": True, "override_id": override_id})


@router.delete("/api/editions/{edition_id}/jump-overrides/{override_id}")
async def remove_jump_override(
    edition_id: int,
    override_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Delete a manual jump override."""
    deleted = delete_jump_override(override_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Override not found")
    return JSONResponse(content={"success": True})


@router.put("/api/editions/{edition_id}/fragments/{fragment_id}")
async def save_fragment_edit(
    edition_id: int,
    fragment_id: str,
    request: Request,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Save edited headline or body text for a fragment.

    Edits are stored in the database and applied during pipeline re-stitch,
    so the corrected text flows through normalization into the final articles.
    """
    body = await request.json()
    edited_headline = body.get("headline")
    edited_body_text = body.get("body_text")

    if edited_headline is None and edited_body_text is None:
        raise HTTPException(status_code=400, detail="Provide headline or body_text to save")

    edit_id = upsert_fragment_edit(
        edition_id=edition_id,
        fragment_id=fragment_id,
        edited_headline=edited_headline,
        edited_body_text=edited_body_text,
    )
    return JSONResponse(content={"success": True, "edit_id": edit_id})


@router.post("/api/editions/{edition_id}/restitch")
async def restitch_edition(
    edition_id: int,
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Re-run the V2 pipeline with manual jump overrides applied.

    Re-runs Phases 3-5 (cell claiming, jump matching with overrides,
    normalization) then rewrites to the database and re-indexes in ChromaDB.
    """
    from src.modules.extraction.pipeline_v2 import run_v2_pipeline

    try:
        result = run_v2_pipeline(edition_id)
        if not result["success"]:
            return JSONResponse(content=result, status_code=400)

        return JSONResponse(content={
            "success": True,
            "edition_id": edition_id,
            "article_count": result["article_count"],
            "stitched_count": result["stitched_count"],
        })
    except Exception as e:
        logger.error(f"Restitch failed for edition {edition_id}: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post("/api/reset-data")
async def reset_data(
    _username: str = Depends(verify_credentials),
) -> JSONResponse:
    """Clear all articles, content_items, editions, and ChromaDB vectors.

    Keeps publishers, schema, and all code intact. Use this to start fresh
    before re-ingesting editions through the new pipeline.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Clear data tables
        cur.execute("DELETE FROM articles")
        articles_deleted = cur.rowcount
        # Clear homepage_pins BEFORE content_items so this is symmetric with
        # delete_edition(). Otherwise a reset leaves orphan pin rows that
        # point at content_item_ids that no longer exist.
        cur.execute("DELETE FROM homepage_pins")
        pins_deleted = cur.rowcount
        cur.execute("DELETE FROM content_items")
        content_items_deleted = cur.rowcount
        cur.execute("DELETE FROM editions")
        editions_deleted = cur.rowcount
        cur.execute("DELETE FROM advertisements")
        ads_deleted = cur.rowcount
        cur.execute("DELETE FROM conversations")
        cur.execute("DELETE FROM conversation_messages")
        cur.execute("DELETE FROM content_impressions")
        cur.execute("DELETE FROM url_clicks")
        cur.execute("DELETE FROM page_regions")
        cur.execute("DELETE FROM review_actions")

        # Reset auto-increment sequences for cleared tables
        cur.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('articles','content_items','editions','conversations','conversation_messages','advertisements')"
        )

        conn.commit()
        conn.close()

        # Clear ads ChromaDB collection
        ads_vectors_deleted = 0
        try:
            from src.core.vector_store import get_ads_collection
            ads_col = get_ads_collection()
            ads_count = ads_col.count()
            if ads_count > 0:
                ads_results = ads_col.get()
                ads_col.delete(ids=ads_results["ids"])
                ads_vectors_deleted = ads_count
        except Exception as e:
            logger.warning(f"Ads ChromaDB clear failed: {e}")

        # Clear articles ChromaDB vectors
        vectors_deleted = 0
        try:
            from src.core.vector_store import get_articles_collection
            col = get_articles_collection()
            count = col.count()
            if count > 0:
                results = col.get()
                col.delete(ids=results["ids"])
                vectors_deleted = count
        except Exception as e:
            logger.warning(f"ChromaDB clear failed (may not exist yet): {e}")

        # Clear legacy ChromaDB collection (old seeded data)
        legacy_vectors_deleted = 0
        try:
            from src.core.vector_store import get_legacy_collection
            legacy_col = get_legacy_collection()
            if legacy_col:
                legacy_count = legacy_col.count()
                if legacy_count > 0:
                    legacy_results = legacy_col.get()
                    legacy_col.delete(ids=legacy_results["ids"])
                    legacy_vectors_deleted = legacy_count
        except Exception as e:
            logger.warning(f"Legacy ChromaDB clear failed: {e}")

        logger.info(
            f"Reset complete: {articles_deleted} articles, "
            f"{content_items_deleted} content_items, {editions_deleted} editions, "
            f"{vectors_deleted} article vectors, {ads_vectors_deleted} ad vectors, "
            f"{legacy_vectors_deleted} legacy vectors"
        )

        return JSONResponse(content={
            "success": True,
            "deleted": {
                "articles": articles_deleted,
                "content_items": content_items_deleted,
                "editions": editions_deleted,
                "advertisements": ads_deleted,
                "vectors": vectors_deleted,
                "ad_vectors": ads_vectors_deleted,
            },
        })
    except Exception as e:
        logger.error(f"Reset failed: {e}", exc_info=True)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


# ── Publisher-scoped admin page routes ──
# These MUST be at the bottom so they don't shadow specific routes like /review, /costs, /directory, /api/*


@router.get("/{publisher_slug}", response_class=HTMLResponse)
async def admin_publisher_dashboard(
    request: Request, publisher_slug: str, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render publisher-scoped admin dashboard."""
    if publisher_slug not in _PUBLISHER_SLUGS:
        return templates.TemplateResponse(request=request, name="admin.html", context={"request": request, "publisher": "", "publisher_slug": ""})
    return templates.TemplateResponse(request=request, name="admin.html", context=_publisher_context(request, publisher_slug))


@router.get("/{publisher_slug}/directory", response_class=HTMLResponse)
async def admin_publisher_directory(
    request: Request, publisher_slug: str, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render publisher-scoped business directory."""
    if publisher_slug not in _PUBLISHER_SLUGS:
        return templates.TemplateResponse(request=request, name="directory.html", context={"request": request, "publisher": "", "publisher_slug": ""})
    return templates.TemplateResponse(request=request, name="directory.html", context=_publisher_context(request, publisher_slug))


@router.get("/{publisher_slug}/costs", response_class=HTMLResponse)
async def admin_publisher_costs(
    request: Request, publisher_slug: str, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render publisher-scoped API costs dashboard."""
    if publisher_slug not in _PUBLISHER_SLUGS:
        return templates.TemplateResponse(request=request, name="costs.html", context={"request": request, "publisher": "", "publisher_slug": ""})
    return templates.TemplateResponse(request=request, name="costs.html", context=_publisher_context(request, publisher_slug))


@router.get("/{publisher_slug}/main-street", response_class=HTMLResponse)
async def admin_publisher_main_street(
    request: Request, publisher_slug: str, _username: str = Depends(verify_credentials)
) -> HTMLResponse:
    """Render publisher-scoped Main Street OS admin page."""
    if publisher_slug not in _PUBLISHER_SLUGS:
        return templates.TemplateResponse(request=request, name="main_street.html", context={"request": request, "publisher": "", "publisher_slug": ""})
    return templates.TemplateResponse(request=request, name="main_street.html", context=_publisher_context(request, publisher_slug))
