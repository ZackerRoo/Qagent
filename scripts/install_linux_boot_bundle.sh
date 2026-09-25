#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -d /home && "$(findmnt -n -o TARGET --target /home 2>/dev/null || true)" == /home ]] \
  || { echo "/home persistent mount is required" >&2; exit 1; }
[[ ! -L /home && "$(stat -c %u /home)" == 0 && \
   $((8#$(stat -c %a /home) & 8#022)) -eq 0 ]] \
  || { echo "unsafe /home ownership or mode" >&2; exit 1; }

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET=/home/qagent-boot
[[ ! -L "$TARGET" ]] || { echo "bootstrap target is a symlink" >&2; exit 1; }
STAGE="$(mktemp -d /home/.qagent-boot.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
install -m 0755 -o root -g root "$SOURCE/scripts/bootstrap_linux_persistent_home.sh" "$STAGE/bootstrap.sh"
install -m 0644 -o root -g root "$SOURCE/deploy/runit/backend.run.in" "$STAGE/backend.run.in"
install -m 0644 -o root -g root "$SOURCE/deploy/runit/frontend.run.in" "$STAGE/frontend.run.in"
install -m 0644 -o root -g root "$SOURCE/deploy/runit/qagent-backup.cron.in" "$STAGE/qagent-backup.cron.in"
install -m 0644 -o root -g root "$SOURCE/deploy/runit/qagent-financial-peer-control.cron.in" "$STAGE/qagent-financial-peer-control.cron.in"
install -m 0644 -o root -g root "$SOURCE/deploy/logrotate/qagent" "$STAGE/qagent.logrotate"
chmod 0755 "$STAGE"
bash -n "$STAGE/bootstrap.sh" "$STAGE/backend.run.in" "$STAGE/frontend.run.in"
if [[ -e "$TARGET" ]]; then
  [[ -d "$TARGET" && "$(stat -c %u "$TARGET")" == 0 && \
     $((8#$(stat -c %a "$TARGET") & 8#022)) -eq 0 ]] \
    || { echo "unsafe existing bootstrap target" >&2; exit 1; }
  ARCHIVE="/home/qagent-boot.previous.$(date +%s)"
  [[ ! -e "$ARCHIVE" ]] || { echo "bootstrap archive already exists" >&2; exit 1; }
  mv -T "$TARGET" "$ARCHIVE"
fi
mv -T "$STAGE" "$TARGET"
trap - EXIT
echo "installed root-owned persistent bootstrap at $TARGET; boot approval unchanged"
