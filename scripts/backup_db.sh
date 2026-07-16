#!/bin/bash
# Consistent SQLite backup via the sqlite3 backup API (safe while the app is
# running), gzip-compressed, keeping the newest $KEEP copies.
#
# Cron example (daily at 03:30):
#   30 3 * * * /home/micu/Video/scripts/backup_db.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$PROJECT_DIR/db.sqlite3"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/video}"
KEEP="${KEEP:-14}"
PYTHON="$PROJECT_DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/db-$STAMP.sqlite3"

"$PYTHON" - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
PY

gzip "$OUT"
echo "Backup: $OUT.gz"

# Rotate: keep the newest $KEEP archives.
ls -1t "$BACKUP_DIR"/db-*.sqlite3.gz 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm --
