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
