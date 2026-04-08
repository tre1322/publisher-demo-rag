"""Vision-based extraction: send PDF pages to GPT-5.4 or Claude as images.

Instead of parsing PDF layout with code (which fails on multi-column
layouts, split text blocks, and column-break hyphens), this pipeline
sends each page as a PNG image to a vision model and asks it to read
the page like a human editor.

The model returns structured JSON with:
- Every article's headline, byline, and full body text
- Jump-out references (article continues on another page)
- Jump-in markers (article is a continuation from another page)

Then we stitch articles across pages using a scoring-based matcher
that evaluates 9 factors: keyword match, page references, byline
consistency, headline similarity, lexical continuity, content kind,
and distance penalty.

Default provider: OpenAI GPT-5.4 (image method, ~$0.04/page)
Fallback: Anthropic Claude Sonnet (~$0.02/page but less accurate)
"""

import base64
import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import fitz  # PyMuPDF

from src.core.config import (
    VISION_COST_PER_PAGE,
    VISION_DPI,
    VISION_MODEL,
    VISION_PAGE_DELAY,
    VISION_PROVIDER,
)
from src.modules.extraction.extract_pages import ARTIFACTS_BASE

logger = logging.getLogger(__name__)

# The prompt that drives structured JSON output from the vision model.
# Proven in testing with GPT-5.4 — do not change without re-testing.
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
        "est_time_minutes": round(page_count * 1.0, 1),  # ~60s avg per page with GPT-5.4
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


# ---------------------------------------------------------------------------
# Vision extraction — supports OpenAI GPT-5.4 and Anthropic Claude
# ---------------------------------------------------------------------------

def _extract_page_openai(
    image_bytes: bytes,
    page_num: int,
    client,
    media_type: str = "image/png",
) -> dict:
    """Send one page image to OpenAI GPT-5.4 and get structured JSON back."""
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = PAGE_PROMPT.replace("PAGE_NUM_PLACEHOLDER", str(page_num))

    response = client.responses.create(
        model=VISION_MODEL,
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:{media_type};base64,{image_b64}",
                },
                {
                    "type": "input_text",
                    "text": prompt,
                },
            ],
        }],
    )

    # Extract text from response
    raw_text = ""
    for item in response.output:
        if hasattr(item, "content") and item.content is not None:
            for block in item.content:
                if hasattr(block, "text"):
                    raw_text += block.text
        if hasattr(item, "text") and item.text:
            raw_text += item.text
    if not raw_text and hasattr(response, "output_text") and response.output_text:
        raw_text = response.output_text

    # Log cost
    try:
        from src.modules.costs.tracker import log_api_call
        usage = getattr(response, "usage", None)
        log_api_call(
            "openai", VISION_MODEL, "vision_extraction",
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        )
    except Exception:
        pass

    return _parse_vision_response(raw_text, page_num)


def _extract_page_anthropic(
    image_bytes: bytes,
    page_num: int,
    client,
    media_type: str = "image/png",
) -> dict:
    """Send one page image to Anthropic Claude and get structured JSON back."""
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
        log_api_call(
            "anthropic", VISION_MODEL, "vision_extraction",
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        )
    except Exception:
        pass

    raw_text = response.content[0].text.strip()
    return _parse_vision_response(raw_text, page_num)


def _parse_vision_response(raw_text: str, page_num: int) -> dict:
    """Parse raw vision model response text into structured JSON."""
    clean = raw_text.strip()

    # Strip markdown code fences if present
    if clean.startswith("```"):
        clean = re.sub(r"^```\w*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        result = _salvage_truncated_json(clean, page_num)

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


# ---------------------------------------------------------------------------
# Scoring-based article stitcher
# ---------------------------------------------------------------------------

def _stitch_articles(page_results: list[dict]) -> list[dict]:
    """Stitch articles across pages using a multi-factor scoring system.

    Evaluates 9 factors for each candidate stitch:
    1. Keyword match (jump_out keyword matches jump_in keyword)
    2. Page reference match (target_page matches continuation's page)
    3. Source page back-reference (continuation points back to source)
    4. Byline consistency
    5. Headline similarity (fuzzy match)
    6. Lexical continuity (last words of source overlap first words of continuation)
    7. Content kind match (news→news, sports→sports)
    8. Page distance penalty
    9. Body text length bonus (prefer longer continuations)

    Returns list of StandardArticle dicts.
    """
    # Separate source articles (with jump_out) and continuations (with jump_in)
    sources = []  # (page_data, article, page_num)
    continuations = []  # (page_data, article, page_num)
    standalone = []  # articles with no jumps

    for page in page_results:
        page_num = page["page_num"]
        for art in page.get("articles", []):
            if art.get("is_ad"):
                continue

            jo = art.get("jump_out") or {}
            ji = art.get("jump_in") or {}

            has_jump_out = bool(jo.get("keyword"))
            has_jump_in = bool(ji.get("keyword")) or art.get("is_continuation")

            if has_jump_out and not has_jump_in:
                sources.append((page, art, page_num))
            elif has_jump_in and not has_jump_out:
                continuations.append((page, art, page_num))
            elif has_jump_out and has_jump_in:
                # Article is both a continuation AND jumps out again (multi-hop)
                continuations.append((page, art, page_num))
                sources.append((page, art, page_num))
            else:
                standalone.append((page, art, page_num))

    # Score all possible source→continuation pairs
    scored_pairs = []
    for s_idx, (s_page, s_art, s_pnum) in enumerate(sources):
        for c_idx, (c_page, c_art, c_pnum) in enumerate(continuations):
            if s_pnum == c_pnum:
                continue  # same page — not a stitch
            score = _score_stitch(s_art, s_pnum, s_page, c_art, c_pnum, c_page)
            if score >= 5.0:
                scored_pairs.append((score, s_idx, c_idx))

    # Greedy one-to-one matching: highest scores first
    scored_pairs.sort(reverse=True)
    used_sources = set()
    used_conts = set()
    matches = []  # (source_idx, cont_idx)

    for score, s_idx, c_idx in scored_pairs:
        if s_idx in used_sources or c_idx in used_conts:
            continue
        matches.append((s_idx, c_idx))
        used_sources.add(s_idx)
        used_conts.add(c_idx)
        logger.info(
            f"  Stitch: '{sources[s_idx][1].get('headline', '')[:50]}' p{sources[s_idx][2]} "
            f"-> p{continuations[c_idx][2]} (score={score:.1f})"
        )

    # Build stitched articles
    stitched = []

    # Process matched sources
    matched_cont_indices = {c_idx for _, c_idx in matches}
    matched_src_indices = {s_idx for s_idx, _ in matches}

    for s_idx, c_idx in matches:
        s_page, s_art, s_pnum = sources[s_idx]
        c_page, c_art, c_pnum = continuations[c_idx]

        body = _merge_article_text(
            s_art.get("body_text", ""),
            c_art.get("body_text", ""),
        )

        # Use source headline (continuation headlines are often "from Page X")
        headline = s_art.get("headline", "")
        byline = s_art.get("byline") or c_art.get("byline") or ""
        if byline.lower().startswith("by "):
            byline = byline[3:].strip()

        stitched.append({
            "headline": headline,
            "subheadline": s_art.get("subheadline") or c_art.get("subheadline") or "",
            "byline": byline,
            "body_text": body,
            "content_type": _infer_page_content_type(
                s_page.get("page_type", ""), headline
            ),
            "start_page": s_pnum,
            "jump_pages": [c_pnum],
            "is_stitched": True,
            "extraction_confidence": 0.90,
            "source_pipeline": "vision",
        })

    # Add unmatched sources (jump_out but no matching continuation found)
    for s_idx, (s_page, s_art, s_pnum) in enumerate(sources):
        if s_idx in matched_src_indices:
            continue
        # Don't double-add if also in continuations
        if s_art.get("is_continuation"):
            continue
        byline = s_art.get("byline") or ""
        if byline.lower().startswith("by "):
            byline = byline[3:].strip()
        stitched.append({
            "headline": s_art.get("headline", ""),
            "subheadline": s_art.get("subheadline") or "",
            "byline": byline,
            "body_text": s_art.get("body_text", ""),
            "content_type": _infer_page_content_type(
                s_page.get("page_type", ""), s_art.get("headline", "")
            ),
            "start_page": s_pnum,
            "jump_pages": [],
            "is_stitched": False,
            "extraction_confidence": 0.85,
            "source_pipeline": "vision",
        })

    # Add standalone articles (no jumps at all)
    for s_page, s_art, s_pnum in standalone:
        byline = s_art.get("byline") or ""
        if byline.lower().startswith("by "):
            byline = byline[3:].strip()
        stitched.append({
            "headline": s_art.get("headline", ""),
            "subheadline": s_art.get("subheadline") or "",
            "byline": byline,
            "body_text": s_art.get("body_text", ""),
            "content_type": _infer_page_content_type(
                s_page.get("page_type", ""), s_art.get("headline", "")
            ),
            "start_page": s_pnum,
            "jump_pages": [],
            "is_stitched": False,
            "extraction_confidence": 0.85,
            "source_pipeline": "vision",
        })

    # Add unmatched continuations as standalone
    for c_idx, (c_page, c_art, c_pnum) in enumerate(continuations):
        if c_idx in matched_cont_indices:
            continue
        # Skip if this continuation is also a source that was already matched
        if c_art.get("jump_out", {}).get("keyword"):
            # Check if it was added as a source
            is_source_added = any(
                sources[s_idx][1] is c_art for s_idx in range(len(sources))
                if s_idx not in matched_src_indices
            )
            if not is_source_added:
                continue

        byline = c_art.get("byline") or ""
        if byline.lower().startswith("by "):
            byline = byline[3:].strip()
        stitched.append({
            "headline": c_art.get("headline", ""),
            "subheadline": c_art.get("subheadline") or "",
            "byline": byline,
            "body_text": c_art.get("body_text", ""),
            "content_type": _infer_page_content_type(
                c_page.get("page_type", ""), c_art.get("headline", "")
            ),
            "start_page": c_pnum,
            "jump_pages": [],
            "is_stitched": False,
            "extraction_confidence": 0.75,
            "source_pipeline": "vision",
        })

    return stitched


def _score_stitch(
    source: dict, s_pnum: int, s_page: dict,
    cont: dict, c_pnum: int, c_page: dict,
) -> float:
    """Score how well a source article and continuation match.

    Returns a float score; higher is better. Minimum threshold: 5.0.
    """
    score = 0.0

    s_jo = source.get("jump_out") or {}
    c_ji = cont.get("jump_in") or {}
    s_kw = (s_jo.get("keyword") or "").upper().strip()
    c_kw = (c_ji.get("keyword") or "").upper().strip()

    # 1. Keyword match (strongest signal) — up to 5 points
    if s_kw and c_kw:
        if s_kw == c_kw:
            score += 5.0
        elif s_kw in c_kw or c_kw in s_kw:
            score += 3.0
        else:
            # Fuzzy keyword match
            ratio = SequenceMatcher(None, s_kw, c_kw).ratio()
            if ratio > 0.6:
                score += ratio * 3.0

    # 2. Page reference match — source targets this page
    s_target = s_jo.get("target_page")
    if s_target and s_target == c_pnum:
        score += 3.0

    # 3. Source page back-reference — continuation points back to source
    c_source = c_ji.get("source_page")
    if c_source and c_source == s_pnum:
        score += 3.0

    # 4. Byline consistency
    s_by = (source.get("byline") or "").lower().strip()
    c_by = (cont.get("byline") or "").lower().strip()
    if s_by and c_by and s_by == c_by:
        score += 1.5

    # 5. Headline similarity
    s_hl = (source.get("headline") or "").lower()
    c_hl = (cont.get("headline") or "").lower()
    if s_hl and c_hl:
        hl_ratio = SequenceMatcher(None, s_hl, c_hl).ratio()
        if hl_ratio > 0.5:
            score += hl_ratio * 1.5

    # 6. Lexical continuity — do the last words of source overlap first words of cont?
    s_body = source.get("body_text", "")
    c_body = cont.get("body_text", "")
    if s_body and c_body:
        s_tail = s_body[-200:].lower().split()
        c_head = c_body[:200].lower().split()
        if s_tail and c_head:
            overlap = set(s_tail[-10:]) & set(c_head[:10])
            # Some overlap of common words is expected; significant overlap suggests match
            if len(overlap) >= 3:
                score += 1.0

    # 7. Content kind match
    s_type = s_page.get("page_type", "").lower()
    c_type = c_page.get("page_type", "").lower()
    if s_type and c_type and s_type == c_type:
        score += 0.5

    # 8. Page distance penalty — articles usually jump nearby
    dist = abs(c_pnum - s_pnum)
    if dist <= 2:
        score += 1.0
    elif dist <= 5:
        score += 0.5
    elif dist > 10:
        score -= 1.0

    # 9. Body text length bonus — prefer longer continuations (more likely real)
    if len(c_body) > 500:
        score += 0.5

    return score


def _merge_article_text(source_body: str, cont_body: str) -> str:
    """Merge source and continuation text, deduplicating any overlap.

    Sometimes the source's trailing text and the continuation's leading
    text overlap (the model transcribes the same paragraph twice). This
    finds the longest suffix/prefix overlap and removes the duplicate.
    """
    if not cont_body:
        return source_body
    if not source_body:
        return cont_body

    # Check for suffix/prefix overlap
    # Take the last 300 chars of source and first 300 chars of continuation
    s_tail = source_body[-300:]
    c_head = cont_body[:300:]

    best_overlap = 0
    min_overlap = 30  # minimum chars to consider as real overlap

    for i in range(min_overlap, min(len(s_tail), len(c_head)) + 1):
        if s_tail.endswith(c_head[:i]):
            best_overlap = i

    if best_overlap >= min_overlap:
        logger.info(f"  Overlap dedup: removed {best_overlap} chars of duplicate text")
        return source_body + "\n\n" + cont_body[best_overlap:].lstrip()

    return source_body + "\n\n" + cont_body


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
    provider: str | None = None,
) -> dict:
    """Full vision pipeline: render pages -> Vision API -> stitch -> StandardArticles.

    Args:
        pdf_path: Path to the PDF file.
        edition_id: Edition ID in the database.
        publisher_id: Publisher ID.
        page_filter: Optional list of page numbers to process (1-indexed).
                     If None, processes all pages.
        on_page_complete: Optional callback(page_num, total_pages, result) for progress.
        provider: Override vision provider ("openai" or "anthropic").
                  Defaults to VISION_PROVIDER from config.

    Returns:
        Dict with success, articles (StandardArticle list), page_results, cost_usd, timing.
    """
    pdf_path = str(pdf_path)
    start_time = time.time()
    vision_provider = (provider or VISION_PROVIDER).lower()

    result = {
        "success": False,
        "edition_id": edition_id,
        "articles": [],
        "page_results": [],
        "pages_processed": 0,
        "cost_usd": 0.0,
        "error": None,
        "provider": vision_provider,
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

    # Initialize the appropriate API client
    if vision_provider == "openai":
        from openai import OpenAI
        client = OpenAI(timeout=120.0)  # 2 min timeout per request to prevent hangs
        extract_fn = _extract_page_openai
        logger.info(f"Vision pipeline using OpenAI {VISION_MODEL}")
    else:
        import anthropic
        client = anthropic.Anthropic()
        extract_fn = _extract_page_anthropic
        logger.info(f"Vision pipeline using Anthropic {VISION_MODEL}")

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

            # Send to Vision API
            page_data = extract_fn(image_bytes, page_num, client, media_type)

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
        f"Vision pipeline complete ({vision_provider}): {len(articles)} articles "
        f"({stitched_count} stitched) from {len(pages_to_process)} pages "
        f"in {elapsed}s, cost=${result['cost_usd']}"
    )

    # Save stitched articles artifact
    articles_path = artifacts_dir / "articles_vision.json"
    with open(articles_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2, ensure_ascii=False)

    return result
