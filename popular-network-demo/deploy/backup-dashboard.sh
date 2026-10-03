#!/bin/sh
# Nightly backup of the dashboard's SQLite DB (Phase 1).
#
# Runs on the droplet host from cron (deploy/cron.d/amplafai-dashboard-backup):
#   1. consistent snapshot via SQLite's online-backup API inside the container
#   2. integrity_check on the snapshot before keeping it
#   3. gzip into $BACKUP_DIR on the host (outside the Docker volume), keep 30 days
#   4. copy off the server to DigitalOcean Spaces IF $SPACES_ENV exists
#      (KEY/SECRET/BUCKET/REGION[/PREFIX] — see deploy/spaces.env.example), and
#      delete off-server copies older than $KEEP_DAYS; otherwise
#      say loudly that the off-server copy was skipped
#
# Exit code is non-zero on any failure so cron/monitoring can notice.
# Restore drill: deploy/restore-check.sh <backup.db.gz>
set -eu
umask 077  # backups hold password hashes: root-only files and directory

CONTAINER="${CONTAINER:-amplafai-dashboard-1}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/amplafai-dashboard}"
SPACES_ENV="${SPACES_ENV:-/opt/amplafai/backups/spaces.env}"
KEEP_DAYS="${KEEP_DAYS:-30}"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/popular_network-$TS.db"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
echo "[$TS] backup start"

docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3, sys
src = sqlite3.connect("/app/data/popular_network.db")
dst = sqlite3.connect("/tmp/nightly-backup.db")
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
n = dst.execute("SELECT count(*) FROM businesses").fetchone()[0]
dst.close(); src.close()
print(f"snapshot integrity={ok} businesses={n}")
sys.exit(0 if ok == "ok" else 1)
PY

docker cp "$CONTAINER:/tmp/nightly-backup.db" "$OUT"
docker exec "$CONTAINER" rm -f /tmp/nightly-backup.db
gzip -9 "$OUT"
chmod 600 "$OUT.gz"  # docker cp sets its own mode; umask doesn't reach it
echo "kept $OUT.gz ($(du -h "$OUT.gz" | cut -f1))"

find "$BACKUP_DIR" -name 'popular_network-*.db.gz' -mtime +"$KEEP_DAYS" -print -delete

if [ -f "$SPACES_ENV" ]; then
  # shellcheck disable=SC1090
  . "$SPACES_ENV"
  PREFIX="${SPACES_PREFIX:-dashboard}"
  s3() {
    docker run --rm \
      -e AWS_ACCESS_KEY_ID="$SPACES_KEY" -e AWS_SECRET_ACCESS_KEY="$SPACES_SECRET" -e AWS_DEFAULT_REGION=us-east-1 \
      -v "$BACKUP_DIR:/b:ro" amazon/aws-cli:2.17.0 \
      --endpoint-url "https://$SPACES_REGION.digitaloceanspaces.com" s3 "$@"
  }
  s3 cp "/b/popular_network-$TS.db.gz" "s3://$SPACES_BUCKET/$PREFIX/popular_network-$TS.db.gz" --only-show-errors
  echo "off-server copy: s3://$SPACES_BUCKET/$PREFIX/popular_network-$TS.db.gz"
  # Off-server copies follow the same $KEEP_DAYS as the local ones (the
  # privacy policy's 30-day deletion promise). Done here rather than by a
  # bucket lifecycle rule because the key may not be allowed to set one.
  CUTOFF=$(( $(date -u +%s) - ${SPACES_KEEP_DAYS:-$KEEP_DAYS} * 86400 ))
  s3 ls "s3://$SPACES_BUCKET/$PREFIX/" | while read -r day time _size name; do
    case "$name" in popular_network-*.db.gz) ;; *) continue ;; esac
    if [ "$(date -u -d "$day $time" +%s)" -lt "$CUTOFF" ]; then
      s3 rm "s3://$SPACES_BUCKET/$PREFIX/$name" --only-show-errors
      echo "expired off-server copy: $name"
    fi
  done
else
  echo "WARNING: off-server copy SKIPPED — $SPACES_ENV not found (this backup is on the droplet only)"
fi
echo "[$(date -u +%Y%m%d-%H%M%S)] backup done"
