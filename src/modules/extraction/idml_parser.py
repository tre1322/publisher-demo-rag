"""IDML extraction: parse Adobe InDesign Markup files for article content.

IDML files are ZIP archives containing XML that preserves the complete
structural data from InDesign — stories (threaded text frames), paragraph
styles, and page layout. This eliminates the need for jump detection,
column analysis, and text normalization required by PDF extraction.

Each "Story" in InDesign is a single text flow that may span multiple
text frames across pages. The full article text is in one Story object
with paragraph styles identifying headlines, bylines, body copy, etc.
"""

import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Paragraph style classification rules.
# Map InDesign paragraph style names to semantic roles.
# These are matched case-insensitively using substring matching.
STYLE_ROLES = {
    # Headlines
    "headline": "headline",
    "head": "headline",
    "banner": "headline",
    "title": "headline",
    "flag": "headline",
    # Subheadlines
    "subhead": "subhead",
    "deck": "subhead",
    "summary": "subhead",
    # Bylines
    "byline": "byline",
    # Body copy
    "body": "body",
    "copy": "body",
    "text": "body",
    "nimrod": "body",
    # Captions
    "cutline": "caption",
    "caption": "caption",
    "photo credit": "caption",
    # Pull quotes
    "drop quote": "pullquote",
    "pull quote": "pullquote",
    "blockquote": "pullquote",
    # Furniture / skip
    "paragraph break": "furniture",
    "folio": "furniture",
    "page number": "furniture",
}


def _classify_style(style_name: str) -> str:
    """Classify a paragraph style name into a semantic role."""
    lower = style_name.lower()
    for pattern, role in STYLE_ROLES.items():
        if pattern in lower:
            return role
    # Default: if no match, treat as body
    if style_name in ("NormalParagraphStyle", "Normal", "[No paragraph style]"):
        return "body"
    return "body"


def _extract_story_text(story_path: str) -> dict | None:
    """Extract text and metadata from a single IDML Story XML file.

    Returns dict with headline, byline, subheads, body_text, and style info,
    or None if the story has no meaningful content.
    """
    tree = ET.parse(story_path)
    root = tree.getroot()

    paragraphs = []  # list of (role, text)

    for psr in root.iter("ParagraphStyleRange"):
        style = psr.get("AppliedParagraphStyle", "").split("/")[-1]
        role = _classify_style(style)

        text_parts = []
        for elem in psr.iter():
            if elem.tag == "Content":
                text_parts.append(elem.text or "")
            elif elem.tag == "Br":
                text_parts.append("\n")

        text = "".join(text_parts).strip()
        if text:
            paragraphs.append((role, style, text))

    if not paragraphs:
        return None

    # Assemble article components from paragraph roles
    headline = ""
    subheadline = ""
    byline = ""
    body_parts = []
    content_type = "unknown"
    has_body = False

    for role, style, text in paragraphs:
        if role == "headline" and not headline:
            headline = text.replace("\n", " ").strip()
        elif role == "subhead":
            if not subheadline and not has_body:
                # First subhead before body = deck/summary
                subheadline = text.replace("\n", " ").strip()
                # Check if it's actually a byline
                if re.match(r"^[Bb]y\s+[A-Z]", subheadline):
                    byline = re.sub(r"^[Bb]y\s+", "", subheadline).strip()
                    subheadline = ""
            else:
                # Subhead within body = section header
                body_parts.append(f"\n{text.strip()}\n")
        elif role == "byline":
            byline = re.sub(r"^[Bb]y\s+", "", text.replace("\n", " ").strip())
        elif role == "body":
            has_body = True
            # Check first paragraph for byline pattern
            if not byline and not body_parts and re.match(r"^[Bb]y\s+[A-Z]", text.strip()):
                byline = re.sub(r"^[Bb]y\s+", "", text.strip().split("\n")[0])
                remainder = "\n".join(text.strip().split("\n")[1:]).strip()
                if remainder:
                    body_parts.append(remainder)
            else:
                body_parts.append(text.strip())
        elif role == "caption":
            content_type = "caption"
            body_parts.append(text.strip())
        elif role == "pullquote":
            # Skip pull quotes — they duplicate body text
            pass
        elif role == "furniture":
            pass  # Skip furniture

    body_text = "\n\n".join(body_parts).strip()

    # Skip very short content (labels, page numbers, etc.)
    total_text = headline + body_text
    if len(total_text) < 20:
        return None

    # Determine content type from styles and content
    if content_type == "caption":
        pass  # already set
    elif any(role == "body" for role, _, _ in paragraphs):
        content_type = "article"
    else:
        content_type = "other"

    story_id = Path(story_path).stem  # e.g., "Story_u1ced4"

    return {
        "story_id": story_id,
        "headline": headline,
        "subheadline": subheadline,
        "byline": byline,
        "body_text": body_text,
        "content_type": content_type,
        "paragraph_count": len([p for p in paragraphs if p[0] == "body"]),
        "char_count": len(body_text),
        "styles_used": list(set(s for _, s, _ in paragraphs)),
    }


def _get_text_frame_positions(spreads_dir: str) -> dict[str, list[dict]]:
    """Extract text frame positions from Spread XML files.

    Returns dict mapping story_id -> list of frame positions (x, y, w, h).
    """
    frames: dict[str, list[dict]] = {}

    for fname in os.listdir(spreads_dir):
        if not fname.endswith(".xml"):
            continue
        tree = ET.parse(os.path.join(spreads_dir, fname))

        for tf in tree.getroot().iter("TextFrame"):
            story_ref = tf.get("ParentStory", "")
            transform = tf.get("ItemTransform", "")

            points = []
            for pp in tf.iter("PathPointType"):
                anchor = pp.get("Anchor", "")
                if anchor:
                    parts = anchor.split()
                    if len(parts) == 2:
                        points.append((float(parts[0]), float(parts[1])))

            if not points or not story_ref:
                continue

            x_min = min(p[0] for p in points)
            y_min = min(p[1] for p in points)
            x_max = max(p[0] for p in points)
            y_max = max(p[1] for p in points)

            # Apply transform translation
            t_parts = transform.split()
            if len(t_parts) == 6:
                tx, ty = float(t_parts[4]), float(t_parts[5])
                x_min += tx; x_max += tx
                y_min += ty; y_max += ty

            frames.setdefault(story_ref, []).append({
                "x": x_min, "y": y_min,
                "x2": x_max, "y2": y_max,
                "w": x_max - x_min, "h": y_max - y_min,
            })

    return frames


def _match_headlines_to_bodies(
    stories: dict[str, dict],
    frame_positions: dict[str, list[dict]],
) -> None:
    """Match headline-only stories to body stories using frame proximity.

    InDesign often puts headlines in separate text frames from the body.
    This finds headline frames positioned directly above body frames and
    assigns the headline text to the body story.
    """
    # Identify headline-only stories vs body stories.
    # Headlines in InDesign are often short text frames with [No paragraph style]
    # positioned directly above the body text frame. Detect them by:
    # 1. Named headline style, OR
    # 2. Short text (< 150 chars) in a small text frame (height < 80pt)
    headline_stories = {}
    body_stories = {}

    for sid, story in stories.items():
        if not story:
            continue
        has_headline_style = any(
            _classify_style(s) == "headline" for s in story.get("styles_used", [])
        )
        # Check if the text frame is small (headline-sized)
        sid_frames = frame_positions.get(sid, [])
        is_small_frame = sid_frames and max(f["h"] for f in sid_frames) < 150

        # Filter out continuation headers and jump references
        story_text = (story.get("body_text") or story.get("headline") or "").lower()
        is_continuation = bool(re.search(r"from\s+page\s*\d|^\w+/\s", story_text))
        is_jump_ref = bool(re.search(r"see\s+\w+\s*[•·]|continued\s+on", story_text))
        is_masthead = bool(re.search(
            r"citizen|star|advocate|observer|wednesday|thursday|friday|monday|tuesday",
            story_text,
        ) and re.search(r"\d{4}|\d+th year|\d+rd year", story_text))
        is_cutline = any("cutline" in s.lower() or "photo credit" in s.lower()
                         for s in story.get("styles_used", []))
        is_boilerplate = bool(re.search(
            r"pica deep|drop quotes in swiss|nick klisch|first name last", story_text))

        if is_continuation or is_jump_ref or is_masthead or is_cutline or is_boilerplate:
            continue  # Skip — not a real headline or article

        if story["char_count"] < 150 and (has_headline_style or is_small_frame):
            headline_stories[sid] = story
        elif story["content_type"] == "article" and story["char_count"] >= 100:
            body_stories[sid] = story

    # For each body story without a headline, find the nearest headline above it.
    # Body stories may have frames on MULTIPLE pages (front + continuation).
    # Match each body frame individually to find the best headline nearby.
    # Sort body stories by frame area (largest first) so the most prominent
    # articles get first pick of headlines
    body_items = sorted(
        body_stories.items(),
        key=lambda item: max(
            (f["w"] * f["h"] for f in frame_positions.get(item[0], [{"w": 0, "h": 0}])),
            default=0,
        ),
        reverse=True,
    )
    used_headlines = set()

    for body_sid, body_story in body_items:

        body_frames = frame_positions.get(body_sid, [])
        if not body_frames:
            continue

        best_headline = None
        best_distance = float("inf")
        best_hl_sid = None

        for bf in body_frames:
            for hl_sid, hl_story in headline_stories.items():
                if hl_sid in used_headlines:
                    continue
                hl_frames = frame_positions.get(hl_sid, [])
                for hf in hl_frames:
                    # Headline must start above or near this body frame
                    if hf["y"] > bf["y"] + 50:
                        continue
                    # Must be on the same "page" (within ~900pt vertically)
                    if abs(hf["y"] - bf["y"]) > 900:
                        continue
                    # Horizontal proximity
                    x_dist = abs(hf["x"] - bf["x"])
                    if x_dist > 500:
                        continue

                    distance = abs(bf["y"] - hf["y"]) + x_dist * 0.2
                    if distance < best_distance:
                        best_distance = distance
                        hl_text = hl_story.get("headline") or hl_story.get("body_text", "")
                        hl_text = hl_text.replace("\n", " ").strip()
                        hl_text = re.sub(r"\s*n\s*Page\s*\d+\s*$", "", hl_text)
                        hl_text = re.sub(r"\s+", " ", hl_text).strip()
                        if hl_text:
                            best_headline = hl_text
                            best_hl_sid = hl_sid

        if best_headline:
            body_story["headline"] = best_headline
            if best_hl_sid:
                used_headlines.add(best_hl_sid)

    # For remaining stories without headlines, use subheadline or first sentence
    for body_story in body_stories.values():
        if not body_story["headline"]:
            if body_story.get("subheadline"):
                body_story["headline"] = body_story["subheadline"]
            elif body_story["body_text"]:
                # Strip "By AUTHOR" prefix before looking for first sentence
                text = re.sub(r"^[Bb]y\s+[A-Z][A-Za-z\s.'-]+\n*", "", body_story["body_text"]).strip()
                first_line = text.split("\n")[0].strip() if text else ""
                if ". " in first_line:
                    body_story["headline"] = first_line[:first_line.index(". ") + 1]
                elif first_line:
                    body_story["headline"] = first_line[:80]


def parse_idml(idml_path: str) -> list[dict]:
    """Parse an IDML file and extract all articles.

    Args:
        idml_path: Path to the .idml file.

    Returns:
        List of article dicts with headline, byline, body_text, etc.
    """
    import tempfile

    idml_path = str(idml_path)
    if not os.path.exists(idml_path):
        raise FileNotFoundError(f"IDML file not found: {idml_path}")

    # Extract IDML (ZIP) to temp directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(idml_path, "r") as z:
            z.extractall(tmp_dir)

        stories_dir = os.path.join(tmp_dir, "Stories")
        if not os.path.isdir(stories_dir):
            raise ValueError(f"No Stories directory in IDML file: {idml_path}")

        spreads_dir = os.path.join(tmp_dir, "Spreads")

        # Parse all stories
        all_stories: dict[str, dict | None] = {}
        for fname in sorted(os.listdir(stories_dir)):
            if not fname.endswith(".xml"):
                continue
            story_path = os.path.join(stories_dir, fname)
            sid = fname.replace("Story_", "").replace(".xml", "")
            all_stories[sid] = _extract_story_text(story_path)

        # Get text frame positions from spreads
        frame_positions = _get_text_frame_positions(spreads_dir)

        # Match headlines to body stories using proximity
        _match_headlines_to_bodies(
            {k: v for k, v in all_stories.items() if v},
            frame_positions,
        )

        # Collect articles (filter small items)
        articles = []
        for sid, story in all_stories.items():
            if story is None:
                continue
            if story["content_type"] != "article":
                continue
            if story["char_count"] < 100:
                continue  # Skip tiny text blocks
            articles.append(story)

        logger.info(
            f"IDML parsed: {len(articles)} articles from {idml_path}"
        )

        # Sort articles by text length (longest first = most prominent)
        articles.sort(key=lambda a: -a["char_count"])

        return articles


def ingest_idml_edition(
    idml_path: str,
    publisher_name: str,
    edition_date: str | None = None,
) -> dict:
    """Parse an IDML file and write articles to all system destinations.

    Uses the shared write layer to write to articles table, content_items,
    ChromaDB, and homepage batch — ensuring IDML-extracted articles are
    fully visible across the chatbot, homepage, and admin panel.

    Args:
        idml_path: Path to the .idml file.
        publisher_name: Publisher name (e.g., "Cottonwood County Citizen").
        edition_date: Edition date in YYYY-MM-DD format.

    Returns:
        Dict with ingestion results.
    """
    from src.modules.editions.database import insert_edition
    from src.modules.extraction.shared_write_layer import write_articles_to_all
    from src.modules.publishers.database import get_publisher_by_name

    # Resolve publisher
    pub_record = get_publisher_by_name(publisher_name)
    publisher_id = pub_record["id"] if pub_record else None

    # Parse IDML
    raw_articles = parse_idml(idml_path)

    # Create edition record
    filename = os.path.basename(idml_path)
    edition_id = insert_edition(
        source_filename=filename,
        publisher_id=publisher_id,
        edition_date=edition_date,
        pdf_path=idml_path,
        upload_status="uploaded",
        extraction_status="extracted",
    )

    # Convert to StandardArticle format
    standard_articles = []
    for art in raw_articles:
        if art["content_type"] != "article":
            continue
        if not art["body_text"] or len(art["body_text"]) < 30:
            continue

        standard_articles.append({
            "headline": art["headline"],
            "subheadline": art.get("subheadline", ""),
            "byline": art.get("byline", ""),
            "body_text": art["body_text"],
            "content_type": "news",  # inferred by shared write layer from headline
            "start_page": None,  # IDML doesn't track page numbers directly
            "jump_pages": [],
            "is_stitched": False,  # no jumps in IDML — stories are already complete
            "extraction_confidence": 0.98,
            "source_pipeline": "idml",
        })

    # Write to all destinations via shared layer
    write_result = write_articles_to_all(
        articles=standard_articles,
        edition_id=edition_id,
        publisher_id=publisher_id,
        publisher_name=publisher_name,
        edition_date=edition_date,
        source_filename=filename,
    )

    logger.info(
        f"IDML ingestion complete: {write_result['articles_written']} articles, "
        f"{write_result['chunks_indexed']} chunks from {filename}"
    )

    return {
        "success": True,
        "edition_id": edition_id,
        "filename": filename,
        "total_stories": len(raw_articles),
        "articles_inserted": write_result["articles_written"],
        "chunks_indexed": write_result["chunks_indexed"],
        "content_items_written": write_result["content_items_written"],
    }
