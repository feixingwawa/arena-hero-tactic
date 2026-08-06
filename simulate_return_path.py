"""同列障碍绕行 短模拟脚本（验证去抖寻路）。

场景：Worker 在 Core(10,10) 正下方 3 格 (10,12)，中间 (10,11) 是障碍。
旧 clamp_step_toward 会 return_deposit:DOWN↔UP 无限对抖（res 卡死）；
带方向记忆的去抖寻路应绕行（LEFT→UP→UP→RIGHT）成功回到 Core。

用法：.venv/Scripts/python.exe simulate_return_path.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from bot.config import TacticConfig
from bot.economy import command_workers
from bot.pathing import manhattan
from bot.roles import assign_roles
from tests.stubs import StubCore, StubTurn, StubUnit

# 清空方向记忆模块级状态，保证模拟从头开始
import bot.economy as economy

for d in (
    economy._last_explore_pos,
    economy._prev_explore_pos,
    economy._explore_phase,
    economy._explore_ticks,
    economy._explore_axis,
    economy._last_explore_dir,
    economy._last_move_dir,
):
    d.clear()

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

CORE = (10, 10)
DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def main() -> None:
    pos = (10, 12)
    start_d = manhattan(pos, CORE)
    # 同一 Worker 跨 tick 复用（真实环境中单位 id 稳定，方向记忆按 id 持续生效）
    worker = StubUnit(position=pos, cargo=1, unit_type="WORKER")
    print("同列障碍绕行模拟：Core=(10,10)，障碍=(10,11)，Worker 从 (10,12) 回城")
    print("tick  pos        d(core)  dir   log")
    print("----  ---------  -------  ----  --------------------------------")
    dirs: list[str] = []
    for tick in range(1, 9):
        turn = StubTurn(
            tick=tick,
            resources=5,
            core=StubCore(position=CORE),
            workers=[worker],
            resource_cells=set(),
            obstacle_cells={(10, 11)},
            visible_enemies=[],
        )
        plan = assign_roles(turn, config=config)
        worker.clear_action()
        logs = command_workers(turn, plan, config=config)
        if worker.action != "move":
            print(
                f"{tick:>4}  {pos}  {manhattan(pos, CORE):>7}  {'--':<5} "
                f"{logs[0] if logs else ''}"
            )
            print(f"\n第 {tick} tick 到达 Core（{worker.action}）✅")
            break
        direction = str(worker.action_args)
        dirs.append(direction)
        dx, dy = DELTA[direction]
        nxt = (pos[0] + dx, pos[1] + dy)
        d_before = manhattan(pos, CORE)
        d_after = manhattan(nxt, CORE)
        print(
            f"{tick:>4}  {pos}  {d_before:>7}  {direction:<5} {logs[0] if logs else ''}"
        )
        pos = nxt
        worker.position = pos  # 推进 worker 位置，模拟真实 tick 移动
        if pos == CORE:
            print(f"\n第 {tick} tick 到达 Core ✅")
            break

    # 对抖检测：紧接反向连续不超过 2 次
    alt_max = 0
    run = 0
    for a, b in zip(dirs, dirs[1:]):
        if DELTA[b] == tuple(-v for v in DELTA[a]):
            run += 1
            alt_max = max(alt_max, run)
        else:
            run = 0

    print("\n方向序列:", " -> ".join(dirs))
    print(f"最终 pos: {pos}  d(core)={manhattan(pos, CORE)} (起点 d={start_d})")
    print(f"紧接反向对抖最大连续次数: {alt_max}")
    ok = alt_max <= 2 and manhattan(pos, CORE) < start_d
    print("结论:", "✅ 无横跳，成功绕行靠近 Core" if ok else "❌ 仍存在对抖/未靠近")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
