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

    # tick 6: 到期 → REVISIT_DUE，可作为回访候选
    mem.observe(_turn(6, set(), core=core), 6)
    rp = mem.resource_points[rp_pos]
    assert rp.state == REVISIT_DUE
    assert rp.is_revisit_due(6) is True
    assert mem.refresh_due(rp_pos, 6) is True
    cands = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
    assert rp_pos in cands

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
    """回访候选：距离截断 + 扇区过滤。"""
    mem = MemoryMap(refresh_interval_ticks=4, sector_count=4)
    core = (10, 10)
    far = (60, 10)  # 距 worker (10,10) = 50 > 40
    near_sector0 = (6, 9)  # ring 5 扇区 0（与 pathing.sector_points 一致）
    near_sector1 = (7, 8)  # ring 5 扇区 1
    mem.observe(_turn(1, {far, near_sector0, near_sector1}, core=core), 1)
    mem.observe(_turn(2, set(), core=core), 2)  # 全部消失 → DEPLETED
    mem.observe(_turn(6, set(), core=core), 6)  # 到期 → REVISIT_DUE

    all_c = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
    assert near_sector0 in all_c
    assert near_sector1 in all_c
    assert far not in all_c  # 距离截断

    s0 = mem.revisit_candidates(core, 6, (10, 10), max_dist=40, sector_id=0)
    s1 = mem.revisit_candidates(core, 6, (10, 10), max_dist=40, sector_id=1)
    assert near_sector0 in s0 and near_sector0 not in s1
    assert near_sector1 in s1 and near_sector1 not in s0


def test_revisit_candidates_sorted_by_distance() -> None:
    """回访候选按距离排序（确定性）。"""
    mem = MemoryMap(refresh_interval_ticks=4)
    core = (10, 10)
    a = (20, 10)  # dist 10
    b = (14, 10)  # dist 4
    mem.observe(_turn(1, {a, b}, core=core), 1)
    mem.observe(_turn(2, set(), core=core), 2)
    mem.observe(_turn(6, set(), core=core), 6)
    cands = mem.revisit_candidates(core, 6, (10, 10), max_dist=40)
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
    assert chunk_of((31, 31)) == (0, 0)
    assert chunk_of((32, 32)) == (1, 1)
    assert chunk_of((-1, 63)) == (-1, 1)
    center = chunk_of((10, 10))
    assert chunk_ring(chunk_of((10, 10)), center) == 0
    assert chunk_ring(chunk_of((74, 10)), center) == 2  # (2,0) 相对 (0,0)


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
