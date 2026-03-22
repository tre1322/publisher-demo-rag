"""Metadata extraction using Claude for advertisement analysis."""

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

EXTRACTION_SYSTEM_PROMPT = """You are an advertisement metadata extraction assistant. Analyze ad text and extract structured metadata.

For each advertisement, extract:
1. advertiser: The business/company name
2. product_name: The specific product, service, or promotion name
3. description: A clear description of the offer (1-2 sentences)
4. category: One of: Dining, Grocery, Hardware, Automotive, Florist, Insurance, Gifts, Services, Retail, Entertainment, Health, Real Estate, Other
5. price: The main price mentioned (as a number, or null if not mentioned)
6. original_price: The original/regular price if a discount is shown (as a number, or null)
7. discount_percent: The discount percentage if mentioned (as a number, or null)
8. valid_from: Start date if mentioned (YYYY-MM-DD format, or null)
9. valid_to: End/expiration date if mentioned (YYYY-MM-DD format, or null)

Return a JSON object with these fields. Always return valid JSON. Use null for missing values."""

EXTRACTION_USER_TEMPLATE = """Extract advertisement metadata from this text:

{raw_text}

Return JSON with: advertiser, product_name, description, category, price, original_price, discount_percent, valid_from, valid_to"""

ENHANCE_SYSTEM_PROMPT = """You are an advertisement metadata assistant. Given partial ad information, fill in missing fields.

Return a JSON object with:
1. category: One of: Dining, Grocery, Hardware, Automotive, Florist, Insurance, Gifts, Services, Retail, Entertainment, Health, Real Estate, Other
2. description: An enhanced description if the current one is sparse or missing (1-2 sentences)
3. discount_percent: Calculate from price/original_price if both are provided but discount is missing

Always return valid JSON."""

ENHANCE_USER_TEMPLATE = """Enhance this advertisement metadata:

Advertiser: {advertiser}
Product: {product_name}
Current Description: {description}
Category: {category}
Price: {price}
Original Price: {original_price}
Discount: {discount_percent}

Return JSON with: category, description, discount_percent (only include fields that need updating)"""


class AdMetadataExtractor:
    """Extracts structured metadata from advertisement text using Claude."""

    def __init__(self) -> None:
        """Initialize the metadata extractor."""
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract_from_raw_text(self, raw_text: str) -> dict:
        """Extract all metadata fields from raw advertisement text.

        Args:
            raw_text: Raw advertisement text.

        Returns:
            Dictionary with all ad fields.
        """
        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=512,
                temperature=0.1,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": EXTRACTION_USER_TEMPLATE.format(raw_text=raw_text),
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
                f"Extracted ad metadata: advertiser={result.get('advertiser')}, "
                f"product={result.get('product_name')}"
            )

            return result

        except Exception as e:
            logger.error(f"Ad metadata extraction failed: {e}")
            return self._default_metadata()

    def enhance_metadata(
        self,
        advertiser: str,
        product_name: str,
        description: str | None = None,
        category: str | None = None,
        price: float | None = None,
        original_price: float | None = None,
        discount_percent: float | None = None,
    ) -> dict:
        """Enhance existing ad metadata by filling in missing fields.

        Args:
            advertiser: Business name.
            product_name: Product/service name.
            description: Current description.
            category: Current category.
            price: Current price.
            original_price: Original price.
            discount_percent: Current discount.

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
                            advertiser=advertiser,
                            product_name=product_name,
                            description=description or "Not provided",
                            category=category or "Not provided",
                            price=price or "Not provided",
                            original_price=original_price or "Not provided",
                            discount_percent=discount_percent or "Not provided",
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

            logger.debug(f"Enhanced ad metadata for '{product_name}'")

            return result

        except Exception as e:
            logger.error(f"Ad metadata enhancement failed: {e}")
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
            "advertiser": "Unknown",
            "product_name": "Unknown",
            "description": None,
            "category": "Other",
            "price": None,
            "original_price": None,
            "discount_percent": None,
            "valid_from": None,
            "valid_to": None,
        }


def main() -> None:
    """Test the ad metadata extractor."""
    extractor = AdMetadataExtractor()

    # Test raw text extraction
    test_ad = """
    Stonehouse & Quarry Lounge
    Prime Rib Friday Special!
    Join us every Friday for our famous slow-roasted prime rib dinner.
    Includes salad bar and choice of potato.
    Only $24.99 (regularly $29.99)
    Valid through December 31st
    """

    print("Testing raw text extraction:")
    result = extractor.extract_from_raw_text(test_ad)
    print(f"Result: {json.dumps(result, indent=2)}")

    # Test enhancement
    print("\nTesting metadata enhancement:")
    enhanced = extractor.enhance_metadata(
        advertiser="Joe's Hardware",
        product_name="Winter Tool Sale",
        description=None,
        category=None,
        price=79.99,
        original_price=99.99,
        discount_percent=None,
    )
    print(f"Enhanced: {json.dumps(enhanced, indent=2)}")


if __name__ == "__main__":
    main()
