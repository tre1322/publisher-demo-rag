"""Metadata extraction using Claude for event analysis."""

import json
import logging
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic

from src.core.config import ANTHROPIC_API_KEY, LLM_MODEL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are an event metadata extraction assistant. Analyze event text and extract structured metadata.

For each event, extract:
1. title: The event name/title
2. description: A clear description of the event (1-2 sentences)
3. location: The venue or place name
4. address: Full address if mentioned (include city, state, zip if available)
5. event_date: The date (YYYY-MM-DD format, or null if not clear)
6. event_time: Start time (HH:MM in 24-hour format, or null)
7. end_time: End time (HH:MM in 24-hour format, or null)
8. category: One of: Community, Entertainment, Sports, Education, Religious, Family, Arts, Music, Food, Business, Other
9. price: Ticket/entry price as a number (use 0 for free events, null if not mentioned)

Return a JSON object with these fields. Always return valid JSON. Use null for missing values.
For dates, try to infer the year as the current or next occurrence if not specified."""

EXTRACTION_USER_TEMPLATE = """Extract event metadata from this text:

{raw_text}

Today's date for reference: {today}

Return JSON with: title, description, location, address, event_date, event_time, end_time, category, price"""

ENHANCE_SYSTEM_PROMPT = """You are an event metadata assistant. Given partial event information, fill in missing fields.

Return a JSON object with:
1. category: One of: Community, Entertainment, Sports, Education, Religious, Family, Arts, Music, Food, Business, Other
2. description: An enhanced description if the current one is sparse or missing (1-2 sentences)

Always return valid JSON."""

ENHANCE_USER_TEMPLATE = """Enhance this event metadata:

Title: {title}
Current Description: {description}
Location: {location}
Address: {address}
Date: {event_date}
Time: {event_time}
Category: {category}
Price: {price}

Return JSON with: category, description (only include fields that need updating)"""


class EventMetadataExtractor:
    """Extracts structured metadata from event text using Claude."""

    def __init__(self) -> None:
        """Initialize the metadata extractor."""
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract_from_raw_text(self, raw_text: str) -> dict:
        """Extract all metadata fields from raw event text.

        Args:
            raw_text: Raw event text.

        Returns:
            Dictionary with all event fields.
        """
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")

        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=512,
                temperature=0.1,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": EXTRACTION_USER_TEMPLATE.format(
                            raw_text=raw_text, today=today
                        ),
                    }
                ],
            )

            # Extract response text
            content_block = response.content[0]
            if hasattr(content_block, "text"):
                response_text = content_block.text
            else:
                response_text = str(content_block)

            # Parse JSON from response
            result = self._parse_json_response(response_text)

            logger.debug(
                f"Extracted event metadata: title={result.get('title')}, "
                f"date={result.get('event_date')}"
            )

            return result

        except Exception as e:
            logger.error(f"Event metadata extraction failed: {e}")
            return self._default_metadata()

    def enhance_metadata(
        self,
        title: str,
        description: str | None = None,
        location: str | None = None,
        address: str | None = None,
        event_date: str | None = None,
        event_time: str | None = None,
        category: str | None = None,
        price: float | None = None,
    ) -> dict:
        """Enhance existing event metadata by filling in missing fields.

        Args:
            title: Event title.
            description: Current description.
            location: Venue name.
            address: Full address.
            event_date: Event date.
            event_time: Start time.
            category: Current category.
            price: Ticket price.

        Returns:
            Dictionary with enhanced fields.
        """
        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=256,
                temperature=0.1,
                system=ENHANCE_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": ENHANCE_USER_TEMPLATE.format(
                            title=title,
                            description=description or "Not provided",
                            location=location or "Not provided",
                            address=address or "Not provided",
                            event_date=event_date or "Not provided",
                            event_time=event_time or "Not provided",
                            category=category or "Not provided",
                            price=price if price is not None else "Not provided",
                        ),
                    }
                ],
            )

            content_block = response.content[0]
            if hasattr(content_block, "text"):
                response_text = content_block.text
            else:
                response_text = str(content_block)

            result = self._parse_json_response(response_text)

            logger.debug(f"Enhanced event metadata for '{title}'")

            return result

        except Exception as e:
            logger.error(f"Event metadata enhancement failed: {e}")
            return {}

    def _parse_json_response(self, response_text: str) -> dict:
        """Parse JSON from Claude response.

        Args:
            response_text: Raw response text.

        Returns:
            Parsed dictionary.
        """
        cleaned_text = response_text.strip()

        # Remove markdown code blocks
        if cleaned_text.startswith("```"):
            lines = cleaned_text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned_text = "\n".join(lines)

        # Find first complete JSON object by matching braces
        json_start = cleaned_text.find("{")
        if json_start == -1:
            return self._default_metadata()

        # Count braces to find the matching closing brace
        brace_count = 0
        json_end = json_start
        for i, char in enumerate(cleaned_text[json_start:], start=json_start):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break

        if json_end > json_start:
            cleaned_text = cleaned_text[json_start:json_end]

        return json.loads(cleaned_text)

    def _default_metadata(self) -> dict:
        """Return default metadata when extraction fails."""
        return {
            "title": "Unknown Event",
            "description": None,
            "location": None,
            "address": None,
            "event_date": None,
            "event_time": None,
            "end_time": None,
            "category": "Other",
            "price": None,
        }


def main() -> None:
    """Test the event metadata extractor."""
    extractor = EventMetadataExtractor()

    # Test raw text extraction
    test_event = """
    Pipestone Holiday Parade
    Saturday, December 14th at 2:00 PM

    Join us on Main Street for our annual holiday parade featuring
    floats, marching bands, and Santa Claus! Hot cocoa available
    at the Pipestone Area Chamber tent.

    Free admission - bring the whole family!
    Main Street, Pipestone, MN 56164
    """

    print("Testing raw text extraction:")
    result = extractor.extract_from_raw_text(test_event)
    print(f"Result: {json.dumps(result, indent=2)}")

    # Test enhancement
    print("\nTesting metadata enhancement:")
    enhanced = extractor.enhance_metadata(
        title="Youth Basketball Tournament",
        description=None,
        location="Pipestone High School",
        address=None,
        event_date="2025-12-28",
        event_time="09:00",
        category=None,
        price=5.00,
    )
    print(f"Enhanced: {json.dumps(enhanced, indent=2)}")


if __name__ == "__main__":
    main()
