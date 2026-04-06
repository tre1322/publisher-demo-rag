"""API cost tracking — logs every paid API call with token counts and costs.

Pricing (as of April 2026):
- Anthropic Claude Sonnet 4: $3/$15 per 1M tokens (input/output)
- Anthropic Claude Opus 4.5: $5/$25 per 1M tokens
- Anthropic Claude Haiku 4.5: $1/$5 per 1M tokens
- DigitalOcean Gradient Qwen3-32B: ~$0.15/$0.15 per 1M tokens (estimate)
- Brave Search: free tier (2000/month), then ~$0.003/query
"""

import logging
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD)
PRICING = {
    # Anthropic models
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5-20250414": {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5-20250414": {"input": 1.00, "output": 5.00},
    # Gradient models
    "qwen3-32b": {"input": 0.15, "output": 0.15},
    # Defaults
    "default_anthropic": {"input": 3.00, "output": 15.00},
    "default_gradient": {"input": 0.15, "output": 0.15},
}


def init_cost_table() -> None:
    """Create the api_costs table if it doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            purpose TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_costs_timestamp ON api_costs(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_costs_provider ON api_costs(provider)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_costs_purpose ON api_costs(purpose)")
    conn.commit()
    conn.close()


def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    pricing = PRICING.get(model)
    if not pricing:
        # Try provider default
        if "claude" in model.lower() or "opus" in model.lower() or "sonnet" in model.lower() or "haiku" in model.lower():
            pricing = PRICING["default_anthropic"]
        else:
            pricing = PRICING["default_gradient"]

    cost = (input_tokens * pricing["input"] / 1_000_000) + (output_tokens * pricing["output"] / 1_000_000)
    return round(cost, 6)


def log_api_call(
    provider: str,
    model: str,
    purpose: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = None,
    metadata: str | None = None,
) -> None:
    """Log a paid API call to the database.

    Args:
        provider: "anthropic", "gradient", "brave"
        model: model name or "brave_search"
        purpose: what the call was for (e.g., "chatbot_response", "ad_ocr", "vision_extraction", "enrichment", "search_agent")
        input_tokens: input token count (from API response usage)
        output_tokens: output token count
        cost_usd: explicit cost override (e.g., for Brave Search flat cost)
        metadata: optional JSON string with extra info
    """
    if cost_usd is None:
        cost_usd = _calculate_cost(model, input_tokens, output_tokens)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO api_costs (provider, model, purpose, input_tokens, output_tokens, cost_usd, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (provider, model, purpose, input_tokens, output_tokens, cost_usd, metadata),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to log API cost: {e}")


def get_cost_summary() -> dict:
    """Get cost summary grouped by provider and purpose."""
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure table exists
    init_cost_table()

    # Total costs
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs")
    total = cursor.fetchone()[0]

    # By provider
    cursor.execute("""
        SELECT provider, COUNT(*) as calls, SUM(input_tokens) as input_tok,
               SUM(output_tokens) as output_tok, SUM(cost_usd) as cost
        FROM api_costs GROUP BY provider ORDER BY cost DESC
    """)
    by_provider = [
        {"provider": r[0], "calls": r[1], "input_tokens": r[2], "output_tokens": r[3], "cost": round(r[4], 4)}
        for r in cursor.fetchall()
    ]

    # By purpose
    cursor.execute("""
        SELECT purpose, COUNT(*) as calls, SUM(cost_usd) as cost
        FROM api_costs GROUP BY purpose ORDER BY cost DESC
    """)
    by_purpose = [
        {"purpose": r[0], "calls": r[1], "cost": round(r[2], 4)}
        for r in cursor.fetchall()
    ]

    # Today's costs
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs WHERE date(timestamp) = date('now')")
    today = cursor.fetchone()[0]

    # This week
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs WHERE timestamp >= datetime('now', '-7 days')")
    week = cursor.fetchone()[0]

    # This month
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs WHERE timestamp >= datetime('now', 'start of month')")
    month = cursor.fetchone()[0]

    conn.close()

    return {
        "total_cost": round(total, 4),
        "today_cost": round(today, 4),
        "week_cost": round(week, 4),
        "month_cost": round(month, 4),
        "by_provider": by_provider,
        "by_purpose": by_purpose,
    }


def get_cost_history(days: int = 30) -> list[dict]:
    """Get daily cost breakdown for the last N days."""
    conn = get_connection()
    cursor = conn.cursor()
    init_cost_table()

    cursor.execute("""
        SELECT date(timestamp) as day, provider, purpose,
               COUNT(*) as calls, SUM(input_tokens) as input_tok,
               SUM(output_tokens) as output_tok, SUM(cost_usd) as cost
        FROM api_costs
        WHERE timestamp >= datetime('now', ?)
        GROUP BY day, provider, purpose
        ORDER BY day DESC, cost DESC
    """, (f"-{days} days",))

    history = [
        {"date": r[0], "provider": r[1], "purpose": r[2], "calls": r[3],
         "input_tokens": r[4], "output_tokens": r[5], "cost": round(r[6], 4)}
        for r in cursor.fetchall()
    ]
    conn.close()
    return history
