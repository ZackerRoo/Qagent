#!/usr/bin/env bash
set -euo pipefail

LABEL="com.qagent.cloud-tunnel"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
unlink "$PLIST_PATH" 2>/dev/null || true
echo "uninstalled $LABEL"
