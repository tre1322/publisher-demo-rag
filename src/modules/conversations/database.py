"""Database operations for conversation logging."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from src.core.config import DATA_DIR

DATABASE_PATH = DATA_DIR / "articles.db"


def get_connection() -> sqlite3.Connection:
    """Get database connection.

    Returns:
        SQLite connection.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_table() -> None:
    """Initialize conversations and conversation_messages tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Conversations table - tracks individual chat sessions
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            message_count INTEGER DEFAULT 0
        )
        """
    )

    # Conversation messages table - stores individual user/assistant turns
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            metadata TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
        """
    )

    # Create indexes for common queries
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
        ON conversation_messages(conversation_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp
        ON conversation_messages(timestamp)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversations_started_at
        ON conversations(started_at)
        """
    )

    conn.commit()
    conn.close()


def insert_conversation(session_id: str) -> int:
    """Create a new conversation session.

    Args:
        session_id: Unique identifier for the session.

    Returns:
        Database ID of the created conversation.
    """
    conn = get_connection()
    cursor = conn.cursor()

    started_at = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO conversations (session_id, started_at)
        VALUES (?, ?)
        """,
        (session_id, started_at),
    )

    conversation_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return conversation_id


def insert_message(
    conversation_id: int,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> int:
    """Insert a message into a conversation.

    Args:
        conversation_id: ID of the conversation.
        role: "user" or "assistant".
        content: Message content.
        metadata: Optional metadata (search results, sources, tool used, etc.).

    Returns:
        Database ID of the created message.
    """
    conn = get_connection()
    cursor = conn.cursor()

    timestamp = datetime.now().isoformat()
    metadata_json = json.dumps(metadata) if metadata else None

    cursor.execute(
        """
        INSERT INTO conversation_messages (conversation_id, role, content, timestamp, metadata)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, role, content, timestamp, metadata_json),
    )

    message_id = cursor.lastrowid

    # Update message count in conversations table
    cursor.execute(
        """
        UPDATE conversations
        SET message_count = message_count + 1
        WHERE id = ?
        """,
        (conversation_id,),
    )

    conn.commit()
    conn.close()

    return message_id


def update_conversation_end_time(conversation_id: int) -> None:
    """Update the end time of a conversation.

    Args:
        conversation_id: ID of the conversation.
    """
    conn = get_connection()
    cursor = conn.cursor()

    ended_at = datetime.now().isoformat()

    cursor.execute(
        """
        UPDATE conversations
        SET ended_at = ?
        WHERE id = ?
        """,
        (ended_at, conversation_id),
    )

    conn.commit()
    conn.close()


def get_conversation(session_id: str) -> dict | None:
    """Get a conversation by session ID.

    Args:
        session_id: Session identifier.

    Returns:
        Conversation data or None if not found.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, session_id, started_at, ended_at, message_count
        FROM conversations
        WHERE session_id = ?
        """,
        (session_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def get_conversation_messages(conversation_id: int) -> list[dict]:
    """Get all messages for a conversation.

    Args:
        conversation_id: ID of the conversation.

    Returns:
        List of messages.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, role, content, timestamp, metadata
        FROM conversation_messages
        WHERE conversation_id = ?
        ORDER BY timestamp ASC
        """,
        (conversation_id,),
    )

    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        message = dict(row)
        # Parse metadata JSON if present
        if message["metadata"]:
            message["metadata"] = json.loads(message["metadata"])
        messages.append(message)

    return messages


def get_all_conversations(limit: int = 100) -> list[dict]:
    """Get all conversations, most recent first.

    Args:
        limit: Maximum number of conversations to return.

    Returns:
        List of conversations.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, session_id, started_at, ended_at, message_count
        FROM conversations
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_conversation_stats() -> dict:
    """Get statistics about conversations.

    Returns:
        Dictionary with conversation statistics.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Total conversations
    cursor.execute("SELECT COUNT(*) FROM conversations")
    total_conversations = cursor.fetchone()[0]

    # Total messages
    cursor.execute("SELECT COUNT(*) FROM conversation_messages")
    total_messages = cursor.fetchone()[0]

    # Average messages per conversation
    cursor.execute(
        """
        SELECT AVG(message_count) FROM conversations
        WHERE message_count > 0
        """
    )
    avg_messages = cursor.fetchone()[0] or 0

    # Most recent conversation
    cursor.execute(
        """
        SELECT started_at FROM conversations
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    most_recent = cursor.fetchone()
    most_recent_time = most_recent[0] if most_recent else None

    conn.close()

    return {
        "total_conversations": total_conversations,
        "total_messages": total_messages,
        "avg_messages_per_conversation": round(avg_messages, 2),
        "most_recent_conversation": most_recent_time,
    }
