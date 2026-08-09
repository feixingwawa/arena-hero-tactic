"""进程外 agent 看门狗：日志/tick 停滞则自动杀旧并重启 bot.main。

设计目标（真实对局，不注入假数据）：
- 监控 logs/agent.log 的 mtime 与最近 `tick=` INFO 行
- 超过 STALL_SEC 无新 tick → 调用与 _ensure_single_agent 相同的杀启流程
- 冷却 COOLDOWN_SEC，避免重启风暴
- 可前台循环：python scripts/agent_watchdog.py
- 或一次性检查：python scripts/agent_watchdog.py --once

环境变量（可选）：
  AGENT_WATCHDOG_STALL_SEC=120
  AGENT_WATCHDOG_COOLDOWN_SEC=60
  AGENT_WATCHDOG_POLL_SEC=15
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ.setdefault("PYTHONPATH", str(ROOT))

LOGS = ROOT / "logs"
AGENT_LOG = LOGS / "agent.log"
WATCH_LOG = LOGS / "watchdog.log"
WATCH_PID = LOGS / "watchdog.pid"
PID_FILE = LOGS / "agent.pid"
ENSURE = ROOT / "scripts" / "_ensure_single_agent.py"

_venv = ROOT / ".venv" / "Scripts" / "python.exe"
if not _venv.exists():
    _venv = ROOT / ".venv" / "bin" / "python"
PY = str(_venv if _venv.exists() else sys.executable)

STALL_SEC = float(os.environ.get("AGENT_WATCHDOG_STALL_SEC", "90"))
COOLDOWN_SEC = float(os.environ.get("AGENT_WATCHDOG_COOLDOWN_SEC", "45"))
POLL_SEC = float(os.environ.get("AGENT_WATCHDOG_POLL_SEC", "10"))

# agent 日志 datefmt="%H:%M:%S"；也兼容完整日期
# 只认「成功提交」的 INFO tick 行（含 (Nms) 或 pop=），避免 TICK_MISMATCH WARNING
# 把 tick_age 重置导致假活拖到 90s 才重启
_TICK_RE_FULL = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}).*?\[INFO\].*?\btick=(\d+)\b"
    r".*?(?:\(\d+ms\)|\bpop=)"
)
_TICK_RE_HMS = re.compile(
    r"(?m)^(\d{2}:\d{2}:\d{2})\s+\[INFO\].*?\btick=(\d+)\b"
    r".*?(?:\(\d+ms\)|\bpop=)"
)


def _log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOGS.mkdir(exist_ok=True)
    with WATCH_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _bot_main_alive() -> list[int]:
    try:
        import psutil
    except ImportError:
        return []
    pids: list[int] = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = p.info.get("cmdline") or []
            low = " ".join(str(x).lower() for x in cl)
            if "bot.main" not in low or "python" not in low:
                continue
            if any(
                x in low
                for x in (
                    "_ensure_single",
                    "agent_watchdog",
                    "restart_agent",
                    "_monitor",
                )
            ):
                continue
            pids.append(int(p.info["pid"]))
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return pids


def _parse_hms_today(hms: str) -> datetime | None:
    """把 HH:MM:SS 落到今天本地日；跨午夜回拨时减一天。"""
    try:
        t = datetime.strptime(hms, "%H:%M:%S").time()
    except ValueError:
        return None
    now = datetime.now()
    ts = datetime.combine(now.date(), t)
    # 日志时间比现在晚很多 → 可能是昨天（跨日）
    if ts.timestamp() - time.time() > 6 * 3600:
        ts = ts.replace(day=ts.day)  # no-op keep type
        from datetime import timedelta

        ts = ts - timedelta(days=1)
    # 仍明显在未来：用 mtime 路径
    if ts.timestamp() - time.time() > 120:
        return None
    return ts


def _parse_last_tick_age(path: Path, *, read_bytes: int = 512_000) -> tuple[float | None, int | None]:
    """从 agent.log 尾部解析最近 tick 行的墙钟年龄（秒）与 tick 号。"""
    if not path.exists():
        return None, None
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > read_bytes:
                f.seek(size - read_bytes)
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None, None

    last_ts: datetime | None = None
    last_tick: int | None = None

    for m in _TICK_RE_FULL.finditer(raw):
        ts_s, tick_s = m.group(1), m.group(2)
        try:
            ts = datetime.strptime(ts_s.replace("T", " "), "%Y-%m-%d %H:%M:%S")
            tick = int(tick_s)
        except ValueError:
            continue
        last_ts, last_tick = ts, tick

    for m in _TICK_RE_HMS.finditer(raw):
        hms, tick_s = m.group(1), m.group(2)
        ts = _parse_hms_today(hms)
        if ts is None:
            continue
        try:
            tick = int(tick_s)
        except ValueError:
            continue
        last_ts, last_tick = ts, tick

    if last_ts is None:
        # 回退：用文件 mtime
        try:
            age = time.time() - path.stat().st_mtime
            return age, None
        except OSError:
            return None, None

    age = time.time() - last_ts.timestamp()
    if age < -5:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = 0.0
    return max(0.0, age), last_tick


def _restart() -> int:
    _log("action=restart via _ensure_single_agent")
    proc = subprocess.run(
        [PY, str(ENSURE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.strip().splitlines()[-12:]:
        _log(f"  ensure: {line}")
    return int(proc.returncode)


def check_once(*, last_restart_mono: float) -> float:
    """执行一次健康检查；若重启则返回新的 last_restart_mono。"""
    alive = _bot_main_alive()
    age, tick = _parse_last_tick_age(AGENT_LOG)
    age_s = f"{age:.0f}s" if age is not None else "n/a"
    _log(
        f"check alive={alive} last_tick={tick} tick_age={age_s} "
        f"stall_limit={STALL_SEC:.0f}s"
    )

    need = False
    reason = ""
    if not alive:
        need = True
        reason = "no_bot_main_process"
    elif age is None:
        # 有进程但无日志：给宽限期，避免刚启动误杀
        if time.monotonic() - last_restart_mono > max(STALL_SEC, 60.0):
            need = True
            reason = "no_tick_in_log"
    elif age >= STALL_SEC:
        need = True
        reason = f"tick_stall_{age:.0f}s"

    if not need:
        return last_restart_mono

    cool_left = COOLDOWN_SEC - (time.monotonic() - last_restart_mono)
    if cool_left > 0:
        _log(f"skip_restart reason={reason} cooldown_left={cool_left:.0f}s")
        return last_restart_mono

    _log(f"RESTART reason={reason}")
    rc = _restart()
    _log(f"restart_done rc={rc}")
    return time.monotonic()


def _other_watchdogs() -> list[int]:
    """返回其它 agent_watchdog 进程 pid（排除本进程与父进程/启动器）。"""
    me = os.getpid()
    try:
        ppid = os.getppid()
    except OSError:
        ppid = -1
    skip = {me, ppid}
    # 再向上跳一层（venv launcher → real python 常见两层）
    try:
        import psutil

        if ppid > 0:
            try:
                skip.add(psutil.Process(ppid).ppid())
            except (psutil.Error, OSError):
                pass
    except ImportError:
        psutil = None  # type: ignore

    pids: list[int] = []
    if psutil is None:
        try:
            import psutil as _ps

            psutil = _ps
        except ImportError:
            return pids

    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(p.info["pid"])
            if pid in skip:
                continue
            cl = p.info.get("cmdline") or []
            low = " ".join(str(x).lower() for x in cl)
            # 必须是在跑 watchdog 脚本，排除仅路径提及
            if "agent_watchdog.py" not in low:
                continue
            if "python" not in low:
                continue
            if "_start_watchdog" in low:
                continue
            pids.append(pid)
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return pids


def _pid_is_watchdog(pid: int) -> bool:
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return False
        cl = " ".join(psutil.Process(pid).cmdline()).lower()
        return "agent_watchdog.py" in cl and "python" in cl
    except Exception:  # noqa: BLE001
        return False


def _acquire_singleton() -> bool:
    """单实例：pid 文件 + 活进程校验；排除 venv 父启动器误判。"""
    LOGS.mkdir(exist_ok=True)
    me = os.getpid()

    if WATCH_PID.exists():
        try:
            old = int(WATCH_PID.read_text(encoding="ascii").strip())
        except ValueError:
            old = -1
        if old > 0 and old != me and _pid_is_watchdog(old):
            _log(f"another_watchdog_running pids=[{old}] (pidfile) — exit")
            return False
        # 陈旧 pid 文件
        try:
            WATCH_PID.unlink()
        except OSError:
            pass

    others = _other_watchdogs()
    # 过滤：若 others 里的进程只是短暂 launcher 且无真实 loop，仍可能误报；
    # 再确认它们不是本进程树
    real = [p for p in others if p != me and _pid_is_watchdog(p)]
    if real:
        _log(f"another_watchdog_running pids={real} — exit")
        return False

    WATCH_PID.write_text(str(me), encoding="ascii")
    return True


def main(argv: list[str] | None = None) -> int:
    global STALL_SEC, POLL_SEC

    ap = argparse.ArgumentParser(description="Arena Hero agent external watchdog")
    ap.add_argument("--once", action="store_true", help="只检查一次后退出")
    ap.add_argument(
        "--stall-sec",
        type=float,
        default=STALL_SEC,
        help=f"无 tick 超过该秒数则重启 (default {STALL_SEC})",
    )
    ap.add_argument(
        "--poll-sec",
        type=float,
        default=POLL_SEC,
        help=f"循环轮询间隔 (default {POLL_SEC})",
    )
    args = ap.parse_args(argv)

    STALL_SEC = float(args.stall_sec)
    POLL_SEC = float(args.poll_sec)

    if not args.once:
        if not _acquire_singleton():
            return 0

    _log(
        f"watchdog start stall={STALL_SEC:.0f}s poll={POLL_SEC:.0f}s "
        f"cooldown={COOLDOWN_SEC:.0f}s py={PY} pid={os.getpid()}"
    )
    last_restart = 0.0  # 允许首次立即重启
    if args.once:
        check_once(last_restart_mono=last_restart)
        return 0

    try:
        while True:
            try:
                last_restart = check_once(last_restart_mono=last_restart)
            except KeyboardInterrupt:
                _log("watchdog stop (KeyboardInterrupt)")
                return 0
            except Exception as exc:  # noqa: BLE001
                _log(f"watchdog_error {type(exc).__name__}: {exc}")
            try:
                time.sleep(POLL_SEC)
            except KeyboardInterrupt:
                _log("watchdog stop (KeyboardInterrupt)")
                return 0
    finally:
        try:
            if WATCH_PID.exists() and WATCH_PID.read_text(encoding="ascii").strip() == str(
                os.getpid()
            ):
                WATCH_PID.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
