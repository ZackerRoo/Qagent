#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
if [[ "${1:-}" != "--confirm-local-writers-stopped" ]]; then
  echo "usage: sudo $0 --confirm-local-writers-stopped" >&2
  echo "this confirmation asserts the Mac backend and scheduler are stopped" >&2
  exit 2
fi

STATE_DIR="${QAGENT_STATE_DIR:-/var/lib/qagent}"
SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
DB_PATH="$STATE_DIR/qagent.db"
if [[ ! -f "$DB_PATH" ]]; then
  echo "production database is missing: $DB_PATH" >&2
  exit 1
fi
python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30) as db:
    result = db.execute("PRAGMA quick_check").fetchone()
if result != ("ok",):
    raise SystemExit(f"database quick_check failed: {result!r}")
PY

for name in qagent-backend qagent-frontend; do
  [[ -x "/etc/sv/$name/run" ]] || { echo "missing /etc/sv/$name/run" >&2; exit 1; }
done
"$(dirname "$0")/sqlite_cutover_manifest.py" --preflight "$DB_PATH"
rm -f /etc/service/.qagent-backend.new /etc/service/.qagent-frontend.new
ln -s /etc/sv/qagent-backend /etc/service/.qagent-backend.new
mv -T /etc/service/.qagent-backend.new /etc/service/qagent-backend
ln -s /etc/sv/qagent-frontend /etc/service/.qagent-frontend.new
mv -T /etc/service/.qagent-frontend.new /etc/service/qagent-frontend
rm -f /etc/sv/qagent-backend/down /etc/sv/qagent-frontend/down
sv up /etc/service/qagent-backend /etc/service/qagent-frontend
if [[ -f /etc/cron.d/qagent-backup.disabled ]]; then
  mv /etc/cron.d/qagent-backup.disabled /etc/cron.d/qagent-backup
fi
touch "$STATE_DIR/.single-writer-approved"
chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR/.single-writer-approved"
echo "enabled Qagent runit services and daily backup after explicit single-writer confirmation"
