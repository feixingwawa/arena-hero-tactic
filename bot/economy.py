"""经济模块：采集 / 交付 / 生产优先级 / 探索。

与 I/O 解耦：接收 turn + RolePlan，直接在单位对象上排队动作。
v0.14：无维护费；动态价格走 bot.rules.unit_cost_for；探索为螺旋扫掠 + 软回撤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.memory import MemoryMap
from bot.rules import core_resource_capacity, unit_cost_for
from bot.pathing import (
    CARDINAL_DELTAS,
    NAME_TO_DELTA,
    LoopTracker,
    Position,
    add_pos,
    beacon_oriented_spiral_target,
    beacon_progress_target,
    bfs_next_step,
    chunk_of,
    clamp_step_toward_memo,
    cells_toward_ring,
    estimate_path_steps,
    guided_step_toward,
    manhattan,
    nearest,
    opposite_name,
    outward_step,
    sector_points,
    spiral_target,
)
from bot.roles import (
    Role,
    RolePlan,
    collect_enemy_positions,
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


@dataclass
class WorkerIntent:
    """Dashboard / 调试用：本 tick Worker 意图（目标点 + 阶段）。

    与 SpiralState 解耦：探索走 spiral，harvest/deposit/retreat 等走 intent。
    get_worker_states() 合并两者供 main → build_snapshot 导出。
    route_waypoints：本 tick 运行时 A* 航点（与 guided 实际执行同源），供前端黄线一致。
    """

    target: Optional[Position] = None
    ring: Optional[int] = None
    sector: Optional[int] = None
    phase: Optional[str] = None  # harvest|deposit|retreat|to_resource|to_cargo|explore|local|beacon|...
    role: Optional[str] = None
    dedicated: Optional[bool] = None
    # 运行时 LoopTracker 未走完的 A* 航点（不含当前格）；None/[] 表示本 tick 无缓存 route
    route_waypoints: Optional[list[Position]] = None
    route_dest: Optional[Position] = None


# 非探索移动（return_deposit / to_resource / retreat / recall）的方向记忆，防 A↔B 对抖
_last_move_dir: dict[str, str] = {}
# 螺旋扫掠探索状态（按 worker id 字符串键）
_spiral_state: dict[str, SpiralState] = {}
# 本 tick 显式意图（command_workers 开头清空，分支写入）
_worker_intents: dict[str, WorkerIntent] = {}
# 范围循环检测：return_deposit / to_resource / retreat 足迹（小范围重复 → 强制重寻路）
_loop_trackers: dict[str, LoopTracker] = {}
# return_deposit 进展追踪：{wkey: (best_man_to_core, last_improve_tick, repath_streak)}
# 用于打破「:repath:loop 连续触发但 man 不降」的局部卡死（线上 fa7407d7 类）。
_deposit_progress: dict[str, tuple[int, int, int]] = {}
# 无进展 tick 阈值：连续 N tick man 未严格下降 → 强制逃逸换侧 + 清足迹
_DEPOSIT_STALL_TICKS: int = 10
# repath:loop 连续触发阈值：超过则强制换 repath_side 并放宽 soft trail
_DEPOSIT_REPATH_STREAK: int = 3
# return 路径资源目标 claim TTL（tick）
_PENDING_RETURN_CLAIM_TTL: int = 16
# return 路径资源目标占用：{worker_key: (mine_pos, claim_tick)}
_pending_return_mines: dict[str, tuple[tuple[int, int], int]] = {}
# 近距抢占 claim：空载 Worker 在矿曼哈顿 ≤ 此值时，可抢走更远 owner 的 claim
# （防「远方工人 claim 后自己还在 beacon 推进，近处发现者却去探索」）
_CLAIM_STEAL_DIST: int = 5
# 经济健康追踪：上次交付 tick 与停滞计数
health_tracker: dict = {"last_deposit_tick": 0, "stall_ticks": 0}
# 跨 tick 资源目标去重：{pos: (claim_tick, worker_key)}。同一目标被其他 Worker
# 在 claim_ttl_ticks 内占用时，本 Worker 不再选择（防多 Worker 汇聚同点导致
# same-point tie 多数失败）。自己的 claim 不排除（保持对目标的持续推进）。
_claimed_targets: dict[Position, tuple[int, str]] = {}
_CLAIM_TTL_TICKS: int = 8


def _purge_expired_resource_claims(tick: int) -> None:
    """清理过期的跨 tick 资源 claim。

    含 tick 回退（单测 tick 不单调）时 claim_tick > tick 的脏数据。
    """
    expired = [
        pos
        for pos, (claim_tick, _) in _claimed_targets.items()
        if claim_tick > tick or tick - claim_tick > _CLAIM_TTL_TICKS
    ]
    for pos in expired:
        _claimed_targets.pop(pos, None)


def _claim_resource_target(
    pos: Position,
    wkey: str,
    tick: int,
    claimed: set[Position],
) -> None:
    """本 tick + 跨 tick 占用同一资源点（一矿一人，防全员扎堆）。"""
    claimed.add(pos)
    _claimed_targets[pos] = (tick, wkey)


def _resource_claimed_by_other(
    pos: Position,
    wkey: str,
    tick: int,
    claimed: set[Position],
    *,
    worker_pos: Optional[Position] = None,
    owner_pos: Optional[Position] = None,
) -> bool:
    """资源点是否已被其他 Worker 占用（本 tick claimed 或未过期跨 tick claim）。

    近距抢占：若 ``worker_pos`` 距矿 ≤ ``_CLAIM_STEAL_DIST``，且比 ``owner_pos``
    更近（或 owner 位置未知），则**不算**被占用，允许本 Worker 抢走 claim。
    """
    entry = _claimed_targets.get(pos)
    if entry is not None:
        claim_tick, owner = entry
        if claim_tick > tick or tick - claim_tick > _CLAIM_TTL_TICKS:
            _claimed_targets.pop(pos, None)
            entry = None
        elif owner != wkey:
            # 近距发现者可抢远方 claim
            if worker_pos is not None and manhattan(worker_pos, pos) <= _CLAIM_STEAL_DIST:
                if owner_pos is None or manhattan(worker_pos, pos) < manhattan(owner_pos, pos):
                    return False
            return True
    # 本 tick 已被更早处理的 Worker 写入 claimed，且不是自己跨 tick 续约的目标
    if pos in claimed:
        if entry is not None and entry[1] == wkey and tick - entry[0] <= _CLAIM_TTL_TICKS:
            return False
        # 近距抢占：本 tick 他人已 claimed，但自己更近 → 允许
        if (
            worker_pos is not None
            and manhattan(worker_pos, pos) <= _CLAIM_STEAL_DIST
            and (owner_pos is None or manhattan(worker_pos, pos) < manhattan(owner_pos, pos))
        ):
            return False
        # 无主记录但已在本 tick claimed → 他人占用
        if entry is None or entry[1] != wkey:
            return True
    return False


def _pick_resource_target(
    pos: Position,
    wkey: str,
    tick: int,
    candidates: list[Position],
    claimed: set[Position],
    worker_positions: Optional[dict[str, Position]] = None,
) -> Optional[Position]:
    """为 Worker 选矿：优先续约自己的 claim；否则最近未占用矿。

    **绝不**在无空闲矿时回退到已占用矿（旧逻辑 `nearest(candidates)` 会导致
    发现 1 个矿后全员 `to_resource` 扎堆）。
    近距（≤ ``_CLAIM_STEAL_DIST``）且比 claim owner 更近的空载 Worker 可抢占。
    """
    positions = worker_positions or {}

    def _owner_pos_of(mine: Position) -> Optional[Position]:
        entry = _claimed_targets.get(mine)
        if entry is None:
            return None
        return positions.get(entry[1])

    # 1) 续约：自己未过期 claim 且仍在候选中
    own_pos: Optional[Position] = None
    own_dist = 10**9
    for c in candidates:
        entry = _claimed_targets.get(c)
        if entry is None:
            continue
        claim_tick, owner = entry
        if owner != wkey or claim_tick > tick or tick - claim_tick > _CLAIM_TTL_TICKS:
            continue
        d = manhattan(pos, c)
        if d < own_dist:
            own_dist = d
            own_pos = c
    if own_pos is not None and not _resource_claimed_by_other(
        own_pos, wkey, tick, claimed, worker_pos=pos, owner_pos=_owner_pos_of(own_pos)
    ):
        return own_pos

    # 2) 最近空闲矿（含近距可抢占）
    available = [
        c for c in candidates
        if not _resource_claimed_by_other(
            c, wkey, tick, claimed, worker_pos=pos, owner_pos=_owner_pos_of(c)
        )
    ]
    if not available:
        return None
    return nearest(pos, available)


def _get_loop_tracker(wkey: str) -> LoopTracker:
    st = _loop_trackers.get(wkey)
    if st is None:
        st = LoopTracker()
        _loop_trackers[wkey] = st
    return st


def _clear_deposit_progress(wkey: str) -> None:
    """交付成功或放弃回城时清理进展状态。"""
    _deposit_progress.pop(wkey, None)


def _force_deposit_escape(
    pos: Position,
    core_position: Position,
    obstacles: set[Position],
    wkey: str,
    tick: int,
    memory: Optional[Any] = None,
) -> Optional[str]:
    """return_deposit 长期无进展时的强制逃逸一步。

    策略（线上 fa7407d7 / acb08516 类：repath:loop 空转 man 不降）：
    1. 清空 LoopTracker 足迹 + last_dir，避免 soft trail 自我堵死；
    2. **短 BFS 最短绕障第一步**（真正离开口袋，不再只靠侧向横跳）；
    3. BFS 失败再：强制切换 repath_side + 跨主轴侧向 / 主轴 / 任意非硬障。
    """
    tracker = _get_loop_tracker(wkey)
    # 保留 repath_side 翻转意图，但 reset 清 route/足迹
    prev_side = int(getattr(tracker, "repath_side", 0) or 0)
    tracker.reset()
    tracker.repath_side = 1 - prev_side
    tracker.cooldown = 0
    tracker.last_repath_tick = tick
    _last_move_dir.pop(wkey, None)

    hard: set[Position] = set(obstacles)
    if memory is not None:
        mem_obs = getattr(memory, "obstacles", None)
        if mem_obs is not None:
            try:
                hard |= set(mem_obs)
            except Exception:
                pass

    man0 = manhattan(pos, core_position)
    # 与 guided 对齐：远距迷宫需要更大 expand，否则 BFS 失败又回侧向横跳
    cap = 800 if man0 <= 16 else (2000 if man0 <= 48 else 5000)
    from bot.pathing import _install_bfs_route

    installed = _install_bfs_route(
        pos,
        core_position,
        hard,
        tracker,
        tick,
        max_expand=cap,
        avoid=None,
        ttl=72,
    )
    if installed is not None:
        _last_move_dir[wkey] = installed
        return installed
    # install 失败时仍试一步（无 route）
    bfs_dir = bfs_next_step(pos, core_position, hard, max_expand=cap)
    if bfs_dir is not None:
        _last_move_dir[wkey] = bfs_dir
        return bfs_dir

    primary = None
    dx = core_position[0] - pos[0]
    dy = core_position[1] - pos[1]
    if abs(dx) >= abs(dy) and dx != 0:
        primary = "RIGHT" if dx > 0 else "LEFT"
    elif dy != 0:
        primary = "DOWN" if dy > 0 else "UP"
    elif dx != 0:
        primary = "RIGHT" if dx > 0 else "LEFT"

    side = int(getattr(tracker, "repath_side", 0) or 0)
    if primary in ("UP", "DOWN"):
        perps = ("RIGHT", "LEFT") if side == 0 else ("LEFT", "RIGHT")
    elif primary in ("LEFT", "RIGHT"):
        perps = ("DOWN", "UP") if side == 0 else ("UP", "DOWN")
    else:
        perps = ("RIGHT", "LEFT", "DOWN", "UP")

    prefer: list[str] = list(perps)
    if primary:
        prefer.append(primary)
    anti = opposite_name(primary) if primary else None
    if anti:
        prefer.append(anti)
    for name in ("RIGHT", "LEFT", "DOWN", "UP"):
        if name not in prefer:
            prefer.append(name)

    best: Optional[str] = None
    best_key: Optional[tuple] = None
    for name in prefer:
        if name not in NAME_TO_DELTA:
            continue
        nxt = add_pos(pos, NAME_TO_DELTA[name])
        if nxt in hard:
            continue
        man_n = manhattan(nxt, core_position)
        # 1) 绝不优先走远（有 man 不升选项时）；2) 同层侧向优先打破口袋；3) 更近 Core
        farther = 1 if man_n > man0 else 0
        is_perp = 0 if name in perps else 1
        key = (farther, is_perp, man_n)
        if best_key is None or key < best_key:
            best_key = key
            best = name
    if best:
        _last_move_dir[wkey] = best
    return best


def _return_deposit_step(
    pos: Position,
    core_position: Position,
    obstacles: set[Position],
    wkey: str,
    uid: Any,
    config: TacticConfig,
    tick: int,
    memory: Optional[Any] = None,
) -> tuple[Optional[str], str]:
    """满货回城一步：带无进展逃逸。返回 (direction, log_tag_suffix)。

    log_tag_suffix 形如 ``LEFT`` / ``LEFT:repath:loop`` / ``DOWN:escape:stall``。
    """
    man = manhattan(pos, core_position)
    prev = _deposit_progress.get(wkey)
    if prev is None:
        best_man, last_improve, repath_streak = man, tick, 0
    else:
        best_man, last_improve, repath_streak = prev
        if man < best_man:
            best_man = man
            last_improve = tick
            repath_streak = 0
        elif man > best_man + 2:
            # 明显走远：重置 best，避免旧近点锁死逃逸
            best_man = man
            last_improve = tick

    stall_ticks = tick - last_improve if tick >= last_improve else 0
    need_escape = stall_ticks >= _DEPOSIT_STALL_TICKS or repath_streak >= _DEPOSIT_REPATH_STREAK

    if need_escape:
        direction = _force_deposit_escape(
            pos, core_position, obstacles, wkey, tick, memory=memory
        )
        # 逃逸后给一个宽限期：清 streak，best 取当前 man
        _deposit_progress[wkey] = (man, tick, 0)
        if direction:
            reason = "stall" if stall_ticks >= _DEPOSIT_STALL_TICKS else "repath_streak"
            return direction, f"{direction}:escape:{reason}"
        # 逃逸失败则回落普通 guided

    direction, repath = _guided_move(
        pos, core_position, obstacles, wkey, config, tick=tick, memory=memory
    )
    if repath:
        repath_streak += 1
    else:
        repath_streak = max(0, repath_streak - 1)
    _deposit_progress[wkey] = (best_man, last_improve, repath_streak)

    if not direction:
        return None, ""
    tag = direction
    if repath:
        tag += ":repath:loop"
    return direction, tag


def _guided_move(
    pos: Position,
    target: Position,
    obstacles: set[Position],
    wkey: str,
    config: TacticConfig,
    tick: int = 0,
    memory: Optional[Any] = None,
    *,
    prefer_bfs: bool = True,
) -> tuple[Optional[str], bool]:
    """带循环检测的朝目标一步；返回 (direction, did_repath)。

    prefer_bfs=True（回城/奔矿/撤退默认）：短 BFS 破障边口袋，避免黄线绕圈。
    """
    tracker = _get_loop_tracker(wkey)
    # 合并 memory 永久障碍，避免只看见本 tick 可见障碍导致贴墙空转
    blocked: set[Position] = set(obstacles)
    if memory is not None:
        mem_obs = getattr(memory, "obstacles", None)
        if mem_obs is not None:
            try:
                blocked |= set(mem_obs)
            except Exception:
                pass
    direction, new_last, did_repath = guided_step_toward(
        pos,
        target,
        blocked,
        last_dir=_last_move_dir.get(wkey),
        prefer_bfs=prefer_bfs,
        tracker=tracker,
        memory=memory,
        window=int(getattr(config, "loop_window_ticks", 12) or 12),
        min_unique=int(getattr(config, "loop_min_unique", 4) or 4),
        bbox_diameter_max=int(getattr(config, "loop_bbox_diameter", 3) or 3),
        static_ticks=int(getattr(config, "loop_static_ticks", 4) or 4),
        repath_cooldown=int(getattr(config, "loop_repath_cooldown", 5) or 5),
        tick=tick,
    )
    if direction:
        _last_move_dir[wkey] = direction
    elif new_last is None and did_repath:
        _last_move_dir.pop(wkey, None)
    return direction, did_repath


def _worker_key(uid: Any) -> str:
    return str(uid)


def _set_intent(
    wkey: str,
    *,
    target: Optional[Position] = None,
    phase: Optional[str] = None,
    role: Optional[str] = None,
    ring: Optional[int] = None,
    sector: Optional[int] = None,
    dedicated: Optional[bool] = None,
) -> None:
    """写入/覆盖本 tick Worker 意图（Dashboard 路径可视化）。"""
    cur = _worker_intents.get(wkey)
    if cur is None:
        cur = WorkerIntent()
        _worker_intents[wkey] = cur
    if target is not None:
        cur.target = target
    if phase is not None:
        cur.phase = phase
    if role is not None:
        cur.role = role
    if ring is not None:
        cur.ring = ring
    if sector is not None:
        cur.sector = sector
    if dedicated is not None:
        cur.dedicated = dedicated


def get_worker_states() -> dict[str, WorkerIntent]:
    """导出 Worker 状态供 Dashboard：合并 spiral + 本 tick intent。

    键为 str(worker.id)。intent 字段优先覆盖 spiral 同名字段。
    附带 LoopTracker.route_waypoints（运行时 A* 缓存），Dashboard 黄线优先画它。
    main.run_session 通过 getattr(bot.economy, "get_worker_states") 调用。
    """
    out: dict[str, WorkerIntent] = {}
    for k, st in _spiral_state.items():
        out[k] = WorkerIntent(
            target=st.target,
            ring=int(st.ring) if st.ring is not None else None,
            sector=int(st.sector_id) if st.sector_id is not None else None,
            phase=str(st.phase) if st.phase is not None else None,
            role="SCOUT",
            dedicated=bool(st.dedicated),
        )
    for k, wi in _worker_intents.items():
        base = out.get(k, WorkerIntent())
        out[k] = WorkerIntent(
            target=wi.target if wi.target is not None else base.target,
            ring=wi.ring if wi.ring is not None else base.ring,
            sector=wi.sector if wi.sector is not None else base.sector,
            phase=wi.phase if wi.phase is not None else base.phase,
            role=wi.role if wi.role is not None else base.role,
            dedicated=wi.dedicated if wi.dedicated is not None else base.dedicated,
        )
    # 挂上本 tick 实际执行的 A* route（与 guided_step_toward / _install_bfs_route 同源）
    for k, intent in list(out.items()):
        tr = _loop_trackers.get(k)
        if tr is None or not tr.route_waypoints:
            continue
        raw = list(tr.route_waypoints)
        wps: list[Position] = []
        for p in raw:
            try:
                wps.append((int(p[0]), int(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        if not wps:
            continue
        dest = tr.route_dest
        dest_t: Optional[Position] = None
        if dest is not None:
            try:
                dest_t = (int(dest[0]), int(dest[1]))
            except (TypeError, ValueError, IndexError):
                dest_t = None
        out[k] = WorkerIntent(
            target=intent.target,
            ring=intent.ring,
            sector=intent.sector,
            phase=intent.phase,
            role=intent.role,
            dedicated=intent.dedicated,
            route_waypoints=wps,
            route_dest=dest_t,
        )
    return out


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
    visible_threats: Any = None,
    has_near_threat: bool = False,
    has_far_threat: bool = False,
    resources: Optional[int] = None,
) -> Optional[str]:
    """Core 空闲时的生产决策，返回 "WORKER"|"VANGUARD"|"RANGER"|None。

    阈值触发型生产调度（Task 3）：
    - Worker 阈值 3/6/9/12 触发对应 V/R 编制要求
    - 紧急 override：近威胁 + V==0 → 优先 VANGUARD（可消耗 reserve）
    - 阶段：补 V/R 达标 → 补 W 到下一阈值 → W=12 后补 V/R 到 4/4
    - 欠编战斗单位时禁止再扩工人，优先攒钱出 V/R（修复 7W0V0R）
    - 兼容：阈值后仍按 config.target 补齐超编（max_population 允许时）

    成本一律走 `rules.unit_cost_for(name, pop + 1)`（spawn 后人口估算）。
    """
    if resources is None:
        core = getattr(turn, 'core', None)
        if core is not None and getattr(core, 'resources', None) is not None:
            resources = int(core.resources)
        elif getattr(turn, 'resources', None) is not None:
            resources = int(turn.resources)
        else:
            state = getattr(turn, 'state', None)
            if state is not None and getattr(state, 'resources', None) is not None:
                resources = int(state.resources)
            else:
                resources = 0
    counts = count_by_type(turn)
    pop = sum(counts.values())
    reserve = effective_reserve(pop, config)

    if pop >= config.max_population:
        return None

    def _try_type(name: str, *, ignore_reserve: bool = False) -> bool:
        cost = unit_cost_for(name, pop + 1)
        # 战斗单位补编 / 紧急出兵允许吃 reserve，避免「永远只有工人」
        need_reserve = 0 if ignore_reserve else reserve
        if resources - cost < need_reserve:
            return False
        if pop + 1 > config.max_population:
            return False
        return True

    V_reqs = {3: 1, 6: 2, 9: 3, 12: 4}
    R_reqs = {6: 1, 9: 2, 12: 4}
    w = counts.get("WORKER", 0)
    v = counts.get("VANGUARD", 0)
    r = counts.get("RANGER", 0)

    # --- 紧急 override：近威胁 + V==0（可吃光 reserve）---
    if has_near_threat and v == 0:
        if _try_type("VANGUARD", ignore_reserve=True):
            return "VANGUARD"

    # --- 阈值阶段逻辑 ---
    current_v_req = 0
    for threshold in sorted(V_reqs.keys()):
        if w >= threshold:
            current_v_req = min(V_reqs[threshold], config.target_vanguards)
    current_r_req = 0
    for threshold in sorted(R_reqs.keys()):
        if w >= threshold:
            current_r_req = min(R_reqs[threshold], config.target_rangers)

    # 欠编战斗单位：允许 ignore_reserve
    combat_behind = (v < current_v_req) or (r < current_r_req)
    if v < current_v_req:
        if _try_type("VANGUARD", ignore_reserve=True):
            return "VANGUARD"
    if r < current_r_req:
        if _try_type("RANGER", ignore_reserve=True):
            return "RANGER"

    # 已欠 V/R 编制时：禁止再扩工人，优先攒钱出战斗单位
    w_soft_cap = min(12, config.target_workers)
    if w < w_soft_cap and not combat_behind:
        if _try_type("WORKER"):
            return "WORKER"

    final_v_cap = min(4, config.target_vanguards)
    final_r_cap = min(4, config.target_rangers)
    if v < final_v_cap:
        if _try_type("VANGUARD", ignore_reserve=True):
            return "VANGUARD"
    if r < final_r_cap:
        if _try_type("RANGER", ignore_reserve=True):
            return "RANGER"

    # --- 兼容 config.target：阈值完成后补超编（target_workers>12 等场景）---
    still_need_combat = (v < config.target_vanguards) or (r < config.target_rangers)
    if w < config.target_workers and not still_need_combat:
        if _try_type("WORKER"):
            return "WORKER"
    if v < config.target_vanguards:
        if _try_type("VANGUARD", ignore_reserve=True):
            return "VANGUARD"
    if r < config.target_rangers:
        if _try_type("RANGER", ignore_reserve=True):
            return "RANGER"

    # --- 资源满/充裕：按目标比例继续生产 W/V/R（可超 soft target，受 max_population 约束）---
    # 「满」：resources >= capacity 的 80%，或绝对量足够再出一波编制
    capacity = core_resource_capacity(pop)
    res_full = resources >= max(int(capacity * 0.8), 1)
    # 也把「远超最贵单位成本」视为充裕，避免 capacity 很小却不生产
    max_unit_cost = max(
        unit_cost_for("WORKER", pop + 1),
        unit_cost_for("VANGUARD", pop + 1),
        unit_cost_for("RANGER", pop + 1),
    )
    res_abundant = resources >= max(max_unit_cost * 2, config.reserve_resources + max_unit_cost)
    if (res_full or res_abundant) and pop < config.max_population:
        tw = max(1, int(config.target_workers))
        tv = max(1, int(config.target_vanguards))
        tr = max(1, int(config.target_rangers))
        # 相对目标比例的缺口：越小越优先补
        # 用 counts/target 比率，选最欠的类型
        ratios = {
            "WORKER": (w / tw) if tw else 999.0,
            "VANGUARD": (v / tv) if tv else 999.0,
            "RANGER": (r / tr) if tr else 999.0,
        }
        # 稳定次序：同比率时 W → V → R（经济优先）
        order = sorted(ratios.keys(), key=lambda n: (ratios[n], {"WORKER": 0, "VANGUARD": 1, "RANGER": 2}[n]))
        for name in order:
            # 比例生产可吃 reserve，避免卡在 reserve 边界永远不造
            if _try_type(name, ignore_reserve=True):
                return name

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
    # 每 tick 清空意图表，由各分支 _set_intent 重新写入
    _worker_intents.clear()
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            return logs
        core_position = _as_position(core.position)

    resources_cells = _resource_cells(turn)
    obstacles = _obstacle_cells(turn)
    # Core 格通常不可作为障碍，但移动时不要踩未知；此处仅避 obstacle_cells
    workers = list(getattr(turn, "workers", None) or ())
    # wkey → 当前位置（近距抢占 claim 时比较 owner 距离）
    worker_positions: dict[str, Position] = {
        _worker_key(w.id): _as_position(w.position) for w in workers
    }
    # 总人口（W+V+R）：本地探索度/人口阈值向信标推进用
    population = total_population(turn)
    # 撤退威胁仍只用战斗单位（role_plan.threat_positions 不含敌方 WORKER）
    # 寻路/探索绕行：含敌方 WORKER，避免矿工对撞叠格却不绕开
    combat_enemy_positions: list[Position] = list(role_plan.threat_positions or [])
    enemy_positions: list[Position] = list(collect_enemy_positions(turn))
    if not enemy_positions and combat_enemy_positions:
        # 兼容无 visible_enemies 仅注入 threat 的旧单测
        enemy_positions = list(combat_enemy_positions)
    soft_enemy_obs: set[Position] = set(enemy_positions)
    tick = int(getattr(turn, "tick", 0) or 0)

    def _path_obstacles_for_goal(goal: Optional[Position] = None) -> set[Position]:
        """硬障碍 + 可见敌人（含敌方 WORKER）软障碍；目标格本身不挡。"""
        obs: set[Position] = set(obstacles) | soft_enemy_obs
        if goal is not None and goal in obs:
            obs.discard(goal)
        # 永不把己方 Core 当地形障碍
        if core_position in obs:
            obs.discard(core_position)
        return obs

    def _deposit_path_obstacles(self_pos: Position, w_uid: Any = None) -> set[Position]:
        """交付寻路：绕开其它己方单位（除自己与 Core），减少核周叠堵。"""
        obs = _path_obstacles_for_goal(core_position)
        for ox in workers:
            if w_uid is not None and ox.id == w_uid:
                continue
            op = _as_position(ox.position)
            if op == self_pos or op == core_position:
                continue
            # 满货自己人仍挡路：逼分散进核，避免全员叠同一邻格 escape:stall
            obs.add(op)
        # 战斗单位也当软障
        for ox in list(getattr(turn, "vanguards", None) or ()) + list(
            getattr(turn, "rangers", None) or ()
        ):
            op = _as_position(ox.position)
            if op == core_position:
                continue
            obs.add(op)
        return obs

    # 资源目标去重：多个 worker 尽量分配不同资源点（本 tick）
    # 并与跨 tick `_claimed_targets` 合并，避免「一矿全员 to_resource」。
    claimed: set[Position] = set()
    _purge_expired_resource_claims(tick)
    alive_keys = {_worker_key(w.id) for w in workers}
    for cpos, (ctick, owner) in list(_claimed_targets.items()):
        if owner not in alive_keys:
            _claimed_targets.pop(cpos, None)
            continue
        if ctick > tick or tick - ctick > _CLAIM_TTL_TICKS:
            _claimed_targets.pop(cpos, None)
            continue
        claimed.add(cpos)

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

    # ===== Task 6: 阶段 A — 清理过期或角色异常的 _pending_return_mines 预约 =====
    for w in workers:
        wkey = _worker_key(w.id)
        if wkey in _pending_return_mines:
            mine_pos, claim_tick = _pending_return_mines[wkey]
            if tick - claim_tick > _PENDING_RETURN_CLAIM_TTL:
                del _pending_return_mines[wkey]
                continue
            assignment = role_plan.get(w.id)
            wrole = assignment.role if assignment else Role.HARVESTER
            if wrole in (Role.RETREAT, Role.HEAL):
                del _pending_return_mines[wkey]

    # --- P3-2 经济健康诊断 ---
    # 1) 本 tick 是否有 deposit 将在下面每次 worker 做 deposit 时更新 last_deposit_tick
    # 先累加 stall_ticks（本 tick 末尾若检测到 deposit 再清零）
    health_tracker["stall_ticks"] = health_tracker.get("stall_ticks", 0) + 1

    # 达到 stall 阈值 → 打日志，并抖动（设为 40 防刷屏）
    if health_tracker["stall_ticks"] >= 50:
        logs.append(f"economy:stall:no_deposit_for_50_ticks:pos={tuple(turn.core.position)}:workers={len(workers)}")
        health_tracker["stall_ticks"] = 40

    # Worker 预指派目标：dispatch_mine 阶段 C 的 "other" 选项预指派（wkey -> P）
    pre_assigned_targets: dict[str, Position] = {}

    # 性能优化 0：command_workers 级 estimate_path_steps 缓存（避免同 tick 同端点重复估算）
    _estimate_cache: dict[tuple[Position, Position], int] = {}

    def _cached_estimate(A: Position, B: Position, est_obs: set[Position]) -> int:
        key = (A, B)
        cached = _estimate_cache.get(key)
        if cached is not None:
            return cached
        # dispatch 相对比较：短路径 dry-run；远距直接曼哈顿，避免 12 步 guided 空转
        man = manhattan(A, B)
        if man >= 10:
            steps = man
        else:
            steps, _ = estimate_path_steps(
                A,
                B,
                est_obs,
                memory=None,  # 禁止 dispatch dry-run 写回 obstacle_cache
                max_steps=min(8, man + 2),
            )
        _estimate_cache[key] = steps
        # 反向路径近似对称（估算目的），反向也写入缓存省一半调用
        _estimate_cache[(B, A)] = steps
        return steps

    # ===== Task 6: 阶段 C — 矿点发现者调度（self vs other 决策）=====
    # 先遍历所有 Worker 作为"发现者"（wd: cargo>0 且 HARVESTER），
    # 发现附近矿点并决策 self（送完再回）或 other（指派空闲 Worker 立即去）
    for wd in workers:
        wd_uid = wd.id
        wd_wkey = _worker_key(wd_uid)
        wd_assignment = role_plan.get(wd_uid)
        wd_role = wd_assignment.role if wd_assignment else Role.HARVESTER
        wd_cargo = int(getattr(wd, "cargo", 0) or 0)
        wd_pos = _as_position(wd.position)

        if wd_cargo <= 0 or wd_role != Role.HARVESTER:
            continue

        # 收集可见资源 + 记忆资源点（视野半径=5）
        all_resource_points: set[Position] = set(resources_cells)
        if memory is not None:
            all_resource_points |= set(memory.resource_points.keys())

        nearby_mines = [
            P for P in all_resource_points
            if manhattan(wd_pos, P) <= 5
        ]
        # 复杂度限制：只取最近 3 个
        if len(nearby_mines) > 3:
            nearby_mines.sort(key=lambda P: manhattan(wd_pos, P))
            nearby_mines = nearby_mines[:3]

        for P in nearby_mines:
            if P in claimed:
                continue

            # 空闲 Worker：cargo=0 且非 wd 自身，且角色为 HARVESTER 或未指派
            idlers = [
                wx for wx in workers
                if int(getattr(wx, "cargo", 0) or 0) == 0
                and wx.id != wd.id
                and (
                    (lambda a: a is None or a.role == Role.HARVESTER)(role_plan.get(wx.id))
                )
            ]
            if len(idlers) > 4:
                idlers.sort(key=lambda wx: manhattan(_as_position(wx.position), P))
                idlers = idlers[:4]

            if not idlers:
                # 无空闲 Worker → self 预约
                _pending_return_mines[wd_wkey] = (P, tick)
                logs.append(f"worker:{wd_wkey}:dispatch:no_idlers:self")
                continue

            # ===== 性能优化 1：Manhattan 启发式预筛（决策明确时跳过 estimate_path_steps）=====
            mh_wd_to_core = manhattan(wd_pos, core_position)
            mh_core_to_P = manhattan(core_position, P)
            mh_self = mh_wd_to_core + mh_core_to_P

            idler_mh_list: list[tuple[int, int, Any]] = []
            for wx in idlers:
                wx_pos = _as_position(wx.position)
                mh_wx_P = manhattan(wx_pos, P)
                mh_other_wx = mh_wx_P + mh_core_to_P
                idler_mh_list.append((mh_other_wx, mh_wx_P, wx))
            idler_mh_list.sort(key=lambda t: (t[0], t[1]))
            mh_other_min, mh_wx_P_argmin, mh_argmin_wx = idler_mh_list[0]

            # 决策差距 >= 25% 时，用曼哈顿距离直接决定，不调用 estimate_path_steps
            # （旧 1.4 仍让大量中距 case 落入 dry-run，抬高 decide_ms）
            AMBIGUOUS_RATIO = 1.25
            if mh_self * AMBIGUOUS_RATIO < mh_other_min:
                # self 明显更优 → 直接 self 预约
                _pending_return_mines[wd_wkey] = (P, tick)
                logs.append(
                    f"worker:{wd_wkey}:dispatch:option=self"
                    f":T_self=mh{mh_self}:T_other=mh{mh_other_min}:heuristic"
                )
                continue
            if mh_other_min * AMBIGUOUS_RATIO < mh_self:
                # other 明显更优 → 直接指派曼哈顿最优 idle Worker
                akey = _worker_key(mh_argmin_wx.id)
                _claim_resource_target(P, akey, tick, claimed)
                pre_assigned_targets[akey] = P
                logs.append(
                    f"worker:{wd_wkey}:dispatch:option=other:to={akey}"
                    f":T_self=mh{mh_self}:T_other=mh{mh_other_min}:heuristic"
                )
                continue

            # ===== 决策模糊 → 调用 _cached_estimate 精确计算（含性能优化 0~4）=====
            # 仅合并「当前可见障碍」；全图 memory.obstacles 过大且对短距比较收益低
            estimate_obstacles = set(obstacles)

            # T_self: Wd → Core → P（缓存命中时零成本）
            T_self_a = _cached_estimate(wd_pos, core_position, estimate_obstacles)
            T_core_to_P = _cached_estimate(core_position, P, estimate_obstacles)
            T_P_to_core = _cached_estimate(P, core_position, estimate_obstacles)
            T_self = T_self_a + T_core_to_P

            # T_other_min：只对 manhattan 最优 1 个 idle Worker 精确 estimate
            idlers_sorted = sorted(
                idlers, key=lambda wx: (
                    manhattan(_as_position(wx.position), P),
                    _worker_key(wx.id),
                )
            )
            estimate_idlers = idlers_sorted[:1]
            T_other_list: list[tuple[int, int, Any]] = []
            for wx in estimate_idlers:
                wx_pos = _as_position(wx.position)
                Ta = _cached_estimate(wx_pos, P, estimate_obstacles)
                tiebreak = manhattan(wx_pos, P)
                T_other_list.append((Ta + T_P_to_core, tiebreak, wx))
            T_other_list.sort(key=lambda t: (t[0], t[1]))
            T_other_min, _, argmin_wx = T_other_list[0]

            if T_self < T_other_min:
                # self 更优：wd 送完货后再回 P
                _pending_return_mines[wd_wkey] = (P, tick)
                logs.append(
                    f"worker:{wd_wkey}:dispatch:option=self"
                    f":T_self={T_self}:T_other={T_other_min}"
                )
            else:
                # other 更优：立即指派空闲 Worker 去 P
                akey = _worker_key(argmin_wx.id)
                _claim_resource_target(P, akey, tick, claimed)
                pre_assigned_targets[akey] = P
                logs.append(
                    f"worker:{wd_wkey}:dispatch:option=other:to={akey}"
                    f":T_self={T_self}:T_other={T_other_min}"
                )

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
            # 满货冲 Core：即使角色是 RETREAT 也优先 return_deposit，
            # 避免贴脸敌工把 man≈2 的送货工反复拉远（线上 res 卡 8 根因）
            if cargo > 0 and pos != core_position and role == Role.RETREAT:
                man_core = manhattan(pos, core_position)
                if man_core <= 4:
                    _set_intent(wkey, target=core_position, phase="deposit", role=str(role))
                    direction, dep_tag = _return_deposit_step(
                        pos,
                        core_position,
                        _deposit_path_obstacles(pos, uid),
                        wkey,
                        uid,
                        config,
                        tick,
                        memory=memory,
                    )
                    if direction and hasattr(w, "move"):
                        w.move(_resolve_direction(w, direction))
                        logs.append(f"worker:{uid}:return_deposit:{dep_tag}")
                    continue
            if pos == core_position:
                # 有货优先 deposit（即使 RETREAT/威胁贴脸），否则资源永远攒不够出 V/R
                if cargo > 0 and hasattr(w, "deposit"):
                    _set_intent(wkey, target=core_position, phase="deposit", role=str(role))
                    w.deposit()
                    logs.append(f"worker:{uid}:deposit")
                    health_tracker["last_deposit_tick"] = tick
                    health_tracker["stall_ticks"] = 0
                    _loop_trackers.pop(wkey, None)
                    _last_move_dir.pop(wkey, None)
                    _clear_deposit_progress(wkey)
                elif role == Role.HEAL and hasattr(w, "heal"):
                    # 上核即 heal：Worker 一 tick 回满。禁止因「场上有人满货」
                    # 先 yield_core——线上任意 deposit 工人都会导致核↔邻格抖动、
                    # 永远 heal 不到（Dashboard 终点也像停在核心上一格）。
                    # 满血后下一 tick 不再是 HEAL，自然让出 Core 给 deposit。
                    _set_intent(wkey, target=core_position, phase="heal", role=str(role))
                    w.heal()
                    logs.append(f"worker:{uid}:heal_at_core")
                elif pos in set(resources_cells) and hasattr(w, "harvest"):
                    _set_intent(wkey, target=core_position, phase="harvest", role=str(role))
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest_at_core")
                elif role == Role.HEAL and cargo <= 0:
                    # 治疗角色到 Core 且无货：兜底 wait（heal 分支已优先处理）
                    _set_intent(wkey, target=core_position, phase="heal", role=str(role))
                    if hasattr(w, "wait"):
                        w.wait()
                    logs.append(f"worker:{uid}:wait_at_core")
                else:
                    # 空货 RETREAT 到 Core 后禁止永久 wait（贴脸敌人工时整局饿死）。
                    # 优先迈出远离敌人的一步；否则直接恢复 spiral 探索找矿。
                    leave_dir = None
                    best_score = -10**9
                    for name, delta in NAME_TO_DELTA.items():
                        nxt = add_pos(pos, delta)
                        if nxt in obstacles:
                            continue
                        if enemy_positions:
                            d_en = min(manhattan(nxt, ep) for ep in enemy_positions)
                        else:
                            d_en = 99
                        d_core = manhattan(nxt, core_position)
                        score = d_en * 100 + d_core
                        if score > best_score:
                            best_score = score
                            leave_dir = name
                    if leave_dir and hasattr(w, "move"):
                        _set_intent(
                            wkey,
                            target=core_position,
                            phase="retreat_scatter",
                            role=str(role),
                        )
                        w.move(_resolve_direction(w, leave_dir))
                        _last_move_dir[wkey] = leave_dir
                        logs.append(f"worker:{uid}:retreat_scatter:{leave_dir}")
                    else:
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
                                population=population,
                            )
                        )
            else:
                wkey = _worker_key(uid)
                # 空货 RETREAT：只撤到 Core 外围 hold 半径，禁止踩核堵 deposit。
                # HEAL / 满货仍可走向 Core（满货在上方已转 return_deposit）。
                hold_r = max(1, int(getattr(config, "retreat_hold_radius", 2) or 2))
                man_core = manhattan(pos, core_position)
                empty_retreat = role == Role.RETREAT and cargo <= 0
                if empty_retreat and man_core <= hold_r:
                    # 已在 hold 带：外散 / 避敌，不朝 Core 再进一步
                    leave_dir = None
                    best_score = -10**9
                    for name, delta in NAME_TO_DELTA.items():
                        nxt = add_pos(pos, delta)
                        if nxt in obstacles or nxt == core_position:
                            continue
                        if enemy_positions:
                            d_en = min(manhattan(nxt, ep) for ep in enemy_positions)
                        else:
                            d_en = 99
                        d_core = manhattan(nxt, core_position)
                        # 优先远离敌人，其次略拉开与 Core 的距离（腾核周）
                        score = d_en * 100 + d_core
                        if score > best_score:
                            best_score = score
                            leave_dir = name
                    if leave_dir and hasattr(w, "move"):
                        _set_intent(
                            wkey,
                            target=core_position,
                            phase="retreat_hold",
                            role=str(role),
                        )
                        w.move(_resolve_direction(w, leave_dir))
                        _last_move_dir[wkey] = leave_dir
                        logs.append(f"worker:{uid}:retreat_hold:{leave_dir}")
                    else:
                        # 无路可散：恢复探索，禁止 wait 堵门
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
                                population=population,
                            )
                        )
                else:
                    # 空货 RETREAT → hold 环；HEAL 始终目标 Core（须踩核才能 heal）。
                    # 禁止再把 HEAL 改到 heal_hold：终点会停在「核心上一格」，单位到不了核。
                    if empty_retreat:
                        goal = cells_toward_ring(pos, core_position, hold_r)
                        phase = "retreat_to_hold"
                    else:
                        goal = core_position
                        phase = "retreat" if role == Role.RETREAT else "heal"
                    _set_intent(wkey, target=goal, phase=phase, role=str(role))
                    path_obs = _path_obstacles_for_goal(goal)
                    # 空货撤退：Core 也当软障，避免路径穿核
                    if empty_retreat:
                        path_obs = set(path_obs)
                        path_obs.add(core_position)
                    direction, repath = _guided_move(
                        pos, goal, path_obs, wkey, config, tick=tick, memory=memory
                    )
                    if direction and hasattr(w, "move"):
                        w.move(_resolve_direction(w, direction))
                        tag = f"worker:{uid}:{phase}:{direction}"
                        if repath:
                            tag += ":repath:loop"
                        logs.append(tag)
                    elif hasattr(w, "wait"):
                        w.wait()
                        logs.append(f"worker:{uid}:wait")
            continue

        # 有货物且在 Core → deposit
        if cargo > 0 and pos == core_position:
            _set_intent(wkey, target=core_position, phase="deposit", role=str(role))
            if hasattr(w, "deposit"):
                w.deposit()
                logs.append(f"worker:{uid}:deposit")
                health_tracker["last_deposit_tick"] = tick
                health_tracker["stall_ticks"] = 0
            # 交付成功：清足迹，避免下一趟误触发 repath
            _loop_trackers.pop(wkey, None)
            _last_move_dir.pop(wkey, None)
            _clear_deposit_progress(wkey)
            continue

        # 有货物 → 回 Core 交付（优先于继续采集）
        if cargo > 0:
            wkey = _worker_key(uid)
            _set_intent(wkey, target=core_position, phase="deposit", role=str(role))
            direction, dep_tag = _return_deposit_step(
                pos,
                core_position,
                _deposit_path_obstacles(pos, uid),
                wkey,
                uid,
                config,
                tick,
                memory=memory,
            )
            if direction and hasattr(w, "move"):
                w.move(_resolve_direction(w, direction))
                logs.append(f"worker:{uid}:return_deposit:{dep_tag}")
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

        # 站在资源格 → harvest（记忆标记已消耗；占用该矿防他人同 tick 再抢）
        if pos in set(resources_cells):
            _claim_resource_target(pos, wkey, tick, claimed)
            if hasattr(w, "harvest"):
                w.harvest()
                logs.append(f"worker:{uid}:harvest")
                if memory is not None:
                    memory.mark_harvested(pos, tick)
            # 若此处恰好为 pending_return_mine 的目标，一并清理预约
            if wkey in _pending_return_mines:
                P_claimed, _ = _pending_return_mines[wkey]
                if P_claimed == pos:
                    del _pending_return_mines[wkey]
            continue

        # ===== Task 6: 阶段 B — 预约优先（_pending_return_mines 优先执行）=====
        if wkey in _pending_return_mines and cargo == 0:
            P, _ = _pending_return_mines[wkey]
            _claim_resource_target(P, wkey, tick, claimed)
            _set_intent(wkey, target=P, phase="to_resource", role=str(role))
            logs.append(f"worker:{wkey}:to_pending_return_mine:pos={P}")
            if pos == P:
                if hasattr(w, "harvest"):
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest")
                    if memory is not None:
                        memory.mark_harvested(pos, tick)
                del _pending_return_mines[wkey]
            else:
                direction, repath = _guided_move(
                    pos, P, _path_obstacles_for_goal(P), wkey, config, tick=tick, memory=memory
                )
                if direction and hasattr(w, "move"):
                    w.move(_resolve_direction(w, direction))
                    tag = f"worker:{uid}:to_resource:{direction}"
                    if repath:
                        tag += ":repath:loop"
                    logs.append(tag)
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait")
            continue

        # 预指派目标（阶段 C other 选项的立即执行）优先级高于普通 candidates
        if wkey in pre_assigned_targets:
            P = pre_assigned_targets[wkey]
            _claim_resource_target(P, wkey, tick, claimed)
            _set_intent(wkey, target=P, phase="to_resource", role=str(role))
            if pos == P:
                if pos in set(resources_cells) and hasattr(w, "harvest"):
                    w.harvest()
                    logs.append(f"worker:{uid}:harvest")
                    if memory is not None:
                        memory.mark_harvested(pos, tick)
                else:
                    if hasattr(w, "wait"):
                        w.wait()
                    logs.append(f"worker:{uid}:wait:bad_target")
            else:
                direction, repath = _guided_move(
                    pos, P, _path_obstacles_for_goal(P), wkey, config, tick=tick, memory=memory
                )
                if direction and hasattr(w, "move"):
                    w.move(_resolve_direction(w, direction))
                    tag = f"worker:{uid}:to_resource:{direction}"
                    if repath:
                        tag += ":repath:loop"
                    logs.append(tag)
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait")
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

        # 走向最近未声称候选资源（无空闲矿时绝不回退到已占用矿 → 其余人继续探索）
        # 近距发现者可比远方 claim owner 更优先（_CLAIM_STEAL_DIST）
        target = _pick_resource_target(
            pos, wkey, tick, candidates, claimed, worker_positions=worker_positions
        )
        if target is not None:
            _claim_resource_target(target, wkey, tick, claimed)
            _set_intent(wkey, target=target, phase="to_resource", role=str(role))
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
                direction, repath = _guided_move(
                    pos, target, _path_obstacles_for_goal(target), wkey, config, tick=tick, memory=memory
                )
                if direction and hasattr(w, "move"):
                    w.move(_resolve_direction(w, direction))
                    tag = f"worker:{uid}:to_resource:{direction}"
                    if repath:
                        tag += ":repath:loop"
                    logs.append(tag)
                elif hasattr(w, "wait"):
                    w.wait()
                    logs.append(f"worker:{uid}:wait")
            continue

        if memory is not None and cargo == 0:
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
                _set_intent(wkey, target=cargo_target, phase="to_cargo", role=str(role))
                if pos == cargo_target:
                    if hasattr(w, "harvest"):
                        w.harvest()
                        memory.mark_cargo_collected(cargo_target)
                        logs.append(f"worker:{uid}:reclaim_cargo")
                else:
                    direction, repath = _guided_move(
                        pos, cargo_target, obstacles, wkey, config, tick=tick, memory=memory
                    )
                    if direction and hasattr(w, "move"):
                        w.move(_resolve_direction(w, direction))
                        tag = f"worker:{uid}:to_cargo:{direction}"
                        if repath:
                            tag += ":repath:loop"
                        logs.append(tag)
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
                    population=population,
                )
            )
            _st = _spiral_state.get(wkey)
            if _st is not None:
                _set_intent(
                    wkey,
                    target=_st.target,
                    phase=str(_st.phase or "explore"),
                    role=str(role),
                    ring=_st.ring,
                    sector=_st.sector_id,
                    dedicated=_st.dedicated,
                )
        else:
            # 无可见/可用资源：螺旋扫掠 + 目标点导航 + 软回撤
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
                    population=population,
                )
            )
            _st = _spiral_state.get(wkey)
            if _st is not None:
                _set_intent(
                    wkey,
                    target=_st.target,
                    phase=str(_st.phase or "explore"),
                    role=str(role),
                    ring=_st.ring,
                    sector=_st.sector_id,
                    dedicated=_st.dedicated,
                )

    # ===== Task 6: 阶段 D — 256 tick GC：清理 _pending_return_mines 中已死亡 Worker =====
    if tick % 256 == 0:
        alive = {_worker_key(w.id) for w in workers}
        # _pending_return_mines（已在 Task 6 加过，确保仍在）
        for d_name, d in [
            ("_spiral_state", _spiral_state),
            ("_last_move_dir", _last_move_dir if '_last_move_dir' in globals() else {}),
            ("_loop_trackers", _loop_trackers if '_loop_trackers' in globals() else {}),
            ("_pending_return_mines", _pending_return_mines),
            ("_worker_intents", _worker_intents),
            ("_deposit_progress", _deposit_progress),
        ]:
            for k in list(d.keys()):
                if k not in alive:
                    d.pop(k, None)
        # 清理已死 Worker 的跨 tick 资源 claim
        for cpos, (ctick, owner) in list(_claimed_targets.items()):
            if owner not in alive:
                _claimed_targets.pop(cpos, None)

    return logs


def _local_explore_ratio(
    memory: Optional[MemoryMap],
    core_position: Position,
    radius: int,
) -> float:
    """Core 周围曼哈顿半径内的本地探索度 ∈ [0, 1]。

    分母：半径内全部格。分子：explored_cells 中的格 + 已知障碍格（障碍视为已完成）。
    `memory is None` → 0.0。
    """
    if memory is None:
        return 0.0
    r = max(0, int(radius))
    cx, cy = _as_position(core_position)
    obstacles = getattr(memory, "obstacles", set()) or set()
    explored_cells = getattr(memory, "explored_cells", {}) or {}
    total = 0
    done = 0
    for dx in range(-r, r + 1):
        max_dy = r - abs(dx)
        for dy in range(-max_dy, max_dy + 1):
            total += 1
            p = (cx + dx, cy + dy)
            if p in obstacles or p in explored_cells:
                done += 1
                continue
            if hasattr(memory, "is_explored") and memory.is_explored(p):
                done += 1
    if total <= 0:
        return 0.0
    return done / float(total)


def _beacon_push_ready(
    config: TacticConfig,
    core_position: Position,
    memory: Optional[MemoryMap],
    population: int,
) -> bool:
    """本地探索度 ≥ 阈值 **并且** 人口 ≥ 阈值 → 应向信标推进。"""
    pop_th = int(getattr(config, "beacon_push_population", 10) or 10)
    ratio_th = float(getattr(config, "beacon_push_explore_ratio", 0.8) or 0.8)
    radius = max(1, int(getattr(config, "spiral_max_ring", 24) or 24))
    pop_ok = int(population) >= pop_th
    explore_ok = _local_explore_ratio(memory, core_position, radius) >= ratio_th
    return pop_ok and explore_ok


def _beacon_chase_allowed(
    config: TacticConfig,
    core_position: Position,
    worker_count: int,
    *,
    memory: Optional[MemoryMap] = None,
    population: int = 0,
) -> bool:
    """是否允许追 Beacon / 向信标推进。

    - Beacon 缺失 → False
    - Core→Beacon 曼哈顿 > beacon_max_chase → False（默认极大≈不限距）
    - 允许条件（满足其一即可）：
      1) 探索度 ≥ 阈值 **且** 总人口 ≥ 阈值（_beacon_push_ready）
      2) worker_count ≥ beacon_min_workers（早期 dedicated 兼容）
    """
    beacon = config.beacon_position
    if beacon is None:
        return False
    max_chase = max(1, int(getattr(config, "beacon_max_chase", 10000) or 10000))
    if manhattan(core_position, beacon) > max_chase:
        return False
    if _beacon_push_ready(config, core_position, memory, population):
        return True
    min_w = max(1, int(getattr(config, "beacon_min_workers", 3) or 3))
    return worker_count >= min_w


def _drop_to_local(st: SpiralState, reason_log: Optional[list[str]] = None,
                   uid: Any = None, tag: str = "") -> None:
    """将 SpiralState 强制回 local（清 target/stall；可选写日志）。"""
    st.phase = "local"
    st.dedicated = False
    st.target = None
    st.stalled_ticks = 0
    if reason_log is not None and uid is not None and tag:
        reason_log.append(f"worker:{uid}:{tag}")


def _is_chunk_skippable(
    memory: Optional[MemoryMap],
    center: Position,
    cand: Position,
    beacon_side: bool = False,
    tick: Optional[int] = None,
) -> bool:
    """目标点是否应被跳过：已探 chunk 且**非 Core 所在 chunk**（beacon_side=False）。

    beacon_side=True（Beacon 侧新行为）：跳过 explored_chunk **Core chunk 也跳过**
    （Beacon 侧不关心 Core chunk 例外）。
    陈旧 chunk 不跳过（允许回访）：tick - last_seen > interval*3 时视为陈旧。
    `memory is None → False`（不跳过）。
    """
    if memory is None:
        return False
    CHUNK = getattr(memory, "CHUNK_SIZE", 16)
    chunk = (cand[0] // CHUNK, cand[1] // CHUNK)
    explored = getattr(memory, "explored_chunks", set())
    if chunk not in explored:
        return False
    if tick is not None and hasattr(memory, "is_chunk_stale"):
        if memory.is_chunk_stale(chunk, tick):
            return False
    if not beacon_side:
        core_chunk = (center[0] // CHUNK, center[1] // CHUNK)
        if chunk == core_chunk:
            return False
    return True


def dual_spiral_target(
    core: tuple[int, int],
    beacon: Optional[tuple[int, int]],
    d_core_now: int,
    sector_id: int,
    sector_count: int,
    ring: int,
    index: int,
    memory: Any,
    config: Any,
    total_workers: int = 0,
    tick: Optional[int] = None,
) -> tuple[int, int]:
    spiral_max_ring = getattr(config, "spiral_max_ring", 24)
    beacon_max_chase = getattr(config, "beacon_max_chase", 10000)
    beacon_min_workers = getattr(config, "beacon_min_workers", 3)

    if d_core_now <= spiral_max_ring:
        return _next_spiral_target_simple(
            core, sector_id, sector_count, ring, index, memory,
            beacon_side=False, tick=tick,
        )

    if (
        beacon is not None
        and manhattan(core, beacon) <= beacon_max_chase
        and total_workers >= beacon_min_workers
    ):
        for _attempt in range(20):
            cand = beacon_oriented_spiral_target(
                core, beacon, sector_id, sector_count, ring, index + _attempt
            )
            if _is_chunk_skippable(
                memory, beacon, cand, beacon_side=True, tick=tick,
            ):
                index += 1
                continue
            return cand
        return beacon_oriented_spiral_target(
            core, beacon, sector_id, sector_count, ring, index
        )

    # 外环且无 Beacon chase：保持当前 ring 继续扫，禁止硬编码回 ring=3
    # （线上多 Worker 在 ring=3 局部循环的根因之一）
    keep_ring = max(3, min(int(ring), int(spiral_max_ring)))
    return _next_spiral_target_simple(
        core, sector_id, sector_count, keep_ring, index, memory,
        beacon_side=False, tick=tick,
    )


def _next_spiral_target_simple(
    core_position: Position,
    sector_id: int,
    sector_count: int,
    ring: int,
    index: int,
    memory: Optional[MemoryMap],
    beacon_side: bool = False,
    obstacles: Optional[set[Position]] = None,
    tick: Optional[int] = None,
) -> Position:
    max_iter = 64
    blocked = obstacles if obstacles is not None else set()
    cur_index = index
    cur_ring = ring
    spiral_max_ring = 24
    for _ in range(max_iter):
        pts = sector_points(core_position, cur_ring, sector_id, sector_count)
        if not pts:
            return core_position
        cand = pts[cur_index % len(pts)]
        if (
            not _is_chunk_skippable(
                memory, core_position, cand, beacon_side=beacon_side, tick=tick,
            )
            and cand not in blocked
        ):
            return cand
        cur_index += 1
        if cur_index >= len(pts):
            cur_index = 0
            cur_ring += 1
            if cur_ring > spiral_max_ring:
                cur_ring = spiral_max_ring
    pts = sector_points(core_position, cur_ring, sector_id, sector_count)
    if pts:
        return pts[cur_index % len(pts)]
    return core_position


def _next_spiral_target(
    core_position: Position,
    st: SpiralState,
    sector_count: int,
    config: TacticConfig,
    memory: Optional[MemoryMap],
    obstacles: Optional[set[Position]] = None,
    tick: Optional[int] = None,
) -> Position:
    """生成螺旋扫掠下一目标点（P0-2），替换现有 3 处 `spiral_target` 直调。

    规则：候选点所在 chunk 已探且非 Core chunk → `st.index += 1` 跳过
    （环扫完则 ring+1 / 回绕，沿用现有推进语义）；未探或 Core chunk 直接返回。
    `obstacles` 非空时，候选点本身是障碍 → 同样跳过，避免 Worker 反复朝
    不可达格振荡（bugfix：目标点绝不落在障碍上）。
    陈旧 chunk 不跳过（允许回访）。
    """
    max_iter = 64  # 防死循环（Core chunk 永不跳过，理论上总能返回）
    blocked = obstacles if obstacles is not None else set()
    for _ in range(max_iter):
        cand = spiral_target(
            core_position, st.sector_id, sector_count, st.ring, st.index
        )
        if (
            not _is_chunk_skippable(
                memory, core_position, cand, tick=tick,
            )
            and cand not in blocked
        ):
            return cand
        # 跳过：推进 index；本环扫完 → ring+1 / 超上限切 beacon phase
        st.index += 1
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if st.index >= len(pts):
            st.index = 0
            st.ring += 1
            if st.ring > config.spiral_max_ring:
                st.phase = "beacon"
                base_ring = getattr(config, 'spiral_base_ring', 3)
                st.ring = max(base_ring, st.ring - config.spiral_max_ring + base_ring)
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
    # _guided_move 会写回 _last_move_dir，先捕获 prev 用于反向对抖检测
    prev_dir = _last_move_dir.get(wkey)
    direction, did_repath = _guided_move(
        pos, target, obstacles, wkey, config, tick=tick, memory=memory
    )

    # 进度追踪：方向为空（卡住）或未缩短与目标距离 → stall+1 + 障碍记录。
    # 紧接反向对抖（A↔B 贴墙振荡）即使缩短距离也不算进展，保证 stall
    # 能累积 → 换横向 offset 绕障。repath 视为有进展。
    progressed = False
    if did_repath:
        progressed = True
    elif direction is not None:
        nxt = add_pos(pos, NAME_TO_DELTA[direction])
        if manhattan(nxt, target) < dist_to_target:
            progressed = True
    if (
        not did_repath
        and direction is not None
        and prev_dir is not None
        and direction == _opposite_dir(prev_dir)
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
        direction, did_repath = _guided_move(
            pos, target, obstacles, wkey, config, tick=tick, memory=memory
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
        if direction:
            _last_move_dir[wkey] = direction

    suffix = ":avoid" if avoided else ""
    rl = ":recall_soft:beacon" if soft_recall else ""
    ded = ":dedicated_beacon" if st.dedicated else ""
    rp = ":repath" if did_repath else ""
    if direction and hasattr(w, "move"):
        w.move(_resolve_direction(w, direction))
        logs.append(
            f"worker:{uid}:explore:{direction}:phase=beacon:ring={st.ring}"
            f":sec={st.sector_id}:stall={st.stalled_ticks}:d={dist_core}"
            f":d_beacon={d_beacon}{rl}{ded}{rp}{suffix}"
        )
    elif hasattr(w, "wait"):
        w.wait()
        logs.append(
            f"worker:{uid}:explore:None:phase=beacon:ring={st.ring}"
            f":sec={st.sector_id}:stall={st.stalled_ticks}:d={dist_core}"
            f":d_beacon={d_beacon}{rl}{ded}{rp}"
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
    population: int = 0,
) -> list[str]:
    """无可见资源时的螺旋扫掠一步（两阶段状态机 + 目标点导航 + 软回撤）。

    设计要点（决策 1）：
    - **local**：删除 recall_dist 硬边界与「绝不朝 Core 收缩」守卫——目标点
      导航沿环切向移动允许曼哈顿距离暂时持平/微降；到达目标 → index+1；
      本环扫完 → ring+1；ring 超 spiral_max_ring → 回 base ring。
      连续 recall_stall_ticks 无进展 → 软回撤：**仅 dedicated 且 Beacon 存在
      时切 beacon**（日志 `:recall_soft:beacon`）；非 dedicated / 无 Beacon
      则**向外扩一层（ring+1）并把 index 跳到环对面（+len(pts)//2）**——
      探索只向外扩散，绝不因 stall 收缩回 Core；非 dedicated 不追远点 Beacon
      （bugfix：避免全员 soft-recall 追 beacon 饿死经济）。
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
    n_workers = len(workers)
    pop_now = int(population) if population else n_workers
    chase_ok = _beacon_chase_allowed(
        config,
        core_position,
        n_workers,
        memory=memory,
        population=pop_now,
    )
    push_ready = bool(
        chase_ok
        and config.beacon_position is not None
        and _beacon_push_ready(config, core_position, memory, pop_now)
    )

    # 到达标记：记录已探 chunk（新 chunk → new_chunk 日志）
    if memory is not None and memory.mark_explored(pos, tick):
        cx, cy = memory.chunk_of(pos)
        logs.append(f"worker:{uid}:new_chunk=({cx},{cy})")

    # 环同步：若 Worker 已明显超出当前 spiral ring，把 ring 抬到当前位置，
    # 避免 target 永远落在 ring=3（把外圈工人拽回 Core 周边空转）。
    if st.phase == "local" and dist_core > st.ring + 2:
        new_ring = min(dist_core, config.spiral_max_ring)
        if new_ring > st.ring:
            st.ring = max(st.ring + 1, new_ring)  # 至少 +1，可跳到当前位置
            if st.ring > config.spiral_max_ring:
                st.ring = config.spiral_max_ring
            st.index = 0
            st.target = None
            st.stalled_ticks = 0

    # ---- Beacon 策略（混合高效 + 探索度且人口推进）----
    # 1) 远距 / 未满足 chase：禁止 dedicated，已 dedicated 也降级 local
    # 2) chase_ok + widx==0：专职 dedicated beacon
    # 3) 探索度≥阈值 **且** 人口≥阈值：空闲探索 Worker 集体 phase=beacon
    # 4) 非 dedicated 残留 beacon：仅 chase 不允许时强制回 local；push_ready 保留
    if st.dedicated and not chase_ok:
        tag = "beacon_abort:far" if config.beacon_position is not None else "beacon_abort:gone"
        min_w = max(1, int(getattr(config, "beacon_min_workers", 3) or 3))
        if (not _beacon_push_ready(config, core_position, memory, pop_now)
                and n_workers < min_w):
            tag = "beacon_abort:min_workers"
        _drop_to_local(st, logs, uid, tag)
    elif chase_ok and widx == 0 and not st.dedicated:
        st.dedicated = True
        st.phase = "beacon"
        logs.append(f"worker:{uid}:dedicated_beacon")
    elif st.dedicated and chase_ok and config.beacon_position is not None:
        st.phase = "beacon"
    elif push_ready and not st.dedicated and st.phase != "beacon":
        # 中后期：探索度 **且** 人口达标 → 非 dedicated 也向信标推进
        st.phase = "beacon"
        st.target = None
        st.stalled_ticks = 0
        _r = max(1, int(getattr(config, "spiral_max_ring", 24) or 24))
        _ratio = _local_explore_ratio(memory, core_position, _r)
        logs.append(
            f"worker:{uid}:beacon_push:pop={pop_now}:explore={_ratio:.2f}"
        )
    # 非 dedicated 残留 beacon：chase 不允许时回 local；push_ready 时保持推进
    if not st.dedicated and st.phase == "beacon":
        if not chase_ok:
            _drop_to_local(st, logs, uid, "beacon_force_local:chase_not_ok")

    # beacon 阶段且 Beacon 消失 / 被拾取 → 回 local（P2-1）
    if st.phase == "beacon" and config.beacon_position is None:
        _drop_to_local(st, logs, uid, "beacon_abort:gone")

    # 绝对安全网（**仅 local 阶段生效**）：距 Core 过远时**不要**强制朝 Core 走
    # （线上 d≈33 被 recall_soft 拉回 → 永远找不到矿）。改为把 spiral ring
    # 抬到当前位置附近，继续外扩扫掠。
    if st.phase == "local" and dist_core > config.spiral_max_ring + 8:
        # 抬 ring 到当前距离附近（不超过 max+4 的软上限），清 target 强制重算
        raise_to = min(dist_core, config.spiral_max_ring + 4)
        if st.ring < raise_to:
            st.ring = raise_to
            st.index = 0
            st.target = None
            st.stalled_ticks = 0
            logs.append(
                f"worker:{uid}:explore_ring_raise:ring={st.ring}:d={dist_core}"
            )
        # 不 return：落入下方 local 导航

    # ---- beacon 阶段 ----
    if st.phase == "beacon":
        beacon = config.beacon_position
        step_radius = max(1, int(getattr(config, "beacon_step_radius", 8) or 8))
        # 非 dedicated 到达 Beacon 近旁：默认回 local；push_ready 时继续推进
        if (
            not st.dedicated
            and not push_ready
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
        st.target = dual_spiral_target(
            core_position, config.beacon_position, dist_core, st.sector_id,
            sector_count, st.ring, st.index, memory, config,
            total_workers=len(workers), tick=tick,
        )
    dist_to_target = manhattan(pos, st.target)

    in_outer_ring = dist_core > config.spiral_max_ring

    # 到达目标 → 推进 index / ring
    if pos == st.target:
        st.index += 1
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if st.index >= len(pts) or st.ring_done:
            st.index = 0
            st.ring_done = False
            st.ring += 1
            if st.ring > config.spiral_max_ring:
                st.phase = "beacon"
                base_ring = getattr(config, 'spiral_base_ring', 3)
                st.ring = max(base_ring, st.ring - config.spiral_max_ring + base_ring)
        st.stalled_ticks = 0
        st.target = dual_spiral_target(
            core_position, config.beacon_position, dist_core, st.sector_id,
            sector_count, st.ring, st.index, memory, config,
            total_workers=len(workers), tick=tick,
        )
        dist_to_target = manhattan(pos, st.target)

    # 必须在 _guided_move 前捕获 prev_dir：_guided_move 会立刻写回 _last_move_dir
    prev_dir = _last_move_dir.get(wkey)

    # 带 LoopTracker 的引导移动，打破局部绕圈（修复工人同区域循环）
    direction, did_repath = _guided_move(
        pos, st.target, obstacles, wkey, config, tick=tick, memory=memory
    )

    # 进度追踪：方向为空（卡住）、目标本身是障碍（不可达）、紧接反向对抖
    # （A↔B 贴墙振荡）或未缩短与目标距离 → stall+1。
    # 反向对抖即使 manhattan 缩短也不算进展（如贴墙横跳），保证 stall
    # 能累积 → 软回撤外扩（bugfix：不再因振荡永远卡在同一环）。
    # repath 视为有进展（已强制换路），清 stall。
    if st.target in obstacles:
        st.stalled_ticks += 1
    elif direction is None:
        st.stalled_ticks += 1
    elif prev_dir is not None and direction == _opposite_dir(prev_dir):
        # 对抖不算进展（即使 repath）
        st.stalled_ticks += 1
    elif direction:
        nxt = add_pos(pos, NAME_TO_DELTA[direction])
        if manhattan(nxt, st.target) < dist_to_target:
            # 真正缩短目标距离才清 stall；裸 repath 不清（否则 ring 永远卡在 3）
            st.stalled_ticks = 0
        else:
            st.stalled_ticks += 1
    else:
        st.stalled_ticks += 1

    # 软回撤：连续 recall_stall_ticks 无进展
    soft_recall = False
    if st.stalled_ticks >= config.recall_stall_ticks:
        st.stalled_ticks = 0
        soft_recall = True
        # 仅「允许 chase 的 dedicated」可 soft-recall 切 beacon；否则 ring+1 外扩
        if st.dedicated and _beacon_chase_allowed(
            config, core_position, n_workers, memory=memory, population=pop_now
        ):
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
        # stall 切换逻辑：
        # - 内环 stall（6 tick 无进展）→ st.ring += 1；若 st.ring > spiral_max_ring → 切 beacon phase
        # - 外环 stall 超阈值 → 切 beacon phase（不再回内环）
        if in_outer_ring:
            st.phase = "beacon"
            base_ring = getattr(config, 'spiral_base_ring', 3)
            st.ring = max(base_ring, st.ring - config.spiral_max_ring + base_ring)
        else:
            st.ring += 1
            if st.ring > config.spiral_max_ring:
                st.phase = "beacon"
                base_ring = getattr(config, 'spiral_base_ring', 3)
                st.ring = max(base_ring, st.ring - config.spiral_max_ring + base_ring)
        pts = sector_points(core_position, st.ring, st.sector_id, sector_count)
        if pts:
            st.index = (st.index + len(pts) // 2) % len(pts)
        else:
            st.index = 0
        st.target = dual_spiral_target(
            core_position, config.beacon_position, dist_core, st.sector_id,
            sector_count, st.ring, st.index, memory, config,
            total_workers=len(workers), tick=tick,
        )
        direction, did_repath = _guided_move(
            pos, st.target, obstacles, wkey, config, tick=tick, memory=memory
        )
        if direction is None:
            direction = outward_step(
                pos,
                core_position,
                obstacles=obstacles,
                last_dir=_last_move_dir.get(wkey),
            )
            if direction:
                _last_move_dir[wkey] = direction

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
        if direction:
            _last_move_dir[wkey] = direction

    if direction and hasattr(w, "move"):
        w.move(_resolve_direction(w, direction))
        suffix = ":avoid" if avoided else ""
        rl = ":recall_soft" if soft_recall else ""
        rp = ":repath" if did_repath else ""
        logs.append(
            f"worker:{uid}:explore:{direction}:ring={st.ring}:sec={st.sector_id}"
            f":stall={st.stalled_ticks}:d={dist_core}{rl}{rp}{suffix}"
        )
    elif hasattr(w, "wait"):
        w.wait()
        rl = ":recall_soft" if soft_recall else ""
        rp = ":repath" if did_repath else ""
        logs.append(
            f"worker:{uid}:explore:None:ring={st.ring}:sec={st.sector_id}"
            f":stall={st.stalled_ticks}:d={dist_core}{rl}{rp}"
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
