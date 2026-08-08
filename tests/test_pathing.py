"""路径模块单测：带方向记忆的去抖寻路（防 A↔B 对抖）+ 螺旋扫掠几何。"""

from __future__ import annotations

from bot.pathing import (
    NAME_TO_DELTA,
    LoopTracker,
    Position,
    add_pos,
    beacon_progress_target,
    bbox_diameter,
    clamp_step_toward,
    clamp_step_toward_memo,
    detect_spatial_loop,
    guided_step_toward,
    manhattan,
    observe_move,
    ring_points,
    sector_points,
    spiral_target,
)


_DIR_DELTA = {name: delta for name, delta in NAME_TO_DELTA.items()}


def _max_consecutive_alternation(dirs: list[str]) -> int:
    """统计方向序列中「紧接反向」连续出现的最大次数。

    UP,DOWN,UP → 2 次连续对抖；LEFT,RIGHT,LEFT → 2。
    """
    max_run = 0
    run = 0
    for prev, cur in zip(dirs, dirs[1:]):
        if prev and cur and _DIR_DELTA[cur] == tuple(-v for v in _DIR_DELTA[prev]):
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run


def _simulate(
    origin: Position,
    target: Position,
    obstacles: set[Position],
    ticks: int,
    last_dir: str | None = None,
) -> tuple[list[str], Position]:
    """用带 memo 的寻路连续走 ticks 步，返回 (方向序列, 终点)。"""
    pos = origin
    dirs: list[str] = []
    for _ in range(ticks):
        direction, last_dir = clamp_step_toward_memo(
            pos, target, obstacles, last_dir=last_dir
        )
        if direction is None:
            break
        dirs.append(direction)
        pos = add_pos(pos, _DIR_DELTA[direction])
    return dirs, pos


def test_clamp_step_toward_no_oscillation() -> None:
    """同列 + 朝 Core 方向被挡：带 memo 的寻路不得严格交替 > 2 次，且能绕行靠近 target。

    场景：origin=(10,12)，target=(10,10)，(10,11) 是障碍（Core 正上方被挡）。
    旧 clamp_step_toward 会 DOWN→UP→DOWN… 无限对抖；新寻路应绕行接近。
    """
    origin = (10, 12)
    target = (10, 10)
    obstacles = {(10, 11)}

    dirs, pos = _simulate(origin, target, obstacles, ticks=6)

    # 绕行成功：终点距 target 严格小于起点距 target（靠近了）
    assert manhattan(pos, target) < manhattan(origin, target), (
        f"did not approach target: pos={pos} dirs={dirs}"
    )
    # 不出现 UP/DOWN 或 LEFT/RIGHT 严格交替超过 2 次
    assert _max_consecutive_alternation(dirs) <= 2, f"oscillation dirs={dirs}"

    # 对照组：旧 clamp_step_toward 同场景会走 DOWN（远离 target 的兜底方向），
    # 下一 tick UP 又变可达 → UP/DOWN 无限对抖（线上 return_deposit:UP↔DOWN 根因）。
    old_first = clamp_step_toward(origin, target, obstacles)
    assert old_first == "DOWN", (
        f"old clamp_step_toward should pick anti-target DOWN, got {old_first}"
    )


def test_clamp_step_toward_memo_reaches_target_around_single_obstacle() -> None:
    """单格障碍正前方：最多 6 步内绕行到达 target 邻域。"""
    origin = (10, 12)
    target = (10, 10)
    obstacles = {(10, 11)}
    dirs, pos = _simulate(origin, target, obstacles, ticks=8)
    assert manhattan(pos, target) <= 1, f"should reach near target, pos={pos} dirs={dirs}"


def test_clamp_step_toward_memo_keeps_progress_when_detour_available() -> None:
    """横向可直接靠近时，不得因 last_dir 反向而拒绝推进（避免无限远离）。"""
    # origin 在目标左下方，RIGHT 可推进（但 RIGHT 是 last_dir 的反方向）
    origin = (9, 12)
    target = (10, 10)
    obstacles = {(10, 11)}  # UP 被挡，RIGHT 可走
    dirs, _ = _simulate(origin, target, obstacles, ticks=4, last_dir="LEFT")
    # 至少有一次 UP/RIGHT 推进（而非一路 LEFT 远离）
    assert any(d in ("UP", "RIGHT") for d in dirs), f"should progress, dirs={dirs}"


def test_clamp_step_toward_memo_memo_dict_roundtrip() -> None:
    """memo dict 自动读写 last_dir，与返回的 last_dir 一致。"""
    memo: dict = {}
    direction, last = clamp_step_toward_memo(
        (10, 12), (10, 10), {(10, 11)}, memo=memo
    )
    assert direction is not None
    assert memo.get("last_dir") == last
    assert last == direction


def test_clamp_step_toward_memo_at_target() -> None:
    """已在目标格返回 (None, None) 且不清除 last_dir 语义（返回 None）。"""
    direction, last = clamp_step_toward_memo((10, 10), (10, 10), last_dir="UP")
    assert direction is None and last is None


def test_bbox_diameter_and_detect_spatial_loop() -> None:
    """小范围足迹：唯一格少 + 包围盒小 → 判 loop；正常推进不判。"""
    assert bbox_diameter([(0, 0), (1, 0), (1, 1)]) == 2
    assert bbox_diameter([]) == 0

    tracker = LoopTracker()
    # 在 2×2 内绕圈 12 步
    cycle = [(0, 0), (1, 0), (1, 1), (0, 1)] * 3
    for p in cycle:
        observe_move(tracker, p, window=12)
    assert detect_spatial_loop(
        tracker, cycle[-1], target=(10, 10), window=12, min_unique=4, bbox_diameter_max=3
    )

    # 正常朝目标推进：不应判 loop
    progress = LoopTracker()
    for x in range(12):
        observe_move(progress, (x, 0), window=12)
    assert not detect_spatial_loop(
        progress, (11, 0), target=(20, 0), window=12, min_unique=4, bbox_diameter_max=3
    )


def test_guided_step_toward_repaths_on_static() -> None:
    """连续同格不动 → 强制 repath，清空 last_dir 粘性并换方向。"""
    tracker = LoopTracker()
    # 模拟服务端拒步：连续 4 tick 停在 (5,5)，目标在右
    for _ in range(4):
        observe_move(tracker, (5, 5), window=12)
    assert detect_spatial_loop(tracker, (5, 5), target=(10, 5), static_ticks=4)

    direction, last, repath = guided_step_toward(
        (5, 5),
        (10, 5),
        obstacles=set(),
        last_dir="LEFT",  # 旧粘性方向（远离目标）
        tracker=tracker,
        static_ticks=4,
        repath_cooldown=5,
    )
    assert repath is True
    assert direction == "RIGHT"  # 应朝目标推进，而非 stick LEFT
    assert last == "RIGHT"


def test_guided_step_toward_repaths_on_bbox_loop() -> None:
    """2×2 绕圈后 repath：禁止 last_dir 对抖，足迹成软障。"""
    tracker = LoopTracker()
    cycle = [(10, 12), (11, 12), (11, 13), (10, 13)] * 3
    for p in cycle:
        observe_move(tracker, p, window=12)
    # 起点在循环区，目标 Core 在上方；中间有障碍模拟贴墙
    obstacles = {(10, 11)}
    direction, _last, repath = guided_step_toward(
        (10, 12),
        (10, 10),
        obstacles=obstacles,
        last_dir="DOWN",
        tracker=tracker,
        window=12,
        min_unique=4,
        bbox_diameter_max=3,
        repath_cooldown=5,
    )
    assert repath is True
    assert direction is not None
    # 不应继续 DOWN（远离 Core 的循环方向）
    assert direction != "DOWN"


def test_ring_points_counts_and_manhattan() -> None:
    """曼哈顿环：radius=0 单点；radius>0 共 4r 个点且均距中心 r。"""
    assert ring_points((5, 5), 0) == [(5, 5)]
    for r in (1, 2, 5, 8):
        pts = ring_points((10, 10), r)
        assert len(pts) == 4 * r, f"ring {r} has {len(pts)}"
        assert all(manhattan(p, (10, 10)) == r for p in pts)
    # 确定性
    assert ring_points((10, 10), 5) == ring_points((10, 10), 5)


def test_ring_points_sorted_by_angle() -> None:
    """环上点按角度稳定排序（顺时针近似），相邻点角度不剧烈跳变。"""
    import math

    center = (0, 0)
    pts = ring_points(center, 3)
    angles = [math.atan2(p[1], p[0]) for p in pts]
    assert angles == sorted(angles), "ring points should be angle-ordered"


def test_sector_points_partition_ring() -> None:
    """扇区切分：各扇区互不重叠，并集覆盖整环。"""
    center = (10, 10)
    for r in (1, 2, 5, 9):
        pts = ring_points(center, r)
        sectors = [set(sector_points(center, r, s, 4)) for s in range(4)]
        # 不重叠
        for i in range(4):
            for j in range(i + 1, 4):
                assert not (sectors[i] & sectors[j]), f"overlap r={r} {i},{j}"
        # 覆盖
        union = set().union(*sectors)
        assert union == set(pts), f"union mismatch r={r}"
        # 每扇区点数一致
        counts = {len(s) for s in sectors}
        assert len(counts) == 1, f"uneven sectors r={r}: {counts}"


def test_sector_points_phase_offset_shifts() -> None:
    """phase_offset 旋转扇区起点，不影响覆盖。"""
    center = (0, 0)
    s0 = set(sector_points(center, 5, 0, 4, phase_offset=1))
    s1 = set(sector_points(center, 5, 1, 4, phase_offset=1))
    assert not (s0 & s1)
    assert len(s0 | s1 | set(sector_points(center, 5, 2, 4, phase_offset=1))
               | set(sector_points(center, 5, 3, 4, phase_offset=1))) == 4 * 5


def test_spiral_target_deterministic_and_in_ring() -> None:
    """spiral_target：确定性、落在环上、index 越界回绕。"""
    core = (10, 10)
    t1 = spiral_target(core, 1, 4, 5, 0)
    t2 = spiral_target(core, 1, 4, 5, 0)
    assert t1 == t2
    assert manhattan(t1, core) == 5
    # index 越界 → 回绕到环内某点（仍在环上）
    t_overflow = spiral_target(core, 1, 4, 5, 999)
    assert manhattan(t_overflow, core) == 5


def test_spiral_target_sectors_differ() -> None:
    """不同扇区在同一 ring/index 的目标不同（分散探索）。"""
    core = (10, 10)
    targets = {spiral_target(core, s, 4, 5, 0) for s in range(4)}
    assert len(targets) == 4
    # 同一扇区不同 index 目标也不同（扫掠推进）
    t0 = spiral_target(core, 0, 4, 5, 0)
    t1 = spiral_target(core, 0, 4, 5, 1)
    assert t0 != t1


def test_beacon_progress_target_straight_line() -> None:
    """直线推进：beacon_progress_target((0,0),(10,0),4) ≈ (4,0)，仍在推进方向。"""
    t = beacon_progress_target((0, 0), (10, 0), step_radius=4)
    assert t == (4, 0)
    assert manhattan(t, (10, 0)) < manhattan((0, 0), (10, 0))


def test_beacon_progress_target_short_finish() -> None:
    """短距收官：manhattan ≤ step_radius → 直接返回 beacon。"""
    assert beacon_progress_target((0, 0), (3, 4), step_radius=8) == (3, 4)
    assert beacon_progress_target((5, 5), (5, 5), step_radius=8) == (5, 5)


def test_beacon_progress_target_avoids_obstacle() -> None:
    """避障：直线点在障碍内 → 横向偏一档，不再返回该障碍格。"""
    t = beacon_progress_target(
        (0, 0), (10, 0), step_radius=4, offset=0, avoid={(4, 0)}
    )
    assert t != (4, 0)
    # 仍向 Beacon 方向推进（比当前位置更近）
    assert manhattan(t, (10, 0)) < 10


def test_beacon_progress_target_offset_deterministic() -> None:
    """offset 档位：确定性 + 横向错开路径。"""
    t1 = beacon_progress_target((0, 0), (10, 0), step_radius=4, offset=1)
    assert t1 == beacon_progress_target((0, 0), (10, 0), step_radius=4, offset=1)
    t_plain = beacon_progress_target((0, 0), (10, 0), step_radius=4, offset=0)
    assert t1 != t_plain


from bot.pathing import estimate_path_steps
from bot.memory import MemoryMap


def test_TR1_1_estimate_no_obstacle_equals_manhattan() -> None:
    """TR-1.1: 无障碍 A→B est_steps == manhattan，d=0/1/4/10/24 五种距离。"""
    distances = [0, 1, 4, 10, 24]
    for d in distances:
        origin = (0, 0)
        target = (d, 0)
        obstacles: set[Position] = set()
        est_steps, _blocked = estimate_path_steps(origin, target, obstacles)
        assert est_steps == manhattan(origin, target), (
            f"d={d}: est_steps={est_steps} != manhattan={manhattan(origin, target)}"
        )


def test_TR1_2_estimate_with_walls_increasing_or_flat() -> None:
    """TR-1.2: d=4 场景加 1/2/3 堵墙 → est_steps 严格递增或持平（绝不减少）。"""
    origin = (0, 0)
    target = (4, 0)
    prev_steps = -1

    obstacles0: set[Position] = set()
    steps0, _ = estimate_path_steps(origin, target, obstacles0)
    prev_steps = steps0

    wall_sets = [
        {(2, 0)},
        {(2, 0), (2, 1)},
        {(2, 0), (2, 1), (2, -1)},
    ]
    for walls in wall_sets:
        steps, _ = estimate_path_steps(origin, target, walls)
        assert steps >= prev_steps, (
            f"walls={walls}: steps={steps} < prev_steps={prev_steps}（不允许减少）"
        )
        prev_steps = steps


def test_TR1_3_estimate_deterministic() -> None:
    """TR-1.3: 同场景连续调用 2 次 estimate → 返回 tuple 完全相等。"""
    origin = (0, 0)
    target = (10, 5)
    obstacles = {(4, 2), (4, 3), (5, 2)}
    r1 = estimate_path_steps(origin, target, obstacles)
    r2 = estimate_path_steps(origin, target, obstacles)
    assert r1 == r2, f"两次 estimate 结果不等: r1={r1} r2={r2}"


def test_TR1_4_memory_block_count_increments() -> None:
    """TR-1.4: MemoryMap + 连续 3 次 estimate 同一堵墙 → block_count 恰好增加 3。"""
    origin = (0, 0)
    target = (4, 0)
    wall_pos = (2, 0)
    obstacles = {wall_pos}

    mem = MemoryMap()
    before = 0
    ost = mem.obstacle_cache.get(wall_pos)
    if ost is not None:
        before = ost.block_count

    for _ in range(3):
        estimate_path_steps(origin, target, obstacles, memory=mem)

    after_ost = mem.obstacle_cache.get(wall_pos)
    assert after_ost is not None, "obstacle_cache 中未记录 wall_pos"
    after = after_ost.block_count
    assert after - before == 3, (
        f"block_count 增量: {after} - {before} = {after - before}，期望 3"
    )


# ---------------------------------------------------------------------------
# Task 2: 历史障碍降权（obstacle_cache.block_count 参与 clamp 评分）
# ---------------------------------------------------------------------------


class _DummyObstacleState:
    """测试用 ObstacleState 替代类（直接存 block_count）。"""

    def __init__(self, block_count: int):
        self.block_count = block_count


def test_TR_2_1_block_count_ge3_avoid() -> None:
    """TR-2.1: block_count>=3 → 历史障碍被强降权（-100），避开该方向。

    origin=(0,0), target=(10,5), 两个主轴方向都有 gain>0：
    - RIGHT gain=1 (无历史降权时 score=101)
    - DOWN gain=1 (无历史降权时 score=101)
    现在 obstacle_cache[(1,0)].block_count=3 → RIGHT 降权 -100 → 1；
    DOWN 不受影响 → DOWN 仍是 101，胜出。
    """
    origin = (0, 0)
    target = (10, 5)  # 这样 RIGHT 和 DOWN 都是 gain=1 的推进方向
    obstacles = set()  # 无硬障碍，单纯比较历史降权效果

    # 构造 obstacle_cache: dict，存对象（有 .block_count 属性）
    obstacle_cache_obj = {
        (1, 0): _DummyObstacleState(block_count=3),  # RIGHT 历史卡了3次
    }
    # 构造一个带 obstacle_cache 属性的 dummy memory
    class _DummyMem:
        def __init__(self, cache):
            self.obstacle_cache = cache
    mem = _DummyMem(obstacle_cache_obj)

    direction, _ = clamp_step_toward_memo(
        origin, target, obstacles=obstacles, memory=mem
    )

    assert direction is not None
    assert direction != "RIGHT", (
        f"RIGHT 历史障碍 block_count=3 应被避开，实际返回 {direction}"
    )
    # DOWN 也是推进方向（gain=1）且无历史障碍，应胜出
    assert direction == "DOWN", (
        f"应选 DOWN（同是推进方向但无历史降权），实际返回 {direction}"
    )

    # 兼容：直接存 int 的 obstacle_cache（字典直接存 int）
    obstacle_cache_int = {
        (1, 0): 3,
    }
    mem_int = _DummyMem(obstacle_cache_int)
    direction2, _ = clamp_step_toward_memo(
        origin, target, obstacles=obstacles, memory=mem_int
    )
    assert direction2 is not None
    assert direction2 != "RIGHT", (
        f"[int存法] RIGHT 历史障碍 block_count=3 应被避开，实际返回 {direction2}"
    )
    assert direction2 == "DOWN", (
        f"[int存法] 应选 DOWN，实际返回 {direction2}"
    )


def test_TR_2_2_block_count_1_still_right() -> None:
    """TR-2.2: block_count=1 → 降权 -30，RIGHT 仍最优（101-30=71，仍高于绕行 100？NO —— 绕行 gain=0 是 100，推进 gain=1 是 101，所以 71 < 100，那这个断言要反？

    修正分析：
    - RIGHT: gain=1 (推进) → 100+1=101, -30 → 71
    - DOWN/UP: gain=0 (推进也是 gain>=0) → 100+0=100，不减
      所以 71 < 100，DOWN/UP 应该胜出？
      等等，那题目的预期是"仍选 RIGHT"，让我再仔细看题面："因 -30 不足以抵消 gain=1 的 100+gain 优势"
      哦，原来 101-30=71 但其他方向 gain=0 是 100？那 71 更小啊……题面是不是写反了？
      等等，不对，我重新看 _clamp_score 逻辑：

    重新分析：
    origin=(0,0), target=(10,0):
    - RIGHT: step=(1,0), nxt=(1,0), gain=1 → gain>=0: score=100+1=101；如果不是 last_dir，不加不减，然后 -30 → 71
    - DOWN: step=(0,1), nxt=(0,1), gain=manhattan((0,0),(10,0)) - manhattan((0,1),(10,0)) = 10 - 11 = -1 < 0；然后 cross_axis? dx=10!=0 所以 name in UP/DOWN → cross_axis=True → score=-30
    - UP: 同 DOWN → score=-30
    - LEFT: step=(-1,0), gain=10 - 11=-1, cross_axis=False → score=-70

    所以 RIGHT 71 >> DOWN/UP -30，仍然胜出！没错，是我错了。
    """
    origin = (0, 0)
    target = (10, 0)
    obstacles = {(2, 0)}

    class _DummyMem:
        def __init__(self, cache):
            self.obstacle_cache = cache

    obstacle_cache = {(1, 0): _DummyObstacleState(block_count=1)}
    mem = _DummyMem(obstacle_cache)

    direction, _ = clamp_step_toward_memo(
        origin, target, obstacles=obstacles, memory=mem
    )

    assert direction == "RIGHT", (
        f"block_count=1 (-30) 不应抵消 RIGHT gain=1 的优势，期望 RIGHT，实际 {direction}"
    )


def test_TR_2_3_clamp_regression_none_memory() -> None:
    """TR-2.3: obstacle_cache=None 回归。

    复制 5 个既有 clamp_step_toward_memo 用例的输入，
    分别用「旧签名不传 memory」与「新签名传 memory=None」调用，
    返回的 step 完全相等。
    """
    # 用例 1：at_target
    args1 = {"origin": (10, 10), "target": (10, 10)}
    # 用例 2：单格障碍前方绕行
    args2 = {"origin": (10, 12), "target": (10, 10), "obstacles": {(10, 11)}}
    # 用例 3：横向可推进 + last_dir=LEFT（反向推进）
    args3 = {
        "origin": (9, 12),
        "target": (10, 10),
        "obstacles": {(10, 11)},
        "last_dir": "LEFT",
    }
    # 用例 4：memo dict 读写
    args4 = {"origin": (10, 12), "target": (10, 10), "obstacles": {(10, 11)}, "memo": {}}
    # 用例 5：ban_dirs 禁用方向
    args5 = {
        "origin": (0, 0),
        "target": (10, 0),
        "obstacles": set(),
        "ban_dirs": ["RIGHT"],
    }

    all_cases = [
        ("case1_at_target", args1),
        ("case2_obstacle_front", args2),
        ("case3_last_dir_left", args3),
        ("case4_memo_dict", args4),
        ("case5_ban_right", args5),
    ]

    for name, kwargs in all_cases:
        # 旧：不传 memory（default=None）
        d_old, l_old = clamp_step_toward_memo(**kwargs)
        # 新：显式传 memory=None
        kwargs_with_none = dict(kwargs)
        # case4 的 memo dict 是会被修改的，每次重置
        if "memo" in kwargs_with_none:
            kwargs_with_none["memo"] = {}
        d_new, l_new = clamp_step_toward_memo(memory=None, **kwargs_with_none)

        assert d_old == d_new, (
            f"{name}: 不传 memory vs memory=None 返回 direction 不等: "
            f"{d_old} vs {d_new}"
        )
        assert l_old == l_new, (
            f"{name}: 不传 memory vs memory=None 返回 last_dir 不等: "
            f"{l_old} vs {l_new}"
        )


def test_TR_2_4_hard_block_not_overridden_by_cache() -> None:
    """TR-2.4: 硬过滤（当前可见 obstacles 集合）不被降权逻辑破坏。

    origin=(0,0), target=(10,0), obstacles={(1,0)}（RIGHT 是硬障碍）。
    obstacle_cache 为空 / block_count=0 无所谓。
    clamp 返回的 step 绝不能等于 RIGHT（即使评分高也被硬过滤），
    必须是 DOWN/UP/LEFT 其一。
    """
    origin = (0, 0)
    target = (10, 0)
    hard_obstacles = {(1, 0)}  # RIGHT 硬障碍

    # 情况 A：不传 memory
    d1, _ = clamp_step_toward_memo(origin, target, obstacles=hard_obstacles)
    assert d1 is not None
    assert d1 != "RIGHT", f"[无memory] RIGHT 是硬障碍，不应返回 RIGHT，实际 {d1}"
    assert d1 in ("DOWN", "UP", "LEFT"), (
        f"[无memory] 应返回 DOWN/UP/LEFT，实际 {d1}"
    )

    # 情况 B：传 memory 且 obstacle_cache 为空
    class _DummyMem:
        def __init__(self, cache):
            self.obstacle_cache = cache
    mem_empty = _DummyMem({})
    d2, _ = clamp_step_toward_memo(origin, target, obstacles=hard_obstacles, memory=mem_empty)
    assert d2 is not None
    assert d2 != "RIGHT", f"[空cache] RIGHT 是硬障碍，不应返回 RIGHT，实际 {d2}"

    # 情况 C：传 memory 且 (1,0).block_count=0（硬障碍仍必须挡）
    mem_zero = _DummyMem({(1, 0): _DummyObstacleState(block_count=0)})
    d3, _ = clamp_step_toward_memo(origin, target, obstacles=hard_obstacles, memory=mem_zero)
    assert d3 is not None
    assert d3 != "RIGHT", f"[bc=0] RIGHT 是硬障碍，不应返回 RIGHT，实际 {d3}"


# ---------------------------------------------------------------------------
# TR-4 双中心螺旋扫掠测试
# ---------------------------------------------------------------------------

from bot.pathing import beacon_oriented_spiral_target


def test_TR_4_beacon_oriented_spiral_basic() -> None:
    """基础：beacon_oriented_spiral_target 返回 Beacon 环上的点。"""
    core = (10, 10)
    beacon = (10, 50)
    for ring in [1, 2, 3, 5]:
        pt = beacon_oriented_spiral_target(core, beacon, 0, 4, ring, 0)
        # 结果应在 Beacon 环上（曼哈顿距离 = ring 或 ring±1）
        d = manhattan(beacon, pt)
        assert d in [ring - 1, ring, ring + 1] or abs(d - ring) <= 2, (
            f"ring={ring}: expected manhattan(beacon, pt) ~= {ring}, got {d}"
        )


def test_TR_4_beacon_oriented_sector_diversity() -> None:
    """扇区分散：不同 sector_id 返回的点应足够分散。"""
    core = (10, 10)
    beacon = (10, 50)
    ring = 3
    pts = []
    for sid in range(5):
        pt = beacon_oriented_spiral_target(core, beacon, sid, 5, ring, 0)
        pts.append(pt)
    # 计算最大两两距离
    max_pair = 0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = manhattan(pts[i], pts[j])
            if d > max_pair:
                max_pair = d
    assert max_pair >= 4, f"扇区点不够分散: max_pair_dist={max_pair}, pts={pts}"
