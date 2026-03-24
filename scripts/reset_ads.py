#!/usr/bin/env python
"""Clear all advertisement data (SQLite + Chroma) for a fresh start.

Usage:
    python scripts/reset_ads.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import get_connection
from src.core.vector_store import get_ads_collection, get_chroma_client
from src.core.config import ADS_COLLECTION

def main() -> None:
    print("=" * 50)
    print("Reset Advertisements — Fresh Start")
    print("=" * 50)

    # 1. Clear SQLite advertisements table
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM advertisements")
    count = cursor.fetchone()[0]
    print(f"\nSQLite: {count} ad rows found")

    cursor.execute("DELETE FROM advertisements")
    conn.commit()
    conn.close()
    print(f"SQLite: deleted {count} ad rows")

    # 2. Delete and recreate the advertisements Chroma collection
    client = get_chroma_client()
    try:
        col = client.get_collection(name=ADS_COLLECTION)
        chunk_count = col.count()
        client.delete_collection(name=ADS_COLLECTION)
        print(f"Chroma: deleted '{ADS_COLLECTION}' collection ({chunk_count} chunks)")
    except Exception:
        print(f"Chroma: '{ADS_COLLECTION}' collection did not exist")

    # Recreate empty collection
    client.get_or_create_collection(
        name=ADS_COLLECTION, metadata={"hnsw:space": "cosine"}
    )
    print(f"Chroma: recreated empty '{ADS_COLLECTION}' collection")

    print(f"\n{'=' * 50}")
    print("Done. You can now re-upload your ad PDFs.")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
