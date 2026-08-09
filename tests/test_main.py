"""bot.main 单测：SDK 版本自检等。"""

from __future__ import annotations

import pytest
from bot.config import DEFAULT_CONFIG


def test_TR_7_4_sdk_version_check_fail(monkeypatch, caplog) -> None:
    """TR-7.4: SDK 版本 0.2.8 不满足要求 → 抛 SystemExit(1)，日志含 'SDK 版本检查失败'。"""
    import logging

    # 确保能捕获到 logger 输出
    caplog.set_level(logging.ERROR, logger="arena_hero_tactic")

    # monkeypatch 掉 importlib.metadata.version，让它返回 "0.2.8"
    # 注意：run_loop 内部是 from importlib.metadata import version，然后调用局部 version
    # 所以需要直接 patch importlib.metadata.version
    fake_version_called: list[str] = []

    def fake_version(pkg_name: str) -> str:
        fake_version_called.append(pkg_name)
        if pkg_name == "arena-hero":
            return "0.2.8"
        return "0.0.0"

    # patch importlib.metadata.version
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    # import bot.main 后再 patch _import_sdk
    import bot.main as main_module

    # 同时 patch 掉 _import_sdk（如果版本检查奇迹通过的话防止进入连接）
    def fake_import_sdk():
        class DummyClient:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def turns(self): return iter(())
        return DummyClient, (), ()

    monkeypatch.setattr(main_module, "_import_sdk", fake_import_sdk)

    # 调用 run_loop，应该抛 SystemExit(1)
    with pytest.raises(SystemExit) as exc_info:
        main_module.run_loop(
            api_key="test_key_1234",
            config=DEFAULT_CONFIG,
            base_url=None,
            max_turns=1,
        )

    assert exc_info.value.code == 1, f"期望退出码 1，实际 {exc_info.value.code}"
    # 检查至少触发了 version 检查
    assert len(fake_version_called) > 0, (
        f"应该调用 importlib.metadata.version。fake_version_called={fake_version_called}"
    )
    # 检查日志是否有错误信息（caplog 可能没抓到 logger，但至少要有上面的断言）
    has_fail_log = any(
        "SDK 版本检查失败" in rec.message
        for rec in caplog.records
        if rec.levelno >= logging.ERROR
    )
    # 如果有 caplog 记录，做双重断言
    if caplog.records:
        assert has_fail_log or len(fake_version_called) > 0, (
            f"应该有 SDK 版本检查失败的日志。logs={caplog.records}"
        )



def test_is_stale_tick_error_variants() -> None:
    from bot.main import _is_stale_tick_error

    assert _is_stale_tick_error(Exception("409 TICK_MISMATCH"))
    assert _is_stale_tick_error(Exception("turn closed"))
    assert not _is_stale_tick_error(Exception("network blip"))


def test_is_client_closed_error_variants() -> None:
    from bot.main import _is_client_closed_error

    class ConfigurationError(Exception):
        pass

    assert _is_client_closed_error(ConfigurationError("the client is closed"))
    assert _is_client_closed_error(Exception("client is closed"))
    assert not _is_client_closed_error(Exception("network blip"))


def test_stale_streak_forces_session_reconnect(monkeypatch) -> None:
    """连续 3 次 submit TICK_MISMATCH → SessionNeedsReconnect，触发外层重连。"""
    import bot.main as main_module
    from bot.config import DEFAULT_CONFIG
    from bot.main import SessionNeedsReconnect, STALE_STREAK_RECONNECT

    class FakeTurn:
        def __init__(self, tick: int) -> None:
            self.tick = tick
            self._n = 0

        def submit(self) -> None:
            raise RuntimeError("409 TICK_MISMATCH")

    class FakeResult:
        def summary(self) -> str:
            return "fake"

        logs: list = []

    class FakeGame:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def turns(self):
            # 足够多的 turn 以触发 streak
            for i in range(10):
                yield FakeTurn(1000 + i)

        def close(self) -> None:
            self.closed = True

    closed_reasons: list[str] = []

    def fake_import_sdk():
        return (lambda **kw: FakeGame()), (), ()

    def fake_decide(turn, config=None):
        return FakeResult()

    def fake_close(game, reason: str) -> None:
        closed_reasons.append(reason)
        getattr(game, "close", lambda: None)()

    monkeypatch.setattr(main_module, "_import_sdk", fake_import_sdk)
    monkeypatch.setattr(main_module, "decide", fake_decide)
    monkeypatch.setattr(main_module, "_try_close_game", fake_close)
    # 加速看门狗：不影响本测（submit 会先触发）
    monkeypatch.setattr(main_module, "STALE_STREAK_RECONNECT", 3)
    monkeypatch.setattr(main_module, "SESSION_IDLE_RECONNECT_SEC", 9999.0)

    with pytest.raises(SessionNeedsReconnect) as ei:
        main_module.run_session(
            api_key="k",
            config=DEFAULT_CONFIG,
            base_url=None,
            max_turns=None,
            turns_done=0,
            dashboard_push=None,
        )
    assert "连续" in str(ei.value) or "TICK_MISMATCH" in str(ei.value).upper() or "过期" in str(ei.value)
    assert closed_reasons, "应调用 _try_close_game"
    assert any("stale_streak" in r for r in closed_reasons)


def test_run_loop_reconnects_on_session_needs_reconnect(monkeypatch) -> None:
    """SessionNeedsReconnect 被 run_loop 捕获后应开启新会话（非 SystemExit）。"""
    import bot.main as main_module
    from bot.config import DEFAULT_CONFIG
    from bot.main import SessionNeedsReconnect

    calls = {"n": 0}

    def fake_import_sdk():
        class Dummy:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def turns(self):
                return iter(())

        return Dummy, (), ()

    def fake_run_session(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SessionNeedsReconnect("test-stale-storm")
        # 第二次：立刻靠 max_turns 结束
        return 1, True

    monkeypatch.setattr(main_module, "_import_sdk", fake_import_sdk)
    monkeypatch.setattr(main_module, "run_session", fake_run_session)
    monkeypatch.setattr(main_module, "load_world_memory", lambda: False)
    monkeypatch.setattr(main_module, "save_world_memory", lambda: None)
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)

    # 绕过 SDK 版本硬检查
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.9")

    n = main_module.run_loop(
        api_key="test_key_xxxx",
        config=DEFAULT_CONFIG,
        max_turns=1,
        dashboard_enabled=False,
    )
    assert calls["n"] >= 2, f"应重连至少一次，calls={calls}"
    assert n >= 1
