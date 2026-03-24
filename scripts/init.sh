#!/bin/bash
set -e  # Exit on any error

# Change to script directory
cd "$(dirname "$0")/.."

echo "======================================"
echo "Publisher RAG Demo - Initialization"
echo "======================================"

# Show working directory for debugging
echo "Working directory: $(pwd)"
echo "Data directory: $(pwd)/data"

# Check for required environment variable
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY environment variable is not set"
    exit 1
fi

# Ensure data directory exists
mkdir -p data/chroma_db data/documents data/ads data/events data/editions
touch data/ingested_files.json

# Check for pre-ingested databases (baked into Docker image)
echo ""
echo "[1/4] Checking for pre-ingested databases..."

if [ -d "data/chroma_db" ] && [ -f "data/articles.db" ]; then
    echo "✓ Databases found (baked into image)"
else
    echo "⚠ Warning: Databases not found, starting with empty database"
    mkdir -p data/chroma_db
fi

echo ""
echo "[2/4] Initializing database tables..."
python3 scripts/init_db.py
echo "✓ Database tables initialized"

# Verify tables exist
echo ""
echo "Verifying database..."
python3 -c "
import sqlite3
from pathlib import Path
db_path = Path('data/articles.db')
if not db_path.exists():
    print('ERROR: Database not created!')
    exit(1)
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
tables = [r[0] for r in cur.fetchall()]
print(f'Tables: {tables}')
if 'advertisements' not in tables:
    print('ERROR: advertisements table missing!')
    exit(1)
print('✓ Database verified')
conn.close()
"

echo ""
echo "[3/4] Loading sample advertisements..."
if python scripts/load_sample_ads.py; then
    echo "✓ Sample ads loaded"
else
    echo "⚠ Warning: Failed to load sample ads (continuing anyway)"
fi

echo ""
echo "[4/4] Loading sample events..."
if python scripts/load_sample_events.py; then
    echo "✓ Sample events loaded"
else
    echo "⚠ Warning: Failed to load sample events (continuing anyway)"
fi

echo ""

# Optional: reindex ads into the new advertisements Chroma collection
# Set RUN_AD_REINDEX_ON_STARTUP=true in Railway env vars to trigger
if [ "$RUN_AD_REINDEX_ON_STARTUP" = "true" ]; then
    echo "[+] Ad reindex requested (RUN_AD_REINDEX_ON_STARTUP=true)..."
    if python scripts/reindex_ads.py; then
        echo "✓ Ad reindex complete"
    else
        echo "⚠ Warning: Ad reindex failed (continuing anyway)"
    fi
else
    echo "[+] Ad reindex skipped (set RUN_AD_REINDEX_ON_STARTUP=true to enable)"
fi

echo ""
echo "======================================"
echo "Initialization complete!"
echo "======================================"
echo ""

# Start admin dashboard in background
echo "Starting admin dashboard on port 7861..."
python src/admin_dashboard.py &
ADMIN_PID=$!
echo "✓ Admin dashboard started (PID: $ADMIN_PID)"

echo ""
echo "Starting chatbot on port 7860..."
echo ""

# Start the main chatbot (foreground)
exec python src/chatbot.py
