#!/usr/bin/env bash
set -euo pipefail

LABEL="com.qagent.backend-tunnel"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Qagent"
SSH_KEY="${QAGENT_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_HOST="${QAGENT_REMOTE_HOST:-zhenkunluo@100.85.40.49}"
REMOTE_BACKEND_HOST="${QAGENT_REMOTE_BACKEND_HOST:-100.85.40.49}"
LOCAL_PORT="${QAGENT_LOCAL_BACKEND_PORT:-8000}"
REMOTE_PORT="${QAGENT_REMOTE_BACKEND_PORT:-8000}"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "Missing SSH key: $SSH_KEY" >&2
  exit 1
fi

if lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Local port $LOCAL_PORT is already in use. Stop the local backend before installing the tunnel." >&2
  exit 1
fi

/usr/bin/ssh \
  -i "$SSH_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o PasswordAuthentication=no \
  -o ConnectTimeout=10 \
  "$REMOTE_HOST" true

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string>
    <string>-N</string>
    <string>-i</string>
    <string>$SSH_KEY</string>
    <string>-o</string>
    <string>IdentitiesOnly=yes</string>
    <string>-o</string>
    <string>BatchMode=yes</string>
    <string>-o</string>
    <string>PasswordAuthentication=no</string>
    <string>-o</string>
    <string>ExitOnForwardFailure=yes</string>
    <string>-o</string>
    <string>ServerAliveInterval=30</string>
    <string>-o</string>
    <string>ServerAliveCountMax=3</string>
    <string>-o</string>
    <string>TCPKeepAlive=yes</string>
    <string>-L</string>
    <string>127.0.0.1:$LOCAL_PORT:$REMOTE_BACKEND_HOST:$REMOTE_PORT</string>
    <string>$REMOTE_HOST</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/qagent-backend-tunnel.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/qagent-backend-tunnel.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST_PATH"
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Installed $LABEL"
echo "Local API: http://127.0.0.1:$LOCAL_PORT/api"
echo "Remote API: http://$REMOTE_BACKEND_HOST:$REMOTE_PORT/api"
launchctl print "gui/$(id -u)/$LABEL"
