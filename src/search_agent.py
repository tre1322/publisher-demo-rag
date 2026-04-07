"""Search agent that uses tools to find relevant articles."""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic

# Import config first to configure logging with timestamps
import src.core.config  # noqa: F401
from src.core.config import ANTHROPIC_API_KEY, LLM_MODEL
from src.search_tools import SearchTools, get_search_tools_schema

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are a search agent for a local newspaper database containing news articles, advertisements, events, and a business directory. Your job is to find ALL relevant content for user queries.

You have access to seven search tools:
1. semantic_search - Find articles by meaning/concepts
2. metadata_search - Filter articles by date, author, location, subject
3. hybrid_search - Combine semantic search with metadata filters (PREFERRED for articles)
4. search_advertisements - Find ads for local businesses, products, and services
5. search_directory - Find local businesses in the directory (name, services, location, phone)
6. search_events - Find local events like concerts, sports, arts, and community gatherings
7. get_database_info - Get info about publishers/newspapers and content counts

CRITICAL: For EVERY query, you MUST call ALL FOUR of these tools:
1. hybrid_search (or semantic_search) - to find relevant news articles
2. search_advertisements - to find relevant ads and local business services
3. search_directory - to find local businesses that may offer what the user needs
4. search_events - to find relevant events

Active advertisers (from search_advertisements) should be prioritized over directory-only listings (from search_directory). This ensures paying advertisers get top placement while still showing all relevant businesses.

For example, a query about "roofing" should return:
- News articles about roofing/construction
- Ads from local roofing companies (PRIORITY)
- Directory listings for hardware/building supply stores
- Any related events (home improvement shows, etc.)

Use appropriate filters based on the query:
- Subject filters for topic categories (Politics, Sports, Business, etc.)
- Date filters when user mentions timeframes
- Category filters to narrow results
- Location filters when user mentions places
- Price filters when user mentions budget
- on_sale_only=true when user asks about deals/sales
- free_only=true when user asks about free events

Today's date is {today}.

Date reference:
- "today" = {today}
- "yesterday" = {yesterday}
- "last week" = {last_week} to {today}
- "this month" = {month_start} to {today}
- "this weekend" = {weekend_start} to {weekend_end}
- "next week" = {today} to {next_week}

Return ALL search results to provide comprehensive answers."""


class SearchAgent:
    """Agent that uses tools to search for articles."""

    def __init__(self) -> None:
        """Initialize the search agent."""
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.search_tools = SearchTools()

        # Get dynamic tool schema with actual categories from database
        tools_schema = get_search_tools_schema()

        # Convert tool schema to Anthropic format
        self.tools = [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
            }
            for tool in tools_schema
        ]
        logger.info(f"Search agent initialized with {len(self.tools)} tools")

    def _get_system_prompt(self) -> str:
        """Get system prompt with current date context."""
        today = datetime.now()

        # Calculate weekend (Saturday and Sunday)
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0 and today.weekday() != 5:
            days_until_saturday = 7
        weekend_start = today + timedelta(days=days_until_saturday)
        weekend_end = weekend_start + timedelta(days=1)

        return AGENT_SYSTEM_PROMPT.format(
            today=today.strftime("%Y-%m-%d"),
            yesterday=(today - timedelta(days=1)).strftime("%Y-%m-%d"),
            last_week=(today - timedelta(days=7)).strftime("%Y-%m-%d"),
            month_start=today.replace(day=1).strftime("%Y-%m-%d"),
            weekend_start=weekend_start.strftime("%Y-%m-%d"),
            weekend_end=weekend_end.strftime("%Y-%m-%d"),
            next_week=(today + timedelta(days=7)).strftime("%Y-%m-%d"),
        )

    def _execute_tool(self, tool_name: str, tool_input: dict) -> list[dict]:
        """Execute a search tool and return results.

        Args:
            tool_name: Name of the tool to execute.
            tool_input: Tool parameters.

        Returns:
            Search results.
        """
        logger.info(f"Executing tool: {tool_name} with {tool_input}")

        pub = getattr(self, "_current_publisher", None)

        if tool_name == "semantic_search":
            return self.search_tools.semantic_search(
                query=tool_input.get("query", ""),
                top_k=tool_input.get("top_k", 5),
                publisher=pub,
            )
        elif tool_name == "metadata_search":
            return self.search_tools.metadata_search(
                date_from=tool_input.get("date_from"),
                date_to=tool_input.get("date_to"),
                author=tool_input.get("author"),
                location=tool_input.get("location"),
                subject=tool_input.get("subject"),
                publisher=pub,
            )
        elif tool_name == "hybrid_search":
            return self.search_tools.hybrid_search(
                query=tool_input.get("query", ""),
                date_from=tool_input.get("date_from"),
                date_to=tool_input.get("date_to"),
                location=tool_input.get("location"),
                subject=tool_input.get("subject"),
                publisher=pub,
            )
        elif tool_name == "search_advertisements":
            pub = getattr(self, "_current_publisher", None)
            return self.search_tools.search_advertisements(
                query=tool_input.get("query"),
                category=tool_input.get("category"),
                max_price=tool_input.get("max_price"),
                on_sale_only=tool_input.get("on_sale_only", False),
                publisher=pub,
            )
        elif tool_name == "search_directory":
            pub = getattr(self, "_current_publisher", None)
            return self.search_tools.search_directory(
                query=tool_input.get("query"),
                category=tool_input.get("category"),
                publisher=pub,
            )
        elif tool_name == "search_events":
            return self.search_tools.search_events(
                query=tool_input.get("query"),
                category=tool_input.get("category"),
                location=tool_input.get("location"),
                date_from=tool_input.get("date_from"),
                date_to=tool_input.get("date_to"),
                max_price=tool_input.get("max_price"),
                free_only=tool_input.get("free_only", False),
            )
        elif tool_name == "get_database_info":
            info = self.search_tools.get_database_info()
            # Return as a single result dict for consistency
            return [
                {
                    "text": f"Database contains content from: {', '.join(info['publishers'])}. "
                    f"Total: {info['article_count']} articles, "
                    f"{info['advertisement_count']} advertisements, "
                    f"{info['event_count']} events.",
                    "metadata": info,
                    "score": 1.0,
                    "search_type": "database_info",
                }
            ]
        else:
            logger.warning(f"Unknown tool: {tool_name}")
            return []

    def search(self, query: str, publisher: str | None = None) -> list[dict]:
        """Search for articles using the agent.

        Args:
            query: User's search query.
            publisher: Optional publisher name to restrict results to.

        Returns:
            List of relevant chunks/articles.
        """
        # Store publisher filter for use in _execute_tool
        self._current_publisher = publisher
        logger.info(f"Search agent processing: '{query}'" + (f" [publisher={publisher}]" if publisher else ""))

        messages: list[anthropic.types.MessageParam] = [
            {"role": "user", "content": query}
        ]

        # Call Claude with tools
        response = self.client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            temperature=0.1,
            system=self._get_system_prompt(),
            tools=self.tools,  # type: ignore[arg-type]
            messages=messages,
        )

        # Log cost
        try:
            from src.modules.costs.tracker import log_api_call
            usage = getattr(response, "usage", None)
            log_api_call("anthropic", LLM_MODEL, "search_agent",
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                publisher=getattr(self, "_current_publisher", None))
        except Exception:
            pass

        # Process response and execute tools
        all_results: list[dict] = []
        tool_results = []

        for content_block in response.content:
            if content_block.type == "tool_use":
                tool_name = content_block.name
                tool_input = content_block.input  # type: ignore[union-attr]

                # Execute the tool
                results = self._execute_tool(tool_name, tool_input)

                # For metadata_search, convert articles to chunks format
                if tool_name == "metadata_search" and results:
                    # Get chunks for each article
                    for article in results:
                        doc_id = article.get("doc_id")
                        if doc_id:
                            chunks = self.search_tools.get_chunks_for_article(doc_id)
                            if chunks:
                                all_results.extend(
                                    chunks[:2]
                                )  # Top 2 chunks per article
                else:
                    all_results.extend(results)

                # Log the results that will be available to the LLM
                logger.info(
                    f"Tool '{tool_name}' returned {len(results)} results"
                )
                for i, result in enumerate(results[:5]):  # Log first 5 results
                    if "title" in result.get("metadata", {}):
                        logger.info(
                            f"  Result {i + 1}: {result['metadata'].get('title', 'Unknown')[:60]}"
                        )
                    elif "title" in result:
                        logger.info(f"  Result {i + 1}: {result.get('title', 'Unknown')[:60]}")
                    elif "product_name" in result:
                        logger.info(
                            f"  Result {i + 1}: {result.get('product_name', 'Unknown')[:60]} "
                            f"({result.get('advertiser', 'Unknown')})"
                        )

                # Track tool result for potential follow-up
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": content_block.id,
                        "content": json.dumps(
                            {"count": len(results), "type": tool_name}
                        ),
                    }
                )

        # Deduplicate results by text hash
        seen = set()
        unique_results = []
        for result in all_results:
            text_hash = hash(result.get("text", ""))
            if text_hash not in seen:
                seen.add(text_hash)
                unique_results.append(result)

        # Sort by score
        unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(f"Search agent returned {len(unique_results)} unique results")
        return unique_results


def main() -> None:
    """Test the search agent."""
    agent = SearchAgent()

    test_queries = [
        "What's in the news today?",
        "Tell me about AI developments",
        "News from Ukraine",
        "Technology news from last week",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        print("-" * 50)
        results = agent.search(query)
        print(f"Found {len(results)} results")
        for i, result in enumerate(results[:3]):
            title = result.get("metadata", {}).get("title", "Unknown")[:50]
            score = result.get("score", 0)
            print(f"  {i + 1}. {title}... (score: {score:.3f})")


if __name__ == "__main__":
    main()
