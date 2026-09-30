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

RUNSV_READY_TIMEOUT="${QAGENT_RUNSV_READY_TIMEOUT:-20}"
if [[ ! "$RUNSV_READY_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "QAGENT_RUNSV_READY_TIMEOUT must be a positive integer" >&2
  exit 1
fi

QAGENT_HOME="${QAGENT_HOME:-/home/${QAGENT_SERVICE_USER:-luozhenkun}/qagent}"
STATE_DIR="${QAGENT_STATE_DIR:-$QAGENT_HOME/state}"
SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
DB_PATH="$STATE_DIR/qagent.db"
FINANCIAL_APPROVAL=/home/qagent-financial-research-approved
financial_research_approved() {
  [[ ! -L /home && "$(stat -c %u /home)" == 0 && \
     $((8#$(stat -c %a /home) & 8#022)) -eq 0 ]] || return 1
  [[ -f "$FINANCIAL_APPROVAL" && ! -L "$FINANCIAL_APPROVAL" ]] || return 1
  [[ "$(stat -c '%u:%g:%a' "$FINANCIAL_APPROVAL")" == "0:0:600" ]] || return 1
  cmp -s "$FINANCIAL_APPROVAL" \
    <(printf 'financial-peer-control-v11-v13:%s\n' "$QAGENT_HOME")
}
financial_research_ready() {
  [[ -d "$QAGENT_HOME/research/daily-financial-20260924-v11" && \
     ! -L "$QAGENT_HOME/research/daily-financial-20260924-v11" && \
     -d "$QAGENT_HOME/research/financial-forward-20260924-v13" && \
     ! -L "$QAGENT_HOME/research/financial-forward-20260924-v13" ]] && \
    financial_research_approved
}
disable_financial_cron() {
  if [[ -f /etc/cron.d/qagent-financial-peer-control ]]; then
    mv /etc/cron.d/qagent-financial-peer-control \
      /etc/cron.d/qagent-financial-peer-control.disabled
  fi
}
disable_financial_cron
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

wait_for_runsv() {
  local service="$1"
  local deadline=$((SECONDS + RUNSV_READY_TIMEOUT))
  while ! sv status "/etc/service/$service" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "runsv did not supervise $service within ${RUNSV_READY_TIMEOUT}s; check that runsvdir watches /etc/service" >&2
      return 1
    fi
    sleep 0.2
  done
}
for name in qagent-backend qagent-frontend; do
  if ! wait_for_runsv "$name"; then
    cleanup_failed=0
    for linked_name in qagent-backend qagent-frontend; do
      if ! unlink "/etc/service/$linked_name"; then
        echo "failed to remove /etc/service/$linked_name after runsv readiness failure" >&2
        cleanup_failed=1
      fi
    done
    if [[ -f /etc/cron.d/qagent-backup ]]; then
      if ! mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled; then
        echo "failed to disable Qagent backup cron after runsv readiness failure" >&2
        cleanup_failed=1
      fi
    fi
    if ! disable_financial_cron; then
      echo "failed to disable Financial research cron after runsv readiness failure" >&2
      cleanup_failed=1
    fi
    if ! rm -f "$STATE_DIR/.single-writer-approved"; then
      echo "failed to remove the single-writer approval marker after runsv readiness failure" >&2
      cleanup_failed=1
    fi
    if (( cleanup_failed != 0 )); then
      echo "Qagent enable stopped before removing down files, but readiness cleanup was incomplete; any retained service link remains disabled by its down file" >&2
    else
      echo "Qagent enable stopped before removing down files; service links were removed, backup cron remains disabled, and no approval marker remains" >&2
    fi
    exit 1
  fi
done

rm -f /etc/sv/qagent-backend/down /etc/sv/qagent-frontend/down
if ! sv up /etc/service/qagent-backend /etc/service/qagent-frontend; then
  rollback_failed=0
  shutdown_failed=0
  for name in qagent-backend qagent-frontend; do
    if ! touch "/etc/sv/$name/down"; then
      echo "failed to restore /etc/sv/$name/down after sv up failure" >&2
      rollback_failed=1
    fi
  done
  if [[ -f /etc/cron.d/qagent-backup ]]; then
    if ! mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled; then
      echo "failed to disable Qagent backup cron after sv up failure" >&2
      rollback_failed=1
    fi
  fi
  if ! disable_financial_cron; then
    echo "failed to disable Financial research cron after sv up failure" >&2
    rollback_failed=1
  fi
  if ! rm -f "$STATE_DIR/.single-writer-approved"; then
    echo "failed to remove the single-writer approval marker after sv up failure" >&2
    rollback_failed=1
  fi
  for name in qagent-backend qagent-frontend; do
    if ! sv -w "$RUNSV_READY_TIMEOUT" down "/etc/service/$name"; then
      echo "failed to confirm $name down within ${RUNSV_READY_TIMEOUT}s after sv up failure" >&2
      rollback_failed=1
      shutdown_failed=1
    fi
  done
  if (( shutdown_failed == 0 )); then
    for name in qagent-backend qagent-frontend; do
      if ! unlink "/etc/service/$name"; then
        echo "failed to remove /etc/service/$name after confirming both services down" >&2
        rollback_failed=1
      fi
    done
  fi
  if (( shutdown_failed != 0 )); then
    echo "Qagent enable failed and shutdown confirmation was incomplete; down files were requested, service links remain managed by runit, backup cron is disabled, and no approval marker remains" >&2
  elif (( rollback_failed != 0 )); then
    echo "Qagent enable failed after both services stopped, but disabled-state cleanup was incomplete; any retained service link remains managed by runit with its down file present" >&2
  else
    echo "Qagent enable failed; both services were returned to down state, service links were removed, backup cron remains disabled, and no approval marker was created" >&2
  fi
  exit 1
fi
if [[ -f /etc/cron.d/qagent-backup.disabled ]]; then
  mv /etc/cron.d/qagent-backup.disabled /etc/cron.d/qagent-backup
fi
if financial_research_ready; then
  if [[ -f /etc/cron.d/qagent-financial-peer-control.disabled ]]; then
    mv /etc/cron.d/qagent-financial-peer-control.disabled \
      /etc/cron.d/qagent-financial-peer-control
  fi
else
  disable_financial_cron
fi
touch "$STATE_DIR/.single-writer-approved"
chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR/.single-writer-approved"
echo "enabled Qagent runit services and daily backup after explicit single-writer confirmation"
