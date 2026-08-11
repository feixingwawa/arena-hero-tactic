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
  8. 后台拉起 agent_watchdog（tick 停滞 / 僵死进程自动杀启）

用法（仓库根目录）：
  python scripts/deploy.py
  python scripts/deploy.py --no-start          # 只装环境
  python scripts/deploy.py --foreground       # 前台跑（Ctrl+C 停）
  python scripts/deploy.py --port 8765
  python scripts/deploy.py --skip-pip         # 跳过 pip（已装好时）
  python scripts/deploy.py --api-key KEY      # 写入/覆盖 .env 中的 Key（不打印明文）
  python scripts/deploy.py --no-watchdog      # 不拉起外部看门狗

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
# 国内网络更稳的默认源；可用环境变量 PIP_INDEX_URL 覆盖
DEFAULT_PIP_INDEX = os.environ.get(
    "PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple"
)
PIP_TIMEOUT = os.environ.get("PIP_DEFAULT_TIMEOUT", "120")


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


def _pip_base(py: Path) -> list[str]:
    """构造带镜像与超时的 pip 命令，缓解 files.pythonhosted.org 超时。"""
    return [
        str(py),
        "-m",
        "pip",
        "install",
        "-i",
        DEFAULT_PIP_INDEX,
        "--default-timeout",
        str(PIP_TIMEOUT),
    ]


def pip_install(skip: bool) -> None:
    if skip:
        _info("跳过 pip install (--skip-pip)")
        return
    py = _venv_python()
    _info(f"使用 pip 源: {DEFAULT_PIP_INDEX} (timeout={PIP_TIMEOUT}s)")
    _info("升级 pip")
    subprocess.check_call(
        _pip_base(py) + ["--upgrade", "pip", "-q"],
        cwd=str(ROOT),
    )
    if not REQ.exists():
        _die(f"缺少 {REQ}")
    _info(f"安装 {REQ.name}")
    subprocess.check_call(
        _pip_base(py) + ["-r", str(REQ), "-q"],
        cwd=str(ROOT),
    )
    # Dashboard + 进程管理（restart/deploy 用）
    _info("安装 flask>=3.0 与 psutil（Dashboard / 进程清理）")
    subprocess.check_call(
        _pip_base(py) + ["flask>=3.0", "psutil", "-q"],
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


_PLACEHOLDER_KEYS = {
    "",
    "your_api_key_here",
    "你的_API_KEY",
    "changeme",
    "REPLACE_WITH_REAL_KEY",
}


def _read_env_key(text: str) -> str:
    m = re.search(r"(?m)^ARENA_HERO_API_KEY\s*=\s*(.*)$", text)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _write_env_key(text: str, key: str) -> str:
    if re.search(r"(?m)^ARENA_HERO_API_KEY\s*=", text):
        text = re.sub(
            r"(?m)^ARENA_HERO_API_KEY\s*=.*$",
            f"ARENA_HERO_API_KEY={key}",
            text,
        )
    else:
        text = text.rstrip() + f"\nARENA_HERO_API_KEY={key}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    return text


def _prompt_api_key() -> str:
    """交互输入 API Key（优先 getpass，失败则回退 input）。"""
    print()
    print("=" * 56)
    print("  需要 Arena Hero API Key 才能连接游戏服务器")
    print("  获取地址: https://doc.arenahero.io/")
    print("=" * 56)
    env_key = os.environ.get("ARENA_HERO_API_KEY", "").strip()
    if env_key and env_key not in _PLACEHOLDER_KEYS:
        _info("检测到环境变量 ARENA_HERO_API_KEY，将使用它")
        return env_key

    if not sys.stdin.isatty():
        _die(
            "当前非交互终端且未提供 API Key。\n"
            "请使用: ./deploy.sh --api-key <KEY>\n"
            "或: ARENA_HERO_API_KEY=<KEY> ./deploy.sh\n"
            "或在交互式终端直接运行以手动输入。"
        )

    try:
        import getpass

        key = getpass.getpass("请输入 ARENA_HERO_API_KEY（输入时不回显）: ").strip()
    except Exception:
        key = input("请输入 ARENA_HERO_API_KEY: ").strip()

    if not key or key in _PLACEHOLDER_KEYS:
        _die("API Key 不能为空或占位符，请重新运行并输入真实 Key")
    return key


def ensure_env(api_key: str | None) -> None:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE.exists():
            _die("无 .env 且无 .env.example，无法初始化")
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        _info(f"已从 .env.example 创建 {ENV_PATH.name}")
    text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

    if api_key:
        key = api_key.strip()
        if not key or key in _PLACEHOLDER_KEYS:
            _die("--api-key 为空或仍是占位符")
        text = _write_env_key(text, key)
        _info("已写入 ARENA_HERO_API_KEY 到 .env（明文不打印）")
    else:
        raw = _read_env_key(text)
        if not raw or raw in _PLACEHOLDER_KEYS:
            key = _prompt_api_key()
            text = _write_env_key(text, key)
            _info("已写入 ARENA_HERO_API_KEY 到 .env（明文不打印）")

    raw = _read_env_key(ENV_PATH.read_text(encoding="utf-8", errors="replace"))
    if not raw or raw in _PLACEHOLDER_KEYS:
        _die("API Key 仍未配置成功")
    masked = raw[:3] + "***" + raw[-4:] if len(raw) > 8 else "***"
    _info(f"API Key 已配置 ({masked})")


def kill_old_agents() -> None:
    """结束旧 bot.main。在 venv 内用 psutil；失败则 pkill。"""
    py = _venv_python()
    killed: list[int] = []
    code = (
        "import os, sys\n"
        "try:\n"
        "    import psutil\n"
        "except ImportError:\n"
        "    sys.exit(2)\n"
        "me = os.getpid()\n"
        "killed = []\n"
        "for p in psutil.process_iter(['pid', 'cmdline']):\n"
        "    try:\n"
        "        cl = p.info.get('cmdline') or []\n"
        "        joined = ' '.join(str(x) for x in cl)\n"
        "        if ('bot.main' in joined or '-m bot.main' in joined) and 'deploy.py' not in joined:\n"
        "            pid = int(p.info['pid'])\n"
        "            if pid != me:\n"
        "                p.kill()\n"
        "                killed.append(pid)\n"
        "    except Exception:\n"
        "        pass\n"
        "print(','.join(str(x) for x in killed))\n"
    )
    try:
        out = subprocess.check_output(
            [str(py), "-c", code],
            cwd=str(ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        killed = [int(x) for x in out.strip().split(",") if x.strip().isdigit()]
    except subprocess.CalledProcessError:
        try:
            subprocess.check_call(_pip_base(py) + ["psutil", "-q"], cwd=str(ROOT))
            out = subprocess.check_output([str(py), "-c", code], cwd=str(ROOT), text=True)
            killed = [int(x) for x in out.strip().split(",") if x.strip().isdigit()]
        except Exception:
            killed = []
    except Exception:
        killed = []

    if not killed:
        try:
            subprocess.run(
                ["pkill", "-f", r"python.*-m bot\.main"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _info("已尝试 pkill 旧 bot.main")
            time.sleep(1.0)
            return
        except Exception:
            pass

    if killed:
        _info(f"已结束旧 agent 进程: {killed}")
        time.sleep(1.5)
    else:
        _info("无旧 bot.main 进程")


def start_agent(
    *,
    port: int,
    host: str = "0.0.0.0",
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
        "--dashboard-host",
        host,
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
    popen_kw: dict = {
        "cwd": str(ROOT),
        "env": env,
        "stdout": out,
        "stderr": err,
    }
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)
    (LOGS / "agent.pid").write_text(str(proc.pid), encoding="ascii")
    _info(f"已后台启动 PID={proc.pid}（logs/agent.pid）")
    return proc.pid


def start_watchdog(*, stall_sec: float = 90.0, poll_sec: float = 10.0) -> int | None:
    """后台拉起 scripts/agent_watchdog.py（单实例；tick 停滞则 ensure_single 重启）。

    根因兜底：进程内 session_watchdog 硬退出 / WS 假活后，必须有进程外监护。
    """
    LOGS.mkdir(exist_ok=True)
    py = _venv_python()
    script = ROOT / "scripts" / "agent_watchdog.py"
    if not script.exists():
        _warn(f"未找到 {script}，跳过外部看门狗")
        return None

    # 结束旧 watchdog（避免多实例；agent_watchdog 自身也有 singleton）
    try:
        import psutil

        me = os.getpid()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cl = p.info.get("cmdline") or []
                low = " ".join(str(x).lower() for x in cl)
                if "agent_watchdog.py" not in low or "python" not in low:
                    continue
                pid = int(p.info["pid"])
                if pid == me:
                    continue
                p.kill()
                _info(f"已结束旧 agent_watchdog pid={pid}")
            except Exception:
                continue
        time.sleep(0.5)
    except ImportError:
        try:
            subprocess.run(
                ["pkill", "-f", r"agent_watchdog\.py"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    cmd = [
        str(py),
        "-u",
        str(script),
        "--stall-sec",
        str(stall_sec),
        "--poll-sec",
        str(poll_sec),
    ]
    out = open(LOGS / "watchdog.stdout", "a", encoding="utf-8")
    err = open(LOGS / "watchdog.stderr", "a", encoding="utf-8")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    out.write(f"\n----- deploy start_watchdog {stamp} -----\n")
    err.write(f"\n----- deploy start_watchdog {stamp} -----\n")
    out.flush()
    err.flush()

    popen_kw: dict = {
        "cwd": str(ROOT),
        "env": env,
        "stdout": out,
        "stderr": err,
    }
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kw)
    (LOGS / "watchdog.pid").write_text(str(proc.pid), encoding="ascii")
    _info(
        f"已后台启动 agent_watchdog PID={proc.pid} "
        f"(stall={stall_sec:.0f}s poll={poll_sec:.0f}s → logs/watchdog.log)"
    )
    return proc.pid


def wait_health(port: int, timeout: float = 20.0) -> bool:
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
    p.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Dashboard 监听地址（默认 0.0.0.0 对公网开放；仅本机用 127.0.0.1）",
    )
    p.add_argument("--api-key", type=str, default=None, help="写入 .env 的 ARENA_HERO_API_KEY")
    p.add_argument("--quiet", action="store_true", help="启动时不加 -v")
    p.add_argument("--no-kill", action="store_true", help="不结束旧 bot.main")
    p.add_argument(
        "--no-watchdog",
        action="store_true",
        help="不后台拉起 agent_watchdog（默认会拉起，防 WS 假活僵死）",
    )
    return p.parse_args(argv)


def _reexec_in_venv_if_needed(argv: list[str] | None) -> None:
    """若当前解释器不是项目 venv，则切换到 venv 再跑（保证 psutil 可 import）。

    注意：不能用 Path.resolve() 比较——venv/bin/python 常是指向系统 python 的符号链接，
    resolve 后会误判为“已在 venv”。
    """
    py = _venv_python()
    if not py.exists():
        return
    if os.environ.get("ARENA_DEPLOY_IN_VENV") == "1":
        return
    exe = Path(sys.executable)
    # 仅当可执行文件路径落在 .venv 目录树内才视为已在 venv
    try:
        exe.absolute().relative_to(VENV_DIR.absolute())
        return
    except ValueError:
        pass
    env = os.environ.copy()
    env["ARENA_DEPLOY_IN_VENV"] = "1"
    cmd = [str(py), str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])]
    _info(f"切换到 venv 解释器: {py}")
    os.execve(str(py), cmd, env)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)
    _info(f"ROOT={ROOT}")

    check_python()
    ensure_venv()
    pip_install(skip=args.skip_pip)
    _reexec_in_venv_if_needed(argv)
    ensure_env(args.api_key)

    if args.no_start:
        _info("环境就绪（--no-start）。启动示例：")
        print(
            f"  {_venv_python()} -m bot.main -v --dashboard "
            f"--dashboard-host {args.host} --dashboard-port {args.port} "
            f"--log-file logs/agent.log"
        )
        return 0

    if not args.no_kill:
        kill_old_agents()

    start_agent(
        port=args.port,
        host=args.host,
        foreground=args.foreground,
        verbose=not args.quiet,
    )

    # 外部看门狗：进程内两阶段 idle 仍不够时的最终兜底（前台模式不拉）
    if not args.foreground and not args.no_watchdog:
        try:
            start_watchdog(stall_sec=90.0, poll_sec=10.0)
        except Exception as exc:  # noqa: BLE001
            _warn(f"启动 agent_watchdog 失败: {exc}")

    # 后台：等 health（假 Key 会很快 401 退出，不宜等太久）
    time.sleep(2)
    ok = wait_health(args.port, timeout=18.0)
    tail_logs()
    print()
    local_url = f"http://127.0.0.1:{args.port}/"
    if ok:
        _info("部署成功")
        _info(f"Dashboard 本机: {local_url}")
        _info(f"Dashboard 监听: {args.host}:{args.port}（0.0.0.0 表示对公网开放）")
        _info("日志: logs/agent.log  |  PID: logs/agent.pid")
        _info("看门狗: logs/watchdog.log | PID: logs/watchdog.pid")
        _info("停止: 结束 bot.main / agent_watchdog，或再运行本脚本（会先 kill 旧进程）")
        return 0

    _warn("Agent 可能仍在连接游戏服务器；若日志持续无 tick，检查 API Key / 网络")
    _info(f"Dashboard（若已起）: {local_url}  监听 {args.host}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
