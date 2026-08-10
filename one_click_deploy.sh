#!/usr/bin/env bash
# 兼容旧入口：转调跨平台 install.py / install.sh
# 推荐直接使用:
#   python3 install.py
#   bash install.sh
#   Windows: install.bat
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"

pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        printf '%s' "$c"
        return 0
      fi
    fi
  done
  return 1
}

if [[ -n "${ROOT:-}" && -f "$ROOT/install.py" ]]; then
  if PY="$(pick_python)"; then
    exec "$PY" "$ROOT/install.py" "$@"
  fi
fi

if [[ -n "${ROOT:-}" && -f "$ROOT/install.sh" ]]; then
  exec bash "$ROOT/install.sh" "$@"
fi

echo "[one-click] ERROR: 缺少 install.py；请更新仓库或改用:" >&2
echo "  python3 <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py)" >&2
exit 1
