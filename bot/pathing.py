"""路径与几何工具：曼哈顿距离、朝目标一步、防守环位。

不依赖 arena-hero SDK，便于离线单测。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable, Optional, Sequence

# 位置类型：(x, y)
Position = tuple[int, int]

# chunk 尺寸：地图记忆按 16×16 chunk 划分（与刷新配额/回访调度相关）
CHUNK_SIZE: int = 16

# 四向位移（与 arena_hero.Direction 语义对齐）
DIR_UP: Position = (0, -1)
DIR_DOWN: Position = (0, 1)
DIR_LEFT: Position = (-1, 0)
DIR_RIGHT: Position = (1, 0)

CARDINAL_DELTAS: tuple[Position, ...] = (DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT)

# 方向名（字符串，供 stub / 真实 SDK 共用）
DIR_NAMES: dict[Position, str] = {
    DIR_UP: "UP",
    DIR_DOWN: "DOWN",
    DIR_LEFT: "LEFT",
    DIR_RIGHT: "RIGHT",
}

NAME_TO_DELTA: dict[str, Position] = {
    "UP": DIR_UP,
    "DOWN": DIR_DOWN,
    "LEFT": DIR_LEFT,
    "RIGHT": DIR_RIGHT,
}

# 方向名 → 反方向名（去抖用）
_OPPOSITE_NAME: dict[str, str] = {
    "UP": "DOWN",
    "DOWN": "UP",
    "LEFT": "RIGHT",
    "RIGHT": "LEFT",
}


def opposite_name(name: Optional[str]) -> Optional[str]:
    """返回方向名的反方向；非法/None 原样返回。"""
    if not name:
        return None
    return _OPPOSITE_NAME.get(name, name)


def manhattan(a: Position, b: Position) -> int:
    """曼哈顿距离 |dx| + |dy|。"""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def chunk_of(pos: Position) -> tuple[int, int]:
    """返回位置所属 chunk（16×16 格，向下取整）。"""
    return (int(pos[0]) // CHUNK_SIZE, int(pos[1]) // CHUNK_SIZE)


def chunk_ring(chunk: tuple[int, int], center_chunk: tuple[int, int]) -> int:
    """返回 chunk 相对 Core chunk 的曼哈顿环序号。"""
    return manhattan((int(chunk[0]), int(chunk[1])), (int(center_chunk[0]), int(center_chunk[1])))


def add_pos(a: Position, delta: Position) -> Position:
    """位置加法。"""
    return (a[0] + delta[0], a[1] + delta[1])


def clamp_step_toward(
    origin: Position,
    target: Position,
    obstacles: Optional[Iterable[Position]] = None,
) -> Optional[str]:
    """从 origin 朝 target 走一格，返回方向名（UP/DOWN/LEFT/RIGHT）。

    优先减少较大轴向差距；若首选格是障碍则尝试另一轴；
    若仍被挡则尝试其余方向中能靠近目标的。
    已在目标格返回 None。
    """
    if origin == target:
        return None

    blocked: set[Position] = set(obstacles) if obstacles is not None else set()
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]

    # 按「能减少的曼哈顿量」排序候选
    candidates: list[tuple[int, Position, str]] = []
    if dx != 0:
        step = DIR_RIGHT if dx > 0 else DIR_LEFT
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, DIR_NAMES[step]))
    if dy != 0:
        step = DIR_DOWN if dy > 0 else DIR_UP
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, DIR_NAMES[step]))

    # 附加其余方向（绕障）
    used = {c[1] for c in candidates}
    for step, name in DIR_NAMES.items():
        if step in used:
            continue
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, name))

    candidates.sort(key=lambda item: (-item[0], item[2]))

    for gain, step, name in candidates:
        nxt = add_pos(origin, step)
        if nxt in blocked:
            continue
        # 至少不远离，或只剩绕障
        if gain > 0 or (gain == 0 and origin != target):
            if gain >= 0 or all(
                add_pos(origin, s) in blocked for s in CARDINAL_DELTAS
            ):
                return name
            if gain > 0:
                return name
        if gain > 0:
            return name

    # 最后：任意非障碍方向
    for step, name in DIR_NAMES.items():
        if add_pos(origin, step) not in blocked:
            return name
    return None


def _clamp_score(
    origin: Position,
    step: Position,
    name: str,
    gain: int,
    dx: int,
    dy: int,
    last_dir: Optional[str],
    obstacle_cache: Optional[dict] = None,
) -> int:
    """clamp_step_toward_memo 的分值：分层避免「反向对抖 / 无限远离」。

    分层规则（按优先级）：
    1. gain > 0（靠近 target）且非 last_dir 反方向 → 100（最优推进）
    2. gain > 0 但恰为反方向 → 80（死胡同兜底，仍优于绕行远离）
    3. gain < 0（必须绕行）→ 优先跨轴绕行（-30），
       同轴远离（反目标轴向）最后（-70）；同层内 keep 微加分、反向微罚。

    历史障碍降权（Task 2）：
    - 若 obstacle_cache 中存在 nxt 且 block_count >= 3 → score -= 100
    - 若 obstacle_cache 中存在 nxt 且 1 <= block_count < 3 → score -= 30
    """
    opp = opposite_name(last_dir)
    if gain >= 0:
        score = 100 + gain
        if name == last_dir:
            score += 10  # 沿原方向继续推进
        if name == opp:
            score -= 20  # 反向推进降权（仅当它是唯一推进方向时胜出）
    else:
        # gain < 0：只能绕行/远离
        cross_axis = (dy != 0 and name in ("LEFT", "RIGHT")) or (
            dx != 0 and name in ("UP", "DOWN")
        )
        if cross_axis:
            score = -30
            if name == last_dir:
                score += 3
            if name == opp:
                score -= 20
        else:
            score = -70  # 反目标轴向（远离 target），最后兜底
            if name == opp:
                score -= 20

    # 历史障碍降权
    nxt = add_pos(origin, step)
    if obstacle_cache is not None and nxt in obstacle_cache:
        bc_val = obstacle_cache[nxt]
        if hasattr(bc_val, "block_count"):
            bc = bc_val.block_count
        else:
            bc = int(bc_val)
        if bc >= 3:
            score -= 100
        elif 1 <= bc < 3:
            score -= 30

    return score


def clamp_step_toward_memo(
    origin: Position,
    target: Position,
    obstacles: Optional[Iterable[Position]] = None,
    last_dir: Optional[str] = None,
    memo: Optional[dict] = None,
    ban_dirs: Optional[Iterable[str]] = None,
    memory: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str]]:
    """朝 target 走一格，返回 (方向, 更新后的 last_dir)。

    逻辑与 clamp_step_toward 相同（优先减少较大轴向差距、被挡则绕行），
    但加入「方向记忆去抖」：
    - 若首选方向 == last_dir 的反方向（紧接反向对抖），强降权；
    - 优先垂直（跨轴）方向 / keep 方向贴墙绕行；
    - 实在只能远离时选跨轴绕行，绝不先选反目标轴向。
    - ban_dirs：强制重寻路时临时禁止的方向（本步降权到几乎不可选）。
    - memory：传入 MemoryMap 可启用历史障碍降权（obstacle_cache.block_count 参与评分）。

    调用方按 worker id 记录 last_dir 并回传；也可传入 memo dict，
    本函数自动读写 memo["last_dir"]（跨 tick 记忆）。
    已在目标格返回 (None, None)。
    """
    if memo is not None and memo.get("last_dir"):
        last_dir = memo["last_dir"]

    if origin == target:
        if memo is not None:
            memo["last_dir"] = None
        return None, None

    blocked: set[Position] = set(obstacles) if obstacles is not None else set()
    banned: set[str] = set(ban_dirs) if ban_dirs is not None else set()
    obstacle_cache = memory.obstacle_cache if memory is not None else None
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]

    # 主轴向候选（靠近 target 的轴）
    candidates: list[tuple[int, Position, str]] = []
    if dx != 0:
        step = DIR_RIGHT if dx > 0 else DIR_LEFT
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, DIR_NAMES[step]))
    if dy != 0:
        step = DIR_DOWN if dy > 0 else DIR_UP
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, DIR_NAMES[step]))

    # 其余方向（绕障 / 远离）
    used = {c[1] for c in candidates}
    for step, name in DIR_NAMES.items():
        if step in used:
            continue
        nxt = add_pos(origin, step)
        gain = manhattan(origin, target) - manhattan(nxt, target)
        candidates.append((gain, step, name))

    best_name: Optional[str] = None
    best_score = -10_000
    for gain, step, name in candidates:
        nxt = add_pos(origin, step)
        if nxt in blocked:
            continue
        score = _clamp_score(origin, step, name, gain, dx, dy, last_dir, obstacle_cache=obstacle_cache)
        if name in banned:
            score -= 500  # 重寻路：禁止继续走旧循环方向
        if score > best_score:
            best_score = score
            best_name = name

    if best_name is None:
        # 理论不可达（四向皆挡）；兜底任意非障碍方向
        for step, name in DIR_NAMES.items():
            if add_pos(origin, step) not in blocked:
                best_name = name
                break

    if memo is not None:
        memo["last_dir"] = best_name
    return best_name, best_name


# ---------------------------------------------------------------------------
# 范围循环检测 + 强制重寻路
# ---------------------------------------------------------------------------


@dataclass
class LoopTracker:
    """单位空间足迹：检测「小范围重复行走」并驱动强制换路。

    线上症状：return_deposit 长时间 LEFT/RIGHT/DOWN 空转，last_dir 防抖
    只能挡 A↔B 对抖，挡不住「在 2×2 格内绕圈」或「服务端拒步同格不动」。
    """

    history: Deque[Position] = field(default_factory=lambda: deque(maxlen=16))
    static_ticks: int = 0
    last_pos: Optional[Position] = None
    cooldown: int = 0
    repath_side: int = 0  # 0/1 交替左右绕行
    last_repath_tick: int = -1

    def reset(self) -> None:
        self.history.clear()
        self.static_ticks = 0
        self.last_pos = None
        self.cooldown = 0


def bbox_diameter(positions: Sequence[Position]) -> int:
    """足迹曼哈顿包围盒直径：max(x)-min(x) + max(y)-min(y)。空序列返回 0。"""
    if not positions:
        return 0
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def detect_spatial_loop(
    tracker: LoopTracker,
    pos: Position,
    target: Optional[Position] = None,
    *,
    window: int = 12,
    min_unique: int = 4,
    bbox_diameter_max: int = 3,
    static_ticks: int = 4,
) -> bool:
    """根据 tracker 当前状态判断是否处于小范围循环。

    调用方应先 ``observe_move`` 再调用本函数（或使用 ``guided_step_toward``）。
    触发条件（任一）：
    1. 连续 static_ticks 同格不动；
    2. 最近 window 步内唯一格 ≤ min_unique，且包围盒直径 ≤ bbox_diameter_max，
       且（若给 target）窗口内对 target 无净进展。
    """
    if tracker.cooldown > 0:
        return False

    if tracker.static_ticks >= max(1, static_ticks):
        return True

    hist = list(tracker.history)
    if len(hist) < max(4, window // 2):
        return False
    recent = hist[-window:] if len(hist) >= window else hist
    unique = set(recent)
    if len(unique) > min_unique:
        return False
    if bbox_diameter(recent) > bbox_diameter_max:
        return False

    # 有目标时：看窗口净进展（首→末），避免 2×2 绕圈时「偶然更近一格」被当成推进
    if target is not None and len(recent) >= 3:
        d0 = manhattan(recent[0], target)
        d1 = manhattan(recent[-1], target)
        # 净靠近 ≥2 格 → 正常推进，不判 loop
        if d0 - d1 >= 2:
            return False
    return True


def observe_move(tracker: LoopTracker, pos: Position, window: int = 12) -> None:
    """记录本 tick 位置，更新 static_ticks / cooldown。

    static_ticks = 连续同格 streak（含当前格）：首次见到某格为 1，
    再同格 +1。连续 4 次同格 → static_ticks==4。
    """
    if tracker.history.maxlen != window:
        # 窗口配置变更时重建 deque，保留最近足迹
        old = list(tracker.history)[-(window - 1) :] if window > 1 else []
        tracker.history = deque(old, maxlen=max(4, window))
    if tracker.last_pos is not None and tracker.last_pos == pos:
        tracker.static_ticks += 1
    else:
        tracker.static_ticks = 1  # 当前格开启新 streak
    tracker.last_pos = pos
    tracker.history.append(pos)
    if tracker.cooldown > 0:
        tracker.cooldown -= 1


def soft_obstacles_from_trail(
    tracker: LoopTracker,
    origin: Position,
    *,
    keep_last: int = 6,
    ban_origin_neighbors: bool = True,
) -> set[Position]:
    """把近期足迹变成软障碍，逼迫寻路离开循环区域。

    - 不把 origin 本身标为障碍（否则无处可走）；
    - 可选：把「曾走过的 origin 邻格」标障，打破贴墙横跳。
    """
    soft: set[Position] = set()
    trail = list(tracker.history)[-keep_last:]
    for p in trail:
        if p != origin:
            soft.add(p)
    if ban_origin_neighbors:
        for step in CARDINAL_DELTAS:
            nb = add_pos(origin, step)
            if nb in tracker.history and nb != origin:
                soft.add(nb)
    return soft


def _primary_hard_blocked(
    origin: Position,
    target: Position,
    hard_obs: set[Position],
) -> tuple[Optional[str], bool]:
    """主轴方向邻格是否被硬障碍挡住。"""
    primary = direction_between(origin, target)
    if not primary or primary not in NAME_TO_DELTA:
        return primary, False
    return primary, add_pos(origin, NAME_TO_DELTA[primary]) in hard_obs


def wall_follow_step(
    origin: Position,
    target: Position,
    hard_obs: set[Position],
    last_dir: Optional[str] = None,
    repath_side: int = 0,
) -> Optional[str]:
    """主轴被硬墙挡住时的贴墙绕行一步。

    修复「墙下左右横跳」：return_deposit 朝 Core 但 UP 被 ### 挡住时，
    clamp 在 LEFT/RIGHT 等分之间来回（一格 man-1 一格 man+1），永远不绕墙。
    本函数：
    - 优先延续 last_dir（若仍是合法垂向且非死胡同），形成稳定贴墙；
    - 否则按 repath_side 选侧，优先「一步后主轴开口」或「沿墙最短开口」；
    - 两侧皆死胡同/口袋（仅 2 格横跳）→ 允许反主轴撤退一步再绕。
    """
    primary, blocked = _primary_hard_blocked(origin, target, hard_obs)
    if not blocked or not primary:
        return None

    if primary in ("UP", "DOWN"):
        perps = ["RIGHT", "LEFT"]
        anti = "DOWN" if primary == "UP" else "UP"
    else:
        perps = ["DOWN", "UP"]
        anti = "LEFT" if primary == "RIGHT" else "RIGHT"
    if int(repath_side) % 2 == 1:
        perps = list(reversed(perps))

    def _side_run(start: Position, side_name: str) -> tuple[int, int]:
        """返回 (opens_immediately 0/1, run_to_opening；99=死胡同)。"""
        if side_name not in NAME_TO_DELTA:
            return 1, 99
        peek = add_pos(start, NAME_TO_DELTA[primary])
        opens = 0 if peek not in hard_obs else 1
        run = 0
        cur = start
        while run < 12:
            pk = add_pos(cur, NAME_TO_DELTA[primary])
            if pk not in hard_obs:
                break
            step = add_pos(cur, NAME_TO_DELTA[side_name])
            if step in hard_obs:
                return opens, 99
            cur = step
            run += 1
        else:
            return opens, 99
        return opens, run

    best: Optional[str] = None
    best_key: Optional[tuple] = None
    for name in perps:
        if name not in NAME_TO_DELTA:
            continue
        nb = add_pos(origin, NAME_TO_DELTA[name])
        if nb in hard_obs:
            continue
        opens, run = _side_run(nb, name)
        # 口袋检测：一步后对侧又是硬障且主轴仍堵 → 典型 2 格横跳
        other = perps[1] if name == perps[0] else perps[0]
        back = add_pos(nb, NAME_TO_DELTA[other]) if other in NAME_TO_DELTA else None
        pocket = (
            run >= 99
            or (
                back is not None
                and back == origin
                and add_pos(nb, NAME_TO_DELTA[primary]) in hard_obs
            )
        )
        key = (1 if pocket else 0, opens, run, manhattan(nb, target))
        if best_key is None or key < best_key:
            best_key = key
            best = name

    # 延续贴墙：仅当该侧不是死胡同/口袋
    if last_dir in perps and last_dir in NAME_TO_DELTA:
        nb = add_pos(origin, NAME_TO_DELTA[last_dir])
        if nb not in hard_obs:
            opens, run = _side_run(nb, last_dir)
            if run < 99 and opens == 0:
                return last_dir
            # 有开口或可跑通才延续；否则落入下面 best/anti 逻辑
            if run < 12 and best == last_dir:
                return last_dir

    # 两侧皆口袋/死胡同 → 反主轴撤退，离开窄缝再绕
    if best is None or (best_key is not None and best_key[0] == 1 and best_key[2] >= 99):
        if anti in NAME_TO_DELTA:
            retreat = add_pos(origin, NAME_TO_DELTA[anti])
            if retreat not in hard_obs:
                return anti
    return best


def guided_step_toward(
    origin: Position,
    target: Position,
    obstacles: Optional[Iterable[Position]] = None,
    last_dir: Optional[str] = None,
    tracker: Optional[LoopTracker] = None,
    memory: Optional[Any] = None,
    *,
    window: int = 12,
    min_unique: int = 4,
    bbox_diameter_max: int = 3,
    static_ticks: int = 4,
    repath_cooldown: int = 5,
    tick: int = 0,
) -> tuple[Optional[str], Optional[str], bool]:
    """朝 target 走一格；若检测到空间循环则强制重寻路。

    返回 ``(direction, new_last_dir, did_repath)``。
    did_repath=True 时调用方应打日志（如 ``:repath:loop``）并清空旧 last_dir 粘性。

    重寻路 / 绕墙策略：
    - 主轴被**硬障碍**挡住 → **贴墙绕行**（wall_follow_step），禁止墙下左右横跳；
    - 空间循环时用 soft trail，但**若主轴邻格在近期足迹中则不保护**（防口袋回钻）；
    - 主轴虽空闲但邻格刚走过（DOWN 撤退后再 UP）→ 强制侧向离开，禁止 2 格震荡；
    - 主轴硬挡时完全不用 soft trail。
    """
    if origin == target:
        if tracker is not None:
            observe_move(tracker, origin, window=window)
            tracker.static_ticks = 0
        return None, None, False

    hard_obs: set[Position] = set(obstacles) if obstacles is not None else set()
    blocked: set[Position] = set(hard_obs)
    did_repath = False
    ban: Optional[list[str]] = None
    eff_last = last_dir
    side = int(getattr(tracker, "repath_side", 0) or 0) if tracker is not None else 0

    if tracker is not None:
        observe_move(tracker, origin, window=window)
        looping = detect_spatial_loop(
            tracker,
            origin,
            target,
            window=window,
            min_unique=min_unique,
            bbox_diameter_max=bbox_diameter_max,
            static_ticks=static_ticks,
        )
        if looping:
            did_repath = True
            tracker.cooldown = max(1, repath_cooldown)
            tracker.last_repath_tick = tick
            tracker.repath_side = 1 - tracker.repath_side
            side = tracker.repath_side
            ban = [last_dir] if last_dir else []
            primary, primary_blocked = _primary_hard_blocked(origin, target, hard_obs)
            if primary_blocked:
                # 贴墙模式：绝不用 soft trail（否则垂向邻格被禁 → 只能上下抖）
                soft: set[Position] = set()
            elif manhattan(origin, target) <= 6:
                # 近目标：soft trail 会把短绕障走崩（man4→steps100+），只禁 last_dir
                soft = set()
            else:
                soft = soft_obstacles_from_trail(
                    tracker, origin, keep_last=6, ban_origin_neighbors=True
                )
                # 仅当主/次轴邻格**不在**近期足迹时才保护，否则会 DOWN 后 UP 回钻口袋
                recent = set(list(tracker.history)[-8:])
                if primary and primary in NAME_TO_DELTA:
                    pcell = add_pos(origin, NAME_TO_DELTA[primary])
                    if pcell not in recent:
                        soft.discard(pcell)
                dx = target[0] - origin[0]
                dy = target[1] - origin[1]
                if abs(dx) > 0 and abs(dy) > 0:
                    secondary = "RIGHT" if dx > 0 else "LEFT"
                    if abs(dy) > abs(dx):
                        secondary = "DOWN" if dy > 0 else "UP"
                    if secondary in NAME_TO_DELTA:
                        scell = add_pos(origin, NAME_TO_DELTA[secondary])
                        if scell not in recent:
                            soft.discard(scell)
            blocked = set(hard_obs) | soft
            eff_last = None
            tracker.static_ticks = 0

    # 主轴硬挡：优先贴墙绕行（不必等 loop 触发）
    primary, primary_blocked = _primary_hard_blocked(origin, target, hard_obs)
    if primary_blocked:
        wf = wall_follow_step(
            origin, target, hard_obs, last_dir=last_dir, repath_side=side
        )
        if wf is not None:
            return wf, wf, did_repath

    # 短距直达：man 很小且主轴空闲时，忽略 soft/reentry，避免 Core 周边障碍
    # 让 reconstruct/deposit dry-run 把 4 格路走成 100+ 步（足迹 soft 自我堵死）。
    man_left = manhattan(origin, target)
    if (
        man_left <= 6
        and primary
        and primary in NAME_TO_DELTA
        and not primary_blocked
    ):
        pcell = add_pos(origin, NAME_TO_DELTA[primary])
        if pcell not in hard_obs:
            # 仅当「刚从主轴邻格走来」且 man>2 时才考虑 reentry；man<=2 无条件推进
            hist = list(tracker.history) if tracker is not None else []
            prior = hist[:-1] if hist and hist[-1] == origin else hist
            just_left_primary = bool(prior and prior[-1] == pcell)
            if man_left <= 2 or not just_left_primary:
                return primary, primary, did_repath

    # 口袋回钻：主轴邻格空闲，但**上一格就是它**（刚从那里走来）
    # 典型：wall_follow anti=DOWN 离开 U 口袋后，clamp 又选 UP 钻回 → 2 格震荡。
    # 仅看 immediate predecessor，避免足迹误伤正常推进/探索。
    reentry = False
    primary_cell: Optional[Position] = None
    if (
        tracker is not None
        and primary
        and primary in NAME_TO_DELTA
        and not primary_blocked
    ):
        primary_cell = add_pos(origin, NAME_TO_DELTA[primary])
        hist = list(tracker.history)
        # hist[-1] 是 origin（本 tick observe）；prior[-1] 为上一格
        prior = hist[:-1] if hist and hist[-1] == origin else hist
        if prior and primary_cell == prior[-1]:
            reentry = True
        elif (
            last_dir
            and opposite_name(primary) == last_dir
            and primary_cell in prior[-3:]
        ):
            reentry = True

    if reentry and tracker is not None and primary_cell is not None:
        soft = soft_obstacles_from_trail(
            tracker, origin, keep_last=6, ban_origin_neighbors=True
        )
        soft.add(primary_cell)
        blocked = set(hard_obs) | soft
        eff_last = None
        ban = list({*(ban or []), primary, *([last_dir] if last_dir else [])})
        # 优先垂向离开；不把 reentry 标成 loop repath（避免探索 stall 被清零）
        if primary in ("UP", "DOWN"):
            perps = ("RIGHT", "LEFT") if side == 0 else ("LEFT", "RIGHT")
        else:
            perps = ("DOWN", "UP") if side == 0 else ("UP", "DOWN")
        anti = opposite_name(primary)
        prefer = list(perps)
        if anti:
            prefer.append(anti)
        for name in prefer:
            if name not in NAME_TO_DELTA or name == primary:
                continue
            nxt = add_pos(origin, NAME_TO_DELTA[name])
            if nxt in hard_obs or nxt == primary_cell:
                continue
            return name, name, did_repath

    direction, new_last = clamp_step_toward_memo(
        origin,
        target,
        obstacles=blocked,
        last_dir=eff_last,
        ban_dirs=ban,
        memory=memory,
    )

    # 若 clamp 仍选了回钻主轴，强制否决
    if (
        reentry
        and direction == primary
        and primary_cell is not None
    ):
        direction = None
        new_last = None

    # 重寻路 / 回钻兜底：优先垂向，再 anti；主轴回钻时不要再优先 primary
    if (did_repath or reentry) and direction is None:
        prefer_order: list[str] = []
        if primary and not reentry:
            prefer_order.append(primary)
        if primary in ("UP", "DOWN"):
            perp = ("RIGHT", "LEFT") if side == 0 else ("LEFT", "RIGHT")
        elif primary in ("LEFT", "RIGHT"):
            perp = ("DOWN", "UP") if side == 0 else ("UP", "DOWN")
        elif last_dir:
            perp = (
                ("UP", "DOWN") if last_dir in ("LEFT", "RIGHT") else ("LEFT", "RIGHT")
            )
            if side == 1:
                perp = (perp[1], perp[0])
        else:
            perp = ("RIGHT", "LEFT", "DOWN", "UP")
        prefer_order.extend(perp)
        if reentry:
            anti = opposite_name(primary)
            if anti:
                prefer_order.append(anti)
        for name in prefer_order:
            if name not in NAME_TO_DELTA:
                continue
            if reentry and name == primary:
                continue
            nxt = add_pos(origin, NAME_TO_DELTA[name])
            if nxt not in hard_obs:  # 兜底只看硬障，忽略 soft
                if reentry and primary_cell is not None and nxt == primary_cell:
                    continue
                direction = name
                new_last = name
                break

    # 仍无解：回退到无软障的普通一步（避免卡死）；回钻时仍禁 primary 邻格
    if direction is None:
        fallback_obs = set(hard_obs)
        if reentry and primary_cell is not None:
            fallback_obs.add(primary_cell)
        direction, new_last = clamp_step_toward_memo(
            origin, target, obstacles=fallback_obs, last_dir=None, memory=memory
        )
        if reentry and direction == primary:
            # 最后手段：任意非 primary、非硬障
            for name, delta in NAME_TO_DELTA.items():
                if name == primary:
                    continue
                nxt = add_pos(origin, delta)
                if nxt not in hard_obs:
                    direction, new_last = name, name
                    break

    return direction, new_last, did_repath


def direction_between(origin: Position, target: Position) -> Optional[str]:
    """返回从 origin 指向 target 的主方向名（邻格或远距主轴）。

    若同格返回 None；对角时优先水平。
    """
    if origin == target:
        return None
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    if abs(dx) >= abs(dy) and dx != 0:
        return "RIGHT" if dx > 0 else "LEFT"
    if dy != 0:
        return "DOWN" if dy > 0 else "UP"
    return None


def is_adjacent(a: Position, b: Position) -> bool:
    """是否四向相邻（曼哈顿 = 1）。"""
    return manhattan(a, b) == 1


def is_in_range_cardinal_or_diag(
    origin: Position,
    target: Position,
    min_range: int = 1,
    max_range: int = 3,
) -> bool:
    """Ranger 射程判定：同行/同列/精确 45° 对角线，距离 1–3。"""
    dx = abs(origin[0] - target[0])
    dy = abs(origin[1] - target[1])
    dist = max(dx, dy)  # 切比雪夫，对角时等于步数
    man = dx + dy
    if man < min_range or man > max_range * 2:
        # 快速拒绝：曼哈顿过大不可能在射程
        pass
    # 直线：同行或同列
    if dx == 0 and dy == 0:
        return False
    if dx == 0 or dy == 0:
        chebyshev = max(dx, dy)
        return min_range <= chebyshev <= max_range
    # 精确 45° 对角线
    if dx == dy:
        return min_range <= dx <= max_range
    return False


def chebyshev(a: Position, b: Position) -> int:
    """切比雪夫距离 max(|dx|, |dy|)。"""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def nearest(
    origin: Position,
    candidates: Sequence[Position],
) -> Optional[Position]:
    """返回离 origin 曼哈顿最近的点；空序列返回 None。"""
    if not candidates:
        return None
    return min(candidates, key=lambda p: (manhattan(origin, p), p[0], p[1]))


def defense_ring_slots(
    core: Position,
    radius: int,
    count: int,
    phase: int = 0,
) -> list[Position]:
    """在 Core 周边生成 count 个防守环槽位（曼哈顿环近似）。

    按四边均匀取样，phase 用于轮换巡逻起点。
    """
    if count <= 0 or radius <= 0:
        return [core]

    # 曼哈顿菱形环上的全部格
    ring: list[Position] = []
    for dx in range(-radius, radius + 1):
        dy = radius - abs(dx)
        ring.append((core[0] + dx, core[1] + dy))
        if dy != 0:
            ring.append((core[0] + dx, core[1] - dy))

    # 去重并稳定排序（顺时针近似：按角度）
    unique = sorted(set(ring), key=lambda p: (
        _angle_key(core, p),
        p[0],
        p[1],
    ))
    if not unique:
        return [core]

    n = len(unique)
    slots: list[Position] = []
    for i in range(count):
        idx = (phase + i * max(1, n // count)) % n
        slots.append(unique[idx])
    return slots


def _angle_key(origin: Position, point: Position) -> float:
    """用于环上排序的简易角度键（atan2 近似，避免 import math 亦可）。"""
    import math

    return math.atan2(point[1] - origin[1], point[0] - origin[0])


def ring_points(center: Position, radius: int) -> list[Position]:
    """返回以 center 为中心的曼哈顿菱形环上全部点（顺时针稳定排序）。

    radius=0 返回 [center]；radius>0 时环上恰有 4*radius 个点。
    排序键 = (角度, x, y)，确定性，供扇区切分与螺旋扫掠复用。
    """
    if radius <= 0:
        return [center]
    ring: list[Position] = []
    for dx in range(-radius, radius + 1):
        dy = radius - abs(dx)
        ring.append((center[0] + dx, center[1] + dy))
        if dy != 0:
            ring.append((center[0] + dx, center[1] - dy))
    unique = sorted(set(ring), key=lambda p: (_angle_key(center, p), p[0], p[1]))
    return unique


def sector_points(
    center: Position,
    radius: int,
    sector_id: int,
    sector_count: int = 4,
    phase_offset: int = 0,
) -> list[Position]:
    """返回环上属于给定扇区的点（确定性扇区切分）。

    规则：环上第 i 个点（稳定排序）当 `(i + phase_offset) % sector_count ==
    sector_id` 时属于该扇区。扇区之间天然不重叠，合起来覆盖整环。
    """
    if sector_count <= 0:
        sector_count = 1
    pts = ring_points(center, radius)
    sid = sector_id % sector_count
    result: list[Position] = []
    for i, p in enumerate(pts):
        if (i + phase_offset) % sector_count == sid:
            result.append(p)
    return result


def spiral_target(
    core: Position,
    sector_id: int,
    sector_count: int,
    ring: int,
    index: int,
) -> Position:
    """返回螺旋扫掠的当前目标点。

    在第 ring 环上取 sector_id 扇区的第 index 个点；index 越界时取模回绕，
    保证永远返回具体 Position（确定性）。
    """
    pts = sector_points(core, ring, sector_id, sector_count)
    if not pts:
        return core
    return pts[index % len(pts)]


def beacon_oriented_spiral_target(
    core: tuple[int,int],
    beacon: tuple[int,int],
    sector_id: int,
    sector_count: int,
    ring: int,
    index: int,
) -> tuple[int,int]:
    import math
    pts = ring_points(beacon, ring)
    beacon_angle = _angle_key(core, beacon)
    sorted_pts = sorted(pts, key=lambda p: (_angle_key(beacon, p) + beacon_angle * 0.1) % (2 * math.pi))
    if not sorted_pts:
        return beacon
    return sorted_pts[(index + sector_id * (len(sorted_pts)//sector_count)) % len(sorted_pts)]


def beacon_progress_target(
    current: Position,
    beacon: Position,
    step_radius: int = 8,
    offset: int = 0,
    avoid: Optional[Iterable[Position]] = None,
) -> Position:
    """返回当前 Worker 朝 Beacon 推进的阶段性目标点（探索优化决策 3）。

    纯函数、确定性，不依赖 SpiralState，单测可直测。规则：
    - `manhattan(current, beacon) <= step_radius` → 直接返回 beacon（收官）。
    - 否则在 current→beacon 方向线上按轴向比例取「距 current 约 step_radius
      曼哈顿距离」的点（曼哈顿测地线中点）。
    - `offset` 决定横向偏移档位（-1/0/+1）：沿垂直主推进轴偏移，用于绕障与
      多 Worker 错开路径；直线点在 `avoid` 障碍内时横向偏一档重试。
    - 每 tick 从当前 pos 重新生成 → 天然随 Worker 推进而推进（d_beacon 单调下降）。
    """
    if manhattan(current, beacon) <= step_radius:
        return beacon

    dx = beacon[0] - current[0]
    dy = beacon[1] - current[1]
    total = abs(dx) + abs(dy)
    if total == 0:
        return beacon

    nx = round(step_radius * abs(dx) / total)
    ny = step_radius - nx
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    base = (current[0] + sx * nx, current[1] + sy * ny)

    blocked: set[Position] = set(avoid) if avoid is not None else set()
    # 横向偏移沿垂直主推进轴的轴（|dx|>=|dy| → 沿 y；否则沿 x）
    lateral_on_y = abs(dx) >= abs(dy)

    def _offset_point(off: int) -> Position:
        if lateral_on_y:
            return (base[0], base[1] + off)
        return (base[0] + off, base[1])

    # 候选顺序：先当前 offset 档，再直线点，再向两侧扩展（绕障 / 错开路径）
    candidates: list[Position] = []
    seen: set[Position] = set()
    for off in (offset, 0, 1, -1, 2, -2):
        cand = _offset_point(off)
        if cand in seen:
            continue
        seen.add(cand)
        candidates.append(cand)

    for cand in candidates:
        if cand in blocked:
            continue
        if manhattan(cand, beacon) < manhattan(current, beacon):
            return cand

    # 兜底：全部被挡/反向时返回直线点（由 clamp_step_toward_memo 绕行）
    return base


def point_sector(
    center: Position,
    pos: Position,
    sector_count: int = 4,
    phase_offset: int = 0,
) -> int:
    """返回 pos 相对 center 所属扇区（与 sector_points 同一套切分规则）。"""
    if sector_count <= 0:
        sector_count = 1
    r = manhattan(center, pos)
    if r <= 0:
        return 0
    pts = ring_points(center, r)
    try:
        idx = pts.index(pos)
    except ValueError:
        return 0
    return (idx + phase_offset) % sector_count


def cells_toward_ring(
    unit_pos: Position,
    core: Position,
    radius: int,
) -> Position:
    """计算单位应前往的防守环目标点（最近环上点）。"""
    if manhattan(unit_pos, core) == radius:
        return unit_pos
    ring = defense_ring_slots(core, radius, count=max(4, radius * 4), phase=0)
    target = nearest(unit_pos, ring)
    return target if target is not None else core


# 探索用的基础四向 + 对角扩展（至少 4 向，最多 8 向）
EXPLORE_DIRS: tuple[Position, ...] = (
    (1, 0),   # RIGHT
    (0, 1),   # DOWN
    (-1, 0),  # LEFT
    (0, -1),  # UP
    (1, 1),   # DOWN-RIGHT
    (-1, 1),  # DOWN-LEFT
    (-1, -1), # UP-LEFT
    (1, -1),  # UP-RIGHT
)


def explore_radius(
    tick: int,
    base: int = 4,
    max_radius: int = 24,
    expand_every: int = 8,
) -> int:
    """随 tick 扩大的探索半径，封顶 max_radius。"""
    if expand_every <= 0:
        expand_every = 1
    if tick < 0:
        tick = 0
    radius = base + (tick // expand_every)
    if radius > max_radius:
        return max_radius
    if radius < 1:
        return 1
    return radius


def explore_target(
    core: Position,
    worker_index: int,
    tick: int = 0,
    phase_offset: int = 0,
    base_radius: int = 4,
    max_radius: int = 24,
    expand_every: int = 8,
    n_dirs: int = 8,
) -> Position:
    """按 worker 索引分散探索目标 = core + dir * radius。

    不同 worker 走不同方向（至少 4 向）；phase_offset 用于振荡换相。
    """
    n = max(4, min(n_dirs, len(EXPLORE_DIRS)))
    idx = (int(worker_index) + int(phase_offset)) % n
    dx, dy = EXPLORE_DIRS[idx]
    radius = explore_radius(tick, base=base_radius, max_radius=max_radius, expand_every=expand_every)
    return (core[0] + dx * radius, core[1] + dy * radius)


def explore_targets(
    core: Position,
    count: int,
    tick: int = 0,
    base_radius: int = 4,
    max_radius: int = 24,
    expand_every: int = 8,
) -> list[Position]:
    """为 count 个 worker 生成分散探索目标列表。"""
    if count <= 0:
        return []
    return [
        explore_target(
            core,
            worker_index=i,
            tick=tick,
            base_radius=base_radius,
            max_radius=max_radius,
            expand_every=expand_every,
        )
        for i in range(count)
    ]


def outward_step(
    origin: Position,
    core: Position,
    preferred: Optional[str] = None,
    obstacles: Optional[Iterable[Position]] = None,
    last_dir: Optional[str] = None,
) -> Optional[str]:
    """优先远离 Core 的一步（搜图用）。

    preferred 若可行且不靠近 Core，则优先采用；否则在四向中选
    使 manhattan(next, core) 最大的非障碍格。
    last_dir 提供方向记忆：若首选方向恰为 last_dir 的反方向（紧接反向
    对抖），降权，优先垂直外扩/keep 方向。
    """
    blocked: set[Position] = set(obstacles) if obstacles is not None else set()
    here = manhattan(origin, core)
    opp = opposite_name(last_dir)

    order: list[str] = []
    if preferred in NAME_TO_DELTA:
        order.append(preferred)
    for name in ("RIGHT", "DOWN", "LEFT", "UP"):
        if name not in order:
            order.append(name)

    best_name: Optional[str] = None
    best_score = -10_000
    for name in order:
        step = NAME_TO_DELTA[name]
        nxt = add_pos(origin, step)
        if nxt in blocked:
            continue
        dist = manhattan(nxt, core)
        # 优先外扩；同等距离时偏好 preferred 顺序
        score = dist * 10
        if name == preferred:
            score += 3
        if dist < here:
            score -= 20  # 重罚往回走
        if name == opp:
            score -= 30  # 禁止紧接反向对抖（仍可作为最后兜底）
        if name == last_dir:
            score += 2   # keep 方向微粘性，减少垂直侧左右横跳
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def estimate_path_steps(
    origin: Position,
    target: Position,
    obstacles: set[Position],
    memory: Optional[Any] = None,
    max_steps: int = 64,
) -> tuple[int, list[Position]]:
    """Dry-run 估算从 origin 到 target 所需步数与被堵障碍格列表。

    - 内部使用临时 LoopTracker 实例（绝不触碰全局 _loop_trackers）；
    - 每步若被障碍挡且传入 memory → 调用 memory.record_obstacle_block(blocked_pos, virtual_tick)
      （同一次 estimate 调用内同一障碍格只 record 一次）；
    - 超过 max_steps 未到达则兜底返回 (max_steps + manhattan(remainder), [])；
    - 返回 (总步数, 被堵障碍格 Position 列表，按遇到顺序)。
    """
    steps, blocked_obs, _waypoints = reconstruct_path(
        origin, target, obstacles, memory=memory, max_steps=max_steps
    )
    return (steps, blocked_obs)


def reconstruct_path(
    origin: Position,
    target: Position,
    obstacles: set[Position],
    memory: Optional[Any] = None,
    max_steps: int = 64,
) -> tuple[int, list[Position], list[Position]]:
    """Dry-run 重建导航路径：返回 (步数, 被堵障碍列表, 路径路点含 origin/target)。

    与 estimate_path_steps 同源，额外输出完整 waypoints 供 Dashboard 可视化。
    不修改全局状态；memory.record_obstacle_block 仅在传入 memory 时写入。
    """
    blocked_obs: list[Position] = []
    recorded_for_memory: set[Position] = set()
    tracker = LoopTracker()
    pos = origin
    last_dir: Optional[str] = None
    steps = 0
    virtual_tick = 0
    waypoints: list[Position] = [origin]

    if origin == target:
        return (0, [], [origin])

    def _on_blocked(blocked_pos: Position) -> None:
        if blocked_pos not in blocked_obs:
            blocked_obs.append(blocked_pos)
        if (
            memory is not None
            and hasattr(memory, "record_obstacle_block")
            and blocked_pos not in recorded_for_memory
        ):
            memory.record_obstacle_block(blocked_pos, virtual_tick)
            recorded_for_memory.add(blocked_pos)

    while pos != target and steps < max_steps:
        virtual_tick += 1

        dx = target[0] - pos[0]
        dy = target[1] - pos[1]
        primary_candidates: list[tuple[Position, str]] = []
        if dx != 0:
            step = DIR_RIGHT if dx > 0 else DIR_LEFT
            primary_candidates.append((add_pos(pos, step), DIR_NAMES[step]))
        if dy != 0:
            step = DIR_DOWN if dy > 0 else DIR_UP
            primary_candidates.append((add_pos(pos, step), DIR_NAMES[step]))

        for cand_pos, _cand_name in primary_candidates:
            if cand_pos in obstacles:
                _on_blocked(cand_pos)

        direction, new_last, _ = guided_step_toward(
            pos,
            target,
            obstacles=obstacles,
            last_dir=last_dir,
            tracker=tracker,
            memory=memory,
            tick=virtual_tick,
        )

        if direction is None or direction not in NAME_TO_DELTA:
            if pos == target:
                break
            remainder = manhattan(pos, target)
            return (max_steps + remainder, blocked_obs, waypoints)

        step_delta = NAME_TO_DELTA[direction]
        nxt = add_pos(pos, step_delta)

        if nxt in obstacles:
            _on_blocked(nxt)
            break

        pos = nxt
        last_dir = new_last
        steps += 1
        waypoints.append(pos)

    if pos == target:
        return (steps, blocked_obs, waypoints)

    remainder = manhattan(pos, target)
    return (max_steps + remainder, blocked_obs, waypoints)
