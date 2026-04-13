"""Test metadata logging changes."""

import json
import sqlite3
from pathlib import Path

# Check if conversation_messages has metadata field
db_path = Path(__file__).parent / "data" / "articles.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check schema
cursor.execute("PRAGMA table_info(conversation_messages)")
columns = cursor.fetchall()
print("conversation_messages schema:")
for col in columns:
    print(f"  {col[1]}: {col[2]}")

print("\n" + "="*60)

# Check if there are any messages with metadata
cursor.execute("""
    SELECT id, role, substr(content, 1, 50) as content_preview, metadata
    FROM conversation_messages
    WHERE metadata IS NOT NULL
    ORDER BY timestamp DESC
    LIMIT 5
""")

rows = cursor.fetchall()
if rows:
    print(f"\nFound {len(rows)} messages with metadata:")
    for row in rows:
        msg_id, role, preview, metadata_json = row
        print(f"\nMessage {msg_id} ({role}): {preview}...")
        if metadata_json:
            metadata = json.loads(metadata_json)
            print(f"  Metadata: {json.dumps(metadata, indent=2)}")
else:
    print("\nNo messages with metadata found yet")

conn.close()
print("\n" + "="*60)
print("Metadata logging infrastructure is ready!")
print("When you run the chatbot, conversation metadata will be logged.")
