#!/usr/bin/env bash
# One-click deploy for Linux / macOS
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[deploy] ERROR: python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

python3 scripts/deploy.py "$@"
