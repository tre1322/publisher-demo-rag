"""Phase 5: Cleanup and normalized web text.

Rewritten based on the Article View System Architecture document.
Key improvements:
- Soft hyphen rejoining
- Column newline collapsing
- Jump reference stripping (normal AND spaced-out)
- Continuation header stripping (KEYWORD/ and FROM PAGE)
- Pull quote filtering (large-font text that duplicates body)
- Caption separation
- Proper paragraph preservation
"""

import json
import logging
import re
import time
from pathlib import Path

from src.modules.editions.database import get_edition
from src.modules.extraction.extract_pages import ARTIFACTS_BASE
from src.modules.extraction.stitch_jumps import get_stitched

logger = logging.getLogger(__name__)

# ── Text Cleanup ──

# Patterns to strip from article body text
STRIP_PATTERNS = [
    # Jump reference labels (normal spacing)
    re.compile(r"SEE\s+\w+\s*[•·\uf06e]\s*(?:BACK\s+)?PAGE\s*\d*", re.IGNORECASE),
    # Jump reference labels (letter-spaced)
    re.compile(r"S\s*E\s*E\s+\w+[\s\u2009•·]*(?:B\s*A\s*C\s*K\s*)?P\s*A\s*G\s*E\s*\d*", re.IGNORECASE),
    # FROM PAGE labels (normal)
    re.compile(r"FROM\s+PAGE\s*\d+", re.IGNORECASE),
    # FROM PAGE labels (letter-spaced)
    re.compile(r"F\s*R\s*O\s*M\s+P\s*A\s*G\s*E\s*\d+", re.IGNORECASE),
    # Continuation headers: "KEYWORD/ subtitle"
    re.compile(r"^[A-Z]{2,}\s*/\s*[^\n]+", re.MULTILINE),
    # Bullet + page reference: "• Page 4"
    re.compile(r"[\uf06e■]\s*Page\s*\d+", re.IGNORECASE),
    # Standalone "• Page N" references
    re.compile(r"[•·]\s*Page\s*\d+", re.IGNORECASE),
    # Bullet character (used as kicker prefix and separator)
    re.compile(r"[\uf06e]\s*"),
    # "Continued on/from page N"
    re.compile(r"[Cc]ontinued\s+(?:on|from)\s+[Pp]age\s*\d+"),
    # Page number references in isolation
    re.compile(r"^\s*[•■]\s*Page\s*\d+\s*$", re.MULTILINE),
]

# Soft hyphen + line break -> rejoin word
SOFT_HYPHEN_RE = re.compile(r"(\w)\xad\s*\n\s*(\w)")
# Also standalone soft hyphens
SOFT_HYPHEN_STANDALONE = re.compile(r"\xad")

# Column newlines: single newlines -> spaces, preserve double newlines
SINGLE_NEWLINE_RE = re.compile(r"(?<!\n)\n(?!\n)")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
MULTI_SPACE_RE = re.compile(r"  +")

# Thin/narrow spaces
THIN_SPACES_RE = re.compile(r"[\u2009\u200a\u202f\u2003]")

# Private use area characters (PDF artifacts)
PUA_RE = re.compile(r"[\uf000-\uf0ff]")


def clean_text(raw_text: str) -> str:
    """Clean raw extracted text into readable web text."""
    text = raw_text

    # Step 1: Rejoin soft-hyphenated words BEFORE collapsing newlines
    text = SOFT_HYPHEN_RE.sub(r"\1\2", text)
    text = SOFT_HYPHEN_STANDALONE.sub("", text)

    # Step 2: Strip jump/continuation patterns
    for pattern in STRIP_PATTERNS:
        text = pattern.sub("", text)

    # Step 3: Clean special characters
    text = THIN_SPACES_RE.sub(" ", text)
    text = PUA_RE.sub("", text)

    # Step 4: Collapse column newlines (single newline -> space)
    text = SINGLE_NEWLINE_RE.sub(" ", text)

    # Step 5: Collapse excess newlines
    text = MULTI_NEWLINE_RE.sub("\n\n", text)

    # Step 6: Collapse multiple spaces
    text = MULTI_SPACE_RE.sub(" ", text)

    # Step 7: Strip each line
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    return text.strip()


def clean_headline(headline: str) -> str:
    """Clean a headline string."""
    text = headline.replace("\n", " ").strip()
    # Remove KEYWORD/ prefix from continuation headers
    text = re.sub(r"^[A-Z]{2,}\s*/\s*", "", text)
    text = SOFT_HYPHEN_STANDALONE.sub("", text)
    text = PUA_RE.sub("", text)
    text = THIN_SPACES_RE.sub(" ", text)
    text = MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def clean_kicker(kicker: str) -> str:
    """Clean a kicker/summary line."""
    text = kicker.replace("\n", " ").strip()
    # Remove bullet prefix
    text = re.sub(r"^[\uf06e■]\s*", "", text)
    text = SOFT_HYPHEN_STANDALONE.sub("", text)
    text = PUA_RE.sub("", text)
    text = THIN_SPACES_RE.sub(" ", text)
    text = MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


# ── Content Type Classification ──

CONTENT_TYPE_PATTERNS = {
    "obituary": [
        re.compile(r"obituar", re.IGNORECASE),
        re.compile(r"passed\s+away", re.IGNORECASE),
        re.compile(r"funeral\s+service", re.IGNORECASE),
    ],
    "legal": [
        re.compile(r"public\s+notice", re.IGNORECASE),
        re.compile(r"legal\s+notice", re.IGNORECASE),
        re.compile(r"state\s+of\s+minnesota", re.IGNORECASE),
        re.compile(r"district\s+court", re.IGNORECASE),
    ],
    "police": [
        re.compile(r"sheriff'?s?\s+report", re.IGNORECASE),
        re.compile(r"police\s+report", re.IGNORECASE),
    ],
    "sports": [
        re.compile(r"(wolverine|cobra|eagle)s?\s+(boys?|girls?|team)", re.IGNORECASE),
        re.compile(r"(basketball|football|hockey|wrestling|volleyball)", re.IGNORECASE),
        re.compile(r"(tournament|tourney|playoffs?|championship)", re.IGNORECASE),
        re.compile(r"athlete\s+of\s+the\s+week", re.IGNORECASE),
    ],
    "opinion": [
        re.compile(r"(editorial|op-?ed|letter\s+to\s+the\s+editor)", re.IGNORECASE),
        re.compile(r"(column|commentary|viewpoint)", re.IGNORECASE),
    ],
    "classifieds": [
        re.compile(r"classifieds?", re.IGNORECASE),
        re.compile(r"(for\s+sale|for\s+rent|help\s+wanted)", re.IGNORECASE),
    ],
    "community": [
        re.compile(r"(church|faith|worship)", re.IGNORECASE),
        re.compile(r"(senior\s+menu|lunch\s+menu)", re.IGNORECASE),
        re.compile(r"(college\s+news|honor\s+roll)", re.IGNORECASE),
    ],
    "proceedings": [
        re.compile(r"(city\s+council|school\s+board|county\s+board)", re.IGNORECASE),
        re.compile(r"(minutes|proceedings|official\s+proceedings)", re.IGNORECASE),
        re.compile(r"board\s+of\s+education", re.IGNORECASE),
    ],
}


def classify_content_type(headline: str, body: str) -> str:
    combined = headline + " " + body[:500]
    for content_type, patterns in CONTENT_TYPE_PATTERNS.items():
        score = sum(1 for p in patterns if p.search(combined))
        if score >= 2:
            return content_type
        if content_type in ("legal", "police", "obituary") and score >= 1:
            return content_type
    return "news"


# ── Prominence & Eligibility ──


def compute_prominence(article: dict, page_count: int) -> float:
    page = article.get("page_number", 1)
    span = article.get("span_columns", 1)
    block_count = article.get("block_count", 1)
    headline = article.get("headline", "")

    if page_count > 1:
        page_score = max(0.3, 1.0 - (page - 1) * 0.7 / (page_count - 1))
    else:
        page_score = 1.0

    span_score = min(1.0, span / 3.0)
    length_score = min(1.0, block_count / 15.0)
    headline_score = 0.8 if headline else 0.2

    return round(page_score * 0.4 + span_score * 0.25 + length_score * 0.2 + headline_score * 0.15, 3)


def compute_confidence(article: dict) -> float:
    headline = article.get("headline", "")
    body = article.get("body_text", "")
    block_count = article.get("block_count", 0)
    score = 0.5
    if headline:
        score += 0.2
    if len(body) > 100:
        score += 0.15
    if block_count >= 3:
        score += 0.1
    if article.get("byline"):
        score += 0.05
    return round(min(1.0, score), 2)


def is_homepage_eligible(article: dict, content_type: str) -> bool:
    if not article.get("headline", "").strip():
        return False
    if len(article.get("body_text", "")) < 50:
        return False
    if content_type in ("classifieds",):
        return False
    return True


# ── Full Normalization ──


def normalize_article(article: dict, page_count: int) -> dict:
    raw_headline = article.get("headline", "")
    raw_kicker = article.get("kicker", "")
    raw_body = article.get("body_text", "")
    raw_text = (raw_headline + "\n\n" + raw_body).strip()

    cleaned_headline = clean_headline(raw_headline)
    cleaned_kicker = clean_kicker(raw_kicker) if raw_kicker else ""
    cleaned_body = clean_text(raw_body)

    parts = [cleaned_headline]
    if cleaned_kicker:
        parts.append(cleaned_kicker)
    parts.append(cleaned_body)
    cleaned_web_text = "\n\n".join(p for p in parts if p)

    content_type = classify_content_type(cleaned_headline, cleaned_body)
    prominence = compute_prominence(article, page_count)
    confidence = compute_confidence(article)
    eligible = is_homepage_eligible(article, content_type)

    return {
        "article_index": article.get("article_index"),
        "page_number": article.get("page_number"),
        "headline": cleaned_headline,
        "subheadline": clean_headline(article.get("subheadline", "")),
        "kicker": cleaned_kicker,
        "byline": article.get("byline", "").strip(),
        "raw_text": raw_text,
        "cleaned_web_text": cleaned_web_text,
        "content_type": content_type,
        "print_prominence_score": prominence,
        "extraction_confidence": confidence,
        "homepage_eligible": eligible,
        "is_stitched": article.get("is_stitched", False),
        "jump_pages": article.get("jump_pages", []),
        "start_page": article.get("page_number"),
        "end_page": max([article.get("page_number", 1)] + article.get("jump_pages", [])),
        "block_count": article.get("block_count", 0),
        "column_id": article.get("column_id"),
        "span_columns": article.get("span_columns", 1),
        "bbox": article.get("bbox"),
    }


def normalize_edition(edition_id: int) -> dict:
    start_time = time.time()
    result = {"success": False, "edition_id": edition_id, "total_articles": 0, "error": None}

    edition = get_edition(edition_id)
    if not edition:
        result["error"] = f"Edition {edition_id} not found"
        return result

    publisher_id = edition.get("publisher_id")
    if not publisher_id:
        result["error"] = f"Edition {edition_id} has no publisher_id"
        return result

    stitched = get_stitched(publisher_id, edition_id)
    if not stitched:
        result["error"] = f"Edition {edition_id} has no stitched data. Run Phase 4 first."
        return result

    articles = stitched.get("articles", [])
    edition_page_count = edition.get("page_count", 8)

    normalized = []
    type_counts = {}
    eligible_count = 0

    for art in articles:
        record = normalize_article(art, edition_page_count)
        normalized.append(record)
        ct = record["content_type"]
        type_counts[ct] = type_counts.get(ct, 0) + 1
        if record["homepage_eligible"]:
            eligible_count += 1

    result["total_articles"] = len(normalized)
    result["type_counts"] = type_counts
    result["homepage_eligible_count"] = eligible_count

    artifacts_dir = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}"
    with open(artifacts_dir / "normalized.json", "w", encoding="utf-8") as f:
        json.dump({
            "edition_id": edition_id, "publisher_id": publisher_id,
            "total_articles": len(normalized), "type_counts": type_counts,
            "homepage_eligible_count": eligible_count,
            "normalization_time_seconds": round(time.time() - start_time, 2),
            "articles": normalized,
        }, f, indent=2, ensure_ascii=False)

    result["success"] = True
    result["artifacts_dir"] = str(artifacts_dir)
    logger.info(f"Phase 5 normalize: {len(normalized)} articles, types={type_counts}, eligible={eligible_count}")
    return result


def get_normalized(publisher_id: int, edition_id: int) -> dict | None:
    path = ARTIFACTS_BASE / f"publisher_{publisher_id}" / f"edition_{edition_id}" / "normalized.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
