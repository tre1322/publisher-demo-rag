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
    # Standalone "• BACK PAGE" or "• PAGE N" left over after keyword stripping
    re.compile(r"[•·\uf06e\u2022]\s*B\s*A\s*C\s*K\s*P\s*A\s*G\s*E\s*", re.IGNORECASE),
    re.compile(r"[•·\uf06e\u2022]\s*P\s*A\s*G\s*E\s*\d+\s*", re.IGNORECASE),
]
# Soft hyphens (U+00AD) are inserted where a word is broken across a column
# line. These should always be rejoined. Regular hyphens (-) at line breaks
# may be compound words (three-fourths) and should NOT be removed.
# First pass: only lowercase continuation (safe before pull quote removal)
SOFT_HYPHEN_REJOIN = re.compile(r"(\w)\u00AD\s*\n?\s*([a-z])")
# Second pass: also uppercase (for MnDOT etc., safe after pull quotes removed)
SOFT_HYPHEN_REJOIN_ALL = re.compile(r"(\w)\u00AD\s*\n?\s*([a-zA-Z])")
# Regular hyphen at a line break: keep the hyphen, just drop the newline.
# "three-\nfourths" → "three-fourths" (not "threefourths")
HARD_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n\s*([a-z])")
PDF_ARTIFACTS = re.compile(r"[\uf06e\uf06d\uf0b7\u2009\u200b\u200c\u200d]")
# Letter-spaced mastheads that may slip past block classification
LETTERSPACED_MASTHEAD = re.compile(
    r"^[A-Zn\s]*[A-Z]\s+[A-Z]\s+[A-Z]\s+[A-Z][A-Z\s,\d]*$", re.MULTILINE
)

# Ad-like lines that contaminate article text from adjacent ads on back pages.
# Each pattern removes an entire paragraph if it matches.
AD_LINE_PATTERNS = [
    re.compile(r"^\d{3}[- ]\d{3}[- ]\d{4}\b"),  # Phone number at start of line
    re.compile(r"^CALL\s+\d{3}[- ]\d{3}[- ]\d{4}", re.IGNORECASE),  # "CALL 507-822-3077"
    re.compile(r"^\d+\s+\w+\s+(Avenue|Ave|Street|St|Drive|Dr|Road|Rd)\b.*\d{5}", re.IGNORECASE),  # Address with zip
    re.compile(r"^Find out more about us", re.IGNORECASE),
    re.compile(r"^Like us on\b", re.IGNORECASE),
    re.compile(r"^Follow us on\b", re.IGNORECASE),
    re.compile(r"^Visit us at\b", re.IGNORECASE),
    re.compile(r"^www\.\S+\.(com|net|org)", re.IGNORECASE),  # URLs
    re.compile(r"^(Submit a photo|graduation information)", re.IGNORECASE),
    re.compile(r"hearing healthcare needs.*Call", re.IGNORECASE),
    re.compile(r"^\(AI-?\d*\)\s*$"),  # AI metadata tags like "(AI-2)"
    re.compile(r"^\d{1,2}\s+\w+$"),  # Bare "13 Windom" map/furniture artifacts
]


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

    # 3. Rejoin soft hyphens (first pass — catches most cases)
    body = SOFT_HYPHEN_REJOIN.sub(r"\1\2", body)

    # 3a. Fix regular hyphens at line breaks: keep hyphen, drop newline
    body = HARD_HYPHEN_LINEBREAK.sub(r"\1-\2", body)

    # 3.5. Strip ad-like paragraphs that contaminate from adjacent ads
    body = _strip_ad_paragraphs(body)

    # 3.6. Strip letter-spaced mastheads that leaked through block classification
    body = LETTERSPACED_MASTHEAD.sub("", body)

    # 3.7. Deduplicate pull quotes (decorative large-font quotes that repeat body text)
    body = _strip_pull_quotes(body)

    # 3.8. Rejoin soft hyphens (second pass — catches cases where a pull quote
    # was between the hyphenated word halves, now removed. Also handles
    # uppercase continuations like MnDOT.)
    body = SOFT_HYPHEN_REJOIN_ALL.sub(r"\1\2", body)

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
    # Fix missing space at paragraph boundaries where two words got concatenated.
    # E.g. "extremely\n\nsolid" collapsed to "extremelysolid" — insert space
    # between a lowercase letter and a lowercase letter across paragraph joins.
    body = re.sub(r"([a-z])(\n\n)([a-z])", r"\1 \3", body)
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


def _strip_ad_paragraphs(text: str) -> str:
    """Remove paragraphs that look like ad content (phone numbers, addresses, etc.).

    Operates on the double-newline-separated block structure before newline
    collapsing, so each "paragraph" is still a raw block from cell claiming.
    """
    blocks = text.split("\n\n")
    cleaned = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        # Check each line within the block against ad patterns
        lines = stripped.split("\n")
        is_ad = False
        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue
            for pat in AD_LINE_PATTERNS:
                if pat.search(line_clean):
                    is_ad = True
                    break
            if is_ad:
                break
        if not is_ad:
            cleaned.append(block)
    return "\n\n".join(cleaned)


_PULL_QUOTE_ATTRIB = re.compile(
    r"^[A-Z]{2,}\s+[A-Z]{2,}.*(?:chair|director|chief|mayor|commissioner|"
    r"superintendent|president|manager|sheriff|attorney|officer|editor|"
    r"pastor|coach|principal|coordinator|board|county|city|school|district)",
    re.IGNORECASE,
)


def _strip_pull_quotes(text: str) -> str:
    """Remove pull quote paragraphs that duplicate body text.

    Pull quotes are decorative reprints of a sentence from the article,
    typically displayed in large font. After extraction they appear as
    paragraphs that duplicate text already present elsewhere in the article.

    Also removes pull-quote attribution lines (e.g. "TOM APPEL County Board Chair").
    """
    blocks = text.split("\n\n")
    if len(blocks) < 3:
        return text

    def _norm(t):
        t = re.sub(r"[\"'\u201c\u201d\u2018\u2019\u00ab\u00bb]", "", t)
        t = re.sub(r"\s+", " ", t).strip().lower()
        return t

    block_norms = [_norm(b) for b in blocks]
    to_remove = set()

    for i, block in enumerate(blocks):
        stripped = block.strip()
        norm_i = block_norms[i]
        if not norm_i:
            continue

        # Remove pull-quote attribution lines: "TOM APPEL County Board Chair"
        # Flatten newlines first since column wrapping may split the line
        flat = re.sub(r"\s+", " ", stripped).strip()
        if len(flat) < 80 and _PULL_QUOTE_ATTRIB.match(flat):
            to_remove.add(i)
            continue

        # Skip very short or very long paragraphs
        if len(norm_i) < 30 or len(norm_i) > 300:
            continue

        # Check if this paragraph is a duplicate of (or contained in) another paragraph.
        # Pull quotes duplicate article body text — keep whichever appears first,
        # remove later duplicates.
        for j, other_norm in enumerate(block_norms):
            if j >= i or j in to_remove:
                continue
            if not other_norm or len(other_norm) < 30:
                continue
            # Exact or near-exact duplicate: remove the later one
            if norm_i == other_norm:
                to_remove.add(i)
                break
            # This paragraph's text is contained inside an earlier, longer one
            if len(other_norm) > len(norm_i) and norm_i in other_norm:
                to_remove.add(i)
                break
            # The earlier paragraph is contained in this one — keep this one,
            # but only if the earlier one is short (pull quote candidate)
            if len(other_norm) < 300 and other_norm in norm_i and len(other_norm) < len(norm_i):
                to_remove.add(j)

    if to_remove:
        blocks = [b for idx, b in enumerate(blocks) if idx not in to_remove]

    return "\n\n".join(blocks)


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
