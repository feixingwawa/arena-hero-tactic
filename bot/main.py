"""入口：读取 API Key，连接 Arena Hero，可持续运行 turns 循环。

用法：
    python -m bot.main
    python -m bot.main -v --max-turns 5
    python -m bot.main -v --log-file logs/agent.log

环境变量：
    ARENA_HERO_API_KEY   必填
    ARENA_HERO_BASE_URL  可选
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from bot.config import DEFAULT_CONFIG, TacticConfig
from bot.strategy import decide

logger = logging.getLogger("arena_hero_tactic")

# 可恢复错误的指数退避：1s → 2s → 4s … 上限 30s
_BACKOFF_INITIAL = 1.0
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX = 30.0


def mask_api_key(key: str) -> str:
    """日志脱敏：仅保留末 4 位。"""
    if not key:
        return "key=***"
    if len(key) <= 4:
        return "key=***"
    return f"key=***{key[-4:]}"


def load_api_key() -> str:
    """从环境变量或 .env 加载 API Key。"""
    key = os.environ.get("ARENA_HERO_API_KEY", "").strip()
    if key:
        return key

    try:
        from dotenv import load_dotenv

        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        env_path = os.path.join(root, ".env")
        load_dotenv(env_path)
        load_dotenv()
        key = os.environ.get("ARENA_HERO_API_KEY", "").strip()
    except ImportError:
        pass

    if not key:
        raise SystemExit(
            "未找到 ARENA_HERO_API_KEY。\n"
            "请复制 .env.example 为 .env 并填入 Key，或设置环境变量。\n"
            "切勿将真实 Key 提交到仓库。"
        )
    return key


def setup_logging(verbose: bool = False, log_file: Optional[str] = None) -> None:
    """配置控制台 + 可选文件日志。

    即使 -v，也把 websockets/httpx/httpcore 压到 WARNING，
    避免 Authorization 头把完整 API Key 打进日志。
    """
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        logging.getLogger("arena_hero_tactic").info("文件日志: %s", path.resolve())

    # 脱敏：禁止网络库 DEBUG 打印完整请求头（含 Bearer token）
    for noisy in (
        "websockets",
        "websockets.client",
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.proxy",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _import_sdk():
    """延迟导入 SDK，并返回错误类型集合。"""
    try:
        from arena_hero import ArenaHeroClient
    except ImportError as exc:
        raise SystemExit(
            "无法导入 arena_hero。请先安装依赖：\n"
            "  pip install -r requirements.txt\n"
            f"原始错误: {exc}"
        ) from exc

    fatal_types: tuple = ()
    recoverable_types: tuple = (ConnectionError, TimeoutError, OSError)
    try:
        from arena_hero import (
            AuthenticationError,
            PolicyViolationError,
            TransportError,
            ProtocolError,
            ConfigurationError,
            APIError,
            ArenaHeroError,
        )

        fatal_types = (AuthenticationError, PolicyViolationError, ConfigurationError)
        recoverable_types = recoverable_types + (
            TransportError,
            ProtocolError,
            APIError,
            ArenaHeroError,
        )
    except ImportError:
        pass

    return ArenaHeroClient, fatal_types, recoverable_types


def _is_fatal_error(exc: BaseException, fatal_types: tuple) -> bool:
    """判断是否为不应重连的致命错误（Key 无效、策略违规等）。"""
    if fatal_types and isinstance(exc, fatal_types):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "authentication" in name:
        return True
    if "policyviolation" in name or "policy violation" in msg:
        return True
    if "unauthorized" in msg or "invalid api key" in msg or "invalid key" in msg:
        return True
    if ("401" in msg or "403" in msg) and (
        "auth" in msg or "key" in msg or "forbidden" in msg or "unauthorized" in msg
    ):
        return True
    return False


def _is_stale_tick_error(exc: BaseException) -> bool:
    """命令窗口已过 / tick 不匹配：跳过本 tick，不重连、不盲目补提交。"""
    msg = str(exc).upper()
    name = type(exc).__name__.upper()
    if "TICK_MISMATCH" in msg or "TICK_MISMATCH" in name:
        return True
    if "409" in msg and ("TICK" in msg or "MISMATCH" in msg or "STALE" in msg):
        return True
    if "TURN CLOSED" in msg or "TURNCLOSED" in name.replace(" ", ""):
        return True
    return False


def run_session(
    api_key: str,
    config: TacticConfig,
    base_url: Optional[str],
    max_turns: Optional[int],
    turns_done: int,
    dashboard_push=None,
) -> tuple[int, bool]:
    """单次连接会话：处理 turns 直到断开或达到 max_turns。

    Args:
        dashboard_push: 可选回调 safe_push_snapshot(turn, result, memory, econ_states)。
            由 run_loop 在 --dashboard 时注入；None 表示不启用 dashboard（零污染）。

    Returns:
        (累计 turn 数, 是否因 max_turns 正常结束应停止重连)
    """
    ArenaHeroClient, _fatal_types, _recoverable = _import_sdk()

    client_kwargs: dict = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    stop_for_max = False
    with ArenaHeroClient(**client_kwargs) as game:
        logger.info("WebSocket 已连接，开始接收 turns")
        for turn in game.turns():
            t0 = time.perf_counter()
            tick = getattr(turn, "tick", "?")
            try:
                result = decide(turn, config=config)
                # --- Dashboard 快照钩子（零兜底伪造；dashboard_push 由 run_loop 直接注入）---
                if dashboard_push is not None:
                    try:
                        from bot.memory import WORLD_MEMORY
                        econ_states: Any = None
                        # 真实 Worker 状态字典：仅当 bot.economy 显式导出时读取
                        # （禁止自己发明 ECONOMY_STATES 这样的模块名；不存在则传 None）
                        try:
                            import importlib as _dash_il
                            _eco_mod = _dash_il.import_module("bot.economy")
                            # 按优先级尝试官方可能的导出名（都没有则 None，绝不兜底伪造）
                            for _name in ("WORKER_STATES", "worker_states", "ECONOMY_STATES",
                                          "_worker_states", "get_worker_states"):
                                _obj = getattr(_eco_mod, _name, None)
                                if _obj is None:
                                    continue
                                if callable(_obj):
                                    try:
                                        econ_states = _obj()
                                    except Exception:
                                        econ_states = None
                                elif isinstance(_obj, dict):
                                    econ_states = _obj
                                if econ_states is not None:
                                    break
                        except Exception:
                            econ_states = None
                        dashboard_push(turn, result, WORLD_MEMORY, econ_states)
                    except Exception:  # noqa: BLE001
                        import logging as _log
                        _log.getLogger("arena_hero_tactic").warning("dashboard:hook_failed", exc_info=True)
                # ---------------------------------------------------------
                try:
                    turn.submit()
                except Exception as submit_exc:
                    # 窗口已关 / tick 过期：跳过本 tick，等下一份 state
                    if _is_stale_tick_error(submit_exc):
                        logger.warning(
                            "tick=%s 提交过期已跳过: %s",
                            tick,
                            submit_exc,
                        )
                    else:
                        raise
                else:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    logger.info("%s (%.0fms)", result.summary(), elapsed_ms)
                    if logger.isEnabledFor(logging.DEBUG):
                        for line in result.logs:
                            logger.debug("  %s", line)
            except Exception as exc:
                if _is_stale_tick_error(exc):
                    logger.warning("tick=%s 已过期: %s", tick, exc)
                else:
                    logger.exception("tick=%s 决策/提交失败", tick)
                # 不再盲目补提交：过期 tick 上再 submit 只会再次 409

            turns_done += 1
            if max_turns is not None and turns_done >= max_turns:
                logger.info("达到 max_turns=%s，退出", max_turns)
                stop_for_max = True
                break

    return turns_done, stop_for_max


def run_loop(
    api_key: str,
    config: TacticConfig = DEFAULT_CONFIG,
    base_url: Optional[str] = None,
    max_turns: Optional[int] = None,
    dashboard_enabled: bool = False,
    dashboard_port: int = 8765,
    dashboard_logger=None,
) -> int:
    """外层无限重连循环 + 内层 turns 决策。

    - Ctrl+C / --max-turns 达到：正常退出
    - AuthenticationError / PolicyViolationError：停止重连并退出
    - 传输/协议等可恢复错误：指数退避后重连

    Returns:
        处理的 turn 数量。
    """
    _ArenaHeroClient, fatal_types, recoverable_types = _import_sdk()

    turn_count = 0
    backoff = _BACKOFF_INITIAL
    session = 0

    logger.info(
        "启动战术「资源优先+均衡防守」 %s max_pop=%s workers=%s vanguards=%s rangers=%s max_turns=%s",
        mask_api_key(api_key),
        config.max_population,
        config.target_workers,
        config.target_vanguards,
        config.target_rangers,
        max_turns if max_turns is not None else "∞",
    )

    # --- P3-1 SDK 版本自检（>=0.2.9,<0.3） ---
    try:
        from importlib.metadata import version
        v = version("arena-hero")
    except Exception:
        v = "0.0.0"
    try:
        parts = v.split(".")
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) >= 3 else 0
        ok = (major == 0 and minor == 2 and patch >= 9)
        if not ok:
            raise RuntimeError(f"version {v}")
    except Exception as e:
        logger.error(f"SDK 版本检查失败（要求 >=0.2.9,<0.3）: {e}")
        raise SystemExit(1)

    dashboard_push = None
    if dashboard_enabled:
        import importlib as _il
        _db = _il.import_module('bot.dashboard')
        get_store = _db.get_store
        start_dashboard_server = _db.start_dashboard_server
        DashboardLogHandler = _db.DashboardLogHandler
        start_dashboard_server(
            host='127.0.0.1', port=dashboard_port,
            store=get_store(),
            logger=dashboard_logger or logger
        )
        _handler = DashboardLogHandler(level=logging.DEBUG)
        logging.getLogger("arena_hero_tactic").addHandler(_handler)
        logging.getLogger().addHandler(_handler)
        # 直接注入回调，避免依赖模块全局（run_session 通过参数接收，零污染）
        dashboard_push = _db.safe_push_snapshot

    while True:
        session += 1
        try:
            logger.info("连接会话 #%s …", session)
            turn_count, stop_for_max = run_session(
                api_key=api_key,
                config=config,
                base_url=base_url,
                max_turns=max_turns,
                turns_done=turn_count,
                dashboard_push=dashboard_push,
            )
            if stop_for_max:
                break
            logger.warning(
                "会话 #%s 结束（已处理 %s turns），准备重连",
                session,
                turn_count,
            )
            backoff = _BACKOFF_INITIAL
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，停止")
            raise
        except SystemExit:
            raise
        except Exception as exc:
            if _is_fatal_error(exc, fatal_types):
                logger.error(
                    "致命错误，停止重连: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                raise SystemExit(
                    f"认证/策略失败，请检查 API Key: {type(exc).__name__}"
                ) from exc

            is_known = bool(recoverable_types) and isinstance(exc, recoverable_types)
            level = logging.WARNING if is_known else logging.ERROR
            logger.log(
                level,
                "会话 #%s 异常 (%s): %s — %.1fs 后重连",
                session,
                type(exc).__name__,
                exc,
                backoff,
            )
            if not is_known:
                logger.debug("未知异常堆栈", exc_info=True)

            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                logger.info("收到 Ctrl+C，停止")
                raise
            backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)
            continue

        try:
            time.sleep(min(backoff, _BACKOFF_INITIAL))
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，停止")
            raise
        backoff = _BACKOFF_INITIAL

    logger.info("循环结束，共处理 %s 个 turn", turn_count)
    return turn_count


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(
        prog="python -m bot.main",
        description="Arena Hero 战术客户端（可持续重连）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="调试日志（打印每单位动作）",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="处理 N 个 turn 后退出（默认无限）",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        metavar="PATH",
        help="同时写入文件日志，例如 logs/agent.log",
    )
    parser.add_argument('--dashboard', action='store_true', default=False,
                        help='启动本地 Dashboard Web UI (localhost)，需 pip install flask>=3.0')
    parser.add_argument('--dashboard-port', type=int, default=8765,
                        help='Dashboard 监听端口（默认 8765）')
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """CLI 入口。"""
    args = parse_args(argv)
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    api_key = load_api_key()
    logger.info("已加载 API Key %s", mask_api_key(api_key))
    base_url = os.environ.get("ARENA_HERO_BASE_URL") or None
    if base_url:
        logger.info("使用自定义 base_url=%s", base_url)

    try:
        turns = run_loop(
            api_key=api_key,
            config=DEFAULT_CONFIG,
            base_url=base_url,
            max_turns=args.max_turns,
            dashboard_enabled=args.dashboard,
            dashboard_port=args.dashboard_port,
            dashboard_logger=logger,
        )
    except KeyboardInterrupt:
        logger.info("用户中断，退出码 0")
        sys.exit(0)


if __name__ == "__main__":
    main()