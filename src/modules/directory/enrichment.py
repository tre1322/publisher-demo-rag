"""Business directory enrichment via Brave Search + LLM summarization.

When ads are uploaded, basic business info (name, address, phone) is extracted
and stored in the organizations table. This module enriches those entries by:

1. Searching Brave Search for the business website
2. Fetching the website content
3. Using Qwen3-32B (via DigitalOcean Gradient) to summarize into a structured profile
4. Updating the organizations table with enriched data

Cost: ~$0.004 per business (Brave Search free tier + Qwen3-32B token cost).
"""

import json
import logging
import os
import re

import httpx
from bs4 import BeautifulSoup

from src.core.config import GRADIENT_BASE_URL, GRADIENT_MODEL, GRADIENT_MODEL_ACCESS_KEY
from src.core.database import get_connection

logger = logging.getLogger(__name__)

BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# Pattern to strip ad metadata from business names before searching
# Matches: "Double", "Single", "Half", "Quarter", "Full", "Spec", fraction chars,
# and "(Campaign Name 2026)" style parentheticals.
_AD_METADATA_RE = re.compile(
    r"\b(?:Single|Double|Half|Quarter|Full|Spec)\b"   # ad size keywords
    r"|[½¼¾⅓⅔]"                                       # fraction characters (½ page ads)
    r"|\((?:[^)]*\d{4})\)"                            # (Campaign Name YYYY)
    , re.IGNORECASE
)


def _clean_business_name(raw_name: str) -> str:
    """Strip ad metadata (size, campaign) from business name for search queries."""
    cleaned = _AD_METADATA_RE.sub("", raw_name)
    # Collapse multiple spaces and newlines
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# Domains to skip in Brave Search results (aggregator/directory sites)
_SKIP_DOMAINS = [
    "facebook.com", "yelp.com", "mapquest.com", "yellowpages.com",
    "bbb.org", "manta.com", "chamberofcommerce.com", "buzzfile.com",
    "dandb.com", "superpages.com", "whitepages.com", "angi.com",
    "indeed.com", "reddit.com", "linkedin.com", "nextdoor.com",
    "justia.com", "tripadvisor.com",
]


def _brave_search(query: str) -> tuple[str | None, str | None, str]:
    """Search Brave and return (top_result_url, error_reason, snippet_text).

    Returns (url, None, snippet) on success, (None, reason, snippet) on failure.
    The snippet is a concat of search result descriptions — useful as fallback
    text for LLM summarization when the actual page can't be fetched.
    """
    if not BRAVE_API_KEY:
        logger.warning("BRAVE_SEARCH_API_KEY not set — skipping web enrichment")
        return None, "BRAVE_SEARCH_API_KEY not configured", ""

    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        # Log Brave Search cost
        try:
            from src.modules.costs.tracker import log_api_call
            log_api_call("brave", "brave_search", "directory_enrichment",
                cost_usd=0.003)  # ~$0.003 per query
        except Exception:
            pass
        data = resp.json()
        results = data.get("web", {}).get("results", [])

        # Collect snippet text from all results as fallback
        snippets = []
        for r in results:
            title = r.get("title", "")
            desc = r.get("description", "")
            if title or desc:
                snippets.append(f"{title}: {desc}")
        snippet_text = "\n".join(snippets)

        if results:
            for r in results:
                url = r.get("url", "")
                if not any(d in url for d in _SKIP_DOMAINS):
                    return url, None, snippet_text
            # All results were directory sites — return snippets for fallback
            domains_found = [r.get("url", "")[:60] for r in results]
            return None, f"All {len(results)} Brave results were directory sites: {domains_found}", snippet_text
        return None, f"Brave Search returned 0 results for: {query}", ""
    except httpx.HTTPStatusError as e:
        msg = f"Brave Search HTTP {e.response.status_code}: {e.response.text[:200]}"
        logger.error(msg)
        return None, msg, ""
    except Exception as e:
        msg = f"Brave Search error: {e}"
        logger.error(msg)
        return None, msg, ""


def _fetch_page_text(url: str, max_chars: int = 3000) -> tuple[str, str | None]:
    """Fetch a URL and extract text content.

    Returns (text, error_reason). text is empty string on failure.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def _try_fetch(verify_ssl: bool = True) -> tuple[str, str | None]:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True, headers=headers, verify=verify_ssl)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if not text.strip():
            return "", f"Page at {url} returned no text content (HTTP {resp.status_code})"
        return text[:max_chars], None

    try:
        return _try_fetch(verify_ssl=True)
    except Exception as e:
        # Retry with SSL verification disabled for sites with bad certs
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e):
            try:
                logger.info(f"Retrying {url} with SSL verification disabled")
                return _try_fetch(verify_ssl=False)
            except httpx.HTTPStatusError as e2:
                msg = f"Page fetch HTTP {e2.response.status_code} for {url} (SSL-retry)"
                logger.error(msg)
                return "", msg
            except Exception as e2:
                msg = f"Page fetch failed for {url} (SSL-retry): {e2}"
                logger.error(msg)
                return "", msg
        if isinstance(e, httpx.HTTPStatusError):
            msg = f"Page fetch HTTP {e.response.status_code} for {url}"
        else:
            msg = f"Page fetch failed for {url}: {e}"
        logger.error(msg)
        return "", msg


def _llm_summarize(business_name: str, page_text: str) -> dict:
    """Use Qwen3-32B via Gradient to summarize business info into structured JSON."""
    if not GRADIENT_MODEL_ACCESS_KEY:
        logger.warning("GRADIENT_MODEL_ACCESS_KEY not set — skipping LLM enrichment")
        return {}

    from openai import OpenAI
    client = OpenAI(
        api_key=GRADIENT_MODEL_ACCESS_KEY,
        base_url=GRADIENT_BASE_URL,
    )

    prompt = f"""Based on this website content for "{business_name}", extract the following information as JSON.
If a field is not found, use null. Do NOT make up information.

Return ONLY valid JSON with these fields:
{{
  "description": "1-2 sentence summary of what this business does",
  "services": "comma-separated list of main services or departments they offer",
  "keywords": "comma-separated list of 15-30 specific products, brands, and items they sell or services they provide. Be very specific - e.g. for a hardware store: grills, Weber, tools, DeWalt, paint, Sherwin-Williams, lumber, plumbing supplies, electrical, lawn mowers, snow blowers, key cutting, screen repair, pipe fitting, garden supplies, fertilizer, mulch. For a grocery store: deli, bakery, pharmacy, organic produce, meat counter, catering, curbside pickup",
  "hours": "business hours if found, e.g. 'Mon-Fri 8am-5pm, Sat 9am-1pm'",
  "email": "contact email if found",
  "website": "website URL",
  "facebook": "Facebook page URL if found",
  "instagram": "Instagram URL if found"
}}

Website content:
{page_text[:2000]}"""

    try:
        response = client.chat.completions.create(
            model=GRADIENT_MODEL,
            max_tokens=512,
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a data extraction assistant. Return ONLY valid JSON. No explanation. No thinking."},
                {"role": "user", "content": prompt + "\n\nReturn ONLY the JSON object, nothing else:"},
            ],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        raw = response.choices[0].message.content or ""
        logger.info(f"LLM raw response for {business_name}: {raw[:200]}")

        # Log cost
        try:
            from src.modules.costs.tracker import log_api_call
            usage = getattr(response, "usage", None)
            log_api_call("gradient", GRADIENT_MODEL, "directory_enrichment",
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0)
        except Exception:
            pass

        # Qwen3 may wrap output in <think>...</think> tags — strip them
        if "<think>" in raw:
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Strip markdown fences
        if "```" in raw:
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()
        # Find the outermost JSON object (handles nested braces)
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    raw = raw[start : i + 1]
                    break
        return json.loads(raw)
    except Exception as e:
        logger.error(f"LLM summarization failed for {business_name}: {e}")
        return {}


def enrich_business(org_id: int) -> bool:
    """Enrich a single business directory entry.

    Returns True if enrichment succeeded.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM organizations WHERE id = ?", (org_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    org = dict(row)
    name = org.get("name", "")
    city = org.get("city", "") or ""
    state = org.get("state", "") or ""
    clean_name = _clean_business_name(name)

    # Sanitize city/state: strip newlines and whitespace
    city = " ".join(city.split()).strip()
    state = " ".join(state.split()).strip()

    logger.info(f"Enriching business: {name} → search as: {clean_name} ({city}, {state})")

    # Ensure enrichment_error column exists (safe migration)
    try:
        cursor.execute("SELECT enrichment_error FROM organizations LIMIT 0")
    except Exception:
        cursor.execute("ALTER TABLE organizations ADD COLUMN enrichment_error TEXT")

    # Step 1: Brave Search (use cleaned name without ad metadata)
    search_parts = [clean_name]
    if city:
        search_parts.append(city)
    if state:
        search_parts.append(state)
    search_query = " ".join(search_parts)
    url, search_err, snippet_text = _brave_search(search_query)

    if not url and not snippet_text:
        # No URL and no snippets — total failure
        cursor.execute(
            "UPDATE organizations SET enrichment_status = 'failed', enrichment_error = ?, updated_at = datetime('now') WHERE id = ?",
            (search_err, org_id),
        )
        conn.commit()
        conn.close()
        logger.warning(f"Enrichment failed for {clean_name}: {search_err}")
        return False

    # Step 2: Fetch page (or use snippet fallback)
    page_text = ""
    if url:
        page_text, fetch_err = _fetch_page_text(url)
        if not page_text and snippet_text:
            # Page fetch failed but we have search snippets — use those
            logger.info(f"Using Brave snippet fallback for {clean_name} (page fetch failed: {fetch_err})")
            page_text = snippet_text
        elif not page_text:
            cursor.execute(
                "UPDATE organizations SET enrichment_status = 'failed', enrichment_error = ?, website = ?, updated_at = datetime('now') WHERE id = ?",
                (fetch_err, url, org_id),
            )
            conn.commit()
            conn.close()
            logger.warning(f"Enrichment failed for {clean_name}: {fetch_err}")
            return False
    elif snippet_text:
        # No direct URL but have snippets from directory sites — use them
        logger.info(f"Using Brave snippet fallback for {clean_name} (all results were directory sites)")
        page_text = snippet_text

    # Step 3: LLM summarize (use cleaned name so LLM doesn't echo ad metadata)
    profile = _llm_summarize(clean_name, page_text)

    # Step 4: Update organizations table
    social = {}
    if profile.get("facebook"):
        social["facebook"] = profile["facebook"]
    if profile.get("instagram"):
        social["instagram"] = profile["instagram"]

    keywords = profile.get("keywords") or ""

    cursor.execute(
        """UPDATE organizations SET
            description = ?,
            services = ?,
            keywords = ?,
            hours_json = ?,
            email = ?,
            website = ?,
            social_json = ?,
            enrichment_status = 'enriched',
            enrichment_error = NULL,
            last_enriched_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?""",
        (
            profile.get("description") or "",
            profile.get("services") or "",
            keywords,
            profile.get("hours") or "",
            profile.get("email") or "",
            profile.get("website") or url,
            json.dumps(social) if social else "",
            org_id,
        ),
    )
    conn.commit()
    conn.close()

    logger.info(f"Enriched: {name} — {profile.get('description', '')[:80]}")
    return True


def enrich_pending_businesses() -> dict:
    """Enrich all businesses with enrichment_status='pending'.

    Returns summary dict with counts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name FROM organizations WHERE enrichment_status = 'pending'"
    )
    pending = cursor.fetchall()
    conn.close()

    total = len(pending)
    enriched = 0
    failed = 0

    for row in pending:
        org_id = row[0]
        try:
            if enrich_business(org_id):
                enriched += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Enrichment failed for org {org_id}: {e}")
            failed += 1

    logger.info(f"Enrichment batch: {enriched}/{total} enriched, {failed} failed")
    return {"total": total, "enriched": enriched, "failed": failed}
