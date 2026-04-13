# Conversation Metadata Logging

## Overview

The chatbot now logs detailed metadata about each search execution to the `conversation_messages.metadata` field in SQLite. This enables debugging, performance analysis, and search strategy optimization.

## What's Logged

For every assistant response, the following metadata is captured:

### Search Metadata

Captures the complete search pipeline execution:

**Search Agent** (`search_agent.py`):
- `search_method`: "search_agent"
- `tools_used`: Array of tool executions with:
  - `tool`: Tool name (e.g., "hybrid_search", "search_advertisements")
  - `parameters`: Input parameters passed to the tool
  - `result_count`: Number of results returned
  - `top_results`: Preview of top 5 results (titles, scores)
- `total_results`: Total results before deduplication
- `unique_results`: Results after deduplication
- `execution_time_ms`: Search execution time in milliseconds
- `publisher`: Publisher filter applied (if any)

**Content Orchestrator** (`content_orchestrator.py`):
- `search_method`: "content_orchestrator"
- `intent`: Detected intent (AD_BUSINESS, ARTICLE_NEWS, MIXED_DISCOVERY)
- `searches_executed`: Array of domain searches:
  - `domain`: Search domain (articles, advertisements, events)
  - `count`: Number of results from that domain
- `total_results`: Total results before deduplication
- `unique_results`: Results after deduplication
- `execution_time_ms`: Search execution time in milliseconds

**Direct Retrieval** (fallback):
- `search_method`: "direct_retrieval"
- `chunks_retrieved`: Number of chunks returned

### Response Metadata

Additional context about the response generation:

- `chunks_count`: Total chunks passed to LLM for context
- `chunks_by_type`: Distribution of chunk types (e.g., `{"article": 4, "advertisement": 2}`)

## Data Structure

```json
{
  "search": {
    "search_method": "search_agent",
    "tools_used": [
      {
        "tool": "hybrid_search",
        "parameters": {"query": "technology", "subject": "Technology"},
        "result_count": 5,
        "top_results": [
          {"title": "AI breakthrough", "score": 0.92}
        ]
      }
    ],
    "total_results": 7,
    "unique_results": 6,
    "execution_time_ms": 1234,
    "publisher": null
  },
  "chunks_count": 6,
  "chunks_by_type": {
    "article": 4,
    "advertisement": 2
  }
}
```

## Use Cases

### 1. Debug Search Issues

When users report poor search results:
```sql
SELECT 
  json_extract(metadata, '$.search.tools_used') as tools,
  json_extract(metadata, '$.search.execution_time_ms') as exec_time,
  content
FROM conversation_messages 
WHERE role = 'assistant' 
  AND json_extract(metadata, '$.chunks_count') < 3;
```

### 2. Performance Analysis

Find slow searches:
```sql
SELECT 
  json_extract(metadata, '$.search.search_method') as method,
  json_extract(metadata, '$.search.execution_time_ms') as time_ms,
  substr(content, 1, 100) as response
FROM conversation_messages 
WHERE role = 'assistant'
  AND json_extract(metadata, '$.search.execution_time_ms') > 2000
ORDER BY time_ms DESC;
```

### 3. Tool Usage Analysis

Which tools are most commonly used?
```python
import sqlite3, json
from collections import Counter

conn = sqlite3.connect('data/articles.db')
cursor = conn.execute("""
  SELECT metadata 
  FROM conversation_messages 
  WHERE metadata IS NOT NULL
""")

tool_counter = Counter()
for row in cursor:
    data = json.loads(row[0])
    tools = data.get('search', {}).get('tools_used', [])
    for t in tools:
        tool_counter[t['tool']] += 1

print(tool_counter.most_common())
```

### 4. Search Strategy A/B Testing

Compare effectiveness of search_agent vs content_orchestrator:
```sql
SELECT 
  json_extract(metadata, '$.search.search_method') as method,
  AVG(json_extract(metadata, '$.chunks_count')) as avg_chunks,
  AVG(json_extract(metadata, '$.search.execution_time_ms')) as avg_time,
  COUNT(*) as count
FROM conversation_messages 
WHERE role = 'assistant' AND metadata IS NOT NULL
GROUP BY method;
```

### 5. Analyze Retrieval Patterns

What types of content are being retrieved?
```sql
SELECT 
  json_extract(metadata, '$.chunks_by_type') as types,
  COUNT(*) as occurrences
FROM conversation_messages 
WHERE role = 'assistant' AND metadata IS NOT NULL
GROUP BY types
ORDER BY occurrences DESC;
```

## Database Schema

The metadata is stored in the existing `conversation_messages.metadata` field:

```sql
CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT,  -- JSON string
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
```

## Implementation Details

### Files Modified

1. **src/search_agent.py**
   - `search()` now returns `tuple[list[dict], dict]`
   - Tracks tool executions, parameters, and results
   - Measures execution time

2. **src/content_orchestrator.py**
   - `search()` now returns `tuple[list[dict], dict]`
   - Tracks intent classification and domain searches
   - Measures execution time

3. **src/chatbot.py**
   - Unpacks metadata from search methods
   - Builds response metadata with chunk counts
   - Passes metadata to `insert_message()`
   - Added `_count_by_type()` helper function

### Backward Compatibility

- The `metadata` field already existed in the database (created but unused)
- Old conversations without metadata continue to work
- Console logging remains unchanged for real-time debugging

## Future Enhancements

Potential additions:
- Log LLM token usage per response
- Track response quality metrics (user feedback, follow-up questions)
- Store embedding model version used
- Log filter parameters (date ranges, publishers)
- Track cache hit rates (if caching is added)
- Store user session context (device, time of day)
