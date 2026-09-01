#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
ROLLBACK_DIR="/etc/qagent/deploy-rollback"
ARCHIVE="${1:-$(find "$ROLLBACK_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)}"
if [[ -z "$ARCHIVE" || ! -d "$ARCHIVE" ]]; then
  echo "no rollback archive found" >&2
  exit 1
fi
for name in qagent-backend qagent-frontend; do
  if [[ -e "/etc/service/$name" || -L "/etc/service/$name" ]]; then
    echo "disable Qagent before rollback" >&2
    exit 1
  fi
  [[ -d "$ARCHIVE/$name" ]] || { echo "archive lacks $name: $ARCHIVE" >&2; exit 1; }
done
STAMP="$(TZ=Asia/Shanghai date +%Y%m%dT%H%M%S%z)"
CURRENT="$ROLLBACK_DIR/pre-rollback-$STAMP"
mkdir -m 0700 "$CURRENT"
for name in qagent-backend qagent-frontend; do
  mv "/etc/sv/$name" "$CURRENT/$name"
  mv "$ARCHIVE/$name" "/etc/sv/$name"
done
echo "restored runit definitions from $ARCHIVE; services remain disabled"
echo "replaced definitions were preserved at $CURRENT"
