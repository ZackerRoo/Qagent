#!/usr/bin/env bash
set -euo pipefail

LABEL="com.qagent.cloud-tunnel"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Qagent"
SSH_KEY="${QAGENT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_HOST="${QAGENT_CLOUD_HOST:?set QAGENT_CLOUD_HOST to user@host}"
HOST_ALIAS="${QAGENT_CLOUD_HOST_KEY_ALIAS:-${REMOTE_HOST#*@}}"

for port in 5173 8000; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "local port $port is already in use; stop local Qagent services/tunnels first" >&2
    exit 1
  fi
done
if [[ ! -f "$SSH_KEY" ]]; then
  echo "missing SSH key: $SSH_KEY" >&2
  exit 1
fi

/usr/bin/ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
  -o PasswordAuthentication=no -o ConnectTimeout=10 -o "HostKeyAlias=$HOST_ALIAS" \
  "$REMOTE_HOST" true
mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

sed \
  -e "s|@SSH_KEY@|$SSH_KEY|g" \
  -e "s|@REMOTE_HOST@|$REMOTE_HOST|g" \
  -e "s|@HOST_ALIAS@|$HOST_ALIAS|g" \
  -e "s|@LOG_DIR@|$LOG_DIR|g" \
  "$(dirname "$0")/../deploy/macos/com.qagent.cloud-tunnel.plist.in" >"$PLIST_PATH"
plutil -lint "$PLIST_PATH"
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Qagent cloud tunnel installed: http://127.0.0.1:5173 and http://127.0.0.1:8000/api"
