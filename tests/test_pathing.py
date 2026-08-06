"""路径模块单测：带方向记忆的去抖寻路（防 A↔B 对抖）。"""

from __future__ import annotations

from bot.pathing import (
    NAME_TO_DELTA,
    Position,
    add_pos,
    clamp_step_toward,
    clamp_step_toward_memo,
    manhattan,
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
