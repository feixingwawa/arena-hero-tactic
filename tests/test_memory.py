"""地图记忆模块单测：资源点状态机 / 障碍累积 / 回访候选 / cargo 事件。"""

from __future__ import annotations

from bot.memory import (
    DEPLETED,
    REVISIT_DUE,
    VISIBLE,
    MemoryMap,
)
from bot.pathing import chunk_of, chunk_ring
from tests.stubs import StubCore, StubEvent, StubTurn, StubUnit


def _turn(tick: int, cells: set, core=(10, 10), events=None, obstacles=None) -> StubTurn:
    return StubTurn(
        tick=tick,
        core=StubCore(position=core),
        resource_cells=set(cells),
        obstacle_cells=set(obstacles or ()),
        events=events or [],
        workers=[StubUnit(position=core)],
    )


def test_resource_state_machine_visible_depleted_revisit() -> None:
    """状态机：可见 → 消失(DEPLETED) → 4 tick 后 REVISIT_DUE → 再可见(VISIBLE)。"""
    mem = MemoryMap(refresh_interval_ticks=4)
    core = (10, 10)
    rp_pos = (14, 10)

    # tick 1: 可见 → VISIBLE
    mem.observe(_turn(1, {rp_pos}, core=core), 1)
    rp = mem.resource_points[rp_pos]
    assert rp.state == VISIBLE

    # tick 2: 消失 → DEPLETED, refresh_due = 2 + 4 = 6
    mem.observe(_turn(2, set(), core=core), 2)
    rp = mem.resource_points[rp_pos]
    assert rp.state == DEPLETED
    assert rp.depleted_tick == 2
    assert rp.refresh_due_tick == 6
    assert mem.refresh_due(rp_pos, 2) is False

    # tick 5: 仍未到期 → DEPLETED
    mem.observe(_turn(5, set(), core=core), 5)
    assert mem.resource_points[rp_pos].state == DEPLETED
    assert mem.refresh_due(rp_pos, 5) is False

    # tick 6: 到期 → REVISIT_DUE，「刷新提示」语义
    mem.observe(_turn(6, set(), core=core), 6)
    rp = mem.resource_points[rp_pos]
    assert rp.state == REVISIT_DUE
    assert rp.is_revisit_due(6) is True
    assert mem.refresh_due(rp_pos, 6) is True
    # REVISIT_DUE 点不被 revisit_candidates 返回（不可作为采集目标）
    cands = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
    assert rp_pos not in cands, "REVISIT_DUE point should not be returned as harvest target"

    # tick 7: 再次可见 → VISIBLE
    mem.observe(_turn(7, {rp_pos}, core=core), 7)
    assert mem.resource_points[rp_pos].state == VISIBLE
    assert mem.refresh_due(rp_pos, 7) is True


def test_mark_harvested_triggers_depleted() -> None:
    """mark_harvested：采集成功后标记 DEPLETED 并安排刷新。"""
    mem = MemoryMap(refresh_interval_ticks=4)
    pos = (20, 20)
    mem.observe(_turn(1, {pos}), 1)
    mem.mark_harvested(pos, 3)
    rp = mem.resource_points[pos]
    assert rp.state == DEPLETED
    assert rp.refresh_due_tick == 3 + 4
    # 未到期不可回访
    assert pos not in mem.revisit_candidates((10, 10), 5, (10, 10), max_dist=40)


def test_obstacles_accumulate_permanently() -> None:
    """障碍永久累积：多次 observe 后仍保留，is_obstacle 命中。"""
    mem = MemoryMap()
    mem.observe(_turn(1, set(), obstacles={(30, 30), (31, 30)}), 1)
    mem.observe(_turn(2, set(), obstacles={(40, 40)}), 2)
    assert mem.is_obstacle((30, 30))
    assert mem.is_obstacle((31, 30))
    assert mem.is_obstacle((40, 40))
    assert not mem.is_obstacle((0, 0))


def test_revisit_candidates_max_dist_and_sector_filter() -> None:
    """回访候选：仅 VISIBLE 点被返回；距离截断 + 扇区过滤。"""
    mem = MemoryMap(refresh_interval_ticks=4, sector_count=4)
    core = (10, 10)
    far = (60, 10)  # 距 worker (10,10) = 50 > 40
    near_sector0 = (6, 9)  # ring 5 扇区 0（与 pathing.sector_points 一致）
    near_sector1 = (7, 8)  # ring 5 扇区 1
    # 先 visible → 消耗 → REVISIT_DUE 到期
    mem.observe(_turn(1, {far, near_sector0, near_sector1}, core=core), 1)
    mem.observe(_turn(2, set(), core=core), 2)  # 全部消失 → DEPLETED
    mem.observe(_turn(6, set(), core=core), 6)  # 到期 → REVISIT_DUE

    # REVISIT_DUE 点不被返回（非 VISIBLE）
    all_c = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
    assert near_sector0 not in all_c
    assert near_sector1 not in all_c
    assert far not in all_c

    # VISIBLE 点应被返回
    mem.observe(_turn(7, {near_sector0, near_sector1}, core=core), 7)  # 恢复 VISIBLE
    all_c = mem.revisit_candidates(core, 7, (10, 10), max_dist=40)
    assert near_sector0 in all_c
    assert near_sector1 in all_c

    s0 = mem.revisit_candidates(core, 7, (10, 10), max_dist=40, sector_id=0)
    s1 = mem.revisit_candidates(core, 7, (10, 10), max_dist=40, sector_id=1)
    assert near_sector0 in s0 and near_sector0 not in s1
    assert near_sector1 in s1 and near_sector1 not in s0


def test_revisit_candidates_sorted_by_distance() -> None:
    """回访候选按距离排序（仅 VISIBLE 点）。"""
    mem = MemoryMap(refresh_interval_ticks=4)
    core = (10, 10)
    a = (20, 10)  # dist 10
    b = (14, 10)  # dist 4
    mem.observe(_turn(1, {a, b}, core=core), 1)
    mem.observe(_turn(2, set(), core=core), 2)
    mem.observe(_turn(6, set(), core=core), 6)
    # tick 6 时都是 REVISIT_DUE（不返回）
    cands = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
    assert cands == []
    # tick 7 重新可见后返回 VISIBLE 点，按距离排序
    mem.observe(_turn(7, {a, b}, core=core), 7)
    cands = mem.revisit_candidates(core, 7, (10, 10), max_dist=40)
    assert cands == [b, a], f"expected sorted by distance: {cands}"


def test_dropped_cargo_event_ingested() -> None:
    """WORKER_CARGO_DROPPED 事件 → dropped_cargo 入库。"""
    mem = MemoryMap()
    events = [
        StubEvent(
            event_type="WORKER_CARGO_DROPPED",
            position=(25, 25),
            values={"amount": 3},
        )
    ]
    mem.observe(_turn(1, set(), events=events), 1)
    assert (25, 25) in mem.dropped_cargo
    cargo = mem.dropped_cargo[(25, 25)]
    assert cargo.amount == 3
    assert cargo.collected is False
    # 回收后软删除
    mem.mark_cargo_collected((25, 25))
    assert mem.dropped_cargo[(25, 25)].collected is True


def test_dropped_cargo_ignores_other_events() -> None:
    """非掉落事件不入库。"""
    mem = MemoryMap()
    events = [
        StubEvent(event_type="HARVEST_SUCCEEDED", position=(25, 25), values={"amount": 1})
    ]
    mem.observe(_turn(1, set(), events=events), 1)
    assert mem.dropped_cargo == {}


def test_chunk_helpers() -> None:
    """chunk_of / chunk_ring。"""
    assert chunk_of((0, 0)) == (0, 0)
    assert chunk_of((15, 15)) == (0, 0)
    assert chunk_of((16, 16)) == (1, 1)
    assert chunk_of((-1, 63)) == (-1, 3)
    center = chunk_of((10, 10))
    assert chunk_ring(chunk_of((10, 10)), center) == 0
    assert chunk_ring(chunk_of((74, 10)), center) == 4  # (4,0) 相对 (0,0)


def test_chunk_quota_cache() -> None:
    """MemoryMap.chunk_quota 与 rules.chunk_quota 一致并缓存。"""
    from bot.rules import chunk_quota as rules_quota

    mem = MemoryMap()
    mem.center_chunk = (0, 0)
    assert mem.chunk_quota((0, 0)) == rules_quota(0)
    assert mem.chunk_quota((8, 0)) == rules_quota(8)
    assert len(mem._chunk_quota_cache) == 2


def test_point_sector_matches_pathing() -> None:
    """point_sector 与 pathing.sector_points 同一套规则。"""
    from bot.pathing import sector_points

    mem = MemoryMap(sector_count=4)
    core = (10, 10)
    for r in (5, 8):
        for p in sector_points(core, r, 2, 4):
            assert mem.point_sector(core, p) == 2, f"{p} should be sector 2"


def test_mark_explored_new_chunk_once() -> None:
    """mark_explored：新 chunk 返回 True 并记录首次 tick；重复返回 False。"""
    mem = MemoryMap()
    assert mem.mark_explored((10, 10), 1) is True   # chunk (0,0)
    assert mem.mark_explored((15, 12), 2) is False  # 同 chunk (0,0)
    assert mem.mark_explored((20, 20), 3) is True   # chunk (1,1) (20//16=1)
    assert mem.explored_chunk_ticks[(0, 0)] == 1
    assert mem.explored_chunk_ticks[(1, 1)] == 3
    assert mem.is_explored((0, 0)) is True
    assert mem.is_explored((2, 2)) is False
    assert mem.explored_chunks == {(0, 0), (1, 1)}


def test_record_obstacle_block_accumulates() -> None:
    """record_obstacle_block：累计 block_count，记录首次/最近时间戳。"""
    mem = MemoryMap()
    mem.record_obstacle_block((30, 30), 5)
    ost = mem.obstacle_cache[(30, 30)]
    assert ost.first_seen_tick == 5
    assert ost.last_seen_tick == 5
    assert ost.block_count == 1
    mem.record_obstacle_block((30, 30), 8)
    ost = mem.obstacle_cache[(30, 30)]
    assert ost.last_seen_tick == 8
    assert ost.block_count == 2


def test_observe_updates_obstacle_cache_timestamps() -> None:
    """observe：可见障碍更新时间戳（保留 block_count），obstacles set API 不变。"""
    mem = MemoryMap()
    mem.observe(_turn(1, set(), obstacles={(30, 30)}), 1)
    assert (30, 30) in mem.obstacles
    ost = mem.obstacle_cache[(30, 30)]
    assert ost.first_seen_tick == 1
    assert ost.last_seen_tick == 1
    assert ost.block_count == 0

    mem.observe(_turn(2, set(), obstacles={(30, 30), (40, 40)}), 2)
    assert mem.obstacle_cache[(30, 30)].last_seen_tick == 2
    assert mem.obstacle_cache[(30, 30)].block_count == 0
    assert mem.obstacle_cache[(40, 40)].first_seen_tick == 2
    assert mem.obstacle_cache[(40, 40)].last_seen_tick == 2
    assert mem.obstacles == {(30, 30), (40, 40)}


def test_TR_5_3_observe_refresh_chunk_last_seen_ticks() -> None:
    """TR-5.3 observe() 刷新 chunk_last_seen_ticks：Worker 位置对应 chunk tick=42。"""
    from tests.stubs import StubTurn, StubUnit, StubCore

    worker = StubUnit(position=(0, 0))
    turn = StubTurn(
        tick=42,
        core=StubCore(position=(10, 10)),
        workers=[worker],
    )
    mm = MemoryMap()
    mm.observe(turn, 42)
    assert (0, 0) in mm.chunk_last_seen_ticks
    assert mm.chunk_last_seen_ticks[(0, 0)] == 42


def test_TR_5_4_fresh_instance_chunk_last_seen_empty() -> None:
    """TR-5.4 清理生效：新 MemoryMap 实例 chunk_last_seen_ticks 为空。"""
    mm1 = MemoryMap()
    mm1.mark_chunk_seen((0, 0), 10)
    assert mm1.chunk_last_seen_ticks == {(0, 0): 10}
    mm2 = MemoryMap()
    assert mm2.chunk_last_seen_ticks == {}
    mm3 = MemoryMap()
    assert mm3.chunk_last_seen_ticks == {}


def test_vision_disk_size() -> None:
    """曼哈顿菱形：r=0→1，r=1→5，r=3→25，r=5→61。"""
    from bot.memory import vision_disk

    assert len(vision_disk((0, 0), 0)) == 1
    assert len(vision_disk((10, 10), 1)) == 5
    assert len(vision_disk((10, 10), 3)) == 25
    assert len(vision_disk((10, 10), 5)) == 61
    # 全部点曼哈顿 ≤ r
    for p in vision_disk((10, 10), 3):
        assert abs(p[0] - 10) + abs(p[1] - 10) <= 3


def test_has_line_of_sight_blocked_by_obstacle() -> None:
    """中间障碍挡视线；目标墙格本身可见。"""
    from bot.memory import has_line_of_sight

    origin = (10, 10)
    target = (14, 10)
    # 无障碍
    assert has_line_of_sight(origin, target, set()) is True
    # 中间墙 (12,10) 挡住 (14,10)
    assert has_line_of_sight(origin, target, {(12, 10)}) is False
    # 目标本身是墙：仍可见（看到墙面）
    assert has_line_of_sight(origin, (12, 10), {(12, 10)}) is True


def test_observe_marks_vision_disk_as_explored() -> None:
    """observe：Core 视距 5 + Worker 视距 3 → 可见格全部进入 explored_cells。

    官方：能看见的格子 = 已探索区域（非仅落脚点）。
    """
    from bot.memory import MemoryMap, VISION_RADIUS, vision_disk
    from tests.stubs import StubTurn, StubCore, StubUnit

    core_pos = (10, 10)
    worker_pos = (20, 10)  # 与 Core 分开，Worker 盘不与 Core 完全重叠
    mem = MemoryMap()
    turn = StubTurn(
        tick=1,
        core=StubCore(position=core_pos),
        workers=[StubUnit(position=worker_pos)],
        obstacle_cells=set(),
    )
    mem.observe(turn, 1)

    # Core r=5 全盘应已探
    for cell in vision_disk(core_pos, VISION_RADIUS["CORE"]):
        assert cell in mem.explored_cells, f"Core FOV missing {cell}"
    # Worker r=3 全盘应已探
    for cell in vision_disk(worker_pos, VISION_RADIUS["WORKER"]):
        assert cell in mem.explored_cells, f"Worker FOV missing {cell}"
    # 落脚点外的格也在（证明不是 footprint-only）
    assert (13, 10) in mem.explored_cells  # Core 东 3 格
    assert (20, 13) in mem.explored_cells  # Worker 北 3 格
    # 超出视野不应标记
    assert (10, 16) not in mem.explored_cells  # Core 南 6 > 5
    assert (24, 10) not in mem.explored_cells  # Worker 东 4 > 3


def test_observe_vision_respects_obstacle_los() -> None:
    """障碍挡视线：墙后格子不标已探；墙格本身标已探。"""
    from bot.memory import MemoryMap
    from tests.stubs import StubTurn, StubCore, StubUnit

    core_pos = (10, 10)
    wall = (12, 10)
    behind = (14, 10)
    mem = MemoryMap()
    turn = StubTurn(
        tick=1,
        core=StubCore(position=core_pos),
        workers=[],  # 仅 Core 视野，避免 Worker 干扰
        obstacle_cells={wall},
    )
    mem.observe(turn, 1)
    assert wall in mem.explored_cells, "wall cell itself should be visible"
    assert behind not in mem.explored_cells, "cell behind wall should not be explored"
    assert (11, 10) in mem.explored_cells


def test_mark_vision_disk_vanguard_ranger_radii() -> None:
    """Vanguard r=4 / Ranger r=5 写入正确。"""
    from bot.memory import MemoryMap, VISION_RADIUS, vision_disk
    from tests.stubs import StubTurn, StubCore, StubUnit

    mem = MemoryMap()
    vpos, rpos = (0, 0), (30, 0)
    turn = StubTurn(
        tick=5,
        core=StubCore(position=(100, 100)),  # 远离，不污染
        workers=[],
        vanguards=[StubUnit(position=vpos, unit_type="VANGUARD")],
        rangers=[StubUnit(position=rpos, unit_type="RANGER")],
        obstacle_cells=set(),
    )
    mem.observe(turn, 5)
    for cell in vision_disk(vpos, VISION_RADIUS["VANGUARD"]):
        assert cell in mem.explored_cells
    for cell in vision_disk(rpos, VISION_RADIUS["RANGER"]):
        assert cell in mem.explored_cells
    # Vanguard 不覆盖 r=5 外点
    assert (0, 5) not in mem.explored_cells
    assert (30, 5) in mem.explored_cells  # Ranger r=5
