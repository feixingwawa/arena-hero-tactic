"""离线内联测试（无需 pytest，网络受限时使用）。"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from bot.config import TacticConfig, DEFAULT_CONFIG, population_upkeep
from bot.pathing import (
    manhattan,
    is_in_range_cardinal_or_diag,
    clamp_step_toward,
    nearest,
    defense_ring_slots,
    explore_target,
    explore_targets,
)
from bot.economy import can_afford, choose_spawn, command_workers, effective_reserve
from bot.combat import (
    assess_threats,
    command_vanguards,
    command_rangers,
    command_core_defense,
    should_core_heal_first,
)
from bot.roles import assign_roles, count_by_type, total_population
from bot.strategy import decide, decide_and_describe
from tests.stubs import StubCore, StubEnemy, StubState, StubTurn, StubUnit

config = TacticConfig(
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

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    print("=== config / pathing ===")
    check("upkeep0", population_upkeep(19) == 0)
    check("upkeep1", population_upkeep(20) == 1)
    check("upkeep3", population_upkeep(40) == 3)
    check("afford", can_afford(7, 5, 2) is True and can_afford(6, 5, 2) is False)
    check("manhattan", manhattan((0, 0), (3, 4)) == 7)
    check(
        "range_ok",
        is_in_range_cardinal_or_diag((10, 10), (10, 13))
        and is_in_range_cardinal_or_diag((10, 10), (13, 13)),
    )
    check("range_bad", not is_in_range_cardinal_or_diag((10, 10), (12, 11)))
    check("step_right", clamp_step_toward((0, 0), (3, 0)) == "RIGHT")
    check("nearest", nearest((0, 0), [(5, 0), (1, 1)]) == (1, 1))
    slots = defense_ring_slots((10, 10), 2, 4)
    check(
        "ring",
        len(slots) == 4
        and all(abs(s[0] - 10) + abs(s[1] - 10) == 2 for s in slots),
    )
    total_target = (
        DEFAULT_CONFIG.target_workers
        + DEFAULT_CONFIG.target_vanguards
        + DEFAULT_CONFIG.target_rangers
    )
    check("default_pop", total_target < 20)

    print("=== economy ===")
    turn = StubTurn(
        resources=20, core=StubCore(), workers=[StubUnit(unit_type="WORKER")]
    )
    check("spawn_worker", choose_spawn(turn, config=config) == "WORKER")
    turn2 = StubTurn(resources=2, core=StubCore(), workers=[])
    check("spawn_none_low_res", choose_spawn(turn2, config=config) is None)

    workers = [StubUnit(unit_type="WORKER") for _ in range(12)]
    vanguards = [StubUnit(unit_type="VANGUARD", hp=4) for _ in range(4)]
    rangers = [StubUnit(unit_type="RANGER") for _ in range(3)]
    turn3 = StubTurn(
        resources=50,
        core=StubCore(),
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
    )
    turn3.state.population = 19
    check(
        "spawn_cap",
        choose_spawn(turn3, config=config, has_near_threat=False) is None,
    )

    turn4 = StubTurn(
        resources=30,
        core=StubCore(position=(10, 10)),
        workers=[StubUnit(unit_type="WORKER") for _ in range(4)],
        visible_enemies=[StubEnemy(position=(12, 10))],
    )
    check(
        "spawn_vang_threat",
        choose_spawn(turn4, config=config, has_near_threat=True) == "VANGUARD",
    )

    w = StubUnit(position=(12, 10), cargo=0, unit_type="WORKER")
    t = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w],
        resource_cells={(12, 10)},
    )
    plan = assign_roles(t, config=config)
    command_workers(t, plan, config=config)
    check("harvest", w.action == "harvest")

    w2 = StubUnit(position=(10, 10), cargo=1, unit_type="WORKER")
    t2 = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w2],
        resource_cells=set(),
    )
    plan2 = assign_roles(t2, config=config)
    command_workers(t2, plan2, config=config)
    check("deposit", w2.action == "deposit")

    w3 = StubUnit(position=(15, 10), cargo=1, unit_type="WORKER")
    t3 = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w3],
        resource_cells={(20, 10)},
    )
    plan3 = assign_roles(t3, config=config)
    command_workers(t3, plan3, config=config)
    check("return_cargo", w3.action == "move" and "LEFT" in str(w3.action_args))

    w4 = StubUnit(position=(14, 10), cargo=0, unit_type="WORKER")
    t4 = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w4],
        resource_cells={(20, 10)},
        visible_enemies=[StubEnemy(position=(15, 10))],
    )
    plan4 = assign_roles(t4, config=config)
    check("retreat_role", plan4.assignments[0].role.value == "retreat")
    command_workers(t4, plan4, config=config)
    check("retreat_move", w4.action == "move")

    # 远敌：空货 worker 不得 retreat，应 explore
    w_far = StubUnit(position=(14, 10), cargo=0, unit_type="WORKER")
    t_far = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w_far],
        resource_cells=set(),
        visible_enemies=[StubEnemy(position=(20, 10))],  # dist=6 > adjacent=2
    )
    plan_far = assign_roles(t_far, config=config)
    check(
        "far_enemy_harvester",
        plan_far.assignments[0].role.value == "harvester",
        f"role={plan_far.assignments[0].role.value}",
    )
    logs_far = command_workers(t_far, plan_far, config=config)
    check(
        "far_enemy_explore",
        w_far.action == "move"
        and not any(":retreat:" in line for line in logs_far)
        and any("explore" in line or "to_resource" in line for line in logs_far),
        f"action={w_far.action} logs={logs_far}",
    )

    # 满货 + 距敌 ≤ retreat_radius → 仍 retreat
    w_cargo = StubUnit(position=(17, 10), cargo=1, unit_type="WORKER")
    t_cargo = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w_cargo],
        resource_cells={(30, 10)},
        visible_enemies=[StubEnemy(position=(20, 10))],  # dist=3 ≤ retreat_radius
    )
    plan_cargo = assign_roles(t_cargo, config=config)
    check(
        "cargo_retreat",
        plan_cargo.assignments[0].role.value == "retreat",
        f"role={plan_cargo.assignments[0].role.value}",
    )

    # 两 worker 无资源：探索方向必须不同（修复同格振荡）
    wa = StubUnit(position=(10, 10), cargo=0, unit_type="WORKER")
    wb = StubUnit(position=(10, 10), cargo=0, unit_type="WORKER")
    te = StubTurn(
        tick=1,
        resources=3,
        core=StubCore(position=(10, 10)),
        workers=[wa, wb],
        resource_cells=set(),
    )
    plane = assign_roles(te, config=config)
    logs_e = command_workers(te, plane, config=config)
    dirs = []
    for line in logs_e:
        if ":explore:" in line:
            # worker:id:explore:DIR 或 worker:id:explore:DIR:r=N:ph=M
            parts = line.split(":")
            # 找到 explore 后的方向字段
            try:
                ei = parts.index("explore")
                dirs.append(parts[ei + 1])
            except (ValueError, IndexError):
                dirs.append(line.rsplit(":", 1)[-1])
    check(
        "explore_dirs_differ",
        wa.action == "move"
        and wb.action == "move"
        and len(dirs) == 2
        and dirs[0] != dirs[1],
        f"dirs={dirs} args={[wa.action_args, wb.action_args]}",
    )
    # pathing 辅助：两 worker 目标点不同
    targets = explore_targets((10, 10), 2, tick=1, base_radius=4)
    check(
        "explore_targets_spread",
        len(targets) == 2 and targets[0] != targets[1],
        f"targets={targets}",
    )
    t_a = explore_target((10, 10), 0, tick=0, base_radius=4)
    t_b = explore_target((10, 10), 1, tick=0, base_radius=4)
    check("explore_index_dirs", t_a != t_b, f"a={t_a} b={t_b}")

    # 早期 spawn：pop < early_game_pop 时 reserve=0，resources>=5 可出 WORKER（官方 cost=5）
    check(
        "early_reserve_zero",
        effective_reserve(0, config) == 0
        and effective_reserve(3, config) == 0
        and effective_reserve(4, config) == config.reserve_resources,
    )
    turn_early = StubTurn(resources=5, core=StubCore(), workers=[])
    check(
        "early_spawn_worker",
        choose_spawn(turn_early, config=config) == "WORKER",
        f"got={choose_spawn(turn_early, config=config)}",
    )
    # 非早期：pop>=early_game_pop 且仍缺 worker；resources=5, reserve=2, cost=5 → 不可
    cfg_mid = TacticConfig(
        max_population=18,
        target_workers=12,
        target_vanguards=2,
        target_rangers=1,
        reserve_resources=2,
        early_game_pop=4,
    )
    workers_mid = [StubUnit(unit_type="WORKER") for _ in range(4)]
    turn_mid = StubTurn(resources=5, core=StubCore(), workers=workers_mid)
    check(
        "mid_spawn_blocked_by_reserve",
        choose_spawn(turn_mid, config=cfg_mid) is None,
    )

    # 跑飞回撤：worker (50,10) dist=40 > explore_max_radius+4=36 → 回撤一步靠近 Core
    w_recall = StubUnit(position=(50, 10), cargo=0, unit_type="WORKER")
    t_recall = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w_recall],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan_recall = assign_roles(t_recall, config=config)
    role_recall_ok = plan_recall.assignments[0].role.value == "harvester"
    logs_recall = command_workers(t_recall, plan_recall, config=config)
    dir_recall = str(w_recall.action_args)
    delta_recall = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[
        dir_recall
    ]
    nxt_recall = (50 + delta_recall[0], 10 + delta_recall[1])
    check(
        "worker_recall_when_too_far",
        role_recall_ok
        and w_recall.action == "move"
        and (
            manhattan(nxt_recall, (10, 10)) < 40
            or any(":recall" in line for line in logs_recall)
        ),
        f"role_ok={role_recall_ok} dir={dir_recall} nxt={nxt_recall} logs={logs_recall}",
    )

    # 探索不朝 Core 收缩：worker (30,10) dist=20 < 32 → explore 方向不减距 Core 距离
    w_expl = StubUnit(position=(30, 10), cargo=0, unit_type="WORKER")
    t_expl = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10)),
        workers=[w_expl],
        resource_cells=set(),
        visible_enemies=[],
    )
    plan_expl = assign_roles(t_expl, config=config)
    logs_expl = command_workers(t_expl, plan_expl, config=config)
    dir_expl = str(w_expl.action_args)
    delta_expl = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[
        dir_expl
    ]
    nxt_expl = (30 + delta_expl[0], 10 + delta_expl[1])
    check(
        "worker_explore_respects_max_radius",
        w_expl.action == "move"
        and manhattan(nxt_expl, (10, 10)) >= 20
        and not any(":recall" in line for line in logs_expl),
        f"dir={dir_expl} nxt={nxt_expl} logs={logs_expl}",
    )

    print("=== combat ===")
    tt = StubTurn(
        core=StubCore(position=(10, 10)),
        visible_enemies=[
            StubEnemy(position=(12, 10)),
            StubEnemy(position=(30, 30)),
        ],
    )
    th = assess_threats(tt, (10, 10), config=config)
    check("threat", th["count"] == 2 and th["has_near_threat"])

    vg = StubUnit(position=(11, 10), hp=4, unit_type="VANGUARD")
    tv = StubTurn(
        core=StubCore(position=(10, 10)),
        vanguards=[vg],
        visible_enemies=[StubEnemy(position=(12, 10))],
    )
    pv = assign_roles(tv, config=config)
    command_vanguards(tv, pv, config=config)
    check("sweep", vg.action == "sweep")

    rg = StubUnit(position=(10, 10), hp=2, unit_type="RANGER")
    tr = StubTurn(
        core=StubCore(position=(10, 10)),
        rangers=[rg],
        visible_enemies=[StubEnemy(position=(10, 13))],
    )
    pr = assign_roles(tr, config=config)
    command_rangers(tr, pr, config=config)
    check("shoot", rg.action in ("shoot", "shoot_cell"))

    core = StubCore(position=(10, 10), hp=2, shield=5)
    tc = StubTurn(resources=5, core=core)
    check("heal_first", should_core_heal_first(tc, config=config))
    acted, _ = command_core_defense(tc, config=config)
    check("heal_act", acted and core.action == "heal")

    core2 = StubCore(position=(10, 10), hp=5, shield=1)
    tc2 = StubTurn(resources=5, core=core2)
    acted2, _ = command_core_defense(tc2, config=config)
    check("repair", acted2 and core2.action == "repair_shield")

    print("=== strategy ===")
    ts = StubTurn(
        tick=3,
        resources=15,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(14, 10)},
    )
    rs = decide(ts, config=config)
    check(
        "decide_spawn",
        ts.core is not None
        and ts.core.action == "spawn"
        and "WORKER" in str(ts.core.action_args),
        rs.summary(),
    )

    td = StubTurn(
        tick=10,
        resources=25,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER") for _ in range(4)],
        visible_enemies=[StubEnemy(position=(13, 10))],
        resource_cells={(8, 10)},
    )
    rd = decide(td, config=config)
    check(
        "decide_threat_spawn",
        rd.has_near_threat
        and td.core is not None
        and td.core.action == "spawn"
        and "VANGUARD" in str(td.core.action_args),
        rd.summary(),
    )

    vg2 = StubUnit(position=(11, 10), hp=4, unit_type="VANGUARD")
    ts2 = StubTurn(
        resources=5,
        core=StubCore(position=(10, 10), hp=5, shield=5),
        vanguards=[vg2],
        visible_enemies=[StubEnemy(position=(12, 10))],
    )
    decide(ts2, config=config)
    check("decide_sweep", vg2.action == "sweep")

    thp = StubTurn(
        resources=20,
        core=StubCore(position=(10, 10), hp=2, shield=5),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(15, 10)},
    )
    rh = decide(thp, config=config)
    check(
        "decide_heal",
        thp.core is not None and thp.core.action == "heal",
        rh.summary(),
    )

    tn = StubTurn(workers=[], resources=0)
    tn.core = None
    rn = decide(tn)
    check("no_core", rn.core_action == "absent")

    # 重生状态：RESPAWNING 时跳过全部行动，仅记录 respawn_at
    trs = StubTurn(
        tick=7,
        resources=0,
        core=StubCore(position=(10, 10)),
        workers=[StubUnit(position=(11, 10), unit_type="WORKER")],
        resource_cells={(12, 10)},
        state=StubState(status="RESPAWNING", respawn_at_tick=120),
    )
    rrs = decide(trs, config=config)
    check(
        "respawn_skip",
        rrs.core_action == "respawn"
        and trs.core is not None
        and trs.core.action is None
        and trs.workers[0].action is None
        and any("respawn_at=120" in line for line in rrs.logs),
        f"core_action={rrs.core_action} logs={rrs.logs}",
    )

    tb = StubTurn(
        tick=1,
        resources=8,
        core=StubCore(position=(10, 10)),
        workers=[StubUnit(position=(10, 11))],
        resource_cells={(12, 10), (14, 10)},
    )
    text = decide_and_describe(tb, config=config)
    check("describe", "tick=" in text and "pop=" in text)

    tcnt = StubTurn(
        core=StubCore(),
        workers=[StubUnit() for _ in range(3)],
        vanguards=[StubUnit(unit_type="VANGUARD", hp=4)],
        rangers=[StubUnit(unit_type="RANGER")],
    )
    check(
        "counts",
        count_by_type(tcnt) == {"WORKER": 3, "VANGUARD": 1, "RANGER": 1}
        and total_population(tcnt) == 5,
    )

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
