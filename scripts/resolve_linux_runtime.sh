#!/usr/bin/env bash
# Source after QAGENT_HOME is set. Explicit overrides take precedence.
resolve_qagent_runtime() {
  local candidate
  PYTHON_BIN="${QAGENT_PYTHON_BIN:-$QAGENT_HOME/runtime/python3.11/bin/python3.11}"
  if [[ ! -x "$PYTHON_BIN" && -z "${QAGENT_PYTHON_BIN:-}" ]]; then
    PYTHON_BIN="$(command -v python3.11 || true)"
  fi
  [[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || { echo "Python 3.11 runtime is missing" >&2; return 1; }
  "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || return 1

  UV_BIN="${QAGENT_UV_BIN:-$QAGENT_HOME/runtime/uv/bin/uv}"
  if [[ ! -x "$UV_BIN" && -z "${QAGENT_UV_BIN:-}" ]]; then
    UV_BIN="$(command -v uv || true)"
  fi
  [[ -n "$UV_BIN" && -x "$UV_BIN" ]] || { echo "uv runtime is missing" >&2; return 1; }

  NODE_BIN="${QAGENT_NODE_BIN:-$QAGENT_HOME/runtime/node-v24/bin/node}"
  if [[ ! -x "$NODE_BIN" && -z "${QAGENT_NODE_BIN:-}" ]]; then
    NODE_BIN=/usr/bin/node
  fi
  [[ -x "$NODE_BIN" ]] && "$NODE_BIN" -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>20 || (a===20 && b>=19) ? 0 : 1)' || {
    echo "Node.js >=20.19 runtime is missing" >&2; return 1;
  }
  NPM_BIN="${QAGENT_NPM_BIN:-$(dirname "$NODE_BIN")/npm}"
  [[ -x "$NPM_BIN" ]] || { echo "npm runtime is missing: $NPM_BIN" >&2; return 1; }
  export PYTHON_BIN UV_BIN NODE_BIN NPM_BIN
}
