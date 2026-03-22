#!/usr/bin/env python
"""Analyze conversation logs to understand user search patterns."""

import argparse
import sys
from collections import Counter
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.modules.conversations.database import (
    get_all_conversations,
    get_conversation_messages,
    get_conversation_stats,
)


def analyze_queries(limit: int = 100) -> None:
    """Analyze user queries to find patterns.

    Args:
        limit: Maximum number of conversations to analyze.
    """
    print("=" * 60)
    print("Conversation Analysis")
    print("=" * 60)

    # Get overall statistics
    stats = get_conversation_stats()
    print("\n📊 Overall Statistics:")
    print(f"  Total conversations: {stats['total_conversations']}")
    print(f"  Total messages: {stats['total_messages']}")
    print(
        f"  Average messages per conversation: {stats['avg_messages_per_conversation']}"
    )
    print(f"  Most recent conversation: {stats['most_recent_conversation']}")

    # Get all conversations
    conversations = get_all_conversations(limit=limit)

    if not conversations:
        print("\nNo conversations found.")
        return

    print(f"\n📝 Analyzing {len(conversations)} conversations...")

    # Collect all user queries
    all_queries = []
    query_words = []
    conversation_lengths = []

    for conv in conversations:
        messages = get_conversation_messages(conv["id"])
        user_messages = [m for m in messages if m["role"] == "user"]

        conversation_lengths.append(len(messages))

        for msg in user_messages:
            query = msg["content"]
            all_queries.append(query)

            # Extract words from query (lowercase, filter short words)
            words = [w.lower() for w in query.split() if len(w) > 3]
            query_words.extend(words)

    # Find most common words in queries
    word_counts = Counter(query_words)
    print("\n🔤 Most Common Query Words:")
    for word, count in word_counts.most_common(20):
        print(f"  {word}: {count}")

    # Find most common full queries
    query_counts = Counter(all_queries)
    print("\n💬 Most Common Queries:")
    for query, count in query_counts.most_common(10):
        print(f"  [{count}x] {query}")

    # Conversation length distribution
    if conversation_lengths:
        avg_length = sum(conversation_lengths) / len(conversation_lengths)
        max_length = max(conversation_lengths)
        min_length = min(conversation_lengths)

        print("\n📏 Conversation Length:")
        print(f"  Average: {avg_length:.1f} messages")
        print(f"  Longest: {max_length} messages")
        print(f"  Shortest: {min_length} messages")


def show_recent_conversations(limit: int = 10) -> None:
    """Show recent conversations.

    Args:
        limit: Number of conversations to show.
    """
    print("=" * 60)
    print(f"Recent Conversations (last {limit})")
    print("=" * 60)

    conversations = get_all_conversations(limit=limit)

    if not conversations:
        print("\nNo conversations found.")
        return

    for i, conv in enumerate(conversations, 1):
        print(f"\n{i}. Session: {conv['session_id']}")
        print(f"   Started: {conv['started_at']}")
        print(f"   Ended: {conv['ended_at'] or 'In progress'}")
        print(f"   Messages: {conv['message_count']}")

        messages = get_conversation_messages(conv["id"])
        if messages:
            print("   Preview:")
            for msg in messages[:4]:  # Show first 2 exchanges
                role_emoji = "👤" if msg["role"] == "user" else "🤖"
                content_preview = (
                    msg["content"][:80] + "..."
                    if len(msg["content"]) > 80
                    else msg["content"]
                )
                print(f"     {role_emoji} {content_preview}")


def export_conversations(output_file: str, limit: int = 100) -> None:
    """Export conversations to a file.

    Args:
        output_file: Path to output file.
        limit: Maximum number of conversations to export.
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

    with open(output_file, "w") as f:
        json.dump(export_data, f, indent=2)

    print(f"✓ Exported {len(export_data)} conversations to {output_file}")


def main() -> None:
    """Run conversation analysis."""
    parser = argparse.ArgumentParser(
        description="Analyze conversation logs from the chatbot"
    )
    parser.add_argument(
        "--analyze",
        "-a",
        action="store_true",
        help="Analyze query patterns and show statistics",
    )
    parser.add_argument(
        "--recent",
        "-r",
        action="store_true",
        help="Show recent conversations",
    )
    parser.add_argument(
        "--export",
        "-e",
        type=str,
        help="Export conversations to JSON file",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=100,
        help="Maximum number of conversations to process (default: 100)",
    )

    args = parser.parse_args()

    # If no arguments, show overall stats
    if not any([args.analyze, args.recent, args.export]):
        stats = get_conversation_stats()
        print("=" * 60)
        print("Conversation Statistics")
        print("=" * 60)
        print(f"\nTotal conversations: {stats['total_conversations']}")
        print(f"Total messages: {stats['total_messages']}")
        print(
            f"Average messages per conversation: {stats['avg_messages_per_conversation']}"
        )
        print(f"Most recent conversation: {stats['most_recent_conversation']}")
        print("\nUse --help to see available analysis options")
        return

    if args.analyze:
        analyze_queries(limit=args.limit)

    if args.recent:
        show_recent_conversations(limit=args.limit)

    if args.export:
        export_conversations(args.export, limit=args.limit)


if __name__ == "__main__":
    main()
