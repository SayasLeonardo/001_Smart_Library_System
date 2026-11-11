#!/usr/bin/env bash
# Daily backup of the Smart Library data. Installed to cron by setup_vps.sh.
# Copy /var/backups/smart-library to another computer regularly: a backup on the same server
# does not protect you if the server itself is lost.
set -euo pipefail

DATA_DIR=/var/lib/smart-library
DEST=/var/backups/smart-library
KEEP_DAYS=14
STAMP="$(date +%F_%H%M)"
mkdir -p "$DEST"

if [ -f "$DATA_DIR/smart_library.db" ]; then
    sqlite3 "$DATA_DIR/smart_library.db" ".backup '$DEST/smart_library_$STAMP.db'"
    [ -d "$DATA_DIR/uploads" ] && tar -czf "$DEST/uploads_$STAMP.tar.gz" -C "$DATA_DIR" uploads
elif [ -r /etc/smart-library/smart-library.env ]; then
    # PostgreSQL mode
    set -a; . /etc/smart-library/smart-library.env; set +a
    [ -n "${DATABASE_URL:-}" ] && pg_dump "$DATABASE_URL" | gzip > "$DEST/smart_library_$STAMP.sql.gz"
fi

find "$DEST" -type f -mtime +"$KEEP_DAYS" -delete
