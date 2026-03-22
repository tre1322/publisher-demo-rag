"""Admin dashboard for conversation analytics."""

import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr
import pandas as pd

from src.core.database import get_connection
from src.modules.analytics import get_click_stats, get_impression_stats
from src.modules.conversations.database import (
    get_all_conversations,
    get_conversation_messages,
    get_conversation_stats,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_stats_data() -> dict:
    """Get overall statistics.

    Returns:
        Dictionary with statistics.
    """
    return get_conversation_stats()


def get_common_queries(limit: int = 100, top_n: int = 20) -> pd.DataFrame:
    """Get most common queries.

    Args:
        limit: Number of conversations to analyze.
        top_n: Number of top queries to return.

    Returns:
        DataFrame with query counts.
    """
    conversations = get_all_conversations(limit=limit)
    all_queries = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        user_messages = [m for m in messages if m["role"] == "user"]
        all_queries.extend([m["content"] for m in user_messages])

    query_counts = Counter(all_queries)
    data = [
        {"Query": query, "Count": count}
        for query, count in query_counts.most_common(top_n)
    ]

    return pd.DataFrame(data) if data else pd.DataFrame(columns=["Query", "Count"])


def get_common_words(limit: int = 100, top_n: int = 30) -> pd.DataFrame:
    """Get most common words in queries.

    Args:
        limit: Number of conversations to analyze.
        top_n: Number of top words to return.

    Returns:
        DataFrame with word counts.
    """
    conversations = get_all_conversations(limit=limit)
    all_words = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        user_messages = [m for m in messages if m["role"] == "user"]

        for msg in user_messages:
            # Extract words (lowercase, filter short words and common stop words)
            stop_words = {
                "what",
                "whats",
                "about",
                "this",
                "that",
                "with",
                "from",
                "have",
                "there",
                "them",
                "they",
            }
            words = [
                w.lower().strip("?.,!")
                for w in msg["content"].split()
                if len(w) > 3 and w.lower() not in stop_words
            ]
            all_words.extend(words)

    word_counts = Counter(all_words)
    data = [
        {"Word": word, "Count": count} for word, count in word_counts.most_common(top_n)
    ]

    return pd.DataFrame(data) if data else pd.DataFrame(columns=["Word", "Count"])


def get_recent_conversations_data(limit: int = 10) -> list[dict]:
    """Get recent conversations with details.

    Args:
        limit: Number of conversations to return.

    Returns:
        List of conversation dictionaries.
    """
    conversations = get_all_conversations(limit=limit)
    results = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])

        # Format conversation preview
        preview_lines = []
        for msg in messages[:4]:  # First 2 exchanges
            role_emoji = "👤" if msg["role"] == "user" else "🤖"
            content = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
            preview_lines.append(f"{role_emoji} {content}")

        results.append(
            {
                "Session ID": conv["session_id"][:8] + "...",
                "Started": conv["started_at"],
                "Messages": conv["message_count"],
                "Duration": _calculate_duration(conv["started_at"], conv["ended_at"]),
                "Preview": "\n".join(preview_lines),
            }
        )

    return results


def _calculate_duration(started_at: str, ended_at: str | None) -> str:
    """Calculate conversation duration.

    Args:
        started_at: Start timestamp.
        ended_at: End timestamp or None.

    Returns:
        Duration string.
    """
    if not ended_at:
        return "In progress"

    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        duration = end - start

        if duration.total_seconds() < 60:
            return f"{int(duration.total_seconds())}s"
        elif duration.total_seconds() < 3600:
            return f"{int(duration.total_seconds() / 60)}m"
        else:
            return f"{duration.total_seconds() / 3600:.1f}h"
    except Exception:
        return "Unknown"


def export_conversations_json(limit: int = 100) -> str:
    """Export conversations to JSON file.

    Args:
        limit: Number of conversations to export.

    Returns:
        Path to exported file.
    """
    import json

    conversations = get_all_conversations(limit=limit)
    export_data = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        export_data.append(
            {
                "session_id": conv["session_id"],
                "started_at": conv["started_at"],
                "ended_at": conv["ended_at"],
                "message_count": conv["message_count"],
                "messages": messages,
            }
        )

    output_path = "data/conversations_export.json"
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2)

    return output_path


# Database browser constants
BROWSABLE_TABLES = [
    "articles",
    "advertisements",
    "events",
    "conversations",
    "conversation_messages",
    "content_impressions",
    "url_clicks",
]

# Columns to truncate for display (contain long text)
TRUNCATE_COLUMNS = {"raw_text", "content", "summary", "description", "subjects"}
TRUNCATE_LENGTH = 100


def get_table_data(
    table_name: str, page: int = 1, page_size: int = 25
) -> tuple[pd.DataFrame, int]:
    """Fetch paginated rows from a database table.

    Args:
        table_name: Name of the table to query.
        page: Page number (1-indexed).
        page_size: Number of rows per page.

    Returns:
        Tuple of (DataFrame with rows, total row count).
    """
    if table_name not in BROWSABLE_TABLES:
        return pd.DataFrame(), 0

    conn = get_connection()
    cursor = conn.cursor()

    # Get total count
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
    total_count = cursor.fetchone()[0]

    # Get paginated data
    offset = (page - 1) * page_size
    cursor.execute(
        f"SELECT * FROM {table_name} LIMIT ? OFFSET ?",  # noqa: S608
        (page_size, offset),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame(), total_count

    # Convert to list of dicts
    data = [dict(row) for row in rows]

    # Truncate long text columns for display
    for row in data:
        for col in TRUNCATE_COLUMNS:
            if col in row and row[col] and len(str(row[col])) > TRUNCATE_LENGTH:
                row[col] = str(row[col])[:TRUNCATE_LENGTH] + "..."

    return pd.DataFrame(data), total_count


def create_dashboard() -> gr.Blocks:
    """Create the admin dashboard interface.

    Returns:
        Configured Gradio Blocks interface.
    """
    with gr.Blocks(title="Admin Dashboard - Publisher RAG Demo") as dashboard:
        gr.Markdown("# 📊 Admin Dashboard - Conversation Analytics")

        # Statistics section
        with gr.Row():
            with gr.Column():
                total_convs = gr.Number(label="Total Conversations", interactive=False)
                total_msgs = gr.Number(label="Total Messages", interactive=False)
            with gr.Column():
                avg_msgs = gr.Number(
                    label="Avg Messages/Conversation", interactive=False
                )
                recent_time = gr.Textbox(
                    label="Most Recent Conversation", interactive=False
                )

        refresh_btn = gr.Button("🔄 Refresh Statistics", variant="primary")

        gr.Markdown("---")

        # Tabs for different views
        with gr.Tabs():
            # Tab 1: Query Analytics
            with gr.Tab("📊 Query Analytics"):
                gr.Markdown("### Most Common Queries")

                with gr.Row():
                    query_limit = gr.Slider(
                        minimum=10,
                        maximum=500,
                        value=100,
                        step=10,
                        label="Conversations to Analyze",
                    )
                    query_top_n = gr.Slider(
                        minimum=5, maximum=50, value=20, step=5, label="Top N Queries"
                    )

                analyze_queries_btn = gr.Button("Analyze Queries")
                queries_table = gr.DataFrame(
                    headers=["Query", "Count"],
                    label="Most Frequent Queries",
                )

                gr.Markdown("### Most Common Query Words")

                with gr.Row():
                    words_limit = gr.Slider(
                        minimum=10,
                        maximum=500,
                        value=100,
                        step=10,
                        label="Conversations to Analyze",
                    )
                    words_top_n = gr.Slider(
                        minimum=10, maximum=50, value=30, step=5, label="Top N Words"
                    )

                analyze_words_btn = gr.Button("Analyze Words")
                words_table = gr.DataFrame(
                    headers=["Word", "Count"],
                    label="Most Common Words",
                )

            # Tab 2: Recent Conversations
            with gr.Tab("💬 Recent Conversations"):
                gr.Markdown("### Recent Conversation History")

                conv_limit = gr.Slider(
                    minimum=5,
                    maximum=50,
                    value=10,
                    step=5,
                    label="Number of Conversations",
                )
                load_convs_btn = gr.Button("Load Conversations")
                convs_table = gr.DataFrame(
                    headers=["Session ID", "Started", "Messages", "Duration", "Preview"],
                    label="Recent Conversations",
                    wrap=True,
                )

            # Tab 3: Export
            with gr.Tab("📥 Export Data"):
                gr.Markdown("### Export Conversation Data")

                export_limit = gr.Slider(
                    minimum=10,
                    maximum=1000,
                    value=100,
                    step=10,
                    label="Conversations to Export",
                )
                export_btn = gr.Button("Export to JSON", variant="primary")
                export_output = gr.Textbox(
                    label="Export Status",
                    placeholder="Click 'Export to JSON' to download data...",
                )

            # Tab 4: Engagement Analytics
            with gr.Tab("📈 Engagement Analytics"):
                gr.Markdown("### Content Engagement Tracking")

                with gr.Row():
                    total_impressions = gr.Number(
                        label="Total Impressions", interactive=False
                    )
                    total_clicks = gr.Number(label="Total Clicks", interactive=False)
                    overall_ctr = gr.Textbox(label="Overall CTR", interactive=False)

                refresh_engagement_btn = gr.Button(
                    "🔄 Refresh Engagement Stats", variant="primary"
                )

                gr.Markdown("### Click-Through Rate by Content Type")
                ctr_table = gr.DataFrame(
                    headers=["Type", "Shown", "Clicked", "CTR %"],
                    label="CTR by Type",
                )

                gr.Markdown("### Top Clicked Content")
                top_clicked_table = gr.DataFrame(
                    headers=["Type", "Content ID", "Clicks"],
                    label="Most Clicked",
                )

                gr.Markdown("### Top Shown Content")
                top_shown_table = gr.DataFrame(
                    headers=["Type", "Content ID", "Impressions"],
                    label="Most Shown",
                )

            # Tab 5: Database Browser
            with gr.Tab("🗄️ Database Browser"):
                gr.Markdown("### Browse Database Tables")

                with gr.Row():
                    table_select = gr.Dropdown(
                        choices=BROWSABLE_TABLES,
                        value="articles",
                        label="Select Table",
                    )
                    page_size = gr.Slider(
                        minimum=10,
                        maximum=100,
                        value=25,
                        step=5,
                        label="Rows Per Page",
                    )

                with gr.Row():
                    prev_btn = gr.Button("← Previous", size="sm")
                    page_info = gr.Textbox(
                        value="Page 1",
                        label="",
                        interactive=False,
                        scale=2,
                    )
                    next_btn = gr.Button("Next →", size="sm")

                load_table_btn = gr.Button("Load Table", variant="primary")
                db_table = gr.DataFrame(label="Table Contents", wrap=True)

                # State for current page
                current_page = gr.State(value=1)

        # Event handlers
        def update_stats():
            """Update statistics display."""
            stats = get_stats_data()
            return (
                stats["total_conversations"],
                stats["total_messages"],
                stats["avg_messages_per_conversation"],
                stats["most_recent_conversation"] or "No conversations yet",
            )

        def analyze_queries_handler(limit: int, top_n: int):
            """Handle query analysis."""
            df = get_common_queries(int(limit), int(top_n))
            return df

        def analyze_words_handler(limit: int, top_n: int):
            """Handle word analysis."""
            df = get_common_words(int(limit), int(top_n))
            return df

        def load_conversations_handler(limit: int):
            """Handle conversation loading."""
            data = get_recent_conversations_data(int(limit))
            df = pd.DataFrame(data)
            return df

        def export_handler(limit: int):
            """Handle export."""
            path = export_conversations_json(int(limit))
            return f"✓ Exported {limit} conversations to {path}"

        # Engagement analytics handlers
        def update_engagement_stats():
            """Update engagement statistics display."""
            impression_stats = get_impression_stats()
            click_stats = get_click_stats()

            # Calculate totals
            total_imp = sum(impression_stats.get("by_type", {}).values())
            total_clk = click_stats.get("total_clicks", 0)
            ctr = f"{(total_clk / total_imp * 100):.1f}%" if total_imp > 0 else "0%"

            # CTR by type table
            ctr_data = []
            for content_type, stats in click_stats.get("ctr_by_type", {}).items():
                ctr_data.append(
                    {
                        "Type": content_type,
                        "Shown": stats["shown"],
                        "Clicked": stats["clicked"],
                        "CTR %": f"{stats['ctr_percent']}%",
                    }
                )
            ctr_df = (
                pd.DataFrame(ctr_data)
                if ctr_data
                else pd.DataFrame(columns=["Type", "Shown", "Clicked", "CTR %"])
            )

            # Top clicked
            top_clicked_data = [
                {"Type": item["content_type"], "Content ID": item["content_id"], "Clicks": item["clicks"]}
                for item in click_stats.get("top_clicked", [])[:10]
            ]
            top_clicked_df = (
                pd.DataFrame(top_clicked_data)
                if top_clicked_data
                else pd.DataFrame(columns=["Type", "Content ID", "Clicks"])
            )

            # Top shown
            top_shown_data = [
                {"Type": item["content_type"], "Content ID": item["content_id"], "Impressions": item["impressions"]}
                for item in impression_stats.get("top_content", [])[:10]
            ]
            top_shown_df = (
                pd.DataFrame(top_shown_data)
                if top_shown_data
                else pd.DataFrame(columns=["Type", "Content ID", "Impressions"])
            )

            return total_imp, total_clk, ctr, ctr_df, top_clicked_df, top_shown_df

        # Database browser handlers
        def load_table_handler(table_name: str, page_sz: int):
            """Load table data and reset to page 1."""
            df, total = get_table_data(table_name, 1, int(page_sz))
            total_pages = max(1, (total + int(page_sz) - 1) // int(page_sz))
            page_text = f"Page 1 of {total_pages} ({total} rows)"
            return df, 1, page_text

        def next_page_handler(
            table_name: str, curr_page: int, page_sz: int
        ) -> tuple[pd.DataFrame, int, str]:
            """Go to next page."""
            page_sz = int(page_sz)
            df, total = get_table_data(table_name, curr_page + 1, page_sz)
            total_pages = max(1, (total + page_sz - 1) // page_sz)

            # Don't go past last page
            if curr_page >= total_pages:
                df, _ = get_table_data(table_name, curr_page, page_sz)
                page_text = f"Page {curr_page} of {total_pages} ({total} rows)"
                return df, curr_page, page_text

            new_page = curr_page + 1
            page_text = f"Page {new_page} of {total_pages} ({total} rows)"
            return df, new_page, page_text

        def prev_page_handler(
            table_name: str, curr_page: int, page_sz: int
        ) -> tuple[pd.DataFrame, int, str]:
            """Go to previous page."""
            page_sz = int(page_sz)
            new_page = max(1, curr_page - 1)
            df, total = get_table_data(table_name, new_page, page_sz)
            total_pages = max(1, (total + page_sz - 1) // page_sz)
            page_text = f"Page {new_page} of {total_pages} ({total} rows)"
            return df, new_page, page_text

        # Wire up events
        refresh_btn.click(
            update_stats,
            outputs=[total_convs, total_msgs, avg_msgs, recent_time],
        )

        analyze_queries_btn.click(
            analyze_queries_handler,
            inputs=[query_limit, query_top_n],
            outputs=queries_table,
        )

        analyze_words_btn.click(
            analyze_words_handler,
            inputs=[words_limit, words_top_n],
            outputs=words_table,
        )

        load_convs_btn.click(
            load_conversations_handler,
            inputs=[conv_limit],
            outputs=convs_table,
        )

        export_btn.click(
            export_handler,
            inputs=[export_limit],
            outputs=export_output,
        )

        # Engagement analytics events
        refresh_engagement_btn.click(
            update_engagement_stats,
            outputs=[
                total_impressions,
                total_clicks,
                overall_ctr,
                ctr_table,
                top_clicked_table,
                top_shown_table,
            ],
        )

        # Database browser events
        load_table_btn.click(
            load_table_handler,
            inputs=[table_select, page_size],
            outputs=[db_table, current_page, page_info],
        )

        next_btn.click(
            next_page_handler,
            inputs=[table_select, current_page, page_size],
            outputs=[db_table, current_page, page_info],
        )

        prev_btn.click(
            prev_page_handler,
            inputs=[table_select, current_page, page_size],
            outputs=[db_table, current_page, page_info],
        )

        # Load initial stats
        dashboard.load(
            update_stats,
            outputs=[total_convs, total_msgs, avg_msgs, recent_time],
        )

    return dashboard


def main() -> None:
    """Launch the admin dashboard."""
    # Get admin password from environment
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not admin_password:
        logger.warning("ADMIN_PASSWORD not set - using default password 'admin'")
        admin_password = "admin"

    dashboard = create_dashboard()

    # Launch with authentication
    dashboard.launch(
        server_name="0.0.0.0",
        server_port=7861,
        auth=("admin", admin_password),
        auth_message="Admin Dashboard - Enter credentials to access analytics",
        share=False,
        # Note: watch_files parameter requires Gradio 6+ which has dependency issues
        # For auto-reload during development, manually restart the server
    )


if __name__ == "__main__":
    main()
