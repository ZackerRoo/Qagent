#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 || $# -ne 1 ]]; then
  echo "usage: sudo $0 /opt/qagent/releases/COMMIT" >&2
  exit 2
fi
RELEASE_DIR="$(realpath "$1")"
CURRENT_LINK="${QAGENT_CURRENT_LINK:-/opt/qagent/current}"
PREVIOUS_LINK="${QAGENT_PREVIOUS_LINK:-/opt/qagent/previous}"

if [[ ! -f "$RELEASE_DIR/backend/pyproject.toml" || ! -f "$RELEASE_DIR/frontend/package-lock.json" ]]; then
  echo "not a prepared Qagent release: $RELEASE_DIR" >&2
  exit 1
fi
for service in qagent-backend qagent-frontend; do
  if [[ -e "/etc/service/$service" || -L "/etc/service/$service" ]]; then
    echo "disable Qagent before switching releases" >&2
    exit 1
  fi
done

install -d -m 0755 "$(dirname "$CURRENT_LINK")"
if [[ -L "$CURRENT_LINK" ]]; then
  OLD_RELEASE="$(realpath "$CURRENT_LINK")"
  ln -s "$OLD_RELEASE" "$PREVIOUS_LINK.new"
  mv -Tf "$PREVIOUS_LINK.new" "$PREVIOUS_LINK"
elif [[ -e "$CURRENT_LINK" ]]; then
  echo "current path exists and is not a symlink: $CURRENT_LINK" >&2
  exit 1
fi
ln -s "$RELEASE_DIR" "$CURRENT_LINK.new"
mv -Tf "$CURRENT_LINK.new" "$CURRENT_LINK"
echo "current Qagent release is now $RELEASE_DIR; services remain disabled"
