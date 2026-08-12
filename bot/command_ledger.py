"""本 tick 指令真源（Command Ledger）。

Dashboard / 调试应以本模块记录的 action 为准，而不是仅 phase 意图。
在 decide() 开头 clear + instrument_turn，使 move/deposit/heal 等 SDK 调用自动入账。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from bot.pathing import NAME_TO_DELTA, Position, add_pos

_lock = threading.RLock()
_tick: int = 0
_commands: list["CommandRecord"] = []
# 里程碑 C：上一 tick 指令（跨 tick 对照）
_prev_tick: Optional[int] = None
_prev_commands: list["CommandRecord"] = []
# 已插桩对象 id，避免重复包装
_instrumented_ids: set[int] = set()


@dataclass
class CommandRecord:
    """单条已排队/已调用的单位或 Core 指令。"""

    unit_id: str
    tick: int
    action: str  # move|harvest|deposit|heal|sweep|shoot|wait|spawn|repair|...
    direction: Optional[str] = None
    next_cell: Optional[tuple[int, int]] = None
    target_cell: Optional[tuple[int, int]] = None
    phase: Optional[str] = None
    role: Optional[str] = None
    source: str = "unknown"  # economy|combat|core|worker|vanguard|ranger
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.next_cell is not None:
            d["next_cell"] = [int(self.next_cell[0]), int(self.next_cell[1])]
        if self.target_cell is not None:
            d["target_cell"] = [int(self.target_cell[0]), int(self.target_cell[1])]
        return d


def clear(tick: int = 0) -> None:
    """新决策 tick：归档旧指令后清空。"""
    global _tick, _commands, _prev_tick, _prev_commands, _instrumented_ids
    with _lock:
        if _commands:
            _prev_tick = _tick
            _prev_commands = list(_commands)
        _tick = int(tick or 0)
        _commands = []
        _instrumented_ids = set()


def reset_all() -> None:
    """测试用：清空当前与上一 tick。"""
    global _tick, _commands, _prev_tick, _prev_commands, _instrumented_ids
    with _lock:
        _tick = 0
        _commands = []
        _prev_tick = None
        _prev_commands = []
        _instrumented_ids = set()


def record(
    unit_id: Any,
    action: str,
    *,
    tick: Optional[int] = None,
    direction: Optional[str] = None,
    from_pos: Optional[Position] = None,
    next_cell: Optional[Position] = None,
    target_cell: Optional[Position] = None,
    phase: Optional[str] = None,
    role: Optional[str] = None,
    source: str = "unknown",
    meta: Optional[dict[str, Any]] = None,
) -> CommandRecord:
    """手动写入一条指令（插桩失败时的兜底）。"""
    global _commands
    dname = _direction_name(direction) if direction is not None else None
    ncell = next_cell
    if ncell is None and from_pos is not None and dname:
        delta = NAME_TO_DELTA.get(dname) or NAME_TO_DELTA.get(dname.upper())
        if delta is not None:
            ncell = add_pos((int(from_pos[0]), int(from_pos[1])), delta)
    rec = CommandRecord(
        unit_id=str(unit_id) if unit_id is not None else "",
        tick=int(_tick if tick is None else tick),
        action=str(action),
        direction=dname,
        next_cell=(int(ncell[0]), int(ncell[1])) if ncell is not None else None,
        target_cell=(
            (int(target_cell[0]), int(target_cell[1]))
            if target_cell is not None
            else None
        ),
        phase=str(phase) if phase is not None else None,
        role=str(role) if role is not None else None,
        source=str(source or "unknown"),
        meta=dict(meta or {}),
    )
    with _lock:
        _commands.append(rec)
    return rec


def get_commands() -> list[CommandRecord]:
    with _lock:
        return list(_commands)


def get_commands_dicts() -> list[dict[str, Any]]:
    return [c.to_dict() for c in get_commands()]


def get_prev_commands() -> list[CommandRecord]:
    with _lock:
        return list(_prev_commands)


def get_prev_tick() -> Optional[int]:
    with _lock:
        return _prev_tick


def get_prev_commands_dicts() -> list[dict[str, Any]]:
    return [c.to_dict() for c in get_prev_commands()]


def commands_by_unit() -> dict[str, CommandRecord]:
    """每单位保留最后一条指令（通常每 tick 一动作）。"""
    out: dict[str, CommandRecord] = {}
    for c in get_commands():
        out[c.unit_id] = c
    return out


def current_tick() -> int:
    with _lock:
        return _tick


# 官方/本仓库 pathing 用 UP/DOWN/LEFT/RIGHT；兼容 N/S/E/W 与别名
_DIR_ALIASES: dict[str, str] = {
    "N": "UP", "NORTH": "UP", "U": "UP", "UP": "UP",
    "S": "DOWN", "SOUTH": "DOWN", "D": "DOWN", "DOWN": "DOWN",
    "E": "RIGHT", "EAST": "RIGHT", "R": "RIGHT", "RIGHT": "RIGHT",
    "W": "LEFT", "WEST": "LEFT", "L": "LEFT", "LEFT": "LEFT",
}


def _direction_name(direction: Any) -> Optional[str]:
    if direction is None:
        return None
    raw: Optional[str] = None
    if isinstance(direction, str):
        raw = direction.strip()
    else:
        name = getattr(direction, "name", None)
        if name is not None:
            raw = str(name)
        else:
            val = getattr(direction, "value", None)
            if val is not None:
                raw = str(val)
            else:
                raw = str(direction).strip()
    if not raw:
        return None
    key = raw.upper()
    return _DIR_ALIASES.get(key, key)


def _as_pos(pos: Any) -> Optional[Position]:
    if pos is None:
        return None
    try:
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            return (int(pos[0]), int(pos[1]))
        x, y = getattr(pos, "x", None), getattr(pos, "y", None)
        if x is not None and y is not None:
            return (int(x), int(y))
        if hasattr(pos, "__iter__"):
            seq = list(pos)
            if len(seq) >= 2:
                return (int(seq[0]), int(seq[1]))
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _unit_pos(unit: Any) -> Optional[Position]:
    return _as_pos(getattr(unit, "position", None))


def instrument_unit(unit: Any, *, tick: int, source: str) -> None:
    """包装单位动作方法，调用时自动写入 ledger。"""
    if unit is None:
        return
    uid_obj = id(unit)
    with _lock:
        if uid_obj in _instrumented_ids:
            return
        _instrumented_ids.add(uid_obj)

    uid = str(getattr(unit, "id", "") or "")
    src = str(source or "unknown")

    def _wrap(method_name: str, action: str, *, is_move: bool = False) -> None:
        if not hasattr(unit, method_name):
            return
        orig = getattr(unit, method_name)
        if not callable(orig):
            return
        # 已包装则跳过
        if getattr(orig, "_ledger_wrapped", False):
            return

        def _fn(*args: Any, **kwargs: Any) -> Any:
            direction = None
            next_cell = None
            target_cell = None
            meta: dict[str, Any] = {}
            pos = _unit_pos(unit)
            if is_move and args:
                direction = _direction_name(args[0])
                if pos is not None and direction:
                    delta = NAME_TO_DELTA.get(direction)
                    if delta is not None:
                        next_cell = add_pos(pos, delta)
            elif action == "sweep" and args:
                direction = _direction_name(args[0])
            elif action == "shoot" and args:
                tgt = args[0]
                meta["shoot_target_id"] = str(getattr(tgt, "id", tgt))
                tpos = _as_pos(getattr(tgt, "position", None))
                if tpos is not None:
                    target_cell = tpos
            elif action == "spawn" and args:
                meta["spawn_type"] = str(
                    getattr(args[0], "name", None)
                    or getattr(args[0], "value", None)
                    or args[0]
                )
            record(
                uid,
                action,
                tick=tick,
                direction=direction,
                from_pos=pos,
                next_cell=next_cell,
                target_cell=target_cell,
                source=src,
                meta=meta or None,
            )
            return orig(*args, **kwargs)

        _fn._ledger_wrapped = True  # type: ignore[attr-defined]
        try:
            setattr(unit, method_name, _fn)
        except Exception:
            pass

    _wrap("move", "move", is_move=True)
    _wrap("deposit", "deposit")
    _wrap("heal", "heal")
    _wrap("harvest", "harvest")
    _wrap("wait", "wait")
    _wrap("sweep", "sweep")
    _wrap("shoot", "shoot")
    _wrap("spawn", "spawn")
    _wrap("repair", "repair")
    _wrap("repair_shield", "repair_shield")


def instrument_turn(turn: Any, tick: int) -> None:
    """对本 tick 全部单位与 Core 插桩。"""
    t = int(tick or 0)
    for w in list(getattr(turn, "workers", None) or ()):
        instrument_unit(w, tick=t, source="worker")
    for v in list(getattr(turn, "vanguards", None) or ()):
        instrument_unit(v, tick=t, source="vanguard")
    for r in list(getattr(turn, "rangers", None) or ()):
        instrument_unit(r, tick=t, source="ranger")
    core = getattr(turn, "core", None)
    if core is not None:
        instrument_unit(core, tick=t, source="core")


def enrich_from_intents(intents: Optional[dict[str, Any]]) -> None:
    """用 economy intent 的 phase/role/target 回填本 tick 指令（不改变 action）。"""
    if not intents:
        return
    with _lock:
        for rec in _commands:
            wi = intents.get(rec.unit_id)
            if wi is None:
                continue
            if rec.phase is None:
                ph = getattr(wi, "phase", None)
                if ph is not None:
                    rec.phase = str(ph)
            if rec.role is None:
                role = getattr(wi, "role", None)
                if role is not None:
                    rec.role = str(role)
            if rec.target_cell is None:
                tgt = getattr(wi, "target", None)
                if tgt is not None:
                    try:
                        rec.target_cell = (int(tgt[0]), int(tgt[1]))
                    except (TypeError, ValueError, IndexError):
                        pass
