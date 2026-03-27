"""Text normalization: clean raw extracted text into readable article text.

Applies these cleanup passes in order:
1. Strip continuation headers (KEYWORD/ subtitle, FROM PAGE 1)
2. Strip jump references (SEE KEYWORD • BACK PAGE)
3. Rejoin soft hyphens (respon- sible -> responsible)
4. Collapse column newlines (single \n -> space, preserve double \n)
5. Strip pull quotes (large decorative quotes that duplicate body text)
6. Strip PDF artifacts (private-use Unicode, thin spaces)
7. Normalize whitespace
"""

import re
import logging

logger = logging.getLogger(__name__)

# Patterns for text cleanup
CONTINUATION_HEADER_PATTERN = re.compile(
    r"^[A-Z]{2,}\s*/\s*[^\n]*\n?", re.MULTILINE
)
FROM_PAGE_PATTERN = re.compile(
    r"F\s*R\s*O\s*M\s+P\s*A\s*G\s*E\s*\d+\s*", re.IGNORECASE
)
JUMP_REF_PATTERNS = [
    re.compile(r"S\s*E\s*E\s+\w+\s*[•·\u2009\uf06e]\s*(?:B\s*A\s*C\s*K\s*)?P\s*A\s*G\s*E\s*\d*", re.IGNORECASE),
    re.compile(r"SEE\s+\w+\s*[•·\uf06e]\s*(?:BACK\s+)?PAGE\s*\d*", re.IGNORECASE),
    re.compile(r"[Cc]ontinued\s+on\s+[Pp]age\s*\d+"),
    re.compile(r"[Cc]ontinued\s+from\s+[Pp]age\s*\d+"),
]
SOFT_HYPHEN_PATTERN = re.compile(r"(\w)[\u00AD\-]\s+([a-z])")
PDF_ARTIFACTS = re.compile(r"[\uf06e\uf06d\uf0b7\u2009\u200b\u200c\u200d]")


def normalize_article(article: dict) -> dict:
    """Clean up article text for readable output.

    Args:
        article: Dict with at least 'body_text', 'headline', 'byline'.

    Returns:
        Same dict with cleaned text fields.
    """
    body = article.get("body_text", "")

    if not body:
        return article

    # 1. Strip continuation headers
    body = CONTINUATION_HEADER_PATTERN.sub("", body)
    body = FROM_PAGE_PATTERN.sub("", body)

    # 2. Strip jump references
    for pat in JUMP_REF_PATTERNS:
        body = pat.sub("", body)

    # 3. Rejoin soft hyphens
    body = SOFT_HYPHEN_PATTERN.sub(r"\1\2", body)

    # 4. Strip PDF artifacts
    body = PDF_ARTIFACTS.sub("", body)

    # 5. Collapse column newlines
    # Preserve double newlines (paragraph breaks) but collapse single newlines to spaces
    body = _collapse_newlines(body)

    # 6. Clean up whitespace
    body = re.sub(r"  +", " ", body)  # multiple spaces to single
    body = re.sub(r"\n ", "\n", body)  # leading space after newline
    body = re.sub(r" \n", "\n", body)  # trailing space before newline
    body = re.sub(r"\n{3,}", "\n\n", body)  # max double newline
    body = body.strip()

    # 7. Clean headline
    headline = article.get("headline", "")
    headline = headline.replace("\n", " ").strip()
    headline = re.sub(r"  +", " ", headline)
    headline = PDF_ARTIFACTS.sub("", headline)

    # 8. Clean byline
    byline = article.get("byline", "")
    byline = byline.replace("\n", " ").strip()
    byline = re.sub(r"^[Bb]y\s+", "", byline)
    byline = _title_case_name(byline)

    article["body_text"] = body
    article["headline"] = headline
    article["byline"] = byline

    return article


def _collapse_newlines(text: str) -> str:
    """Collapse column-wrap newlines to spaces, preserve real paragraph breaks.

    The input text uses \n\n to separate blocks (structural boundaries from the
    cell-claiming phase) and single \n for column line-wraps within a block.

    Strategy:
    1. Split on \n\n to get structural blocks (these are real paragraph candidates)
    2. Within each block, collapse single \n to spaces (column line-wraps)
    3. Rejoin blocks with \n\n to preserve paragraph structure
    """
    # Split on double-newline to get structural blocks
    blocks = text.split("\n\n")
    cleaned_blocks = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Within a block, single newlines are column line-wraps → collapse to space
        lines = block.split("\n")
        merged = " ".join(line.strip() for line in lines if line.strip())
        if merged:
            cleaned_blocks.append(merged)

    return "\n\n".join(cleaned_blocks)


def _title_case_name(name: str) -> str:
    """Convert a name to title case (JOHN SMITH -> John Smith)."""
    if not name:
        return name
    # If all uppercase, title-case it
    if name == name.upper():
        return name.title()
    return name


def normalize_all_articles(articles: list[dict]) -> list[dict]:
    """Normalize all articles in a list."""
    for article in articles:
        normalize_article(article)
    return articles
