#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "root access is required to inspect runit supervise state; rerun with sudo: sudo $0" >&2
  exit 1
fi

STATE_DIR="${QAGENT_STATE_DIR:-/var/lib/qagent}"
EXPECTED_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
DB_PATH="$STATE_DIR/qagent.db"

for service in qagent-backend qagent-frontend; do
  if ! sv status "/etc/service/$service" | grep -q '^run:'; then
    echo "$service is not running under runit" >&2
    exit 1
  fi
done

for service in qagent-backend qagent-frontend; do
  pid="$(cat "/etc/service/$service/supervise/pid")"
  owner="$(ps -o user= -p "$pid" | tr -d ' ')"
  if [[ "$owner" != "$EXPECTED_USER" ]]; then
    echo "$service runs as $owner, expected $EXPECTED_USER" >&2
    exit 1
  fi
done

if [[ ! -f /etc/cron.d/qagent-backup ]]; then
  echo "Qagent backup cron is not enabled" >&2
  exit 1
fi

for port in 8000 5173; do
  if ! ss -H -ltn "sport = :$port" | awk '{print $4}' | grep -Fxq "127.0.0.1:$port"; then
    echo "port $port is not bound exactly on 127.0.0.1" >&2
    ss -H -ltn "sport = :$port" >&2 || true
    exit 1
  fi
done

python3 - "$DB_PATH" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30) as db:
    result = db.execute("PRAGMA quick_check").fetchone()
if result != ("ok",):
    raise SystemExit(f"database quick_check failed: {result!r}")
PY

curl --fail --silent --show-error http://127.0.0.1:8000/api/automation/scheduler >/dev/null
curl --fail --silent --show-error http://127.0.0.1:5173/ >/dev/null
echo "Qagent deployment verification passed"
