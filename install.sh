#!/usr/bin/env bash
# =============================================================================
# Arena Hero Tactic — 一键部署（Linux / macOS / 可在 Git Bash 下使用）
#
# 跨平台主入口是 install.py（Windows / macOS / Linux 通用）。
# 本脚本优先调用 install.py；若仅有旧环境则保留 bash 回退逻辑的最小包装。
#
# 推荐用法：
#   # 已克隆仓库
#   python3 install.py
#   # 或
#   bash install.sh
#
#   # 远程一行（Linux / macOS）
#   python3 <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py)
#   # 或
#   bash <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.sh)
#
#   # 非交互
#   python3 install.py --api-key '你的KEY'
#   ARENA_HERO_API_KEY='你的KEY' bash install.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"

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

# ---------- 优先：跨平台 install.py ----------
if [[ -n "${ROOT:-}" && -f "$ROOT/install.py" ]]; then
  if PY="$(pick_python)"; then
    exec "$PY" "$ROOT/install.py" "$@"
  fi
fi

# 远程通过 bash <(curl ...) 管道执行时，可能没有本地 install.py：
# 尝试下载 install.py 到临时文件再跑（仍用 Python，保证与 Windows 同一套逻辑）
if PY="$(pick_python)"; then
  REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py}"
  TMP_PY="$(mktemp "${TMPDIR:-/tmp}/arena-install.XXXXXX.py")"
  cleanup() { rm -f "$TMP_PY"; }
  trap cleanup EXIT
  if command -v curl >/dev/null 2>&1; then
    if curl -fsSL --retry 3 --retry-delay 2 -o "$TMP_PY" "$REPO_RAW"; then
      exec "$PY" "$TMP_PY" "$@"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -q -O "$TMP_PY" "$REPO_RAW"; then
      exec "$PY" "$TMP_PY" "$@"
    fi
  fi
fi

echo "[install] ERROR: 需要 Python >= 3.11 以运行跨平台 install.py" >&2
echo "[install] Ubuntu/Debian: sudo apt-get install -y python3.11 python3.11-venv curl" >&2
echo "[install] macOS: brew install python@3.12" >&2
echo "[install] 或直接: python3 install.py --api-key YOUR_KEY" >&2
exit 1
