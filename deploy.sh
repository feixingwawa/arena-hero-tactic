#!/usr/bin/env bash
# One-click deploy for Linux / macOS
# Prefer python3.11+ (project requires >=3.11)
set -euo pipefail
cd "$(dirname "$0")"

pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "[deploy] ERROR: 需要 Python 3.11+。当前系统未找到可用解释器。" >&2
  echo "[deploy] Ubuntu/Debian 示例: sudo apt-get install -y python3.11 python3.11-venv" >&2
  exit 1
fi

echo "[deploy] using interpreter: $PY ($("$PY" -c 'import sys; print("%d.%d.%d"%sys.version_info[:3])'))"
exec "$PY" scripts/deploy.py "$@"
