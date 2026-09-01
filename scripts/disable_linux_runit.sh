#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

DISABLE_TIMEOUT="${QAGENT_DISABLE_TIMEOUT:-20}"
if [[ ! "$DISABLE_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "QAGENT_DISABLE_TIMEOUT must be a positive integer" >&2
  exit 1
fi

declare -a ENABLED_SERVICES=()
declare -a SERVICE_PIDS=()
collect_process_tree() {
  local parent="$1"
  local child
  SERVICE_PIDS+=("$parent")
  while read -r child; do
    [[ -n "$child" ]] && collect_process_tree "$child"
  done < <(pgrep -P "$parent" 2>/dev/null || true)
}

for name in qagent-frontend qagent-backend; do
  touch "/etc/sv/$name/down"
done
if [[ -f /etc/cron.d/qagent-backup ]]; then
  mv /etc/cron.d/qagent-backup /etc/cron.d/qagent-backup.disabled
fi
rm -f /var/lib/qagent/.single-writer-approved

for name in qagent-frontend qagent-backend; do
  if [[ -e "/etc/service/$name" || -L "/etc/service/$name" ]]; then
    ENABLED_SERVICES+=("$name")
    pid="$(cat "/etc/service/$name/supervise/pid" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
      collect_process_tree "$pid"
    fi
  fi
done

for name in "${ENABLED_SERVICES[@]}"; do
  if ! sv -w "$DISABLE_TIMEOUT" down "/etc/service/$name"; then
    echo "failed to stop $name within ${DISABLE_TIMEOUT}s; service links were retained under runit with down files present" >&2
    exit 1
  fi
  if ! sv status "/etc/service/$name" | grep -q '^down:'; then
    echo "$name did not report a down state; service links were retained under runit with down files present" >&2
    exit 1
  fi
done

deadline=$((SECONDS + DISABLE_TIMEOUT))
while true; do
  lingering_pids=()
  for pid in "${SERVICE_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      lingering_pids+=("$pid")
    fi
  done
  busy_ports=()
  for port in 8000 5173; do
    if ss -H -ltn "sport = :$port" | grep -q .; then
      busy_ports+=("$port")
    fi
  done
  if (( ${#lingering_pids[@]} == 0 && ${#busy_ports[@]} == 0 )); then
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "Qagent shutdown did not quiesce within ${DISABLE_TIMEOUT}s; service links were retained under runit with down files present" >&2
    if (( ${#lingering_pids[@]} > 0 )); then
      echo "still-running supervised process IDs: ${lingering_pids[*]}" >&2
    fi
    if (( ${#busy_ports[@]} > 0 )); then
      echo "ports still listening: ${busy_ports[*]}" >&2
    fi
    exit 1
  fi
  sleep 0.2
done

for name in "${ENABLED_SERVICES[@]}"; do
  unlink "/etc/service/$name"
done
echo "disabled Qagent services and backup cron; database was not modified"
