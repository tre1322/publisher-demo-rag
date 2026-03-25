#!/bin/bash
set -e  # Exit on any error

# Change to script directory
cd "$(dirname "$0")/.."

echo "[INIT] init.sh started"
echo "======================================"
echo "Publisher RAG Demo - Initialization"
echo "======================================"

# Show working directory for debugging
echo "[INIT] Working directory: $(pwd)"
echo "[INIT] Data directory: $(pwd)/data"
echo "[INIT] RUN_AD_REINDEX_ON_STARTUP=${RUN_AD_REINDEX_ON_STARTUP:-<not set>}"

# Check for required environment variable
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY environment variable is not set"
    exit 1
fi

# Ensure data directory exists
mkdir -p data/chroma_db data/documents data/ads data/events data/editions
touch data/ingested_files.json

# ALWAYS copy staged quadd DB into the mounted volume — overwrites old versions
# (Railway volume persists old files; we need the latest baked version every deploy)
if [ -f "/app/staged/quadd_articles.db" ]; then
    echo "[INIT] Copying staged quadd_articles.db into data volume (overwriting any old version)..."
    cp -f /app/staged/quadd_articles.db data/quadd_articles.db
    echo "[INIT] ✓ quadd_articles.db copied ($(ls -la data/quadd_articles.db))"
else
    echo "[INIT] WARNING: No staged quadd_articles.db found at /app/staged/"
fi

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
echo "[DEBUG] articles.db exists before init_db: $(ls -la data/articles.db 2>&1)"
echo "[DEBUG] Article count before init_db: $(python3 -c "import sqlite3; c=sqlite3.connect('data/articles.db'); print(c.execute('SELECT COUNT(*) FROM articles').fetchone()[0])" 2>&1)"
echo "[DEBUG] chroma_db contents: $(ls -la data/chroma_db/ 2>&1)"
python3 scripts/init_db.py
echo "✓ Database tables initialized"
echo "[DEBUG] Article count after init_db: $(python3 -c "import sqlite3; c=sqlite3.connect('data/articles.db'); print(c.execute('SELECT COUNT(*) FROM articles').fetchone()[0])" 2>&1)"

echo ""
echo "[2.5/4] Seeding articles from quadd extraction..."
python3 scripts/seed_articles.py || echo "⚠ Warning: Article seeding failed (continuing)"
echo "[DEBUG] Article count after seed: $(python3 -c "import sqlite3; c=sqlite3.connect('data/articles.db'); print(c.execute('SELECT COUNT(*) FROM articles').fetchone()[0])" 2>&1)"

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
# Optional: wipe all ads for a fresh start (set RESET_ADS_ON_STARTUP=true)
echo "[INIT] RESET_ADS_ON_STARTUP='${RESET_ADS_ON_STARTUP:-<not set>}'"
if [ "${RESET_ADS_ON_STARTUP}" = "true" ]; then
    echo "[INIT] Resetting all ads (SQLite + Chroma)"
    python scripts/reset_ads.py && RESET_RC=$? || RESET_RC=$?
    echo "[INIT] reset_ads.py exited with code ${RESET_RC}"
else
    echo "[INIT] Ad reset skipped"
fi

echo ""
echo "[INIT] About to evaluate reindex flag"
echo "[INIT] RUN_AD_REINDEX_ON_STARTUP='${RUN_AD_REINDEX_ON_STARTUP}'"
# Reindex ads into the advertisements Chroma collection (guarded by env var)
if [ "${RUN_AD_REINDEX_ON_STARTUP}" = "true" ]; then
    echo "[INIT] Reindex condition matched, running scripts/reindex_ads.py"
    python scripts/reindex_ads.py && REINDEX_RC=$? || REINDEX_RC=$?
    echo "[INIT] reindex_ads.py exited with code ${REINDEX_RC}"
    if [ "${REINDEX_RC}" -ne 0 ]; then
        echo "[INIT] WARNING: Ad reindex failed (exit ${REINDEX_RC}), continuing anyway"
    else
        echo "[INIT] Ad reindex complete"
    fi
else
    echo "[INIT] Reindex condition not matched, skipping"
fi

echo ""
echo "[2.5/4] Ingesting quadd extraction articles..."
# Ingest articles extracted by the quadd pipeline (if quadd DB is available)
QUADD_DB="${QUADD_DB_PATH:-/app/data/quadd_articles.db}"
if [ -f "$QUADD_DB" ]; then
    echo "[INIT] Found quadd DB at $QUADD_DB, ingesting articles..."
    python scripts/ingest_quadd_articles.py --quadd-db "$QUADD_DB" && QUADD_RC=$? || QUADD_RC=$?
    if [ "${QUADD_RC}" -ne 0 ]; then
        echo "[INIT] WARNING: Quadd ingestion failed (exit ${QUADD_RC}), continuing"
    else
        echo "✓ Quadd articles ingested"
    fi
else
    echo "[INIT] No quadd DB found at $QUADD_DB, skipping article ingestion"
fi

# Reindex articles into Chroma AFTER all ingestion is done
echo ""
echo "[INIT] RUN_ARTICLE_REINDEX_ON_STARTUP='${RUN_ARTICLE_REINDEX_ON_STARTUP}'"
echo "[DEBUG] Article count before reindex: $(python3 -c "import sqlite3; c=sqlite3.connect('data/articles.db'); c.row_factory=sqlite3.Row; print(c.execute('SELECT COUNT(*) FROM articles WHERE (cleaned_text IS NOT NULL AND length(cleaned_text) > 50) OR (full_text IS NOT NULL AND length(full_text) > 50)').fetchone()[0])" 2>&1)"
if [ "${RUN_ARTICLE_REINDEX_ON_STARTUP}" = "true" ]; then
    echo "[INIT] Reindexing articles into ChromaDB..."
    python scripts/reindex_articles.py && ART_RC=$? || ART_RC=$?
    if [ "${ART_RC}" -ne 0 ]; then
        echo "[INIT] WARNING: Article reindex failed (exit ${ART_RC}), continuing"
    else
        echo "✓ Article reindex complete"
    fi
else
    echo "[INIT] Article reindex skipped (set RUN_ARTICLE_REINDEX_ON_STARTUP=true to enable)"
fi

echo ""
echo "[3/4] Sample data loading..."
# Sample ads/events are opt-in — only load if LOAD_SAMPLE_DATA=true
# These are Pipestone demo businesses, not real production data
if [ "${LOAD_SAMPLE_DATA}" = "true" ]; then
    echo "[INIT] Loading sample data (LOAD_SAMPLE_DATA=true)"
    if python scripts/load_sample_ads.py; then
        echo "✓ Sample ads loaded"
    else
        echo "⚠ Warning: Failed to load sample ads (continuing anyway)"
    fi
    if python scripts/load_sample_events.py; then
        echo "✓ Sample events loaded"
    else
        echo "⚠ Warning: Failed to load sample events (continuing anyway)"
    fi
else
    echo "[INIT] Sample data skipped (set LOAD_SAMPLE_DATA=true to enable)"
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
# Force rebuild 1774461920
