#!/usr/bin/env bash
# Arena Hero Tactic 真正一键部署（克隆 + 环境 + 启动）
# 用法:
#   curl -fsSL <本脚本 URL> | bash -s -- --api-key YOUR_KEY
#   或本地: bash one_click_deploy.sh --api-key YOUR_KEY
#   仅安装不启动: bash one_click_deploy.sh --api-key YOUR_KEY --no-start
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/feixingwawa/arena-hero-tactic.git}"
REPO_TAR="${REPO_TAR:-https://codeload.github.com/feixingwawa/arena-hero-tactic/tar.gz/refs/heads/main}"
INSTALL_DIR="${INSTALL_DIR:-$PWD/arena-hero-tactic}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_INDEX_URL
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"

info() { echo "[one-click] $*"; }
die()  { echo "[one-click] ERROR: $*" >&2; exit 1; }

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

ensure_python() {
  if PY="$(pick_python)"; then
    info "Python OK: $PY ($("$PY" -c 'import sys; print("%d.%d.%d"%sys.version_info[:3])'))"
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    info "尝试安装 python3.11..."
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -y
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev curl ca-certificates
    PY="$(pick_python)" || die "安装后仍无 Python 3.11+"
    info "Python OK: $PY"
    return 0
  fi
  die "需要 Python 3.11+，请先安装"
}

fetch_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "已存在 git 仓库: $INSTALL_DIR，尝试 pull"
    git -C "$INSTALL_DIR" pull --ff-only || true
    return 0
  fi
  if [[ -f "$INSTALL_DIR/scripts/deploy.py" ]]; then
    info "已存在项目目录: $INSTALL_DIR"
    return 0
  fi

  info "获取源码 → $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if command -v git >/dev/null 2>&1; then
    if GIT_HTTP_VERSION=1.1 git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"; then
      return 0
    fi
    info "git clone 失败，改用 tar 包"
  fi

  local tmp
  tmp="$(mktemp -d)"
  curl -fsSL --retry 3 --retry-delay 2 -o "$tmp/src.tgz" "$REPO_TAR"
  tar -xzf "$tmp/src.tgz" -C "$tmp"
  local extracted
  extracted="$(find "$tmp" -maxdepth 1 -type d -name 'arena-hero-tactic-*' | head -1)"
  [[ -n "$extracted" ]] || die "解压失败"
  rm -rf "$INSTALL_DIR"
  mv "$extracted" "$INSTALL_DIR"
  rm -rf "$tmp"
}

main() {
  ensure_python
  command -v curl >/dev/null 2>&1 || die "需要 curl"
  fetch_repo
  chmod +x "$INSTALL_DIR/deploy.sh" "$INSTALL_DIR/scripts/deploy.py" || true
  info "执行仓库内一键部署: $INSTALL_DIR/deploy.sh $*"
  exec "$INSTALL_DIR/deploy.sh" "$@"
}

main "$@"
