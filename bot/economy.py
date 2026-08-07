"""经济模块：采集 / 交付 / 生产优先级 / 探索。

与 I/O 解耦：接收 turn + RolePlan，直接在单位对象上排队动作。
v0.14：无维护费；动态价格走 bot.rules.unit_cost_for；探索为螺旋扫掠 + 软回撤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.memory import MemoryMap
from bot.rules import unit_cost_for
from bot.pathing import (
    CARDINAL_DELTAS,
    NAME_TO_DELTA,
    Position,
    add_pos,
    beacon_progress_target,
    chunk_of,
    clamp_step_toward_memo,
    manhattan,
    nearest,
    outward_step,
    sector_points,
    spiral_target,
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
    """早期人口时 reserve 视为 0，便于 resources 刚够动态价即可出 WORKER。"""
    if pop < config.early_game_pop:
        return 0
    return config.reserve_resources


@dataclass
class SpiralState:
    """单个 Worker 的螺旋扫掠探索状态。

    Attributes:
        phase: 探索阶段。'local'=基于 Core 螺旋扫掠；'beacon'=朝 Beacon 方向推进。
        ring: 当前曼哈顿环半径（local 阶段有效）。
        sector_id: 扇区（worker_index % sector_count）。
        index: 当前环扇区点列表中的下标（local）/ Beacon 绕障横向 offset 计数（beacon）。
        target: 当前目标点（None 表示待生成）。
        stalled_ticks: 连续无进展 tick 数（软回撤阈值 recall_stall_ticks）。
        ring_done: 当前环已扫完标记。
        dedicated: P1-1 专职 Beacon Worker（widx==0 指派；beacon 存在时恒为 beacon）。
    """

    phase: str = "local"  # 'local' | 'beacon'
    ring: int = 3
    sector_id: int = 0
    index: int = 0
    target: Optional[Position] = None
    stalled_ticks: int = 0
    ring_done: bool = False
    dedicated: bool = False


# 非探索移动（return_deposit / to_resource / retreat / recall）的方向记忆，防 A↔B 对抖
_last_move_dir: dict[str, str] = {}
# 螺旋扫掠探索状态（按 worker id 字符串键）
_spiral_state: dict[str, SpiralState] = {}
# 跨 tick 资源目标去重：{pos: (claim_tick, worker_key)}。同一目标被其他 Worker
# 在 claim_ttl_ticks 内占用时，本 Worker 不再选择（防多 Worker 汇聚同点导致
# same-point tie 多数失败）。自己的 claim 不排除（保持对目标的持续推进）。
_claimed_targets: dict[Position, tuple[int, str]] = {}
_CLAIM_TTL_TICKS: int = 8


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


def _pick_explore_direction_avoiding_enemies(
    pos: Position,
    preferred: Optional[str],
    enemies: Sequence[Position],
    core_position: Position,
    obstacles: set[Position],
) -> tuple[Optional[str], bool]:
    """探索时避敌改道：preferred 若安全则保留；否则选不更靠近敌人的方向。

    这是「改道」而非「强制外扩」：不再要求避敌方向必须远离 Core（删除
    「绝不朝 Core 收缩」守卫，避免制造 d=36/37 单维势阱）。
    仅禁止直接反向（防 A↔B 对抖）与靠近敌人。

    规则：
    1. 无敌人 → 原方向
    2. preferred 不更靠近敌人 → 保留 preferred
    3. preferred 会靠近敌人 → 优先垂直方向中「不更靠近敌」的一步
    4. 绝不优先选 preferred 的正反方向（LEFT↔RIGHT / UP↔DOWN）
    """
    if not enemies:
        return preferred, False

    nearest_enemy = nearest(pos, list(enemies))
    if nearest_enemy is None:
        return preferred, False

    dist_now = manhattan(pos, nearest_enemy)
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
        score = 0
        if dist_after < dist_now:
            score -= 200
        elif dist_after > dist_now:
            score += 50
        else:
            score += 15
        if name == preferred:
            score += 20
        if name == opp:
            score -= 100  # 强力惩罚反向横跳
        # 垂直改道额外加分（不要求外扩）
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
    4. 人口/资源不足 → None

    成本一律走 `rules.unit_cost_for(name, pop + 1)`（spawn 后人口估算，
    v0.14 动态价格，无维护费）。早期（pop < early_game_pop）reserve 视为 0。
    """
    counts = count_by_type(turn)
    pop = total_population(turn)
    resources = _get_resources(turn)
    reserve = effective_reserve(pop, config)

    if pop >= config.max_population:
        return None

    def try_type(name: str) -> Optional[str]:
        cost = unit_cost_for(name, pop + 1)
        if not can_afford(resources, cost, reserve):
            return None
        if pop + 1 > config.max_population:
            return None
        return name

    # 1) Worker 优先（早期经济）
    if counts["WORKER"] < config.target_workers:
        chosen = try_type("WORKER")
        if chosen:
            return chosen

    # 2) 威胁驱动战斗单位
    combat_total = counts["VANGUARD"] + counts["RANGER"]
    combat_target = config.target_vanguards + config.target_rangers
    if has_near_threat or has_far_threat:
        if counts["VANGUARD"] < config.target_vanguards or (
            has_near_threat and counts["VANGUARD"] == 0
        ):
            chosen = try_type("VANGUARD")
            if chosen:
                return chosen
        if has_far_threat and counts["RANGER"] < max(1, config.target_rangers):
            chosen = try_type("RANGER")
            if chosen:
                return chosen
        if combat_total < combat_target:
            # 更便宜的 Vanguard 优先
            if counts["VANGUARD"] < config.target_vanguards:
                chosen = try_type("VANGUARD")
                if chosen:
                    return chosen
            chosen = try_type("RANGER")
            if chosen:
                return chosen

    # 3) 和平补齐编制
    if counts["WORKER"] < config.target_workers:
        chosen = try_type("WORKER")
        if chosen:
            return chosen
    if counts["VANGUARD"] < config.target_vanguards:
        chosen = try_type("VANGUARD")
        if chosen:
            return chosen
    if counts["RANGER"] < config.target_rangers:
        chosen = try_type("RANGER")
        if chosen:
            return chosen

    return None


def command_workers(
    turn: Any,
    role_plan: RolePlan,
    config: TacticConfig = DEFAULT_CONFIG,
    core_position: Optional[Position] = None,
    memory: Optional[MemoryMap] = None,
) -> list[str]:
    """为 Worker 排队 harvest / deposit / move。

    无可见资源时执行螺旋扫掠（目标点导航 + 软回撤），不再使用
    recall_dist 硬边界与「绝不朝 Core 收缩」守卫（修复 d=36/37 势阱）。
    传入 memory 后，采集分支优先记忆回访候选（revisit_candidates，
    按 worker 扇区优先），并在采集成功后 mark_harvested。
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
    tick = int(getattr(turn, "tick", 0) or 0)

    # 资源目标去重：多个 worker 尽量分配不同资源点
    claimed: set[Position] = set()

    # Beacon 提取（P2-1）：持有者优先采集；GROUND 同格可拾取。
    # SDK 0.2.9：beacon.status 为 BeaconStatus(StrEnum) | None：
    #   "CARRIED" 必有 carrier_id；status=None 表示「位置公开、未被拾取」
    #   （模型约束：非 CARRIED 时 carrier_id 必为 None）→ 与 GROUND 同语义。
    beacon = getattr(turn, "beacon", None)
    beacon_carrier_ids: set[str] = set()
    beacon_ground_pos: Optional[Position] = None
    if beacon is not None:
        status = getattr(beacon, "status", None)
        status_str = getattr(status, "value", status)
        if status_str == "CARRIED":
            cid = getattr(beacon, "carrier_id", None)
            if cid is not None:
                beacon_carrier_ids.add(str(cid))
        elif status_str in ("GROUND", None):
            bpos = getattr(beacon, "position", None)
            if bpos is not None:
                try:
                    beacon_ground_pos = _as_position(bpos)
                except Exception:
                    beacon_ground_pos = None

    for w in workers:
        uid = w.id
        assignment = role_plan.get(uid)
        pos = _as_position(w.position)
        cargo = int(getattr(w, "cargo", 0) or 0)
        role = assignment.role if assignment else Role.HARVESTER
        wkey = _worker_key(uid)
        is_beacon_carrier = wkey in beacon_carrier_ids

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

        # 拾取地面 Beacon（P2-1：同格 GROUND → pickup_beacon）
        if (
            beacon_ground_pos is not None
            and pos == beacon_ground_pos
            and hasattr(w, "pickup_beacon")
        ):
            w.pickup_beacon()
            logs.append(f"worker:{uid}:pickup_beacon")
            continue

        # 站在资源格 → harvest（记忆标记已消耗）
        if pos in set(resources_cells):
            if hasattr(w, "harvest"):
                w.harvest()
                logs.append(f"worker:{uid}:harvest")
                if memory is not None:
                    memory.mark_harvested(pos, tick)
            continue

        # 候选 = 可见资源 ∪ 记忆回访候选（按 worker 扇区优先；无本扇区候选则放宽）
        # Beacon 持有者（P2-1：1 点 → 2 资源）跳过扇区限制，全图找最近资源
        candidates: list[Position] = list(resources_cells)
        if memory is not None:
            sector_id = None if is_beacon_carrier else (
                getattr(assignment, "sector_id", None) if assignment else None
            )
            mem_cands = memory.revisit_candidates(
                core_position,
                tick,
                pos,
                max_dist=config.revisit_max_distance,
                sector_id=sector_id,
            )
            if not mem_cands and sector_id is not None:
                mem_cands = memory.revisit_candidates(
                    core_position,
                    tick,
                    pos,
                    max_dist=config.revisit_max_distance,
                )
            for c in mem_cands:
                if c not in candidates:
                    candidates.append(c)

        # 走向最近未声称候选资源
        available = [c for c in candidates if c not in claimed]
        target = nearest(pos, available) if available else nearest(pos, candidates)
        if target is not None:
            claimed.add(target)
            if pos == target:
                if pos in set(resources_cells) and hasattr(w, "harvest"):
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest")
                    if memory is not None:
                        memory.mark_harvested(pos, tick)
                else:
                    # 目标格无实际资源（记忆脏数据），回退到探索
                    if hasattr(w, "wait"):
                        w.wait()
                    logs.append(f"worker:{uid}:wait:bad_target")
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
        elif memory is not None and cargo == 0:
            # cargo 回收（P2-2）：空载 worker 优先前往未回收掉落 cargo
            cargo_target: Optional[Position] = None
            for cpos, cst in memory.dropped_cargo.items():
                if cst.collected:
                    continue
                if manhattan(pos, cpos) > config.revisit_max_distance:
                    continue
                if cargo_target is None or manhattan(pos, cpos) < manhattan(
                    pos, cargo_target
                ):
                    cargo_target = cpos
            if cargo_target is not None and cargo_target not in claimed:
                claimed.add(cargo_target)
                if pos == cargo_target:
                    if hasattr(w, "harvest"):
                        w.harvest()
                        memory.mark_cargo_collected(cargo_target)
                        logs.append(f"worker:{uid}:reclaim_cargo")
                else:
                    direction, _ = clamp_step_toward_memo(
                        pos,
                        cargo_target,
                        obstacles,
                        last_dir=_last_move_dir.get(wkey),
                    )
                    if direction and hasattr(w, "move"):
                        _last_move_dir[wkey] = direction
                        w.move(_resolve_direction(w, direction))
                        logs.append(f"worker:{uid}:to_cargo:{direction}")
                    elif hasattr(w, "wait"):
                        w.wait()
                        logs.append(f"worker:{uid}:wait")
                continue
            # 无 cargo 可回收 → 螺旋探索
            logs.extend(
                _explore_spiral_step(
                    w=w,
                    workers=workers,
                    wkey=wkey,
                    uid=uid,
                    pos=pos,
                    core_position=core_position,
                    obstacles=obstacles,
                    enemy_positions=enemy_positions,
                    config=config,
                    memory=memory,
                    tick=tick,
                )
            )
        else:
            # 无可见资源：螺旋扫掠 + 目标点导航 + 软回撤（替代旧 recall 硬边界）
            logs.extend(
                _explore_spiral_step(
                    w=w,
                    workers=workers,
                    wkey=wkey,
                    uid=uid,
                    pos=pos,
                    core_position=core_position,
                    obstacles=obstacles,
                    enemy_positions=enemy_positions,
                    config=config,
                    memory=memory,
                    tick=tick,
                )
            )

    return logs


def _is_chunk_skippable(
    memory: Optional[MemoryMap],
    core_position: Position,
    cand: Position,
) -> bool:
    """目标点是否应被跳过：已探 chunk 且**非 Core 所在 chunk**。

    共享知识约定（决策 4）：chunk=32×32，Core 在 (10,10) 时 d≤20 本地扫掠
    几乎全在 chunk (0,0)；若字面「跳过已探 chunk 内所有点」，首次到达 Core
    即标记 (0,0) 已探 → 本地扫掠被整体吞掉。故「**Core chunk 永不跳过**」
    （枢纽允许重复扫掠）。`memory is None → False`（不跳过）。
    """
    if memory is None:
        return False
    cand_chunk = chunk_of(cand)
    if cand_chunk == chunk_of(core_position):
        return False
    return memory.is_explored(cand_chunk)


def _next_spiral_target(
    core_position: Position,
    st: SpiralState,
    sector_count: int,
    config: TacticConfig,
    memory: Optional[MemoryMap],
    obstacles: Optional[set[Position]] = None,
) -> Position:
    """生成螺旋扫掠下一目标点（P0-2），替换现有 3 处 `spiral_target` 直调。

    规则：候选点所在 chunk 已探且非 Core chunk → `st.index += 1` 跳过
    （环扫完则 ring+1 / 回绕，沿用现有推进语义）；未探或 Core chunk 直接返回。
    `obstacles` 非空时，候选点本身是障碍 → 同样跳过，避免 Worker 反复朝
    不可达格振荡（bugfix：目标点绝不落在障碍上）。
    """
    max_iter = 64  # 防死循环（Core chunk 永不跳过，理论上总能返回）
    blocked = obstacles if obstacles is not None else set()
    for _ in range(max_iter):
        cand = spiral_target(
            core_position, st.sector_id, sector_count, st.ring, st.index
        )
        if (
            not _is_chunk_skippable(memory, core_position, cand)
            and cand not in blocked
        ):
            return cand
        # 跳过：推进 index；本环扫完 → ring+1 / 回 base ring
        st.index += 1
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if st.index >= len(pts):
            st.index = 0
            st.ring += 1
            if st.ring > config.spiral_max_ring:
                st.ring = config.spiral_base_ring
    return spiral_target(
        core_position, st.sector_id, sector_count, st.ring, st.index
    )


def _beacon_explore_step(
    w: Any,
    wkey: str,
    uid: Any,
    pos: Position,
    core_position: Position,
    obstacles: set[Position],
    enemy_positions: Sequence[Position],
    config: TacticConfig,
    st: SpiralState,
    memory: Optional[MemoryMap],
    tick: int,
    dist_core: int,
    soft_recall: bool = False,
) -> list[str]:
    """beacon 阶段一步（决策 1/3/6）：每 tick 从当前 pos 朝 Beacon 生成推进目标。

    - 目标 = `beacon_progress_target(pos, beacon, step_radius, offset=st.index%3-1,
      avoid=obstacles)`，每 tick 重新生成 → 天然随 Worker 推进而推进
      （`d_beacon` 单调下降，日志 `:phase=beacon:d_beacon=...`）。
    - 卡 stall（direction None / 无进展）→ 扫描四邻障碍 `record_obstacle_block`
      + 日志 `:beacon_obstacle:pos=...:count=...`（P2-2）。
    - stall ≥ `recall_stall_ticks` → `st.index += 1` 换横向 offset 绕障（不回 Core）。
    - 软回撤切入时日志带 `:recall_soft:beacon`（P1-2）。
    """
    beacon = config.beacon_position
    if beacon is None:
        # 防御：beacon 消失（调用方已处理，此处兜底回 local）
        st.phase = "local"
        st.target = None
        st.stalled_ticks = 0
        return []

    step_radius = max(1, int(getattr(config, "beacon_step_radius", 8) or 8))
    d_beacon = manhattan(pos, beacon)
    logs: list[str] = []

    def _regenerate_target() -> Position:
        offset = (st.index % 3) - 1
        target = beacon_progress_target(
            pos, beacon, step_radius=step_radius, offset=offset, avoid=obstacles
        )
        st.target = target
        return target

    target = _regenerate_target()
    dist_to_target = manhattan(pos, target)
    direction, _ = clamp_step_toward_memo(
        pos, target, obstacles, last_dir=_last_move_dir.get(wkey)
    )

    # 进度追踪：方向为空（卡住）或未缩短与目标距离 → stall+1 + 障碍记录。
    # 紧接反向对抖（A↔B 贴墙振荡）即使缩短距离也不算进展，保证 stall
    # 能累积 → 换横向 offset 绕障。
    progressed = False
    if direction is not None:
        nxt = add_pos(pos, NAME_TO_DELTA[direction])
        if manhattan(nxt, target) < dist_to_target:
            progressed = True
    last_dir = _last_move_dir.get(wkey)
    if (
        direction is not None
        and last_dir is not None
        and direction == _opposite_dir(last_dir)
    ):
        progressed = False
    if progressed:
        st.stalled_ticks = 0
    else:
        st.stalled_ticks += 1
        if memory is not None:
            for dpos in CARDINAL_DELTAS:
                nb = add_pos(pos, dpos)
                if nb in obstacles:
                    memory.record_obstacle_block(nb, tick)
                    count = memory.obstacle_cache[nb].block_count
                    logs.append(
                        f"worker:{uid}:beacon_obstacle:pos={nb}:count={count}"
                    )

    # beacon 阶段卡 stall ≥ 阈值 → 换横向 offset（绕障，不回 Core）
    if st.stalled_ticks >= config.recall_stall_ticks:
        st.stalled_ticks = 0
        st.index += 1
        target = _regenerate_target()
        direction, _ = clamp_step_toward_memo(
            pos, target, obstacles, last_dir=_last_move_dir.get(wkey)
        )

    # 避敌改道（不强制外扩）
    avoided = False
    if enemy_positions and direction:
        direction, avoided = _pick_explore_direction_avoiding_enemies(
            pos,
            preferred=direction,
            enemies=enemy_positions,
            core_position=core_position,
            obstacles=obstacles,
        )

    suffix = ":avoid" if avoided else ""
    rl = ":recall_soft:beacon" if soft_recall else ""
    ded = ":dedicated_beacon" if st.dedicated else ""
    if direction and hasattr(w, "move"):
        _last_move_dir[wkey] = direction
        w.move(_resolve_direction(w, direction))
        logs.append(
            f"worker:{uid}:explore:{direction}:phase=beacon:ring={st.ring}"
            f":sec={st.sector_id}:stall={st.stalled_ticks}:d={dist_core}"
            f":d_beacon={d_beacon}{rl}{ded}{suffix}"
        )
    elif hasattr(w, "wait"):
        w.wait()
        logs.append(
            f"worker:{uid}:explore:None:phase=beacon:ring={st.ring}"
            f":sec={st.sector_id}:stall={st.stalled_ticks}:d={dist_core}"
            f":d_beacon={d_beacon}{rl}{ded}"
        )
    return logs


def _explore_spiral_step(
    w: Any,
    workers: Sequence[Any],
    wkey: str,
    uid: Any,
    pos: Position,
    core_position: Position,
    obstacles: set[Position],
    enemy_positions: Sequence[Position],
    config: TacticConfig,
    memory: Optional[MemoryMap] = None,
    tick: int = 0,
) -> list[str]:
    """无可见资源时的螺旋扫掠一步（两阶段状态机 + 目标点导航 + 软回撤）。

    设计要点（决策 1）：
    - **local**：删除 recall_dist 硬边界与「绝不朝 Core 收缩」守卫——目标点
      导航沿环切向移动允许曼哈顿距离暂时持平/微降；到达目标 → index+1；
      本环扫完 → ring+1；ring 超 spiral_max_ring → 回 base ring。
      连续 recall_stall_ticks 无进展 → 软回撤：**若 Beacon 存在则切 beacon**
      （P0-1/P1-2，日志 `:recall_soft:beacon`）；否则**向外扩一层（ring+1）
      并把 index 跳到环对面（+len(pts)//2）**——探索只向外扩散，绝不因
      stall 收缩回 Core（bugfix：软回撤不再 ring-1）。
    - **beacon**：每 tick 朝 Beacon 生成推进目标（决策 3），stall 换 offset
      绕障（决策 6）；Beacon 消失 → 回 local；非 dedicated 到达近旁 → 回 local。
    - **dedicated**（P1-1）：`widx==0` 且 Beacon 存在 → 专职 Beacon，恒为 beacon。
    - **绝对安全网**（极少触发）：**仅 local 阶段生效**——距 Core 过远直接朝
      Core 一步；beacon 阶段以 Beacon 方向为准，不受 Core 距离限制。
    - **到达标记**（P0-2）：`mark_explored(pos, tick)`，新 chunk → `new_chunk` 日志。
    - 避敌仅改道（_pick_explore_direction_avoiding_enemies），不强制外扩。

    返回本 worker 的日志行列表（local 含 :ring=..:sec=..:stall=..；
    beacon 追加 :phase=beacon:d_beacon=.. / :dedicated_beacon）。
    """
    logs: list[str] = []
    sector_count = max(1, config.sector_count)
    widx = _worker_index(uid, workers, fallback=0)
    st = _spiral_state.get(wkey)
    if st is None:
        st = SpiralState(
            ring=config.spiral_base_ring,
            sector_id=widx % sector_count,
            index=0,
            target=None,
            stalled_ticks=0,
            ring_done=False,
        )
        _spiral_state[wkey] = st

    dist_core = manhattan(pos, core_position)

    # 到达标记：记录已探 chunk（新 chunk → new_chunk 日志）
    if memory is not None and memory.mark_explored(pos, tick):
        cx, cy = memory.chunk_of(pos)
        logs.append(f"worker:{uid}:new_chunk=({cx},{cy})")

    # P1-1 dedicated 指派：widx==0 且 Beacon 存在 → 专职 Beacon
    if config.beacon_position is not None and widx == 0 and not st.dedicated:
        st.dedicated = True
        st.phase = "beacon"
        logs.append(f"worker:{uid}:dedicated_beacon")
    # dedicated 强制 beacon（Beacon 存在时）
    if st.dedicated and config.beacon_position is not None:
        st.phase = "beacon"

    # beacon 阶段且 Beacon 消失 / 被拾取 → 回 local（P2-1）
    if st.phase == "beacon" and config.beacon_position is None:
        st.phase = "local"
        st.target = None
        st.stalled_ticks = 0

    # 绝对安全网（**仅 local 阶段生效**）：距 Core 过远直接朝 Core 一步
    if st.phase == "local" and dist_core > config.spiral_max_ring + 8:
        direction, _ = clamp_step_toward_memo(
            pos, core_position, obstacles, last_dir=_last_move_dir.get(wkey)
        )
        if direction and hasattr(w, "move"):
            _last_move_dir[wkey] = direction
            st.stalled_ticks = 0
            w.move(_resolve_direction(w, direction))
            logs.append(
                f"worker:{uid}:explore:{direction}:ring={st.ring}:sec={st.sector_id}"
                f":stall={st.stalled_ticks}:d={dist_core}:recall_soft"
            )
        elif hasattr(w, "wait"):
            w.wait()
            logs.append(f"worker:{uid}:wait_idle")
        return logs

    # ---- beacon 阶段 ----
    if st.phase == "beacon":
        beacon = config.beacon_position
        step_radius = max(1, int(getattr(config, "beacon_step_radius", 8) or 8))
        # 非 dedicated 到达 Beacon 近旁 → 回 local（决策 1）
        if (
            not st.dedicated
            and beacon is not None
            and manhattan(pos, beacon) <= step_radius
        ):
            st.phase = "local"
            st.target = None
            st.stalled_ticks = 0
        else:
            logs.extend(
                _beacon_explore_step(
                    w=w,
                    wkey=wkey,
                    uid=uid,
                    pos=pos,
                    core_position=core_position,
                    obstacles=obstacles,
                    enemy_positions=enemy_positions,
                    config=config,
                    st=st,
                    memory=memory,
                    tick=tick,
                    dist_core=dist_core,
                    soft_recall=False,
                )
            )
            return logs

    # ---- local 阶段（目标点导航）----
    if st.target is None:
        st.target = _next_spiral_target(
            core_position, st, sector_count, config, memory, obstacles
        )
    dist_to_target = manhattan(pos, st.target)

    # 到达目标 → 推进 index / ring
    if pos == st.target:
        st.index += 1
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if st.index >= len(pts) or st.ring_done:
            st.index = 0
            st.ring_done = False
            st.ring += 1
            if st.ring > config.spiral_max_ring:
                st.ring = config.spiral_base_ring  # 回 base ring 重新开始
        st.stalled_ticks = 0
        st.target = _next_spiral_target(
            core_position, st, sector_count, config, memory, obstacles
        )
        dist_to_target = manhattan(pos, st.target)

    direction, _ = clamp_step_toward_memo(
        pos, st.target, obstacles, last_dir=_last_move_dir.get(wkey)
    )

    # 进度追踪：方向为空（卡住）、目标本身是障碍（不可达）、紧接反向对抖
    # （A↔B 贴墙振荡）或未缩短与目标距离 → stall+1。
    # 反向对抖即使 manhattan 缩短也不算进展（如贴墙横跳），保证 stall
    # 能累积 → 软回撤外扩（bugfix：不再因振荡永远卡在同一环）。
    last_dir = _last_move_dir.get(wkey)
    if st.target in obstacles:
        st.stalled_ticks += 1
    elif direction is None:
        st.stalled_ticks += 1
    elif last_dir is not None and direction == _opposite_dir(last_dir):
        st.stalled_ticks += 1
    else:
        nxt = add_pos(pos, NAME_TO_DELTA[direction])
        if manhattan(nxt, st.target) >= dist_to_target:
            st.stalled_ticks += 1
        else:
            st.stalled_ticks = 0

    # 软回撤：连续 recall_stall_ticks 无进展
    soft_recall = False
    if st.stalled_ticks >= config.recall_stall_ticks:
        st.stalled_ticks = 0
        soft_recall = True
        if config.beacon_position is not None:
            # P0-1 / P1-2：软回撤优先切向 Beacon（而非 ring-1 向 Core 收缩）
            st.phase = "beacon"
            logs.extend(
                _beacon_explore_step(
                    w=w,
                    wkey=wkey,
                    uid=uid,
                    pos=pos,
                    core_position=core_position,
                    obstacles=obstacles,
                    enemy_positions=enemy_positions,
                    config=config,
                    st=st,
                    memory=memory,
                    tick=tick,
                    dist_core=dist_core,
                    soft_recall=True,
                )
            )
            return logs
        # 无 Beacon：软回撤 **向外扩** —— 绝不因 stall 收缩回 Core。
        # 1) ring+1（向外扩一层；达到上限后保持 max ring，不收缩）
        # 2) index 跳到环对面（+len(pts)//2），避免反复卡同一障碍
        st.ring += 1
        if st.ring > config.spiral_max_ring:
            st.ring = config.spiral_max_ring
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if pts:
            st.index = (st.index + len(pts) // 2) % len(pts)
        else:
            st.index = 0
        st.target = _next_spiral_target(
            core_position, st, sector_count, config, memory, obstacles
        )
        direction, _ = clamp_step_toward_memo(
            pos, st.target, obstacles, last_dir=_last_move_dir.get(wkey)
        )
        if direction is None:
            # 兜底：优先远离 Core（绝不因 stall 收缩）；仅四向皆挡时为 None
            direction = outward_step(
                pos,
                core_position,
                obstacles=obstacles,
                last_dir=_last_move_dir.get(wkey),
            )

    # 避敌改道（不强制外扩）
    avoided = False
    if enemy_positions and direction:
        direction, avoided = _pick_explore_direction_avoiding_enemies(
            pos,
            preferred=direction,
            enemies=enemy_positions,
            core_position=core_position,
            obstacles=obstacles,
        )

    if direction and hasattr(w, "move"):
        _last_move_dir[wkey] = direction
        w.move(_resolve_direction(w, direction))
        suffix = ":avoid" if avoided else ""
        rl = ":recall_soft" if soft_recall else ""
        logs.append(
            f"worker:{uid}:explore:{direction}:ring={st.ring}:sec={st.sector_id}"
            f":stall={st.stalled_ticks}:d={dist_core}{rl}{suffix}"
        )
    elif hasattr(w, "wait"):
        w.wait()
        rl = ":recall_soft" if soft_recall else ""
        logs.append(
            f"worker:{uid}:explore:None:ring={st.ring}:sec={st.sector_id}"
            f":stall={st.stalled_ticks}:d={dist_core}{rl}"
        )

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
