"""Ad processing utilities: OCR fallback, categorization, text enrichment.

Provides a pipeline for turning raw/image-based ad PDFs into searchable,
enriched text suitable for embedding and retrieval.
"""

import base64
import logging
import re

import anthropic
import fitz

from src.core.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

# ── Minimum text threshold ──────────────────────────────────────────────
# If extracted text is shorter than this (in characters), trigger OCR fallback.
MIN_TEXT_LENGTH = 30

# ── Category keyword map ────────────────────────────────────────────────
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "healthcare": [
        "health", "hospital", "clinic", "medical", "nurse", "doctor",
        "pharmacy", "dental", "therapy", "wellness", "care", "patient",
        "daisy award", "nomination", "nominate",
    ],
    "real_estate": [
        "real estate", "realty", "home for sale", "listing", "property",
        "house", "apartment", "rent", "mortgage", "acre", "sq ft",
        "bedroom", "bath", "open house",
    ],
    "dining": [
        "restaurant", "dining", "menu", "food", "pizza", "burger",
        "grill", "cafe", "coffee", "catering", "buffet", "bar & grill",
        "fish fry", "prime rib", "breakfast", "lunch", "dinner",
    ],
    "entertainment": [
        "theater", "theatre", "movie", "cinema", "show", "concert",
        "live music", "performance", "ticket", "matinee", "film",
    ],
    "automotive": [
        "auto", "car", "truck", "vehicle", "tire", "oil change",
        "repair", "dealer", "motors", "collision",
    ],
    "finance": [
        "bank", "credit union", "loan", "insurance", "financial",
        "invest", "mortgage", "accounting", "tax",
    ],
    "events": [
        "event", "festival", "fair", "fundraiser", "auction",
        "celebration", "parade", "tournament", "race",
    ],
    "grocery": [
        "grocery", "supermarket", "produce", "meat", "deli",
        "bakery", "farm", "organic",
    ],
    "retail": [
        "shop", "store", "sale", "clearance", "discount",
        "gift", "jewelry", "clothing", "hardware",
    ],
}

# ── Location patterns ───────────────────────────────────────────────────
_LOCATION_PATTERN = re.compile(
    r"""
    (?:
        (?P<city>(?:[A-Z][a-z]+\.?\s?)+)   # City name(s), including "St. James"
        ,\s*
        (?P<state>MN|Minnesota|SD|South\sDakota|IA|Iowa)
    )
    """,
    re.VERBOSE,
)


# ── OCR via Claude Vision ───────────────────────────────────────────────

def ocr_pdf_bytes(data: bytes, filename: str = "ad.pdf") -> str:
    """Extract text from an image-based PDF using Claude Vision API.

    Renders each page to an image, sends to Claude for OCR.
    Only call this when normal text extraction returns empty/short text.

    Args:
        data: Raw PDF bytes.
        filename: Original filename (for logging).

    Returns:
        Extracted OCR text, or empty string on failure.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("OCR skipped: ANTHROPIC_API_KEY not set")
        return ""

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        logger.error(f"OCR: failed to open PDF {filename}: {e}")
        return ""

    # Render pages to images
    image_parts = []
    try:
        for page_idx in range(min(len(doc), 5)):  # Cap at 5 pages
            page = doc[page_idx]
            # Render at 200 DPI for good OCR quality without huge images
            pix = page.get_pixmap(dpi=200)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            image_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            })
        doc.close()
    except Exception as e:
        doc.close()
        logger.error(f"OCR: failed to render pages for {filename}: {e}")
        return ""

    if not image_parts:
        return ""

    # Call Claude Vision for OCR
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content = image_parts + [{
            "type": "text",
            "text": (
                "Extract ALL readable text from this advertisement image. "
                "Start with the business/advertiser name (from logo, header, "
                "or branding area). Then include phone numbers, addresses, "
                "dates, prices, and all promotional text. Return only the "
                "extracted text, no commentary."
            ),
        }]

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )

        ocr_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                ocr_text += block.text

        ocr_text = ocr_text.strip()
        if ocr_text:
            logger.info(
                f"OCR extracted {len(ocr_text)} chars from {filename} "
                f"({len(image_parts)} pages)"
            )
        else:
            logger.warning(f"OCR returned empty text for {filename}")

        return ocr_text

    except Exception as e:
        logger.error(f"OCR API call failed for {filename}: {e}")
        return ""


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}


def is_image_file(filename: str) -> bool:
    """Check if a filename has an image extension."""
    from pathlib import Path
    return Path(filename).suffix.lower() in _IMAGE_EXTENSIONS


def _get_media_type(filename: str) -> str:
    """Get MIME type for an image filename."""
    from pathlib import Path
    return _IMAGE_MEDIA_TYPES.get(Path(filename).suffix.lower(), "image/png")


def ocr_image_bytes(data: bytes, filename: str = "ad.png") -> str:
    """Extract text from an image ad using Claude Vision API.

    Args:
        data: Raw image bytes (PNG, JPG, etc.).
        filename: Original filename (for logging and media type detection).

    Returns:
        Extracted text, or empty string on failure.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("OCR skipped: ANTHROPIC_API_KEY not set")
        return ""

    b64 = base64.b64encode(data).decode("utf-8")
    media_type = _get_media_type(filename)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract ALL readable text from this advertisement image. "
                            "Start with the business/advertiser name (from logo, header, "
                            "or branding area). Then include phone numbers, addresses, "
                            "dates, prices, and all promotional text. Return only the "
                            "extracted text, no commentary."
                        ),
                    },
                ],
            }],
        )

        ocr_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                ocr_text += block.text

        ocr_text = ocr_text.strip()
        if ocr_text:
            logger.info(f"Image OCR extracted {len(ocr_text)} chars from {filename}")
        else:
            logger.warning(f"Image OCR returned empty text for {filename}")

        return ocr_text

    except Exception as e:
        logger.error(f"Image OCR API call failed for {filename}: {e}")
        return ""


def extract_business_name_from_image_bytes(
    data: bytes, filename: str = "ad.png"
) -> str:
    """Use Claude Vision to identify business name from an image ad.

    Args:
        data: Raw image bytes.
        filename: Original filename.

    Returns:
        Business name string, or empty string on failure.
    """
    if not ANTHROPIC_API_KEY:
        return ""

    b64 = base64.b64encode(data).decode("utf-8")
    media_type = _get_media_type(filename)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "What is the business or advertiser name in this ad? "
                            "Look at logos, headers, and branding. Return ONLY the "
                            "business name, nothing else. If you cannot determine "
                            "the business name, return exactly: UNKNOWN"
                        ),
                    },
                ],
            }],
        )

        name = ""
        for block in response.content:
            if hasattr(block, "text"):
                name = block.text.strip()

        if name and name.upper() != "UNKNOWN" and len(name) < 200:
            logger.info(f"Business name from image for {filename}: '{name}'")
            return name
        return ""

    except Exception as e:
        logger.error(f"Business name image extraction failed for {filename}: {e}")
        return ""


def ocr_pdf_file(file_path: str) -> str:
    """OCR a PDF file on disk. Convenience wrapper around ocr_pdf_bytes."""
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        return ocr_pdf_bytes(data, filename=str(file_path))
    except Exception as e:
        logger.error(f"OCR: failed to read {file_path}: {e}")
        return ""


def extract_business_name_from_image(data: bytes, filename: str = "ad.pdf") -> str:
    """Use Claude Vision to identify the business/advertiser name from an ad PDF.

    Renders the first page to an image and asks Claude to identify the
    business name from logos, headers, and branding.

    Args:
        data: Raw PDF bytes.
        filename: Original filename (for logging).

    Returns:
        Business name string, or empty string on failure.
    """
    if not ANTHROPIC_API_KEY:
        return ""

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        page = doc[0]
        pix = page.get_pixmap(dpi=150)  # Lower DPI is fine for name extraction
        png_bytes = pix.tobytes("png")
        doc.close()
        b64 = base64.b64encode(png_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Business name extraction: failed to render {filename}: {e}")
        return ""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "What is the business or advertiser name in this ad? "
                            "Look at logos, headers, and branding. Return ONLY the "
                            "business name, nothing else. If you cannot determine "
                            "the business name, return exactly: UNKNOWN"
                        ),
                    },
                ],
            }],
        )

        name = ""
        for block in response.content:
            if hasattr(block, "text"):
                name = block.text.strip()

        if name and name.upper() != "UNKNOWN" and len(name) < 200:
            logger.info(f"Business name extracted via Vision for {filename}: '{name}'")
            return name
        else:
            logger.info(f"Business name extraction returned no result for {filename}")
            return ""

    except Exception as e:
        logger.error(f"Business name Vision API call failed for {filename}: {e}")
        return ""


# ── Ad categorization ───────────────────────────────────────────────────

def categorize_ad(text: str, advertiser: str = "") -> str:
    """Categorize an ad using keyword heuristics.

    Args:
        text: Ad text content.
        advertiser: Advertiser name.

    Returns:
        Category string (e.g., "healthcare", "real_estate", "general").
    """
    combined = f"{advertiser} {text}".lower()
    scores: dict[str, int] = {}

    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)  # type: ignore[arg-type]
    return "general"


# ── Location extraction ─────────────────────────────────────────────────

def extract_location(text: str) -> str:
    """Extract location from ad text using pattern matching.

    Returns:
        Location string like "Windom, MN" or empty string.
    """
    match = _LOCATION_PATTERN.search(text)
    if match:
        return f"{match.group('city')}, {match.group('state')}"
    return ""


# ── Text enrichment ─────────────────────────────────────────────────────

def enrich_ad_text(
    advertiser: str,
    raw_text: str,
    ocr_text: str = "",
    category: str = "",
    location: str = "",
) -> str:
    """Build enriched searchable text for an ad.

    Prepends a semantic header with advertiser name, category, and location,
    then appends the best available content text.

    Args:
        advertiser: Advertiser/business name.
        raw_text: Text extracted from PDF.
        ocr_text: OCR-extracted text (if any).
        category: Ad category.
        location: Detected location.

    Returns:
        Enriched text string suitable for embedding.
    """
    # Build header
    parts = [f"{advertiser} advertisement."]
    if category and category != "general":
        label = category.replace("_", " ").title()
        parts.append(f"{label} promotion.")
    if location:
        parts.append(f"Location: {location}.")

    header = " ".join(parts)

    # Pick best content (priority order)
    content = ocr_text or raw_text or ""

    if not content.strip():
        return header

    return f"{header} {content}"


# ── Query expansion ─────────────────────────────────────────────────────

_QUERY_EXPANSIONS: dict[str, list[str]] = {
    "healthcare": ["health", "medical", "clinic", "hospital", "nurse", "award", "nomination"],
    "real_estate": ["real estate", "listing", "property", "house", "home for sale", "homes for sale", "home", "homes"],
    "dining": ["restaurant", "food", "menu", "dining", "special"],
    "entertainment": ["theater", "movie", "show", "concert", "ticket"],
    "automotive": ["auto", "car", "truck", "repair", "vehicle"],
    "finance": ["bank", "insurance", "loan", "financial"],
    "events": ["event", "festival", "fair", "fundraiser"],
}


def expand_ad_query(query: str) -> list[str]:
    """Expand a user query with related ad terms.

    Args:
        query: Original user query.

    Returns:
        List of expansion terms (may be empty).
    """
    query_lower = query.lower()
    expansions = set()

    # Detect intent categories from query
    for category, terms in _QUERY_EXPANSIONS.items():
        for term in terms:
            if term in query_lower:
                expansions.update(terms)
                break

    # Generic ad-intent expansions
    ad_signals = ["advertis", "promot", "sponsor", "ad ", "ads ", "deal", "sale"]
    if any(s in query_lower for s in ad_signals):
        expansions.update(["advertisement", "promotion", "sponsored", "local business"])

    # Remove terms already in the query
    query_words = set(query_lower.split())
    expansions -= query_words

    return list(expansions)
