"""经济模块单测：生产优先级、维护费、采集交付。"""

from __future__ import annotations

from uuid import uuid4

from bot.config import TacticConfig, population_upkeep
from bot.economy import can_afford, choose_spawn, command_workers
from bot.roles import RolePlan, assign_roles
from tests.stubs import StubCore, StubEnemy, StubTurn, StubUnit


def test_population_upkeep_tiers() -> None:
    assert population_upkeep(0) == 0
    assert population_upkeep(19) == 0
    assert population_upkeep(20) == 1
    assert population_upkeep(39) == 1
    assert population_upkeep(40) == 3
    assert population_upkeep(60) == 6


def test_can_afford_respects_reserve() -> None:
    assert can_afford(10, 3, 2) is True
    # 5-3=2 >= 2 → True（reserve 恰好满足）
    assert can_afford(5, 3, 2) is True
    assert can_afford(4, 3, 2) is False
    assert can_afford(5, 3, 2) is True


def test_spawn_prefers_worker_when_short(config: TacticConfig) -> None:
    turn = StubTurn(
        resources=20,
        core=StubCore(),
        workers=[StubUnit(unit_type="WORKER")],
        vanguards=[],
        rangers=[],
    )
    choice = choose_spawn(turn, config=config)
    assert choice == "WORKER"


def test_spawn_skips_when_resources_low(config: TacticConfig) -> None:
    turn = StubTurn(
        resources=2,  # reserve=2, worker cost 3 → 不够
        core=StubCore(),
        workers=[],
    )
    # resources 2, cost 3, reserve 2 → 2-3=-1 < 2
    choice = choose_spawn(turn, config=config)
    assert choice is None


def test_spawn_stops_near_upkeep_cap(config: TacticConfig) -> None:
    """人口接近 hard cap 时停止常规扩军。"""
    workers = [StubUnit(unit_type="WORKER") for _ in range(12)]
    vanguards = [StubUnit(unit_type="VANGUARD", hp=4) for _ in range(4)]
    rangers = [StubUnit(unit_type="RANGER") for _ in range(3)]
    # pop = 19
    turn = StubTurn(
        resources=50,
        core=StubCore(),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
    )
    turn.state.population = 19
    turn.state.upkeep_next_tick = 0
    choice = choose_spawn(turn, config=config, has_near_threat=False)
    assert choice is None


def test_spawn_vanguard_on_near_threat(config: TacticConfig) -> None:
    turn = StubTurn(
        resources=30,
        core=StubCore(position=(10, 10)),
        workers=[StubUnit(unit_type="WORKER") for _ in range(4)],
        vanguards=[],
        rangers=[],
        visible_enemies=[StubEnemy(position=(12, 10), hp=4)],
    )
    # workers 已达 target_workers=4，有近威胁且无战斗单位 → Vanguard
    choice = choose_spawn(
        turn, config=config, has_near_threat=True, has_far_threat=False
    )
    assert choice == "VANGUARD"


def test_worker_harvest_on_resource(config: TacticConfig) -> None:
    worker = StubUnit(position=(12, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells={(12, 10)},
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config)
    assert worker.action == "harvest"
    assert any("harvest" in line for line in logs)


def test_worker_deposit_when_cargo_at_core(config: TacticConfig) -> None:
    worker = StubUnit(position=(10, 10), cargo=1, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
    )
    plan = assign_roles(turn, config=config)
    command_workers(turn, plan, config=config)
    assert worker.action == "deposit"


def test_worker_returns_with_cargo(config: TacticConfig) -> None:
    worker = StubUnit(position=(15, 10), cargo=1, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells={(20, 10)},
    )
    plan = assign_roles(turn, config=config)
    command_workers(turn, plan, config=config)
    assert worker.action == "move"
    # 应向 Core（左）移动
    direction = str(worker.action_args)
    assert "LEFT" in direction or direction == "LEFT"


def test_worker_retreats_from_enemy(config: TacticConfig) -> None:
    worker = StubUnit(position=(14, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells={(20, 10)},
        visible_enemies=[StubEnemy(position=(15, 10))],  # 邻格威胁
    )
    plan = assign_roles(turn, config=config)
    assert plan.assignments[0].role.value == "retreat"
    command_workers(turn, plan, config=config)
    assert worker.action == "move"


def test_worker_explores_despite_far_enemy(config: TacticConfig) -> None:
    """远距离敌人（> retreat_adjacent）不得触发 retreat，应继续 explore/to_resource。"""
    # worker (14,10), enemy (20,10), core (10,10)
    # manhattan(worker, enemy)=6 > retreat_adjacent(2)
    # enemy 距 core=10 > threat_radius(8) → 非 near threat
    worker = StubUnit(position=(14, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),  # 无资源 → explore
        visible_enemies=[StubEnemy(position=(20, 10))],
    )
    plan = assign_roles(turn, config=config)
    assert plan.assignments[0].role.value == "harvester"
    assert plan.assignments[0].role.value != "retreat"
    logs = command_workers(turn, plan, config=config)
    assert worker.action == "move"
    assert not any(":retreat:" in line for line in logs)
    assert any(
        "explore" in line or "to_resource" in line for line in logs
    ), f"expected explore/to_resource, got {logs}"


def test_worker_retreats_with_cargo_near_enemy(config: TacticConfig) -> None:
    """满货 + 距敌 ≤ retreat_radius 时仍应 retreat（保护货物）。"""
    # worker (17,10) cargo=1, enemy (20,10): dist=3 ≤ retreat_radius(3)
    # cargo=0 时 dist=3 > adjacent(1) 不会撤；满货应撤
    worker = StubUnit(position=(17, 10), cargo=1, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells={(30, 10)},
        visible_enemies=[StubEnemy(position=(20, 10))],
    )
    plan = assign_roles(turn, config=config)
    assert plan.assignments[0].role.value == "retreat"
    command_workers(turn, plan, config=config)
    assert worker.action == "move"


def test_two_workers_explore_different_directions(config: TacticConfig) -> None:
    """无可见资源时，两 worker 不得冲向同一固定格（修复振荡）。"""
    from bot.pathing import explore_target

    wa = StubUnit(position=(10, 10), cargo=0, unit_type="WORKER")
    wb = StubUnit(position=(10, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=3,
        core=StubCore(position=(10, 10)),
        workers=[wa, wb],
        resource_cells=set(),
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config)
    dirs = []
    for line in logs:
        if ":explore:" not in line:
            continue
        parts = line.split(":")
        try:
            ei = parts.index("explore")
            dirs.append(parts[ei + 1])
        except (ValueError, IndexError):
            dirs.append(line.rsplit(":", 1)[-1])
    assert wa.action == "move"
    assert wb.action == "move"
    assert len(dirs) == 2
    assert dirs[0] != dirs[1], f"both explored same dir: {dirs}"
    assert explore_target((10, 10), 0) != explore_target((10, 10), 1)


def test_early_spawn_ignores_reserve(config: TacticConfig) -> None:
    """pop < early_game_pop 时 reserve 视为 0，resources>=3 可出 WORKER。"""
    from bot.economy import effective_reserve

    assert effective_reserve(0, config) == 0
    assert effective_reserve(config.early_game_pop - 1, config) == 0
    assert effective_reserve(config.early_game_pop, config) == config.reserve_resources

    turn = StubTurn(resources=3, core=StubCore(), workers=[])
    assert choose_spawn(turn, config=config) == "WORKER"

    # 非早期：pop>=early 且仍缺 worker，reserve 生效，3 资源不够
    cfg_mid = TacticConfig(
        max_population=18,
        target_workers=12,
        target_vanguards=2,
        target_rangers=1,
        reserve_resources=2,
        early_game_pop=4,
    )
    mid = StubTurn(
        resources=3,
        core=StubCore(),
        workers=[StubUnit(unit_type="WORKER") for _ in range(4)],
    )
    assert choose_spawn(mid, config=cfg_mid) is None


_DIR_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def test_worker_recall_when_too_far(config: TacticConfig) -> None:
    """Worker 距 Core 远超探索上限（跑飞）→ 回撤靠近 Core，日志标 :recall。

    场景：worker (50,10)、core (10,10)，dist_core=40 > explore_max_radius+4=36。
    应触发回撤：朝 Core 方向移动使 manhattan(新位置, core) < 40，或日志含 :recall。
    """
    from bot.pathing import manhattan

    worker = StubUnit(position=(50, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    assert plan.assignments[0].role.value == "harvester"

    logs = command_workers(turn, plan, config=config)
    assert worker.action == "move"
    direction = str(worker.action_args)
    dx, dy = _DIR_DELTA[direction]
    new_pos = (50 + dx, 10 + dy)
    assert manhattan(new_pos, (10, 10)) < 40 or any(
        ":recall" in line for line in logs
    ), f"expected recall toward core, got dir={direction} logs={logs}"


def test_worker_explore_respects_max_radius(config: TacticConfig) -> None:
    """探索不朝 Core 收缩：worker at (30,10)（dist=20 < 32）方向不减距 Core 距离。

    断言 explore 方向使 manhattan(nxt, core) >= 20（不朝 Core 收缩）。
    """
    from bot.pathing import manhattan

    worker = StubUnit(position=(30, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    assert plan.assignments[0].role.value == "harvester"

    logs = command_workers(turn, plan, config=config)
    assert worker.action == "move"
    direction = str(worker.action_args)
    dx, dy = _DIR_DELTA[direction]
    new_pos = (30 + dx, 10 + dy)
    assert manhattan(new_pos, (10, 10)) >= 20, (
        f"explore shrank toward core: dir={direction} new_pos={new_pos} logs={logs}"
    )
