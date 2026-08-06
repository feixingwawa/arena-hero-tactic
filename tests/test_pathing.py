"""路径模块单测：带方向记忆的去抖寻路（防 A↔B 对抖）+ 螺旋扫掠几何。"""

from __future__ import annotations

from bot.pathing import (
    NAME_TO_DELTA,
    Position,
    add_pos,
    clamp_step_toward,
    clamp_step_toward_memo,
    manhattan,
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
