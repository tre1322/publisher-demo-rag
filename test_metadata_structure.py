"""Show example metadata structure that will be logged."""

import json

# Example search metadata from search_agent
search_agent_metadata = {
    "search_method": "search_agent",
    "tools_used": [
        {
            "tool": "hybrid_search",
            "parameters": {
                "query": "technology news",
                "subject": "Technology"
            },
            "result_count": 5,
            "top_results": [
                {"title": "New AI breakthrough announced", "score": 0.92},
                {"title": "Tech startup raises funding", "score": 0.87},
            ]
        },
        {
            "tool": "search_advertisements",
            "parameters": {
                "query": "technology",
                "category": None
            },
            "result_count": 2,
            "top_results": [
                {"product_name": "Laptop Sale", "advertiser": "Tech Store"}
            ]
        }
    ],
    "total_results": 7,
    "unique_results": 6,
    "execution_time_ms": 1234,
    "publisher": None
}

# Example search metadata from content_orchestrator
orchestrator_metadata = {
    "search_method": "content_orchestrator",
    "intent": "ARTICLE_NEWS",
    "searches_executed": [
        {"domain": "articles", "count": 5},
        {"domain": "advertisements", "count": 2},
        {"domain": "events", "count": 1}
    ],
    "total_results": 8,
    "unique_results": 7,
    "execution_time_ms": 892
}

# Example metadata stored in conversation_messages.metadata
response_metadata = {
    "search": search_agent_metadata,  # or orchestrator_metadata
    "chunks_count": 6,
    "chunks_by_type": {
        "article": 4,
        "advertisement": 2
    }
}

print("=" * 70)
print("METADATA STRUCTURE FOR CONVERSATION LOGGING")
print("=" * 70)

print("\n1. Search Agent Metadata (from SearchAgent.search()):")
print("-" * 70)
print(json.dumps(search_agent_metadata, indent=2))

print("\n2. Content Orchestrator Metadata (from ContentOrchestrator.search()):")
print("-" * 70)
print(json.dumps(orchestrator_metadata, indent=2))

print("\n3. Response Metadata (stored in conversation_messages.metadata):")
print("-" * 70)
print(json.dumps(response_metadata, indent=2))

print("\n" + "=" * 70)
print("WHAT THIS ENABLES")
print("=" * 70)
print("""
✓ Debug search performance: See which tools were called and what they returned
✓ Analyze search strategies: Compare search_agent vs orchestrator effectiveness
✓ Track execution times: Identify slow searches that need optimization
✓ Understand retrieval: See exactly what chunks were available to the LLM
✓ Improve relevance: Analyze which tool parameters correlate with good responses
✓ A/B test search logic: Compare different search strategies with real data
""")

print("=" * 70)
print("HOW TO VIEW THIS DATA")
print("=" * 70)
print("""
Option 1: Admin Dashboard (port 7861)
  - View conversation metadata in a web UI
  - Filter by search method, execution time, etc.

Option 2: SQL Query
  SELECT
    cm.id,
    cm.role,
    substr(cm.content, 1, 50) as preview,
    json_extract(cm.metadata, '$.search.search_method') as search_method,
    json_extract(cm.metadata, '$.search.execution_time_ms') as exec_time,
    json_extract(cm.metadata, '$.chunks_count') as chunks
  FROM conversation_messages cm
  WHERE cm.metadata IS NOT NULL
  ORDER BY cm.timestamp DESC;

Option 3: Python script to analyze patterns
  import sqlite3, json
  conn = sqlite3.connect('data/articles.db')
  cursor = conn.execute("SELECT metadata FROM conversation_messages WHERE metadata IS NOT NULL")
  for row in cursor:
      data = json.loads(row[0])
      # Analyze search patterns, tool usage, performance, etc.
""")
