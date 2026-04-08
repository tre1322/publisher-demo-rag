"""Test OpenAI GPT-5.4 vision extraction on a single PDF page.

Usage:
    # Test page 1 (default):
    uv run python scripts/test_openai_vision.py --pdf path/to/paper.pdf

    # Test specific page:
    uv run python scripts/test_openai_vision.py --pdf path/to/paper.pdf --page 7

    # With reasoning (extended thinking):
    uv run python scripts/test_openai_vision.py --pdf path/to/paper.pdf --reasoning high

Requires: OPENAI_API_KEY environment variable set.
"""

import argparse
import base64
import json
import os
import sys
import time

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROMPT = """You are a newspaper text transcription tool. Your job is to TRANSCRIBE the exact text from this newspaper page into structured JSON.

CRITICAL: You must copy the EXACT words printed on the page. Do NOT summarize, paraphrase, shorten, or make up any text. Every word in body_text must appear on the page exactly as printed. If you cannot read a word, use [illegible]. Never fabricate or infer text that isn't visible.

Return ONLY valid JSON with this structure:

{
  "page_num": PAGE_NUM,
  "page_type": "<front|inside|sports|classifieds|opinion|obituaries>",
  "articles": [
    {
      "headline": "<exact headline text as printed>",
      "subheadline": "<exact subheadline text, or null>",
      "byline": "<author name without 'By' prefix, or null>",
      "body_text": "<EXACT article body text transcribed word-for-word from the page. Preserve paragraph breaks with \\n\\n. Copy every sentence completely.>",
      "jump_out": {
        "keyword": "<slug keyword like COUNCIL or SCHOOL or null>",
        "target_page": <page number or null>
      },
      "jump_in": {
        "keyword": "<slug keyword or null>",
        "source_page": <page number or null>
      },
      "is_continuation": <true if this article continues from another page, else false>,
      "is_ad": false
    }
  ]
}

Rules:
- TRANSCRIBE every article's body text EXACTLY as printed — word for word, sentence for sentence
- Do NOT summarize, paraphrase, or shorten any text
- Do NOT make up or infer text that is not visible on the page
- Preserve paragraph breaks as \\n\\n in body_text
- For jump references like "SEE COUNCIL • PAGE 8" or "COUNCIL/ FROM PAGE 1" or "KEYWORD • Page N", set jump_out or jump_in accordingly
- If an article continues to another page, set jump_out with the keyword and target page
- If this is a continuation from another page, set is_continuation=true and jump_in with source page
- Do NOT include advertisements in the articles array
- Do NOT include photo captions, pull quotes, or page furniture in articles
- For byline, extract just the name (remove "By" prefix)
- Return ONLY the JSON object, no explanation, no markdown code fences"""


def test_with_pdf_input(pdf_path: str, page_num: int, reasoning: str | None = None):
    """Send a PDF page directly to GPT-5.4 using OpenAI's PDF file input."""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    # Read PDF and encode as base64
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    print(f"PDF: {pdf_path} ({len(pdf_bytes):,} bytes)")
    print(f"Page: {page_num}")
    print(f"Reasoning: {reasoning or 'none'}")
    print(f"Model: gpt-5.4")
    print("Sending to OpenAI API...")
    print()

    prompt = PROMPT.replace("PAGE_NUM", str(page_num))
    # Add page-specific instruction
    prompt = f"Extract articles from PAGE {page_num} ONLY of this newspaper PDF.\n\n" + prompt

    start = time.time()

    # Build request kwargs
    filename = os.path.basename(pdf_path)
    kwargs = {
        "model": "gpt-5.4",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{pdf_b64}",
                    },
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                ],
            }
        ],
    }

    # Add reasoning if requested
    if reasoning:
        kwargs["reasoning"] = {"effort": reasoning}

    response = client.responses.create(**kwargs)

    elapsed = round(time.time() - start, 1)

    # Extract text from response — handle both standard and reasoning output formats
    raw_text = ""
    for item in response.output:
        # Standard message format
        if hasattr(item, "content") and item.content is not None:
            for block in item.content:
                if hasattr(block, "text"):
                    raw_text += block.text
        # Some responses have text directly on the item
        if hasattr(item, "text") and item.text:
            raw_text += item.text

    # Fallback: try response.output_text if available
    if not raw_text and hasattr(response, "output_text") and response.output_text:
        raw_text = response.output_text

    # Parse usage
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    reasoning_tokens = 0
    if usage and hasattr(usage, "output_tokens_details"):
        details = usage.output_tokens_details
        reasoning_tokens = getattr(details, "reasoning_tokens", 0) if details else 0

    print(f"Response received in {elapsed}s")
    print(f"Input tokens: {input_tokens:,}")
    print(f"Output tokens: {output_tokens:,}")
    if reasoning_tokens:
        print(f"Reasoning tokens: {reasoning_tokens:,}")

    # Estimate cost ($2.50/M input, $15/M output)
    cost = (input_tokens * 2.50 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
    print(f"Estimated cost: ${cost:.4f}")
    print()

    # Try to parse JSON
    clean = raw_text.strip()
    if clean.startswith("```"):
        import re
        clean = re.sub(r"^```\w*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)

    try:
        result = json.loads(clean)
        articles = result.get("articles", [])
        print(f"Extracted {len(articles)} articles:")
        print()
        for i, art in enumerate(articles):
            hl = art.get("headline", "")[:70]
            by = art.get("byline", "") or ""
            body = art.get("body_text", "")
            jump = art.get("jump_out", {})
            cont = art.get("is_continuation", False)
            jump_str = ""
            if jump and jump.get("keyword"):
                jump_str = f" -> {jump['keyword']} p.{jump.get('target_page', '?')}"
            cont_str = " [CONTINUATION]" if cont else ""

            print(f"  {i+1}. {hl}")
            if by:
                print(f"     By: {by}")
            print(f"     Body: {len(body)} chars{jump_str}{cont_str}")
            print(f"     First 150: {body[:150]}")
            print()

        # Save full result
        out_path = pdf_path.rsplit(".", 1)[0] + f"_page{page_num}_gpt54.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Full JSON saved to: {out_path}")

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw response ({len(raw_text)} chars):")
        print(raw_text[:2000])

        # Save raw response for debugging
        out_path = pdf_path.rsplit(".", 1)[0] + f"_page{page_num}_raw.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        print(f"Raw response saved to: {out_path}")


def test_with_image_input(pdf_path: str, page_num: int, reasoning: str | None = None):
    """Render PDF page to image and send to GPT-5.4 (fallback method)."""
    import fitz
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    # Render page
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]
    zoom = 200 / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()

    img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

    print(f"PDF: {pdf_path}")
    print(f"Page: {page_num} (rendered as PNG, {len(img_bytes):,} bytes)")
    print(f"Reasoning: {reasoning or 'none'}")
    print("Sending image to OpenAI API...")
    print()

    prompt = PROMPT.replace("PAGE_NUM", str(page_num))

    start = time.time()

    kwargs = {
        "model": "gpt-5.4",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{img_b64}",
                    },
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                ],
            }
        ],
    }

    if reasoning:
        kwargs["reasoning"] = {"effort": reasoning}

    response = client.responses.create(**kwargs)

    elapsed = round(time.time() - start, 1)

    raw_text = ""
    for item in response.output:
        if hasattr(item, "content") and item.content is not None:
            for block in item.content:
                if hasattr(block, "text"):
                    raw_text += block.text
        if hasattr(item, "text") and item.text:
            raw_text += item.text
    if not raw_text and hasattr(response, "output_text") and response.output_text:
        raw_text = response.output_text

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    reasoning_tokens = 0
    if usage and hasattr(usage, "output_tokens_details"):
        details = usage.output_tokens_details
        reasoning_tokens = getattr(details, "reasoning_tokens", 0) if details else 0

    print(f"Response received in {elapsed}s")
    print(f"Input tokens: {input_tokens:,}")
    print(f"Output tokens: {output_tokens:,}")
    if reasoning_tokens:
        print(f"Reasoning tokens: {reasoning_tokens:,}")
    cost = (input_tokens * 2.50 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
    print(f"Estimated cost: ${cost:.4f}")
    print()

    # Try to parse JSON
    import re as _re
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = _re.sub(r"^```\w*\n?", "", clean)
        clean = _re.sub(r"\n?```$", "", clean)

    try:
        result = json.loads(clean)
        articles = result.get("articles", [])
        print(f"Extracted {len(articles)} articles:")
        for i, art in enumerate(articles):
            hl = art.get("headline", "")[:70]
            body = art.get("body_text", "")
            print(f"  {i+1}. {hl} ({len(body)} chars)")
    except json.JSONDecodeError:
        print(f"Raw response ({len(raw_text)} chars):")
        print(raw_text[:1000])

    out_path = pdf_path.rsplit(".", 1)[0] + f"_page{page_num}_gpt54_image.json"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(raw_text)
    print(f"Saved to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test GPT-5.4 vision on a newspaper PDF")
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--page", type=int, default=1, help="Page number (1-indexed)")
    parser.add_argument("--reasoning", choices=["low", "medium", "high"], default=None,
                        help="Reasoning effort level (extended thinking)")
    parser.add_argument("--method", choices=["pdf", "image"], default="pdf",
                        help="Send PDF directly or render as image first")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"File not found: {args.pdf}")
        sys.exit(1)

    if args.method == "pdf":
        test_with_pdf_input(args.pdf, args.page, args.reasoning)
    else:
        test_with_image_input(args.pdf, args.page, args.reasoning)
