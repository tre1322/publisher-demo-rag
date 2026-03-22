"""Query transformation agent for improving search queries."""

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

TRANSFORM_SYSTEM_PROMPT = """You are a search query optimizer for a news article database. Your job is to transform user questions into effective search queries.

Given a user's question, generate 1-3 search queries that would find relevant news articles. Consider:
- The user's intent (what information they're looking for)
- Key topics, entities, or themes
- Alternative phrasings that might match article content

Return a JSON object with:
- "queries": array of 1-3 search query strings
- "reasoning": brief explanation of your query strategy

Examples:

User: "What's happening today?"
Response: {"queries": ["breaking news", "latest headlines", "current events"], "reasoning": "Generic request for recent news - using common news-related terms"}

User: "Tell me about the election"
Response: {"queries": ["election results voting", "political campaign candidates", "election news polls"], "reasoning": "Broad political topic - covering results, campaigns, and polling"}

User: "Any tech news?"
Response: {"queries": ["technology innovation", "AI artificial intelligence", "tech industry companies"], "reasoning": "Technology sector - covering innovation, AI, and industry news"}

Always return valid JSON. Generate queries that would match news article content, not the exact user phrasing."""

TRANSFORM_USER_TEMPLATE = """Transform this user question into effective search queries:

User question: {question}

Return JSON with "queries" array and "reasoning" field."""


class QueryTransformer:
    """Transforms user queries into optimized search queries."""

    def __init__(self) -> None:
        """Initialize the query transformer."""
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def transform(self, user_query: str) -> list[str]:
        """Transform a user query into optimized search queries.

        Args:
            user_query: The original user question.

        Returns:
            List of optimized search queries.
        """
        logger.info(f"Transforming query: '{user_query}'")

        response_text = ""
        try:
            response = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=256,
                temperature=0.3,
                system=TRANSFORM_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": TRANSFORM_USER_TEMPLATE.format(question=user_query),
                    }
                ],
            )

            # Extract response text
            content_block = response.content[0]
            if hasattr(content_block, "text"):
                response_text = content_block.text  # type: ignore[union-attr]
            else:
                response_text = str(content_block)

            logger.debug(f"Raw transformer response: {response_text}")

            # Clean up response - extract JSON from markdown code blocks if present
            cleaned_text = response_text.strip()

            # Remove markdown code blocks
            if cleaned_text.startswith("```"):
                # Find the end of the code block
                lines = cleaned_text.split("\n")
                # Remove first line (```json or ```)
                lines = lines[1:]
                # Remove last line if it's closing ```
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned_text = "\n".join(lines)

            # Try to find JSON object in the text
            json_start = cleaned_text.find("{")
            json_end = cleaned_text.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                cleaned_text = cleaned_text[json_start:json_end]

            logger.debug(f"Cleaned text for parsing: {cleaned_text}")

            # Parse JSON response
            result = json.loads(cleaned_text)
            queries = result.get("queries", [user_query])
            reasoning = result.get("reasoning", "")

            logger.info(f"Generated {len(queries)} search queries: {queries}")
            logger.info(f"Reasoning: {reasoning}")

            return queries

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse transformer response: {e}")
            if response_text:
                logger.warning(f"Response text was: {response_text[:500]}")
            logger.info("Falling back to original query")
            return [user_query]
        except Exception as e:
            logger.error(f"Query transformation failed: {e}")
            logger.info("Falling back to original query")
            return [user_query]


def main() -> None:
    """Test the query transformer."""
    transformer = QueryTransformer()

    test_queries = [
        "What's happening today?",
        "Tell me about AI",
        "Any news about Ukraine?",
        "What are the latest political developments?",
    ]

    for query in test_queries:
        print(f"\nOriginal: {query}")
        transformed = transformer.transform(query)
        print(f"Transformed: {transformed}")
        print("-" * 50)


if __name__ == "__main__":
    main()
