"""探索优化端到端短模拟（T05 验收）。

场景：2 个 Worker + Beacon 在 (50,10)（距 Core 40 曼哈顿）。
验证：
1. widx==0 Worker 进入 phase=beacon（dedicated_beacon）且 d_beacon 单调下降；
2. widx==1 Worker 留守 Core 周边（local 螺旋巡逻）；
3. 无重复 chunk（new_chunk 日志每条 chunk 只出现一次）；
4. Beacon 消失（CARRIED）后全部回 local。

不联网、不 submit，仅驱动 command_workers + 手动推进位置。
运行：.venv/Scripts/python.exe simulate_explore_beacon.py
"""

from __future__ import annotations

from bot.config import TacticConfig, set_beacon_position
from bot.economy import _last_move_dir, _spiral_state, command_workers
from bot.memory import MemoryMap
from bot.pathing import manhattan
from bot.roles import assign_roles
from tests.stubs import StubBeacon, StubCore, StubTurn, StubUnit

DIR_DELTA = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

CORE = (10, 10)
BEACON = (50, 10)

cfg = TacticConfig(
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
    recall_stall_ticks=6,
    spiral_base_ring=3,
    spiral_max_ring=32,
    sector_count=2,
)


def main() -> int:
    set_beacon_position(cfg, BEACON)
    _spiral_state.clear()
    _last_move_dir.clear()
    mem = MemoryMap()

    wa = StubUnit(position=CORE, cargo=0, unit_type="WORKER")  # widx==0 → dedicated
    wb = StubUnit(position=CORE, cargo=0, unit_type="WORKER")  # widx==1 → local
    turn = StubTurn(
        tick=1,
        resources=5,
        core=StubCore(position=CORE),
        workers=[wa, wb],
        resource_cells=set(),
        visible_enemies=[],
        beacon=StubBeacon(position=BEACON, status="GROUND", carrier_id=None),
    )
    plan = assign_roles(turn, config=cfg)

    all_logs: list[str] = []
    new_chunks: list[str] = []
    d_history: list[int] = [manhattan(wa.position, BEACON)]

    def tick(status: str = "GROUND") -> None:
        nonlocal d_history
        wa.clear_action()
        wb.clear_action()
        turn.beacon = StubBeacon(
            position=BEACON, status=status, carrier_id=None
        )
        logs = command_workers(turn, plan, config=cfg, memory=mem)
        all_logs.extend(logs)
        for w in (wa, wb):
            if w.action == "move":
                dx, dy = DIR_DELTA[str(w.action_args)]
                w.position = (w.position[0] + dx, w.position[1] + dy)
        d_history.append(manhattan(wa.position, BEACON))

    print("=== 阶段 1：Beacon GROUND (50,10)，推进 30 tick ===")
    for _ in range(30):
        tick()

    for line in all_logs:
        if "new_chunk=" in line:
            chunk = line.split("new_chunk=")[1]
            if chunk not in new_chunks:
                new_chunks.append(chunk)

    st_a = _spiral_state[str(wa.id)]
    st_b = _spiral_state[str(wb.id)]
    print(f"wa: phase={st_a.phase} dedicated={st_a.dedicated} pos={wa.position}")
    print(f"wb: phase={st_b.phase} dedicated={st_b.dedicated} pos={wb.position}")
    print(f"d_beacon 序列（wa，前 12 tick）: {d_history[:12]}")

    ok = True
    # 1) widx==0 进入 phase=beacon（dedicated）
    if not (
        st_a.dedicated and st_a.phase == "beacon"
        and any(":dedicated_beacon" in line for line in all_logs)
    ):
        print("FAIL  widx==0 未进入 dedicated beacon")
        ok = False
    # 2) d_beacon 单调下降（前 12 tick，无阻塞直线推进）
    mono = all(
        d_history[i] >= d_history[i + 1] for i in range(min(11, len(d_history) - 1))
    )
    if not mono:
        print(f"FAIL  d_beacon 未单调下降: {d_history[:12]}")
        ok = False
    # 3) local Worker 留守 Core 周边（d_core <= 20）
    d_core_wb = manhattan(wb.position, CORE)
    if not (st_b.phase == "local" and d_core_wb <= 20):
        print(f"FAIL  local Worker 未留守 Core 周边: phase={st_b.phase} d_core={d_core_wb}")
        ok = False
    # 4) 无重复 chunk
    if not new_chunks:
        print("FAIL  无 new_chunk 日志")
        ok = False
    if len(new_chunks) != len(set(new_chunks)):
        print(f"FAIL  重复 chunk: {new_chunks}")
        ok = False
    print(f"new_chunk 序列（无重复）: {new_chunks}")

    print("=== 阶段 2：Beacon 消失（CARRIED）→ 全回 local ===")
    set_beacon_position(cfg, None)
    for _ in range(3):
        tick(status="CARRIED")
    st_a = _spiral_state[str(wa.id)]
    st_b = _spiral_state[str(wb.id)]
    print(f"wa: phase={st_a.phase}  wb: phase={st_b.phase}")
    if not (st_a.phase == "local" and st_b.phase == "local"):
        print("FAIL  Beacon 消失后未全部回 local")
        ok = False

    print(f"\n=== 模拟结果: {'PASS' if ok else 'FAIL'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
