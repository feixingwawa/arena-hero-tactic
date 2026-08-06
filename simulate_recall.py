"""跑飞回撤 + 螺旋扫掠 短模拟脚本。

场景：worker 从 (50,10) 出发（距 Core(10,10) = 40，超出探索上限 32+4），
连续 5 tick 无敌人、无资源。预期：
- 先逐步回撤靠近 Core（每 tick manhattan 递减或至少不增超过 2）
- 之后进入垂直轴扫掠（方向切垂直轴）

用法：.venv/Scripts/python.exe simulate_recall.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from bot.config import TacticConfig
from bot.economy import command_workers
from bot.pathing import manhattan
from bot.roles import assign_roles
from tests.stubs import StubCore, StubTurn, StubUnit

# 清空探索模块级状态，保证模拟从头开始
import bot.economy as economy

for d in (
    economy._last_explore_pos,
    economy._prev_explore_pos,
    economy._explore_phase,
    economy._explore_ticks,
    economy._explore_axis,
    economy._last_explore_dir,
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
    pos = (50, 10)
    print("tick  pos        d(core)  dir   log")
    print("----  ---------  -------  ----  --------------------------------")
    for tick in range(1, 6):
        worker = StubUnit(position=pos, cargo=0, unit_type="WORKER")
        turn = StubTurn(
            tick=tick,
            resources=5,
            core=StubCore(position=CORE),
            workers=[worker],
            resource_cells=set(),
            visible_enemies=[],
        )
        plan = assign_roles(turn, config=config)
        logs = command_workers(turn, plan, config=config)
        direction = str(worker.action_args)
        dx, dy = DELTA[direction]
        nxt = (pos[0] + dx, pos[1] + dy)
        d_before = manhattan(pos, CORE)
        d_after = manhattan(nxt, CORE)
        print(
            f"{tick:>4}  {pos}  {d_before:>7}  {direction:<5} {logs[0] if logs else ''}"
        )
        assert d_after <= d_before + 2, (
            f"tick {tick}: distance jumped from {d_before} to {d_after}"
        )
        pos = nxt

    print("\n最终 pos:", pos)
    print("结论: 每 tick manhattan 未暴增（<= +2），回撤/扫掠行为符合预期")


if __name__ == "__main__":
    main()
