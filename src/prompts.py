"""Prompt templates for the Publisher RAG Demo."""

from urllib.parse import quote

HELP_MESSAGE = """I'm a news and local information assistant. Here's what I can help with:

**News Articles**
- Search by topic: "What's happening in technology?"
- Filter by date: "News from last week"
- Filter by location: "News about California"

**Product Deals**
- Find sales: "What's on sale?"
- Filter by category: "Electronics deals"
- Filter by price: "Products under $50"

**Local Events**
- Browse events: "What's happening this weekend?"
- Filter by type: "Concerts near me"
- Find free events: "Free events downtown"

Try asking about news, deals, or events!
"""

SYSTEM_PROMPT = """You are a helpful assistant for a news consumer.
Your role is to be a helpful assistant. You have access to local information and should prioritize using that in your responses.
You should gently bring people back to talking about local news, events, and shopping.

Rules:
- Only use information from the provided context
- ALWAYS cite sources using HTML hyperlinks that open in new tabs: <a href="url" target="_blank">Article Title</a>
- Be concise but complete
- If multiple articles discuss the topic, synthesize the information and cite all relevant sources
- When multiple articles cover the same topic from different dates, prefer the most recent one and mention the date
- Present the information as if you know it
- Do not mention that the information is based on an article
- Do not make any commentary on the information, such as whether it is complete or not
- You can have conversations with the user, but don't make up any information. General knowledge is ok.

Sponsored content disclosure (LEGAL REQUIREMENT):
- Content marked [SPONSORED] in the context is paid advertising
- When referencing sponsored content, ALWAYS include "[Sponsored]" before the link
- Example: [Sponsored] <a href="url" target="_blank">Product Name</a> from Advertiser
- This disclosure is legally required and must never be omitted

When answering from advertisements:
- ALWAYS name the business/advertiser explicitly (use the "Business:" field)
- Say "Country Road Greenhouse is advertising..." not just "there's a greenhouse..."
- Include the business name in every ad-based answer, even when summarizing
- If a follow-up question asks "who is that?" or "what's the name?" refer to the Business field from the prior ad context

When no results are found:
- If the context is empty or marked as "No results found", respond conversationally
- Vary your response naturally - don't always say the same thing
- Reference what the user asked about in your response
- Be helpful and suggest alternatives when appropriate
- ONLY suggest things that appear in the provided context — NEVER invent or guess local business names, event names, or places
- Keep it brief and friendly
- Examples of varied responses:
  - "I couldn't find any articles about [topic] in our recent editions."
  - "Hmm, I don't have any news on that right now. Try asking about a different topic."
  - "No results for [topic]. Want to try a different search?"
  - "I'm not seeing anything about that in my current database."

Conversation handling:
- You have access to the conversation history for context
- Handle follow-up questions naturally, understanding references like "that article", "tell me more", or "what else"
- Maintain consistency with your previous answers
- If the user asks about something from a previous turn but the current context doesn't include it, let them know you need to search for that specific information again"""

QUERY_TEMPLATE = """Context:
{context}

Question: {question}

Answer:"""


def make_tracked_url(
    url: str,
    content_type: str,
    content_id: str,
    conversation_id: int | None = None,
) -> str:
    """Wrap a URL in a tracking redirect.

    Uses absolute URL so Gradio opens links in a new tab.

    Args:
        url: The original URL to track.
        content_type: Type of content ('article', 'event', 'advertisement').
        content_id: ID of the content.
        conversation_id: Optional conversation ID for attribution.

    Returns:
        Tracking URL that redirects to the original URL.
    """
    from src.core.config import BASE_URL

    if not url:
        return ""

    params = f"url={quote(url, safe='')}&type={content_type}&id={quote(str(content_id), safe='')}"
    if conversation_id:
        params += f"&conv={conversation_id}"

    # Use absolute URL so Gradio opens in new tab
    return f"{BASE_URL}/track?{params}"


def get_content_id(chunk: dict) -> str:
    """Extract content ID from a search result chunk.

    Args:
        chunk: Search result chunk with metadata.

    Returns:
        Content ID string.
    """
    metadata = chunk.get("metadata", {})
    # Try different ID fields based on content type
    return (
        metadata.get("doc_id")
        or metadata.get("ad_id")
        or metadata.get("event_id")
        or "unknown"
    )


def format_context(
    chunks: list[dict], conversation_id: int | None = None
) -> str:
    """Format retrieved chunks into context string.

    Args:
        chunks: List of chunk dictionaries with text and metadata.
        conversation_id: Optional conversation ID for URL tracking.

    Returns:
        Formatted context string.
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        metadata = chunk.get("metadata", {})
        title = metadata.get("title", "Unknown")
        date = metadata.get("publish_date", "Unknown date")
        author = metadata.get("author", "Unknown author")
        text = chunk.get("text", "")

        # Get original URL and content info
        original_url = metadata.get("url", "")
        content_type = chunk.get("search_type", "article")
        content_id = get_content_id(chunk)

        # Create tracked URL if original URL exists
        if original_url:
            url = make_tracked_url(
                original_url, content_type, content_id, conversation_id
            )
        else:
            url = ""

        # Format differently for ads vs articles/events
        if content_type == "advertisement":
            advertiser = metadata.get("advertiser") or metadata.get("title") or "Unknown"
            product = metadata.get("product_name", "")
            ad_category = metadata.get("ad_category") or metadata.get("category") or ""
            location = metadata.get("location", "")

            ad_parts = [f"[SPONSORED Advertisement {i}]"]
            ad_parts.append(f"Business: {advertiser}")
            if product and product != advertiser:
                ad_parts.append(f"Product/Service: {product}")
            if ad_category:
                ad_parts.append(f"Category: {ad_category}")
            if location:
                ad_parts.append(f"Location: {location}")
            if url:
                ad_parts.append(f"URL: {url}")
            ad_parts.append(f"Promotion: {text}")

            context_parts.append("\n".join(ad_parts) + "\n")
        elif content_type == "event":
            context_parts.append(
                f"[Event {i}]\n"
                f"Title: {title}\n"
                f"Date: {date}\n"
                f"URL: {url}\n"
                f"Content: {text}\n"
            )
        else:
            context_parts.append(
                f"[Article {i}]\n"
                f"Title: {title}\n"
                f"Date: {date}\n"
                f"Author: {author}\n"
                f"URL: {url}\n"
                f"Content: {text}\n"
            )

    return "\n---\n".join(context_parts)


def ensure_sponsored_disclosure(response: str, chunks: list[dict]) -> str:
    """Ensure all ad references in response have [Sponsored] disclosure.

    This is a legal requirement - every ad reference MUST be disclosed.
    Post-processes Claude's response to inject [Sponsored] if missing.

    Args:
        response: Claude's generated response.
        chunks: Search result chunks that were in context.

    Returns:
        Response with guaranteed [Sponsored] disclosure for all ads.
    """
    import re

    # Extract ad identifiers from chunks
    ad_identifiers: list[tuple[str, str]] = []  # (product_name, advertiser)
    for chunk in chunks:
        if chunk.get("search_type") != "advertisement":
            continue
        metadata = chunk.get("metadata", {})
        product_name = metadata.get("product_name", "")
        advertiser = metadata.get("advertiser", "")
        if product_name:
            ad_identifiers.append((product_name, advertiser))

    if not ad_identifiers:
        return response  # No ads in context

    modified = response

    for product_name, advertiser in ad_identifiers:
        # Check if product name appears in response
        if product_name not in modified:
            continue

        # Find all occurrences of the product name
        # Check if [Sponsored] appears within 50 chars before each occurrence
        pattern = re.compile(re.escape(product_name))
        offset = 0

        for match in pattern.finditer(response):
            start = match.start()
            # Look back up to 50 chars for [Sponsored]
            lookback_start = max(0, start - 50)
            lookback_text = response[lookback_start:start]

            if "[Sponsored]" not in lookback_text and "[sponsored]" not in lookback_text.lower():
                # Need to inject [Sponsored] before this occurrence
                # Find the right injection point - before any HTML tag or at match start
                inject_pos = start + offset

                # If this is inside an <a> tag, inject before the <a>
                # Look for <a that precedes this without a closing >
                before_text = modified[:inject_pos]
                last_a_open = before_text.rfind("<a ")
                last_a_close = before_text.rfind("</a>")

                if last_a_open > last_a_close and last_a_open > before_text.rfind(">", 0, last_a_open):
                    # We're inside an unclosed <a> tag, inject before it
                    inject_pos = last_a_open + offset
                    # But make sure we're not already preceded by [Sponsored]
                    pre_check = modified[max(0, inject_pos - 15):inject_pos]
                    if "[Sponsored]" in pre_check:
                        continue

                # Inject [Sponsored] with a space
                modified = modified[:inject_pos] + "[Sponsored] " + modified[inject_pos:]
                offset += len("[Sponsored] ")

    return modified


def format_sources(chunks: list[dict]) -> str:
    """Format source attribution for response.

    Args:
        chunks: List of chunk dictionaries with metadata.

    Returns:
        Formatted sources string.
    """
    sources = []
    seen_docs = set()

    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        doc_id = metadata.get("doc_id", "")

        if doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)

        title = metadata.get("title", "Unknown")
        date = metadata.get("publish_date", "Unknown date")
        score = chunk.get("score", 0.0)

        sources.append(f"• {title} ({date}) - Relevance: {score:.2f}")

    if sources:
        return "\n\n**Sources:**\n" + "\n".join(sources)
    return ""
