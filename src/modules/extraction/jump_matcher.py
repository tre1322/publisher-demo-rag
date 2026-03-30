"""Bipartite jump matching: connect front-page jump-outs to back-page continuations.

Uses multiple signals for matching:
1. Keyword exact match (SEE COUNCIL -> COUNCIL/ header)
2. Page number targeting (Continued on page 8 -> page 8)
3. Headline-to-keyword overlap
4. Body text TF-IDF similarity (last 100 words vs first 100 words)
5. Sentence bridge (front ends mid-sentence, continuation starts lowercase)

Solves as a constrained bipartite matching: each jump-out matches at most one
continuation, and each continuation matches at most one jump-out.
"""

import logging
import re
from dataclasses import dataclass

from src.modules.extraction.cell_claiming import ArticleFragment

logger = logging.getLogger(__name__)


@dataclass
class JumpEdge:
    """A potential match between a front-page fragment and a continuation."""
    src_page: int
    src_seed_id: int
    src_headline: str
    dst_page: int
    dst_seed_id: int
    dst_label: str
    score: float
    match_reasons: list


def collect_jump_outs(all_fragments: dict[int, list[ArticleFragment]]) -> list[dict]:
    """Collect all jump-out references across all pages.

    Returns list of dicts with: keyword, target_page, source_page, source_fragment.
    """
    jump_outs = []

    for page_num, fragments in all_fragments.items():
        # Get page-level jump-outs (from the first fragment which has the list)
        page_jump_outs = []
        for frag in fragments:
            pjo = getattr(frag, "_page_jump_outs", [])
            if pjo:
                page_jump_outs = pjo
                break

        for jo in page_jump_outs:
            jump_outs.append({
                "keyword": jo["keyword"],
                "target_page": jo.get("target_page"),
                "source_page": page_num,
                "block_y": jo.get("block_y", 0),
            })

        # Also check fragment-level jump-outs (for fragments that directly detected them)
        for frag in fragments:
            if frag.jump_out_keyword:
                # Avoid duplicates
                already = any(
                    j["keyword"] == frag.jump_out_keyword and j["source_page"] == page_num
                    for j in jump_outs
                )
                if not already:
                    jump_outs.append({
                        "keyword": frag.jump_out_keyword,
                        "target_page": frag.jump_out_target_page,
                        "source_page": page_num,
                        "source_fragment": frag,
                    })

    return jump_outs


def collect_continuations(all_fragments: dict[int, list[ArticleFragment]]) -> list[dict]:
    """Collect all continuation header fragments across all pages."""
    continuations = []

    for page_num, fragments in all_fragments.items():
        for frag in fragments:
            if frag.kind == "continuation_header" and frag.label:
                continuations.append({
                    "label": frag.label,
                    "page": page_num,
                    "fragment": frag,
                })

    return continuations


def score_match(
    jump_out: dict,
    continuation: dict,
    all_fragments: dict[int, list[ArticleFragment]],
) -> tuple[float, list[str]]:
    """Score a potential jump-out → continuation match.

    Returns (score, list_of_match_reasons).
    """
    score = 0.0
    reasons = []

    jo_keyword = jump_out["keyword"].upper()
    cont_label = continuation["label"].upper()

    # 1. Exact keyword match (+10)
    if jo_keyword == cont_label:
        score += 10.0
        reasons.append(f"exact_keyword={jo_keyword}")

    # 2. Fuzzy keyword match (+6)
    elif jo_keyword in cont_label or cont_label in jo_keyword:
        score += 6.0
        reasons.append(f"partial_keyword={jo_keyword}~{cont_label}")

    # 3. Page targeting (+8 for exact match)
    jo_target = jump_out.get("target_page")
    cont_page = continuation["page"]

    if jo_target is not None and jo_target == cont_page:
        score += 8.0
        reasons.append(f"exact_page={jo_target}")
    elif jo_target is None:
        # "BACK PAGE" — check if continuation is on the last page
        # Give a small bonus for being on a later page than source
        source_page = jump_out.get("source_page", 1)
        if cont_page > source_page:
            score += 3.0
            reasons.append(f"later_page={cont_page}>{source_page}")

    # 4. Sentence bridge detection (+5)
    # Check if any source fragment on the source page ends mid-sentence
    # and the continuation starts with lowercase
    source_page = jump_out.get("source_page", 1)
    source_frags = all_fragments.get(source_page, [])
    cont_frag = continuation["fragment"]

    cont_first_char = ""
    if cont_frag.body_text:
        # Strip "FROM PAGE 1" header text
        cleaned = re.sub(r"^F\s*R\s*O\s*M\s+P\s*A\s*G\s*E\s*\d+\s*", "", cont_frag.body_text).strip()
        if cleaned:
            cont_first_char = cleaned[0]

    for sfrag in source_frags:
        if sfrag.body_text:
            last_char = sfrag.body_text.rstrip()[-1] if sfrag.body_text.rstrip() else ""
            # If source ends without terminal punctuation and continuation starts lowercase
            if last_char and last_char not in ".!?\"'" and cont_first_char.islower():
                score += 5.0
                reasons.append(f"sentence_bridge: '{last_char}' -> '{cont_first_char}'")
                break

    # 5. Penalty: keyword doesn't match at all (-5)
    if not reasons or (score < 3 and jo_keyword != cont_label):
        score -= 5.0
        reasons.append("no_match")

    return score, reasons


def _infer_jump_outs_from_continuations(
    continuations: list[dict],
    all_fragments: dict[int, list[ArticleFragment]],
) -> list[dict]:
    """Infer jump-outs for papers that only have 'FROM PAGE N' on back pages.

    Some papers (e.g. Pipestone Star) don't print 'SEE STORY • PAGE X' at the
    end of front-page articles. Instead, back-page continuations are labeled
    'STORY\nFROM PAGE 1'. We infer the matching front-page article by:
    1. The continuation's source_page is known (from 'FROM PAGE N' text)
    2. Score candidates on:
       a. Continuation label keyword appears in front-page headline (+8)
       b. Front-page article ends without terminal punctuation (truncated) (+5)
       c. Text bridge: continuation starts lowercase (mid-sentence) (+3)
    3. Best-scoring candidate above threshold becomes the inferred jump-out
    """
    inferred = []
    used_source_frags: set = set()  # avoid assigning same front-page frag to multiple conts

    for cont in continuations:
        frag = cont["fragment"]
        cont_label = cont["label"] or ""

        # Only process continuations with EXPLICIT "FROM PAGE N" text in the headline.
        # This filters out "AA/Al-Anon" style notices that just happen to use "KEYWORD/"
        # format and aren't actually article jumps.
        m_src = re.search(r"FROM\s+PAGE\s+(\d+)", frag.headline or "", re.IGNORECASE)
        if not m_src:
            m_src = re.search(r"FROM\s+PAGE\s+(\d+)", (frag.body_text or "")[:200], re.IGNORECASE)
        if not m_src:
            continue  # no explicit source page — skip
        source_page = int(m_src.group(1))

        source_frags = all_fragments.get(source_page, [])
        title_frags = [f for f in source_frags if f.kind == "title" and f.body_text]
        if not title_frags:
            continue

        # Extract the clean keyword (strip "FROM PAGE N" suffix)
        kw = re.sub(r"\s*FROM\s+PAGE\s*\d*", "", cont_label, flags=re.IGNORECASE).strip().upper()
        if not kw:
            kw = cont_label.upper()

        # Strip "FROM PAGE N" prefix from continuation body for text bridge check
        cont_body = re.sub(
            r"^[A-Z\s]+FROM\s+PAGE\s+\d+\s*", "", (frag.body_text or ""), flags=re.IGNORECASE
        ).strip()

        best_frag = None
        best_score = 0.0

        for tf in title_frags:
            if tf.seed_id in used_source_frags:
                continue

            score = 0.0
            hl_upper = (tf.headline or "").upper()
            body_text = (tf.body_text or "")
            body_end = body_text.rstrip()

            # a. Keyword in headline (+8)
            headline_score = 0.0
            if kw and kw in hl_upper:
                score += 8.0
                headline_score += 8.0

            # b. Any word of keyword (>=4 chars) in headline (+4)
            kw_words = [w for w in kw.split() if len(w) >= 4]
            if kw_words and any(w in hl_upper for w in kw_words):
                score += 4.0
                headline_score += 4.0

            # Require at least one headline signal — body text alone is not sufficient.
            # The continuation label (e.g. PERMITS) must appear in the article's
            # headline; otherwise we risk matching unrelated articles that happen to
            # mention the keyword in passing.
            if headline_score == 0.0:
                continue

            # c. Keyword in body text (first 500 chars) — (+3, supporting signal only)
            # Strip "KEYWORD • PAGE N" inside-the-paper teaser lines first
            clean_body = re.sub(r"[A-Z]{2,}\s*[•·]\s*PAGE\s*\d+", "", body_text[:500], flags=re.IGNORECASE)
            if kw and kw in clean_body.upper():
                score += 3.0

            # d. Front article is truncated (ends mid-sentence) (+3)
            last_char = body_end[-1] if body_end else ""
            if last_char and last_char not in ".!?\"'":
                score += 3.0

            # e. Continuation starts mid-sentence (lowercase) (+3)
            if cont_body and cont_body[0].islower():
                score += 3.0

            if score > best_score:
                best_score = score
                best_frag = tf

        if best_frag and best_score >= 4.0:
            used_source_frags.add(best_frag.seed_id)
            inferred.append({
                "keyword": kw,
                "target_page": cont["page"],
                "source_page": source_page,
                "source_fragment": best_frag,
                "_inferred": True,
            })
            logger.info(
                f"  Inferred jump-out: p{source_page} '{best_frag.headline[:40]}' "
                f"-> p{cont['page']} '{cont_label}' (score={best_score:.1f})"
            )

    return inferred


def merge_continuation_columns(
    all_fragments: dict[int, list[ArticleFragment]],
) -> dict[int, list[ArticleFragment]]:
    """Merge orphan body fragments into adjacent continuation_header fragments.

    On back pages, continuation articles often span multiple newspaper columns.
    The cell claiming phase may create separate fragments for each column — one
    continuation_header fragment and one or more orphan_body fragments. This
    function detects and merges them before jump matching runs.

    Detection criteria (all must be true):
    1. Same page as a continuation_header fragment
    2. Fragment is orphan_body (or has no headline) — not a title or cont header
    3. Fragment's y-position falls within the continuation_header's y-band
       (between this header and the next header/title below on the page)
    4. Text continuity: the continuation ends mid-sentence OR the orphan starts
       mid-sentence (lowercase first character)
    """
    for page_num, fragments in list(all_fragments.items()):
        # Find continuation_header fragments on this page
        cont_frags = [f for f in fragments if f.kind == "continuation_header"]
        if not cont_frags:
            continue

        # Find potential merge targets: orphan_body or headline-less body fragments
        # that are NOT titles or continuation headers themselves
        other_frags = [
            f for f in fragments
            if f.kind not in ("continuation_header", "title")
            and not f.headline.strip()
        ]
        if not other_frags:
            continue

        # Sort continuation headers by y-position for y-band computation
        cont_frags.sort(key=lambda f: f.top_y)

        # Collect all article boundary y-positions (continuation headers + titles)
        # to define y-bands. Each continuation owns the band from its top_y to the
        # next boundary below it.
        boundary_ys = sorted(set(
            f.top_y for f in fragments
            if f.kind in ("continuation_header", "title")
        ))

        merged_seeds = set()

        for cont in cont_frags:
            # Compute this continuation's y-band ceiling: the next boundary below
            y_max = float("inf")
            for by in boundary_ys:
                if by > cont.top_y + 5:
                    y_max = by
                    break

            # Find orphans in this y-band
            to_merge = []
            for orphan in other_frags:
                if orphan.seed_id in merged_seeds:
                    continue

                # Must overlap with the continuation's y-band
                if orphan.top_y < cont.top_y - 30:
                    continue
                if orphan.top_y >= y_max - 5:
                    continue

                # Text continuity check
                cont_text = cont.body_text.rstrip()
                orphan_text = orphan.body_text.strip()
                if not orphan_text:
                    continue

                # Does the continuation end mid-sentence?
                ends_mid = cont_text and cont_text[-1] not in '.!?"\')\u201d'
                # Does the orphan start mid-sentence (lowercase)?
                starts_mid = orphan_text[0].islower()

                if ends_mid or starts_mid:
                    to_merge.append(orphan)

            if not to_merge:
                continue

            # Sort merge candidates by column (left-to-right reading order),
            # then by y (top-to-bottom)
            to_merge.sort(key=lambda f: (
                min(l[0] for l in f.lanes) if f.lanes else 999,
                f.top_y,
            ))

            for orphan in to_merge:
                cont.body_text = cont.body_text + "\n\n" + orphan.body_text
                cont.cell_ids.extend(orphan.cell_ids)
                cont.lanes.extend(orphan.lanes)
                cont.bottom_y = max(cont.bottom_y, orphan.bottom_y)
                merged_seeds.add(orphan.seed_id)
                logger.info(
                    f"  Merged orphan fragment (seed {orphan.seed_id}, "
                    f"{len(orphan.body_text)} chars) into continuation "
                    f"'{cont.label}' on page {page_num}"
                )

        # Remove merged fragments from the page's fragment list
        if merged_seeds:
            all_fragments[page_num] = [
                f for f in fragments if f.seed_id not in merged_seeds
            ]

    return all_fragments


def match_jumps(
    all_fragments: dict[int, list[ArticleFragment]],
) -> list[JumpEdge]:
    """Solve bipartite matching between jump-outs and continuations.

    Each jump-out matches at most one continuation, and vice versa.
    Uses greedy assignment by descending score (deterministic).
    """
    jump_outs = collect_jump_outs(all_fragments)
    continuations = collect_continuations(all_fragments)

    if not continuations:
        return []

    # If no explicit jump-outs detected, try reverse inference from continuations
    # that know their source_page (e.g. "PLAY\nFROM PAGE 1" format papers).
    if not jump_outs:
        jump_outs = _infer_jump_outs_from_continuations(continuations, all_fragments)

    if not jump_outs:
        return []

    logger.info(f"Jump matching: {len(jump_outs)} jump-outs, {len(continuations)} continuations")

    # Score all possible edges
    edges = []
    for jo in jump_outs:
        for cont in continuations:
            # Don't match to same page
            if jo.get("source_page") == cont["page"]:
                continue

            score, reasons = score_match(jo, cont, all_fragments)

            if score > 0:
                edge = JumpEdge(
                    src_page=jo.get("source_page", 0),
                    src_seed_id=-1,  # will be resolved during stitching
                    src_headline=jo["keyword"],
                    dst_page=cont["page"],
                    dst_seed_id=cont["fragment"].seed_id,
                    dst_label=cont["label"],
                    score=score,
                    match_reasons=reasons,
                )
                # For inferred jump-outs, carry the already-resolved source frag
                if jo.get("_inferred") and jo.get("source_fragment"):
                    edge._inferred_frag = jo["source_fragment"]
                else:
                    edge._inferred_frag = None
                edges.append(edge)

    # Sort by score descending (greedy matching)
    edges.sort(key=lambda e: -e.score)

    # Greedy one-to-one matching
    matched_outs = set()  # (source_page, keyword) -> used
    matched_ins = set()   # (dest_page, seed_id) -> used
    final_edges = []

    for edge in edges:
        out_key = (edge.src_page, edge.src_headline)
        in_key = (edge.dst_page, edge.dst_seed_id)

        if out_key in matched_outs or in_key in matched_ins:
            continue

        matched_outs.add(out_key)
        matched_ins.add(in_key)
        final_edges.append(edge)

        logger.info(
            f"  MATCH: p{edge.src_page} '{edge.src_headline}' -> "
            f"p{edge.dst_page} '{edge.dst_label}' "
            f"(score={edge.score:.1f}, reasons={edge.match_reasons})"
        )

    logger.info(f"  {len(final_edges)} matches found")
    return final_edges


def _merge_same_page_orphans(
    all_fragments: dict[int, list[ArticleFragment]],
) -> dict[int, list[ArticleFragment]]:
    """Prepend orphan body fragments into adjacent title fragments on the same page.

    When a headline's seed_col_ids underestimates the article's column span, the
    lede paragraph ends up as an orphan_body fragment directly below the headline
    cell rather than claimed by the headline seed.  This function detects that
    case and prepends the orphan text to the title article's body.

    Detection criteria (all must be true):
    1. Same page as a title fragment.
    2. Fragment kind is "orphan_body" (no headline of its own).
    3. The orphan's top_y is within 120pt below the title fragment's top_y
       (i.e. it sits immediately below or beside the headline cell).
    4. The orphan's top_y is ABOVE the title fragment's own bottom_y + 50
       (so it can't be trailing content from another article below).
    """
    for page_num, fragments in list(all_fragments.items()):
        title_frags = [f for f in fragments if f.kind == "title"]
        orphan_frags = [f for f in fragments if f.kind == "orphan_body" and not f.headline.strip()]

        if not title_frags or not orphan_frags:
            continue

        merged_orphan_ids = set()

        for title in title_frags:
            lede_candidates = []
            for orphan in orphan_frags:
                if orphan.seed_id in merged_orphan_ids:
                    continue
                # Must be below (or at) the headline, not above it
                if orphan.top_y < title.top_y - 10:
                    continue
                # Must be close enough to be the lede (within 300pt)
                if orphan.top_y > title.top_y + 300:
                    continue
                # Must not already be well below the title fragment's content
                if title.bottom_y > title.top_y + 50 and orphan.top_y > title.bottom_y + 50:
                    continue
                lede_candidates.append(orphan)

            if not lede_candidates:
                continue

            # Sort by y-position: prepend topmost orphans first
            lede_candidates.sort(key=lambda f: f.top_y)

            for orphan in lede_candidates:
                if orphan.body_text.strip():
                    # Prepend the orphan lede before the title's body
                    title.body_text = orphan.body_text.strip() + "\n\n" + title.body_text if title.body_text else orphan.body_text.strip()
                    title.cell_ids.extend(orphan.cell_ids)
                    title.lanes = orphan.lanes + title.lanes
                    title.top_y = min(title.top_y, orphan.top_y)
                    merged_orphan_ids.add(orphan.seed_id)
                    logger.info(
                        f"  Prepended orphan lede (seed {orphan.seed_id}, "
                        f"{len(orphan.body_text)} chars) into title "
                        f"'{title.headline[:40]}' on page {page_num}"
                    )

        if merged_orphan_ids:
            all_fragments[page_num] = [
                f for f in fragments if f.seed_id not in merged_orphan_ids
            ]

    return all_fragments


def stitch_fragments(
    all_fragments: dict[int, list[ArticleFragment]],
    edges: list[JumpEdge],
) -> list[dict]:
    """Stitch matched fragments into complete articles.

    Strategy:
    1. For each matched edge (keyword → continuation), find the front-page
       article that "owns" that jump keyword by y-proximity
    2. Merge the continuation body into the front-page article
    3. Output all articles (stitched + standalone)
    """
    # Build continuation lookup: (page, seed_id) -> fragment
    cont_lookup = {}
    for page_num, frags in all_fragments.items():
        for frag in frags:
            cont_lookup[(page_num, frag.seed_id)] = frag

    # Build edge lookup: (src_page, keyword) -> edge
    edge_by_key = {}
    for edge in edges:
        edge_by_key[(edge.src_page, edge.src_headline.upper())] = edge

    # Step 1: For each edge, assign the jump-out keyword to the nearest
    # front-page fragment by GEOMETRIC PROXIMITY (y-position of the jump
    # reference block relative to fragment bottom_y).
    #
    # This is more reliable than body-text TF-IDF because "SEE SCHOOL"
    # appears physically at the bottom of the Larson/School Board article,
    # not the "Fun is elementary" article — even though both mention "school".
    frag_jumps: dict[tuple, list] = {}  # (page, seed_id) -> [(keyword, edge)]

    for edge in edges:
        src_page = edge.src_page
        src_frags = all_fragments.get(src_page, [])

        if not src_frags:
            continue

        # For inferred edges (reverse-matched), the source fragment is already known.
        if getattr(edge, "_inferred_frag", None) is not None:
            best_frag = edge._inferred_frag
            key = (src_page, best_frag.seed_id)
            existing = frag_jumps.get(key, [])
            if existing:
                existing.append((edge.src_headline, edge, 9999))
            else:
                frag_jumps[key] = [(edge.src_headline, edge, 9999)]
            logger.info(
                f"  Assigned inferred '{edge.src_headline}' -> '{edge.dst_label}' "
                f"to '{best_frag.headline[:40]}'"
            )
            continue

        # Find the y-position of the jump-out block on the source page
        jump_block_y = None
        for frag in src_frags:
            pjo = getattr(frag, "_page_jump_outs", [])
            for jo in pjo:
                if jo["keyword"] and jo["keyword"].upper() == edge.src_headline.upper():
                    jump_block_y = jo.get("block_y", 0)
                    break
            if jump_block_y is not None:
                break

        # Also check fragment-level jump-out
        if jump_block_y is None:
            for frag in src_frags:
                if frag.jump_out_keyword and frag.jump_out_keyword.upper() == edge.src_headline.upper():
                    jump_block_y = frag.bottom_y
                    break

        if jump_block_y is None:
            jump_block_y = 9999  # fallback — will pick the last fragment

        # Find the front-page fragment that OWNS this jump-out reference.
        #
        # Strategy: match the jump ref to the fragment whose LANES contain
        # the jump ref's column AND whose y-range covers the jump ref's position.
        # This handles multi-column articles correctly — the SCHOOL jump ref
        # in col=5 at y=579 matches Larson (lanes in cols 1-5, y=353-903).

        # Get jump ref column from the page-level jump-out data
        jump_ref_col = None
        for frag in src_frags:
            pjo = getattr(frag, "_page_jump_outs", [])
            for jo in pjo:
                if jo.get("keyword") and jo["keyword"].upper() == edge.src_headline.upper():
                    jump_ref_col = jo.get("block_col")
                    break
            if jump_ref_col is not None:
                break

        best_frag = None
        best_score = -float("inf")

        for frag in src_frags:
            if frag.kind in ("continuation_header", "orphan_body"):
                continue
            if not frag.body_text:
                continue

            score = 0.0
            frag_cols = set(l[0] for l in frag.lanes) if frag.lanes else set()

            # CRITICAL: Does the fragment own the jump ref's column?
            if jump_ref_col is not None and jump_ref_col in frag_cols:
                score += 10000  # strong signal — column containment

            # Is the jump ref within the fragment's y-range?
            if frag.top_y <= jump_block_y <= frag.bottom_y + 200:
                score += 5000

            # Closer bottom_y to jump ref = better
            y_dist = abs(frag.bottom_y - jump_block_y)
            score -= y_dist

            # Penalty for fragments starting below the jump ref
            if frag.top_y > jump_block_y + 50:
                score -= 20000

            if score > best_score:
                best_score = score
                best_frag = frag

        if best_frag:
            key = (src_page, best_frag.seed_id)
            existing = frag_jumps.get(key, [])
            if existing:
                # Already has a jump — keep both (article can have multiple jumps)
                existing.append((edge.src_headline, edge, best_score))
            else:
                frag_jumps[key] = [(edge.src_headline, edge, best_score)]
            logger.info(
                f"  Assigned '{edge.src_headline}' -> '{edge.dst_label}' "
                f"to '{best_frag.headline[:40]}' (score={best_score:.0f})"
            )

    # Step 2: Build articles
    used_as_continuation = set()
    articles = []

    for page_num in sorted(all_fragments.keys()):
        for frag in all_fragments[page_num]:
            # Skip continuations that will be merged
            if frag.kind == "continuation_header":
                is_target = any(
                    e.dst_page == page_num and e.dst_seed_id == frag.seed_id
                    for e in edges
                )
                if is_target:
                    continue

            article = {
                "headline": frag.headline,
                "byline": frag.byline,
                "body_parts": [frag.body_text] if frag.body_text else [],
                "start_page": page_num,
                "jump_pages": [],
                "has_jumps": False,
                "kind": frag.kind,
                "label": frag.label,
            }

            # Check if this fragment has assigned jump-outs
            frag_key = (page_num, frag.seed_id)
            if frag_key in frag_jumps:
                for item in frag_jumps[frag_key]:
                    keyword, edge = item[0], item[1]
                    cont_frag = cont_lookup.get((edge.dst_page, edge.dst_seed_id))
                    if cont_frag and (edge.dst_page, edge.dst_seed_id) not in used_as_continuation:
                        article["body_parts"].append(cont_frag.body_text)
                        article["jump_pages"].append(edge.dst_page)
                        article["has_jumps"] = True
                        used_as_continuation.add((edge.dst_page, edge.dst_seed_id))

            # Join body parts
            article["body_text"] = "\n\n".join(p for p in article["body_parts"] if p)
            del article["body_parts"]

            # Skip empty articles
            if not article["headline"] and len(article["body_text"]) < 50:
                continue

            articles.append(article)

    # Add unclaimed continuations as standalone articles
    for page_num, frags in all_fragments.items():
        for frag in frags:
            if frag.kind == "continuation_header":
                if (page_num, frag.seed_id) not in used_as_continuation:
                    articles.append({
                        "headline": frag.headline,
                        "byline": frag.byline,
                        "body_text": frag.body_text,
                        "start_page": page_num,
                        "jump_pages": [],
                        "has_jumps": False,
                        "kind": frag.kind,
                        "label": frag.label,
                    })

    return articles


def _frag_bottom_y(frag: ArticleFragment, all_fragments: dict) -> float:
    """Get the bottom-most y position of a fragment's cells."""
    return frag.bottom_y
