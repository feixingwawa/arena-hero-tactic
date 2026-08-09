#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键部署 / 启动 Arena Hero 战术 Agent。

功能：
  1. 检查 Python >= 3.11
  2. 创建/复用项目 .venv
  3. 安装 requirements.txt + Flask（Dashboard）+ psutil
  4. 若无 .env 则从 .env.example 复制，并校验 API Key
  5. 结束旧 bot.main 进程（避免双开抢 8765）
  6. 后台启动 bot.main -v --dashboard
  7. 探测 /health 与日志，打印结果

用法（仓库根目录）：
  python scripts/deploy.py
  python scripts/deploy.py --no-start          # 只装环境
  python scripts/deploy.py --foreground       # 前台跑（Ctrl+C 停）
  python scripts/deploy.py --port 8765
  python scripts/deploy.py --skip-pip         # 跳过 pip（已装好时）
  python scripts/deploy.py --api-key KEY      # 写入/覆盖 .env 中的 Key（不打印明文）

Windows 也可双击根目录 deploy.bat。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
LOGS = ROOT / "logs"
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQ = ROOT / "requirements.txt"
MIN_PY = (3, 11)


def _info(msg: str) -> None:
    print(f"[deploy] {msg}")


def _warn(msg: str) -> None:
    print(f"[deploy] WARN: {msg}")


def _die(msg: str, code: int = 1) -> None:
    print(f"[deploy] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _venv_pip() -> list[str]:
    return [str(_venv_python()), "-m", "pip"]


def check_python() -> None:
    v = sys.version_info
    _info(f"host python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    if (v.major, v.minor) < MIN_PY:
        _die(f"需要 Python >={MIN_PY[0]}.{MIN_PY[1]}，当前 {v.major}.{v.minor}")


def ensure_venv() -> Path:
    py = _venv_python()
    if py.exists():
        _info(f"复用 venv: {VENV_DIR}")
        return py
    _info(f"创建 venv: {VENV_DIR}")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=str(ROOT))
    if not py.exists():
        _die(f"venv 创建失败，未找到 {py}")
    return py


def pip_install(skip: bool) -> None:
    if skip:
        _info("跳过 pip install (--skip-pip)")
        return
    py = _venv_python()
    _info("升级 pip")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"],
        cwd=str(ROOT),
    )
    if not REQ.exists():
        _die(f"缺少 {REQ}")
    _info(f"安装 {REQ.name}")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "-r", str(REQ), "-q"],
        cwd=str(ROOT),
    )
    # Dashboard + 进程管理（restart/deploy 用）
    _info("安装 flask>=3.0 与 psutil（Dashboard / 进程清理）")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "flask>=3.0", "psutil", "-q"],
        cwd=str(ROOT),
    )
    # 版本自检
    code = (
        "import importlib.metadata as m; "
        "print('arena-hero', m.version('arena-hero')); "
        "import flask; print('flask', flask.__version__)"
    )
    out = subprocess.check_output([str(py), "-c", code], cwd=str(ROOT), text=True)
    for line in out.strip().splitlines():
        _info(f"package: {line}")


def ensure_env(api_key: str | None) -> None:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE.exists():
            _die("无 .env 且无 .env.example，无法初始化")
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        _info(f"已从 .env.example 创建 {ENV_PATH.name}")
    text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    if api_key:
        key = api_key.strip()
        if not key:
            _die("--api-key 为空")
        if re.search(r"(?m)^ARENA_HERO_API_KEY\s*=", text):
            text = re.sub(
                r"(?m)^ARENA_HERO_API_KEY\s*=.*$",
                f"ARENA_HERO_API_KEY={key}",
                text,
            )
        else:
            text = text.rstrip() + f"\nARENA_HERO_API_KEY={key}\n"
        ENV_PATH.write_text(text, encoding="utf-8")
        _info("已写入 ARENA_HERO_API_KEY 到 .env（明文不打印）")
        text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

    m = re.search(r"(?m)^ARENA_HERO_API_KEY\s*=\s*(.*)$", text)
    raw = (m.group(1).strip().strip('"').strip("'") if m else "")
    if not raw or raw in {"your_api_key_here", "你的_API_KEY", "changeme"}:
        _die(
            "请先配置 API Key：编辑 .env 设置 ARENA_HERO_API_KEY=... "
            "或运行 python scripts/deploy.py --api-key <KEY>"
        )
    masked = raw[:3] + "***" + raw[-4:] if len(raw) > 8 else "***"
    _info(f"API Key 已配置 ({masked})")


def kill_old_agents() -> None:
    try:
        import psutil  # type: ignore
    except ImportError:
        subprocess.check_call(
            _venv_pip() + ["install", "psutil", "-q"],
            cwd=str(ROOT),
        )
        import psutil  # type: ignore

    killed: list[int] = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = p.info.get("cmdline") or []
            joined = " ".join(str(x) for x in cl)
            if "bot.main" in joined or "-m bot.main" in joined:
                # 避免误杀当前 deploy 自己
                if "deploy.py" in joined:
                    continue
                p.kill()
                killed.append(int(p.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
    if killed:
        _info(f"已结束旧 agent 进程: {killed}")
        time.sleep(1.5)
    else:
        _info("无旧 bot.main 进程")


def start_agent(
    *,
    port: int,
    foreground: bool,
    verbose: bool,
) -> int | None:
    LOGS.mkdir(exist_ok=True)
    py = _venv_python()
    cmd = [
        str(py),
        "-u",
        "-m",
        "bot.main",
        "--dashboard",
        "--dashboard-port",
        str(port),
        "--log-file",
        "logs/agent.log",
    ]
    if verbose:
        cmd.insert(4, "-v")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    # 确保从项目根加载 .env
    os.chdir(ROOT)

    _info("启动命令: " + " ".join(cmd))
    if foreground:
        _info("前台模式：Ctrl+C 停止")
        # 继承 stdio
        raise SystemExit(subprocess.call(cmd, cwd=str(ROOT), env=env))

    out = open(LOGS / "agent.stdout", "w", encoding="utf-8")
    err = open(LOGS / "agent.stderr", "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    (LOGS / "agent.pid").write_text(str(proc.pid), encoding="ascii")
    _info(f"已后台启动 PID={proc.pid}（logs/agent.pid）")
    return proc.pid


def wait_health(port: int, timeout: float = 45.0) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    _info(f"等待 Dashboard {url} …")
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200 and ("ok" in body.lower() or "true" in body.lower()):
                    _info(f"health OK: {body[:200]}")
                    return True
                last_err = f"HTTP {resp.status}: {body[:120]}"
        except Exception as e:
            last_err = str(e)
        # 进程是否已退出
        pid_file = LOGS / "agent.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="ascii").strip())
                if sys.platform == "win32":
                    r = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}"],
                        capture_output=True,
                        text=True,
                    )
                    if str(pid) not in r.stdout:
                        _warn(f"进程 {pid} 已退出，见 logs/agent.stderr")
                        break
                else:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        _warn(f"进程 {pid} 已退出，见 logs/agent.stderr")
                        break
            except Exception:
                pass
        time.sleep(1.5)
    _warn(f"health 未就绪: {last_err}")
    return False


def tail_logs() -> None:
    for name in ("agent.stderr", "agent.stdout", "agent.log"):
        p = LOGS / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        lines = text.splitlines()
        _info(f"--- {name} tail ---")
        print("\n".join(lines[-20:]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Arena Hero tactic 一键部署")
    p.add_argument("--no-start", action="store_true", help="只安装环境，不启动 agent")
    p.add_argument("--foreground", action="store_true", help="前台运行 agent")
    p.add_argument("--skip-pip", action="store_true", help="跳过 pip 安装")
    p.add_argument("--port", type=int, default=8765, help="Dashboard 端口（默认 8765）")
    p.add_argument("--api-key", type=str, default=None, help="写入 .env 的 ARENA_HERO_API_KEY")
    p.add_argument("--quiet", action="store_true", help="启动时不加 -v")
    p.add_argument("--no-kill", action="store_true", help="不结束旧 bot.main")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)
    _info(f"ROOT={ROOT}")

    check_python()
    ensure_venv()
    pip_install(skip=args.skip_pip)
    ensure_env(args.api_key)

    if args.no_start:
        _info("环境就绪（--no-start）。启动示例：")
        print(f"  {_venv_python()} -m bot.main -v --dashboard --log-file logs/agent.log")
        return 0

    if not args.no_kill:
        kill_old_agents()

    start_agent(
        port=args.port,
        foreground=args.foreground,
        verbose=not args.quiet,
    )

    # 后台：等 health
    time.sleep(3)
    ok = wait_health(args.port, timeout=50.0)
    tail_logs()
    print()
    if ok:
        _info("部署成功")
        _info(f"Dashboard: http://127.0.0.1:{args.port}/")
        _info("日志: logs/agent.log  |  PID: logs/agent.pid")
        _info("停止: 结束 bot.main 进程，或再运行本脚本（会先 kill 旧进程）")
        return 0

    _warn("Agent 可能仍在连接游戏服务器；若日志持续无 tick，检查 API Key / 网络")
    _info(f"Dashboard（若已起）: http://127.0.0.1:{args.port}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
