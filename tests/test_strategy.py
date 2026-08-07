"""主策略集成单测：decide(turn) 端到端（stub，不联网）。"""

from __future__ import annotations

from typing import Any

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


def test_decide_memory_injection_smoke(config: TacticConfig) -> None:
    """decide 注入干净 memory：observe 生效、回访候选驱动采集。"""
    from bot.memory import MemoryMap

    mem = MemoryMap(refresh_interval_ticks=4)
    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[worker],
        resource_cells={(14, 10)},
    )
    result = decide(turn, config=config, memory=mem)
    # observe 已记录资源点
    assert (14, 10) in mem.resource_points
    # worker 走向可见资源（move）
    assert worker.action in ("move", "harvest")
    assert "threat:" in "".join(result.logs)


def test_decide_memory_observe_updates_on_tick(config: TacticConfig) -> None:
    """decide 每 tick observe：资源点消失后进入 DEPLETED。"""
    from bot.memory import DEPLETED, MemoryMap

    mem = MemoryMap(refresh_interval_ticks=4)
    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    core = StubCore(position=(10, 10), hp=5, shield=5)
    t1 = StubTurn(tick=1, resources=5, core=core, workers=[worker],
                  resource_cells={(14, 10)})
    decide(t1, config=config, memory=mem)
    assert mem.resource_points[(14, 10)].state == "VISIBLE"

    t2 = StubTurn(tick=2, resources=5, core=core, workers=[worker],
                  resource_cells=set())
    decide(t2, config=config, memory=mem)
    assert mem.resource_points[(14, 10)].state == DEPLETED


def test_decide_syncs_beacon_ground(config: TacticConfig) -> None:
    """P2-1：turn.beacon GROUND → config.beacon_position 写入位置。"""
    from tests.stubs import StubBeacon

    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[worker],
        resource_cells={(14, 10)},
        beacon=StubBeacon(position=(50, 10), status="GROUND", carrier_id=None),
    )
    result = decide(turn, config=config)
    assert config.beacon_position == (50, 10)
    assert any(
        "strategy:beacon:pos=(50, 10)" in line for line in result.logs
    ), result.logs


def test_decide_clears_beacon_when_carried(config: TacticConfig) -> None:
    """P2-1：CARRIED → beacon_position 清 None（停止向旧位置推进）。"""
    from bot.config import set_beacon_position
    from tests.stubs import StubBeacon

    set_beacon_position(config, (50, 10))  # 先模拟上一 tick 有 beacon
    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[worker],
        resource_cells={(14, 10)},
        beacon=StubBeacon(
            position=(50, 10), status="CARRIED", carrier_id=worker.id
        ),
    )
    result = decide(turn, config=config)
    assert config.beacon_position is None
    assert any("strategy:beacon:cleared" in line for line in result.logs)


def test_decide_clears_beacon_when_absent(config: TacticConfig) -> None:
    """P2-1：turn 无 beacon → beacon_position 清 None。"""
    from bot.config import set_beacon_position

    set_beacon_position(config, (50, 10))
    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[worker],
        resource_cells={(14, 10)},
        beacon=None,
    )
    result = decide(turn, config=config)
    assert config.beacon_position is None
    assert any("strategy:beacon:absent" in line for line in result.logs)


def test_beacon_status_parsing(config: TacticConfig) -> None:
    """SDK 0.2.9 BeaconStatus 解析：GROUND/None → 写位置；CARRIED → 清。

    真实 SDK 的 `ChampionBeacon.status` 是 `BeaconStatus`(StrEnum) | None
    （`BeaconStatus.GROUND.value == "GROUND"`、`CARRIED` 必有 carrier_id、
    `status=None` 表示位置公开且未被拾取）。stub 用字符串、真实 SDK 用枚举，
    两者都必须正确写/清 `config.beacon_position`。
    """
    from bot.config import set_beacon_position
    from tests.stubs import StubBeacon

    try:
        from arena_hero import BeaconStatus  # type: ignore
    except Exception:
        BeaconStatus = None  # type: ignore[assignment]

    ground_values: list[Any] = ["GROUND"]
    if BeaconStatus is not None:
        ground_values.append(BeaconStatus.GROUND)
    carried_values: list[Any] = ["CARRIED"]
    if BeaconStatus is not None:
        carried_values.append(BeaconStatus.CARRIED)

    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    core = StubCore(position=(10, 10), hp=5, shield=5)

    # GROUND 与 status=None → 写位置（status=None：位置公开、非 CARRIED）
    for status_val in ground_values + [None]:
        set_beacon_position(config, None)
        turn = StubTurn(
            tick=1,
            resources=5,
            core=core,
            workers=[worker],
            resource_cells={(14, 10)},
            beacon=StubBeacon(
                position=(50, 10), status=status_val, carrier_id=None
            ),
        )
        result = decide(turn, config=config)
        assert config.beacon_position == (50, 10), (
            f"status={status_val!r} should write beacon position"
        )
        assert any(
            "strategy:beacon:pos=(50, 10)" in line for line in result.logs
        ), f"status={status_val!r} logs={result.logs}"

    # CARRIED（字符串或真实枚举）→ 清 None
    for status_val in carried_values:
        set_beacon_position(config, (50, 10))
        turn = StubTurn(
            tick=1,
            resources=5,
            core=core,
            workers=[worker],
            resource_cells={(14, 10)},
            beacon=StubBeacon(
                position=(50, 10), status=status_val, carrier_id=worker.id
            ),
        )
        result = decide(turn, config=config)
        assert config.beacon_position is None, (
            f"status={status_val!r} should clear beacon position"
        )
        assert any("strategy:beacon:cleared" in line for line in result.logs)


def test_decide_beacon_multi_tick_sync(config: TacticConfig) -> None:
    """P2-1：多 tick 连续同步——GROUND 写、CARRIED 清、再 GROUND 写。"""
    from bot.config import set_beacon_position
    from tests.stubs import StubBeacon

    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    core = StubCore(position=(10, 10), hp=5, shield=5)

    t1 = StubTurn(
        tick=1, resources=5, core=core, workers=[worker],
        resource_cells={(14, 10)},
        beacon=StubBeacon(position=(50, 10), status="GROUND", carrier_id=None),
    )
    decide(t1, config=config)
    assert config.beacon_position == (50, 10)

    t2 = StubTurn(
        tick=2, resources=5, core=core, workers=[worker],
        resource_cells={(14, 10)},
        beacon=StubBeacon(
            position=(50, 10), status="CARRIED", carrier_id=worker.id
        ),
    )
    decide(t2, config=config)
    assert config.beacon_position is None

    t3 = StubTurn(
        tick=3, resources=5, core=core, workers=[worker],
        resource_cells={(14, 10)},
        beacon=StubBeacon(position=(60, 12), status="GROUND", carrier_id=None),
    )
    decide(t3, config=config)
    assert config.beacon_position == (60, 12)


def test_default_config_new_defaults() -> None:
    """v0.14 优化默认值：编制 ≤ 人口上限；新增螺旋/记忆参数；无 upkeep 字段。"""
    cfg = DEFAULT_CONFIG
    total = cfg.target_workers + cfg.target_vanguards + cfg.target_rangers
    assert total <= cfg.max_population
    assert cfg.max_population == 30
    assert cfg.target_workers == 14
    assert cfg.sector_count == 2
    assert cfg.spiral_base_ring == 3
    assert cfg.spiral_max_ring == 32
    assert cfg.recall_stall_ticks == 6
    assert cfg.refresh_interval_ticks == 4
    assert cfg.revisit_max_distance == 40
    # Beacon 导向探索新增字段（探索优化 T01）
    assert cfg.beacon_step_radius == 8
    assert cfg.beacon_position is None
    # v0.14 已移除维护费与固定成本字段
    assert not hasattr(cfg, "upkeep_soft_cap")
    assert not hasattr(cfg, "upkeep_hard_cap")
    assert not hasattr(cfg, "worker_cost")
