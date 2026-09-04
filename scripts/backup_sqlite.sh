#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "usage: $0 SOURCE_DB BACKUP_DIR [KEEP_DAYS] [MIN_FREE_BYTES]" >&2
  exit 2
fi

SOURCE_DB="$1"
BACKUP_DIR="$2"
KEEP_DAYS="${3:-${QAGENT_BACKUP_KEEP_DAYS:-5}}"
MIN_FREE_BYTES="${4:-${QAGENT_BACKUP_MIN_FREE_BYTES:-10737418240}}"

if [[ ! -f "$SOURCE_DB" ]]; then
  echo "source database does not exist: $SOURCE_DB" >&2
  exit 1
fi
if [[ ! "$KEEP_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "KEEP_DAYS must be a positive integer" >&2
  exit 2
fi
if [[ ! "$MIN_FREE_BYTES" =~ ^[0-9]+$ ]]; then
  echo "MIN_FREE_BYTES must be a non-negative integer" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
LOCK_FILE="$BACKUP_DIR/.backup.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "another Qagent backup is already running" >&2
    exit 0
  fi
else
  LOCK_DIR="$BACKUP_DIR/.backup-lock-dir"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another Qagent backup is already running" >&2
    exit 0
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
fi

STAMP="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S%z)"
FINAL_PATH="$BACKUP_DIR/qagent-$STAMP.db"
TEMP_PATH="$BACKUP_DIR/.qagent-$STAMP.db.tmp"
trap 'rm -f "$TEMP_PATH"; if [[ -n "${LOCK_DIR:-}" ]]; then rmdir "$LOCK_DIR" 2>/dev/null || true; fi' EXIT

python3 - "$SOURCE_DB" "$BACKUP_DIR" "$MIN_FREE_BYTES" <<'PY'
import os
import sqlite3
import sys

source, backup_dir, minimum_free_text = sys.argv[1:]
minimum_free = int(minimum_free_text)
with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as database:
    page_count = int(database.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(database.execute("PRAGMA page_size").fetchone()[0])
estimated_backup = max(os.stat(source).st_size, page_count * page_size)
filesystem = os.statvfs(backup_dir)
available = filesystem.f_bavail * filesystem.f_frsize
required = estimated_backup + minimum_free
if available < required:
    raise SystemExit(
        "insufficient backup space: "
        f"available_bytes={available} estimated_backup_bytes={estimated_backup} "
        f"minimum_free_after_backup_bytes={minimum_free} required_bytes={required}"
    )
PY

python3 - "$SOURCE_DB" "$TEMP_PATH" <<'PY'
import sqlite3
import sys

source, destination = sys.argv[1:]
with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as src:
    with sqlite3.connect(destination, timeout=30) as dst:
        src.backup(dst, pages=1_000, sleep=0.05)
        result = dst.execute("PRAGMA quick_check").fetchone()
if result != ("ok",):
    raise SystemExit(f"backup quick_check failed: {result!r}")
PY

chmod 0600 "$TEMP_PATH"
mv "$TEMP_PATH" "$FINAL_PATH"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'qagent-*.db' -mtime "+$KEEP_DAYS" -delete
echo "$FINAL_PATH"
