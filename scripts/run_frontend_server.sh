#!/usr/bin/env zsh
set -euo pipefail

if [[ -f "$HOME/.zshrc" ]]; then
  source "$HOME/.zshrc" >/dev/null 2>&1 || true
fi

cd "$(dirname "$0")/../frontend"
exec npm run dev
