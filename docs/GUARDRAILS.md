# Response Grounding Guardrails

## Overview

The chatbot implements strict guardrails to ensure responses are grounded in search results from the database, not from the LLM's training data. This prevents the system from answering questions using general knowledge when the information isn't in the local archives.

## Problem Statement

Without guardrails, LLMs can "leak" training data:
- User asks: "What is photosynthesis?"
- Database has: No articles about photosynthesis
- Without guardrails: LLM answers from training data ❌
- With guardrails: "I don't have information about that in our archives" ✓

## Implementation

### 1. System Prompt Constraints

**Before** (allowed training data):
```
- Only use information from the provided context
- You can have conversations with the user, but don't make up any information. General knowledge is ok.
```

**After** (strict grounding):
```
- CRITICAL: Only use information from the provided context - NEVER use your general knowledge or training data
- If the context doesn't contain the answer, say "I don't have information about that in our archives"
- You can have polite conversations (greetings, thanks, clarifications) but for ANY factual question, ONLY use the provided context
- Never provide factual information from outside the search results, even if you know it from your training data
```

### 2. Post-Processing Validation

After response generation, `_validate_response_grounding()` checks for:

**Training Data Indicators** (rejected):
- "as of my knowledge cutoff"
- "I don't have access to"
- "I'm an AI assistant"
- "according to my training"
- "as a language model"
- "I can't browse"
- "I don't have real-time"

**Empty Context Responses** (evaluated):
- No chunks + short response (<100 chars) → ✓ Allow (conversational)
- No chunks + "no info" phrases → ✓ Allow (appropriate response)
- No chunks + long response (>100 chars) → ✗ Reject (likely training data)

**Suspicious Patterns**:
- Few chunks (<2) + very long response (>400 chars) → ⚠️ Allow but log (flagged for review)

### 3. Metadata Logging

Validation results are logged to `conversation_messages.metadata`:

```json
{
  "validation": {
    "is_valid": false,
    "reason": "long_response_no_chunks"
  }
}
```

This enables:
- Auditing guardrail effectiveness
- Finding edge cases that slip through
- Tuning validation thresholds

## What's Allowed

✅ **Short conversational responses** (no chunks needed):
- "Hello! How can I help you?"
- "Thanks for asking!"
- "Sure, let me search for that."

✅ **"No information" responses** (when no chunks):
- "I don't have information about that in our archives."
- "I couldn't find any articles on that topic."
- "No results for [topic]. Try a different search?"

✅ **Grounded responses** (with chunks):
- "According to the article published yesterday, the city council approved..."
- "The local hardware store is advertising 20% off tools this weekend."

## What's Rejected

❌ **Training data responses** (no chunks):
- User: "What is photosynthesis?"
- Response: "Photosynthesis is the process by which plants convert light energy..."
- **Guardrail**: Replaced with "I don't have information about that in our archives."

❌ **AI self-references**:
- "As of my knowledge cutoff in 2024..."
- "I'm an AI assistant and I don't have access to..."
- **Guardrail**: Replaced with fallback response

❌ **Mixed training data + search results**:
- "Generally speaking, photosynthesis is... [training data]. Also, our local school has a science fair... [search result]"
- **Guardrail**: Phrase detection catches "generally speaking" pattern

## Testing

Run the test suite to verify guardrails:

```bash
uv run python test_guardrails.py
```

**Test Cases**:
1. ✅ Empty chunks + short conversational response
2. ✅ Empty chunks + "no info" response
3. ✗ Empty chunks + long factual response (rejected)
4. ✗ Training data indicator phrases (rejected)
5. ✅ Valid response with search results
6. ✗ AI self-reference phrases (rejected)

## Monitoring

### Check for Failed Validations

Find responses that were rejected by guardrails:

```sql
SELECT 
  cm.id,
  c.session_id,
  cm.timestamp,
  substr(cm.content, 1, 100) as response_preview,
  json_extract(cm.metadata, '$.validation.reason') as rejection_reason,
  json_extract(cm.metadata, '$.chunks_count') as chunks_count
FROM conversation_messages cm
JOIN conversations c ON c.id = cm.conversation_id
WHERE cm.role = 'assistant'
  AND json_extract(cm.metadata, '$.validation.is_valid') = 0
ORDER BY cm.timestamp DESC;
```

### Check for Suspicious Patterns

Long responses with few chunks (allowed but flagged):

```sql
SELECT 
  cm.id,
  c.session_id,
  length(cm.content) as response_length,
  json_extract(cm.metadata, '$.chunks_count') as chunks_count,
  substr(cm.content, 1, 100) as preview
FROM conversation_messages cm
JOIN conversations c ON c.id = cm.conversation_id
WHERE cm.role = 'assistant'
  AND json_extract(cm.metadata, '$.chunks_count') < 2
  AND length(cm.content) > 300
ORDER BY response_length DESC;
```

### Analyze Rejection Reasons

```sql
SELECT 
  json_extract(metadata, '$.validation.reason') as reason,
  COUNT(*) as occurrences
FROM conversation_messages
WHERE role = 'assistant'
  AND json_extract(metadata, '$.validation.is_valid') = 0
GROUP BY reason
ORDER BY occurrences DESC;
```

## Edge Cases

### Conversational Context

With conversation history, the LLM might reference previous turns:
- User: "Tell me about the park project"
- Bot: [Response from chunks]
- User: "When does it start?"
- Bot: "According to the previous article, construction begins next month."

This is **allowed** because:
- The information came from chunks in a previous turn
- The conversation history is included in the prompt
- No new training data is being introduced

### Follow-up Questions

If a follow-up question asks about the same topic but new chunks aren't retrieved:
- Current behavior: May use conversation history ✓
- Alternative: Force new search for every question

**Recommendation**: Current behavior is acceptable as long as the original information was grounded.

### Partial Matches

What if chunks contain partial information and the LLM fills gaps?
- Chunks: "The event is on Saturday"
- User: "What time?"
- Response: "I don't have the specific time in the article. It mentions Saturday but not the time."

This is **correct** - the LLM should acknowledge gaps rather than inferring or using training data.

## Tuning Parameters

Adjust thresholds in `_validate_response_grounding()`:

```python
# Current values:
SHORT_RESPONSE_THRESHOLD = 100  # chars - conversational responses
LONG_RESPONSE_THRESHOLD = 400   # chars - flag for review
MIN_CHUNKS_FOR_LONG_RESPONSE = 2

# Stricter (fewer false negatives):
SHORT_RESPONSE_THRESHOLD = 50
LONG_RESPONSE_THRESHOLD = 200
MIN_CHUNKS_FOR_LONG_RESPONSE = 3

# More permissive (fewer false positives):
SHORT_RESPONSE_THRESHOLD = 150
LONG_RESPONSE_THRESHOLD = 600
MIN_CHUNKS_FOR_LONG_RESPONSE = 1
```

Monitor the `validation.reason` metadata to see which threshold is triggering most rejections.

## Future Improvements

### 1. Embeddings-Based Detection

Compare response embedding to chunk embeddings:
```python
response_embedding = embed(response)
chunk_embeddings = [embed(chunk['text']) for chunk in chunks]
max_similarity = max([cosine_similarity(response_embedding, ce) for ce in chunk_embeddings])

if max_similarity < 0.5:
    # Response semantically diverges from chunks
    return False, "semantic_divergence"
```

### 2. Fact Verification

Extract claims from response and verify against chunks:
```python
claims = extract_claims(response)  # "The park opens in May"
for claim in claims:
    if not verify_in_chunks(claim, chunks):
        return False, f"unverified_claim: {claim}"
```

### 3. Citation Enforcement

Require inline citations for all facts:
```python
if len(chunks) > 0 and "[Source:" not in response:
    return False, "missing_citations"
```

### 4. User Feedback Loop

Allow users to flag incorrect responses:
```python
# Add thumbs up/down buttons to UI
# Log feedback to improve validation
```

## Related Documentation

- [Metadata Logging](METADATA_LOGGING.md) - How search and validation data is logged
- [System Prompts](../src/prompts.py) - Full prompt templates
- [Query Engine](../src/query_engine.py) - Response generation pipeline
