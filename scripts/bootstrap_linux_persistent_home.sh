#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6 || true)"
QAGENT_HOME="${QAGENT_HOME:-${SERVICE_HOME:-/home/$SERVICE_USER}/qagent}"
case "$QAGENT_HOME" in /home/*) ;; *) echo "QAGENT_HOME must be under /home" >&2; exit 1 ;; esac
APP_DIR="${QAGENT_APP_DIR:-$QAGENT_HOME/current}"
STATE_DIR="${QAGENT_STATE_DIR:-$QAGENT_HOME/state}"
BACKUP_DIR="${QAGENT_BACKUP_DIR:-$QAGENT_HOME/backups}"
LOG_DIR="${QAGENT_LOG_DIR:-$QAGENT_HOME/logs}"
CONFIG_DIR="${QAGENT_CONFIG_DIR:-$QAGENT_HOME/config}"
ENV_FILE="$CONFIG_DIR/qagent.env"

fail() { echo "$*" >&2; exit 1; }
[[ -d /home && "$(findmnt -n -o TARGET --target /home 2>/dev/null || true)" == /home ]] \
  || fail "/home must be mounted persistent storage before bootstrap"
[[ -f "$APP_DIR/deploy/runit/backend.run.in" && -f "$APP_DIR/deploy/runit/frontend.run.in" ]] \
  || fail "Qagent repository is missing from persistent home: $APP_DIR"
[[ -f "$APP_DIR/scripts/resolve_linux_runtime.sh" ]] \
  || fail "Qagent runtime resolver is missing from persistent home: $APP_DIR"
source "$APP_DIR/scripts/resolve_linux_runtime.sh"
resolve_qagent_runtime
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "persistent Qagent config is missing or unsafe: $ENV_FILE"
[[ -f "$STATE_DIR/qagent.db" && ! -L "$STATE_DIR/qagent.db" ]] \
  || fail "production database is missing or unsafe; bootstrap will not create it"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "service user does not exist: $SERVICE_USER"
runuser -u "$SERVICE_USER" -- test -r "$ENV_FILE" \
  || fail "service user cannot read persistent Qagent config: $ENV_FILE"
[[ -x /usr/bin/chpst && -d /etc/service && -d /etc/cron.d && -d /etc/logrotate.d && -r /etc/timezone ]] \
  || fail "runit, /etc/service, and UTC timezone configuration must be prepared"
[[ "$(tr -d '[:space:]' </etc/timezone)" =~ ^(UTC|Etc/UTC|GMT|Etc/GMT)$ ]] \
  || fail "Qagent cron definitions require host timezone UTC"
python3 - "$STATE_DIR/qagent.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30) as db:
    result = db.execute("PRAGMA quick_check").fetchone()
if result != ("ok",):
    raise SystemExit(f"production database quick_check failed: {result!r}")
PY
python3 - "$ENV_FILE" "$STATE_DIR/qagent.db" "$STATE_DIR" <<'PY'
import sys

config_path, db_path, data_dir = sys.argv[1:]
values = {}
with open(config_path, encoding="utf-8") as source:
    for line in source:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
expected_url = "sqlite:///" + db_path
if values.get("QAGENT_DATABASE_URL") != expected_url:
    raise SystemExit("persistent config QAGENT_DATABASE_URL does not point to persistent state DB")
if values.get("QAGENT_DATA_DIR") != data_dir:
    raise SystemExit("persistent config QAGENT_DATA_DIR does not point to persistent state directory")
PY

DESIRED_ENABLED=0
if [[ -f "$STATE_DIR/.single-writer-approved" ]]; then
  DESIRED_ENABLED=1
fi

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR" "$BACKUP_DIR" "$LOG_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" /etc/qagent
ln -sfn "$ENV_FILE" /etc/qagent/qagent.env
install -d -m 0755 -o root -g root /etc/sv

render() {
  sed -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
      -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" \
      -e "s|@QAGENT_HOME@|$QAGENT_HOME|g" \
      -e "s|@APP_DIR@|$APP_DIR|g" \
      -e "s|@ENV_FILE@|$ENV_FILE|g" \
      -e "s|@DAILY_BUNDLE@|$QAGENT_HOME/research/daily-financial-20260924-v11|g" \
      -e "s|@FORWARD_BUNDLE@|$QAGENT_HOME/research/financial-forward-20260924-v13|g" \
      -e "s|@STATE_DIR@|$STATE_DIR|g" \
      -e "s|@BACKUP_DIR@|$BACKUP_DIR|g" \
      -e "s|@NODE_BIN_DIR@|$(dirname "$NODE_BIN")|g" \
      -e "s|@NPM_BIN@|$NPM_BIN|g" \
      -e "s|@LOG_DIR@|$LOG_DIR|g" "$1" >"$2"
}

for name in backend frontend; do
  stage="$(mktemp -d /etc/sv/.qagent-$name.XXXXXX)"
  render "$APP_DIR/deploy/runit/$name.run.in" "$stage/run"
  chmod 0755 "$stage/run"
  if (( DESIRED_ENABLED == 0 )); then touch "$stage/down"; fi
  if [[ -e "/etc/sv/qagent-$name" ]] && diff -qr "/etc/sv/qagent-$name" "$stage" >/dev/null; then
    rm -rf "$stage"
    continue
  fi
  if [[ -L "/etc/service/qagent-$name" ]]; then
    target="$(readlink -f "/etc/service/qagent-$name" || true)"
    [[ "$target" == "/etc/sv/qagent-$name" ]] || fail "unexpected service link: /etc/service/qagent-$name"
    unlink "/etc/service/qagent-$name"
  elif [[ -e "/etc/service/qagent-$name" ]]; then
    fail "unexpected non-symlink service entry: /etc/service/qagent-$name"
  fi
  if [[ -e "/etc/sv/qagent-$name" ]]; then
    mv "/etc/sv/qagent-$name" "/etc/sv/qagent-$name.pre-bootstrap.$(date +%s)"
  fi
  mv "$stage" "/etc/sv/qagent-$name"
done

render "$APP_DIR/deploy/runit/qagent-backup.cron.in" /etc/cron.d/.qagent-backup.tmp
sed -i -e "s|@BACKUP_KEEP_DAYS@|${QAGENT_BACKUP_KEEP_DAYS:-5}|g" \
       -e "s|@BACKUP_MIN_FREE_BYTES@|${QAGENT_BACKUP_MIN_FREE_BYTES:-10737418240}|g" \
       /etc/cron.d/.qagent-backup.tmp
chmod 0644 /etc/cron.d/.qagent-backup.tmp
mv -f /etc/cron.d/.qagent-backup.tmp /etc/cron.d/qagent-backup.disabled

render "$APP_DIR/deploy/logrotate/qagent" /etc/logrotate.d/.qagent.tmp
chmod 0644 /etc/logrotate.d/.qagent.tmp
mv -f /etc/logrotate.d/.qagent.tmp /etc/logrotate.d/qagent

# This cron file is also disabled. Restore its source template only when the
# pinned peer-control bundles are already present; never trigger their jobs here.
PEER_READY=0
if [[ -d "$QAGENT_HOME/research/daily-financial-20260924-v11" && \
      -d "$QAGENT_HOME/research/financial-forward-20260924-v13" ]]; then
  PEER_READY=1
  render "$APP_DIR/deploy/runit/qagent-financial-peer-control.cron.in" \
      /etc/cron.d/.qagent-financial-peer-control.tmp
  chmod 0644 /etc/cron.d/.qagent-financial-peer-control.tmp
  mv -f /etc/cron.d/.qagent-financial-peer-control.tmp /etc/cron.d/qagent-financial-peer-control.disabled
fi

set_cron_state() {
  local name="$1"
  if (( DESIRED_ENABLED == 1 )); then
    if [[ -f "/etc/cron.d/$name.disabled" ]]; then
      mv -f "/etc/cron.d/$name.disabled" "/etc/cron.d/$name"
    fi
  else
    if [[ -f "/etc/cron.d/$name" ]]; then
      mv -f "/etc/cron.d/$name" "/etc/cron.d/$name.disabled"
    fi
  fi
}

for name in qagent-backend qagent-frontend; do
  service_link="/etc/service/$name"
  service_target="/etc/sv/$name"
  if (( DESIRED_ENABLED == 1 )); then
    rm -f "$service_target/down"
    if [[ ! -L "$service_link" ]]; then
      ln -s "$service_target" "$service_link"
    elif [[ "$(readlink -f "$service_link")" != "$service_target" ]]; then
      fail "unexpected service link: $service_link"
    fi
  else
    touch "$service_target/down"
    if [[ -L "$service_link" ]]; then
      [[ "$(readlink -f "$service_link")" == "$service_target" ]] \
        || fail "unexpected service link: $service_link"
      unlink "$service_link"
    elif [[ -e "$service_link" ]]; then
      fail "unexpected non-symlink service entry: $service_link"
    fi
  fi
done
set_cron_state qagent-backup
if (( PEER_READY == 1 )); then
  set_cron_state qagent-financial-peer-control
elif [[ -f /etc/cron.d/qagent-financial-peer-control ]]; then
  mv -f /etc/cron.d/qagent-financial-peer-control \
    /etc/cron.d/qagent-financial-peer-control.disabled
fi

if (( DESIRED_ENABLED == 1 )); then
  echo "persistent Qagent definitions restored to previously enabled state"
else
  echo "persistent Qagent definitions restored; services and all Qagent cron jobs remain disabled"
fi
