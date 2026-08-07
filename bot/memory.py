"""地图记忆模块：资源点状态机 / 障碍缓存 / 掉落 cargo（P1-1 / P2-2）。

单局进程内状态，跨 tick 累积。设计约定：
- 资源点状态机：`VISIBLE →（从 resource_cells 消失）→ DEPLETED →
  （tick >= depleted_tick + refresh_interval_ticks）→ REVISIT_DUE →（再次可见）→ VISIBLE`。
- SDK 每 tick 的 `resource_cells` 只含**当前可见** RESOURCE 格；某格从可见集合
  消失且记忆为 VISIBLE → 判定已消耗（depleted_tick = tick）。
- 回补节拍：`refresh_interval_ticks`（默认 4，近似「每 4 resolved tick」）。
- **REVISIT_DUE 仅是「该 chunk 可能已刷新」的信息提示，不是可采集目标**：
  官方规则回补可能发生在**新位置**（确定性随机选槽），旧格位置在重新可见之前
  不能作为 harvest 目标。`revisit_candidates` 只返回 VISIBLE（当前确认存在）点。
- 障碍永久累积（地形）；掉落 cargo 来自 `WORKER_CARGO_DROPPED` 事件。
- 模块级 `WORLD_MEMORY` 单例供线上 `decide()` 默认使用；测试注入新实例。

CRUD 说明：`observe` 负责 Create/Update（资源/障碍/掉落）；`mark_harvested`
负责状态迁移（Update）；`revisit_candidates` 负责 Read；掉落仅软删除
（`DroppedCargoState.collected = True`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from bot.pathing import (
    Position,
    chunk_of,
    chunk_ring,
    manhattan,
    ring_points,
)
from bot.roles import _as_position

# 资源点状态常量
VISIBLE = "VISIBLE"
DEPLETED = "DEPLETED"
REVISIT_DUE = "REVISIT_DUE"

# 掉落 cargo 事件类型（SDK ResolutionEvent.event_type）
CARGO_DROPPED_EVENT = "WORKER_CARGO_DROPPED"


@dataclass
class ResourcePointState:
    """单个资源点的记忆状态（状态机见模块 docstring）。"""

    pos: Position
    state: str = VISIBLE
    seen_tick: int = 0
    depleted_tick: int = 0
    refresh_due_tick: int = 0
    chunk_ring: int = 0

    def mark_visible(self, tick: int) -> None:
        self.state = VISIBLE
        self.seen_tick = tick

    def mark_depleted(self, tick: int, interval: int) -> None:
        if self.state == DEPLETED:
            return
        self.state = DEPLETED
        self.depleted_tick = tick
        self.refresh_due_tick = tick + interval

    def is_revisit_due(self, tick: int) -> bool:
        return self.state == REVISIT_DUE and tick >= self.refresh_due_tick


@dataclass
class DroppedCargoState:
    """死亡掉落 cargo 的记忆状态。"""

    pos: Position
    amount: int
    drop_tick: int
    collected: bool = False


class MemoryMap:
    """进程内单局地图记忆。

    Attributes:
        refresh_interval_ticks: 资源点刷新回补节拍。
        revisit_max_distance: 回访候选最大曼哈顿距离。
        sector_count: 扇区数（回访候选按 worker 扇区过滤时使用）。
        resource_points: pos -> ResourcePointState。
        obstacles: 永久障碍集合（地形）。
        dropped_cargo: pos -> DroppedCargoState。
    """

    def __init__(
        self,
        refresh_interval_ticks: int = 4,
        revisit_max_distance: int = 40,
        sector_count: int = 4,
    ) -> None:
        self.refresh_interval_ticks = max(1, int(refresh_interval_ticks))
        self.revisit_max_distance = int(revisit_max_distance)
        self.sector_count = max(1, int(sector_count))
        self.resource_points: dict[Position, ResourcePointState] = {}
        self.obstacles: set[Position] = set()
        self.dropped_cargo: dict[Position, DroppedCargoState] = {}
        self._chunk_quota_cache: dict[int, int] = {}
        self.center_chunk: tuple[int, int] = (0, 0)

    # ---- Create / Update ----

    def observe(self, turn: Any, tick: int) -> None:
        """从 turn 更新记忆：资源可见性 / 障碍累积 / 掉落 cargo 事件。"""
        # Core 位置 → center chunk（回访 ring 基准）
        core = getattr(turn, "core", None)
        if core is not None:
            try:
                self.center_chunk = chunk_of(_as_position(core.position))
            except Exception:
                pass

        visible: set[Position] = set()
        cells = getattr(turn, "resource_cells", None)
        if cells is not None:
            for c in cells:
                try:
                    visible.add(_as_position(c))
                except Exception:
                    pass

        # 障碍永久累积
        obs = getattr(turn, "obstacle_cells", None)
        if obs is not None:
            for o in obs:
                try:
                    self.obstacles.add(_as_position(o))
                except Exception:
                    pass

        # 状态迁移：VISIBLE 且当前不可见 → DEPLETED；DEPLETED 到期 → REVISIT_DUE
        for pos, rp in list(self.resource_points.items()):
            if pos in visible:
                rp.mark_visible(tick)
            elif rp.state == VISIBLE:
                rp.mark_depleted(tick, self.refresh_interval_ticks)
            elif (
                rp.state == DEPLETED
                and tick >= rp.refresh_due_tick
            ):
                rp.state = REVISIT_DUE

        # 新可见资源点入库
        for pos in visible:
            if pos not in self.resource_points:
                self.resource_points[pos] = ResourcePointState(
                    pos=pos,
                    state=VISIBLE,
                    seen_tick=tick,
                    chunk_ring=self._chunk_ring_of(pos),
                )

        # 掉落 cargo 事件
        events = getattr(turn, "events", None)
        if events is not None:
            for ev in events:
                et = getattr(ev, "event_type", None)
                if et != CARGO_DROPPED_EVENT:
                    continue
                epos = getattr(ev, "position", None)
                if epos is None:
                    continue
                try:
                    p = _as_position(epos)
                except Exception:
                    continue
                amount = 0
                values = getattr(ev, "values", None)
                if isinstance(values, dict):
                    try:
                        amount = int(values.get("amount", 0) or 0)
                    except (TypeError, ValueError):
                        amount = 0
                self.remember_dropped_cargo(p, amount, tick)

    def mark_harvested(self, pos: Position, tick: int) -> None:
        """采集成功后标记已消耗（进入 DEPLETED，安排刷新回访）。"""
        p = _as_position(pos)
        rp = self.resource_points.get(p)
        if rp is None:
            rp = ResourcePointState(
                pos=p,
                seen_tick=tick,
                chunk_ring=self._chunk_ring_of(p),
            )
            self.resource_points[p] = rp
        rp.mark_depleted(tick, self.refresh_interval_ticks)

    def remember_dropped_cargo(self, pos: Position, amount: int, tick: int) -> None:
        """记录掉落 cargo（重复事件则累加金额，不覆盖）。"""
        p = _as_position(pos)
        existing = self.dropped_cargo.get(p)
        if existing is not None and not existing.collected:
            existing.amount = max(existing.amount, int(amount))
            return
        self.dropped_cargo[p] = DroppedCargoState(
            pos=p,
            amount=int(amount),
            drop_tick=int(tick),
            collected=False,
        )

    def mark_cargo_collected(self, pos: Position) -> None:
        """cargo 回收后软删除（collected=True）。"""
        p = _as_position(pos)
        cargo = self.dropped_cargo.get(p)
        if cargo is not None:
            cargo.collected = True

    # ---- Read ----

    def refresh_due(self, pos: Position, tick: int) -> bool:
        """该资源点是否「值得回访检查」：VISIBLE（当前可见）或 REVISIT_DUE（刷新到期）。

        注意：返回 True 不代表可采集——REVISIT_DUE 只是「chunk 可能已刷新」的提示，
        旧格位置在重新可见前不可 harvest；采集目标以 `revisit_candidates`（仅 VISIBLE）为准。
        """
        rp = self.resource_points.get(_as_position(pos))
        if rp is None:
            return False
        if rp.state == VISIBLE:
            return True
        return rp.is_revisit_due(tick)

    def revisit_candidates(
        self,
        core: Position,
        tick: int,
        worker_pos: Position,
        max_dist: Optional[int] = None,
        sector_id: Optional[int] = None,
    ) -> list[Position]:
        """返回**可采集**的资源点候选（仅 VISIBLE = 当前确认存在的资源格）。

        REVISIT_DUE（DEPLETED 到期）点**不返回**：官方规则下资源回补可能落在
        新位置，旧格位置在重新可见之前不能当 harvest 目标；发现刷新资源靠螺旋
        扫掠重新进入视野后由 `observe` 恢复 VISIBLE。

        Args:
            core: Core 位置（扇区/环基准）。
            tick: 当前 tick。
            worker_pos: Worker 位置（距离截断基准）。
            max_dist: 最大曼哈顿距离；None 用 self.revisit_max_distance。
            sector_id: 若给定，仅返回属于该扇区的点（多 Worker 分工）。

        Returns:
            候选点列表（确定性排序：先距离、后坐标）。
        """
        limit = self.revisit_max_distance if max_dist is None else int(max_dist)
        result: list[Position] = []
        for pos, rp in self.resource_points.items():
            if rp.state != VISIBLE:
                continue
            if manhattan(worker_pos, pos) > limit:
                continue
            if sector_id is not None and self.point_sector(core, pos) != sector_id:
                continue
            result.append(pos)
        result.sort(key=lambda p: (manhattan(worker_pos, p), p[0], p[1]))
        return result

    def is_obstacle(self, pos: Position) -> bool:
        """该格是否为已知障碍。"""
        return _as_position(pos) in self.obstacles

    # ---- 几何辅助 ----

    def chunk_of(self, pos: Position) -> tuple[int, int]:
        return chunk_of(_as_position(pos))

    def chunk_quota(self, chunk: tuple[int, int]) -> int:
        """返回 chunk 的刷新配额（软约束，防扎堆；按 ring 缓存）。"""
        from bot.rules import chunk_quota as _quota

        ring = chunk_ring(
            (int(chunk[0]), int(chunk[1])),
            (int(self.center_chunk[0]), int(self.center_chunk[1])),
        )
        if ring not in self._chunk_quota_cache:
            self._chunk_quota_cache[ring] = _quota(ring)
        return self._chunk_quota_cache[ring]

    def point_sector(
        self,
        core: Position,
        pos: Position,
        sector_count: Optional[int] = None,
        phase_offset: int = 0,
    ) -> int:
        """返回资源点相对 Core 所属扇区（与 pathing.sector_points 同一套规则）。"""
        sc = self.sector_count if sector_count is None else max(1, int(sector_count))
        c = _as_position(core)
        p = _as_position(pos)
        r = manhattan(c, p)
        if r <= 0:
            return 0
        pts = ring_points(c, r)
        try:
            idx = pts.index(p)
        except ValueError:
            return 0
        return (idx + phase_offset) % sc

    def _chunk_ring_of(self, pos: Position) -> int:
        return chunk_ring(chunk_of(_as_position(pos)), self.center_chunk)


# 线上默认单例：decide(turn, config, memory=None) 时使用
WORLD_MEMORY: MemoryMap = MemoryMap()
