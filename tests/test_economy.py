"""经济模块单测：生产优先级、动态单位价格、采集交付、记忆回访。"""

from __future__ import annotations

from bot.config import TacticConfig
from bot.economy import can_afford, choose_spawn, command_workers
from bot.memory import MemoryMap
from bot.roles import assign_roles
from bot.rules import unit_cost_for
from tests.stubs import StubCore, StubEnemy, StubTurn, StubUnit


def test_unit_cost_dynamic_boundaries() -> None:
    """动态价格：pop 19→20 涨价边界（Worker 5→7）。"""
    assert unit_cost_for("WORKER", 19) == 5
    assert unit_cost_for("WORKER", 20) == 7
    # vanguard/ranger 同步涨价
    assert unit_cost_for("VANGUARD", 20) == 13
    assert unit_cost_for("RANGER", 20) == 16


def test_can_afford_respects_reserve() -> None:
    assert can_afford(10, 5, 2) is True
    # 7-5=2 >= 2 → True（reserve 恰好满足）
    assert can_afford(7, 5, 2) is True
    assert can_afford(6, 5, 2) is False
    assert can_afford(7, 5, 2) is True


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
        resources=2,  # reserve=2, worker cost 5 → 不够
        core=StubCore(),
        workers=[],
    )
    # resources 2, cost 5, reserve 2 → 2-5=-3 < 2
    choice = choose_spawn(turn, config=config)
    assert choice is None


def test_spawn_dynamic_cost_at_pop_boundary(config: TacticConfig) -> None:
    """pop 19→20 边界：spawn 成本按 pop+1=20 计（Worker 7），reserve 保留。

    pop=19、resources=9、reserve=2 → 9-7=2 ≥ 2 可出；resources=8 → 不可。
    不再有维护费阻断：pop≥20 也能按真实成本理性 spawn。
    """
    cfg = TacticConfig(
        max_population=30,
        target_workers=14,
        target_vanguards=3,
        target_rangers=2,
        reserve_resources=2,
        early_game_pop=4,
    )
    workers = [StubUnit(unit_type="WORKER") for _ in range(12)]
    vanguards = [StubUnit(unit_type="VANGUARD", hp=4) for _ in range(4)]
    rangers = [StubUnit(unit_type="RANGER") for _ in range(3)]
    # pop = 19（12+4+3）
    turn = StubTurn(
        resources=9,
        core=StubCore(),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
    )
    turn.state.population = 19
    assert unit_cost_for("WORKER", 20) == 7
    # 9 - 7 = 2 ≥ reserve(2) → 可出 WORKER（cost 按 spawn 后人口 20）
    choice = choose_spawn(turn, config=cfg, has_near_threat=False)
    assert choice == "WORKER"

    turn_low = StubTurn(
        resources=8,
        core=StubCore(),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
    )
    turn_low.state.population = 19
    # 8 - 7 = 1 < reserve(2) → 不可
    assert choose_spawn(turn_low, config=cfg, has_near_threat=False) is None


def test_spawn_allows_pop_over_20_with_dynamic_cost(config: TacticConfig) -> None:
    """pop≥20 不再被维护费逻辑阻止：按动态价格可继续 spawn。"""
    cfg30 = TacticConfig(
        max_population=30,
        target_workers=14,
        target_vanguards=3,
        target_rangers=2,
        reserve_resources=2,
        early_game_pop=4,
    )
    # pop = 20：13 worker（仍缺 1 个目标）+ 5 vanguard + 2 ranger
    workers = [StubUnit(unit_type="WORKER") for _ in range(13)]
    vanguards = [StubUnit(unit_type="VANGUARD", hp=4) for _ in range(5)]
    rangers = [StubUnit(unit_type="RANGER") for _ in range(2)]
    turn = StubTurn(
        resources=50,
        core=StubCore(),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
    )
    turn.state.population = 20
    # pop+1=21 时 Worker 成本仍为 7；50-7 远超 reserve → 可继续出
    assert unit_cost_for("WORKER", 21) == 7
    assert choose_spawn(turn, config=cfg30, has_near_threat=False) == "WORKER"


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


def test_two_workers_explore_different_sectors(config: TacticConfig) -> None:
    """无可见资源时，两 worker 分属不同扇区（螺旋目标不同，防扎堆）。

    新螺旋扫掠按 sector_id = worker_index % sector_count 分扇区；
    首 tick 方向可能相同（都朝各自环内目标），但 spiral_target 必须不同，
    且不触发旧 recall 边界。
    """
    from bot.pathing import spiral_target

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
    assert wa.action == "move"
    assert wb.action == "move"
    assert not any(":recall" in line for line in logs), logs
    # 两个 worker 的螺旋目标不同（扇区不同）
    t0 = spiral_target((10, 10), 0, config.sector_count, config.spiral_base_ring, 0)
    t1 = spiral_target((10, 10), 1, config.sector_count, config.spiral_base_ring, 0)
    assert t0 != t1, f"same sector target: {t0}"
    # 日志含螺旋字段
    assert all(":ring=" in line and ":sec=" in line for line in logs if ":explore:" in line)


def test_early_spawn_ignores_reserve(config: TacticConfig) -> None:
    """pop < early_game_pop 时 reserve 视为 0，resources>=5 可出 WORKER。"""
    from bot.economy import effective_reserve

    assert effective_reserve(0, config) == 0
    assert effective_reserve(config.early_game_pop - 1, config) == 0
    assert effective_reserve(config.early_game_pop, config) == config.reserve_resources

    turn = StubTurn(resources=5, core=StubCore(), workers=[])
    assert choose_spawn(turn, config=config) == "WORKER"

    # 非早期：pop>=early 且仍缺 worker，reserve 生效，5 资源不够
    cfg_mid = TacticConfig(
        max_population=18,
        target_workers=12,
        target_vanguards=2,
        target_rangers=1,
        reserve_resources=2,
        early_game_pop=4,
    )
    mid = StubTurn(
        resources=5,
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


def test_worker_explore_navigates_to_spiral_target(config: TacticConfig) -> None:
    """探索改目标点导航：worker (30,10)（d=20）朝其螺旋目标走，逐步接近。

    新逻辑删除「绝不朝 Core 收缩」守卫——允许目标点导航暂时降低距 Core
    距离（切向扫掠），但每一步必须缩短与螺旋目标的曼哈顿距离（有进展）。
    """
    from bot.economy import _spiral_state
    from bot.pathing import manhattan, spiral_target

    _spiral_state.clear()
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

    # worker 下标 0 → sector 0，ring=base，index=0
    target = spiral_target((10, 10), 0, config.sector_count, config.spiral_base_ring, 0)
    pos = worker.position
    dist_before = manhattan(pos, target)
    logs = command_workers(turn, plan, config=config)
    assert worker.action == "move"
    direction = str(worker.action_args)
    dx, dy = _DIR_DELTA[direction]
    new_pos = (30 + dx, 10 + dy)
    dist_after = manhattan(new_pos, target)
    assert dist_after < dist_before, (
        f"did not progress toward spiral target: dir={direction} new={new_pos} "
        f"target={target} logs={logs}"
    )
    assert not any(":recall_soft" in line for line in logs), logs


def test_worker_return_deposit_avoids_oscillation(config: TacticConfig) -> None:
    """Worker 在 Core 正下方 3 格、中间 (10,11) 有障碍：连续 6 tick 不得横跳。

    旧 clamp_step_toward 会 return_deposit:DOWN↔UP 无限对抖（线上症状），
    带方向记忆的去抖寻路应绕行靠近 Core。
    """
    from bot.economy import _last_move_dir
    from bot.pathing import manhattan

    _last_move_dir.clear()  # 清空模块级方向记忆，保证从干净状态开始

    core_pos = (10, 10)
    obstacles = {(10, 11)}
    start = (10, 12)
    worker = StubUnit(position=start, cargo=1, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        obstacle_cells=obstacles,
    )
    plan = assign_roles(turn, config=config)

    dirs: list[str] = []
    pos = start
    for _ in range(6):
        worker.clear_action()
        command_workers(turn, plan, config=config)
        if worker.action != "move":
            break  # 到达 Core 后 deposit，模拟结束
        direction = str(worker.action_args)
        dirs.append(direction)
        dx, dy = _DIR_DELTA[direction]
        pos = (pos[0] + dx, pos[1] + dy)
        worker.position = pos  # 推进 worker 位置，模拟真实 tick 移动

    # 不出现 UP/DOWN 或 LEFT/RIGHT 严格交替超过 2 次
    max_run = 0
    run = 0
    for prev, cur in zip(dirs, dirs[1:]):
        if _DIR_DELTA[cur] == tuple(-v for v in _DIR_DELTA[prev]):
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    assert max_run <= 2, f"return_deposit oscillation dirs={dirs} pos={pos}"

    # 绕行靠近 Core：6 tick 后距 Core 严格小于起点距 Core（=2）
    assert manhattan(pos, core_pos) < manhattan(start, core_pos), (
        f"did not approach core: pos={pos} dirs={dirs}"
    )


def test_worker_recall_boundary_no_oscillation(config: TacticConfig) -> None:
    """d=36/37 势阱修复：worker 在 Core 右侧 d=37，连续 10 tick 无重复位置。

    新逻辑为目标点导航（无 recall_dist 硬边界）：worker 朝螺旋目标行进，
    不再 RIGHT↔LEFT 横跳，且整体朝 Core 方向推进（x 递减）。
    """
    from bot.economy import _last_move_dir, _spiral_state
    from bot.pathing import manhattan

    _spiral_state.clear()
    _last_move_dir.clear()

    core_pos = (10, 10)
    worker = StubUnit(position=(47, 10), cargo=0, unit_type="WORKER")  # d=37
    turn = StubTurn(
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)

    seen: set[tuple[int, int]] = set()
    pos = worker.position
    left_steps = 0
    for _ in range(10):
        assert pos not in seen, f"position revisited (A↔B 对抖): {pos}"
        seen.add(pos)
        worker.clear_action()
        command_workers(turn, plan, config=config)
        if worker.action != "move":
            break
        direction = str(worker.action_args)
        dx, dy = _DIR_DELTA[direction]
        pos = (pos[0] + dx, pos[1] + dy)
        worker.position = pos
        if dx < 0:
            # 朝 Core 方向推进（worker 在 Core 右侧 → x 递减）
            left_steps += 1

    assert left_steps >= 5, "应持续朝 Core/目标方向推进，而非横跳"
    assert manhattan(worker.position, core_pos) <= manhattan((47, 10), core_pos), (
        f"explore moved away from core: pos={worker.position}"
    )


def test_explore_spiral_advances_ring_after_target_reached(
    config: TacticConfig,
) -> None:
    """到达螺旋目标后 index+1 / 扫完本环 ring+1。"""
    from bot.economy import _spiral_state
    from bot.pathing import spiral_target

    _spiral_state.clear()
    core_pos = (10, 10)
    # sector 0 的目标点 (6,9)：让 worker 从目标点开始，第一 tick 应推进 index
    target = spiral_target(core_pos, 0, config.sector_count, config.spiral_base_ring, 0)
    worker = StubUnit(position=target, cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    command_workers(turn, plan, config=config)
    st = _spiral_state[str(worker.id)]
    # 已从 index=0 推进到 index=1（ring 不变，sector 0 环 5 有 5 个点）
    assert st.index == 1
    assert st.ring == config.spiral_base_ring
    assert st.target != target


def test_explore_soft_retreat_after_stall(config: TacticConfig) -> None:
    """连续 recall_stall_ticks 无进展 → 软回撤（换目标 / ring-1），而非横跳。"""
    from bot.economy import _last_move_dir, _spiral_state

    _spiral_state.clear()
    _last_move_dir.clear()
    core_pos = (10, 10)
    # worker (20,10) 四向被障碍围死 → 目标点不可达 → 持续 stall
    worker = StubUnit(position=(20, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        obstacle_cells={(19, 10), (20, 9), (20, 11), (21, 10)},
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    saw_soft_recall = False
    for _ in range(config.recall_stall_ticks + 3):
        worker.clear_action()
        logs = command_workers(turn, plan, config=config)
        if any(":recall_soft" in line for line in logs):
            saw_soft_recall = True
            break
    assert saw_soft_recall, f"expected soft recall after stall, logs={logs}"


def test_explore_absolute_safety_net(config: TacticConfig) -> None:
    """绝对安全网：d > spiral_max_ring + 8 时直接朝 Core 一步。"""
    from bot.economy import _last_move_dir, _spiral_state
    from bot.pathing import manhattan

    _spiral_state.clear()
    _last_move_dir.clear()
    core_pos = (10, 10)
    start = (55, 10)  # d=45 > 32+8=40
    worker = StubUnit(position=start, cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config)
    assert worker.action == "move"
    direction = str(worker.action_args)
    dx, dy = _DIR_DELTA[direction]
    new_pos = (start[0] + dx, start[1] + dy)
    assert manhattan(new_pos, core_pos) < manhattan(start, core_pos), (
        f"expected recall_soft toward core: dir={direction} logs={logs}"
    )
    assert any(":recall_soft" in line for line in logs), logs


def test_worker_harvest_marks_memory(config: TacticConfig) -> None:
    """采集成功后 mark_harvested → 记忆资源点进入 DEPLETED。"""
    mem = MemoryMap(refresh_interval_ticks=4)
    worker = StubUnit(position=(12, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells={(12, 10)},
    )
    mem.observe(turn, 1)  # 资源点入库
    plan = assign_roles(turn, config=config)
    command_workers(turn, plan, config=config, memory=mem)
    assert worker.action == "harvest"
    rp = mem.resource_points[(12, 10)]
    assert rp.state == "DEPLETED"
    assert rp.refresh_due_tick == 1 + 4


def test_worker_goes_to_revisit_candidate_from_memory(config: TacticConfig) -> None:
    """无可见资源时，记忆 REVISIT_DUE 候选优先于螺旋探索。"""
    from bot.memory import DEPLETED, REVISIT_DUE

    mem = MemoryMap(refresh_interval_ticks=4)
    core_pos = (10, 10)
    rp = (14, 10)  # dist 4
    # 构造：tick1 可见 → tick2 消失(DEPLETED) → tick6 到期(REVISIT_DUE)
    t1 = StubTurn(tick=1, core=StubCore(position=core_pos), resource_cells={rp})
    mem.observe(t1, 1)
    t2 = StubTurn(tick=2, core=StubCore(position=core_pos), resource_cells=set())
    mem.observe(t2, 2)
    assert mem.resource_points[rp].state == DEPLETED
    t6 = StubTurn(tick=6, core=StubCore(position=core_pos), resource_cells=set())
    mem.observe(t6, 6)
    assert mem.resource_points[rp].state == REVISIT_DUE

    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=6,
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config, memory=mem)
    assert worker.action == "move"
    assert any(":to_resource:" in line for line in logs), logs
    assert not any(":explore:" in line for line in logs), logs


def test_worker_revisit_sector_preference(config: TacticConfig) -> None:
    """多 Worker 分工：回访候选按各自扇区优先，不扎堆同一资源点。"""
    from bot.memory import REVISIT_DUE

    mem = MemoryMap(refresh_interval_ticks=4, sector_count=4)
    core_pos = (10, 10)
    # ring 5 上两个不同扇区的点（与 pathing.sector_points 一致）
    s0_point = (6, 9)  # 扇区 0
    s1_point = (7, 8)  # 扇区 1
    for p in (s0_point, s1_point):
        t1 = StubTurn(tick=1, core=StubCore(position=core_pos), resource_cells={p})
        mem.observe(t1, 1)
        t2 = StubTurn(tick=2, core=StubCore(position=core_pos), resource_cells=set())
        mem.observe(t2, 2)
        t6 = StubTurn(tick=6, core=StubCore(position=core_pos), resource_cells=set())
        mem.observe(t6, 6)
        assert mem.resource_points[p].state == REVISIT_DUE

    wa = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")  # 下标 0 → sector 0
    wb = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")  # 下标 1 → sector 1
    turn = StubTurn(
        tick=6,
        resources=5,
        core=StubCore(position=core_pos),
        workers=[wa, wb],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    assert plan.get(wa.id).sector_id == 0
    assert plan.get(wb.id).sector_id == 1

    # 扇区过滤：各自只看到本扇区候选（防扎堆的机制来源）
    assert mem.revisit_candidates(core_pos, 6, (11, 10), 40, sector_id=0) == [s0_point]
    assert mem.revisit_candidates(core_pos, 6, (11, 10), 40, sector_id=1) == [s1_point]

    logs = command_workers(turn, plan, config=config, memory=mem)
    to_res = [line for line in logs if ":to_resource:" in line]
    # 两个 worker 都导航到记忆回访点（而非探索）
    assert len(to_res) == 2, logs
    assert wa.action == "move"
    assert wb.action == "move"


def test_beacon_pickup_ground(config: TacticConfig) -> None:
    """GROUND Beacon 与 worker 同格 → pickup_beacon。"""
    from tests.stubs import StubBeacon

    worker = StubUnit(position=(15, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        beacon=StubBeacon(position=(15, 10), status="GROUND", carrier_id=None),
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config)
    assert worker.action == "pickup_beacon"
    assert any("pickup_beacon" in line for line in logs), logs


def test_beacon_carrier_prefers_harvest_over_explore(config: TacticConfig) -> None:
    """Beacon 持有者（1 点 → 2 资源）：跳过扇区限制优先采集记忆回访点。"""
    from bot.memory import REVISIT_DUE
    from tests.stubs import StubBeacon

    mem = MemoryMap(refresh_interval_ticks=4, sector_count=4)
    core_pos = (10, 10)
    # 扇区 1 的资源点（worker 是扇区 0，若走扇区过滤会漏掉）
    s1_point = (7, 8)
    t1 = StubTurn(tick=1, core=StubCore(position=core_pos), resource_cells={s1_point})
    mem.observe(t1, 1)
    t2 = StubTurn(tick=2, core=StubCore(position=core_pos), resource_cells=set())
    mem.observe(t2, 2)
    t6 = StubTurn(tick=6, core=StubCore(position=core_pos), resource_cells=set())
    mem.observe(t6, 6)
    assert mem.resource_points[s1_point].state == REVISIT_DUE

    worker = StubUnit(position=(11, 10), cargo=0, unit_type="WORKER")  # sector 0
    turn = StubTurn(
        tick=6,
        resources=5,
        core=StubCore(position=core_pos),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
        beacon=StubBeacon(position=(20, 20), status="CARRIED", carrier_id=worker.id),
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config, memory=mem)
    # 持有者应导航到扇区 1 的回访点（to_resource），而非探索
    assert worker.action == "move"
    assert any(":to_resource:" in line for line in logs), logs
    assert not any(":explore:" in line for line in logs), logs


def test_cargo_reclaim_moves_to_dropped(config: TacticConfig) -> None:
    """空载 worker 优先前往掉落 cargo 回收。"""
    from tests.stubs import StubEvent

    mem = MemoryMap()
    events = [
        StubEvent(
            event_type="WORKER_CARGO_DROPPED",
            position=(30, 10),
            values={"amount": 3},
        )
    ]
    t0 = StubTurn(tick=1, core=StubCore(position=(10, 10)), events=events)
    mem.observe(t0, 1)
    assert (30, 10) in mem.dropped_cargo

    worker = StubUnit(position=(20, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=2,
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config, memory=mem)
    assert worker.action == "move"
    assert any(":to_cargo:" in line for line in logs), logs
    assert not any(":explore:" in line for line in logs), logs


def test_cargo_reclaim_on_cell_harvests_and_collects(config: TacticConfig) -> None:
    """worker 站在掉落 cargo 格 → harvest + 标记 collected。"""
    from tests.stubs import StubEvent

    mem = MemoryMap()
    events = [
        StubEvent(
            event_type="WORKER_CARGO_DROPPED",
            position=(15, 10),
            values={"amount": 2},
        )
    ]
    t0 = StubTurn(tick=1, core=StubCore(position=(10, 10)), events=events)
    mem.observe(t0, 1)

    worker = StubUnit(position=(15, 10), cargo=0, unit_type="WORKER")
    turn = StubTurn(
        tick=2,
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config, memory=mem)
    assert worker.action == "harvest"
    assert any(":reclaim_cargo" in line for line in logs), logs
    assert mem.dropped_cargo[(15, 10)].collected is True


def test_cargo_reclaim_ignored_when_full(config: TacticConfig) -> None:
    """满货 worker 不回收 cargo：优先回 Core 交付。"""
    from tests.stubs import StubEvent

    mem = MemoryMap()
    events = [
        StubEvent(
            event_type="WORKER_CARGO_DROPPED",
            position=(30, 10),
            values={"amount": 3},
        )
    ]
    t0 = StubTurn(tick=1, core=StubCore(position=(10, 10)), events=events)
    mem.observe(t0, 1)

    worker = StubUnit(position=(20, 10), cargo=1, unit_type="WORKER")
    turn = StubTurn(
        tick=2,
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[worker],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_workers(turn, plan, config=config, memory=mem)
    assert worker.action == "move"
    assert any(":return_deposit:" in line for line in logs), logs
    assert not any(":to_cargo:" in line for line in logs), logs
