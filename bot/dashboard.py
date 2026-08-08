"""Dashboard 后端存储层：快照/日志环缓冲、SSE 订阅、build_snapshot。单文件模块。"""
from __future__ import annotations
import collections
import logging
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


def _build_path_estimate(
    origin: tuple[int, int],
    target: Optional[list],
    obstacles: set[tuple[int, int]],
) -> Optional[dict]:
    """用 pathing.reconstruct_path 生成路径可视化数据（不修改 memory）。"""
    if target is None or not isinstance(target, (list, tuple)) or len(target) < 2:
        return None
    try:
        tx, ty = int(target[0]), int(target[1])
    except (TypeError, ValueError):
        return None
    try:
        from bot.pathing import reconstruct_path

        steps, blocked, waypoints = reconstruct_path(
            (int(origin[0]), int(origin[1])),
            (tx, ty),
            obstacles,
            memory=None,  # 禁止 dry-run 写回 memory
            max_steps=64,
        )
        return {
            "steps": int(steps),
            "waypoints": [[int(p[0]), int(p[1])] for p in waypoints],
            "blocked": [[int(p[0]), int(p[1])] for p in blocked],
            "destination": [tx, ty],
        }
    except Exception:
        return None


def _build_unit(
    u: Any,
    econ_states: Optional[dict],
    obstacles: Optional[set[tuple[int, int]]] = None,
) -> dict:
    uid = str(getattr(u, "id", ""))
    pos_obj = getattr(u, "position", None)
    if pos_obj is not None:
        ux, uy = _as_position(pos_obj)
    else:
        ux, uy = int(getattr(u, "x", 0)), int(getattr(u, "y", 0))
    econ: dict
    if econ_states and uid in econ_states:
        ws = econ_states[uid]
        tgt = getattr(ws, "target", None)
        tgt_list: Optional[list] = None
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
        econ = {"target": tgt_list,
                "ring": int(ring) if ring is not None else None,
                "sector": int(sector) if sector is not None else None,
                "phase": str(phase) if phase is not None else None,
                "role": str(role) if role is not None else None,
                "dedicated": bool(dedicated) if dedicated is not None else None}
    else:
        econ = {"target": None, "ring": None, "sector": None,
                "phase": None, "role": None, "dedicated": None}
        tgt_list = None
    path_estimate = None
    if tgt_list is not None:
        path_estimate = _build_path_estimate(
            (int(ux), int(uy)), tgt_list, obstacles or set()
        )
    return {"id": uid,
            "type": str(getattr(u, "type", getattr(u, "unit_type", ""))),
            "x": int(ux), "y": int(uy),
            "hp": int(getattr(u, "hp", 0) or 0),
            "cargo": int(getattr(u, "cargo", 0) or 0),
            "econ": econ, "path_estimate": path_estimate}


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
    units_list = []
    for u in all_units:
        try:
            units_list.append(_build_unit(u, econ_states, path_obstacles))
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
        "memory": {
            "explored_chunks": _safe_explored_chunks(memory),
            "explored_cells": _safe_explored_cells(memory),
            "obstacle_blocks": _safe_obstacle_blocks(memory),
        },
    }


def safe_push_snapshot(turn: Any, result: Any, memory: Any, econ_states: Optional[dict] = None) -> None:
    try:
        snap = build_snapshot(turn, result, memory, econ_states)
        if snap is None:
            # 临时诊断：记录 build_snapshot 为何返回 None（真实SDK链路排查用）
            import logging as _lg
            _lg.getLogger("arena_hero_tactic").warning(
                "dashboard:build_snapshot=None turn=%s result=%s turn_tick=%r result_tick=%r",
                type(turn).__name__, type(result).__name__,
                getattr(turn, "tick", "<noattr>"), getattr(result, "tick", "<noattr>"))
            return
        get_store().push_snapshot(snap)
    except Exception:  # noqa: BLE001
        import logging, traceback
        logging.getLogger("arena_hero_tactic").warning(
            "dashboard:ERROR %s", traceback.format_exc(limit=1))


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
        frames = store.get_history(n)
        # 为历史帧也补全 data_source meta（历史数据可能来自老版本 build_snapshot）
        wrapped = []
        for f in frames:
            w = dict(f)
            w.setdefault("data_source", f.get("data_source", "arena_hero_sdk_turn"))
            w.setdefault("provider", f.get("provider", "official SDK pipeline"))
            wrapped.append(w)
        return _no_store(jsonify({
            "ok": True,
            "data_source": ("arena_hero_sdk_turn" if wrapped else "none (empty)"),
            "frames": wrapped,
            "count": len(wrapped),
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

def start_dashboard_server(host: str = "127.0.0.1", port: int = 8765,
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

    banner = f"Dashboard 已启动 http://{host}:{port}"
    if logger is not None:
        logger.info(banner)
    else:
        print(banner)

    return server_thread
