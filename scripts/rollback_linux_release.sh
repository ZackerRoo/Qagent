#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
CURRENT_LINK="${QAGENT_CURRENT_LINK:-/opt/qagent/current}"
PREVIOUS_LINK="${QAGENT_PREVIOUS_LINK:-/opt/qagent/previous}"
for service in qagent-backend qagent-frontend; do
  if [[ -e "/etc/service/$service" || -L "/etc/service/$service" ]]; then
    echo "disable Qagent before rolling back a release" >&2
    exit 1
  fi
done
if [[ ! -L "$CURRENT_LINK" || ! -L "$PREVIOUS_LINK" ]]; then
  echo "both current and previous release symlinks are required" >&2
  exit 1
fi
CURRENT_TARGET="$(realpath "$CURRENT_LINK")"
PREVIOUS_TARGET="$(realpath "$PREVIOUS_LINK")"
ln -s "$PREVIOUS_TARGET" "$CURRENT_LINK.new"
mv -Tf "$CURRENT_LINK.new" "$CURRENT_LINK"
ln -s "$CURRENT_TARGET" "$PREVIOUS_LINK.new"
mv -Tf "$PREVIOUS_LINK.new" "$PREVIOUS_LINK"
echo "rolled back current release to $PREVIOUS_TARGET; services remain disabled"
