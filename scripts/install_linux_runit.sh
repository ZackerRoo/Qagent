#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6 || true)"
QAGENT_HOME="${QAGENT_HOME:-${SERVICE_HOME:-/home/$SERVICE_USER}/qagent}"
case "$QAGENT_HOME" in /home/*) ;; *) echo "QAGENT_HOME must be under /home" >&2; exit 1 ;; esac
APP_DIR="${QAGENT_APP_DIR:-$QAGENT_HOME/current}"
STATE_DIR="${QAGENT_STATE_DIR:-$QAGENT_HOME/state}"
BACKUP_DIR="${QAGENT_BACKUP_DIR:-$QAGENT_HOME/backups}"
BACKUP_KEEP_DAYS="${QAGENT_BACKUP_KEEP_DAYS:-5}"
BACKUP_MIN_FREE_BYTES="${QAGENT_BACKUP_MIN_FREE_BYTES:-10737418240}"
LOG_DIR="${QAGENT_LOG_DIR:-$QAGENT_HOME/logs}"
ENV_DIR="/etc/qagent"
CONFIG_DIR="${QAGENT_CONFIG_DIR:-$QAGENT_HOME/config}"
ENV_FILE="$CONFIG_DIR/qagent.env"
SV_DIR="/etc/sv"
SERVICE_DIR="/etc/service"
ROLLBACK_DIR="${QAGENT_RUNIT_ROLLBACK_DIR:-$BACKUP_DIR/deploy-rollback}"

if [[ ! "$BACKUP_KEEP_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "QAGENT_BACKUP_KEEP_DAYS must be a positive integer" >&2
  exit 2
fi
if [[ ! "$BACKUP_MIN_FREE_BYTES" =~ ^[0-9]+$ ]]; then
  echo "QAGENT_BACKUP_MIN_FREE_BYTES must be a non-negative integer" >&2
  exit 2
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "service user does not exist: $SERVICE_USER" >&2
  exit 1
fi
if [[ -z "$SERVICE_HOME" || ! -d "$SERVICE_HOME" ]]; then
  echo "service home does not exist: $SERVICE_HOME" >&2
  exit 1
fi
if [[ ! -d /home || "$(findmnt -n -o TARGET --target /home 2>/dev/null || true)" != "/home" ]]; then
  echo "/home must be a mounted persistent filesystem before installing Qagent" >&2
  exit 1
fi
if [[ ! -d "$SERVICE_DIR" || ! -x /usr/bin/runsvdir ]]; then
  echo "active runit layout was not found (/etc/service and /usr/bin/runsvdir required)" >&2
  exit 1
fi
if [[ ! -x /usr/bin/chpst ]]; then
  echo "runit chpst is required at /usr/bin/chpst" >&2
  exit 1
fi
if [[ ! -r /etc/timezone ]]; then
  echo "the backup cron requires a Debian/Ubuntu host with /etc/timezone set to UTC" >&2
  exit 1
fi
CRON_HOST_TIMEZONE="$(tr -d '[:space:]' </etc/timezone)"
case "$CRON_HOST_TIMEZONE" in
  UTC|Etc/UTC|GMT|Etc/GMT) ;;
  *)
    echo "the backup cron is fixed at 19:30 UTC; host timezone must be UTC, found: $CRON_HOST_TIMEZONE" >&2
    exit 1
    ;;
esac
if [[ "$(env -u TZ date +%z)" != "+0000" ]]; then
  echo "the backup cron requires a zero-offset UTC host" >&2
  exit 1
fi
for name in qagent-backend qagent-frontend; do
  if [[ -e "$SERVICE_DIR/$name" || -L "$SERVICE_DIR/$name" ]]; then
    echo "$SERVICE_DIR/$name is enabled; disable Qagent before replacing service definitions" >&2
    exit 1
  fi
done
if [[ ! -f "$APP_DIR/backend/pyproject.toml" || ! -f "$APP_DIR/frontend/package-lock.json" ]]; then
  echo "QAGENT_APP_DIR is not a Qagent checkout: $APP_DIR" >&2
  exit 1
fi
source "$ROOT_DIR/scripts/resolve_linux_runtime.sh"
resolve_qagent_runtime
for binary in "$PYTHON_BIN" "$UV_BIN" "$NODE_BIN" "$NPM_BIN"; do
  runuser -u "$SERVICE_USER" -- test -x "$binary" || {
    echo "service user cannot execute runtime: $binary" >&2; exit 1;
  }
done

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$QAGENT_HOME/releases" "$STATE_DIR" "$BACKUP_DIR" "$LOG_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" "$CONFIG_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" "$ENV_DIR"
install -d -m 0700 -o root -g root "$ROLLBACK_DIR"
install -d -m 0755 -o root -g root "$SV_DIR"

ENV_FILE_MODE="0640"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_DIR/qagent.env" && ! -L "$ENV_DIR/qagent.env" ]]; then
    install -m 0640 -o root -g "$SERVICE_USER" "$ENV_DIR/qagent.env" "$ENV_FILE"
  fi
fi
if [[ ! -f "$ENV_FILE" ]]; then
  umask 0027
  {
    echo "QAGENT_ENVIRONMENT=production"
    echo "QAGENT_HOME=$QAGENT_HOME"
    echo "QAGENT_DATA_DIR=$STATE_DIR"
    echo "QAGENT_DATABASE_URL=sqlite:///$STATE_DIR/qagent.db"
  } >"$ENV_FILE"
else
  echo "preserving existing environment file: $ENV_FILE"
  CURRENT_ENV_MODE="$(stat -c '%a' "$ENV_FILE")"
  printf -v ENV_FILE_MODE '%04o' "$(((8#$CURRENT_ENV_MODE & 8#600) | 8#040))"
fi
"$PYTHON_BIN" "$APP_DIR/scripts/merge_proxy_environment.py" \
  --source /etc/environment --target "$ENV_FILE"
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod "$ENV_FILE_MODE" "$ENV_FILE"
ln -sfn "$ENV_FILE" "$ENV_DIR/qagent.env"

runuser -u "$SERVICE_USER" -- "$UV_BIN" sync \
  --directory "$APP_DIR/backend" --frozen --no-dev --python "$PYTHON_BIN"
if ! "$APP_DIR/backend/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "existing backend virtualenv uses Python <3.11; recreate $APP_DIR/backend/.venv" >&2
  exit 1
fi
for log_name in backend.log frontend.log; do
  touch "$LOG_DIR/$log_name"
  chown "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR/$log_name"
  chmod 0640 "$LOG_DIR/$log_name"
done
runuser -u "$SERVICE_USER" -- env -u QAGENT_DATABASE_URL \
  PYTHONPATH="$APP_DIR/backend" "$APP_DIR/backend/.venv/bin/python" \
  "$APP_DIR/scripts/verify_isolated_linux_install.py"
(
  cd "$APP_DIR/frontend"
  runuser -u "$SERVICE_USER" -- env PATH="$(dirname "$NODE_BIN"):$PATH" "$NPM_BIN" ci
  runuser -u "$SERVICE_USER" -- env PATH="$(dirname "$NODE_BIN"):$PATH" "$NPM_BIN" run build
)

render() {
  sed \
    -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@SERVICE_HOME@|$SERVICE_HOME|g" \
    -e "s|@QAGENT_HOME@|$QAGENT_HOME|g" \
    -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@STATE_DIR@|$STATE_DIR|g" \
    -e "s|@BACKUP_DIR@|$BACKUP_DIR|g" \
    -e "s|@BACKUP_KEEP_DAYS@|$BACKUP_KEEP_DAYS|g" \
    -e "s|@BACKUP_MIN_FREE_BYTES@|$BACKUP_MIN_FREE_BYTES|g" \
    -e "s|@LOG_DIR@|$LOG_DIR|g" \
    -e "s|@NODE_BIN_DIR@|$(dirname "$NODE_BIN")|g" \
    -e "s|@NPM_BIN@|$NPM_BIN|g" \
    "$1" >"$2"
}

STAGING="$(mktemp -d /etc/qagent/.runit-stage.XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
for name in backend frontend; do
  install -d -m 0755 "$STAGING/qagent-$name"
  render "$APP_DIR/deploy/runit/$name.run.in" "$STAGING/qagent-$name/run"
  chmod 0755 "$STAGING/qagent-$name/run"
  touch "$STAGING/qagent-$name/down"
done
render "$APP_DIR/deploy/runit/qagent-backup.cron.in" "$STAGING/qagent-backup"
chmod 0644 "$STAGING/qagent-backup"
render "$APP_DIR/deploy/logrotate/qagent" "$STAGING/qagent-logrotate"
chmod 0644 "$STAGING/qagent-logrotate"

STAMP="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S%z)"
ARCHIVE="$ROLLBACK_DIR/$STAMP"
install -d -m 0700 "$ARCHIVE"
for name in qagent-backend qagent-frontend; do
  if [[ -d "$SV_DIR/$name" ]]; then
    mv "$SV_DIR/$name" "$ARCHIVE/$name"
  fi
  mv "$STAGING/$name" "$SV_DIR/$name"
done
if [[ -f /etc/cron.d/qagent-backup.disabled ]]; then
  mv /etc/cron.d/qagent-backup.disabled "$ARCHIVE/qagent-backup.disabled"
fi
if [[ -f /etc/logrotate.d/qagent ]]; then
  mv /etc/logrotate.d/qagent "$ARCHIVE/qagent-logrotate"
fi
mv "$STAGING/qagent-backup" /etc/cron.d/qagent-backup.disabled
mv "$STAGING/qagent-logrotate" /etc/logrotate.d/qagent
rmdir "$STAGING"
trap - EXIT

echo "installed runit definitions but did not enable or start Qagent"
echo "the backup cron remains disabled until scripts/enable_linux_runit.sh is run"
echo "previous definitions, if any, were archived at $ARCHIVE"
