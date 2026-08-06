"""路径与几何工具：曼哈顿距离、朝目标一步、防守环位。

不依赖 arena-hero SDK，便于离线单测。
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# 位置类型：(x, y)
Position = tuple[int, int]

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
    name: str,
    gain: int,
    dx: int,
    dy: int,
    last_dir: Optional[str],
) -> int:
    """clamp_step_toward_memo 的分值：分层避免「反向对抖 / 无限远离」。

    分层规则（按优先级）：
    1. gain > 0（靠近 target）且非 last_dir 反方向 → 100（最优推进）
    2. gain > 0 但恰为反方向 → 80（死胡同兜底，仍优于绕行远离）
    3. gain < 0（必须绕行）→ 优先跨轴绕行（-30），
       同轴远离（反目标轴向）最后（-70）；同层内 keep 微加分、反向微罚。
    """
    opp = opposite_name(last_dir)
    if gain >= 0:
        score = 100 + gain
        if name == last_dir:
            score += 10  # 沿原方向继续推进
        if name == opp:
            score -= 20  # 反向推进降权（仅当它是唯一推进方向时胜出）
        return score
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
        return score
    score = -70  # 反目标轴向（远离 target），最后兜底
    if name == opp:
        score -= 20
    return score


def clamp_step_toward_memo(
    origin: Position,
    target: Position,
    obstacles: Optional[Iterable[Position]] = None,
    last_dir: Optional[str] = None,
    memo: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """朝 target 走一格，返回 (方向, 更新后的 last_dir)。

    逻辑与 clamp_step_toward 相同（优先减少较大轴向差距、被挡则绕行），
    但加入「方向记忆去抖」：
    - 若首选方向 == last_dir 的反方向（紧接反向对抖），强降权；
    - 优先垂直（跨轴）方向 / keep 方向贴墙绕行；
    - 实在只能远离时选跨轴绕行，绝不先选反目标轴向。

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
        score = _clamp_score(name, gain, dx, dy, last_dir)
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
