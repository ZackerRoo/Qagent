#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.qagent-runtime"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
BACKEND_RUNNER="$ROOT_DIR/scripts/run_backend_server.sh"
FRONTEND_RUNNER="$ROOT_DIR/scripts/run_frontend_server.sh"
BACKEND_SESSION="qagent-backend"
FRONTEND_SESSION="qagent-frontend"

mkdir -p "$RUNTIME_DIR"

has_session() {
  local session="$1"
  (screen -ls 2>/dev/null || true) | grep -Eq "[0-9]+[.]${session}[[:space:]]"
}

session_line() {
  local session="$1"
  (screen -ls 2>/dev/null || true) | grep -E "[0-9]+[.]${session}[[:space:]]" | head -1 | sed 's/^[[:space:]]*//'
}

start_one() {
  local name="$1"
  local session="$2"
  local runner="$3"
  local log="$4"
  local port="$5"
  if has_session "$session"; then
    echo "$name already running $(session_line "$session")"
    return
  fi
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$name port $port is already in use"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN
    return 1
  fi
  : >"$log"
  screen -dmS "$session" /bin/bash -lc "cd '$ROOT_DIR' && exec '$runner' >> '$log' 2>&1"
  local ready="false"
  for _ in {1..20}; do
    if has_session "$session" && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      ready="true"
      break
    fi
    sleep 0.5
  done
  if [[ "$ready" != "true" ]]; then
    echo "$name failed to start; see $log"
    return 1
  fi
  echo "$name started $(session_line "$session") log=$log"
}

stop_one() {
  local name="$1"
  local session="$2"
  if has_session "$session"; then
    screen -S "$session" -X quit
    echo "$name stopped"
  else
    echo "$name not running"
  fi
}

status_one() {
  local name="$1"
  local session="$2"
  local port="$3"
  if has_session "$session"; then
    echo "$name running $(session_line "$session") port=$port"
  else
    echo "$name stopped port=$port"
  fi
}

case "${1:-start}" in
  start)
    start_one backend "$BACKEND_SESSION" "$BACKEND_RUNNER" "$BACKEND_LOG" 8000
    start_one frontend "$FRONTEND_SESSION" "$FRONTEND_RUNNER" "$FRONTEND_LOG" 5173
    ;;
  stop)
    stop_one frontend "$FRONTEND_SESSION"
    stop_one backend "$BACKEND_SESSION"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    status_one backend "$BACKEND_SESSION" 8000
    status_one frontend "$FRONTEND_SESSION" 5173
    ;;
  logs)
    echo "backend log: $BACKEND_LOG"
    tail -n 40 "$BACKEND_LOG" 2>/dev/null || true
    echo
    echo "frontend log: $FRONTEND_LOG"
    tail -n 40 "$FRONTEND_LOG" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs}" >&2
    exit 2
    ;;
esac
