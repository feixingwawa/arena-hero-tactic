#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arena Hero Tactic — 跨平台一键部署入口（Windows / macOS / Linux）。

用法（仓库根目录或任意目录）：

  # 已有仓库
  python install.py
  python install.py --api-key YOUR_KEY
  python install.py --no-start

  # 远程一行（会下载源码到当前目录 arena-hero-tactic/）
  # Windows PowerShell:
  #   irm https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py -OutFile install.py
  #   py install.py
  # Windows CMD:
  #   curl -fsSL -o install.py https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py
  #   py install.py
  # Linux / macOS:
  #   python3 <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py)

环境变量：
  ARENA_HERO_API_KEY   非交互传入 Key
  REPO_URL             git 仓库地址
  REPO_ZIP / REPO_TAR  无 git 时的源码包
  INSTALL_DIR          安装目录（默认 ./arena-hero-tactic）
  PIP_INDEX_URL        pip 镜像
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Sequence, Union
from urllib.request import urlretrieve

REPO_URL = os.environ.get(
    "REPO_URL", "https://github.com/feixingwawa/arena-hero-tactic.git"
)
REPO_ZIP = os.environ.get(
    "REPO_ZIP",
    "https://codeload.github.com/feixingwawa/arena-hero-tactic/zip/refs/heads/main",
)
REPO_TAR = os.environ.get(
    "REPO_TAR",
    "https://codeload.github.com/feixingwawa/arena-hero-tactic/tar.gz/refs/heads/main",
)
DEFAULT_INSTALL_DIR = Path(
    os.environ.get("INSTALL_DIR", str(Path.cwd() / "arena-hero-tactic"))
).resolve()
MIN_PY = (3, 11)
PLACEHOLDER_KEYS = {
    "",
    "your_api_key_here",
    "你的_API_KEY",
    "changeme",
    "REPLACE_WITH_REAL_KEY",
}

# 国内更稳的默认 pip 源
if "PIP_INDEX_URL" not in os.environ:
    os.environ["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
if "PIP_DEFAULT_TIMEOUT" not in os.environ:
    os.environ["PIP_DEFAULT_TIMEOUT"] = "120"

CmdSpec = Union[str, Sequence[str]]


def _info(msg: str) -> None:
    print(f"[install] {msg}")


def _ok(msg: str) -> None:
    print(f"[install] {msg}")


def _warn(msg: str) -> None:
    print(f"[install] WARN: {msg}")


def _die(msg: str, code: int = 1) -> None:
    print(f"[install] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _banner() -> None:
    print(
        """
╔══════════════════════════════════════════════════════════╗
║     Arena Hero Tactic  —  一键全自动部署 + 运行          ║
║     跨平台：Windows / macOS / Linux                      ║
║     资源优先 + 均衡防守战术客户端                         ║
╚══════════════════════════════════════════════════════════╝
""".strip()
    )


def _is_placeholder(key: Optional[str]) -> bool:
    if key is None:
        return True
    return key.strip() in PLACEHOLDER_KEYS


def _looks_like_repo(path: Path) -> bool:
    return (path / "scripts" / "deploy.py").is_file() and (
        path / "bot" / "main.py"
    ).is_file()


def _self_dir() -> Optional[Path]:
    """脚本所在目录（支持直接文件路径；stdin/`-` 管道时返回 None）。"""
    try:
        p = Path(__file__).resolve().parent
        if p.exists() and str(p) not in (".", ""):
            return p
    except Exception:
        pass
    return None


def _python_abs(cmd: CmdSpec) -> Optional[str]:
    """运行候选解释器，若版本 >=3.11 则返回其 sys.executable 绝对路径。"""
    parts: list[str] = list(cmd) if not isinstance(cmd, str) else [cmd]
    try:
        r = subprocess.run(
            parts
            + [
                "-c",
                "import sys; "
                "import sys as _s; "
                "print(_s.executable) if _s.version_info >= (3, 11) else None; "
                "raise SystemExit(0 if _s.version_info >= (3, 11) else 1)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0:
            lines = (r.stdout or "").strip().splitlines()
            if lines and lines[-1].strip():
                return lines[-1].strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def pick_python() -> str:
    """返回可用的 Python 3.11+ 可执行**绝对路径**（优先当前解释器）。"""
    if sys.version_info >= MIN_PY and sys.executable:
        try:
            return str(Path(sys.executable).resolve())
        except OSError:
            return sys.executable

    candidates: list[CmdSpec] = []
    if sys.platform == "win32":
        candidates.extend(
            [
                ["py", "-3.13"],
                ["py", "-3.12"],
                ["py", "-3.11"],
                ["py", "-3"],
                "python",
                "python3",
            ]
        )
    else:
        candidates.extend(
            ["python3.13", "python3.12", "python3.11", "python3", "python"]
        )

    seen: set[str] = set()
    for c in candidates:
        key = " ".join(c) if not isinstance(c, str) else c
        if key in seen:
            continue
        seen.add(key)
        if isinstance(c, str):
            resolved = shutil.which(c) or c
            abs_py = _python_abs(resolved)
        else:
            abs_py = _python_abs(list(c))
        if abs_py:
            return abs_py

    _die(
        "需要 Python >= 3.11。\n"
        "  Windows: https://www.python.org/downloads/ （勾选 Add to PATH）\n"
        "  macOS:   brew install python@3.12\n"
        "  Ubuntu:  sudo apt-get install -y python3.11 python3.11-venv"
    )
    raise SystemExit(1)  # unreachable


def ensure_python() -> str:
    v = sys.version_info
    if (v.major, v.minor) >= MIN_PY:
        _ok(f"Python: {sys.executable} ({v.major}.{v.minor}.{v.micro})")
        try:
            return str(Path(sys.executable).resolve())
        except OSError:
            return sys.executable
    _warn(
        f"当前解释器 {v.major}.{v.minor} < 3.11，尝试寻找系统中的 Python 3.11+"
    )
    py = pick_python()
    _ok(f"Python: {py}")
    return py


def _run_git(args: list[str], cwd: Optional[Path] = None) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _download(url: str, dest: Path) -> None:
    _info(f"下载: {url}")
    try:
        urlretrieve(url, str(dest))
    except Exception as exc:
        _die(f"下载失败: {url}\n  {exc}")


def _extract_archive(archive: Path, dest_parent: Path) -> Path:
    """解压 zip/tar.gz，返回解压出的项目根目录。"""
    tmp_out = dest_parent / "_extract_tmp"
    if tmp_out.exists():
        shutil.rmtree(tmp_out, ignore_errors=True)
    tmp_out.mkdir(parents=True, exist_ok=True)

    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(tmp_out)
    elif name.endswith((".tar.gz", ".tgz")) or name.endswith(".tar"):
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(tmp_out)
    else:
        _die(f"未知压缩格式: {archive}")

    candidates = [
        p
        for p in tmp_out.iterdir()
        if p.is_dir()
        and (p.name.startswith("arena-hero-tactic") or _looks_like_repo(p))
    ]
    if not candidates:
        if _looks_like_repo(tmp_out):
            return tmp_out
        _die(f"解压后未找到项目目录: {tmp_out}")
    return candidates[0]


def fetch_repo(install_dir: Path) -> Path:
    """定位或下载仓库，返回项目根目录。"""
    self_dir = _self_dir()
    if self_dir and _looks_like_repo(self_dir):
        _ok(f"使用本地仓库: {self_dir}")
        return self_dir

    if _looks_like_repo(install_dir):
        _ok(f"复用已有目录: {install_dir}")
        if (install_dir / ".git").is_dir():
            _info("尝试 git pull …")
            _run_git(["pull", "--ff-only"], cwd=install_dir)
        return install_dir

    _info(f"下载源码 → {install_dir}")
    install_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="arena-install-") as tmp:
        tmp_path = Path(tmp)
        if shutil.which("git"):
            clone_dest = tmp_path / "repo"
            env = os.environ.copy()
            env.setdefault("GIT_HTTP_VERSION", "1.1")
            try:
                r = subprocess.run(
                    ["git", "clone", "--depth", "1", REPO_URL, str(clone_dest)],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env=env,
                )
                if r.returncode == 0 and _looks_like_repo(clone_dest):
                    if install_dir.exists():
                        shutil.rmtree(install_dir, ignore_errors=True)
                    shutil.move(str(clone_dest), str(install_dir))
                    _ok("git clone 完成")
                    return install_dir
                _warn("git clone 失败，改用压缩包")
            except (OSError, subprocess.SubprocessError) as exc:
                _warn(f"git clone 异常: {exc}，改用压缩包")

        prefer_zip = sys.platform == "win32"
        urls = [REPO_ZIP, REPO_TAR] if prefer_zip else [REPO_TAR, REPO_ZIP]
        last_err: Optional[BaseException] = None
        for url in urls:
            try:
                suffix = ".zip" if "zip" in url else ".tgz"
                archive = tmp_path / f"src{suffix}"
                _download(url, archive)
                extracted = _extract_archive(archive, tmp_path)
                if install_dir.exists():
                    shutil.rmtree(install_dir, ignore_errors=True)
                shutil.move(str(extracted), str(install_dir))
                _ok("源码就绪")
                return install_dir
            except SystemExit:
                raise
            except Exception as exc:
                last_err = exc
                _warn(f"压缩包失败 ({url}): {exc}")
        _die(f"无法获取源码。最后错误: {last_err}")
    raise SystemExit(1)  # unreachable


def _read_env_key(env_path: Path) -> str:
    if not env_path.is_file():
        return ""
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("ARENA_HERO_API_KEY="):
            val = s.split("=", 1)[1].strip().strip('"').strip("'")
            return val
    return ""


def resolve_api_key(api_key_arg: Optional[str], install_dir: Path) -> str:
    if api_key_arg and not _is_placeholder(api_key_arg):
        _info("使用命令行 --api-key")
        return api_key_arg.strip()

    env_key = os.environ.get("ARENA_HERO_API_KEY", "").strip()
    if env_key and not _is_placeholder(env_key):
        _info("使用环境变量 ARENA_HERO_API_KEY")
        return env_key

    existing = _read_env_key(install_dir / ".env")
    if existing and not _is_placeholder(existing):
        _info("检测到 .env 中已有 API Key，将直接使用")
        return existing

    print()
    print("────────────────────────────────────────")
    print("  请输入 Arena Hero API Key")
    print("  获取: https://doc.arenahero.io/")
    print("  （输入时不回显，回车确认）")
    print("────────────────────────────────────────")

    if not sys.stdin.isatty():
        _die(
            "无法交互输入 API Key（无 TTY）。请改用:\n"
            "  python install.py --api-key YOUR_KEY\n"
            "  或: ARENA_HERO_API_KEY=YOUR_KEY python install.py"
        )

    try:
        import getpass

        key = getpass.getpass("API Key: ").strip()
    except Exception:
        key = input("API Key: ").strip()

    if _is_placeholder(key):
        _die("API Key 不能为空或占位符")
    _ok("已接收 API Key（不会打印明文）")
    return key


def run_deploy(
    py: str, install_dir: Path, api_key: str, pass_args: list[str]
) -> int:
    deploy_py = install_dir / "scripts" / "deploy.py"
    if not deploy_py.is_file():
        _die(f"缺少 {deploy_py}")

    os.environ["ARENA_HERO_API_KEY"] = api_key
    cmd = [py, str(deploy_py), "--api-key", api_key, *pass_args]
    _info("开始安装依赖并启动（Dashboard 默认 0.0.0.0:8765）…")
    _info(f"cd {install_dir}")
    try:
        r = subprocess.run(cmd, cwd=str(install_dir))
        return int(r.returncode)
    except KeyboardInterrupt:
        print("\n[install] 用户中断")
        return 130


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Arena Hero Tactic 跨平台一键部署（Win/Mac/Linux）"
    )
    p.add_argument("--api-key", type=str, default=None, help="写入 .env 的 API Key")
    p.add_argument(
        "--install-dir",
        type=str,
        default=None,
        help="安装目录（默认 ./arena-hero-tactic 或本仓库）",
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help="只装环境不启动（转发给 deploy.py）",
    )
    p.add_argument("--skip-pip", action="store_true", help="跳过 pip（转发给 deploy.py）")
    p.add_argument("--foreground", action="store_true", help="前台运行 agent")
    p.add_argument("--port", type=int, default=None, help="Dashboard 端口")
    p.add_argument("--host", type=str, default=None, help="Dashboard 监听地址")
    p.add_argument("--quiet", action="store_true", help="启动不加 -v")
    p.add_argument("--no-kill", action="store_true", help="不结束旧 bot.main")
    args, unknown = p.parse_known_args(argv)
    args.unknown = unknown
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    _banner()

    py = ensure_python()
    try:
        same = Path(py).resolve() == Path(sys.executable).resolve()
    except OSError:
        same = py == sys.executable
    if not same and sys.version_info < MIN_PY:
        try:
            script = Path(__file__).resolve()
            if not script.is_file():
                script = None  # type: ignore[assignment]
        except Exception:
            script = None
        if script is None:
            _die("请用 Python 3.11+ 直接运行本脚本（当前解释器版本过低）")
        _info(f"切换到 {py} 重新执行 …")
        argv_list = [py, str(script), *(argv if argv is not None else sys.argv[1:])]
        try:
            os.execv(py, argv_list)
        except OSError as exc:
            _warn(f"execv 失败 ({exc})，改用 subprocess")
            r = subprocess.run(argv_list)
            raise SystemExit(r.returncode)

    install_dir = (
        Path(args.install_dir).expanduser().resolve()
        if args.install_dir
        else DEFAULT_INSTALL_DIR
    )
    self_dir = _self_dir()
    if self_dir and _looks_like_repo(self_dir) and not args.install_dir:
        install_dir = self_dir

    root = fetch_repo(install_dir)
    api_key = resolve_api_key(args.api_key, root)

    pass_args: list[str] = list(args.unknown)
    if args.no_start:
        pass_args.append("--no-start")
    if args.skip_pip:
        pass_args.append("--skip-pip")
    if args.foreground:
        pass_args.append("--foreground")
    if args.quiet:
        pass_args.append("--quiet")
    if args.no_kill:
        pass_args.append("--no-kill")
    if args.port is not None:
        pass_args.extend(["--port", str(args.port)])
    if args.host is not None:
        pass_args.extend(["--host", args.host])

    return run_deploy(py, root, api_key, pass_args)


if __name__ == "__main__":
    raise SystemExit(main())
