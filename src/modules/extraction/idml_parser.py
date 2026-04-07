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
# Style rules are checked in order — put more specific patterns first
# to avoid false matches (e.g. "head" matching "subhead").
STYLE_RULES = [
    # Furniture / skip (check first to exclude boilerplate)
    ("paragraph break", "furniture"),
    ("folio", "furniture"),
    ("page number", "furniture"),
    ("what's inside", "furniture"),   # "what's inside headline cit" = sidebar teasers
    ("minor category", "furniture"),  # classified section headers
    ("end of category", "furniture"),
    ("letterhead", "furniture"),
    ("briefly head", "furniture"),    # brief teasers
    # Captions / photo credits
    ("cutline", "caption"),
    ("caption", "caption"),
    ("photo credit", "caption"),
    # Pull quotes (skip — duplicate body text)
    ("drop quote", "pullquote"),
    ("pull quote", "pullquote"),
    ("blockquote", "pullquote"),
    # Subheadlines — check BEFORE "head" to avoid false match
    ("subhead", "subhead"),
    ("deck", "subhead"),
    ("summary deck", "subhead"),
    ("summary", "subhead"),
    # Headlines
    ("headline", "headline"),
    ("banner", "headline"),
    ("flag", "headline"),
    # Bylines
    ("byline", "byline"),
    # Body copy
    ("nimrod body copy", "body"),      # "cit nimrod body copy"
    ("nimrodbodycopy", "body"),
    ("nimrodcopy", "body"),
    ("body text", "body"),             # "body text OA"
    ("body copy", "body"),
    ("body", "body"),
    ("nimrod", "body"),
    ("classified", "body"),
    # Normal/default styles — treat as body
    ("normal", "body"),
]


def _classify_style(style_name: str) -> str:
    """Classify a paragraph style name into a semantic role.

    Uses ordered rules list (STYLE_RULES) — more specific patterns
    are checked first to avoid false matches like 'head' in 'subhead'.
    """
    lower = style_name.lower()
    for pattern, role in STYLE_RULES:
        if pattern in lower:
            return role
    # Default: unstyled text treated as body
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
        # Walk text from this ParagraphStyleRange, handling nested Change elements.
        # IDML nests: PSR > CharacterStyleRange > Content (normal text)
        #             PSR > Change > CharacterStyleRange > Content (tracked changes)
        # We must NOT use psr.iter() because Change elements at the PSR level can
        # span across subsequent PSRs, duplicating their content.
        # Instead: walk direct children, then recurse one level into Change/CSR.
        def _collect_text(parent):
            """Collect Content/Br from direct children, recursing into wrappers."""
            for child in parent:
                if child.tag == "Content":
                    text_parts.append(child.text or "")
                elif child.tag == "Br":
                    text_parts.append("\n")
                elif child.tag in ("CharacterStyleRange", "Change", "HyperlinkTextSource"):
                    _collect_text(child)
        _collect_text(psr)

        text = "".join(text_parts).strip()
        if text:
            paragraphs.append((role, style, text))

    if not paragraphs:
        return None

    # Pre-filter: remove template placeholders common in Pipestone Star IDML files.
    # These are paragraphs with boilerplate text left in the InDesign template.
    _PLACEHOLDER_PATTERNS = [
        re.compile(r"^(Headline)+$", re.IGNORECASE),           # "HeadlineHeadline"
        re.compile(r"^(Body)+\s*$", re.IGNORECASE),            # "BodyBody", "BodyBodyBody"
        re.compile(r"^(Subhead)+$", re.IGNORECASE),            # "SubheadSubhead"
        re.compile(r"Pipestone County has highest 14-day"),     # template placeholder text
        re.compile(r"^By \w+\s*\w*\s*Word count:", re.IGNORECASE),  # "By Kyle Word count:" or "By KyleWord count:"
        re.compile(r"Word count:\s*\d+\s*$", re.IGNORECASE),  # standalone "Word count: 847"
    ]

    filtered_paragraphs = []
    for role, style, text in paragraphs:
        clean = text.strip()
        # Check if this is a template placeholder
        is_placeholder = False
        for pat in _PLACEHOLDER_PATTERNS:
            if pat.search(clean):
                is_placeholder = True
                break
        if is_placeholder:
            # Special case: headline style may have real text with "Headline" suffix
            # e.g. "County proceeds with paving of CSAH 25 Headline"
            if role == "headline" and len(clean) > 30:
                # Strip trailing "Headline" label and keep the real part
                stripped = re.sub(r"\s*Headline\s*$", "", clean, flags=re.IGNORECASE).strip()
                if stripped and len(stripped) > 10:
                    filtered_paragraphs.append((role, style, stripped))
                    continue
            logger.debug(f"Skipping template placeholder: [{style}] {clean[:60]}")
            continue
        filtered_paragraphs.append((role, style, text))

    paragraphs = filtered_paragraphs
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
            # Check first paragraph for byline pattern (with word count stripped)
            first_line = text.strip().split("\n")[0]
            # Strip word count from anywhere in the line before matching
            first_line_clean = re.sub(r"(?i)\s*Word\s*count:\s*\d+", "", first_line).strip()
            byline_match = re.match(r"^[Bb]y\s+(.+)$", first_line_clean)
            if not byline and not body_parts and byline_match:
                byline = byline_match.group(1).strip()
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

    # Filter orphan body fragments (< 15 chars and not a meaningful word)
    body_parts = [p for p in body_parts if len(p.strip()) >= 15 or re.match(r"^[A-Z]", p.strip())]
    body_text = "\n\n".join(body_parts).strip()

    # Post-extraction cleanup: strip IDML artifacts
    # Remove embedded word counts like "Word count: 847"
    body_text = re.sub(r"(?i)\bword count:\s*\d+\b", "", body_text)
    # Remove trailing style labels like "BodyBody", "HeadlineHeadline", "Headline"
    body_text = re.sub(r"(?i)\b(Body|Headline|Subhead|Byline|Caption)\1*\b", "", body_text)
    # Remove trailing "Headline" or "Body" labels on their own lines
    body_text = re.sub(r"(?im)^\s*(Body|Headline|Subhead|Byline|Caption)\s*$", "", body_text)
    # Clean byline: remove email duplicated into name, fix "Kyle KuphalKyle" pattern
    if byline:
        # Remove email addresses from byline display name
        byline = re.sub(r"\S+@\S+\.\S+", "", byline).strip()
        # Fix doubled names: "Kyle KuphalKyle" → "Kyle Kuphal"
        # Detect when last word starts repeating the first name
        words = byline.split()
        if len(words) >= 2:
            first = words[0].lower()
            last = words[-1].lower()
            if len(last) > len(first) and last.startswith(first):
                # "KuphalKyle" — the last word has the first name appended
                words[-1] = words[-1][:len(words[-1]) - len(words[0])]
                byline = " ".join(w for w in words if w)
    # Clean headline: remove trailing "Headline" label
    headline = re.sub(r"(?i)\s*Headline\s*$", "", headline).strip()
    # If headline looks like a byline, swap it
    # Patterns: "Name | Title", contains @, "staff reporter", "from staff reports", URL
    def _looks_like_byline(text: str) -> bool:
        """Check if text looks like a byline rather than a headline."""
        return bool(
            "@" in text
            or re.search(r"(?i)\b(staff|reporter|editor|correspondent|publisher)\b", text)
            or re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+ \|", text)
            or re.match(r"(?i)^from\s+(staff|wire)", text)
            or re.match(r"(?i)^(https?://|www\.)", text)
        )
    _BYLINE_IN_HEADLINE = _looks_like_byline(headline)
    if headline and _BYLINE_IN_HEADLINE:
        if not byline and not re.match(r"(?i)^(https?://|www\.)", headline):
            byline = re.sub(r"\s*\|.*", "", headline).strip()  # "Eric Viccaro | Sports editor" → "Eric Viccaro"
        headline = ""
    # If no headline, try to use the first non-byline sentence of body as headline
    if not headline and body_text:
        lines = body_text.split("\n")
        for i, line in enumerate(lines):
            candidate = line.strip()
            if not candidate or len(candidate) < 10:
                continue
            # Skip byline-like lines, emails, URLs
            if _looks_like_byline(candidate) or re.match(r"(?i)^by\s", candidate):
                # This is a byline — extract it and skip
                if not byline:
                    byline = re.sub(r"\s*\|.*", "", candidate).strip()
                    byline = re.sub(r"\S+@\S+\.\S+", "", byline).strip()
                continue
            if len(candidate) < 150:
                headline = candidate
                # Remove this line from body
                lines[i] = ""
                body_text = "\n".join(lines).strip()
                break
    # Final byline-in-headline check (catches cases set by the fallback)
    if headline and _looks_like_byline(headline):
        if not byline:
            byline = re.sub(r"\s*\|.*", "", headline).strip()
            byline = re.sub(r"\S+@\S+\.\S+", "", byline).strip()
        headline = ""
        # Try one more time to find a real headline from body
        if body_text:
            for line in body_text.split("\n"):
                candidate = line.strip()
                if candidate and len(candidate) > 10 and not _looks_like_byline(candidate) and not re.match(r"(?i)^by\s", candidate):
                    if len(candidate) < 150:
                        headline = candidate
                        body_text = body_text.replace(line, "", 1).strip()
                    break

    # Collapse multiple blank lines
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

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

            spread_id = fname.replace(".xml", "")
            frames.setdefault(story_ref, []).append({
                "x": x_min, "y": y_min,
                "x2": x_max, "y2": y_max,
                "w": x_max - x_min, "h": y_max - y_min,
                "spread": spread_id,
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
        is_jump_ref = bool(re.search(r"see\s+\w+\s*[•·]|continued\s+on|page\s*\d+\s*$", story_text))
        # Masthead: only short text that looks like a newspaper header
        # (e.g. "Cottonwood County Citizen WEDNESDAY, April 1, 2026")
        is_masthead = story["char_count"] < 200 and bool(re.search(
            r"citizen|star|advocate|observer",
            story_text,
        ) and re.search(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday).*\d{4}", story_text))
        is_cutline = any("cutline" in s.lower() or "photo credit" in s.lower()
                         for s in story.get("styles_used", []))
        is_whats_inside = any("what" in s.lower() and "inside" in s.lower()
                              for s in story.get("styles_used", []))
        is_boilerplate = bool(re.search(
            r"pica deep|drop quotes in swiss|nick klisch|first name last", story_text))

        # Byline text frames: "Eric Viccaro | Sports editor" etc.
        is_byline_frame = bool(
            re.search(r"@", story_text)
            or re.search(r"(?i)\b(reporter|editor|publisher|correspondent)\b", story_text)
            or re.match(r"^[a-z]+ [a-z]+ \|", story_text)  # "name name |"
            or re.match(r"(?i)^from\s+(staff|wire)", story_text)
            or re.match(r"(?i)^(https?://|www\.)", story_text)
            or re.match(r"(?i)^by\s+[a-z]", story_text)
        )

        if is_continuation or is_jump_ref or is_masthead or is_cutline or is_whats_inside or is_boilerplate or is_byline_frame:
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
                    # CRITICAL: only match frames on the SAME spread/page.
                    # Different spreads reuse the same coordinate space,
                    # so cross-spread comparisons produce false matches.
                    if hf.get("spread") != bf.get("spread"):
                        continue
                    # Headline must start above or near this body frame
                    if hf["y"] > bf["y"] + 50:
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


def _stitch_jumped_articles(
    stories: dict[str, dict | None],
    frame_positions: dict[str, list[dict]],
) -> None:
    """Stitch articles that jump across pages using keyword matching.

    InDesign newspapers use jump references like:
    - Jump out: "See riverfest •BACK Page" (on front page)
    - Jump in:  "Riverfest/ Parade will take place June 13 From page 1" (on inner page)

    The continuation body text is in a SEPARATE story positioned near the
    jump-in header. This function:
    1. Finds jump-out/jump-in keyword pairs
    2. Identifies the front-page body story (near the jump-out)
    3. Identifies the continuation body story (near the jump-in)
    4. Appends the continuation text to the front-page story
    """
    # Step 1: Find all jump-out and jump-in stories by keyword
    jump_outs: dict[str, str] = {}   # keyword -> story_id
    jump_ins: dict[str, str] = {}    # keyword -> story_id

    for sid, story in stories.items():
        if not story:
            continue
        full_text = (story.get("body_text") or story.get("headline") or "").strip()
        lower = full_text.lower()

        # Jump out: "See KEYWORD •Page" or "See KEYWORD •BACK Page"
        m = re.search(r"[Ss]ee\s+(\w+)\s*[•·]", lower)
        if m:
            jump_outs[m.group(1).lower()] = sid

        # Jump in: "KEYWORD/ ... From page N" (may have newlines between)
        m = re.search(r"^(\w+)/\s*.*[Ff]rom\s+page", full_text, re.DOTALL)
        if m:
            jump_ins[m.group(1).lower()] = sid

    if not jump_outs or not jump_ins:
        return

    # Step 2: For each matched keyword, find front body + continuation body
    consumed_sids: set[str] = set()

    for keyword in set(jump_outs.keys()) & set(jump_ins.keys()):
        out_sid = jump_outs[keyword]
        in_sid = jump_ins[keyword]

        # Find front-page body story: the article that CONTAINS the keyword
        # or whose headline contains it. This is more reliable than spatial
        # proximity because InDesign spreads use different coordinate systems.
        best_front_sid = None
        best_front_score = 0

        for sid, story in stories.items():
            if not story or sid == out_sid or sid == in_sid:
                continue
            if story.get("content_type") != "article" or story.get("char_count", 0) < 100:
                continue
            headline_lower = (story.get("headline") or "").lower()
            body_lower = (story.get("body_text") or "").lower()
            # Score: headline match is strongest, then early body mention.
            # Penalize stories that look like continuations (start mid-sentence).
            starts_mid = body_lower and body_lower[0].islower()
            if keyword in headline_lower:
                score = 10000
            elif keyword in body_lower[:200] and not starts_mid:
                score = 5000
            elif keyword in body_lower[:500]:
                score = 1000
            elif keyword in body_lower:
                score = 100
            else:
                continue
            if score > best_front_score:
                best_front_score = score
                best_front_sid = sid

        # Find continuation body story: positioned near the jump-in header
        # on the same spread (similar coordinates)
        in_frames = frame_positions.get(in_sid, [])
        best_cont_sid = None
        best_cont_dist = float("inf")

        if in_frames:
            in_f = in_frames[0]
            for sid, story in stories.items():
                if not story or sid == out_sid or sid == in_sid or sid == best_front_sid:
                    continue
                if story.get("char_count", 0) < 50:
                    continue
                # Skip other jump-in headers
                st = (story.get("body_text") or "").strip()
                if re.search(r"^\w+/\s*.*[Ff]rom\s+page", st, re.DOTALL):
                    continue
                sid_frames = frame_positions.get(sid, [])
                for sf in sid_frames:
                    x_dist = abs(sf["x"] - in_f["x"])
                    y_diff = sf["y"] - in_f["y"]
                    # Same spread = similar coordinate range
                    if x_dist < 300 and -20 < y_diff < 500:
                        dist = x_dist + abs(y_diff)
                        if dist < best_cont_dist:
                            best_cont_dist = dist
                            best_cont_sid = sid

        # Step 3: Merge continuation into front story
        if best_front_sid and best_cont_sid:
            front_story = stories[best_front_sid]
            cont_story = stories[best_cont_sid]
            if front_story and cont_story:
                cont_text = cont_story.get("body_text", "").strip()
                if cont_text:
                    front_story["body_text"] = (
                        front_story.get("body_text", "") + "\n\n" + cont_text
                    )
                    front_story["char_count"] = len(front_story["body_text"])
                    front_story["is_stitched"] = True
                    # Mark continuation as consumed so it doesn't appear as separate article
                    cont_story["content_type"] = "consumed_continuation"
                    logger.info(
                        f"Stitched '{keyword}': {best_front_sid} + {best_cont_sid} "
                        f"({front_story['char_count']} chars)"
                    )

        # Mark jump-out and jump-in header stories as furniture (not articles)
        if stories.get(out_sid):
            stories[out_sid]["content_type"] = "furniture"
        if stories.get(in_sid):
            stories[in_sid]["content_type"] = "furniture"


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

        # Build spread → page number mapping from designmap.xml
        # designmap.xml lists spreads in actual print order
        spread_to_page: dict[str, int] = {}
        designmap_path = os.path.join(tmp_dir, "designmap.xml")
        if os.path.exists(designmap_path):
            dm_tree = ET.parse(designmap_path)
            ns = {"idPkg": "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"}
            page_num = 1
            for sp in dm_tree.getroot().findall(".//idPkg:Spread", ns):
                src = sp.get("src", "")
                # src = "Spreads/Spread_uc4.xml" → spread_id = "Spread_uc4"
                spread_id = src.replace("Spreads/", "").replace(".xml", "")
                spread_to_page[spread_id] = page_num
                page_num += 1

        # Assign start_page to each story based on which spread its frames are on
        for sid, story in all_stories.items():
            if not story:
                continue
            sid_frames = frame_positions.get(sid, [])
            if sid_frames:
                # Use the earliest page any frame appears on
                pages = [spread_to_page.get(f.get("spread", ""), 999) for f in sid_frames]
                story["start_page"] = min(pages) if pages else None

        # Stitch jumped articles using keyword matching (before headline matching)
        _stitch_jumped_articles(
            {k: v for k, v in all_stories.items() if v},
            frame_positions,
        )

        # Match headlines to body stories using proximity
        _match_headlines_to_bodies(
            {k: v for k, v in all_stories.items() if v},
            frame_positions,
        )

        # Collect "what's inside" tease text to filter out tease-matched articles.
        # Tease headlines on the front page duplicate actual article headlines
        # on inner pages, causing false matches with nearby body stories.
        whats_inside_texts = set()
        for sid, story in all_stories.items():
            if not story:
                continue
            if any("what" in s.lower() and "inside" in s.lower()
                   for s in story.get("styles_used", [])):
                # Grab the tease text (headline or body)
                text = (story.get("headline") or story.get("body_text") or "").lower().strip()
                # Split on "n Page" to get just the tease headline
                text = re.split(r"\s*n\s*page\s*\d", text)[0].strip()
                if text:
                    whats_inside_texts.add(text)

        # Collect articles — filter boilerplate, furniture, and tiny fragments
        articles = []
        for sid, story in all_stories.items():
            if story is None:
                continue
            if story["content_type"] != "article":
                continue
            if story["char_count"] < 150:
                continue  # Skip tiny text blocks

            # Filter boilerplate by content patterns
            hl = (story.get("headline") or "").lower()
            body = (story.get("body_text") or "").lower()
            combined = hl + " " + body[:300]

            # Staff listings and contact info
            if re.search(r"citizen staff:|citizen publishing|507-831-3455|@windomnews\.com", combined):
                if story["char_count"] < 800:
                    continue
            # Subscription/masthead info
            if re.search(r"\d+\w*\s+year\s+\d+\w*\s+edition\s+\$", combined):
                continue
            # "Visit us online" promos
            if re.search(r"visit us online|www\.windomnews\.com", hl):
                continue
            # "MORE ONLINE" sidebars
            if "more online" in hl.lower():
                continue
            # Pull quote attributions (short text with name + title)
            if story["char_count"] < 300 and re.search(
                r"(board member|co-chair|columnist|editor|artist|graduate)",
                hl, re.IGNORECASE,
            ):
                continue
            # Letters to editor policy box
            if "we welcome letters to the editor" in body[:100]:
                continue
            # Weather data tables
            if re.search(r"a look back.*[HLPT].*tues|mon\.\s+\d+\s+\d+", body[:100]):
                continue
            # Poll quotes (very short, just a quote)
            if story["char_count"] < 200 and body.startswith('"'):
                continue
            # Q&A sidebar fragments (n Year GRADUATED:)
            if re.search(r"^\s*n\s+(year|current residence|parents|siblings)", body[:80]):
                continue
            # Classified ad contact headers
            if "to place your classified" in combined:
                continue
            # Subscription rate info
            if "subscription rates of" in body[:200]:
                continue
            # All-conference team tables (just stats, no article)
            if re.search(r"^\d{4}\s+(red rock|big south|conference).*team", body[:80], re.IGNORECASE):
                continue
            # "Quick views" sidebar
            if hl.startswith("quick views"):
                continue
            # Photo captions that slipped through (very short, starts with ALL CAPS name)
            if story["char_count"] < 300 and re.match(r"^[A-Z]{2,}\s+[A-Z]{2,}", body.strip()):
                continue
            # "What's Inside" tease duplicates: headline matches a tease on the front page
            # These are small frame headlines that got matched to wrong body stories
            if hl and any(hl in tease or tease in hl for tease in whats_inside_texts):
                continue
            # Production/editorial notes (internal InDesign comments)
            if re.search(r"(?i)(print off|art bucket|PDF proof|kyle\s*$|\* front page|\* complete story|\* jumps match|\* by line|\* cutline|\* obits)", combined):
                continue
            # Pipestone Star staff/contact boilerplate
            if re.search(r"@pipestonestar\.com|pipestone\s*star\s*staff|507-825-3333", combined):
                if story["char_count"] < 800:
                    continue
            # FTP credentials and upload instructions
            if re.search(r"(?i)ftp\.\w+|password:|user name:", combined[:200]):
                continue

            articles.append(story)

        # Collect classified listings from stories with Classified style
        classifieds = []
        for sid, story in all_stories.items():
            if not story:
                continue
            styles_lower = [s.lower() for s in story.get("styles_used", [])]
            has_classified = any("classified" in s or "minor category" in s for s in styles_lower)
            if not has_classified:
                continue
            if story.get("char_count", 0) < 20:
                continue

            # Split into individual listings by blank lines
            body = story.get("body_text", "")
            current_category = "general"

            for block in re.split(r"\n{2,}", body):
                block = block.strip()
                if not block or len(block) < 15:
                    continue
                # Detect category headers (short ALL CAPS or Title Case lines)
                if len(block) < 40 and (block.isupper() or block.istitle()):
                    current_category = block.lower().replace(" ", "_")
                    continue
                classifieds.append({
                    "text": block,
                    "category": current_category,
                    "headline": current_category.replace("_", " ").title(),
                })

        logger.info(
            f"IDML parsed: {len(articles)} articles, "
            f"{len(classifieds)} classified listings from {idml_path}"
        )

        # Sort articles by text length (longest first = most prominent)
        articles.sort(key=lambda a: -a["char_count"])

        return articles, classifieds


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

    # Parse IDML (returns articles + classified listings)
    raw_articles, classifieds = parse_idml(idml_path)

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
            "content_type": None,  # inferred by shared write layer from headline
            "start_page": art.get("start_page"),
            "jump_pages": [],
            "is_stitched": False,  # no jumps in IDML — stories are already complete
            "extraction_confidence": 0.98,
            "source_pipeline": "idml",
        })

    # Clear old content_items for this edition (idempotent re-run)
    from src.modules.content_items.database import delete_content_items_for_edition
    deleted = delete_content_items_for_edition(edition_id)
    if deleted > 0:
        logger.info(f"Cleared {deleted} old content items for edition {edition_id}")

    # Write to all destinations via shared layer
    write_result = write_articles_to_all(
        articles=standard_articles,
        edition_id=edition_id,
        publisher_id=publisher_id,
        publisher_name=publisher_name,
        edition_date=edition_date,
        source_filename=filename,
    )

    # Process classified listings as individual ads
    classifieds_ingested = 0
    if classifieds:
        try:
            from src.ad_ingestion import AdIngester, _upsert_directory_entry
            from src.modules.advertisements import insert_edition_advertisement

            for listing in classifieds:
                ad_id = str(uuid.uuid4())
                text = listing["text"]
                category = listing["category"]

                insert_edition_advertisement(
                    ad_id=ad_id,
                    advertiser_name=listing["headline"],
                    extracted_text=text,
                    publisher=publisher_name,
                    edition_id=edition_id,
                    ad_type="classified",
                    ad_category=category,
                )
                classifieds_ingested += 1
        except Exception as e:
            logger.error(f"Classified ingestion failed: {e}")

    logger.info(
        f"IDML ingestion complete: {write_result['articles_written']} articles, "
        f"{write_result['chunks_indexed']} chunks, "
        f"{classifieds_ingested} classifieds from {filename}"
    )

    return {
        "success": True,
        "edition_id": edition_id,
        "filename": filename,
        "total_stories": len(raw_articles),
        "articles_inserted": write_result["articles_written"],
        "chunks_indexed": write_result["chunks_indexed"],
        "content_items_written": write_result["content_items_written"],
        "classifieds_ingested": classifieds_ingested,
    }
