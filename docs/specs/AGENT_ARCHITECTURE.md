# Agent Architecture

## Current Implementation

The chatbot uses a two-stage architecture:

```text
User Query → QueryEngine → SearchAgent (tool selection) → Tools → Results → LLM Response
```

1. **SearchAgent** (`src/search_agent.py`): Uses Claude with tools to decide what to search
2. **QueryEngine** (`src/query_engine.py`): Takes results, formats context, generates final response

### How It Works

1. User submits a query
2. SearchAgent calls Claude with available tools (hybrid_search, search_advertisements, search_events, etc.)
3. Claude decides which tools to call and with what parameters
4. Tools execute and return results
5. QueryEngine formats results as context and calls Claude again to generate the response

### Current Limitations

- **Single tool-calling round**: No iterative refinement
- **No reasoning trace**: Tool selection reasoning isn't visible to the response LLM
- **No self-correction**: If a search returns nothing, can't try alternative queries

---

## Future Enhancement: Iterative Reasoning Loop

To enable multi-step reasoning and self-correction, modify `SearchAgent.search()`:

```python
def search(self, query: str, max_iterations: int = 3) -> list[dict]:
    """Search with iterative reasoning."""
    messages = [{"role": "user", "content": query}]
    all_results = []

    for _ in range(max_iterations):
        response = self.client.messages.create(
            model=LLM_MODEL,
            max_tokens=1024,
            temperature=0.1,
            system=self._get_system_prompt(),
            tools=self.tools,
            messages=messages,
        )

        # If LLM is done reasoning, exit loop
        if response.stop_reason == "end_turn":
            break

        # Process tool calls
        tool_results_content = []
        for content_block in response.content:
            if content_block.type == "tool_use":
                results = self._execute_tool(content_block.name, content_block.input)
                all_results.extend(results)

                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": json.dumps({
                        "count": len(results),
                        "results": [r.get("text", "")[:200] for r in results[:3]]
                    })
                })

        # Add assistant response and tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results_content})

    return deduplicate_and_sort(all_results)
```

### Benefits of Iterative Approach

1. **Self-correction**: If search returns 0 results, LLM can try different parameters
2. **Refinement**: Can narrow or expand searches based on initial results
3. **Chained reasoning**: Can search articles first, then find related ads

### Example Flow

```text
User: "What roofing companies are in Pipestone?"

Iteration 1:
  Thought: Search for roofing in ads
  Action: search_advertisements(query="roofing")
  Observation: 0 results

Iteration 2:
  Thought: Try the Services category instead
  Action: search_advertisements(category="Services")
  Observation: 5 results (including roofing company)

Iteration 3:
  Thought: Found what I need, done
  [end_turn]
```

---

## Alternative: LangChain Agents

For more complex agent behaviors, consider LangChain:

```python
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_anthropic import ChatAnthropic
from langchain.tools import StructuredTool

tools = [
    StructuredTool.from_function(func=search_articles, name="search_articles", ...),
    StructuredTool.from_function(func=search_ads, name="search_ads", ...),
]

llm = ChatAnthropic(model="claude-sonnet-4-20250514")
agent = create_tool_calling_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

result = executor.invoke({"input": query})
```

### When to Consider LangChain

- Need complex multi-step workflows
- Want LangSmith observability
- Integrating with other LangChain tools (web search, SQL, etc.)
- Community-maintained agent patterns

### Trade-offs

| Aspect | Custom Implementation | LangChain |
|--------|----------------------|-----------|
| Simplicity | ✅ ~300 lines | ❌ More abstraction |
| Control | ✅ Full | ⚠️ Some hidden logic |
| Latency | ✅ Single round (fast) | ⚠️ Multiple rounds |
| Features | ⚠️ Basic | ✅ Full agent patterns |
