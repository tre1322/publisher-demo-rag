"""Query engine for the Publisher RAG Demo."""

import logging
from collections.abc import Iterator

import anthropic
import chromadb
from sentence_transformers import SentenceTransformer

# Config import configures logging with timestamps
from src.core.config import (
    ANTHROPIC_API_KEY,
    CHROMA_PERSIST_DIR,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    LLM_MODEL,
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
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))

        try:
            self.collection = self.chroma_client.get_collection(name=COLLECTION_NAME)
        except Exception:
            logger.warning(
                f"Collection '{COLLECTION_NAME}' not found. Please run ingestion first."
            )
            self.collection = None

        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Please set it in .env file.")

        self.anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        # Initialize search agent for intelligent search
        self.search_agent = SearchAgent()
        logger.info("Search agent initialized")

        # Track consecutive empty results for help suggestions
        self._consecutive_empty_results = 0
        self._empty_results_threshold = 3

    def retrieve(self, query: str) -> list[dict]:
        """Retrieve relevant chunks for a query.

        Args:
            query: The user's query.

        Returns:
            List of relevant chunks with metadata and scores.
        """
        if self.collection is None:
            logger.warning("Collection is None - no documents indexed")
            return []

        logger.info(f"Query: '{query}'")
        logger.info(f"Collection has {self.collection.count()} chunks")

        # Generate query embedding
        query_embedding = self.embedding_model.encode(query).tolist()
        logger.info("Generated query embedding")

        # Query ChromaDB
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=RETRIEVAL_TOP_K,
            include=["documents", "metadatas", "distances"],
        )

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

                logger.info(
                    f"  Chunk {i + 1}: score={score:.3f}, "
                    f"distance={distance:.3f}, title='{title}...'"
                )

                # Filter by similarity threshold
                if score < SIMILARITY_THRESHOLD:
                    logger.info(
                        f"    -> FILTERED OUT (score {score:.3f} < "
                        f"threshold {SIMILARITY_THRESHOLD})"
                    )
                    continue

                chunk = {
                    "text": doc,
                    "metadata": metadata,
                    "score": score,
                }
                chunks.append(chunk)

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

        # Call Claude API
        logger.info(f"Calling Claude API ({LLM_MODEL})...")
        response = self.anthropic_client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            temperature=LLM_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=messages,  # type: ignore[arg-type]
        )
        logger.info("Received response from Claude")

        # Extract text from response
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

        # Call Claude API with streaming
        logger.info(f"Calling Claude API with streaming ({LLM_MODEL})...")
        with self.anthropic_client.messages.stream(
            model=LLM_MODEL,
            max_tokens=1024,
            temperature=LLM_TEMPERATURE,
            system=SYSTEM_PROMPT,
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

    def query(self, query: str, history: list[dict] | None = None) -> dict:
        """Process a query and return response with sources.

        Args:
            query: The user's query.
            history: Conversation history for context.

        Returns:
            Dictionary with response and sources.
        """
        # Check if user is asking for help
        if self._is_help_request(query):
            logger.info("Help request detected")
            self._consecutive_empty_results = 0
            return {
                "response": HELP_MESSAGE,
                "sources": "",
                "chunks": [],
            }

        # Use search agent for intelligent retrieval
        logger.info(f"Processing query with search agent: '{query}'")
        chunks = self.search_agent.search(query)

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
            True if collection exists and has documents.
        """
        if self.collection is None:
            return False
        return self.collection.count() > 0


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
