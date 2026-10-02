#!/bin/sh
# Restore drill for a nightly backup (Phase 1: "test a restore").
#
#   deploy/restore-check.sh /var/backups/amplafai-dashboard/popular_network-YYYYMMDD-HHMMSS.db.gz
#
# Opens the backup in a throwaway copy inside the running container (never
# touches the live DB), runs integrity_check, and prints row counts next to
# the live DB's so you can see the backup is whole and recent.
#
# To actually restore after a disaster (not done by this script):
#   docker compose stop dashboard
#   gunzip -c <backup.db.gz> > /tmp/restore.db
#   docker run --rm -v amplafai_dashboard-data:/d -v /tmp:/t alpine cp /t/restore.db /d/popular_network.db
#   docker compose start dashboard
set -eu

CONTAINER="${CONTAINER:-amplafai-dashboard-1}"
BACKUP="${1:?usage: restore-check.sh <backup.db.gz>}"

gunzip -c "$BACKUP" > /tmp/restore-check.db
docker cp /tmp/restore-check.db "$CONTAINER:/tmp/restore-check.db"
rm -f /tmp/restore-check.db

status=0
docker exec -i "$CONTAINER" python - <<'PY' || status=$?
import sqlite3, sys
tables = ["businesses", "users", "posts", "approvals", "reviews", "settings", "marketing_plan", "chat_turns"]
bk = sqlite3.connect("/tmp/restore-check.db")
live = sqlite3.connect("file:/app/data/popular_network.db?mode=ro", uri=True)
ok = bk.execute("PRAGMA integrity_check").fetchone()[0]
print(f"integrity_check: {ok}")
print(f"{'table':16} {'backup':>8} {'live':>8}")
for t in tables:
    b = bk.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    l = live.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"{t:16} {b:>8} {l:>8}")
bk.close(); live.close()
sys.exit(0 if ok == "ok" else 1)
PY
docker exec "$CONTAINER" rm -f /tmp/restore-check.db
exit $status
