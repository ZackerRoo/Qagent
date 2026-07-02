#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
exec .venv/bin/python -m uvicorn qagent.app:create_app --factory --host 127.0.0.1 --port 8000
