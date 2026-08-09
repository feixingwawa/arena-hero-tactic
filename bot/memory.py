"""地图记忆模块：资源点状态机 / 障碍缓存 / 掉落 cargo / 视野已探（P1-1 / P2-2）。

跨 tick 累积；可选落盘跨进程重启恢复。设计约定：
- 资源点状态机：`VISIBLE →（在 FOV 内且 resource_cells 无此格）→ DEPLETED →
  （tick >= depleted_tick + refresh_interval_ticks）→ REVISIT_DUE →（再次可见）→ VISIBLE`。
- SDK 每 tick 的 `resource_cells` 只含**当前可见** RESOURCE 格。
  **仅当该格本 tick 仍在己方单位/Core 视野内、却不在 resource_cells 时**，
  才把 VISIBLE 判为已消耗（DEPLETED）。离开 FOV 不等于采空——否则空背包
  Worker 刚发现矿、下一步走出视距后记忆立刻丢失，会继续 beacon 探索不回头采。
- 回补节拍：`refresh_interval_ticks`（默认 4，近似「每 4 resolved tick」）。
- **REVISIT_DUE 仅是「该 chunk 可能已刷新」的信息提示，不是可采集目标**：
  官方规则回补可能发生在**新位置**（确定性随机选槽），旧格位置在重新可见之前
  不能作为 harvest 目标。`revisit_candidates` 只返回 VISIBLE（含「曾见、仍未在 FOV
  确认消失」的点，供空载 Worker 导航回去采）。
- 障碍永久累积（地形）；掉落 cargo 来自 `WORKER_CARGO_DROPPED` 事件。
- **已探格子 = 官方视野**：服务端每 tick 只发当前 FOV，Agent 自存。
  视距（曼哈顿）：Core=5 / Worker=3 / Vanguard=4 / Ranger=5；
  障碍挡视线（可见墙格本身，不穿透）；单位/Core/资源不挡。
  `explored_cells` 记录曾进入任意己方单位视野的格子（非仅落脚点）。
- 模块级 `WORLD_MEMORY` 单例供线上 `decide()` 默认使用；测试注入新实例。
- **持久化**：`to_dict` / `from_dict` / `save` / `load`；默认路径
  `.arena_hero_state.json`（gitignore）。服务端不回放历史 FOV，重启必须自载。

CRUD 说明：`observe` 负责 Create/Update（资源/障碍/掉落/视野已探）；`mark_harvested`
  负责状态迁移（Update）；`revisit_candidates` 负责 Read；掉落仅软删除
  （`DroppedCargoState.collected = True`）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from bot.pathing import (
    CHUNK_SIZE,
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

# 官方视野半径（曼哈顿，docs/GAME_UNDERSTANDING.md §2.4）
# 障碍挡视线；单位/Core/资源不挡。能看见的格子 = 已探索区域。
VISION_RADIUS: dict[str, int] = {
    "WORKER": 3,
    "VANGUARD": 4,
    "RANGER": 5,
    "CORE": 5,
}


def vision_disk(center: Position, radius: int) -> list[Position]:
    """返回曼哈顿菱形视野盘（含 center，半径 0..radius 全部格）。"""
    cx, cy = int(center[0]), int(center[1])
    r = max(0, int(radius))
    cells: list[Position] = []
    for dx in range(-r, r + 1):
        for dy in range(-(r - abs(dx)), (r - abs(dx)) + 1):
            cells.append((cx + dx, cy + dy))
    return cells


def has_line_of_sight(
    origin: Position,
    target: Position,
    obstacles: set[Position],
) -> bool:
    """曼哈顿网格视线：从 origin 到 target 路径上（不含两端）是否被障碍挡住。

    规则对齐官方：障碍挡视线；目标格本身若是墙仍「可见」（看到墙面）。
    使用轴对齐优先的曼哈顿步进（先走较大轴分量），确定性。
    """
    ox, oy = int(origin[0]), int(origin[1])
    tx, ty = int(target[0]), int(target[1])
    if ox == tx and oy == ty:
        return True
    x, y = ox, oy
    # 最多 man 步，避免死循环
    for _ in range(abs(tx - ox) + abs(ty - oy)):
        if x == tx and y == ty:
            break
        dx = 0 if x == tx else (1 if tx > x else -1)
        dy = 0 if y == ty else (1 if ty > y else -1)
        # 优先沿较大剩余轴走，保证路径唯一
        rem_x, rem_y = abs(tx - x), abs(ty - y)
        if rem_x >= rem_y and dx != 0:
            x += dx
        elif dy != 0:
            y += dy
        else:
            x += dx
        # 中间格（尚未到 target）若是障碍 → 挡视线
        if (x, y) != (tx, ty) and (x, y) in obstacles:
            return False
    return True


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


@dataclass
class ObstacleState:
    """单个障碍的记忆状态（探索优化 P2-2）。

    Attributes:
        pos: 障碍坐标。
        first_seen_tick: 首次可见 tick。
        last_seen_tick: 最近一次可见 tick（observe 每 tick 更新）。
        block_count: Beacon 推进被该障碍卡住的累计次数（record_obstacle_block 累计）。
    """

    pos: Position
    first_seen_tick: int = 0
    last_seen_tick: int = 0
    block_count: int = 0


class MemoryMap:
    """进程内单局地图记忆。

    Attributes:
        refresh_interval_ticks: 资源点刷新回补节拍。
        revisit_max_distance: 回访候选最大曼哈顿距离。
        sector_count: 扇区数（回访候选按 worker 扇区过滤时使用）。
        resource_points: pos -> ResourcePointState。
        obstacles: 永久障碍集合（地形）。
        obstacle_cache: pos -> ObstacleState（含时间戳与 block_count）。
        dropped_cargo: pos -> DroppedCargoState。
        explored_chunks: 全局已探 chunk 集合（多 Worker 共同贡献）。
        explored_chunk_ticks: chunk -> 首次到达 tick（供日志 / 未来过期策略）。
        explored_cells: 格子级已探 = 曾进入己方视野（pos -> 首次可见 tick）。
        explored_cell_last_seen: 格子级最近可见 tick。
    """

    CHUNK_SIZE: int = CHUNK_SIZE
    # 格子级足迹上限，防止长局无限增长（FIFO 近似：超出后丢最早）
    MAX_EXPLORED_CELLS: int = 20000

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
        self.obstacle_cache: dict[Position, ObstacleState] = {}
        self.dropped_cargo: dict[Position, DroppedCargoState] = {}
        self.explored_chunks: set[tuple[int, int]] = set()
        self.explored_chunk_ticks: dict[tuple[int, int], int] = {}
        self.chunk_last_seen_ticks: dict[tuple[int, int], int] = {}
        self.explored_cells: dict[Position, int] = {}
        self.explored_cell_last_seen: dict[Position, int] = {}
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

        # 先算障碍（视线判定依赖）
        obs = getattr(turn, "obstacle_cells", None)
        if obs is not None:
            for o in obs:
                try:
                    p = _as_position(o)
                except Exception:
                    continue
                self.obstacles.add(p)
                ost = self.obstacle_cache.get(p)
                if ost is None:
                    self.obstacle_cache[p] = ObstacleState(
                        pos=p,
                        first_seen_tick=int(tick),
                        last_seen_tick=int(tick),
                        block_count=0,
                    )
                else:
                    ost.last_seen_tick = int(tick)

        visible: set[Position] = set()
        cells = getattr(turn, "resource_cells", None)
        if cells is not None:
            for c in cells:
                try:
                    visible.add(_as_position(c))
                except Exception:
                    pass

        # 本 tick 己方实际 FOV（Core/W/V/R 视距 ∩ 视线），用于「确认采空」
        fov_cells = self._current_vision_cells(turn)

        # 状态迁移：
        # - 在 resource_cells → VISIBLE
        # - VISIBLE 且在 FOV 内但不在 resource_cells → DEPLETED（真采空/消失）
        # - VISIBLE 但不在 FOV → 保持 VISIBLE（走出视距，矿可能仍在）
        # - DEPLETED 到期 → REVISIT_DUE
        for pos, rp in list(self.resource_points.items()):
            if pos in visible:
                rp.mark_visible(tick)
            elif rp.state == VISIBLE and pos in fov_cells:
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

        # 官方视野 → 已探：每 tick 把己方单位/Core 曼哈顿视距盘写入 explored_cells
        # （能看见的格子 = 已探索区域；障碍挡视线，墙格本身可见）
        self._mark_unit_visions(turn, tick)

    def _vision_sources(self, turn: Any) -> list[tuple[Position, int]]:
        """收集己方 Core/Worker/Vanguard/Ranger 的 (原点, 视距)。"""
        sources: list[tuple[Position, int]] = []
        core = getattr(turn, "core", None)
        if core is not None:
            cpos = getattr(core, "position", None)
            if cpos is not None:
                try:
                    sources.append((_as_position(cpos), VISION_RADIUS["CORE"]))
                except Exception:
                    pass
        for attr, key in (
            ("workers", "WORKER"),
            ("vanguards", "VANGUARD"),
            ("rangers", "RANGER"),
        ):
            for u in getattr(turn, attr, None) or ():
                upos = getattr(u, "position", None)
                if upos is None:
                    continue
                try:
                    sources.append((_as_position(upos), VISION_RADIUS[key]))
                except Exception:
                    continue
        return sources

    def _current_vision_cells(self, turn: Any) -> set[Position]:
        """本 tick 己方单位/Core 视线可达的全部格子（曼哈顿视距 + 障碍挡视线）。"""
        blockers: set[Position] = set(self.obstacles)
        obs = getattr(turn, "obstacle_cells", None)
        if obs is not None:
            for o in obs:
                try:
                    blockers.add(_as_position(o))
                except Exception:
                    pass
        seen: set[Position] = set()
        for origin, radius in self._vision_sources(turn):
            for cell in vision_disk(origin, radius):
                if cell == origin or has_line_of_sight(origin, cell, blockers):
                    seen.add(cell)
        return seen


    def _mark_unit_visions(self, turn: Any, tick: int) -> None:
        """按官方视距把己方 Core/Worker/Vanguard/Ranger 视野盘标为已探。"""
        blockers: set[Position] = set(self.obstacles)
        obs = getattr(turn, "obstacle_cells", None)
        if obs is not None:
            for o in obs:
                try:
                    blockers.add(_as_position(o))
                except Exception:
                    pass

        for origin, radius in self._vision_sources(turn):
            self.mark_vision_disk(origin, radius, tick, blockers)
            self.mark_chunk_seen(origin, tick)

    def mark_vision_disk(
        self,
        origin: Position,
        radius: int,
        tick: int,
        obstacles: set[Position] | None = None,
    ) -> int:
        """将 origin 曼哈顿半径内、视线可达的格子标为已探。

        返回新标记的格子数。障碍挡视线；目标墙格本身仍标记可见。
        """
        o = _as_position(origin)
        blockers = obstacles if obstacles is not None else self.obstacles
        new_n = 0
        for cell in vision_disk(o, radius):
            if cell != o and not has_line_of_sight(o, cell, blockers):
                continue
            if self.mark_cell_visited(cell, tick):
                new_n += 1
            # chunk 级螺旋跳过仍以单位落脚 / mark_explored 为准，
            # 避免 FOV 边缘误把整块 16x16 标为已探而跳过深处。
        return new_n


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

    def mark_cell_visited(self, pos: Position, tick: int) -> bool:
        """记录格子曾被看见/到达（精确到 cell）。

        官方语义：视野内格子即已探索；本方法是底层写入。
        返回 True 表示该格首次记录。同时更新 last_seen。
        超过 MAX_EXPLORED_CELLS 时丢弃约 10% 最早记录，避免内存膨胀。
        """
        p = _as_position(pos)
        t = int(tick)
        is_new = p not in self.explored_cells
        if is_new:
            self.explored_cells[p] = t
            if len(self.explored_cells) > self.MAX_EXPLORED_CELLS:
                # 丢最早 10%（按 first_seen 排序）
                drop_n = max(1, self.MAX_EXPLORED_CELLS // 10)
                oldest = sorted(
                    self.explored_cells.items(), key=lambda kv: (kv[1], kv[0])
                )[:drop_n]
                for op, _ in oldest:
                    self.explored_cells.pop(op, None)
                    self.explored_cell_last_seen.pop(op, None)
        self.explored_cell_last_seen[p] = t
        return is_new

    def is_cell_explored(self, pos: Position) -> bool:
        """该格子是否曾进入任意己方单位视野（或被 mark 过）。"""
        return _as_position(pos) in self.explored_cells

    def mark_explored(self, pos: Position, tick: int) -> bool:
        """记录单位到达 `pos`（格子 + 所在 chunk；兼容旧调用）。

        新 **chunk** 返回 True（调用方据此打 `new_chunk` 日志）；重复 chunk 返回 False。
        格子级写入 `explored_cells`；完整 FOV 由 `observe` → `mark_vision_disk` 覆盖。
        """
        p = _as_position(pos)
        self.mark_cell_visited(p, tick)
        c = chunk_of(p)
        if c in self.explored_chunks:
            return False
        self.explored_chunks.add(c)
        self.explored_chunk_ticks[c] = int(tick)
        return True

    def mark_chunk_seen(self, pos: tuple[int, int], tick: int) -> None:
        """记录某位置所在 chunk 最近一次有 Worker 经过的 tick（每 tick 全量刷新，非首次）。"""
        p = _as_position(pos)
        chunk = (p[0] // self.CHUNK_SIZE, p[1] // self.CHUNK_SIZE)
        self.chunk_last_seen_ticks[chunk] = int(tick)
        # 同步格子 last_seen（observe 路径也会 mark_cell_visited）
        self.explored_cell_last_seen[p] = int(tick)
        if p not in self.explored_cells:
            self.explored_cells[p] = int(tick)

    def is_chunk_stale(self, chunk: tuple[int, int], tick: int, interval: int | None = None) -> bool:
        if interval is None:
            interval = self.refresh_interval_ticks
        last = self.chunk_last_seen_ticks.get(chunk, 0)
        return (int(tick) - last) > int(interval) * 50

    def is_explored(self, chunk: tuple[int, int]) -> bool:
        """该 chunk 是否已被（任意 Worker）探索过。"""
        return chunk in self.explored_chunks

    def record_obstacle_block(self, pos: Position, tick: int) -> None:
        """记录 Beacon 推进被该障碍卡住的累计次数（P2-2）。

        首次记录创建 ObstacleState；重复记录累计 `block_count` 并刷新
        `last_seen_tick`。不依赖 `obstacles` 集合（调用方已确认该格是障碍）。
        """
        p = _as_position(pos)
        ost = self.obstacle_cache.get(p)
        if ost is None:
            self.obstacle_cache[p] = ObstacleState(
                pos=p,
                first_seen_tick=int(tick),
                last_seen_tick=int(tick),
                block_count=1,
            )
        else:
            ost.last_seen_tick = int(tick)
            ost.block_count += 1

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

    # ---- 持久化（跨 agent 重启）----

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 的 dict（坐标用 [x,y]）。"""
        def _pos_key(p: Position) -> list[int]:
            return [int(p[0]), int(p[1])]

        def _chunk_key(c: tuple[int, int]) -> list[int]:
            return [int(c[0]), int(c[1])]

        resources: list[dict[str, Any]] = []
        for pos, rp in self.resource_points.items():
            resources.append(
                {
                    "pos": _pos_key(pos),
                    "state": rp.state,
                    "seen_tick": int(rp.seen_tick),
                    "depleted_tick": int(rp.depleted_tick),
                    "refresh_due_tick": int(rp.refresh_due_tick),
                    "chunk_ring": int(rp.chunk_ring),
                }
            )

        obstacles = [_pos_key(p) for p in sorted(self.obstacles)]
        obstacle_cache: list[dict[str, Any]] = []
        for pos, ost in self.obstacle_cache.items():
            obstacle_cache.append(
                {
                    "pos": _pos_key(pos),
                    "first_seen_tick": int(ost.first_seen_tick),
                    "last_seen_tick": int(ost.last_seen_tick),
                    "block_count": int(ost.block_count),
                }
            )

        dropped: list[dict[str, Any]] = []
        for pos, dc in self.dropped_cargo.items():
            dropped.append(
                {
                    "pos": _pos_key(pos),
                    "amount": int(dc.amount),
                    "drop_tick": int(dc.drop_tick),
                    "collected": bool(dc.collected),
                }
            )

        explored_chunks = [
            _chunk_key(c) for c in sorted(self.explored_chunks)
        ]
        explored_chunk_ticks = [
            {"chunk": _chunk_key(c), "tick": int(t)}
            for c, t in sorted(self.explored_chunk_ticks.items())
        ]
        chunk_last_seen = [
            {"chunk": _chunk_key(c), "tick": int(t)}
            for c, t in sorted(self.chunk_last_seen_ticks.items())
        ]
        # 格子级已探可能很大：只保留 first_seen + last_seen 合并列表
        explored_cells = [
            {
                "pos": _pos_key(p),
                "first": int(t),
                "last": int(self.explored_cell_last_seen.get(p, t)),
            }
            for p, t in sorted(self.explored_cells.items())
        ]

        return {
            "version": MEMORY_PERSIST_VERSION,
            "refresh_interval_ticks": int(self.refresh_interval_ticks),
            "revisit_max_distance": int(self.revisit_max_distance),
            "sector_count": int(self.sector_count),
            "center_chunk": _chunk_key(self.center_chunk),
            "resource_points": resources,
            "obstacles": obstacles,
            "obstacle_cache": obstacle_cache,
            "dropped_cargo": dropped,
            "explored_chunks": explored_chunks,
            "explored_chunk_ticks": explored_chunk_ticks,
            "chunk_last_seen_ticks": chunk_last_seen,
            "explored_cells": explored_cells,
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """用 dict 覆盖当前实例字段（就地更新，保留 self 身份）。"""
        if not isinstance(data, dict):
            return

        def _as_pos(raw: Any) -> Optional[Position]:
            try:
                if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                    return (int(raw[0]), int(raw[1]))
            except (TypeError, ValueError):
                return None
            return None

        def _as_chunk(raw: Any) -> Optional[tuple[int, int]]:
            return _as_pos(raw)

        try:
            self.refresh_interval_ticks = max(
                1, int(data.get("refresh_interval_ticks", self.refresh_interval_ticks))
            )
            self.revisit_max_distance = int(
                data.get("revisit_max_distance", self.revisit_max_distance)
            )
            self.sector_count = max(
                1, int(data.get("sector_count", self.sector_count))
            )
        except (TypeError, ValueError):
            pass

        cc = _as_chunk(data.get("center_chunk"))
        if cc is not None:
            self.center_chunk = cc

        # 资源点
        self.resource_points.clear()
        for item in data.get("resource_points") or ():
            if not isinstance(item, dict):
                continue
            p = _as_pos(item.get("pos"))
            if p is None:
                continue
            try:
                self.resource_points[p] = ResourcePointState(
                    pos=p,
                    state=str(item.get("state", VISIBLE)),
                    seen_tick=int(item.get("seen_tick", 0) or 0),
                    depleted_tick=int(item.get("depleted_tick", 0) or 0),
                    refresh_due_tick=int(item.get("refresh_due_tick", 0) or 0),
                    chunk_ring=int(item.get("chunk_ring", 0) or 0),
                )
            except (TypeError, ValueError):
                continue

        # 障碍
        self.obstacles.clear()
        for raw in data.get("obstacles") or ():
            p = _as_pos(raw)
            if p is not None:
                self.obstacles.add(p)

        self.obstacle_cache.clear()
        for item in data.get("obstacle_cache") or ():
            if not isinstance(item, dict):
                continue
            p = _as_pos(item.get("pos"))
            if p is None:
                continue
            try:
                self.obstacle_cache[p] = ObstacleState(
                    pos=p,
                    first_seen_tick=int(item.get("first_seen_tick", 0) or 0),
                    last_seen_tick=int(item.get("last_seen_tick", 0) or 0),
                    block_count=int(item.get("block_count", 0) or 0),
                )
                self.obstacles.add(p)
            except (TypeError, ValueError):
                continue

        # 掉落 cargo
        self.dropped_cargo.clear()
        for item in data.get("dropped_cargo") or ():
            if not isinstance(item, dict):
                continue
            p = _as_pos(item.get("pos"))
            if p is None:
                continue
            try:
                self.dropped_cargo[p] = DroppedCargoState(
                    pos=p,
                    amount=int(item.get("amount", 0) or 0),
                    drop_tick=int(item.get("drop_tick", 0) or 0),
                    collected=bool(item.get("collected", False)),
                )
            except (TypeError, ValueError):
                continue

        # chunk 已探
        self.explored_chunks.clear()
        for raw in data.get("explored_chunks") or ():
            c = _as_chunk(raw)
            if c is not None:
                self.explored_chunks.add(c)

        self.explored_chunk_ticks.clear()
        for item in data.get("explored_chunk_ticks") or ():
            if not isinstance(item, dict):
                continue
            c = _as_chunk(item.get("chunk"))
            if c is None:
                continue
            try:
                self.explored_chunk_ticks[c] = int(item.get("tick", 0) or 0)
                self.explored_chunks.add(c)
            except (TypeError, ValueError):
                continue

        self.chunk_last_seen_ticks.clear()
        for item in data.get("chunk_last_seen_ticks") or ():
            if not isinstance(item, dict):
                continue
            c = _as_chunk(item.get("chunk"))
            if c is None:
                continue
            try:
                self.chunk_last_seen_ticks[c] = int(item.get("tick", 0) or 0)
            except (TypeError, ValueError):
                continue

        # 格子级已探
        self.explored_cells.clear()
        self.explored_cell_last_seen.clear()
        for item in data.get("explored_cells") or ():
            if not isinstance(item, dict):
                continue
            p = _as_pos(item.get("pos"))
            if p is None:
                continue
            try:
                first = int(item.get("first", item.get("tick", 0)) or 0)
                last = int(item.get("last", first) or first)
            except (TypeError, ValueError):
                continue
            self.explored_cells[p] = first
            self.explored_cell_last_seen[p] = last

        # 超限裁剪（与 mark_cell_visited 一致）
        if len(self.explored_cells) > self.MAX_EXPLORED_CELLS:
            drop_n = max(1, len(self.explored_cells) - self.MAX_EXPLORED_CELLS)
            oldest = sorted(
                self.explored_cells.items(), key=lambda kv: (kv[1], kv[0])
            )[:drop_n]
            for op, _ in oldest:
                self.explored_cells.pop(op, None)
                self.explored_cell_last_seen.pop(op, None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryMap":
        """从 dict 构造新实例。"""
        mem = cls()
        mem.load_dict(data)
        return mem

    def save(self, path: Union[str, Path, None] = None) -> Path:
        """原子写 JSON 到 path（默认 DEFAULT_MEMORY_PATH）。"""
        target = Path(path) if path is not None else default_memory_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        tmp = target.with_suffix(target.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        return target

    def load(self, path: Union[str, Path, None] = None) -> bool:
        """从 path 加载到 self；文件不存在返回 False，损坏则记日志并返回 False。"""
        target = Path(path) if path is not None else default_memory_path()
        if not target.is_file():
            return False
        try:
            raw = target.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("memory load failed path=%s: %s", target, exc)
            return False
        if not isinstance(data, dict):
            _log.warning("memory load invalid root type path=%s", target)
            return False
        self.load_dict(data)
        return True

    def maybe_autosave(self, tick: int, path: Union[str, Path, None] = None) -> bool:
        """每 MEMORY_AUTOSAVE_EVERY_TICKS 个 tick 落盘一次；成功返回 True。"""
        t = int(tick)
        every = max(1, int(MEMORY_AUTOSAVE_EVERY_TICKS))
        if t <= 0 or (t % every) != 0:
            return False
        try:
            self.save(path)
            return True
        except OSError as exc:
            _log.warning("memory autosave failed tick=%s: %s", t, exc)
            return False


# 持久化版本号（破坏性变更时递增，load 可做迁移）
MEMORY_PERSIST_VERSION = 1
# 默认落盘路径（相对 cwd / 仓库根；已在 .gitignore）
DEFAULT_MEMORY_FILENAME = ".arena_hero_state.json"
# 自动保存间隔（tick）
MEMORY_AUTOSAVE_EVERY_TICKS = 25

_log = logging.getLogger("arena_hero_tactic.memory")


def default_memory_path() -> Path:
    """解析默认记忆文件路径：优先仓库根（bot/ 上级），否则 cwd。"""
    env = os.environ.get("ARENA_HERO_MEMORY_PATH", "").strip()
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    root = here.parent
    return root / DEFAULT_MEMORY_FILENAME


def load_world_memory(path: Union[str, Path, None] = None) -> bool:
    """加载到 WORLD_MEMORY 单例；返回是否成功读到文件。"""
    ok = WORLD_MEMORY.load(path)
    if ok:
        n_chunk = len(WORLD_MEMORY.explored_chunks)
        n_cell = len(WORLD_MEMORY.explored_cells)
        n_obs = len(WORLD_MEMORY.obstacles)
        _log.info(
            "memory loaded chunks=%s cells=%s obstacles=%s path=%s",
            n_chunk,
            n_cell,
            n_obs,
            path if path is not None else default_memory_path(),
        )
    else:
        _log.info(
            "memory start empty (no file or load failed) path=%s",
            path if path is not None else default_memory_path(),
        )
    return ok


def save_world_memory(path: Union[str, Path, None] = None) -> Optional[Path]:
    """保存 WORLD_MEMORY；失败返回 None。"""
    try:
        p = WORLD_MEMORY.save(path)
        _log.info(
            "memory saved chunks=%s cells=%s obstacles=%s path=%s",
            len(WORLD_MEMORY.explored_chunks),
            len(WORLD_MEMORY.explored_cells),
            len(WORLD_MEMORY.obstacles),
            p,
        )
        return p
    except OSError as exc:
        _log.warning("memory save failed: %s", exc)
        return None


# 线上默认单例：decide(turn, config, memory=None) 时使用
WORLD_MEMORY: MemoryMap = MemoryMap()
