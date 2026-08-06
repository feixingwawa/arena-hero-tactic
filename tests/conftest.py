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
