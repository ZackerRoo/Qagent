#!/bin/bash
# Install only the independent research cron. Secret arrives on stdin, never argv.
set +x
set -euo pipefail
umask 077

fail() { printf '%s\n' 'relay research installation failed' >&2; exit 1; }
[[ $EUID -eq 0 && $# -eq 1 ]] || fail
bundle=$1
[[ $bundle == /opt/qagent-research/tushare-relay-20260913 ]] || fail
[[ $(date +%z) == +0000 ]] || fail
[[ -f /etc/timezone ]] || fail
[[ $(tr -d '\n' </etc/timezone) =~ ^(Etc/UTC|UTC)$ ]] || fail
service_user=luozhenkun
id "$service_user" >/dev/null 2>&1 || fail
getent group "$service_user" >/dev/null || fail
python=/opt/qagent/current/backend/.venv/bin/python
[[ -x $python && -f $bundle/scripts/archive_tushare_research.py
   && -f $bundle/scripts/collect_tushare_research.py
   && -d $bundle/backend/qagent/providers
   && -f $bundle/deploy/runit/qagent-tushare-research.cron.in ]] || fail
[[ -d /etc/qagent && ! -L /etc/qagent && -d /etc/cron.d ]] || fail
env_file=/etc/qagent/relay-research.env
cron_file=/etc/cron.d/qagent-relay-research
[[ ! -L $env_file && ! -L $cron_file ]] || fail
IFS= read -r relay_key || [[ -n ${relay_key:-} ]] || fail
[[ $relay_key =~ ^[A-Za-z0-9_-]+$ ]] || fail
env_tmp=$(mktemp /etc/qagent/.relay-research.env.XXXXXX)
cron_tmp=$(mktemp /etc/cron.d/.relay-research.XXXXXX)
trap 'rm -f -- "$env_tmp" "$cron_tmp"' EXIT
printf "QAGENT_TUSHARE_RELAY_KEY='%s'\n" "$relay_key" >"$env_tmp"
unset relay_key
# Refuse replacement of a different existing credential, including unknown content.
if [[ -e $env_file ]]; then
    cmp -s "$env_file" "$env_tmp" || fail
fi
sed -e "s|@SERVICE_USER@|$service_user|g" \
    -e "s|@ENV_FILE@|$env_file|g" \
    -e "s|@PYTHON@|$python|g" \
    -e "s|@APP_DIR@|$bundle|g" \
    -e 's|@RESEARCH_OUTPUT_DIR@|/var/lib/qagent-research/tushare-relay|g' \
    "$bundle/deploy/runit/qagent-tushare-research.cron.in" >"$cron_tmp"
if grep -q '@[A-Z_]*@' "$cron_tmp"; then fail; fi
output_dir=/var/lib/qagent-research/tushare-relay
[[ ! -L /var/lib/qagent-research && ! -L $output_dir ]] || fail
install -d -m 0750 -o "$service_user" -g "$service_user" "$output_dir"
chown root:"$service_user" "$env_tmp"
chmod 0640 "$env_tmp"
chown root:root "$cron_tmp"
chmod 0644 "$cron_tmp"
mv -f -- "$env_tmp" "$env_file"
mv -f -- "$cron_tmp" "$cron_file"
printf '%s\n' 'relay research cron installed (weekdays 08:30 UTC)'
