#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 SOURCE_SNAPSHOT DESTINATION_DB [--replace]" >&2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
  exit 2
fi

SOURCE_DB="$1"
DEST_DB="$2"
SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
REPLACE="${3:-}"
if [[ -n "$REPLACE" && "$REPLACE" != "--replace" ]]; then
  usage
  exit 2
fi
if [[ ! -f "$SOURCE_DB" ]]; then
  echo "snapshot does not exist: $SOURCE_DB" >&2
  exit 1
fi
if [[ "$SOURCE_DB" == "$DEST_DB" ]]; then
  echo "source and destination must differ" >&2
  exit 1
fi
for sidecar in "$SOURCE_DB-wal" "$SOURCE_DB-shm"; do
  if [[ -e "$sidecar" ]]; then
    echo "snapshot has SQLite sidecar; create a consistent backup first: $sidecar" >&2
    exit 1
  fi
done
for service in qagent-backend qagent-frontend; do
  if [[ -e "/etc/service/$service" || -L "/etc/service/$service" ]]; then
    echo "$service is enabled; disable both Qagent services before installing a snapshot" >&2
    exit 1
  fi
  supervise_pid="/etc/sv/$service/supervise/pid"
  if [[ -s "$supervise_pid" ]]; then
    pid="$(tr -dc '0-9' <"$supervise_pid")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "$service still has an active runsv supervisor (pid $pid)" >&2
      exit 1
    fi
  fi
done
if command -v ss >/dev/null 2>&1; then
  for port in 8000 5173; do
    if ss -H -ltn "sport = :$port" | grep -q .; then
      echo "port $port is still listening; refusing database replacement" >&2
      exit 1
    fi
  done
fi
if command -v pgrep >/dev/null 2>&1; then
  ACTIVE_QAGENT="$(pgrep -u "$SERVICE_USER" -af \
    '([/]uvicorn .*qagent|qagent[.]app:create_app|npm .*preview.*5173|node .*vite.*5173)' || true)"
  if [[ -n "$ACTIVE_QAGENT" ]]; then
    echo "Qagent-related process is still active for $SERVICE_USER:" >&2
    echo "$ACTIVE_QAGENT" >&2
    exit 1
  fi
fi
if [[ -e "$DEST_DB" && "$REPLACE" != "--replace" ]]; then
  echo "destination exists; refusing to replace it without --replace: $DEST_DB" >&2
  exit 1
fi

python3 - "$SOURCE_DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30) as db:
    result = db.execute("PRAGMA quick_check").fetchone()
if result != ("ok",):
    raise SystemExit(f"source quick_check failed: {result!r}")
PY

DEST_DIR="$(dirname "$DEST_DB")"
mkdir -p "$DEST_DIR"
TEMP_PATH="$(mktemp "$DEST_DIR/.qagent-restore.XXXXXX")"
trap 'rm -f "$TEMP_PATH"' EXIT
cp "$SOURCE_DB" "$TEMP_PATH"
chmod 0600 "$TEMP_PATH"

if [[ -e "$DEST_DB" ]]; then
  SAFETY_DIR="$DEST_DIR/before-restore.$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S%z)"
  mkdir -m 0700 "$SAFETY_DIR"
  for existing in "$DEST_DB" "$DEST_DB-wal" "$DEST_DB-shm"; do
    if [[ -e "$existing" ]]; then
      cp -p "$existing" "$SAFETY_DIR/"
    fi
  done
  rm -f "$DEST_DB-wal" "$DEST_DB-shm"
  echo "preserved previous SQLite file set in $SAFETY_DIR"
else
  for stale in "$DEST_DB-wal" "$DEST_DB-shm"; do
    if [[ -e "$stale" ]]; then
      echo "refusing stale destination sidecar without a destination database: $stale" >&2
      exit 1
    fi
  done
fi
# TEMP and DEST are in the same directory, so rename atomically replaces the
# stopped destination database without a window where qagent.db is absent.
mv -f "$TEMP_PATH" "$DEST_DB"
chown "$SERVICE_USER:$SERVICE_USER" "$DEST_DB"
chmod 0600 "$DEST_DB"
echo "installed verified snapshot at $DEST_DB"
