"""战斗与防守模块：威胁评估、防守圈、sweep/shoot、治疗。"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.pathing import (
    NAME_TO_DELTA,
    Position,
    add_pos,
    cells_toward_ring,
    clamp_step_toward,
    defense_ring_slots,
    direction_between,
    is_adjacent,
    is_in_range_cardinal_or_diag,
    manhattan,
    nearest,
)
from bot.roles import Role, RolePlan, _as_position


def _pick_unused_slot(
    slot_candidates: list[tuple[int,int]],
    taken_positions: set[tuple[int,int]],
) -> Optional[tuple[int,int]]:
    """从 slot_candidates 中选第一个未被 taken 的；若全部占用则横向 +1 相位偏移再找一轮。"""
    for slot in slot_candidates:
        if slot not in taken_positions:
            return slot
    # 偏移一轮
    for slot in slot_candidates:
        alt = (slot[0]+1, slot[1])
        if alt not in taken_positions: return alt
        alt = (slot[0], slot[1]+1)
        if alt not in taken_positions: return alt
    return slot_candidates[0] if slot_candidates else None


def _obstacle_cells(turn: Any) -> set[Position]:
    cells = getattr(turn, "obstacle_cells", None)
    if cells is None:
        return set()
    return {_as_position(c) for c in cells}


def _leave_core_step(
    unit: Any,
    pos: Position,
    core_position: Position,
    obstacles: set[Position],
    *,
    preferred: Optional[Position] = None,
    uid: Any = None,
    kind: str = "unit",
    logs: Optional[list[str]] = None,
) -> bool:
    """非工人与 Core 同格时强制移开一格。

    Returns:
        True 表示已处理（已 move/wait），调用方应 ``continue``。
        False 表示未重叠，无需处理。
    """
    if pos != core_position:
        return False
    blocked: set[Position] = set(obstacles)
    direction: Optional[str] = None
    if preferred is not None and preferred != pos:
        direction = clamp_step_toward(pos, preferred, blocked)
    if direction is None:
        for name, delta in NAME_TO_DELTA.items():
            nxt = add_pos(pos, delta)
            if nxt not in blocked:
                direction = name
                break
    tag = f"{kind}:{uid}" if uid is not None else kind
    out = logs if logs is not None else []
    if direction and hasattr(unit, "move"):
        unit.move(_resolve_direction(direction))
        out.append(f"{tag}:leave_core:{direction}")
        return True
    if hasattr(unit, "wait"):
        unit.wait()
    out.append(f"{tag}:leave_core:blocked")
    return True


def _resolve_direction(direction_name: str) -> Any:
    try:
        from arena_hero import Direction  # type: ignore

        return Direction[direction_name]
    except Exception:
        return direction_name


def _enemies(turn: Any) -> list[Any]:
    return list(getattr(turn, "visible_enemies", None) or ())


def assess_threats(
    turn: Any,
    core_position: Position,
    config: TacticConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """威胁评估摘要。"""
    enemies = _enemies(turn)
    positions = [_as_position(e.position) for e in enemies]
    near = [p for p in positions if manhattan(p, core_position) <= config.threat_radius]
    adjacent_to_core = [p for p in positions if manhattan(p, core_position) <= 1]
    return {
        "count": len(positions),
        "positions": positions,
        "near": near,
        "adjacent_to_core": adjacent_to_core,
        "has_near_threat": len(near) > 0,
        "core_under_fire": len(adjacent_to_core) > 0,
    }


def _nearest_enemy_to(
    origin: Position,
    enemies: list[Any],
) -> Optional[Any]:
    if not enemies:
        return None
    return min(
        enemies,
        key=lambda e: (manhattan(origin, _as_position(e.position)), str(getattr(e, "id", ""))),
    )


def command_vanguards(
    turn: Any,
    role_plan: RolePlan,
    config: TacticConfig = DEFAULT_CONFIG,
    core_position: Optional[Position] = None,
) -> list[str]:
    """Vanguard：邻格 sweep，否则守环/朝威胁移动。"""
    logs: list[str] = []
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            return logs
        core_position = _as_position(core.position)

    enemies = _enemies(turn)
    obstacles = _obstacle_cells(turn)
    vanguards = list(getattr(turn, "vanguards", None) or ())
    slots = defense_ring_slots(
        core_position,
        config.defense_radius,
        count=max(len(vanguards), 1),
        phase=int(getattr(turn, "tick", 0) or 0) % 8,
    )
    taken: set[tuple[int,int]] = set()

    for i, v in enumerate(vanguards):
        uid = v.id
        assignment = role_plan.get(uid)
        pos = _as_position(v.position)

        # 治疗回城（允许短暂与 Core 同格 heal；下 tick 非 HEAL 会 leave_core）
        if assignment and assignment.role == Role.HEAL:
            if pos == core_position and hasattr(v, "heal"):
                v.heal()
                logs.append(f"vanguard:{uid}:heal")
            else:
                direction = clamp_step_toward(pos, core_position, obstacles)
                if direction and hasattr(v, "move"):
                    v.move(_resolve_direction(direction))
                    logs.append(f"vanguard:{uid}:to_heal:{direction}")
            continue

        # 禁止与 Core 重叠：立即移开（优先朝防守环）
        ring_pref = cells_toward_ring(pos, core_position, config.defense_radius)
        if _leave_core_step(
            v,
            pos,
            core_position,
            obstacles,
            preferred=ring_pref,
            uid=uid,
            kind="vanguard",
            logs=logs,
        ):
            continue

        # 邻格有敌人 → sweep
        adjacent_enemies = [
            e for e in enemies if is_adjacent(pos, _as_position(e.position))
        ]
        if adjacent_enemies:
            target = _nearest_enemy_to(pos, adjacent_enemies)
            if target is not None:
                tpos = _as_position(target.position)
                direction = direction_between(pos, tpos)
                if direction and hasattr(v, "sweep"):
                    v.sweep(_resolve_direction(direction))
                    logs.append(f"vanguard:{uid}:sweep:{direction}")
                    continue

        # 有近威胁：朝最近威胁移动（但不过度远离 Core）
        near_enemies = [
            e
            for e in enemies
            if manhattan(_as_position(e.position), core_position) <= config.threat_radius
        ]
        if near_enemies:
            target = _nearest_enemy_to(pos, near_enemies)
            if target is not None:
                tpos = _as_position(target.position)
                # 限制：不要跑出 threat_radius + 2
                if manhattan(pos, core_position) <= config.threat_radius + 2:
                    direction = clamp_step_toward(pos, tpos, obstacles)
                    if direction and hasattr(v, "move"):
                        v.move(_resolve_direction(direction))
                        logs.append(f"vanguard:{uid}:intercept:{direction}")
                        continue

        # 默认：守在防守环
        base_slot = cells_toward_ring(
            pos, core_position, config.defense_radius
        )
        slot_candidates = list(slots) if slots else [base_slot]
        slot = _pick_unused_slot(slot_candidates, taken)
        if slot is not None:
            taken.add(slot)
        else:
            slot = base_slot
        if pos == slot:
            if hasattr(v, "wait"):
                v.wait()
            logs.append(f"vanguard:{uid}:hold")
        else:
            direction = clamp_step_toward(pos, slot, obstacles)
            if direction and hasattr(v, "move"):
                v.move(_resolve_direction(direction))
                logs.append(f"vanguard:{uid}:to_ring:{direction}")
            elif hasattr(v, "wait"):
                v.wait()
                logs.append(f"vanguard:{uid}:wait")

    return logs


def command_rangers(
    turn: Any,
    role_plan: RolePlan,
    config: TacticConfig = DEFAULT_CONFIG,
    core_position: Optional[Position] = None,
) -> list[str]:
    """Ranger：射程内 shoot，否则守外圈。"""
    logs: list[str] = []
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            return logs
        core_position = _as_position(core.position)

    enemies = _enemies(turn)
    obstacles = _obstacle_cells(turn)
    rangers = list(getattr(turn, "rangers", None) or ())
    slots = defense_ring_slots(
        core_position,
        config.ranger_radius,
        count=max(len(rangers), 1),
        phase=(int(getattr(turn, "tick", 0) or 0) + 3) % 8,
    )
    ranger_fire_ledger: dict = {}
    taken: set[tuple[int,int]] = set()

    for i, r in enumerate(rangers):
        uid = r.id
        assignment = role_plan.get(uid)
        pos = _as_position(r.position)

        if assignment and assignment.role == Role.HEAL:
            if pos == core_position and hasattr(r, "heal"):
                r.heal()
                logs.append(f"ranger:{uid}:heal")
            else:
                direction = clamp_step_toward(pos, core_position, obstacles)
                if direction and hasattr(r, "move"):
                    r.move(_resolve_direction(direction))
                    logs.append(f"ranger:{uid}:to_heal:{direction}")
            continue

        # 禁止与 Core 重叠：立即移开（优先朝 Ranger 环）
        ring_pref = cells_toward_ring(pos, core_position, config.ranger_radius)
        if _leave_core_step(
            r,
            pos,
            core_position,
            obstacles,
            preferred=ring_pref,
            uid=uid,
            kind="ranger",
            logs=logs,
        ):
            continue

        # 射程内敌人 → shoot
        shootable = [
            e
            for e in enemies
            if is_in_range_cardinal_or_diag(pos, _as_position(e.position))
        ]
        if shootable:
            # 优先打离 Core 近、HP 低的
            def shoot_key(e: Any) -> tuple:
                ep = _as_position(e.position)
                hp = int(getattr(e, "hp", 99) or 99)
                return (manhattan(ep, core_position), hp, str(getattr(e, "id", "")))

            sorted_enemies = sorted(shootable, key=shoot_key)
            target = None
            for candidate in sorted_enemies:
                enemy_id_str = str(getattr(candidate, "id", ""))
                enemy_hp = int(getattr(candidate, "hp", 99) or 99)
                expected = ranger_fire_ledger.get(enemy_id_str, 0)
                if expected + 1 > enemy_hp:
                    logs.append(f"ranger:{uid}:shoot_avoid_overkill:enemy={enemy_id_str}")
                    continue
                target = candidate
                ranger_fire_ledger[enemy_id_str] = expected + 1
                break

            if target is not None:
                if hasattr(r, "shoot"):
                    try:
                        r.shoot(target)
                        logs.append(f"ranger:{uid}:shoot:{getattr(target, 'id', target)}")
                    except TypeError:
                        # stub 可能签名不同，退回 shoot_cell
                        if hasattr(r, "shoot_cell"):
                            r.shoot_cell(_as_position(target.position))
                            logs.append(f"ranger:{uid}:shoot_cell:{_as_position(target.position)}")
                    continue
                if hasattr(r, "shoot_cell"):
                    r.shoot_cell(_as_position(target.position))
                    logs.append(f"ranger:{uid}:shoot_cell:{_as_position(target.position)}")
                    continue

        # 可见威胁但不在射程：微调位置（仍靠近防守环）
        if enemies:
            target_e = _nearest_enemy_to(pos, enemies)
            if target_e is not None:
                tpos = _as_position(target_e.position)
                # 尝试走到能射击的位置：先向威胁靠近一格，但不远离 Core 太多
                if manhattan(pos, core_position) <= config.ranger_radius + 2:
                    direction = clamp_step_toward(pos, tpos, obstacles)
                    if direction and hasattr(r, "move"):
                        wpos = pos  # 仅一步
                        r.move(_resolve_direction(direction))
                        logs.append(f"ranger:{uid}:reposition:{direction}")
                        continue

        # 默认守外圈
        base_slot = cells_toward_ring(
            pos, core_position, config.ranger_radius
        )
        slot_candidates = list(slots) if slots else [base_slot]
        slot = _pick_unused_slot(slot_candidates, taken)
        if slot is not None:
            taken.add(slot)
        else:
            slot = base_slot
        if pos == slot:
            if hasattr(r, "wait"):
                r.wait()
            logs.append(f"ranger:{uid}:hold")
        else:
            direction = clamp_step_toward(pos, slot, obstacles)
            if direction and hasattr(r, "move"):
                r.move(_resolve_direction(direction))
                logs.append(f"ranger:{uid}:to_ring:{direction}")
            elif hasattr(r, "wait"):
                r.wait()
                logs.append(f"ranger:{uid}:wait")

    return logs


def command_core_defense(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
) -> tuple[bool, list[str]]:
    """Core 治疗 / 修盾决策。

    Returns:
        (did_act, logs) — did_act=True 表示已占用 Core 本 tick 动作（不可再 spawn）。
    """
    logs: list[str] = []
    core = getattr(turn, "core", None)
    if core is None:
        return False, logs

    resources = int(getattr(turn, "resources", 0) or 0)
    hp = int(getattr(core, "hp", config.core_max_hp) or config.core_max_hp)
    shield = int(getattr(core, "shield", config.core_max_shield) or 0)

    # 低 HP 优先 heal（战后结算，可预排）
    if hp < config.core_max_hp and hp <= config.core_heal_hp_threshold and resources >= 1:
        if hasattr(core, "heal"):
            core.heal()
            logs.append(f"core:heal:hp={hp}")
            return True, logs

    # 低盾修盾
    if (
        shield < config.core_max_shield
        and shield <= config.core_shield_threshold
        and resources >= 1
    ):
        if hasattr(core, "repair_shield"):
            core.repair_shield()
            logs.append(f"core:repair_shield:shield={shield}")
            return True, logs

    # HP 不满但资源充裕时也可治疗（非紧急）
    if hp < config.core_max_hp and resources >= config.reserve_resources + 1:
        # 仅当没有更紧迫的生产需求时由 strategy 决定；这里仅标记可治疗
        pass

    return False, logs


def should_core_heal_first(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
) -> bool:
    """是否应优先占用 Core 动作用于治疗/修盾。"""
    core = getattr(turn, "core", None)
    if core is None:
        return False
    resources = int(getattr(turn, "resources", 0) or 0)
    if resources < 1:
        return False
    hp = int(getattr(core, "hp", config.core_max_hp) or config.core_max_hp)
    shield = int(getattr(core, "shield", config.core_max_shield) or 0)
    if hp <= config.core_heal_hp_threshold and hp < config.core_max_hp:
        return True
    if shield <= config.core_shield_threshold and shield < config.core_max_shield:
        return True
    return False
