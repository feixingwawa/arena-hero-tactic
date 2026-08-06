"""经济模块：采集 / 交付 / 生产优先级 / 维护费控制。

与 I/O 解耦：接收 turn + RolePlan，直接在单位对象上排队动作。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence
from uuid import UUID

from bot.config import TacticConfig, DEFAULT_CONFIG, population_upkeep
from bot.pathing import (
    EXPLORE_DIRS,
    NAME_TO_DELTA,
    Position,
    add_pos,
    clamp_step_toward,
    clamp_step_toward_memo,
    explore_radius,
    explore_target,
    manhattan,
    nearest,
    outward_step,
)
from bot.roles import (
    Role,
    RolePlan,
    count_by_type,
    total_population,
    _as_position,
)


def _get_resources(turn: Any) -> int:
    """读取 Core 当前资源。"""
    if hasattr(turn, "resources") and turn.resources is not None:
        return int(turn.resources)
    state = getattr(turn, "state", None)
    if state is not None and hasattr(state, "resources"):
        return int(state.resources)
    return 0


def _get_upkeep_next(turn: Any) -> int:
    """读取下 tick 维护费。"""
    state = getattr(turn, "state", None)
    if state is not None and hasattr(state, "upkeep_next_tick"):
        return int(state.upkeep_next_tick or 0)
    pop = total_population(turn)
    if hasattr(turn, "state") and turn.state is not None:
        pop_attr = getattr(turn.state, "population", None)
        if pop_attr is not None:
            pop = int(pop_attr)
    return population_upkeep(pop)


def _resource_cells(turn: Any) -> list[Position]:
    """可见资源格列表。"""
    cells = getattr(turn, "resource_cells", None)
    if cells is None:
        return []
    return [_as_position(c) for c in cells]


def _obstacle_cells(turn: Any) -> set[Position]:
    cells = getattr(turn, "obstacle_cells", None)
    if cells is None:
        return set()
    return {_as_position(c) for c in cells}


def _resolve_direction(unit: Any, direction_name: str) -> Any:
    """将方向名转为 SDK Direction 枚举（若可用），否则返回字符串。"""
    try:
        from arena_hero import Direction  # type: ignore

        return Direction[direction_name]
    except Exception:
        return direction_name


def _resolve_unit_type(type_name: str) -> Any:
    """将单位类型名转为 SDK UnitType（若可用）。"""
    try:
        from arena_hero import UnitType  # type: ignore

        return UnitType[type_name]
    except Exception:
        return type_name


def can_afford(resources: int, cost: int, reserve: int) -> bool:
    """资源是否足够支付 cost，并保留 reserve。"""
    return resources - cost >= reserve


def effective_reserve(pop: int, config: TacticConfig) -> int:
    """早期人口时 reserve 视为 0，便于 resources>=worker_cost 即可出 WORKER。"""
    if pop < config.early_game_pop:
        return 0
    return config.reserve_resources


# 振荡检测：worker 最近位置（按 id 字符串键）
_last_explore_pos: dict[str, Position] = {}
_prev_explore_pos: dict[str, Position] = {}  # 上上格，检测 A↔B 对抖
_explore_phase: dict[str, int] = {}
# 用「进程内相对探索时长」扩半径，避免世界 tick 过大导致半径恒顶满却无换向节奏
_explore_ticks: dict[str, int] = {}
# 粘性探索主轴（RIGHT/LEFT/UP/DOWN），防止远敌避让造成横跳
_explore_axis: dict[str, str] = {}
_last_explore_dir: dict[str, str] = {}
# 非探索移动（return_deposit / to_resource / retreat / recall）的方向记忆，防 A↔B 对抖
_last_move_dir: dict[str, str] = {}


def _worker_key(uid: Any) -> str:
    return str(uid)


def _worker_index(uid: Any, workers: Sequence[Any], fallback: int) -> int:
    """稳定的 worker 分散索引：优先列表下标，否则 id 哈希。"""
    try:
        for i, w in enumerate(workers):
            if getattr(w, "id", None) == uid:
                return i
    except Exception:
        pass
    try:
        return abs(hash(str(uid))) % 8
    except Exception:
        return fallback % 8


def _opposite_dir(name: str) -> str:
    return {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}.get(
        name, name
    )


def _perpendicular_dirs(name: str) -> tuple[str, str]:
    if name in ("LEFT", "RIGHT"):
        return ("UP", "DOWN")
    return ("LEFT", "RIGHT")


def _axis_from_index(widx: int) -> str:
    """稳定主轴：0 RIGHT, 1 DOWN, 2 LEFT, 3 UP …"""
    return ("RIGHT", "DOWN", "LEFT", "UP")[widx % 4]


def _pick_explore_direction_avoiding_enemies(
    pos: Position,
    preferred: Optional[str],
    enemies: Sequence[Position],
    core_position: Position,
    obstacles: set[Position],
) -> tuple[Optional[str], bool]:
    """探索时避敌：保持主轴；危险时垂直外扩，禁止直接反向（防横跳）。

    规则：
    1. 无敌人 → 原方向
    2. preferred 不更靠近敌人 → 保留 preferred
    3. preferred 会靠近敌人 → 优先垂直方向中「不靠近敌 + 外扩」的一步
    4. 绝不优先选 preferred 的正反方向（LEFT↔RIGHT / UP↔DOWN）
    """
    if not enemies:
        return preferred, False

    nearest_enemy = nearest(pos, list(enemies))
    if nearest_enemy is None:
        return preferred, False

    dist_now = manhattan(pos, nearest_enemy)
    core_now = manhattan(pos, core_position)
    opp = _opposite_dir(preferred) if preferred else None

    # preferred 安全则绝不改道
    if preferred in NAME_TO_DELTA:
        pref_nxt = add_pos(pos, NAME_TO_DELTA[preferred])
        if pref_nxt not in obstacles:
            if manhattan(pref_nxt, nearest_enemy) >= dist_now:
                return preferred, False

    # 候选：垂直方向优先，再 preferred，最后才 opposite（仅兜底）
    order: list[str] = []
    if preferred in NAME_TO_DELTA:
        order.extend(_perpendicular_dirs(preferred))
        order.append(preferred)
        order.append(_opposite_dir(preferred))
    else:
        order.extend(["RIGHT", "DOWN", "UP", "LEFT"])

    best_name: Optional[str] = None
    best_score = -10_000
    seen: set[str] = set()
    for name in order:
        if name in seen or name not in NAME_TO_DELTA:
            continue
        seen.add(name)
        nxt = add_pos(pos, NAME_TO_DELTA[name])
        if nxt in obstacles:
            continue
        dist_after = manhattan(nxt, nearest_enemy)
        core_after = manhattan(nxt, core_position)
        score = 0
        if dist_after < dist_now:
            score -= 200
        elif dist_after > dist_now:
            score += 50
        else:
            score += 15
        if core_after > core_now:
            score += 60 + (core_after - core_now) * 12
        elif core_after < core_now:
            score -= 80  # 禁止借避敌缩回 Core
        if name == preferred:
            score += 20
        if name == opp:
            score -= 100  # 强力惩罚反向横跳
        # 垂直外扩额外加分
        if preferred and name in _perpendicular_dirs(preferred):
            score += 35
        if score > best_score:
            best_score = score
            best_name = name

    if best_name is None:
        return preferred, False
    avoided = preferred is not None and best_name != preferred
    return best_name, avoided


def choose_spawn(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
    has_near_threat: bool = False,
    has_far_threat: bool = False,
) -> Optional[str]:
    """Core 空闲时的生产决策，返回 "WORKER"|"VANGUARD"|"RANGER"|None。

    优先级：
    1. workers 不足目标 → WORKER
    2. 有威胁且战斗单位不足 → Vanguard（近）/ Ranger（远）
    3. 补齐目标编制的 Vanguard / Ranger
    4. 人口/维护费/资源不足 → None

    早期（pop < early_game_pop）spawn 时 reserve 视为 0。
    """
    counts = count_by_type(turn)
    pop = total_population(turn)
    resources = _get_resources(turn)
    upkeep = _get_upkeep_next(turn)
    reserve = effective_reserve(pop, config)

    # 已在维护费档位：除非严重缺防，停止扩军
    if upkeep > 0 or pop >= config.upkeep_hard_cap:
        combat = counts["VANGUARD"] + counts["RANGER"]
        if not (has_near_threat and combat == 0):
            return None

    if pop >= config.max_population:
        return None

    # 软上限：只允许补防
    soft_blocked = pop >= config.upkeep_soft_cap

    def try_type(name: str, cost: int) -> Optional[str]:
        if not can_afford(resources, cost, reserve):
            return None
        if pop + 1 > config.max_population:
            return None
        # 预测 upkeep
        if population_upkeep(pop + 1) > 0 and not (
            has_near_threat and counts["VANGUARD"] + counts["RANGER"] == 0
        ):
            return None
        return name

    # 1) Worker 优先（早期经济）
    if counts["WORKER"] < config.target_workers and not soft_blocked:
        chosen = try_type("WORKER", config.worker_cost)
        if chosen:
            return chosen

    # 2) 威胁驱动战斗单位
    combat_total = counts["VANGUARD"] + counts["RANGER"]
    combat_target = config.target_vanguards + config.target_rangers
    if has_near_threat or has_far_threat:
        if counts["VANGUARD"] < config.target_vanguards or (
            has_near_threat and counts["VANGUARD"] == 0
        ):
            chosen = try_type("VANGUARD", config.vanguard_cost)
            if chosen:
                return chosen
        if has_far_threat and counts["RANGER"] < max(1, config.target_rangers):
            chosen = try_type("RANGER", config.ranger_cost)
            if chosen:
                return chosen
        if combat_total < combat_target:
            # 更便宜的 Vanguard 优先
            if counts["VANGUARD"] < config.target_vanguards:
                chosen = try_type("VANGUARD", config.vanguard_cost)
                if chosen:
                    return chosen
            chosen = try_type("RANGER", config.ranger_cost)
            if chosen:
                return chosen

    if soft_blocked:
        return None

    # 3) 和平补齐编制
    if counts["WORKER"] < config.target_workers:
        chosen = try_type("WORKER", config.worker_cost)
        if chosen:
            return chosen
    if counts["VANGUARD"] < config.target_vanguards:
        chosen = try_type("VANGUARD", config.vanguard_cost)
        if chosen:
            return chosen
    if counts["RANGER"] < config.target_rangers:
        chosen = try_type("RANGER", config.ranger_cost)
        if chosen:
            return chosen

    return None


def command_workers(
    turn: Any,
    role_plan: RolePlan,
    config: TacticConfig = DEFAULT_CONFIG,
    core_position: Optional[Position] = None,
) -> list[str]:
    """为 Worker 排队 harvest / deposit / move。

    返回日志字符串列表（便于调试与测试断言）。
    """
    logs: list[str] = []
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            return logs
        core_position = _as_position(core.position)

    resources_cells = _resource_cells(turn)
    obstacles = _obstacle_cells(turn)
    # Core 格通常不可作为障碍，但移动时不要踩未知；此处仅避 obstacle_cells
    workers = list(getattr(turn, "workers", None) or ())
    # 探索避敌：使用角色计划中的威胁位置（可见敌人）
    enemy_positions: list[Position] = list(role_plan.threat_positions or [])

    # 资源目标去重：多个 worker 尽量分配不同资源点
    claimed: set[Position] = set()

    for w in workers:
        uid = w.id
        assignment = role_plan.get(uid)
        pos = _as_position(w.position)
        cargo = int(getattr(w, "cargo", 0) or 0)
        role = assignment.role if assignment else Role.HARVESTER

        # 撤退 / 治疗：走向 Core
        if role in (Role.RETREAT, Role.HEAL):
            if pos == core_position:
                if role == Role.HEAL and hasattr(w, "heal"):
                    w.heal()
                    logs.append(f"worker:{uid}:heal_at_core")
                elif cargo > 0 and hasattr(w, "deposit"):
                    w.deposit()
                    logs.append(f"worker:{uid}:deposit")
                elif pos in set(resources_cells) and hasattr(w, "harvest"):
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest_at_core")
                else:
                    if hasattr(w, "wait"):
                        w.wait()
                    logs.append(f"worker:{uid}:wait_at_core")
            else:
                wkey = _worker_key(uid)
                direction, _ = clamp_step_toward_memo(
                    pos,
                    core_position,
                    obstacles,
                    last_dir=_last_move_dir.get(wkey),
                )
                if direction and hasattr(w, "move"):
                    _last_move_dir[wkey] = direction
                    w.move(_resolve_direction(w, direction))
                    logs.append(f"worker:{uid}:retreat:{direction}")
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait")
            continue

        # 有货物且在 Core → deposit
        if cargo > 0 and pos == core_position:
            if hasattr(w, "deposit"):
                w.deposit()
                logs.append(f"worker:{uid}:deposit")
            continue

        # 有货物 → 回 Core 交付（优先于继续采集）
        if cargo > 0:
            wkey = _worker_key(uid)
            direction, _ = clamp_step_toward_memo(
                pos,
                core_position,
                obstacles,
                last_dir=_last_move_dir.get(wkey),
            )
            if direction and hasattr(w, "move"):
                _last_move_dir[wkey] = direction
                w.move(_resolve_direction(w, direction))
                logs.append(f"worker:{uid}:return_deposit:{direction}")
            continue

        # 站在资源格 → harvest
        if pos in set(resources_cells):
            if hasattr(w, "harvest"):
                w.harvest()
                logs.append(f"worker:{uid}:harvest")
            continue

        # 走向最近未声称资源
        available = [c for c in resources_cells if c not in claimed]
        target = nearest(pos, available) if available else nearest(pos, resources_cells)
        if target is not None:
            claimed.add(target)
            if pos == target:
                if hasattr(w, "harvest"):
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest")
            else:
                wkey = _worker_key(uid)
                direction, _ = clamp_step_toward_memo(
                    pos,
                    target,
                    obstacles,
                    last_dir=_last_move_dir.get(wkey),
                )
                if direction and hasattr(w, "move"):
                    _last_move_dir[wkey] = direction
                    w.move(_resolve_direction(w, direction))
                    logs.append(f"worker:{uid}:to_resource:{direction}")
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait")
        else:
            # 无可见资源：粘性主轴外扩；遇敌垂直避让，禁止反向横跳
            widx = _worker_index(uid, workers, fallback=len(logs))
            wkey = _worker_key(uid)
            local_t = _explore_ticks.get(wkey, 0) + 1
            _explore_ticks[wkey] = local_t
            phase = _explore_phase.get(wkey, 0)
            axis = _explore_axis.get(wkey) or _axis_from_index(widx)
            _explore_axis[wkey] = axis

            prev = _last_explore_pos.get(wkey)
            prev2 = _prev_explore_pos.get(wkey)
            last_dir = _last_explore_dir.get(wkey)

            # 原地卡住 → 改垂直主轴
            if prev is not None and prev == pos:
                axis = _perpendicular_dirs(axis)[widx % 2]
                _explore_axis[wkey] = axis
                phase = (phase + 1) % 8
            # A↔B 对抖 → 强制垂直轴，清 last_dir
            elif prev is not None and prev2 is not None and pos == prev2 and pos != prev:
                axis = _perpendicular_dirs(axis)[0]
                _explore_axis[wkey] = axis
                phase = (phase + 1) % 8
                last_dir = None
            # 慢换向：每 12 tick 微旋相位，主轴尽量粘住
            if local_t % 12 == 0:
                phase = (phase + 1) % 8
            phase %= 8
            _explore_phase[wkey] = phase
            _prev_explore_pos[wkey] = prev if prev is not None else pos
            _last_explore_pos[wkey] = pos

            r_plan = explore_radius(
                local_t,
                base=config.explore_base_radius,
                max_radius=config.explore_max_radius,
                expand_every=config.explore_expand_every,
            )
            dist_core = manhattan(pos, core_position)
            # 修复：探索半径严格封顶 explore_max_radius，绝不用 dist_core 顶上去。
            # 旧代码 r_now=max(r_plan, dist_core+2, base) 让 Worker 永远在
            # 「当前距离 +2」外扩，单 worker 越走越远最终回不了 Core。
            r_now = min(
                max(r_plan, config.explore_base_radius),
                config.explore_max_radius,
            )

            # 跑飞回撤：Worker 距 Core 已超出探索上限 + 环带余量 → 回撤一步靠近 Core。
            recall_dist = config.explore_max_radius + 4
            if dist_core > recall_dist:
                direction, _ = clamp_step_toward_memo(
                    pos,
                    core_position,
                    obstacles,
                    last_dir=_last_move_dir.get(wkey) or _last_explore_dir.get(wkey),
                )
                if direction is None:
                    direction = outward_step(
                        pos,
                        core_position,
                        preferred=None,
                        obstacles=obstacles,
                        last_dir=_last_explore_dir.get(wkey),
                    )
                if direction and hasattr(w, "move"):
                    _last_move_dir[wkey] = direction
                    # 回撤方向也写回 explore 记忆，阻止下一 tick explore 立刻反向（d=36/37 对抖）
                    _last_explore_dir[wkey] = direction
                    w.move(_resolve_direction(w, direction))
                    logs.append(
                        f"worker:{uid}:explore:{direction}:recall:r={r_now}:d={dist_core}"
                        f":ph={phase % 8}:ax={axis}"
                    )
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait_idle")
                continue

            # 超出当前目标半径：不再沿原射线外扩，切垂直轴扫掠一步，
            # 粘住垂直轴（_explore_axis 更新）并相位+1，触发下一轮换向扫掠。
            if dist_core > r_now + 2:
                side = _perpendicular_dirs(axis)[phase % 2]
                axis = side
                _explore_axis[wkey] = axis
                phase = (phase + 1) % 8
                _explore_phase[wkey] = phase

            ax_delta = NAME_TO_DELTA[axis]
            side = _perpendicular_dirs(axis)[phase % 2]
            side_delta = NAME_TO_DELTA[side]
            explore = (
                core_position[0] + ax_delta[0] * r_now + side_delta[0] * (phase % 3),
                core_position[1] + ax_delta[1] * r_now + side_delta[1] * (phase % 3),
            )

            # 默认：沿主轴外扩一步（不依赖可能落在身后的目标点）
            direction = outward_step(
                pos,
                core_position,
                preferred=axis,
                obstacles=obstacles,
                last_dir=_last_explore_dir.get(wkey),
            )
            # 若目标确实在更外侧，允许朝目标走，但仍禁止靠近 Core
            step_to = clamp_step_toward(pos, explore, obstacles)
            if step_to:
                nxt = add_pos(pos, NAME_TO_DELTA[step_to])
                if manhattan(nxt, core_position) >= dist_core:
                    direction = step_to
            preferred = direction or axis

            # 禁止紧接反向横跳 → 改垂直外扩
            if last_dir and direction and direction == _opposite_dir(last_dir):
                for cand in _perpendicular_dirs(last_dir):
                    nxt = add_pos(pos, NAME_TO_DELTA[cand])
                    if nxt not in obstacles and manhattan(nxt, core_position) >= dist_core:
                        direction = cand
                        preferred = cand
                        axis = cand
                        _explore_axis[wkey] = axis
                        break

            avoided = False
            if enemy_positions:
                direction, avoided = _pick_explore_direction_avoiding_enemies(
                    pos,
                    preferred=preferred or axis,
                    enemies=enemy_positions,
                    core_position=core_position,
                    obstacles=obstacles,
                )
                # 避敌成功且未反向：可短暂粘垂直轴；若选了反向则强制垂直
                if direction:
                    if direction == _opposite_dir(axis):
                        for cand in _perpendicular_dirs(axis):
                            nxt = add_pos(pos, NAME_TO_DELTA[cand])
                            if nxt not in obstacles:
                                direction = cand
                                avoided = True
                                break
                    if direction in _perpendicular_dirs(axis):
                        _explore_axis[wkey] = direction
                        axis = direction

            # 最终守卫：绝不朝 Core 收缩（除非贴身撤退，此处是 explore）
            if direction:
                nxt = add_pos(pos, NAME_TO_DELTA[direction])
                if manhattan(nxt, core_position) < dist_core:
                    direction = outward_step(
                        pos,
                        core_position,
                        preferred=axis,
                        obstacles=obstacles,
                        last_dir=_last_explore_dir.get(wkey),
                    )
                    avoided = True

            if direction and hasattr(w, "move"):
                w.move(_resolve_direction(w, direction))
                _last_explore_dir[wkey] = direction
                suffix = ":avoid" if avoided else ""
                logs.append(
                    f"worker:{uid}:explore:{direction}:r={r_now}:d={dist_core}"
                    f":ph={phase % 8}:ax={axis}{suffix}"
                )
            elif hasattr(w, "wait"):
                w.wait()
                logs.append(f"worker:{uid}:wait_idle")

    return logs


def command_core_economy(
    turn: Any,
    role_plan: RolePlan,
    config: TacticConfig = DEFAULT_CONFIG,
    prefer_heal: bool = False,
) -> list[str]:
    """Core 生产决策（治疗由 combat/strategy 更高优先级处理时可跳过）。

    若 prefer_heal=True，本函数不 spawn（留给治疗逻辑）。
    """
    logs: list[str] = []
    core = getattr(turn, "core", None)
    if core is None:
        return logs

    if prefer_heal:
        logs.append("core:skip_spawn_for_heal")
        return logs

    spawn_type = choose_spawn(
        turn,
        config=config,
        has_near_threat=role_plan.has_near_threat,
        has_far_threat=role_plan.has_far_threat,
    )
    if spawn_type is None:
        logs.append("core:no_spawn")
        return logs

    if hasattr(core, "spawn"):
        core.spawn(_resolve_unit_type(spawn_type))
        logs.append(f"core:spawn:{spawn_type}")
    return logs
