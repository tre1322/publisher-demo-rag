"""Metadata extraction using Claude for article analysis."""

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

EXTRACTION_SYSTEM_PROMPT = """You are a metadata extraction assistant. Analyze news articles and extract structured metadata.

For each article, extract:
1. location: The primary geographic location mentioned (country, city, or region). Use "Global" if no specific location.
2. subjects: 2-4 topic categories that describe the article. Use standardized categories like:
   - Politics, Business, Technology, Science, Health, Sports, Entertainment,
   - Environment, Education, Crime, International, Economy, Military, Weather
3. summary: A 1-2 sentence summary of the article's main point.

Return a JSON object with these fields. Always return valid JSON."""

EXTRACTION_USER_TEMPLATE = """Extract metadata from this article:

Title: {title}
Author: {author}
Date: {date}

Content:
{content}

Return JSON with: location, subjects (array), summary"""


class MetadataExtractor:
    """Extracts structured metadata from article text using Claude."""

    def __init__(self) -> None:
        """Initialize the metadata extractor."""
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def extract(
        self,
        title: str,
        author: str,
        date: str,
        content: str,
    ) -> dict:
        """Extract metadata from article content.

        Args:
            title: Article title.
            author: Author name.
            date: Publication date.
            content: Article text content.

        Returns:
            Dictionary with location, subjects, and summary.
        """
        # Truncate content if too long
        max_content_chars = 4000
        if len(content) > max_content_chars:
            content = content[:max_content_chars] + "..."

        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=256,
                temperature=0.1,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": EXTRACTION_USER_TEMPLATE.format(
                            title=title,
                            author=author,
                            date=date,
                            content=content,
                        ),
                    }
                ],
            )

            # Extract response text
            content_block = response.content[0]
            if hasattr(content_block, "text"):
                response_text = content_block.text  # type: ignore[union-attr]
            else:
                response_text = str(content_block)

            # Clean up response - extract JSON
            cleaned_text = response_text.strip()

            # Remove markdown code blocks
            if cleaned_text.startswith("```"):
                lines = cleaned_text.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned_text = "\n".join(lines)

            # Find JSON object
            json_start = cleaned_text.find("{")
            json_end = cleaned_text.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                cleaned_text = cleaned_text[json_start:json_end]

            # Parse JSON
            result = json.loads(cleaned_text)

            metadata = {
                "location": result.get("location", "Unknown"),
                "subjects": result.get("subjects", []),
                "summary": result.get("summary", ""),
            }

            logger.debug(
                f"Extracted metadata for '{title[:50]}': "
                f"location={metadata['location']}, "
                f"subjects={metadata['subjects']}"
            )

            return metadata

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse extraction response: {e}")
            return self._default_metadata()
        except Exception as e:
            logger.error(f"Metadata extraction failed: {e}")
            return self._default_metadata()

    def _default_metadata(self) -> dict:
        """Return default metadata when extraction fails."""
        return {
            "location": "Unknown",
            "subjects": ["General"],
            "summary": "",
        }


def main() -> None:
    """Test the metadata extractor."""
    extractor = MetadataExtractor()

    test_article = {
        "title": "New AI Breakthrough in Medical Diagnosis",
        "author": "Jane Smith",
        "date": "2024-01-15",
        "content": """
        Researchers at Stanford University have developed a new artificial
        intelligence system that can diagnose certain types of cancer with
        95% accuracy, surpassing human doctors in early detection rates.

        The system, called MedAI, uses deep learning algorithms trained on
        millions of medical images. Clinical trials are set to begin next month
        at several hospitals across California.

        "This could revolutionize early cancer detection," said Dr. Michael Chen,
        lead researcher on the project. The technology is expected to be
        particularly useful in underserved areas with limited access to specialists.
        """,
    }

    result = extractor.extract(**test_article)
    print(f"Extracted metadata: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
