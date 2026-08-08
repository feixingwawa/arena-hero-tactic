"""pytest fixtures（依赖 tests.stubs，安装 pytest 后可用）。"""

from __future__ import annotations

import pytest

from bot.config import TacticConfig
from tests.stubs import StubCore, StubTurn, StubUnit

# 再导出，兼容 `from tests.conftest import StubTurn`
from tests.stubs import StubCore as StubCore  # noqa: F401
from tests.stubs import StubEnemy as StubEnemy  # noqa: F401
from tests.stubs import StubTurn as StubTurn  # noqa: F401
from tests.stubs import StubUnit as StubUnit  # noqa: F401
from tests.stubs import StubState as StubState  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_global_state() -> None:
    """每个测试前清空模块级状态（探索记忆 / WORLD_MEMORY 单例）。

    设计约定：decide/command_workers 默认使用模块级单例与进程内 dict，
    测试必须清理，避免跨测试污染。
    """
    from bot.config import DEFAULT_CONFIG
    from bot.economy import (
        _last_move_dir,
        _loop_trackers,
        _pending_return_mines,
        _spiral_state,
        health_tracker,
    )
    import bot.memory as memory_module
    from bot.memory import WORLD_MEMORY

    _spiral_state.clear()
    _last_move_dir.clear()
    _loop_trackers.clear()
    _pending_return_mines.clear()
    health_tracker["last_deposit_tick"] = 0
    health_tracker["stall_ticks"] = 0
    WORLD_MEMORY.resource_points.clear()
    WORLD_MEMORY.obstacles.clear()
    WORLD_MEMORY.obstacle_cache.clear()
    WORLD_MEMORY.dropped_cargo.clear()
    WORLD_MEMORY.explored_chunks.clear()
    WORLD_MEMORY.explored_chunk_ticks.clear()
    WORLD_MEMORY.chunk_last_seen_ticks.clear()
    WORLD_MEMORY._chunk_quota_cache.clear()
    # Task 5 预留：若 bot.memory 后续新增模块级 chunk_last_seen_ticks 字典，在此清理
    if hasattr(memory_module, "chunk_last_seen_ticks"):
        chunk_last_seen = getattr(memory_module, "chunk_last_seen_ticks")
        if isinstance(chunk_last_seen, dict):
            chunk_last_seen.clear()
    # 防 default-config 的 beacon_position 跨测试污染（frozen 用 object.__setattr__）
    object.__setattr__(DEFAULT_CONFIG, "beacon_position", None)
    yield


@pytest.fixture
def config() -> TacticConfig:
    return TacticConfig(
        max_population=18,
        target_workers=4,
        target_vanguards=2,
        target_rangers=1,
        defense_radius=3,
        ranger_radius=4,
        threat_radius=8,
        retreat_adjacent=1,
        retreat_radius=3,
        reserve_resources=2,
    )


@pytest.fixture
def basic_turn() -> StubTurn:
    """1 Core + 1 Worker，附近有资源。"""
    core = StubCore(position=(10, 10), hp=5, shield=5)
    worker = StubUnit(
        position=(10, 11),
        hp=2,
        cargo=0,
        unit_type="WORKER",
    )
    return StubTurn(
        tick=1,
        resources=8,
        core=core,
        workers=[worker],
        resource_cells={(12, 10), (14, 10)},
    )
