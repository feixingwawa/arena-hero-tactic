"""Dashboard 后端层单测（严格：仅使用官方 tests.stubs / 真实 MemoryMap / 纯结构机制测试，
不包含任何 tick 递进拟真、不包含任何自编业务对象伪造类）。

分类说明：
- 机制测试（pure mechanism）：仅验证数据结构容量、线程安全、HTTP 合约字段结构、异常吞没。
    这类测试从未声称代表真实对局状态，仅用于保证 Dashboard 基础设施部件正常工作。
- 适配器结构测试（adapter）：使用项目官方 tests.stubs + 真实 bot.memory.MemoryMap
    验证 build_snapshot 从官方 StubTurn/StubUnit 序列化到 JSON dict 的字段兼容。
- 零污染/Argparse：验证禁用 dashboard 时不导入 flask，不影响主 bot 行为。
- 绝不包含：虚拟 tick 推进、自造坐标 Worker/Enemy/Vanguard 移动、自造 obstacles 热图
    连续 tick 资源增长等"拟真对局"行为 —— 所有这类脚本已在 deliverables/ 目录删除。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

import bot.dashboard as dashboard_module


# ========== Fixtures ==========

@pytest.fixture(autouse=True)
def _reset_dashboard_singleton(monkeypatch) -> None:
    """每个测试前重置 DashboardStore 单例，确保状态隔离。"""
    monkeypatch.setattr(dashboard_module, "_store_singleton", None)
    yield


@pytest.fixture
def fresh_store() -> dashboard_module.DashboardStore:
    """机制测试用独立、非单例的 DashboardStore（capacity=120 默认）。"""
    return dashboard_module.DashboardStore(capacity=120, log_capacity=5000)


# ========== 适配器结构测试辅助函数（官方 Stub + 真实 MemoryMap + SimpleNamespace）
#            （无任何自编 DashStub* 业务伪类）

from tests.stubs import StubBeacon, StubCore, StubEnemy, StubTurn, StubUnit
from bot.memory import MemoryMap  # 项目真实内存对象，不是伪造 stub


def _make_adapter_fixture() -> tuple[StubTurn, Any, MemoryMap, dict[str, Any]]:
    """构造 build_snapshot 适配器结构测试输入：

      - Turn/Unit/Core/Enemy/Beacon 全部使用项目官方 tests.stubs（baseline 169 测试同一套）
      - Memory 使用真实 bot.memory.MemoryMap 对象（mark_obstacle / mark_explored 调用真实 API）
      - Decision Result 使用 types.SimpleNamespace（仅结构字段容器，不声称属于任何业务类）
      - Econ WorkerState 使用 SimpleNamespace（target/ring/sector/phase/role/dedicated）
    """
    # 官方 StubTurn：tick=7, resources=25, core(3,7), 2W / 1V / 1R, 1Enemy, 1Beacon(50,50)
    # 注意：StubUnit.unit_type 而不是 .type；id 是 UUID 不是 str；StubCore 没有 hp_max/shield_max
    w1_id = UUID("11111111-1111-4111-8111-111111111111")
    w2_id = UUID("22222222-2222-4222-8222-222222222222")
    v1_id = UUID("33333333-3333-4333-8333-333333333333")
    r1_id = UUID("44444444-4444-4444-8444-444444444444")
    core_obj = StubCore(position=(3, 7), hp=3, shield=1, resources=25)
    workers = [
        StubUnit(id=w1_id, position=(4, 8), hp=2, cargo=1, unit_type="WORKER"),
        StubUnit(id=w2_id, position=(10, -5), hp=2, cargo=0, unit_type="WORKER"),
    ]
    vanguards = [StubUnit(id=v1_id, position=(3, 8), hp=4, unit_type="VANGUARD")]
    rangers = [StubUnit(id=r1_id, position=(4, 7), hp=2, unit_type="RANGER")]
    enemies = [StubEnemy(id=UUID("e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1"),
                         position=(50, 50), hp=2, unit_type="VANGUARD", controlled=False)]
    turn = StubTurn(
        tick=7,
        resources=25,
        core=core_obj,
        workers=workers,
        vanguards=vanguards,
        rangers=rangers,
        visible_enemies=enemies,
        beacon=StubBeacon(position=(50, 50), status="GROUND", carrier_id=None),
    )
    # StubTurn.resource_cells 官方默认是 set[Position]（无amount）。为覆盖 _safe_resource_cells
    # 两种分支使用"结构异构"输入：一个 DashStubResourceObj 风格对象（有 position + amount）
    # + 一个 ((pos), amt) 元组。官方 StubTurn dataclass 允许实例 setattr 自定义字段值
    # （这里不实例化新类，直接复用官方结构 + setattr，非自编业务伪类）
    cell_obj = types.SimpleNamespace(position=(1, 2), amount=3)
    turn.resource_cells = [cell_obj, ((5, 6), 2)]  # type: ignore[assignment]  # 结构异构
    # 使用真实 MemoryMap，调用官方 API 写入 obstacles/explored_chunks/obstacle_blocks
    # mark_explored(pos, tick)：pos 所在 16×16 chunk；explored_chunk_ticks 记首次 tick
    # record_obstacle_block(pos, tick) 第二参是 tick，block_count 每次 +1；
    # 结构测试需要固定 count → 直接写 obstacle_cache（与 runtime 同源结构）
    from bot.memory import ObstacleState

    memory = MemoryMap()
    memory.obstacles.add((2, 3))
    memory.mark_explored((0, 0), 99)   # chunk (0,0)
    memory.mark_explored((16, 0), 95)  # chunk (1,0)
    memory.obstacle_cache[(2, 3)] = ObstacleState(
        pos=(2, 3), first_seen_tick=1, last_seen_tick=77, block_count=77
    )
    memory.obstacle_cache[(4, 4)] = ObstacleState(
        pos=(4, 4), first_seen_tick=1, last_seen_tick=88, block_count=88
    )
    # DecisionResult 使用 SimpleNamespace 结构容器（只存字段，不冒充 bot DecisionPlan）
    result = types.SimpleNamespace(
        tick=7,
        resources=25,
        population=5,
        counts={'WORKER': 2, 'VANGUARD': 1, 'RANGER': 1},
        has_near_threat=False,
        logs=["adapter-structure-fixture"],
        core_action="spawn:WORKER",  # 设置 core_action，验证序列化
    )
    # Econ states: plain dict key by str(id) → SimpleNamespace with target/ring/...
    econ_states: dict[str, Any] = {
        str(w1_id): types.SimpleNamespace(target=(1, 2), ring=3, sector=0,
                                          phase='local', role='HARVEST', dedicated=False)
    }
    return turn, result, memory, econ_states


# ========== 第一组：DashboardStore 环缓冲（纯机制测试，不涉及业务拟真）==========

def test_store_snapshot_ringbuffer_MECHANISM(fresh_store: dashboard_module.DashboardStore) -> None:
    """【纯机制】push 130 个结构帧 {tick:int} → deque 容量=120 → 首帧 tick=10 / 末帧 129。
    不代表任何真实对局，仅验证 deque(maxlen=N) 环形行为。"""
    for t in range(130):
        fresh_store.push_snapshot({"tick": t})
    hist = fresh_store.get_history(200)
    assert len(hist) == 120, f"期望 history len=120，实际 {len(hist)}"
    assert hist[0]["tick"] == 10, f"期望首帧 tick=10，实际 {hist[0]['tick']}"
    assert hist[-1]["tick"] == 129, f"期望末帧 tick=129，实际 {hist[-1]['tick']}"


def test_store_log_ringbuffer_MECHANISM(fresh_store: dashboard_module.DashboardStore) -> None:
    """【纯机制】push 6000 条结构日志 → deque(maxlen=5000)。"""
    for ts in range(6000):
        fresh_store.push_log_entry({"ts_ms": ts, "level": "INFO", "name": "t", "msg": str(ts)})
    logs = fresh_store.get_logs(after_ts_ms=0, limit=9999)
    assert len(logs) == 5000, f"期望 logs len=5000，实际 {len(logs)}"


def test_store_get_latest_empty_and_full_MECHANISM(fresh_store: dashboard_module.DashboardStore) -> None:
    """【纯机制】空→None；push 1结构帧→get_latest tick 返回。"""
    assert fresh_store.get_latest() is None, "空 store 期望 get_latest() is None"
    fresh_store.push_snapshot({"tick": 0})
    latest = fresh_store.get_latest()
    assert latest is not None and latest["tick"] == 0


# ========== 第二组：线程安全（纯机制测试） ==========

def test_store_threadsafe_concurrent_push_get_MECHANISM(fresh_store: dashboard_module.DashboardStore) -> None:
    """【纯机制】8 worker × 250 次并发 push_snapshot（tick=wid*10000+i，不代表真实对局）
    ，最终 history len=120 无重复/损坏。"""
    N_WORKERS = 8
    N_PER_WORKER = 250
    errors: list[Exception] = []

    def _worker(wid: int) -> None:
        try:
            for i in range(N_PER_WORKER):
                fresh_store.push_snapshot({"tick": wid * 10000 + i})
                _ = fresh_store.get_latest()
        except Exception as e:
            errors.append(e)
            raise

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        list(ex.map(_worker, range(N_WORKERS)))

    assert len(errors) == 0, f"并发错误: {errors[:5]}"
    hist = fresh_store.get_history(2000)
    assert len(hist) == 120, f"期望 history len=120，实际 {len(hist)}"
    ticks_seen: set[int] = set()
    for h in hist:
        assert isinstance(h, dict) and "tick" in h
        ticks_seen.add(h["tick"])
    assert len(ticks_seen) == len(hist), "history 不应有重复 tick"


# ========== 第三组：build_snapshot 官方适配器（结构测试，不拟真对局） ==========

def test_build_snapshot_official_stubs_and_memory() -> None:
    """【适配器结构】使用项目官方 StubTurn/StubUnit/StubCore/StubEnemy + 真实 MemoryMap
    序列化字段正确；包含 data_source/provider 元信息；StubCore 缺 hp_max/shield_max
    → 返回 None（禁止 0 兜底伪造）。"""
    turn, result, memory, econ_states = _make_adapter_fixture()

    snap = dashboard_module.build_snapshot(turn, result, memory, econ_states)
    assert snap is not None, "真实结构输入 build_snapshot 不应返回 None"

    # 1. JSON 序列化成功（不是官方 SDK 类型但能传输）
    json_str = json.dumps(snap)
    assert isinstance(json_str, str) and len(json_str) > 100

    # 2. data_source meta（EXP-817061：真实数据标识显式存在）
    assert snap.get("data_source") == "arena_hero_sdk_turn", snap.get("data_source")
    assert "provider" in snap

    # 3. 顶层字段全存在（新API下：resources/population 虽非 None 但来自 namespace tick=7）
    for top_key in ("tick", "core", "resources", "population", "counts", "has_near_threat",
                    "units", "beacon", "resource_cells", "obstacles",
                    "visible_enemies", "memory", "decision_logs"):
        assert top_key in snap, f"缺少顶层字段 {top_key}"

    # 4. tick=7，resources=25，population=5
    assert snap["tick"] == 7
    assert snap["resources"] == 25
    assert snap["population"] == 5

    # 5. core 字段：hp=3, shield=1 存在 + hp_max/shield_max/action 正确
    core = snap["core"]
    assert core is not None
    assert core["x"] == 3 and core["y"] == 7
    assert core["hp"] == 3
    assert core["shield"] == 1
    # 关键：官方 StubCore 未声明 hp_max / shield_max → 必须是 None（禁止 0 兜底伪造）
    assert core["hp_max"] is None, f"StubCore 无 hp_max → 必须 None，实际={core['hp_max']}"
    assert core["shield_max"] is None, f"StubCore 无 shield_max → 必须 None，实际={core['shield_max']}"
    assert core["action"] == "spawn:WORKER"  # 来自 SimpleNamespace.core_action

    # 6. units len=4；W1 econ.target=[1,2], ring=3, phase='local'
    assert len(snap["units"]) == 4
    w1_id = str(UUID("11111111-1111-4111-8111-111111111111"))
    w1 = next(u for u in snap["units"] if u["id"] == w1_id)
    assert w1["type"] == "WORKER"
    assert w1["x"] == 4 and w1["y"] == 8
    assert w1["hp"] == 2 and w1["cargo"] == 1
    assert w1["econ"]["target"] == [1, 2]
    assert w1["econ"]["ring"] == 3
    assert w1["econ"]["phase"] == "local"
    assert w1["econ"]["role"] == "HARVEST"
    assert w1["econ"]["dedicated"] is False

    # 7. unit.type 集合：WORKER/VANGUARD/RANGER
    utypes = {u["type"] for u in snap["units"]}
    assert utypes == {"WORKER", "VANGUARD", "RANGER"}

    # 8. Memory（真实 MemoryMap 对象写入值）：explored_chunks cx,cy,last_seen；
    #    obstacle_blocks x,y,count
    chunks = snap["memory"]["explored_chunks"]
    assert any(c["cx"] == 0 and c["cy"] == 0 and c["last_seen"] == 99 for c in chunks), chunks
    assert any(c["cx"] == 1 and c["cy"] == 0 and c["last_seen"] == 95 for c in chunks), chunks
    oblocks = snap["memory"]["obstacle_blocks"]
    assert any(o["x"] == 2 and o["y"] == 3 and o["count"] == 77 for o in oblocks), oblocks
    assert any(o["x"] == 4 and o["y"] == 4 and o["count"] == 88 for o in oblocks), oblocks
    # obstacles list contains at least (2,3) marked via mark_obstacle
    assert any(o["x"] == 2 and o["y"] == 3 for o in snap["obstacles"]), snap["obstacles"]

    # 9. beacon (50,50) GROUND carrier None
    beacon = snap["beacon"]
    assert beacon is not None
    assert beacon["x"] == 50 and beacon["y"] == 50
    assert beacon["status"] == "GROUND"
    assert beacon["carrier_id"] is None

    # 10. resource_cells：异构 input（SimpleNamespace pos+amt 以及 tuple ((x,y), amt)）→ 2 条
    assert len(snap["resource_cells"]) == 2, snap["resource_cells"]
    assert any(r["x"] == 1 and r["y"] == 2 and r["amount"] == 3 for r in snap["resource_cells"])
    assert any(r["x"] == 5 and r["y"] == 6 and r["amount"] == 2 for r in snap["resource_cells"])

    # 11. visible_enemies 至少 1 条（50,50 hp=2 type=VANGUARD）
    assert len(snap["visible_enemies"]) >= 1
    enemy = snap["visible_enemies"][0]
    assert enemy["x"] == 50 and enemy["y"] == 50
    assert enemy["hp"] == 2
    assert enemy["type"] == "VANGUARD"

    # 12. decision_logs 含 fixture line
    assert "adapter-structure-fixture" in json.dumps(snap.get("decision_logs", []))


def test_build_snapshot_returns_None_for_no_tick_no_result_strictness() -> None:
    """【严格不兜底】turn=None 或 result=None 或 tick 缺失 → build_snapshot 返回 None。
    EXP-817061：禁止连接失败回退到 tick=0 / 0值伪造。

    注：正式管线 result.tick 优先，turn.tick 可作回退（真实 SDK turn 有 tick）；
    仅当 result 与 turn 都无 tick 时才返回 None。
    """
    from tests.stubs import StubTurn
    # Case A: turn=None, result=None → None
    assert dashboard_module.build_snapshot(None, None, None) is None
    # Case B: result 无 tick 但 turn 有真实 tick → 允许用 turn.tick（非伪造 0）
    turn = StubTurn(tick=1)
    result_wo_tick = types.SimpleNamespace(resources=5)
    snap_b = dashboard_module.build_snapshot(turn, result_wo_tick, None)
    assert snap_b is not None and snap_b.get("tick") == 1
    # Case C: result 与 turn 都无可用 tick → 必须 None，不得伪造 tick=0
    turn_wo_tick = types.SimpleNamespace(core=None, workers=[], vanguards=[], rangers=[])
    assert dashboard_module.build_snapshot(turn_wo_tick, result_wo_tick, None) is None, (
        "result/turn 均无 tick 时 build_snapshot 必须返回 None，不得兜底伪造 tick=0"
    )


# ========== 第四组：safe_push_snapshot 异常吞（纯机制） ==========

def test_safe_push_build_error_swallows_MECHANISM(monkeypatch, caplog) -> None:
    """【纯机制】build_snapshot 抛异常 → safe_push_snapshot 不抛；logging WARNING。

    快照已改为后台线程：入队后需短暂等待 worker 消费。
    """
    import time as _time

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard_module, "build_snapshot", _boom)
    with caplog.at_level(logging.WARNING, logger="arena_hero_tactic"):
        dashboard_module.safe_push_snapshot(None, None, None)
        # 后台 worker 异步 build；轮询最多 ~1s
        deadline = _time.monotonic() + 1.5
        while _time.monotonic() < deadline:
            if any(
                "dashboard:ERROR" in r.getMessage()
                for r in caplog.records
                if r.levelno >= logging.WARNING
            ):
                break
            _time.sleep(0.05)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    has_dashboard_error = any("dashboard:ERROR" in r.getMessage() for r in warnings)
    assert has_dashboard_error, (
        f"期望 WARNING 含 'dashboard:ERROR'，实际 {[r.getMessage() for r in warnings]}"
    )


# ========== 第五组：DashboardLogHandler 安全（纯机制） ==========

def test_log_handler_emit_no_propagate_exception_MECHANISM(monkeypatch) -> None:
    """【纯机制】store push_log_entry 抛异常 → handler.emit 不抛。"""
    store_obj = dashboard_module.DashboardStore()
    def _boom_store(*a, **kw):
        raise ValueError("boom")
    monkeypatch.setattr(store_obj, "push_log_entry", _boom_store)
    monkeypatch.setattr(dashboard_module, "get_store", lambda: store_obj)
    handler = dashboard_module.DashboardLogHandler()
    record = logging.LogRecord(name="test", level=logging.INFO, pathname=__file__,
                               lineno=1, msg="hello", args=(), exc_info=None)
    try:
        handler.emit(record)
    except Exception as e:
        pytest.fail(f"handler.emit 不应抛异常: {type(e).__name__}: {e}")


# ========== 第六组：Flask HTTP 合约结构测试（纯机制，数据仅结构占位） ==========

try:
    import flask  # noqa: F401
    _HAS_FLASK = True
except ImportError:
    _HAS_FLASK = False

skip_no_flask = pytest.mark.skipif(not _HAS_FLASK, reason="flask not installed")


@skip_no_flask
def test_api_latest_empty_store_503_message_REAL_STRICTNESS(monkeypatch) -> None:
    """【严格性】空 store（无真实SDK快照）→ HTTP 503，响应含：
    ok=False + data_source='none (waiting)' + message + remedy 列表。
    EXP-532325 / EXP-817061：禁止返回 tick=-1 或 tick=0 假占位。"""
    # 确保单例模式也干净：monkeypatch _store_singleton to None (already by fixture)
    store = dashboard_module.DashboardStore()
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/api/state/latest")
    assert resp.status_code == 503, f"期望 503 明确无数据错误码，实际 {resp.status_code}"
    data = resp.get_json()
    assert data["ok"] is False
    assert data["data_source"] == "none (waiting)", data.get("data_source")
    assert "tick" in data and data["tick"] is None
    assert isinstance(data.get("message"), str) and len(data["message"]) > 50
    assert isinstance(data.get("remedy"), list) and len(data["remedy"]) >= 2


@skip_no_flask
def test_api_latest_200_tick_WRAPPED_MECHANISM() -> None:
    """【结构合约】push 5 结构帧 → 200，json tick=4，ok=True + data_source 显式存在。"""
    store = dashboard_module.DashboardStore()
    for t in range(5):
        store.push_snapshot({"tick": t, "core": {"x": 0, "y": 0}, "units": []})
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/api/state/latest")
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}"
    data = resp.get_json()
    assert data["ok"] is True
    assert data["tick"] == 4, f"期望 tick=4，实际 {data.get('tick')}"
    assert "data_source" in data and "provider" in data


@skip_no_flask
def test_api_history_WRAPPED_FRAMES_MECHANISM() -> None:
    """【结构合约】push 5 → history?n=120 → ok=True, count=5, frames.tick 升序 [0..4]。"""
    store = dashboard_module.DashboardStore()
    for t in range(5):
        store.push_snapshot({"tick": t})
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/api/state/history?n=120")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["count"] == 5, f"期望 count=5，实际 {body.get('count')}"
    frames = body["frames"]
    assert isinstance(frames, list) and len(frames) == 5
    ticks = [it["tick"] for it in frames]
    assert ticks == [0, 1, 2, 3, 4], f"期望升序 [0..4]，实际 {ticks}"
    # data_source 标识每个帧存在
    for f in frames:
        assert "data_source" in f and "provider" in f
    # 默认 compact：去掉 memory/obstacles 等重字段，避免前端卡死
    assert body.get("compact") is True
    for f in frames:
        assert f.get("compact") is True
        assert "memory" not in f
        assert "obstacles" not in f

    # full=1 返回完整帧
    resp_full = client.get("/api/state/history?n=120&full=1")
    body_full = resp_full.get_json()
    assert body_full.get("compact") is False
    assert body_full["count"] == 5


@skip_no_flask
def test_api_logs_WRAPPED_ITEMS_MECHANISM() -> None:
    """【结构合约】push 8 日志 → after=0 limit=5 → ok=True, count=5, has_more=True, data_source。"""
    store = dashboard_module.DashboardStore()
    for i in range(8):
        store.push_log_entry({"ts_ms": 1000 + i, "level": "INFO", "name": "t", "msg": f"m{i}"})
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/api/logs?after_ts_ms=0&limit=5")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 5
    assert len(data["items"]) == 5
    assert data["has_more"] is True
    assert data["data_source"] == "logging_arena_hero_tactic_handler"


@skip_no_flask
def test_health_ENDPOINT_META_EXPLICIT_MECHANISM() -> None:
    """【结构合约】/health 返回 ok/snapshots/logs 计数 + 两个 *_data_source 标识。"""
    store = dashboard_module.DashboardStore()
    store.push_snapshot({"tick": 7})
    store.push_log_entry({"ts_ms": 1, "level": "INFO", "name": "t", "msg": "hi"})
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["snapshots"] == 1
    assert body["logs"] == 1
    assert body["snapshots_data_source"] == "arena_hero_sdk_turn"
    assert body["logs_data_source"] == "logging_arena_hero_tactic_handler"
    assert body["data_source"] == "health_internal_probe"


@skip_no_flask
def test_api_logs_stream_initial_batch_MECHANISM() -> None:
    """【纯机制】push 8 条日志 → SSE stream 首块 event: log 次数 ≥ 8。"""
    store = dashboard_module.DashboardStore()
    for i in range(8):
        store.push_log_entry({"ts_ms": 1000 + i, "level": "INFO", "name": "t", "msg": f"m{i}"})
    app = dashboard_module.create_app(store)
    client = app.test_client()
    resp = client.get("/api/logs/stream?max_events=16", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    chunk_bytes = bytearray()
    try:
        for idx, piece in enumerate(resp.response):
            if isinstance(piece, (bytes, bytearray)):
                chunk_bytes.extend(bytes(piece))
            else:
                chunk_bytes.extend(str(piece).encode("utf-8"))
            if idx >= 32 or len(chunk_bytes) >= 8192:
                break
    finally:
        try: resp.close()
        except Exception: pass
    text = chunk_bytes.decode("utf-8", errors="replace")[:4096]
    count = text.count("event: log")
    assert count >= 8, f"`event: log` 应≥8 次，实际 {count} 次。head={text[:300]!r}"


@skip_no_flask
def test_start_dashboard_server_missing_flask_message_MECHANISM(monkeypatch) -> None:
    """【纯机制】flask import 失败 → 抛 ImportError 提示 pip install flask>=3.0。"""
    import importlib
    real_import = importlib.import_module
    def _fake(name, *a, **kw):
        if name == "flask":
            raise ImportError("No module named 'flask'")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(importlib, "import_module", _fake)
    with pytest.raises(ImportError) as exc_info:
        dashboard_module.start_dashboard_server(host="127.0.0.1", port=9998,
                                                 store=dashboard_module.DashboardStore())
    assert "pip install flask>=3.0" in str(exc_info.value)


# ========== 第七组：CLI 零污染 & argparse（纯机制，不启动 Dashboard） ==========

def test_cli_argparse_dashboard_flags_MECHANISM() -> None:
    """【纯机制】argparse flags parse → dashboard=True/False + port 正确默认。"""
    from bot.main import parse_args
    args_with = parse_args(["--dashboard", "--dashboard-port", "9999"])
    assert args_with.dashboard is True
    assert args_with.dashboard_port == 9999
    args_default = parse_args([])
    assert args_default.dashboard is False
    assert args_default.dashboard_port == 8765


def test_zero_pollution_no_flag_doesnt_import_flask_STRICTNESS() -> None:
    """【严格零污染】subprocess 不启用 dashboard 时 sys.modules 无 flask 前缀。"""
    code = (
        "import sys; "
        "import bot.main; "
        "print(any(k.startswith('flask') for k in sys.modules))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=60,
    )
    assert proc.returncode == 0, f"非零退出码：stderr={proc.stderr!r} stdout={proc.stdout!r}"
    lines = (proc.stdout or "").strip().splitlines()
    assert lines, f"空 stdout，stderr={proc.stderr!r}"
    assert lines[-1].strip() == "False", (
        f"未启用 dashboard 时 flask 不应存在于 sys.modules，末行={lines[-1]!r}"
    )


def test_requirements_flask_is_commented_out_STRICTNESS() -> None:
    """【严格零污染】requirements.txt 含 'flask' 的行必须全部注释掉或 optional 说明。"""
    req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
    text = req_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    flask_lines = [ln.rstrip() for ln in lines if "flask" in ln.lower()]
    assert len(flask_lines) > 0, "requirements.txt 中找不到含 'flask' 的注释声明行"
    for ln in flask_lines:
        stripped = ln.lstrip()
        ok = stripped.startswith("#") or "optional" in stripped.lower() or "dashboard" in stripped.lower()
        assert ok, (f"requirements.txt 含未注释启用的 flask 依赖行（违反零污染）: {ln!r}\n"
                    "请改为注释 + '# optional: dashboard' 声明")


# ========== 路径估计：真实绕障 / 不穿障 ==========

def test_bfs_path_detours_around_wall() -> None:
    """中间竖墙时 BFS 必须绕行，路点不得落在障碍格。"""
    from bot.dashboard import _bfs_path

    origin = (0, 0)
    target = (4, 0)
    # 竖墙 x=2,y=-2..2 阻断直线
    obstacles = {(2, y) for y in range(-2, 3)}
    wps, blocked = _bfs_path(origin, target, obstacles, max_expand=2000)
    assert wps is not None, "应找到绕障路径"
    cells = [(int(p[0]), int(p[1])) for p in wps]
    for c in cells:
        assert c not in obstacles, f"路点穿障 {c}"
    assert cells[0] == origin
    assert cells[-1] == target
    # 必须绕行：出现 y!=0 的点
    assert any(y != 0 for _, y in cells), f"未绕行: {cells}"
    assert blocked, "应记录触碰墙体"


def test_bfs_path_never_enters_blocked_goal() -> None:
    """终点在障碍上时停在邻接自由格，不踏入终点障。"""
    from bot.dashboard import _bfs_path

    origin = (0, 0)
    target = (3, 0)
    obstacles = {(3, 0)}
    wps, blocked = _bfs_path(origin, target, obstacles, max_expand=500)
    assert wps is not None
    cells = [(int(p[0]), int(p[1])) for p in wps]
    assert target not in cells
    assert all(c not in obstacles for c in cells)
    assert abs(cells[-1][0] - 3) + abs(cells[-1][1] - 0) == 1
    # 终点障应记入 blocked 供前端高亮
    assert any(tuple(b) == (3, 0) or list(b) == [3, 0] for b in blocked)


def test_build_path_estimate_no_through_obstacles() -> None:
    """_build_path_estimate 路点不得与障碍重合（除起点异常叠障）。"""
    from bot.dashboard import _build_path_estimate

    origin = (0, 0)
    target = [6, 0]
    obstacles = {(2, 0), (3, 0), (4, 0)}  # 直线全堵
    est = _build_path_estimate(origin, target, obstacles)
    assert est is not None
    assert est.get("planned") in {"astar", "bfs", "reconstruct", "none", "runtime"}
    wps = est.get("waypoints") or []
    for p in wps:
        t = (int(p[0]), int(p[1]))
        if t == origin:
            continue
        assert t not in obstacles, f"estimate 穿障 {t} planned={est.get('planned')}"
    # 有绕行时应为 astar/bfs 且 steps > manhattan
    if est.get("planned") in {"astar", "bfs"} and not est.get("partial"):
        assert est["steps"] > 6 or abs(wps[-1][1]) > 0


def test_build_path_estimate_clear_is_bfs_shortest() -> None:
    """无障碍时 A*/BFS 为曼哈顿最短，planned=astar（或 bfs 兜底），非 approx。"""
    from bot.dashboard import _build_path_estimate

    est = _build_path_estimate((1, 1), [4, 3], set())
    assert est is not None
    assert est["planned"] in {"astar", "bfs"}
    assert est.get("approx") is False
    assert est["steps"] == 5  # |3|+|2|
    wps = est["waypoints"]
    assert wps[0] == [1, 1]
    assert wps[-1] == [4, 3]


def test_build_path_estimate_prefers_runtime_route() -> None:
    """有 runtime_route 时 planned=runtime，黄线与执行航点一致。"""
    from bot.dashboard import _build_path_estimate

    # 故意给一条绕行 runtime 路线（与直线不同）
    runtime = [[1, 0], [1, 1], [2, 1], [3, 1], [3, 0]]
    est = _build_path_estimate(
        (0, 0), [3, 0], set(), runtime_route=runtime
    )
    assert est is not None
    assert est["planned"] == "runtime"
    wps = est["waypoints"]
    assert wps[0] == [0, 0]
    # 必须包含 runtime 拐点，不能被重算成直线
    cells = [(p[0], p[1]) for p in wps]
    assert (1, 1) in cells and (2, 1) in cells
    for c in cells[1:]:
        assert c not in {(9, 9)}  # smoke
