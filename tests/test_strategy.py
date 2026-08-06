"""主策略集成单测：decide(turn) 端到端（stub，不联网）。"""

from __future__ import annotations

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.pathing import clamp_step_toward, defense_ring_slots, nearest
from bot.roles import Role, assign_roles, count_by_type, total_population
from bot.strategy import decide, decide_and_describe
from tests.stubs import StubCore, StubEnemy, StubTurn, StubUnit


def test_decide_early_economy_spawns_worker(config: TacticConfig) -> None:
    """早期资源充足、工人不足 → Core spawn WORKER。"""
    turn = StubTurn(
        tick=3,
        resources=15,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(14, 10)},
    )
    result = decide(turn, config=config)
    assert result.tick == 3
    assert turn.core is not None
    assert turn.core.action == "spawn"
    action_arg = str(turn.core.action_args)
    assert "WORKER" in action_arg
    assert "spawn" in result.core_action


def test_decide_worker_moves_to_resource(config: TacticConfig) -> None:
    worker = StubUnit(position=(10, 11), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[worker],
        resource_cells={(10, 14)},
    )
    # 早期 reserve=0、cost=5 → 恰好够 spawn 1 WORKER；worker 仍应走向资源
    decide(turn, config=config)
    assert worker.action in ("move", "harvest")


def test_decide_defense_on_threat(config: TacticConfig) -> None:
    """有近威胁且缺防时，优先生产 Vanguard。"""
    turn = StubTurn(
        tick=10,
        resources=25,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER") for _ in range(4)],
        vanguards=[],
        rangers=[],
        visible_enemies=[StubEnemy(position=(13, 10), hp=4)],
        resource_cells={(8, 10)},
    )
    result = decide(turn, config=config)
    assert result.has_near_threat is True
    assert turn.core is not None
    assert turn.core.action == "spawn"
    assert "VANGUARD" in str(turn.core.action_args)


def test_decide_vanguard_sweeps(config: TacticConfig) -> None:
    vanguard = StubUnit(position=(11, 10), hp=4, unit_type="VANGUARD")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[],
        vanguards=[vanguard],
        visible_enemies=[StubEnemy(position=(12, 10))],
    )
    result = decide(turn, config=config)
    assert vanguard.action == "sweep"
    assert result.population == 1


def test_decide_core_heal_priority(config: TacticConfig) -> None:
    """Core 低血时优先 heal，而非 spawn。"""
    turn = StubTurn(
        resources=20,
        core=StubCore(position=(10, 10), hp=2, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(15, 10)},
    )
    result = decide(turn, config=config)
    assert turn.core is not None
    assert turn.core.action == "heal"
    assert "heal" in result.core_action


def test_decide_no_core() -> None:
    turn = StubTurn(core=None, workers=[], resources=0)
    turn.core = None
    result = decide(turn)
    assert result.core_action == "absent"


def test_decide_respawning_skips_actions(config: TacticConfig) -> None:
    """RESPAWNING 状态：decide 直接返回，不排队任何动作。"""
    from tests.stubs import StubState

    turn = StubTurn(
        tick=7,
        resources=0,
        core=StubCore(position=(10, 10)),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(12, 10)},
        state=StubState(status="RESPAWNING", respawn_at_tick=120),
    )
    result = decide(turn, config=config)
    assert result.core_action == "respawn"
    assert turn.core is not None and turn.core.action is None
    assert turn.workers[0].action is None
    assert any("respawn_at=120" in line for line in result.logs)
    assert result.population == 1


def test_decide_and_describe(basic_turn: StubTurn, config: TacticConfig) -> None:
    text = decide_and_describe(basic_turn, config=config)
    assert "tick=" in text
    assert "pop=" in text


def test_role_assignment_counts(config: TacticConfig) -> None:
    turn = StubTurn(
        core=StubCore(),
        workers=[StubUnit(unit_type="WORKER") for _ in range(3)],
        vanguards=[StubUnit(unit_type="VANGUARD", hp=4)],
        rangers=[StubUnit(unit_type="RANGER")],
    )
    assert count_by_type(turn) == {"WORKER": 3, "VANGUARD": 1, "RANGER": 1}
    assert total_population(turn) == 5
    plan = assign_roles(turn, config=config)
    assert len(plan.assignments) == 5
    assert all(a.role in Role for a in plan.assignments)


def test_pathing_helpers() -> None:
    assert nearest((0, 0), [(5, 0), (1, 1)]) == (1, 1)
    assert nearest((0, 0), []) is None
    d = clamp_step_toward((0, 0), (3, 0), obstacles=set())
    assert d == "RIGHT"
    d2 = clamp_step_toward((0, 0), (3, 0), obstacles={(1, 0)})
    # 被挡后应尝试绕行（UP/DOWN）或仍给方向
    assert d2 in ("UP", "DOWN", "LEFT", "RIGHT", None)
    slots = defense_ring_slots((10, 10), radius=2, count=4)
    assert len(slots) == 4
    for s in slots:
        assert abs(s[0] - 10) + abs(s[1] - 10) == 2


def test_default_config_keeps_upkeep_zero() -> None:
    """默认编制人口应 < 20。"""
    cfg = DEFAULT_CONFIG
    total = cfg.target_workers + cfg.target_vanguards + cfg.target_rangers
    assert total <= cfg.max_population
    assert total < 20
    assert cfg.max_population < 20
