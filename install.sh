#!/usr/bin/env bash
# =============================================================================
# Arena Hero Tactic — 一行命令全自动部署
#
# 复制下面这一行到终端即可（会提示输入 API Key，然后自动部署并运行）：
#
#   bash /home/install-arena.sh
#
# 或（若已推送到 GitHub raw）：
#   bash <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.sh)
#
# 也可非交互：
#   bash /home/install-arena.sh --api-key '你的KEY'
#   ARENA_HERO_API_KEY='你的KEY' bash /home/install-arena.sh
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/feixingwawa/arena-hero-tactic.git}"
REPO_TAR="${REPO_TAR:-https://codeload.github.com/feixingwawa/arena-hero-tactic/tar.gz/refs/heads/main}"
INSTALL_DIR="${INSTALL_DIR:-$PWD/arena-hero-tactic}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-120}"

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; CYN=$'\033[36m'; RST=$'\033[0m'
info() { printf '%s[install]%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s[install]%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s[install]%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s[install] ERROR:%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

is_placeholder() {
  case "${1:-}" in
    ""|your_api_key_here|你的_API_KEY|changeme|REPLACE_WITH_REAL_KEY) return 0 ;;
    *) return 1 ;;
  esac
}

banner() {
  cat <<'EOF'
╔══════════════════════════════════════════════════════════╗
║     Arena Hero Tactic  —  一键全自动部署 + 运行          ║
║     资源优先 + 均衡防守战术客户端                         ║
╚══════════════════════════════════════════════════════════╝
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

pick_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        printf '%s' "$c"
        return 0
      fi
    fi
  done
  return 1
}

ensure_python() {
  if PY="$(pick_python)"; then
    ok "Python: $PY ($("$PY" -c 'import sys; print("%d.%d.%d"%sys.version_info[:3])'))"
    return 0
  fi
  warn "未找到 Python 3.11+，尝试自动安装…"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    if command -v sudo >/dev/null 2>&1 && [[ "$(id -u)" -ne 0 ]]; then
      sudo apt-get update -y
      sudo apt-get install -y python3.11 python3.11-venv python3.11-dev curl ca-certificates
    else
      apt-get update -y
      apt-get install -y python3.11 python3.11-venv python3.11-dev curl ca-certificates
    fi
    PY="$(pick_python)" || die "安装后仍无 Python 3.11+"
    ok "Python: $PY"
    return 0
  fi
  die "需要 Python >= 3.11，请先手动安装"
}

fetch_repo() {
  local self_dir
  self_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [[ -n "$self_dir" && -f "$self_dir/scripts/deploy.py" && -f "$self_dir/bot/main.py" ]]; then
    INSTALL_DIR="$self_dir"
    ok "使用本地仓库: $INSTALL_DIR"
    return 0
  fi

  if [[ -f "$INSTALL_DIR/scripts/deploy.py" && -f "$INSTALL_DIR/bot/main.py" ]]; then
    ok "复用已有目录: $INSTALL_DIR"
    if [[ -d "$INSTALL_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
      info "尝试 git pull …"
      git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
    fi
    return 0
  fi

  info "下载源码 → $INSTALL_DIR"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  local tmp
  tmp="$(mktemp -d)"

  if command -v git >/dev/null 2>&1; then
    if GIT_HTTP_VERSION=1.1 git clone --depth 1 "$REPO_URL" "$tmp/repo" 2>/dev/null; then
      rm -rf "$INSTALL_DIR"
      mv "$tmp/repo" "$INSTALL_DIR"
      rm -rf "$tmp"
      ok "git clone 完成"
      return 0
    fi
    warn "git clone 失败，改用 tar 包"
  fi

  need_cmd curl
  curl -fsSL --retry 3 --retry-delay 2 -o "$tmp/src.tgz" "$REPO_TAR" \
    || { rm -rf "$tmp"; die "下载源码失败: $REPO_TAR"; }
  tar -xzf "$tmp/src.tgz" -C "$tmp"
  local extracted
  extracted="$(find "$tmp" -maxdepth 1 -type d -name 'arena-hero-tactic-*' | head -1)"
  [[ -n "$extracted" ]] || { rm -rf "$tmp"; die "解压失败"; }
  rm -rf "$INSTALL_DIR"
  mv "$extracted" "$INSTALL_DIR"
  rm -rf "$tmp"
  ok "源码就绪"
}

read_env_key() {
  local f="$INSTALL_DIR/.env"
  [[ -f "$f" ]] || { printf ''; return 0; }
  grep -E '^ARENA_HERO_API_KEY=' "$f" 2>/dev/null | head -1 \
    | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs || true
}

# 从指定 fd / 设备读一行密钥；成功返回 0 并写入 REPLY_KEY
_read_secret_line() {
  # $1: 模式 stdin | tty
  local mode="${1:-stdin}"
  REPLY_KEY=""
  if [[ "$mode" == "tty" ]]; then
    # 先探测能否真正打开，避免 -r 通过但 read 失败后死循环
    if ! { : < /dev/tty; } 2>/dev/null; then
      return 1
    fi
    # 提示写到 stderr，避免与密钥输入混在管道里
    printf 'API Key: ' >&2
    # 不使用 -p（重定向后提示可能丢）；从 /dev/tty 静默读
    IFS= read -r -s REPLY_KEY < /dev/tty || return 1
    printf '\n' >&2
  else
    printf 'API Key: ' >&2
    IFS= read -r -s REPLY_KEY || return 1
    printf '\n' >&2
  fi
  REPLY_KEY="$(printf '%s' "$REPLY_KEY" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  return 0
}

prompt_api_key() {
  FINAL_KEY=""

  if [[ -n "${API_KEY_ARG:-}" ]] && ! is_placeholder "$API_KEY_ARG"; then
    FINAL_KEY="$API_KEY_ARG"
    info "使用命令行 --api-key"
    return 0
  fi

  if [[ -n "${ARENA_HERO_API_KEY:-}" ]] && ! is_placeholder "$ARENA_HERO_API_KEY"; then
    FINAL_KEY="$ARENA_HERO_API_KEY"
    info "使用环境变量 ARENA_HERO_API_KEY"
    return 0
  fi

  local existing
  existing="$(read_env_key)"
  if ! is_placeholder "$existing"; then
    FINAL_KEY="$existing"
    info "检测到 .env 中已有 API Key，将直接使用"
    return 0
  fi

  # 交互输入策略：
  # 1) stdin 是 TTY（真实终端 / PTY）→ 直接读 stdin（最可靠）
  # 2) 否则尝试 /dev/tty（stdin 被管道占用时仍能交互）
  # 3) 都不可用 → 明确报错，避免空转
  local read_mode=""
  if [[ -t 0 ]]; then
    read_mode="stdin"
  elif { : < /dev/tty; } 2>/dev/null; then
    read_mode="tty"
  else
    die "无法交互输入 API Key（无 TTY）。请改用:
  bash install.sh --api-key YOUR_KEY
  或: ARENA_HERO_API_KEY=YOUR_KEY bash install.sh"
  fi

  echo
  echo "────────────────────────────────────────"
  echo "  请输入 Arena Hero API Key"
  echo "  获取: https://doc.arenahero.io/"
  echo "  （输入时不回显，回车确认）"
  echo "────────────────────────────────────────"

  local key="" attempts=0 max_attempts=5
  while is_placeholder "$key"; do
    attempts=$((attempts + 1))
    if [[ "$attempts" -gt "$max_attempts" ]]; then
      die "连续 ${max_attempts} 次未收到有效 API Key，已退出。可用 --api-key 传入。"
    fi
    REPLY_KEY=""
    if ! _read_secret_line "$read_mode"; then
      # stdin 失败时再试一次 /dev/tty；tty 失败则切 stdin（若可用）
      if [[ "$read_mode" == "stdin" ]] && { : < /dev/tty; } 2>/dev/null; then
        warn "stdin 读取失败，改从 /dev/tty 读取…"
        read_mode="tty"
        continue
      elif [[ "$read_mode" == "tty" && -t 0 ]]; then
        warn "/dev/tty 读取失败，改从 stdin 读取…"
        read_mode="stdin"
        continue
      fi
      die "读取 API Key 失败（无可用终端）。请使用: bash install.sh --api-key YOUR_KEY"
    fi
    key="$REPLY_KEY"
    if is_placeholder "$key"; then
      warn "Key 不能为空或占位符，请重新输入（剩余 $((max_attempts - attempts)) 次）"
    fi
  done
  FINAL_KEY="$key"
  ok "已接收 API Key（不会打印明文）"
}

run_deploy() {
  chmod +x "$INSTALL_DIR/deploy.sh" "$INSTALL_DIR/scripts/deploy.py" 2>/dev/null || true
  # 若上游仓库还没有交互式 ensure_env，把我们增强版 deploy.py 保留在本地即可
  local py
  py="$(pick_python)" || die "无 Python 3.11+"

  [[ -n "${FINAL_KEY:-}" ]] || die "内部错误：FINAL_KEY 为空"
  export ARENA_HERO_API_KEY="$FINAL_KEY"

  info "开始安装依赖并启动（Dashboard 默认 :8765）…"
  cd "$INSTALL_DIR"
  # 始终显式传 --api-key，避免 .env 占位符导致失败
  exec "$py" scripts/deploy.py --api-key "$FINAL_KEY" "$@"
}

# ---------- main ----------
API_KEY_ARG=""
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-key)
      API_KEY_ARG="${2:-}"
      shift 2 || die "--api-key 需要参数"
      ;;
    --api-key=*)
      API_KEY_ARG="${1#*=}"
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      PASS_ARGS+=("$1")
      shift
      ;;
  esac
done

banner
need_cmd curl
ensure_python
fetch_repo
prompt_api_key
run_deploy ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
