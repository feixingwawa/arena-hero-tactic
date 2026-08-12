"""Dashboard 后端存储层：快照/日志环缓冲、SSE 订阅、build_snapshot。单文件模块。"""
from __future__ import annotations
import collections
import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

__all__ = [
    "get_store",
    "DashboardStore",
    "DashboardLogHandler",
    "build_snapshot",
    "safe_push_snapshot",
    "create_app",
    "start_dashboard_server",
]

_store_lock = threading.Lock()
_store_singleton: Optional["DashboardStore"] = None

# 异步快照队列：主循环只入队，后台线程 build+push，避免 BFS 占满 decide 窗口
_snap_queue: queue.Queue = queue.Queue(maxsize=1)
_snap_worker_started = False
_snap_worker_lock = threading.Lock()


def get_store() -> "DashboardStore":
    global _store_singleton
    if _store_singleton is None:
        with _store_lock:
            if _store_singleton is None:
                _store_singleton = DashboardStore()
    return _store_singleton


def _as_position(pos: Any) -> tuple[int, int]:
    if isinstance(pos, tuple) and len(pos) >= 2:
        return (int(pos[0]), int(pos[1]))
    if hasattr(pos, "x") and hasattr(pos, "y"):
        return (int(pos.x), int(pos.y))
    try:
        it = iter(pos)
        return (int(next(it)), int(next(it)))
    except Exception:
        return (0, 0)


def _compact_history_frame(snap: dict) -> dict:
    """压缩历史帧：趋势图/回放索引只需轻量字段。

    完整快照含 explored_cells / obstacles / path_estimate，120 帧可达数十 MB，
    前端每 500ms 全量拉取会卡死，地图 canvas 无法绘制。
    """
    if not isinstance(snap, dict):
        return {}
    core = snap.get("core") or {}
    beacon = snap.get("beacon") or {}
    units_out: list[dict] = []
    for u in snap.get("units") or []:
        if not isinstance(u, dict):
            continue
        econ = u.get("econ") if isinstance(u.get("econ"), dict) else {}
        units_out.append({
            "id": u.get("id"),
            "type": u.get("type"),
            "x": u.get("x"),
            "y": u.get("y"),
            "hp": u.get("hp"),
            "cargo": u.get("cargo"),
            "phase": econ.get("phase") or u.get("phase"),
            "role": econ.get("role") or u.get("role"),
            # 指令真源轻量字段（暂停回放仍能看本 tick 动作）
            "action": u.get("action"),
            "direction": u.get("direction"),
            "next_cell": u.get("next_cell"),
            "step_path": u.get("step_path"),
        })
    # decision_logs 仅保留字符串摘要，供趋势图 deposit 标记
    dlogs_raw = snap.get("decision_logs") or []
    dlogs: list[str] = []
    for item in dlogs_raw[:20]:
        if isinstance(item, str):
            dlogs.append(item[:120])
        elif isinstance(item, dict):
            msg = item.get("msg") or item.get("message") or item.get("text") or ""
            if msg:
                dlogs.append(str(msg)[:120])
    return {
        "tick": snap.get("tick"),
        "ts_ms": snap.get("ts_ms"),
        "resources": snap.get("resources"),
        "population": snap.get("population"),
        "counts": snap.get("counts") or {},
        "has_near_threat": bool(snap.get("has_near_threat")),
        "core": {
            "x": core.get("x"),
            "y": core.get("y"),
            "hp": core.get("hp"),
            "shield": core.get("shield"),
            "action": core.get("action"),
        } if core else None,
        "beacon": {
            "status": beacon.get("status"),
            "x": beacon.get("x"),
            "y": beacon.get("y"),
            "carrier_id": beacon.get("carrier_id"),
        } if beacon else None,
        "units": units_out,
        "decision_logs": dlogs,
        "data_source": snap.get("data_source", "arena_hero_sdk_turn"),
        "provider": snap.get("provider", "official SDK pipeline"),
        "data_kind": snap.get("data_kind", "command"),
        "commands": snap.get("commands") or [],
        "prev_tick": snap.get("prev_tick"),
        "compact": True,
    }


class DashboardStore:
    def __init__(self, capacity: int = 120, log_capacity: int = 5000) -> None:
        self._snapshots: collections.deque = collections.deque(maxlen=capacity)
        self._logs: collections.deque = collections.deque(maxlen=log_capacity)
        self._log_subscribers: list[Callable] = []
        self._subscriber_cond: threading.Condition = threading.Condition()
        self._write_lock: threading.Lock = threading.Lock()

    def push_snapshot(self, snap: dict) -> None:
        with self._write_lock:
            snap["ts_ms"] = int(time.time() * 1000)
            self._snapshots.append(snap)

    def get_latest(self) -> Optional[dict]:
        with self._write_lock:
            return self._snapshots[-1] if self._snapshots else None

    def get_history(self, n: int) -> list[dict]:
        with self._write_lock:
            return list(self._snapshots)[-min(n, len(self._snapshots)):] if self._snapshots else []

    def get_history_compact(self, n: int) -> list[dict]:
        """历史帧轻量投影：供趋势图/回放索引，避免 40MB+ JSON 卡死前端地图。"""
        frames = self.get_history(n)
        return [_compact_history_frame(f) for f in frames]

    def push_log_entry(self, entry: dict) -> None:
        with self._write_lock:
            self._logs.append(entry)
        with self._subscriber_cond:
            for cb in list(self._log_subscribers):
                try:
                    cb(entry)
                except Exception:
                    pass

    def get_logs(self, after_ts_ms: int = 0, limit: int = 200) -> list[dict]:
        result = []
        with self._write_lock:
            for e in self._logs:
                if int(e.get("ts_ms", 0)) > after_ts_ms:
                    result.append(e)
                    if len(result) >= limit:
                        break
        return result

    def subscribe_logs(self, callback: Callable) -> Callable:
        with self._subscriber_cond:
            self._log_subscribers.append(callback)

        def unsubscribe() -> None:
            with self._subscriber_cond:
                if callback in self._log_subscribers:
                    self._log_subscribers.remove(callback)
        return unsubscribe


class DashboardLogHandler(logging.Handler):
    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level=level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {"ts_ms": int(time.time() * 1000), "level": record.levelname,
                     "name": record.name, "msg": self.format(record)}
            get_store().push_log_entry(entry)
        except Exception:  # noqa: BLE001
            import sys, traceback
            traceback.print_exc(limit=1, file=sys.stderr)


def _safe_resource_cells(turn: Any) -> list[dict]:
    result = []
    for item in (getattr(turn, "resource_cells", None) or []):
        try:
            if hasattr(item, "position") and hasattr(item, "amount"):
                px, py = _as_position(item.position)
                amt = int(getattr(item, "amount", 0))
            elif isinstance(item, tuple) and len(item) >= 2:
                first = item[0]
                if isinstance(first, tuple) or hasattr(first, "x"):
                    px, py = _as_position(first)
                    amt = int(item[1]) if len(item) > 1 else 0
                elif len(item) >= 3:
                    px, py, amt = int(item[0]), int(item[1]), int(item[2])
                else:
                    continue
            else:
                continue
            result.append({"x": int(px), "y": int(py), "amount": int(amt)})
        except Exception:
            continue
    return result


def _safe_obstacles(memory: Any) -> list[dict]:
    result = []
    obs = getattr(memory, "obstacles", None)
    if obs is None:
        return result
    try:
        for o in obs:
            try:
                x, y = _as_position(o)
                result.append({"x": int(x), "y": int(y)})
            except Exception:
                continue
    except Exception:
        pass
    return result


def _safe_visible_enemies(turn: Any) -> list[dict]:
    result = []
    for e in (getattr(turn, "visible_enemies", None) or []):
        try:
            pos = getattr(e, "position", None)
            if pos is not None:
                ex, ey = _as_position(pos)
            else:
                ex, ey = int(getattr(e, "x", 0)), int(getattr(e, "y", 0))
            result.append({"id": str(getattr(e, "id", "")),
                           "type": str(getattr(e, "type", getattr(e, "unit_type", ""))),
                           "x": int(ex), "y": int(ey), "hp": int(getattr(e, "hp", 0))})
        except Exception:
            continue
    return result


def _safe_explored_chunks(memory: Any) -> list[dict]:
    """导出已探 16×16 chunk（对齐 MemoryMap / system_design-explore）。

    语义：
      - explored_chunks：Worker 到达过的 chunk 集合（整局永久）
      - explored_chunk_ticks：首次到达 tick → first_seen
      - chunk_last_seen_ticks：最近经过 tick → last_seen（无则回退 first_seen）
    返回 [{cx, cy, first_seen, last_seen}, ...]
    """
    if memory is None:
        return []

    def _as_chunk_key(key: Any) -> Optional[tuple[int, int]]:
        try:
            if isinstance(key, tuple) and len(key) >= 2:
                return (int(key[0]), int(key[1]))
            if hasattr(key, "x") and hasattr(key, "y"):
                return (int(key.x), int(key.y))
        except Exception:
            return None
        return None

    first_ticks = getattr(memory, "explored_chunk_ticks", None)
    last_ticks = getattr(memory, "chunk_last_seen_ticks", None)
    explored = getattr(memory, "explored_chunks", None)

    keys: set[tuple[int, int]] = set()
    if explored is not None:
        try:
            for c in explored:
                ck = _as_chunk_key(c)
                if ck is not None:
                    keys.add(ck)
        except Exception:
            pass
    for src in (first_ticks, last_ticks):
        if src is None or not hasattr(src, "items"):
            continue
        try:
            for k in src.keys():
                ck = _as_chunk_key(k)
                if ck is not None:
                    keys.add(ck)
        except Exception:
            continue

    # 兼容旧/私有 dict：_explored_chunks 为 chunk -> tick
    if not keys:
        priv = getattr(memory, "_explored_chunks", None)
        if priv is not None and hasattr(priv, "items"):
            try:
                for k in priv.keys():
                    ck = _as_chunk_key(k)
                    if ck is not None:
                        keys.add(ck)
                if first_ticks is None:
                    first_ticks = priv
            except Exception:
                pass

    if not keys:
        return []

    result: list[dict] = []
    count = 0
    for cx, cy in keys:
        if count >= 10000:
            break
        try:
            first = 0
            last = 0
            if first_ticks is not None and hasattr(first_ticks, "get"):
                try:
                    first = int(first_ticks.get((cx, cy), 0) or 0)
                except Exception:
                    first = 0
            if last_ticks is not None and hasattr(last_ticks, "get"):
                try:
                    last = int(last_ticks.get((cx, cy), 0) or 0)
                except Exception:
                    last = 0
            if last <= 0:
                last = first
            result.append({
                "cx": cx,
                "cy": cy,
                "first_seen": first,
                "last_seen": last,
            })
            count += 1
        except Exception:
            continue
    return result


def _safe_explored_cells(memory: Any) -> list[dict]:
    """导出格子级已探（官方视野：曾进入己方 FOV 的格子）。

    返回 [{x, y, first_seen, last_seen}, ...]，上限 8000 条，优先最近可见。
    对齐 docs：Core5/Worker3/Vanguard4/Ranger5 曼哈顿视距。
    """
    if memory is None:
        return []
    cells = getattr(memory, "explored_cells", None)
    last_map = getattr(memory, "explored_cell_last_seen", None)
    if cells is None or not hasattr(cells, "items"):
        return []

    items: list[tuple[tuple[int, int], int, int]] = []
    try:
        for key, first_raw in cells.items():
            try:
                x, y = _as_position(key)
                first = int(first_raw or 0)
            except Exception:
                continue
            last = first
            if last_map is not None and hasattr(last_map, "get"):
                try:
                    last = int(last_map.get(key, first) or first)
                except Exception:
                    last = first
            items.append(((int(x), int(y)), first, last))
    except Exception:
        return []

    # 最近 last_seen 优先，便于 UI 在上限内保留活跃区域
    items.sort(key=lambda t: (t[2], t[0][0], t[0][1]), reverse=True)
    result: list[dict] = []
    for (x, y), first, last in items[:8000]:
        result.append({
            "x": x,
            "y": y,
            "first_seen": first,
            "last_seen": last,
        })
    return result


def _safe_obstacle_blocks(memory: Any) -> list[dict]:
    raw = getattr(memory, "obstacle_blocks", None)
    items = None
    if raw is not None and hasattr(raw, "items"):
        items = list(raw.items())
    else:
        cache = getattr(memory, "obstacle_cache", None)
        if cache is not None and hasattr(cache, "items"):
            items = [(k, int(getattr(v, "block_count", 0) or 0)) for k, v in cache.items()]
    if items is None:
        return []
    try:
        items.sort(key=lambda kv: int(kv[1] or 0), reverse=True)
    except Exception:
        pass
    result = []
    for it in items[:200]:
        try:
            x, y = _as_position(it[0])
            result.append({"x": int(x), "y": int(y), "count": int(it[1] or 0)})
        except Exception:
            continue
    return result


def _obstacle_set_from_memory(memory: Any) -> set[tuple[int, int]]:
    """从 memory 提取障碍格集合（Dashboard 路径 dry-run 用，不写回 memory）。"""
    out: set[tuple[int, int]] = set()
    obs = getattr(memory, "obstacles", None)
    if obs is None:
        return out
    try:
        for o in obs:
            try:
                x, y = _as_position(o)
                out.add((int(x), int(y)))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _bfs_path(
    origin: tuple[int, int],
    target: tuple[int, int],
    obstacles: set[tuple[int, int]],
    *,
    max_expand: int = 4000,
) -> tuple[Optional[list[list[int]]], list[list[int]]]:
    """四连通 BFS 最短绕障路径（Dashboard 可视化专用）。

    - **绝不**踏入 obstacles 格（起点若叠在障上仅作显示起点，下一步必须离开）；
    - 终点若在障上：寻到邻接可达自由格即止（不踏入终点障）；
    - 返回 (waypoints|None, blocked_touched)。
    """
    ox, oy = origin
    tx, ty = target
    if (ox, oy) == (tx, ty):
        return [[ox, oy]], []

    hard = set(obstacles)
    goal = (tx, ty)
    goal_blocked = goal in hard
    # 可接受到达格：自由终点，或终点被挡时的邻接自由格（永不含障碍格）
    goal_accept: set[tuple[int, int]] = set()
    if not goal_blocked:
        goal_accept.add(goal)
    else:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (tx + dx, ty + dy)
            if n not in hard:
                goal_accept.add(n)
        if not goal_accept:
            return None, [[tx, ty]]

    from collections import deque

    q: deque[tuple[int, int]] = deque()
    q.append((ox, oy))
    parent: dict[tuple[int, int], Optional[tuple[int, int]]] = {(ox, oy): None}
    blocked_touch: list[list[int]] = [[tx, ty]] if goal_blocked else []
    seen_block: set[tuple[int, int]] = {goal} if goal_blocked else set()
    expanded = 0

    def _finish(end: tuple[int, int]) -> tuple[list[list[int]], list[list[int]]]:
        path: list[list[int]] = []
        cur: Optional[tuple[int, int]] = end
        while cur is not None:
            path.append([cur[0], cur[1]])
            cur = parent.get(cur)
        path.reverse()
        return path, blocked_touch

    if (ox, oy) in goal_accept:
        return [[ox, oy]], blocked_touch

    while q and expanded < max_expand:
        x, y = q.popleft()
        expanded += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            nxt = (nx, ny)
            if nxt in parent:
                continue
            if nxt in hard:
                # 硬障永不入队；仅记录触碰
                if nxt not in seen_block:
                    seen_block.add(nxt)
                    blocked_touch.append([nx, ny])
                continue
            parent[nxt] = (x, y)
            if nxt in goal_accept:
                return _finish(nxt)
            q.append(nxt)

    return None, blocked_touch


def _downsample_waypoints(
    wps: list[list[int]], *, max_points: int = 80
) -> list[list[int]]:
    """过密路点降采样，保留首尾与转折，便于前端绘制。

    **禁止**抽掉拐点后留下非四连通邻接（否则 canvas lineTo 会画成斜线）。
    降采样后必须再经 ``_ensure_cardinal_chain``。
    """
    if len(wps) <= max_points:
        return wps
    keep = {0, len(wps) - 1}
    # 均匀取样
    step = max(1, (len(wps) - 1) // (max_points - 2))
    for i in range(0, len(wps), step):
        keep.add(i)
    # 转折点
    for i in range(1, len(wps) - 1):
        ax, ay = wps[i - 1]
        bx, by = wps[i]
        cx, cy = wps[i + 1]
        if (bx - ax, by - ay) != (cx - bx, cy - by):
            keep.add(i)
    idxs = sorted(keep)
    if len(idxs) > max_points:
        # 再均匀压缩
        step2 = max(1, len(idxs) // max_points)
        core = idxs[::step2]
        if idxs[-1] not in core:
            core.append(idxs[-1])
        idxs = sorted(set(core))
    return _ensure_cardinal_chain([wps[i] for i in idxs])


def _manhattan_staircase(
    a: tuple[int, int],
    b: tuple[int, int],
    *,
    obstacles: Optional[set[tuple[int, int]]] = None,
    prefer_horizontal_first: bool = True,
) -> list[list[int]]:
    """在 a→b 之间插入四连通阶梯（不含 a，含 b）。

    用于滤障删点 / 降采样后避免 canvas 直接斜线。若某步踩障则换轴顺序；
    仍无法前进则停止（不穿障）。
    """
    ax, ay = int(a[0]), int(a[1])
    bx, by = int(b[0]), int(b[1])
    if (ax, ay) == (bx, by):
        return []
    hard = obstacles or set()
    out: list[list[int]] = []
    x, y = ax, ay

    def _try_axis(h_first: bool) -> list[list[int]]:
        cx, cy = ax, ay
        pts: list[list[int]] = []
        # 限制步数，防止异常坐标爆炸
        budget = abs(bx - ax) + abs(by - ay) + 2
        while (cx, cy) != (bx, by) and budget > 0:
            budget -= 1
            moved = False
            order = []
            if h_first:
                if cx != bx:
                    order.append((1 if bx > cx else -1, 0))
                if cy != by:
                    order.append((0, 1 if by > cy else -1))
            else:
                if cy != by:
                    order.append((0, 1 if by > cy else -1))
                if cx != bx:
                    order.append((1 if bx > cx else -1, 0))
            for dx, dy in order:
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in hard and (nx, ny) != (bx, by):
                    continue
                # 终点若在障上仍允许最后一格由调用方决定；此处不主动踏障
                if (nx, ny) in hard:
                    continue
                cx, cy = nx, ny
                pts.append([cx, cy])
                moved = True
                break
            if not moved:
                break
        return pts

    first = _try_axis(prefer_horizontal_first)
    if first and first[-1] == [bx, by]:
        return first
    second = _try_axis(not prefer_horizontal_first)
    if second and second[-1] == [bx, by]:
        return second
    # 退路：无障阶梯（仅可视化补全；穿障由 filter 上游尽量避免）
    x, y = ax, ay
    while x != bx:
        x += 1 if bx > x else -1
        out.append([x, y])
    while y != by:
        y += 1 if by > y else -1
        out.append([x, y])
    return out


def _ensure_cardinal_chain(
    wps: list[list[int]],
    *,
    obstacles: Optional[set[tuple[int, int]]] = None,
    max_points: int = 160,
) -> list[list[int]]:
    """保证相邻路点均为四连通一步；空隙用曼哈顿阶梯填满。

    Dashboard 黄线 = 可执行步序列；禁止出现 (dx,dy) 同时非 0 的斜跳。
    """
    if not wps:
        return wps
    out: list[list[int]] = [[int(wps[0][0]), int(wps[0][1])]]
    for p in wps[1:]:
        tx, ty = int(p[0]), int(p[1])
        if out[-1] == [tx, ty]:
            continue
        lx, ly = out[-1][0], out[-1][1]
        dx, dy = abs(tx - lx), abs(ty - ly)
        if dx + dy == 1:
            out.append([tx, ty])
        elif dx + dy == 0:
            continue
        else:
            # 非邻接（含对角/远跳）：展开为四连通阶梯
            fill = _manhattan_staircase(
                (lx, ly), (tx, ty), obstacles=obstacles
            )
            for q in fill:
                if out[-1] != q:
                    out.append(q)
                if len(out) >= max_points:
                    return out
        if len(out) >= max_points:
            break
    return out


def _filter_obstacle_waypoints(
    wps: list[list[int]],
    obstacles: set[tuple[int, int]],
    *,
    origin: tuple[int, int],
    target: tuple[int, int],
) -> list[list[int]]:
    """剥离路径中的障碍格；禁止画线与障碍重合。

    仅保留起点（单位当前格，即使异常叠障也要有起点），其余障碍格一律剔除。
    剔除后若出现非四连通空隙，用自由格阶梯补全（仍不踏障），避免前端斜线。
    目的地菱形由前端 destination 单独绘制，不依赖 waypoints 含 target 障格。
    """
    if not wps:
        return wps
    raw: list[list[int]] = []
    for p in wps:
        t = (int(p[0]), int(p[1]))
        # 除 origin 外，障碍格一律不进折线
        if t in obstacles and t != origin:
            continue
        if raw and raw[-1] == [t[0], t[1]]:
            continue
        raw.append([t[0], t[1]])
    if not raw:
        return [[origin[0], origin[1]]]
    # 滤障可能造成跳跃：强制四连通阶梯（绕开 hard）
    return _ensure_cardinal_chain(raw, obstacles=obstacles, max_points=160)


def _runtime_expand_cap(man: int) -> int:
    """与 guided_step_toward / _install_bfs_route 相同的 A* expand 上限。"""
    if man <= 12:
        return 600
    if man <= 40:
        return 1800
    return 4500


def _blocked_touch_along_path(
    wps: list[list[int]],
    hard: set[tuple[int, int]],
    *,
    limit: int = 32,
) -> list[list[int]]:
    """沿路径邻接障碍，供前端粉色高亮（非路径本身）。"""
    out: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    for p in wps:
        try:
            x, y = int(p[0]), int(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in hard and nb not in seen:
                seen.add(nb)
                out.append([nb[0], nb[1]])
                if len(out) >= limit:
                    return out
    return out


def _path_reached(
    wps: list[list[int]],
    tx: int,
    ty: int,
    hard: set[tuple[int, int]],
) -> bool:
    if not wps:
        return False
    lx, ly = int(wps[-1][0]), int(wps[-1][1])
    if (tx, ty) in hard:
        return abs(lx - tx) + abs(ly - ty) == 1
    return lx == tx and ly == ty


def _build_path_estimate(
    origin: tuple[int, int],
    target: Optional[list],
    obstacles: set[tuple[int, int]],
    *,
    cache: Optional[dict] = None,
    max_steps: int = 160,
    far_manhattan: int = 40,
    runtime_route: Optional[list] = None,
) -> Optional[dict]:
    """生成与**运行时执行**一致的导航路径可视化（不修改 memory）。

    硬约束：waypoints 不得落在 obstacles 上（穿障线禁止）。
    策略（submit 之后，后台线程算，可完整跑 A*）：
    1. **优先** Worker 本 tick 已缓存的 A* ``route_waypoints``（与 guided 实际走的相同）；
    2. 否则调用 pathing._astar_route（与 bfs_next_step / _install_bfs_route 同源 + 同 expand cap）；
    3. A* 失败再 reconstruct_path；仍失败只画原点，**绝不**曼哈顿穿障补线。
    """
    if target is None or not isinstance(target, (list, tuple)) or len(target) < 2:
        return None
    try:
        ox, oy = int(origin[0]), int(origin[1])
        tx, ty = int(target[0]), int(target[1])
    except (TypeError, ValueError):
        return None

    hard = set(obstacles) if obstacles else set()
    man = abs(tx - ox) + abs(ty - oy)
    if man <= 0:
        est = {
            "steps": 0,
            "waypoints": [[ox, oy]],
            "blocked": [],
            "destination": [tx, ty],
            "planned": "noop",
            "approx": False,
            "partial": False,
        }
        return est

    # 1) 运行时已装好的 A* 航点：前端黄线 = 单位下一步真正会走的路
    if runtime_route:
        raw_rt: list[list[int]] = [[ox, oy]]
        for p in runtime_route:
            try:
                if isinstance(p, dict):
                    raw_rt.append([int(p["x"]), int(p["y"])])
                else:
                    raw_rt.append([int(p[0]), int(p[1])])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        wps = _filter_obstacle_waypoints(
            raw_rt, hard, origin=(ox, oy), target=(tx, ty)
        )
        # 运行时 route 已是执行路径，不降采样丢拐点（最多 96 与 runtime 缓存一致）
        if len(wps) > 96:
            wps = wps[:96]
        reached = _path_reached(wps, tx, ty, hard)
        # 终点未在 route 尾：仍画已装航点（单位正沿途走），标 partial 但不改路线
        est = {
            "steps": max(0, len(wps) - 1),
            "waypoints": wps if wps else [[ox, oy]],
            "blocked": _blocked_touch_along_path(wps, hard),
            "destination": [tx, ty],
            "approx": not reached,
            "partial": not reached,
            "planned": "runtime",
        }
        return est

    key = ((ox, oy), (tx, ty))
    if cache is not None and key in cache:
        return cache[key]

    expand_cap = _runtime_expand_cap(man)

    # 2) 与 runtime 同源 A*（接口名历史为 bfs_next_step，实现已是 A*）
    try:
        from bot.pathing import _astar_route

        result = _astar_route(
            (ox, oy), (tx, ty), hard, max_expand=expand_cap, avoid=None
        )
        if result is not None:
            rev, _parent, found = result
            raw = [[ox, oy]] + [[int(p[0]), int(p[1])] for p in rev]
            wps = _filter_obstacle_waypoints(
                raw, hard, origin=(ox, oy), target=(tx, ty)
            )
            # 完整路径不降采样（与 runtime route[:96] 对齐）；过长截断尾
            if len(wps) > 96:
                wps = wps[:96]
            reached = _path_reached(wps, tx, ty, hard)
            # 邻接障碍高亮 + 终点若在障上
            blocked = _blocked_touch_along_path(wps, hard)
            if (tx, ty) in hard and [tx, ty] not in blocked:
                blocked = [[tx, ty]] + blocked
                blocked = blocked[:32]
            est = {
                "steps": max(0, len(rev)),
                "waypoints": wps if wps else [[ox, oy]],
                "blocked": blocked[:32],
                "destination": [tx, ty],
                "approx": not reached,
                "partial": not reached,
                "planned": "astar",  # 与 runtime A* 同源；前端仍按 planned 实线/虚线
            }
            if cache is not None:
                cache[key] = est
            return est
    except Exception:
        pass

    # 兼容旧测试/短路径：本地 BFS（同 cap 语义下的最短路，无 A* 启发时仍正确）
    try:
        bfs_wps, bfs_blocked = _bfs_path(
            (ox, oy), (tx, ty), hard, max_expand=expand_cap
        )
        if bfs_wps and len(bfs_wps) >= 1:
            wps = _filter_obstacle_waypoints(
                bfs_wps, hard, origin=(ox, oy), target=(tx, ty)
            )
            if len(wps) > 96:
                wps = wps[:96]
            reached = _path_reached(wps, tx, ty, hard)
            est = {
                "steps": max(0, len(bfs_wps) - 1),
                "waypoints": wps,
                "blocked": (bfs_blocked or [])[:32],
                "destination": [tx, ty],
                "approx": not reached,
                "partial": not reached,
                "planned": "bfs",
            }
            if cache is not None:
                cache[key] = est
            return est
    except Exception:
        pass

    # 3) A*/BFS 未通：runtime 同源 reconstruct（仍禁止穿障补线）
    try:
        from bot.pathing import reconstruct_path

        step_cap = max(16, min(int(max_steps), man + max(16, man // 2)))
        steps, blocked, waypoints = reconstruct_path(
            (ox, oy),
            (tx, ty),
            hard,
            memory=None,
            max_steps=step_cap,
        )
        raw = [[int(p[0]), int(p[1])] for p in waypoints]
        wps = _filter_obstacle_waypoints(
            raw, hard, origin=(ox, oy), target=(tx, ty)
        )
        if len(wps) > 96:
            wps = wps[:96]
        reached = bool(wps) and wps[-1][0] == tx and wps[-1][1] == ty
        est = {
            "steps": int(steps) if reached else max(len(wps) - 1, 0),
            "waypoints": wps if wps else [[ox, oy]],
            "blocked": [[int(p[0]), int(p[1])] for p in blocked][:32],
            "destination": [tx, ty],
            "approx": not reached,
            "planned": "reconstruct",
            "partial": not reached,
        }
        if cache is not None:
            cache[key] = est
        return est
    except Exception:
        est = {
            "steps": int(man),
            "waypoints": [[ox, oy]],
            "blocked": [],
            "destination": [tx, ty],
            "approx": True,
            "planned": "none",
            "partial": True,
        }
        if cache is not None:
            cache[key] = est
        return est


def _build_unit(
    u: Any,
    econ_states: Optional[dict],
    obstacles: Optional[set[tuple[int, int]]] = None,
    path_cache: Optional[dict] = None,
    cmd_by_unit: Optional[dict] = None,
) -> dict:
    uid = str(getattr(u, "id", ""))
    pos_obj = getattr(u, "position", None)
    if pos_obj is not None:
        ux, uy = _as_position(pos_obj)
    else:
        ux, uy = int(getattr(u, "x", 0)), int(getattr(u, "y", 0))
    econ: dict
    tgt_list: Optional[list] = None
    runtime_route: Optional[list] = None
    if econ_states and uid in econ_states:
        ws = econ_states[uid]
        tgt = getattr(ws, "target", None)
        if tgt is not None:
            try:
                tx, ty = _as_position(tgt)
                tgt_list = [int(tx), int(ty)]
            except Exception:
                tgt_list = None
        ring = getattr(ws, "ring", None)
        sector = getattr(ws, "sector", None)
        if sector is None:
            sector = getattr(ws, "sector_id", None)
        phase = getattr(ws, "phase", None)
        role = getattr(ws, "role", None)
        dedicated = getattr(ws, "dedicated", None)
        raw_route = getattr(ws, "route_waypoints", None)
        if raw_route:
            try:
                runtime_route = []
                for p in raw_route:
                    if isinstance(p, dict):
                        runtime_route.append([int(p["x"]), int(p["y"])])
                    else:
                        runtime_route.append([int(p[0]), int(p[1])])
            except Exception:
                runtime_route = None
        if tgt_list is None:
            rd = getattr(ws, "route_dest", None)
            if rd is not None:
                try:
                    rx, ry = _as_position(rd)
                    tgt_list = [int(rx), int(ry)]
                except Exception:
                    pass
        econ = {"target": tgt_list,
                "ring": int(ring) if ring is not None else None,
                "sector": int(sector) if sector is not None else None,
                "phase": str(phase) if phase is not None else None,
                "role": str(role) if role is not None else None,
                "dedicated": bool(dedicated) if dedicated is not None else None}
    else:
        econ = {"target": None, "ring": None, "sector": None,
                "phase": None, "role": None, "dedicated": None}

    # 指令真源（本 tick 实际 SDK 调用）
    cmd = None
    if cmd_by_unit and uid in cmd_by_unit:
        cmd = cmd_by_unit[uid]
        if isinstance(cmd, dict):
            pass
        else:
            cmd = getattr(cmd, "to_dict", lambda: None)() or None
    action = None
    direction = None
    next_cell: Optional[list] = None
    if isinstance(cmd, dict):
        action = cmd.get("action")
        direction = cmd.get("direction")
        nc = cmd.get("next_cell")
        if isinstance(nc, (list, tuple)) and len(nc) >= 2:
            next_cell = [int(nc[0]), int(nc[1])]
        # 无 econ target 时用指令 target
        if tgt_list is None:
            tc = cmd.get("target_cell")
            if isinstance(tc, (list, tuple)) and len(tc) >= 2:
                tgt_list = [int(tc[0]), int(tc[1])]
                econ = dict(econ)
                econ["target"] = tgt_list
        if econ.get("phase") is None and cmd.get("phase"):
            econ = dict(econ)
            econ["phase"] = cmd.get("phase")
        if econ.get("role") is None and cmd.get("role"):
            econ = dict(econ)
            econ["role"] = cmd.get("role")

    path_estimate = None
    utype = str(getattr(u, "type", getattr(u, "unit_type", "")) or "")
    # step_path：本 tick 真实一步（move 的 next_cell）— 前端默认只画这个
    step_path: Optional[list] = None
    if next_cell is not None:
        step_path = [[int(ux), int(uy)], [int(next_cell[0]), int(next_cell[1])]]

    # 多步计划：Worker 优先 runtime route；非 move 不强制长线
    want_plan = (
        (tgt_list is not None or runtime_route)
        and ("WORKER" in utype.upper() or action == "move")
    )
    if want_plan and action in (None, "move", "sweep"):
        dest = tgt_list
        if dest is None and runtime_route:
            try:
                last = runtime_route[-1]
                dest = [int(last[0]), int(last[1])]
            except Exception:
                dest = None
        if dest is None and next_cell is not None:
            dest = list(next_cell)
        if dest is not None:
            path_estimate = _build_path_estimate(
                (int(ux), int(uy)),
                dest,
                obstacles or set(),
                cache=path_cache,
                runtime_route=runtime_route,
            )
    # 仅有一步时也给出最小 path_estimate，保证前端有东西画
    if path_estimate is None and step_path is not None:
        path_estimate = {
            "steps": 1,
            "waypoints": step_path,
            "blocked": [],
            "destination": list(next_cell) if next_cell else step_path[-1],
            "approx": False,
            "partial": False,
            "planned": "step",
        }
    if path_estimate is not None and step_path is not None:
        path_estimate = dict(path_estimate)
        path_estimate["step_path"] = step_path
        path_estimate["step_only_default"] = True

    return {
        "id": uid,
        "type": utype,
        "x": int(ux),
        "y": int(uy),
        "hp": int(getattr(u, "hp", 0) or 0),
        "cargo": int(getattr(u, "cargo", 0) or 0),
        "econ": econ,
        "path_estimate": path_estimate,
        "action": str(action) if action is not None else None,
        "direction": str(direction) if direction is not None else None,
        "next_cell": next_cell,
        "step_path": step_path,
        "command": cmd if isinstance(cmd, dict) else None,
    }


def build_snapshot(turn: Any, result: Any, memory: Any, econ_states: Optional[dict] = None) -> Optional[dict]:
    # 严格：没有 turn / 没有 result 的任何一项绝对不兜底数（不返回 0 值伪造数据）
    if turn is None or result is None:
        return None
    # tick：必须来自 result 或 turn，绝不用 0 兜底
    _tick_raw = (
        getattr(result, "tick", None)
        or getattr(turn, "tick", None)
        or getattr(turn, "current_tick", None)
        or getattr(turn, "turn_id", None)
    )
    if _tick_raw is None:
        # 没有真实 tick 信息 → 不允许伪造 tick=0，直接返回 None（safe_push_snapshot 丢弃）
        return None
    try:
        tick = int(_tick_raw)
    except (TypeError, ValueError):
        return None
    counts: dict = {}
    try:
        for k, v in (getattr(result, "counts", None) or {}).items():
            counts[str(k)] = int(v or 0)
    except Exception:
        counts = {}
    dlogs: list = []
    try:
        dlogs = list(getattr(result, "logs", None) or [])
    except Exception:
        dlogs = []
    core = getattr(turn, "core", None)
    core_dict: Optional[dict] = None
    if core is not None:
        cpos = getattr(core, "position", None)
        cx: Optional[int]
        cy: Optional[int]
        if cpos is not None:
            cx, cy = _as_position(cpos)
        else:
            cx0, cy0 = getattr(core, "x", None), getattr(core, "y", None)
            if cx0 is None or cy0 is None:
                cx = cy = None
            else:
                cx, cy = int(cx0), int(cy0)
        # hp/shield 只有属性存在才设置，否则 None（不默认 0 伪造满血核心）
        _hp = getattr(core, "hp", None)
        _hp_max = getattr(core, "hp_max", getattr(core, "max_hp", None))
        _shield = getattr(core, "shield", None)
        _shield_max = getattr(core, "shield_max", getattr(core, "max_shield", None))
        _core_action = getattr(result, "core_action", None)
        core_dict = {
            "position": [int(cx), int(cy)] if cx is not None and cy is not None else None,
            "x": int(cx) if cx is not None else None,
            "y": int(cy) if cy is not None else None,
            "hp": int(_hp) if _hp is not None else None,
            "hp_max": int(_hp_max) if _hp_max is not None else None,
            "shield": int(_shield) if _shield is not None else None,
            "shield_max": int(_shield_max) if _shield_max is not None else None,
            "action": str(_core_action) if _core_action is not None else None,
        }
    all_units = (list(getattr(turn, "workers", None) or []) +
                 list(getattr(turn, "vanguards", None) or []) +
                 list(getattr(turn, "rangers", None) or []))
    path_obstacles = _obstacle_set_from_memory(memory)
    # 同帧路径估算缓存：多 Worker 常指向同一 Core/矿点
    path_cache: dict = {}
    # 本 tick 指令真源
    commands_raw: list = []
    try:
        commands_raw = list(getattr(result, "commands", None) or [])
    except Exception:
        commands_raw = []
    if not commands_raw:
        try:
            from bot.command_ledger import get_commands_dicts

            commands_raw = get_commands_dicts()
        except Exception:
            commands_raw = []
    cmd_by_unit: dict = {}
    for c in commands_raw:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("unit_id") or "")
        if cid:
            cmd_by_unit[cid] = c
    prev_commands_raw: list = []
    prev_tick_val = None
    try:
        prev_commands_raw = list(getattr(result, "prev_commands", None) or [])
        prev_tick_val = getattr(result, "prev_tick", None)
    except Exception:
        pass
    if not prev_commands_raw:
        try:
            from bot.command_ledger import get_prev_commands_dicts, get_prev_tick

            prev_commands_raw = get_prev_commands_dicts()
            prev_tick_val = get_prev_tick()
        except Exception:
            pass
    units_list = []
    for u in all_units:
        try:
            units_list.append(
                _build_unit(
                    u,
                    econ_states,
                    path_obstacles,
                    path_cache=path_cache,
                    cmd_by_unit=cmd_by_unit,
                )
            )
        except Exception:
            continue
    beacon = getattr(turn, "beacon", None)
    beacon_dict: Optional[dict] = None
    if beacon is not None:
        try:
            bpos = getattr(beacon, "position", None)
            bx: Optional[int]
            by: Optional[int]
            bposition: Optional[list]
            if bpos is not None:
                bpx, bpy = _as_position(bpos)
                bx, by, bposition = int(bpx), int(bpy), [int(bpx), int(bpy)]
            else:
                bx0, by0 = getattr(beacon, "x", None), getattr(beacon, "y", None)
                if bx0 is None or by0 is None:
                    bx, by, bposition = None, None, None
                else:
                    bx, by = int(bx0), int(by0)
                    bposition = [bx, by]
            bstatus = getattr(beacon, "status", None)
            if bstatus is not None:
                bstatus = str(bstatus)
            cid = getattr(beacon, "carrier_id", None)
            beacon_dict = {"position": bposition, "x": bx, "y": by,
                           "status": bstatus,
                           "carrier_id": str(cid) if cid is not None else None}
        except Exception:
            beacon_dict = None
    resources_raw = getattr(result, "resources", None)
    population_raw = getattr(result, "population", None)
    return {
        # 数据来源标识（EXP-817061：明确标识真实来源，禁止前端把模拟数据当真实）
        "data_source": "arena_hero_sdk_turn",
        "provider": "bot.main run_session → official SDK submit pipeline",
        "data_kind": "command",
        "tick": tick,
        "ts_ms": int(time.time() * 1000),
        "resources": int(resources_raw) if resources_raw is not None else None,
        "population": int(population_raw) if population_raw is not None else None,
        "counts": counts,
        "has_near_threat": bool(getattr(result, "has_near_threat", False)),
        "has_near_threat_explicit": "has_near_threat" in dir(result) or isinstance(getattr(result, "has_near_threat", None), bool),
        "decision_logs": dlogs,
        "core": core_dict,
        "units": units_list,
        "beacon": beacon_dict,
        "resource_cells": _safe_resource_cells(turn),
        "obstacles": _safe_obstacles(memory),
        "visible_enemies": _safe_visible_enemies(turn),
        "commands": list(commands_raw),
        "prev_commands": list(prev_commands_raw),
        "prev_tick": int(prev_tick_val) if prev_tick_val is not None else None,
        "memory": {
            "explored_chunks": _safe_explored_chunks(memory),
            "explored_cells": _safe_explored_cells(memory),
            "obstacle_blocks": _safe_obstacle_blocks(memory),
        },
    }


def _ensure_snap_worker() -> None:
    """启动单例后台线程：只保留最新一帧快照参数，丢弃积压。"""
    global _snap_worker_started
    if _snap_worker_started:
        return
    with _snap_worker_lock:
        if _snap_worker_started:
            return

        def _worker() -> None:
            log = logging.getLogger("arena_hero_tactic")
            while True:
                try:
                    item = _snap_queue.get()
                except Exception:
                    continue
                if item is None:
                    continue
                turn, result, memory, econ_states = item
                try:
                    snap = build_snapshot(turn, result, memory, econ_states)
                    if snap is None:
                        log.warning(
                            "dashboard:build_snapshot=None turn=%s result=%s "
                            "turn_tick=%r result_tick=%r",
                            type(turn).__name__,
                            type(result).__name__,
                            getattr(turn, "tick", "<noattr>"),
                            getattr(result, "tick", "<noattr>"),
                        )
                        continue
                    get_store().push_snapshot(snap)
                except Exception:  # noqa: BLE001
                    import traceback

                    log.warning(
                        "dashboard:ERROR %s", traceback.format_exc(limit=1)
                    )

        t = threading.Thread(
            target=_worker, name="dashboard-snap-worker", daemon=True
        )
        t.start()
        _snap_worker_started = True


def safe_push_snapshot(
    turn: Any, result: Any, memory: Any, econ_states: Optional[dict] = None
) -> None:
    """非阻塞入队：主循环立刻返回，BFS/path_estimate 在后台做。

    队列 maxsize=1：新帧覆盖旧帧（drop oldest），保证 UI 跟最新 tick，
    且不会因 build 慢而堆积拖垮进程。
    """
    try:
        _ensure_snap_worker()
        item = (turn, result, memory, econ_states)
        try:
            _snap_queue.put_nowait(item)
        except queue.Full:
            try:
                _snap_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                _snap_queue.put_nowait(item)
            except queue.Full:
                # 极端：worker 卡在 put/get 间隙，丢本帧即可
                pass
    except Exception:  # noqa: BLE001
        import traceback

        logging.getLogger("arena_hero_tactic").warning(
            "dashboard:enqueue_ERROR %s", traceback.format_exc(limit=1)
        )


# ============ Module F: Flask App + REST + SSE Routes ============

def create_app(store: "DashboardStore"):
    import importlib
    import os
    import json
    import queue

    flask_mod = importlib.import_module("flask")
    Flask = flask_mod.Flask
    request = flask_mod.request
    jsonify = flask_mod.jsonify
    send_from_directory = flask_mod.send_from_directory
    Response = flask_mod.Response
    stream_with_context = flask_mod.stream_with_context

    static_dir = os.path.join(os.path.dirname(__file__), "dashboard_static")
    app = Flask(__name__, static_folder=static_dir, static_url_path="/static")

    def _no_store(resp):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.route("/")
    def index():
        resp = send_from_directory(static_dir, "index.html")
        return _no_store(resp)

    @app.route("/api/state/latest")
    def api_state_latest():
        s = store.get_latest()
        if s is None:
            # EXP-532325 / EXP-817061：禁止"连接失败回退模拟/伪造数据"
            # 无数据 → 返回明确失败信息 + 等待动作指引，不返回 tick=-1 或 0 这种假占位数据
            resp = jsonify({
                "ok": False,
                "data_source": "none (waiting)",
                "provider": None,
                "tick": None,
                "message": (
                    "暂无来自 arena_hero SDK 的真实快照。\n"
                    "可能原因：\n"
                    "  1) 尚未通过 `python -m bot.main --dashboard` 接入官方 match 对战服务器\n"
                    "  2) 对局尚未开始（仍在匹配阶段）\n"
                    "  3) 官方 SDK 认证失败（检查 ARENA_HERO_API_KEY 环境变量）\n"
                    "请先连接真实对战对局后再刷新。"
                ),
                "remedy": [
                    "export ARENA_HERO_API_KEY=你的官方API_KEY",
                    "python -m bot.main --dashboard",
                    "等待对局开始至少 1 个 tick 后返回此页面刷新"
                ]
            })
            resp.status_code = 503  # Service Unavailable: server up but upstream data missing
            return _no_store(resp)
        # 有真实快照：附加 data_source meta 版本号标识
        payload = dict(s)
        payload.setdefault("data_source", s.get("data_source", "arena_hero_sdk_turn"))
        payload.setdefault("provider", s.get("provider", "official SDK pipeline"))
        payload["ok"] = True
        return _no_store(jsonify(payload))

    @app.route("/api/state/history")
    def api_state_history():
        n = request.args.get("n", 60, type=int)
        n = max(1, min(120, n))
        # 默认 compact=1：去掉 explored_cells/obstacles/path 等重字段，避免 40MB+ 卡死地图
        # full=1 或 compact=0 时返回完整帧（调试用）
        full = request.args.get("full", "0")
        compact_arg = request.args.get("compact", "1")
        use_full = str(full).lower() in ("1", "true", "yes") or str(compact_arg).lower() in (
            "0", "false", "no"
        )
        if use_full:
            frames = store.get_history(n)
            wrapped = []
            for f in frames:
                w = dict(f)
                w.setdefault("data_source", f.get("data_source", "arena_hero_sdk_turn"))
                w.setdefault("provider", f.get("provider", "official SDK pipeline"))
                wrapped.append(w)
        else:
            wrapped = store.get_history_compact(n)
        return _no_store(jsonify({
            "ok": True,
            "data_source": ("arena_hero_sdk_turn" if wrapped else "none (empty)"),
            "frames": wrapped,
            "count": len(wrapped),
            "compact": (not use_full),
        }))

    @app.route("/api/logs")
    def api_logs():
        after = request.args.get("after_ts_ms", 0, type=int)
        limit = request.args.get("limit", 200, type=int)
        limit = max(1, min(1000, limit))
        items = store.get_logs(after_ts_ms=after, limit=limit)
        return jsonify({"ok": True, "data_source": "logging_arena_hero_tactic_handler",
                        "items": items,
                        "has_more": len(items) == limit,
                        "count": len(items)})

    @app.route("/api/logs/stream")
    def api_logs_stream():
        max_events = request.args.get("max_events", None, type=int)

        def _sse_gen():
            produced = 0
            initial = store.get_logs(limit=200)
            for entry in initial:
                yield f"event: log\ndata: {json.dumps(entry)}\n\n"
                produced += 1
                if max_events is not None and produced >= max_events:
                    return

            q: "queue.Queue" = queue.Queue(maxsize=1000)
            callback = lambda e: q.put(e)
            unsub = store.subscribe_logs(callback)
            try:
                while True:
                    if max_events is not None and produced >= max_events:
                        break
                    try:
                        entry = q.get(timeout=15)
                        yield f"event: log\ndata: {json.dumps(entry)}\n\n"
                        produced += 1
                    except queue.Empty:
                        yield ": ping\n\n"
                        produced += 1
            except GeneratorExit:
                pass
            finally:
                unsub()

        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(stream_with_context(_sse_gen()), headers=headers)

    @app.route("/health")
    def health():
        return jsonify({
            "ok": True,
            "data_source": "health_internal_probe",
            "provider": "bot.dashboard DashboardStore",
            "snapshots": len(store._snapshots),
            "snapshots_data_source": "arena_hero_sdk_turn" if store._snapshots else "none (waiting)",
            "logs": len(store._logs),
            "logs_data_source": "logging_arena_hero_tactic_handler" if store._logs else "none",
        })

    return app


# ============ Module G: start_dashboard_server ============

def start_dashboard_server(host: str = "0.0.0.0", port: int = 8765,
                           store: Optional["DashboardStore"] = None,
                           logger: Optional[logging.Logger] = None) -> threading.Thread:
    import importlib
    import time

    try:
        importlib.import_module("flask")
    except ImportError as exc:
        raise ImportError(
            "未安装 flask，无法启动 Dashboard。\n"
            "请先运行： pip install flask>=3.0\n"
            f"原始错误: {exc}"
        ) from exc

    store = store or get_store()
    app = create_app(store)

    def _run_server():
        app.run(host=host, port=port, debug=False, use_reloader=False,
                threaded=True, ssl_context=None)

    server_thread = threading.Thread(
        target=_run_server,
        daemon=True,
        name="arena-dashboard-server",
    )
    server_thread.start()
    time.sleep(0.2)

    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    banner = f"Dashboard 已启动 http://{display_host}:{port} （监听 {host}:{port}，公网可访问）"
    if logger is not None:
        logger.info(banner)
    else:
        print(banner)

    return server_thread
