#!/usr/bin/env bash
set -euo pipefail

LABEL="com.qagent.backend"

launchctl print "gui/$(id -u)/$LABEL"
echo
lsof -n -P -iTCP:8000 -sTCP:LISTEN || true
