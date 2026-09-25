#!/usr/bin/env bash
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin

[[ "$(id -u)" == 0 && "${1:-}" == --confirm-restore-after-restart ]] || {
  echo "usage: sudo $0 --confirm-restore-after-restart" >&2; exit 2;
}
QAGENT_HOME="${QAGENT_HOME:-/home/${QAGENT_SERVICE_USER:-luozhenkun}/qagent}"
SERVICE_USER="${QAGENT_SERVICE_USER:-luozhenkun}"
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] \
  || { echo "unsafe service user" >&2; exit 1; }
[[ "$QAGENT_HOME" =~ ^/home/[a-zA-Z0-9_./-]+$ && "$QAGENT_HOME" != *..* ]] \
  || { echo "unsafe QAGENT_HOME" >&2; exit 1; }
[[ -d /home && "$(findmnt -n -o TARGET --target /home 2>/dev/null || true)" == /home ]] \
  || { echo "/home persistent mount is required" >&2; exit 1; }
[[ ! -L /home && "$(stat -c %u /home)" == 0 && \
   $((8#$(stat -c %a /home) & 8#022)) -eq 0 ]] \
  || { echo "unsafe /home ownership or mode" >&2; exit 1; }
for asset in /home/qagent-boot /home/qagent-boot/bootstrap.sh \
  /home/qagent-boot/backend.run.in /home/qagent-boot/frontend.run.in \
  /home/qagent-boot/qagent-backup.cron.in \
  /home/qagent-boot/qagent-financial-peer-control.cron.in \
  /home/qagent-boot/qagent.logrotate; do
  [[ ! -L "$asset" && "$(stat -c %u "$asset")" == 0 && \
     $((8#$(stat -c %a "$asset") & 8#022)) -eq 0 ]] \
    || { echo "unsafe boot asset: $asset" >&2; exit 1; }
done
backend_template=/home/qagent-boot/backend.run.in
chpst_line="$(grep -n '^exec chpst -u @SERVICE_USER@:@SERVICE_USER@' "$backend_template" | cut -d: -f1)"
source_line="$(grep -n '^[[:space:]]*source @ENV_FILE@$' "$backend_template" | cut -d: -f1)"
[[ "$chpst_line" =~ ^[0-9]+$ && "$source_line" =~ ^[0-9]+$ && \
   "$chpst_line" -lt "$source_line" ]] \
  || { echo "backend template must source env after chpst" >&2; exit 1; }
ENV_FILE="$QAGENT_HOME/config/qagent.env"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && \
   "$(stat -c %u "$ENV_FILE")" == 0 && \
   "$(stat -c %a "$ENV_FILE")" == 640 && \
   "$(stat -c %g "$ENV_FILE")" == "$(id -g "$SERVICE_USER")" ]] \
  || { echo "persistent env must be root-owned mode 0640 with service group" >&2; exit 1; }
runuser -u "$SERVICE_USER" -- test -r "$ENV_FILE" \
  || { echo "service user cannot read persistent env" >&2; exit 1; }
[[ -f "$QAGENT_HOME/state/.single-writer-approved" && \
   ! -L "$QAGENT_HOME/state/.single-writer-approved" && \
   -L /etc/service/qagent-backend && -L /etc/service/qagent-frontend ]] \
  || { echo "existing single-writer approval and enabled services are required" >&2; exit 1; }
[[ "$(readlink -f /etc/service/qagent-backend)" == /etc/sv/qagent-backend && \
   "$(readlink -f /etc/service/qagent-frontend)" == /etc/sv/qagent-frontend ]] \
  || { echo "unexpected service links" >&2; exit 1; }
[[ ! -L /home/qagent-boot-approved ]] || { echo "unsafe approval symlink" >&2; exit 1; }
STAGE="$(mktemp /home/.qagent-boot-approved.XXXXXX)"
trap 'rm -f "$STAGE"' EXIT
printf '%s\n' "$QAGENT_HOME" >"$STAGE"
chown root:root "$STAGE"
chmod 0600 "$STAGE"
mv -f "$STAGE" /home/qagent-boot-approved
trap - EXIT
echo "approved restoration of the existing single writer after image restart"
