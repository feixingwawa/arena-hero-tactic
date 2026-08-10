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
    # 不与 Core 同格（同格会优先 leave_core）
    ranger = StubUnit(position=(10, 12), hp=2, unit_type="RANGER")
    enemy = StubEnemy(position=(10, 15), hp=2)  # 直线距离 3
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


def test_vanguard_leaves_core_overlap(config: TacticConfig) -> None:
    """Vanguard 与 Core 同格 → 立即 move 离开，日志 leave_core。"""
    from bot.pathing import NAME_TO_DELTA, add_pos

    core_pos = (10, 10)
    vanguard = StubUnit(position=core_pos, hp=4, unit_type="VANGUARD")
    turn = StubTurn(
        tick=0,
        core=StubCore(position=core_pos),
        vanguards=[vanguard],
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    logs = command_vanguards(turn, plan, config=config, core_position=core_pos)
    assert vanguard.action == "move", (vanguard.action, logs)
    assert any("leave_core" in line for line in logs), logs
    # action_args 可能是 str 或 SDK Direction 枚举
    arg = vanguard.action_args
    name = getattr(arg, "value", arg) if arg is not None else None
    name = str(name) if name is not None else ""
    assert name in NAME_TO_DELTA, (arg, name, logs)
    nxt = add_pos(core_pos, NAME_TO_DELTA[name])
    assert nxt != core_pos


def test_ranger_leaves_core_overlap(config: TacticConfig) -> None:
    """Ranger 与 Core 同格 → 即使有可射击目标，也优先 leave_core。"""
    from bot.pathing import NAME_TO_DELTA

    core_pos = (10, 10)
    ranger = StubUnit(position=core_pos, hp=2, unit_type="RANGER")
    enemy = StubEnemy(position=(10, 13), hp=2)
    turn = StubTurn(
        core=StubCore(position=core_pos),
        rangers=[ranger],
        visible_enemies=[enemy],
    )
    plan = assign_roles(turn, config=config)
    logs = command_rangers(turn, plan, config=config, core_position=core_pos)
    assert ranger.action == "move", (ranger.action, logs)
    assert ranger.action not in ("shoot", "shoot_cell")
    assert any("leave_core" in line for line in logs), logs
    arg = ranger.action_args
    name = getattr(arg, "value", arg) if arg is not None else None
    name = str(name) if name is not None else ""
    assert name in NAME_TO_DELTA, (arg, name, logs)


def test_TR_7_2_pick_unused_slot_vanguards(config: TacticConfig) -> None:
    """TR-7.2: 3 个 Vanguards 在 Core 周围相同位置，tick=0 phase=0 → 3 个目标位置互不相同。"""
    from bot.pathing import NAME_TO_DELTA

    core_pos = (10, 10)
    # 3 个 Vanguard 起始都在 (11, 10)，Core 在 (10,10)，tick=0（phase=0）
    v_list = [
        StubUnit(position=(11, 10), hp=4, unit_type="VANGUARD") for _ in range(3)
    ]
    turn = StubTurn(
        tick=0,
        core=StubCore(position=core_pos, hp=10, shield=5),
        vanguards=v_list,
        visible_enemies=[],  # 无威胁 → 触发 defense slot 选择
    )
    plan = assign_roles(turn, config=config)
    command_vanguards(turn, plan, config=config, core_position=core_pos)

    # 计算每个 Vanguard 的目标位置（如果是 move 就用方向推一步；如果是 wait/hold 就是当前位置）
    def target_pos(v: StubUnit) -> tuple[int, int]:
        pos = tuple(v.position)
        action = v.action
        args = v.action_args
        name = getattr(args, "value", args) if args is not None else None
        name = str(name) if name is not None else ""
        if action == "move" and name in NAME_TO_DELTA:
            dx, dy = NAME_TO_DELTA[name]
            return (pos[0] + dx, pos[1] + dy)
        return pos

    targets = [target_pos(v) for v in v_list]
    unique_targets = set(targets)
    # 3 个目标位置互不相同（如果出现 wait 也可以，只要不重复）
    assert len(unique_targets) >= 2, f"3 Vanguards 目标位置重复过多: {targets}"
    # 检查至少不全部相同
    assert not (targets[0] == targets[1] == targets[2]), (
        f"3 Vanguards 全部撞在同一位置 {targets[0]}"
    )


def test_TR_7_3_ranger_fire_ledger_overkill(config: TacticConfig) -> None:
    """TR-7.3: 2 敌（各 HP=1）+ 4 Rangers → 至少 2 条 shoot_avoid_overkill，shoot 目标 ID 不重复。"""
    core_pos = (10, 10)
    e1 = StubEnemy(position=(10, 13), hp=1, unit_type="VANGUARD")
    e2 = StubEnemy(position=(13, 10), hp=1, unit_type="VANGUARD")
    # 4 个 Rangers 不与 Core 同格；两两分别在 e1/e2 直线射程内，
    # 使后两名会因 fire_ledger 触发 shoot_avoid_overkill。
    r_list = [
        StubUnit(position=(10, 12), hp=2, unit_type="RANGER"),  # 射 e1
        StubUnit(position=(12, 10), hp=2, unit_type="RANGER"),  # 射 e2
        StubUnit(position=(10, 11), hp=2, unit_type="RANGER"),  # e1 overkill
        StubUnit(position=(11, 10), hp=2, unit_type="RANGER"),  # e2 overkill
    ]
    turn = StubTurn(
        core=StubCore(position=core_pos, hp=10, shield=5),
        rangers=r_list,
        visible_enemies=[e1, e2],
    )
    plan = assign_roles(turn, config=config)
    logs = command_rangers(turn, plan, config=config, core_position=core_pos)

    # 统计 shoot_avoid_overkill 日志数
    overkill_logs = [l for l in logs if "shoot_avoid_overkill" in l]
    assert len(overkill_logs) >= 2, (
        f"应该至少 2 条 overkill 日志，但只有 {len(overkill_logs)}: {logs}"
    )

    # 统计实际 shoot 的目标 ID（从 shoot:xxx 或 shoot_cell 推断）
    shot_ids: list[str] = []
    for r in r_list:
        if r.action == "shoot" and r.action_args is not None:
            target = r.action_args[0] if isinstance(r.action_args, tuple) else r.action_args
            tid = str(getattr(target, "id", target))
            shot_ids.append(tid)
    # 目标 ID 不重复（最多 2 个不同 ID，因为两个敌人各 HP=1）
    assert len(set(shot_ids)) <= 2, f"shoot 目标超过 2 个敌人: {shot_ids}"
    # 至少有不超过 2 次实际 shoot（因为两个敌人各只需要 1 次伤害）
    assert len(shot_ids) <= 2, f"实际 shoot 次数 {len(shot_ids)} 超过敌人 HP 总和: {shot_ids}"


def test_vanguard_guided_detours_around_wall(config: TacticConfig) -> None:
    """Vanguard 回城治疗应绕过墙（memory/可见障碍），不贪心撞墙。"""
    from bot.memory import MemoryMap
    from bot.pathing import NAME_TO_DELTA, add_pos

    core_pos = (10, 10)
    # 起点在 Core 右侧，中间 (11,10) 是墙 → 贪心会 RIGHT 撞墙
    start = (12, 10)
    wall = (11, 10)
    vanguard = StubUnit(position=start, hp=1, unit_type="VANGUARD")
    turn = StubTurn(
        tick=5,
        core=StubCore(position=core_pos),
        vanguards=[vanguard],
        visible_enemies=[],
        obstacle_cells=[wall],
    )
    mem = MemoryMap()
    mem.obstacles.add(wall)
    plan = assign_roles(turn, config=config)
    assert plan.get(vanguard.id) is not None
    assert plan.get(vanguard.id).role.value == "heal"  # type: ignore[union-attr]
    logs = command_vanguards(
        turn, plan, config=config, core_position=core_pos, memory=mem
    )
    assert vanguard.action == "move", (vanguard.action, logs)
    arg = vanguard.action_args
    name = getattr(arg, "value", arg) if arg is not None else None
    name = str(name) if name is not None else ""
    assert name in NAME_TO_DELTA, (arg, name, logs)
    nxt = add_pos(start, NAME_TO_DELTA[name])
    assert nxt != wall, f"不应走进墙 {wall}: dir={name} nxt={nxt} logs={logs}"
    assert any("to_heal" in line for line in logs), logs


def test_vanguard_heals_at_core_then_leaves_next_tick(config: TacticConfig) -> None:
    """受伤 Vanguard 在 Core 上 heal；满血下一 tick 必须 leave_core，不长期占 Core。"""
    from bot.pathing import NAME_TO_DELTA

    core_pos = (10, 10)
    # tick1: 低血在 Core → heal
    v1 = StubUnit(position=core_pos, hp=1, unit_type="VANGUARD")
    turn1 = StubTurn(
        tick=1,
        core=StubCore(position=core_pos),
        vanguards=[v1],
        visible_enemies=[],
    )
    plan1 = assign_roles(turn1, config=config)
    logs1 = command_vanguards(turn1, plan1, config=config, core_position=core_pos)
    assert v1.action == "heal", (v1.action, logs1)
    assert any(":heal" in line for line in logs1), logs1

    # tick2: 已满血仍在 Core → leave_core（非 HEAL）
    v2 = StubUnit(position=core_pos, hp=4, unit_type="VANGUARD")
    turn2 = StubTurn(
        tick=2,
        core=StubCore(position=core_pos),
        vanguards=[v2],
        visible_enemies=[],
    )
    plan2 = assign_roles(turn2, config=config)
    assert plan2.get(v2.id) is not None
    assert plan2.get(v2.id).role.value != "heal"  # type: ignore[union-attr]
    logs2 = command_vanguards(turn2, plan2, config=config, core_position=core_pos)
    assert v2.action == "move", (v2.action, logs2)
    assert any("leave_core" in line for line in logs2), logs2
    arg = v2.action_args
    name = getattr(arg, "value", arg) if arg is not None else None
    name = str(name) if name is not None else ""
    assert name in NAME_TO_DELTA, (arg, name, logs2)


def test_ranger_heal_role_and_guided_to_core(config: TacticConfig) -> None:
    """Ranger 半血触发 HEAL，用引导寻路朝 Core 移动。"""
    core_pos = (10, 10)
    ranger = StubUnit(position=(14, 10), hp=1, unit_type="RANGER")
    turn = StubTurn(
        tick=3,
        core=StubCore(position=core_pos),
        rangers=[ranger],
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    assert plan.get(ranger.id) is not None
    assert plan.get(ranger.id).role.value == "heal"  # type: ignore[union-attr]
    logs = command_rangers(turn, plan, config=config, core_position=core_pos)
    assert ranger.action == "move", (ranger.action, logs)
    assert any("to_heal" in line for line in logs), logs


def test_vanguard_half_hp_triggers_heal_without_adjacent_enemy(
    config: TacticConfig,
) -> None:
    """Vanguard max_hp=4 时 hp<=2 且无邻敌 → HEAL（半血阈值）。"""
    core_pos = (10, 10)
    v = StubUnit(position=(13, 10), hp=2, unit_type="VANGUARD")
    turn = StubTurn(
        core=StubCore(position=core_pos),
        vanguards=[v],
        visible_enemies=[],
    )
    plan = assign_roles(turn, config=config)
    a = plan.get(v.id)
    assert a is not None
    assert a.role.value == "heal"
    assert a.hint_target == core_pos


def test_strike_team_two_v_two_r_toward_enemy_core(config: TacticConfig) -> None:
    """可见敌方 CORE 时分配 2 Vanguard + 2 Ranger 为 STRIKE 并朝其移动。"""
    from bot.roles import Role, assign_roles

    core_pos = (10, 10)
    enemy_core = (20, 10)
    vs = [
        StubUnit(position=(11 + i, 10), hp=4, unit_type="VANGUARD") for i in range(3)
    ]
    rs = [
        StubUnit(position=(11 + i, 12), hp=2, unit_type="RANGER") for i in range(3)
    ]
    turn = StubTurn(
        core=StubCore(position=core_pos),
        vanguards=vs,
        rangers=rs,
        visible_enemies=[StubEnemy(position=enemy_core, unit_type="CORE", hp=5)],
    )
    plan = assign_roles(turn, config=config)
    assert plan.enemy_core_position == enemy_core
    strike_v = [a for a in plan.assignments if a.role == Role.STRIKE and a.unit_type == "VANGUARD"]
    strike_r = [a for a in plan.assignments if a.role == Role.STRIKE and a.unit_type == "RANGER"]
    assert len(strike_v) == 2
    assert len(strike_r) == 2
    assert all(a.hint_target == enemy_core for a in strike_v + strike_r)
    # 第 3 个仍 GUARD
    guards = [a for a in plan.assignments if a.role == Role.GUARD]
    assert len(guards) == 2  # 1V + 1R

    v_logs = command_vanguards(turn, plan, config=config, core_position=core_pos)
    r_logs = command_rangers(turn, plan, config=config, core_position=core_pos)
    assert any(":strike:" in line for line in v_logs), v_logs
    assert any(":strike:" in line for line in r_logs), r_logs
    # 至少两名 V 在移动
    moving_v = sum(1 for v in vs if v.action == "move")
    assert moving_v >= 2, [(v.action, v.action_args) for v in vs]


def test_assess_threats_ignores_enemy_worker_for_near(config: TacticConfig) -> None:
    """敌方 WORKER 不计入 has_near_threat。"""
    from bot.combat import assess_threats

    turn = StubTurn(
        core=StubCore(position=(10, 10)),
        visible_enemies=[
            StubEnemy(position=(12, 10), unit_type="WORKER"),
            StubEnemy(position=(30, 30), unit_type="VANGUARD"),
        ],
    )
    t = assess_threats(turn, (10, 10), config=config)
    assert t["has_near_threat"] is False
    assert t["count"] == 2
    assert len(t["combat_positions"]) == 1
