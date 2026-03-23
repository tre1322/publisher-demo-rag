"""Newspaper PDF parser with layout-aware article reconstruction.

Uses PyMuPDF (fitz) for page geometry and text block extraction.
Detects columns, reading order, article boundaries, ad blocks,
and continuation/jump patterns across pages.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ── Jump / continuation patterns ──

# Forward jumps: "Continued on Page A6", "See Page 5", etc.
FORWARD_JUMP_PATTERNS = [
    re.compile(
        r"(?:continued|continues|continued?\s+on|turn\s+to|see(?:\s+story)?(?:\s+on)?|jump\s+to)"
        r"\s+(?:page\s+)?([A-Za-z]?\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:see|turn\s+to)\s+(?:page\s+)?([A-Za-z]?\d+)",
        re.IGNORECASE,
    ),
]

# Backward jumps: "Continued from Page 1", "From A1", etc.
BACKWARD_JUMP_PATTERNS = [
    re.compile(
        r"(?:continued|continues|continuing)\s+from\s+(?:page\s+)?([A-Za-z]?\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"from\s+(?:page\s+)?([A-Za-z]?\d+)\b",
        re.IGNORECASE,
    ),
]

# Pattern to clean jump artifacts from final text
JUMP_ARTIFACT_PATTERN = re.compile(
    r"(?:continued|continues|continuing|continued?\s+on|turn\s+to|see(?:\s+story)?(?:\s+on)?|"
    r"jump\s+to|from)\s+(?:page\s+)?[A-Za-z]?\d+\s*",
    re.IGNORECASE,
)

# ── Ad detection patterns ──

AD_INDICATORS = [
    re.compile(r"\b(?:call|dial)\s+\(?\d{3}\)?\s*[-.]?\s*\d{3}\s*[-.]?\s*\d{4}", re.IGNORECASE),
    re.compile(r"\bwww\.\S+\.\w{2,}", re.IGNORECASE),
    re.compile(r"\bsale\b.*\b(?:\$|percent|%|off)\b", re.IGNORECASE),
    re.compile(r"\$\d+[\.,]?\d*\s*(?:off|each|per|/)", re.IGNORECASE),
    re.compile(r"\b(?:coupon|discount|special\s+offer|limited\s+time|now\s+hiring)\b", re.IGNORECASE),
    re.compile(r"\b(?:visit\s+us|hours|open\s+daily|mon(?:day)?[\s-]+(?:fri|sat))\b", re.IGNORECASE),
]

# Headlines: large font, short text, often all-caps or title case
HEADLINE_MIN_FONT_SIZE = 12.0
BYLINE_PATTERNS = [
    re.compile(r"^by\s+[A-Z]", re.IGNORECASE),
    re.compile(r"^staff\s+(?:writer|report)", re.IGNORECASE),
    re.compile(r"^(?:associated\s+press|ap|reuters|upi)\b", re.IGNORECASE),
]


@dataclass
class TextBlock:
    """A text block extracted from a PDF page with position info."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_num: int  # 0-indexed
    font_size: float = 0.0
    is_bold: bool = False

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class PageRegion:
    """A classified region on a page."""

    blocks: list[TextBlock]
    page_num: int
    region_type: str  # "article_start", "article_body", "advertisement", "continuation"
    headline: str = ""
    byline: str = ""
    forward_jump_target: str | None = None
    backward_jump_source: str | None = None


@dataclass
class ParsedArticle:
    """A reconstructed article from one or more page regions."""

    headline: str
    byline: str
    body_parts: list[str] = field(default_factory=list)
    start_page: int = 0  # 1-indexed for display
    continuation_pages: list[int] = field(default_factory=list)
    section: str = ""

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.body_parts)

    @property
    def cleaned_text(self) -> str:
        """Full text with jump artifacts removed."""
        text = self.full_text
        text = JUMP_ARTIFACT_PATTERN.sub("", text)
        # Clean up extra whitespace from removals
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


@dataclass
class ParsedAd:
    """An advertisement extracted from a page."""

    text: str
    page_num: int  # 1-indexed
    advertiser_name: str = ""


@dataclass
class EditionParseResult:
    """Complete parse result for a newspaper edition."""

    articles: list[ParsedArticle]
    advertisements: list[ParsedAd]
    page_count: int
    warnings: list[str] = field(default_factory=list)


class NewspaperParser:
    """Parses newspaper PDFs into structured articles and advertisements."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def parse(self, pdf_path: Path) -> EditionParseResult:
        """Parse a full newspaper PDF into articles and ads.

        Args:
            pdf_path: Path to the newspaper PDF.

        Returns:
            EditionParseResult with reconstructed articles and ads.
        """
        self.warnings = []

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as e:
            logger.error(f"Failed to open PDF: {e}")
            return EditionParseResult(
                articles=[], advertisements=[], page_count=0,
                warnings=[f"Failed to open PDF: {e}"],
            )

        page_count = len(doc)
        logger.info(f"Parsing {page_count}-page PDF: {pdf_path.name}")

        # Check if text-extractable
        first_page_text = doc[0].get_text() if page_count > 0 else ""
        if not first_page_text.strip():
            self.warnings.append(
                "PDF appears to be image-based (no extractable text). "
                "OCR fallback not yet implemented."
            )
            logger.warning("Image-based PDF detected, no text extractable")
            doc.close()
            return EditionParseResult(
                articles=[], advertisements=[], page_count=page_count,
                warnings=self.warnings,
            )

        # Step 1: Extract text blocks from all pages
        all_blocks: dict[int, list[TextBlock]] = {}
        for page_idx in range(page_count):
            blocks = self._extract_page_blocks(doc[page_idx], page_idx)
            all_blocks[page_idx] = blocks
            logger.debug(f"Page {page_idx + 1}: {len(blocks)} text blocks")

        doc.close()

        # Step 2: Classify regions on each page
        all_regions: list[PageRegion] = []
        for page_idx in range(page_count):
            regions = self._classify_page_regions(all_blocks.get(page_idx, []), page_idx)
            all_regions.extend(regions)

        # Step 3: Separate articles from ads
        article_regions = [r for r in all_regions if r.region_type != "advertisement"]
        ad_regions = [r for r in all_regions if r.region_type == "advertisement"]

        # Step 4: Reconstruct articles across jumps
        articles = self._reconstruct_articles(article_regions)

        # Step 5: Build ad records
        advertisements = self._build_ads(ad_regions)

        logger.info(
            f"Parsed: {len(articles)} articles, {len(advertisements)} ads "
            f"from {page_count} pages"
        )

        return EditionParseResult(
            articles=articles,
            advertisements=advertisements,
            page_count=page_count,
            warnings=self.warnings,
        )

    def _extract_page_blocks(self, page: fitz.Page, page_idx: int) -> list[TextBlock]:
        """Extract text blocks with position and font info from a page."""
        blocks = []

        # Use get_text("dict") for detailed font info
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # Skip image blocks
                continue

            block_text_parts = []
            max_font_size = 0.0
            has_bold = False

            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                    font_size = span.get("size", 0)
                    if font_size > max_font_size:
                        max_font_size = font_size
                    font_name = span.get("font", "").lower()
                    if "bold" in font_name or "black" in font_name:
                        has_bold = True
                if line_text.strip():
                    block_text_parts.append(line_text.strip())

            text = "\n".join(block_text_parts).strip()
            if not text:
                continue

            bbox = block.get("bbox", (0, 0, 0, 0))
            blocks.append(TextBlock(
                text=text,
                x0=bbox[0],
                y0=bbox[1],
                x1=bbox[2],
                y1=bbox[3],
                page_num=page_idx,
                font_size=max_font_size,
                is_bold=has_bold,
            ))

        # Sort by reading order: top to bottom, left to right within similar Y
        blocks.sort(key=lambda b: (round(b.y0 / 20) * 20, b.x0))
        return blocks

    def _classify_page_regions(
        self, blocks: list[TextBlock], page_idx: int
    ) -> list[PageRegion]:
        """Classify blocks on a page into article starts, bodies, ads, continuations."""
        if not blocks:
            return []

        regions: list[PageRegion] = []
        current_blocks: list[TextBlock] = []
        current_type = "article_body"
        current_headline = ""
        current_byline = ""
        forward_jump = None
        backward_jump = None

        for block in blocks:
            text = block.text.strip()
            if not text:
                continue

            # Check if this block is an ad
            if self._is_ad_block(block):
                # Flush current article region if any
                if current_blocks:
                    regions.append(PageRegion(
                        blocks=current_blocks,
                        page_num=page_idx,
                        region_type=current_type,
                        headline=current_headline,
                        byline=current_byline,
                        forward_jump_target=forward_jump,
                        backward_jump_source=backward_jump,
                    ))
                    current_blocks = []
                    current_headline = ""
                    current_byline = ""
                    forward_jump = None
                    backward_jump = None

                regions.append(PageRegion(
                    blocks=[block],
                    page_num=page_idx,
                    region_type="advertisement",
                ))
                current_type = "article_body"
                continue

            # Check for headline (article start)
            if self._is_headline(block):
                # Flush previous region
                if current_blocks:
                    regions.append(PageRegion(
                        blocks=current_blocks,
                        page_num=page_idx,
                        region_type=current_type,
                        headline=current_headline,
                        byline=current_byline,
                        forward_jump_target=forward_jump,
                        backward_jump_source=backward_jump,
                    ))
                current_blocks = [block]
                current_type = "article_start"
                current_headline = text
                current_byline = ""
                forward_jump = None
                backward_jump = None
                continue

            # Check for byline right after headline
            if current_type == "article_start" and not current_byline:
                if any(p.match(text) for p in BYLINE_PATTERNS):
                    current_byline = text
                    current_blocks.append(block)
                    continue

            # Check for backward jump (continuation from another page)
            bj = self._detect_backward_jump(text)
            if bj and not current_headline:
                current_type = "continuation"
                backward_jump = bj

            # Check for forward jump
            fj = self._detect_forward_jump(text)
            if fj:
                forward_jump = fj

            current_blocks.append(block)

        # Flush remaining
        if current_blocks:
            regions.append(PageRegion(
                blocks=current_blocks,
                page_num=page_idx,
                region_type=current_type,
                headline=current_headline,
                byline=current_byline,
                forward_jump_target=forward_jump,
                backward_jump_source=backward_jump,
            ))

        return regions

    def _is_headline(self, block: TextBlock) -> bool:
        """Determine if a text block is likely a headline."""
        text = block.text.strip()

        # Must be reasonably short
        if len(text) > 200 or len(text) < 5:
            return False

        # Must have larger font or be bold
        if block.font_size >= HEADLINE_MIN_FONT_SIZE:
            return True

        if block.is_bold and block.font_size >= 10.0 and len(text) < 120:
            return True

        return False

    def _is_ad_block(self, block: TextBlock) -> bool:
        """Determine if a text block is likely an advertisement."""
        text = block.text
        matches = sum(1 for pattern in AD_INDICATORS if pattern.search(text))
        # Need at least 2 ad indicators to classify as ad
        return matches >= 2

    def _detect_forward_jump(self, text: str) -> str | None:
        """Detect a forward jump pattern and return the target page."""
        for pattern in FORWARD_JUMP_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        return None

    def _detect_backward_jump(self, text: str) -> str | None:
        """Detect a backward jump pattern and return the source page."""
        for pattern in BACKWARD_JUMP_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        return None

    def _normalize_page_ref(self, ref: str) -> int | None:
        """Convert a page reference like 'A6' or '5' to a 0-indexed page number.

        Simple heuristic: strip letter prefix, parse number, subtract 1.
        """
        cleaned = re.sub(r"^[A-Za-z]+", "", ref)
        try:
            return int(cleaned) - 1
        except ValueError:
            return None

    def _reconstruct_articles(self, regions: list[PageRegion]) -> list[ParsedArticle]:
        """Reconstruct complete articles from page regions, resolving jumps."""
        # Separate article starts from continuations and body regions
        article_starts: list[PageRegion] = []
        continuations: list[PageRegion] = []
        orphan_bodies: list[PageRegion] = []

        for region in regions:
            if region.region_type == "article_start":
                article_starts.append(region)
            elif region.region_type == "continuation":
                continuations.append(region)
            else:
                orphan_bodies.append(region)

        articles: list[ParsedArticle] = []

        for start_region in article_starts:
            body_text = "\n".join(b.text for b in start_region.blocks)
            article = ParsedArticle(
                headline=start_region.headline,
                byline=start_region.byline,
                body_parts=[body_text],
                start_page=start_region.page_num + 1,
            )

            # Follow forward jumps
            if start_region.forward_jump_target:
                target_page = self._normalize_page_ref(start_region.forward_jump_target)
                if target_page is not None:
                    cont = self._find_continuation(
                        continuations, target_page, start_region.page_num
                    )
                    if cont:
                        cont_text = "\n".join(b.text for b in cont.blocks)
                        article.body_parts.append(cont_text)
                        article.continuation_pages.append(target_page + 1)
                        continuations.remove(cont)
                    else:
                        self.warnings.append(
                            f"Jump target page {start_region.forward_jump_target} "
                            f"not found for article: {start_region.headline[:60]}"
                        )

            articles.append(article)

        # Handle orphan continuations that reference a source page
        for cont in continuations:
            if cont.backward_jump_source:
                source_page = self._normalize_page_ref(cont.backward_jump_source)
                # Try to find matching article by start page
                matched = False
                if source_page is not None:
                    for article in articles:
                        if article.start_page == source_page + 1:
                            cont_text = "\n".join(b.text for b in cont.blocks)
                            article.body_parts.append(cont_text)
                            article.continuation_pages.append(cont.page_num + 1)
                            matched = True
                            break
                if not matched:
                    # Create standalone article from continuation
                    cont_text = "\n".join(b.text for b in cont.blocks)
                    if len(cont_text.strip()) > 50:
                        articles.append(ParsedArticle(
                            headline=cont_text[:80].split("\n")[0],
                            byline="",
                            body_parts=[cont_text],
                            start_page=cont.page_num + 1,
                        ))

        # Handle orphan body regions (text without clear article start)
        for body in orphan_bodies:
            body_text = "\n".join(b.text for b in body.blocks)
            if len(body_text.strip()) > 100:
                first_line = body_text.strip().split("\n")[0][:80]
                articles.append(ParsedArticle(
                    headline=first_line,
                    byline="",
                    body_parts=[body_text],
                    start_page=body.page_num + 1,
                ))

        # Filter out very short "articles" (likely fragments)
        articles = [a for a in articles if len(a.cleaned_text) > 50]

        return articles

    def _find_continuation(
        self,
        continuations: list[PageRegion],
        target_page: int,
        source_page: int,
    ) -> PageRegion | None:
        """Find a continuation region on the target page that matches a jump."""
        # Look for continuations on the target page that reference the source
        for cont in continuations:
            if cont.page_num == target_page:
                if cont.backward_jump_source:
                    source_ref = self._normalize_page_ref(cont.backward_jump_source)
                    if source_ref == source_page:
                        return cont
                # If no explicit back-reference, still match by page
                return cont
        return None

    def _build_ads(self, ad_regions: list[PageRegion]) -> list[ParsedAd]:
        """Build advertisement records from ad regions."""
        ads: list[ParsedAd] = []

        for region in ad_regions:
            text = "\n".join(b.text for b in region.blocks).strip()
            if len(text) < 10:
                continue

            # Try to extract advertiser name from first strong line
            lines = text.split("\n")
            advertiser = lines[0].strip()[:100] if lines else "Unknown Advertiser"

            ads.append(ParsedAd(
                text=text,
                page_num=region.page_num + 1,
                advertiser_name=advertiser,
            ))

        return ads
