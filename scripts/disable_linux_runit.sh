#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
for name in qagent-frontend qagent-backend; do
  touch "/etc/sv/$name/down"
  if [[ -e "/etc/service/$name" || -L "/etc/service/$name" ]]; then
    sv down "/etc/service/$name" 2>/dev/null || true
    unlink "/etc/service/$name"
  fi
done
if [[ -f /etc/cron.d/qagent-backup ]]; then
  mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled
fi
rm -f /var/lib/qagent/.single-writer-approved
echo "disabled Qagent services and backup cron; database was not modified"
