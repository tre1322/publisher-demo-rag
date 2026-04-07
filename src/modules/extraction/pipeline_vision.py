"""Vision-based extraction: send PDF pages to Claude Opus as images.

Instead of parsing PDF layout with code (which fails on multi-column
layouts, split text blocks, and column-break hyphens), this pipeline
sends each page as a PNG image to Claude's vision model and asks it
to read the page like a human editor.

Claude returns structured JSON with:
- Every article's headline, byline, and full body text
- Jump-out references (article continues on another page)
- Jump-in markers (article is a continuation from another page)

Then we stitch articles across pages by matching jump keywords.

Proven approach: tested on Cottonwood County Citizen 03-18-26,
pages 1+5. All 3 jumped articles stitched correctly.

Cost: ~$0.02/page, ~$0.30 per 14-page edition.
"""

import base64
import json
import logging
import re
import time
from pathlib import Path

import anthropic
import fitz  # PyMuPDF

from src.core.config import VISION_COST_PER_PAGE, VISION_DPI, VISION_MODEL, VISION_PAGE_DELAY
from src.modules.extraction.extract_pages import ARTIFACTS_BASE

logger = logging.getLogger(__name__)

# The prompt that drives structured JSON output from Claude Vision.
# Proven in testing — do not change without re-testing on known editions.
PAGE_PROMPT = """You are a newspaper text transcription tool. Your job is to TRANSCRIBE the exact text from this newspaper page into structured JSON.

CRITICAL: You must copy the EXACT words printed on the page. Do NOT summarize, paraphrase, shorten, or make up any text. Every word in body_text must appear on the page exactly as printed. If you cannot read a word, use [illegible]. Never fabricate or infer text that isn't visible.

Return ONLY valid JSON with this structure:

{
  "page_num": <integer — set this to PAGE_NUM_PLACEHOLDER>,
  "page_type": "<front|inside|sports|classifieds|opinion|obituaries>",
  "articles": [
    {
      "headline": "<exact headline text as printed>",
      "subheadline": "<exact subheadline text, or null>",
      "byline": "<author name without 'By' prefix, or null>",
      "body_text": "<EXACT article body text transcribed word-for-word from the page. Preserve paragraph breaks with \\n\\n. Copy every sentence completely.>",
      "jump_out": {
        "keyword": "<slug keyword like COUNCIL or SCHOOL or null>",
        "target_page": <page number or null>
      },
      "jump_in": {
        "keyword": "<slug keyword or null>",
        "source_page": <page number or null>
      },
      "is_continuation": <true if this article continues from another page, else false>,
      "is_ad": false
    }
  ],
  "ads": [
    {
      "advertiser": "<business name>",
      "text": "<ad text content>",
      "is_ad": true
    }
  ]
}

Rules:
- TRANSCRIBE every article's body text EXACTLY as printed — word for word, sentence for sentence
- Do NOT summarize, paraphrase, or shorten any text
- Do NOT make up or infer text that is not visible on the page
- Preserve paragraph breaks as \\n\\n in body_text
- For jump references like "SEE COUNCIL • PAGE 8" or "COUNCIL/ FROM PAGE 1" or "KEYWORD • Page N", set jump_out or jump_in accordingly
- If an article continues to another page, set jump_out with the keyword and target page
- If this is a continuation from another page, set is_continuation=true and jump_in with source page
- Do NOT include advertisements in the articles array — put them in ads array
- Do NOT include photo captions, pull quotes, or page furniture in articles
- For byline, extract just the name (remove "By" prefix)
- Return ONLY the JSON object, no explanation, no markdown code fences"""


def estimate_vision_cost(page_count: int) -> dict:
    """Estimate cost and time for vision processing.

    Returns dict with page_count, estimated USD cost, and estimated minutes.
    """
    return {
        "pages": page_count,
        "est_cost_usd": round(page_count * VISION_COST_PER_PAGE, 2),
        "est_time_minutes": round(page_count * 1.5, 1),  # ~90s avg per page
    }


def _render_page_image(doc: fitz.Document, page_num: int) -> tuple[bytes, str]:
    """Render a single PDF page to image bytes for vision API.

    Uses PNG (lossless) at full DPI. Falls back to JPEG if PNG exceeds
    the 5MB API limit — JPEG with quality 95 preserves text sharpness
    while being ~3x smaller. Only reduces DPI as a last resort.

    Args:
        doc: Open PyMuPDF document.
        page_num: 1-indexed page number.

    Returns:
        Tuple of (image_bytes, media_type) where media_type is
        "image/png" or "image/jpeg".
    """
    page = doc[page_num - 1]
    zoom = VISION_DPI / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    max_bytes = 4_500_000

    # Try PNG first (lossless — best for text)
    png_bytes = pix.tobytes("png")
    if len(png_bytes) <= max_bytes:
        return png_bytes, "image/png"

    # PNG too large — switch to JPEG at quality 95 (still very sharp)
    logger.info(
        f"Page {page_num}: PNG too large ({len(png_bytes)} bytes), "
        f"using JPEG at full {VISION_DPI} DPI"
    )
    jpeg_bytes = pix.tobytes("jpeg", jpg_quality=95)
    if len(jpeg_bytes) <= max_bytes:
        return jpeg_bytes, "image/jpeg"

    # JPEG still too large — reduce DPI progressively
    scale = 0.80
    while len(jpeg_bytes) > max_bytes and scale > 0.4:
        reduced_dpi = int(VISION_DPI * scale)
        logger.warning(
            f"Page {page_num} image too large ({len(jpeg_bytes)} bytes), "
            f"reducing to {reduced_dpi} DPI JPEG"
        )
        zoom = reduced_dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        jpeg_bytes = pix.tobytes("jpeg", jpg_quality=95)
        scale -= 0.1

    return jpeg_bytes, "image/jpeg"


def _extract_page_vision(
    image_bytes: bytes,
    page_num: int,
    client: anthropic.Anthropic,
    media_type: str = "image/png",
) -> dict:
    """Send one page image to Claude Vision and get structured JSON back.

    Args:
        image_bytes: Image bytes (PNG or JPEG).
        page_num: 1-indexed page number (injected into prompt).
        client: Anthropic API client.
        media_type: MIME type of the image ("image/png" or "image/jpeg").

    Returns:
        Parsed JSON dict with page_num, articles, ads.
    """
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = PAGE_PROMPT.replace("PAGE_NUM_PLACEHOLDER", str(page_num))

    response = client.messages.create(
        model=VISION_MODEL,
        max_tokens=16384,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
        }],
    )

    # Log cost
    try:
        from src.modules.costs.tracker import log_api_call
        usage = getattr(response, "usage", None)
        log_api_call("anthropic", VISION_MODEL, "vision_extraction",
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0)
    except Exception:
        pass

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```\w*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to salvage truncated JSON
        result = _salvage_truncated_json(raw_text, page_num)

    # Ensure page_num is set
    result["page_num"] = page_num

    return result


def _salvage_truncated_json(raw_text: str, page_num: int) -> dict:
    """Extract complete article objects from a truncated JSON response.

    When max_tokens is too low, the response gets cut off mid-JSON.
    This extracts any complete article objects that were fully formed.
    """
    logger.warning(f"Page {page_num}: JSON parse failed, attempting salvage")

    articles = []
    # Find all complete article objects using regex
    pattern = r'\{[^{}]*"headline"[^{}]*"body_text"[^{}]*\}'
    for match in re.finditer(pattern, raw_text, re.DOTALL):
        try:
            art = json.loads(match.group())
            articles.append(art)
        except json.JSONDecodeError:
            continue

    if articles:
        logger.info(f"  Salvaged {len(articles)} complete article(s) from truncated response")

    return {
        "page_num": page_num,
        "articles": articles,
        "ads": [],
        "_salvaged": True,
    }


def _stitch_articles(page_results: list[dict]) -> list[dict]:
    """Stitch articles across pages using jump_out/jump_in keywords.

    For each article with a jump_out keyword, find the matching continuation
    on the target page and concatenate the body text.

    Returns list of StandardArticle dicts.
    """
    # Build index: (keyword, source_page) -> continuation article
    continuations = {}
    for page in page_results:
        page_num = page["page_num"]
        for art in page.get("articles", []):
            ji = art.get("jump_in")
            if art.get("is_continuation") and ji and ji.get("keyword"):
                key = (ji["keyword"].upper().strip(), ji.get("source_page"))
                continuations[key] = {**art, "_page_num": page_num}

    stitched = []
    used_keys = set()

    for page in page_results:
        page_num = page["page_num"]
        for art in page.get("articles", []):
            if art.get("is_continuation"):
                continue  # skip — merged into source article

            if art.get("is_ad"):
                continue  # skip ads

            body = art.get("body_text", "")
            headline = art.get("headline", "")
            jump_pages = []
            is_stitched = False

            # Check for jump-out
            jo = art.get("jump_out")
            if jo and jo.get("keyword"):
                kw = jo["keyword"].upper().strip()
                target = jo.get("target_page")

                # Try exact match (keyword + source_page)
                cont = (
                    continuations.get((kw, page_num))
                    or continuations.get((kw, None))
                    or next((v for k, v in continuations.items() if k[0] == kw), None)
                )

                if cont:
                    key = next(k for k, v in continuations.items() if v is cont)
                    if key not in used_keys:
                        cont_body = cont.get("body_text", "")
                        if cont_body:
                            body = body + "\n\n" + cont_body
                            jump_pages.append(cont["_page_num"])
                            is_stitched = True
                            used_keys.add(key)

            # Build StandardArticle
            byline = art.get("byline") or ""
            if byline.lower().startswith("by "):
                byline = byline[3:].strip()

            stitched.append({
                "headline": headline,
                "subheadline": art.get("subheadline") or "",
                "byline": byline,
                "body_text": body,
                "content_type": _infer_page_content_type(page.get("page_type", ""), headline),
                "start_page": page_num,
                "jump_pages": jump_pages,
                "is_stitched": is_stitched,
                "extraction_confidence": 0.85,
                "source_pipeline": "vision",
            })

    # Add any unclaimed continuations as standalone articles
    for key, cont in continuations.items():
        if key not in used_keys:
            stitched.append({
                "headline": cont.get("headline", ""),
                "subheadline": cont.get("subheadline") or "",
                "byline": cont.get("byline") or "",
                "body_text": cont.get("body_text", ""),
                "content_type": "news",
                "start_page": cont["_page_num"],
                "jump_pages": [],
                "is_stitched": False,
                "extraction_confidence": 0.75,
                "source_pipeline": "vision",
            })

    return stitched


def _infer_page_content_type(page_type: str, headline: str) -> str:
    """Infer content type from page type and headline."""
    pt = page_type.lower()
    if pt == "sports":
        return "sports"
    if pt == "obituaries":
        return "obituary"
    if pt == "opinion":
        return "opinion"
    if pt == "classifieds":
        return "classified"

    # Fallback to headline keywords
    hl = headline.lower()
    if any(w in hl for w in ("wrestling", "basketball", "hockey", "football", "tournament")):
        return "sports"
    if any(w in hl for w in ("obituar", "death", "funeral")):
        return "obituary"
    return "news"


def run_vision_pipeline(
    pdf_path: str | Path,
    edition_id: int,
    publisher_id: int,
    page_filter: list[int] | None = None,
    on_page_complete: callable = None,
) -> dict:
    """Full vision pipeline: render pages -> Claude Vision -> stitch -> StandardArticles.

    Args:
        pdf_path: Path to the PDF file.
        edition_id: Edition ID in the database.
        publisher_id: Publisher ID.
        page_filter: Optional list of page numbers to process (1-indexed).
                     If None, processes all pages.
        on_page_complete: Optional callback(page_num, total_pages, result) for progress.

    Returns:
        Dict with success, articles (StandardArticle list), page_results, cost_usd, timing.
    """
    pdf_path = str(pdf_path)
    start_time = time.time()

    result = {
        "success": False,
        "edition_id": edition_id,
        "articles": [],
        "page_results": [],
        "pages_processed": 0,
        "cost_usd": 0.0,
        "error": None,
    }

    # Open PDF
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result["error"] = f"Failed to open PDF: {e}"
        return result

    total_pages = len(doc)
    pages_to_process = page_filter or list(range(1, total_pages + 1))

    # Create artifacts directory
    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Initialize Anthropic client
    client = anthropic.Anthropic()

    page_results = []

    for page_num in pages_to_process:
        if page_num < 1 or page_num > total_pages:
            logger.warning(f"Skipping page {page_num} (out of range)")
            continue

        logger.info(f"Vision extracting page {page_num}/{total_pages}...")

        try:
            # Render page (PNG preferred, falls back to JPEG for large pages)
            image_bytes, media_type = _render_page_image(doc, page_num)
            fmt = "PNG" if "png" in media_type else "JPEG"
            logger.info(f"  Page {page_num}: {len(image_bytes)} bytes {fmt}")

            # Send to Claude Vision
            page_data = _extract_page_vision(image_bytes, page_num, client, media_type)

            article_count = len(page_data.get("articles", []))
            ad_count = len(page_data.get("ads", []))
            logger.info(f"  Page {page_num}: {article_count} articles, {ad_count} ads")

            # Save per-page artifact for debugging
            artifact_path = artifacts_dir / f"vision_page_{page_num:03d}.json"
            with open(artifact_path, "w", encoding="utf-8") as f:
                json.dump(page_data, f, indent=2, ensure_ascii=False)

            page_results.append(page_data)
            result["cost_usd"] += VISION_COST_PER_PAGE

            if on_page_complete:
                on_page_complete(page_num, total_pages, page_data)

        except Exception as e:
            logger.error(f"  Page {page_num} failed: {e}", exc_info=True)
            page_results.append({
                "page_num": page_num,
                "articles": [],
                "ads": [],
                "_error": str(e),
            })

        # Rate limiting between pages
        if page_num != pages_to_process[-1]:
            time.sleep(VISION_PAGE_DELAY)

    doc.close()

    # Stitch articles across pages
    articles = _stitch_articles(page_results)

    result["success"] = True
    result["articles"] = articles
    result["page_results"] = page_results
    result["pages_processed"] = len(pages_to_process)
    result["cost_usd"] = round(result["cost_usd"], 2)

    elapsed = round(time.time() - start_time, 1)
    stitched_count = sum(1 for a in articles if a.get("is_stitched"))
    logger.info(
        f"Vision pipeline complete: {len(articles)} articles "
        f"({stitched_count} stitched) from {len(pages_to_process)} pages "
        f"in {elapsed}s, cost=${result['cost_usd']}"
    )

    # Save stitched articles artifact
    articles_path = artifacts_dir / "articles_vision.json"
    with open(articles_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)

    return result
