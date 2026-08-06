"""战斗模块单测：威胁评估、sweep/shoot、防守环、Core 治疗。"""

from __future__ import annotations

from bot.combat import (
    assess_threats,
    command_core_defense,
    command_rangers,
    command_vanguards,
    should_core_heal_first,
)
from bot.config import TacticConfig
from bot.pathing import is_in_range_cardinal_or_diag, manhattan
from bot.roles import assign_roles
from tests.stubs import StubCore, StubEnemy, StubTurn, StubUnit


def test_manhattan_basic() -> None:
    assert manhattan((0, 0), (3, 4)) == 7


def test_ranger_range_rules() -> None:
    origin = (10, 10)
    assert is_in_range_cardinal_or_diag(origin, (10, 13)) is True  # 直线 3
    assert is_in_range_cardinal_or_diag(origin, (13, 13)) is True  # 对角 3
    assert is_in_range_cardinal_or_diag(origin, (12, 11)) is False  # (2,1) 非法
    assert is_in_range_cardinal_or_diag(origin, (10, 10)) is False
    assert is_in_range_cardinal_or_diag(origin, (10, 14)) is False  # 超距


def test_assess_near_threat(config: TacticConfig) -> None:
    turn = StubTurn(
        core=StubCore(position=(10, 10)),
        visible_enemies=[
            StubEnemy(position=(12, 10)),
            StubEnemy(position=(30, 30)),
        ],
    )
    threat = assess_threats(turn, (10, 10), config=config)
    assert threat["count"] == 2
    assert threat["has_near_threat"] is True
    assert len(threat["near"]) == 1


def test_vanguard_sweeps_adjacent(config: TacticConfig) -> None:
    vanguard = StubUnit(position=(11, 10), hp=4, unit_type="VANGUARD")
    turn = StubTurn(
        core=StubCore(position=(10, 10)),
        vanguards=[vanguard],
        visible_enemies=[StubEnemy(position=(12, 10), hp=2)],
    )
    plan = assign_roles(turn, config=config)
    logs = command_vanguards(turn, plan, config=config)
    assert vanguard.action == "sweep"
    assert any("sweep" in line for line in logs)


def test_vanguard_holds_defense_ring(config: TacticConfig) -> None:
    # 已在半径 3 的环上
    vanguard = StubUnit(position=(13, 10), hp=4, unit_type="VANGUARD")
    turn = StubTurn(
        tick=0,
        core=StubCore(position=(10, 10)),
        vanguards=[vanguard],
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    command_vanguards(turn, plan, config=config)
    # 可能 hold 或微调到 slot；不应 sweep
    assert vanguard.action in ("wait", "move", None) or vanguard.action == "wait"


def test_ranger_shoots_in_range(config: TacticConfig) -> None:
    ranger = StubUnit(position=(10, 10), hp=2, unit_type="RANGER")
    enemy = StubEnemy(position=(10, 13), hp=2)  # 直线距离 3
    turn = StubTurn(
        core=StubCore(position=(10, 10)),
        rangers=[ranger],
        visible_enemies=[enemy],
    )
    plan = assign_roles(turn, config=config)
    logs = command_rangers(turn, plan, config=config)
    assert ranger.action in ("shoot", "shoot_cell")
    assert any("shoot" in line for line in logs)


def test_core_heals_when_low_hp(config: TacticConfig) -> None:
    core = StubCore(position=(10, 10), hp=2, shield=5)
    turn = StubTurn(resources=5, core=core)
    assert should_core_heal_first(turn, config=config) is True
    acted, logs = command_core_defense(turn, config=config)
    assert acted is True
    assert core.action == "heal"
    assert any("heal" in line for line in logs)


def test_core_repairs_shield(config: TacticConfig) -> None:
    # HP 健康但盾低
    core = StubCore(position=(10, 10), hp=5, shield=1)
    turn = StubTurn(resources=5, core=core)
    # shield_threshold 默认 2，shield=1 → 应修盾
    acted, logs = command_core_defense(turn, config=config)
    assert acted is True
    assert core.action == "repair_shield"


def test_core_no_heal_when_full(config: TacticConfig) -> None:
    core = StubCore(position=(10, 10), hp=5, shield=5)
    turn = StubTurn(resources=10, core=core)
    assert should_core_heal_first(turn, config=config) is False
    acted, _ = command_core_defense(turn, config=config)
    assert acted is False
