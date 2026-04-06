"""Query engine for the Publisher RAG Demo.

Supports two LLM providers:
- "anthropic": Claude via Anthropic API (default)
- "gradient": Qwen/Llama via DigitalOcean Gradient Serverless Inference
"""

import logging
from collections.abc import Iterator

import anthropic
from sentence_transformers import SentenceTransformer

# Config import configures logging with timestamps
from src.core.config import (
    ANTHROPIC_API_KEY,
    EMBEDDING_MODEL,
    GRADIENT_BASE_URL,
    GRADIENT_MODEL,
    GRADIENT_MODEL_ACCESS_KEY,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TEMPERATURE,
    MAX_CONTEXT_TOKENS,
    RETRIEVAL_TOP_K,
    SIMILARITY_THRESHOLD,
)
from src.prompts import (
    HELP_MESSAGE,
    QUERY_TEMPLATE,
    SYSTEM_PROMPT,
    format_context,
    format_sources,
    get_system_prompt,
)
from src.search_agent import SearchAgent

logger = logging.getLogger(__name__)

# Maximum number of conversation turns to keep in history
MAX_HISTORY_TURNS = 10


class QueryEngine:
    """Handles query processing and response generation."""

    def __init__(self) -> None:
        """Initialize the query engine."""
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)

        # Use articles collection for direct retrieval fallback
        from src.core.vector_store import get_articles_collection, get_legacy_collection

        try:
            self.collection = get_articles_collection()
            self.legacy_collection = get_legacy_collection()
        except Exception as e:
            logger.error(f"Failed to initialize collections: {e}")
            self.collection = None
            self.legacy_collection = None

        self.llm_provider = LLM_PROVIDER.lower()
        logger.info(f"LLM provider: {self.llm_provider}")

        if self.llm_provider == "gradient":
            if not GRADIENT_MODEL_ACCESS_KEY:
                raise ValueError("GRADIENT_MODEL_ACCESS_KEY not set. Required for gradient provider.")
            # Use OpenAI SDK with Gradient's OpenAI-compatible endpoint
            from openai import OpenAI
            self.openai_client = OpenAI(
                api_key=GRADIENT_MODEL_ACCESS_KEY,
                base_url=GRADIENT_BASE_URL,
            )
            self.anthropic_client = None
            logger.info(f"Gradient LLM initialized: model={GRADIENT_MODEL}, base_url={GRADIENT_BASE_URL}")
        else:
            if not ANTHROPIC_API_KEY:
                raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")
            self.anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            self.openai_client = None
            logger.info(f"Anthropic LLM initialized: model={LLM_MODEL}")

        # Initialize search agent for intelligent search
        try:
            self.search_agent = SearchAgent()
            logger.info("Search agent initialized")
        except Exception as e:
            logger.error(f"Search agent init failed: {e}. Falling back to direct retrieval.")
            self.search_agent = None

        # Track consecutive empty results for help suggestions
        self._consecutive_empty_results = 0
        self._empty_results_threshold = 3

    def retrieve(self, query: str, publisher: str | None = None) -> list[dict]:
        """Retrieve relevant chunks for a query.

        Args:
            query: The user's query.
            publisher: Optional publisher name to filter results (e.g. "Pipestone County Star").
                       When set, only articles from this publisher are returned.

        Returns:
            List of relevant chunks with metadata and scores.
        """
        if self.collection is None:
            logger.warning("Collection is None - no documents indexed")
            return []

        logger.info(f"Query: '{query}'" + (f" [publisher={publisher}]" if publisher else ""))
        logger.info(f"Collection has {self.collection.count()} chunks")

        # Generate query embedding
        query_embedding = self.embedding_model.encode(query).tolist()
        logger.info("Generated query embedding")

        # Query ChromaDB — optionally filter by publisher
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": RETRIEVAL_TOP_K,
            "include": ["documents", "metadatas", "distances"],
        }
        if publisher:
            query_kwargs["where"] = {"publisher": publisher}
            logger.info(f"Filtering to publisher: {publisher}")

        results = self.collection.query(**query_kwargs)

        # Process results
        chunks = []
        total_retrieved = len(results["documents"][0]) if results["documents"] else 0
        logger.info(f"ChromaDB returned {total_retrieved} chunks")

        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                # Convert distance to similarity score (cosine)
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance  # Convert distance to similarity

                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                title = str(metadata.get("title", "Unknown"))[:50]

                content_type = str(metadata.get("content_type", "article"))
                doc_preview = str(doc)[:120].replace("\n", " ") if doc else "(empty)"
                logger.info(
                    f"  Chunk {i + 1}: score={score:.3f}, "
                    f"type={content_type}, title='{title}', "
                    f"text='{doc_preview}...'"
                )

                # Filter by similarity threshold
                if score < SIMILARITY_THRESHOLD:
                    logger.info(
                        f"    -> FILTERED OUT (score {score:.3f} < "
                        f"threshold {SIMILARITY_THRESHOLD})"
                    )
                    continue

                # Boost score for recent articles
                publish_date = str(metadata.get("edition_date", "") or metadata.get("publish_date", ""))
                freshness_boost = 1.0
                if publish_date:
                    try:
                        from datetime import datetime
                        pd = datetime.strptime(publish_date, "%Y-%m-%d")
                        age_days = (datetime.now() - pd).days
                        if age_days <= 7:
                            freshness_boost = 1.15
                        elif age_days <= 30:
                            freshness_boost = 1.05
                    except (ValueError, TypeError):
                        pass

                chunk = {
                    "text": doc,
                    "metadata": metadata,
                    "score": score * freshness_boost,
                }
                chunks.append(chunk)

        # Re-sort by boosted score
        chunks.sort(key=lambda c: c["score"], reverse=True)

        logger.info(
            f"After filtering: {len(chunks)} chunks pass threshold "
            f"(threshold={SIMILARITY_THRESHOLD})"
        )
        return chunks

    def _truncate_context(self, chunks: list[dict]) -> list[dict]:
        """Truncate context to fit within token limit.

        Args:
            chunks: List of chunks.

        Returns:
            Truncated list of chunks.
        """
        # Simple word-based estimation (rough approximation)
        total_words = 0
        truncated = []

        for chunk in chunks:
            chunk_words = len(chunk["text"].split())
            if total_words + chunk_words > MAX_CONTEXT_TOKENS:
                # Keep at least top 2 chunks
                if len(truncated) >= 2:
                    break
            truncated.append(chunk)
            total_words += chunk_words

        return truncated

    def generate_response(
        self, query: str, chunks: list[dict], history: list[dict] | None = None
    ) -> str:
        """Generate a response using Claude.

        Args:
            query: The user's query.
            chunks: Retrieved chunks for context.
            history: Conversation history (list of {"role": "user/assistant", "content": "..."}).

        Returns:
            Generated response.
        """
        # Handle empty results - let Claude generate a conversational response
        if not chunks:
            logger.info(
                "No chunks available - letting Claude generate conversational response"
            )
            context = "No results found for this query."
        else:
            # Truncate context if needed
            original_count = len(chunks)
            chunks = self._truncate_context(chunks)
            if len(chunks) < original_count:
                logger.info(f"Truncated from {original_count} to {len(chunks)} chunks")

            # Format context
            context = format_context(chunks)
            context_words = len(context.split())
            logger.info(
                f"Context size: {context_words} words from {len(chunks)} chunks"
            )

        # Build prompt
        user_message = QUERY_TEMPLATE.format(context=context, question=query)

        # Build messages array with history
        messages: list[dict[str, str]] = []

        if history:
            # Limit history to MAX_HISTORY_TURNS (each turn = user + assistant)
            limited_history = history[-(MAX_HISTORY_TURNS * 2) :]
            logger.info(
                f"Including {len(limited_history)} messages from conversation history"
            )

            for msg in limited_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current query with context
        messages.append({"role": "user", "content": user_message})

        # Call LLM API
        if self.llm_provider == "gradient":
            logger.info(f"Calling Gradient API ({GRADIENT_MODEL})...")
            # OpenAI-compatible format — system prompt goes as first message
            sys_prompt = get_system_prompt(getattr(self, "_current_publisher", "") or "your local newspaper")
            oai_messages = [{"role": "system", "content": sys_prompt}] + messages
            response = self.openai_client.chat.completions.create(
                model=GRADIENT_MODEL,
                max_tokens=1024,
                temperature=LLM_TEMPERATURE,
                messages=oai_messages,
            )
            logger.info("Received response from Gradient")
            # Log cost
            try:
                from src.modules.costs.tracker import log_api_call
                usage = getattr(response, "usage", None)
                log_api_call("gradient", GRADIENT_MODEL, "chatbot_response",
                    input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0)
            except Exception:
                pass
            return response.choices[0].message.content or ""
        else:
            logger.info(f"Calling Claude API ({LLM_MODEL})...")
            response = self.anthropic_client.messages.create(
                model=LLM_MODEL,
                max_tokens=1024,
                temperature=LLM_TEMPERATURE,
                system=get_system_prompt(getattr(self, "_current_publisher", "") or "your local newspaper"),
                messages=messages,  # type: ignore[arg-type]
            )
            logger.info("Received response from Claude")
            # Log cost
            try:
                from src.modules.costs.tracker import log_api_call
                usage = getattr(response, "usage", None)
                log_api_call("anthropic", LLM_MODEL, "chatbot_response",
                    input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                    output_tokens=getattr(usage, "output_tokens", 0) if usage else 0)
            except Exception:
                pass
            content_block = response.content[0]
            if hasattr(content_block, "text"):
                return content_block.text  # type: ignore[union-attr]
            return str(content_block)

    def generate_response_streaming(
        self,
        query: str,
        chunks: list[dict],
        history: list[dict] | None = None,
        conversation_id: int | None = None,
    ) -> Iterator[str]:
        """Generate a streaming response using Claude.

        Args:
            query: The user's query.
            chunks: Retrieved chunks for context.
            history: Conversation history.
            conversation_id: Optional conversation ID for URL tracking.

        Yields:
            Text tokens as they arrive from the API.
        """
        # Handle empty results
        if not chunks:
            logger.info(
                "No chunks available - letting Claude generate conversational response"
            )
            context = "No results found for this query."
        else:
            # Truncate context if needed
            original_count = len(chunks)
            chunks = self._truncate_context(chunks)
            if len(chunks) < original_count:
                logger.info(f"Truncated from {original_count} to {len(chunks)} chunks")

            # Format context with conversation_id for URL tracking
            context = format_context(chunks, conversation_id=conversation_id)
            context_words = len(context.split())
            logger.info(
                f"Context size: {context_words} words from {len(chunks)} chunks"
            )
            # Log assembled context for debugging retrieval issues
            for ci, ch in enumerate(chunks):
                ch_type = ch.get("search_type", ch.get("metadata", {}).get("content_type", "article"))
                ch_text = str(ch.get("text", ""))[:150].replace("\n", " ")
                logger.info(
                    f"  Context chunk {ci + 1} [{ch_type}]: '{ch_text}...'"
                )

        # Build prompt
        user_message = QUERY_TEMPLATE.format(context=context, question=query)

        # Build messages array with history
        messages: list[dict[str, str]] = []

        if history:
            limited_history = history[-(MAX_HISTORY_TURNS * 2) :]
            logger.info(
                f"Including {len(limited_history)} messages from conversation history"
            )

            for msg in limited_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current query with context
        messages.append({"role": "user", "content": user_message})

        # Call LLM API with streaming
        if self.llm_provider == "gradient":
            logger.info(f"Calling Gradient API with streaming ({GRADIENT_MODEL})...")
            sys_prompt = get_system_prompt(getattr(self, "_current_publisher", "") or "your local newspaper")
            oai_messages = [{"role": "system", "content": sys_prompt}] + messages
            stream = self.openai_client.chat.completions.create(
                model=GRADIENT_MODEL,
                max_tokens=1024,
                temperature=LLM_TEMPERATURE,
                messages=oai_messages,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            logger.info("Streaming response complete")
        else:
            logger.info(f"Calling Claude API with streaming ({LLM_MODEL})...")
            with self.anthropic_client.messages.stream(
                model=LLM_MODEL,
                max_tokens=1024,
                temperature=LLM_TEMPERATURE,
                system=get_system_prompt(getattr(self, "_current_publisher", "") or "your local newspaper"),
                messages=messages,  # type: ignore[arg-type]
            ) as stream:
                for text in stream.text_stream:
                    yield text
            logger.info("Streaming response complete")

    def _is_help_request(self, query: str) -> bool:
        """Check if the query is asking for help.

        Args:
            query: The user's query.

        Returns:
            True if the user is asking for help.
        """
        query_lower = query.lower().strip()
        help_patterns = [
            "help",
            "what can you do",
            "how do i use",
            "what do you know",
            "how does this work",
            "what are you",
            "what is this",
        ]
        return any(pattern in query_lower for pattern in help_patterns)

    def query(self, query: str, history: list[dict] | None = None, publisher: str | None = None) -> dict:
        """Process a query and return response with sources.

        Args:
            query: The user's query.
            history: Conversation history for context.
            publisher: Optional publisher filter for multi-tenant sites.

        Returns:
            Dictionary with response and sources.
        """
        # Store publisher for dynamic system prompt
        self._current_publisher = publisher

        # Check if user is asking for help
        if self._is_help_request(query):
            logger.info("Help request detected")
            self._consecutive_empty_results = 0
            return {
                "response": HELP_MESSAGE,
                "sources": "",
                "chunks": [],
            }

        # Use search agent for intelligent retrieval, fall back to direct retrieval
        if self.search_agent is not None:
            logger.info(f"Processing query with search agent: '{query}'" + (f" [publisher={publisher}]" if publisher else ""))
            chunks = self.search_agent.search(query, publisher=publisher)
        else:
            logger.info(f"Search agent unavailable, using direct retrieval: '{query}'")
            chunks = self.retrieve(query, publisher=publisher)

        # Generate response with conversation history
        response = self.generate_response(query, chunks, history)

        # Track consecutive empty results
        if not chunks:
            self._consecutive_empty_results += 1
            logger.info(
                f"No results - consecutive empty: {self._consecutive_empty_results}"
            )

            # Add help suggestion after threshold
            if self._consecutive_empty_results >= self._empty_results_threshold:
                response += "\n\nNeed help? Just ask me what I can do!"
                self._consecutive_empty_results = 0
        else:
            # Reset counter on successful results
            self._consecutive_empty_results = 0

        # Format sources
        sources = format_sources(chunks)

        return {
            "response": response,
            "sources": sources,
            "chunks": chunks,
        }

    def is_ready(self) -> bool:
        """Check if the query engine is ready.

        Returns:
            True if any content collection has documents, or if DB has ads.
        """
        # Check articles collection
        if self.collection is not None and self.collection.count() > 0:
            return True
        # Check legacy collection
        if self.legacy_collection is not None and self.legacy_collection.count() > 0:
            return True
        # Check ads collection
        try:
            from src.core.vector_store import get_ads_collection
            ads_col = get_ads_collection()
            if ads_col.count() > 0:
                return True
        except Exception:
            pass
        # Check if DB has any ads (even without vector index, SQLite search works)
        try:
            from src.modules.advertisements import get_advertisement_count
            if get_advertisement_count() > 0:
                return True
        except Exception:
            pass
        return False


def main() -> None:
    """Test the query engine."""
    engine = QueryEngine()

    if not engine.is_ready():
        print("No documents indexed. Please run ingestion first.")
        return

    # Test query
    test_query = "What are the main topics covered?"
    result = engine.query(test_query)

    print(f"Query: {test_query}")
    print(f"\nResponse: {result['response']}")
    print(result["sources"])


if __name__ == "__main__":
    main()
