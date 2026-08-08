"""单位角色分配：harvester / guard / scout。

根据当前编制与威胁态势，为每个单位指定本 tick 角色，
供 economy / combat 模块消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.pathing import Position, manhattan


class Role(str, Enum):
    """单位战术角色。"""

    HARVESTER = "harvester"  # 采集 / 交付
    GUARD = "guard"  # 防守环驻守
    SCOUT = "scout"  # 轻探（本战术几乎不用，预留）
    RETREAT = "retreat"  # 紧急回撤 Core
    HEAL = "heal"  # 回 Core 治疗


@dataclass
class UnitSnapshot:
    """与 SDK 解耦的单位快照，便于单测。"""

    id: UUID
    unit_type: str  # "WORKER" | "VANGUARD" | "RANGER"
    position: Position
    hp: int
    cargo: int = 0
    max_hp: int = 2


@dataclass
class RoleAssignment:
    """单个单位的角色分配结果。"""

    unit_id: UUID
    role: Role
    unit_type: str
    position: Position
    hp: int
    cargo: int = 0
    # 可选提示目标（资源点 / 防守位 / 敌人）
    hint_target: Optional[Position] = None
    # 螺旋扫掠扇区（worker_index % sector_count；非 Worker 为 None）
    sector_id: Optional[int] = None


@dataclass
class RolePlan:
    """整回合角色计划。"""

    assignments: list[RoleAssignment] = field(default_factory=list)
    threat_positions: list[Position] = field(default_factory=list)
    has_near_threat: bool = False
    has_far_threat: bool = False

    def by_role(self, role: Role) -> list[RoleAssignment]:
        return [a for a in self.assignments if a.role == role]

    def get(self, unit_id: UUID) -> Optional[RoleAssignment]:
        for a in self.assignments:
            if a.unit_id == unit_id:
                return a
        return None


def _as_position(pos: Any) -> Position:
    """将 SDK Position / tuple 规范为 (x, y)。"""
    if isinstance(pos, tuple) and len(pos) >= 2:
        return (int(pos[0]), int(pos[1]))
    if hasattr(pos, "x") and hasattr(pos, "y"):
        return (int(pos.x), int(pos.y))
    # 可迭代
    x, y = pos  # type: ignore[misc]
    return (int(x), int(y))


def snapshot_from_unit(unit: Any, unit_type: str, max_hp: int) -> UnitSnapshot:
    """从真实 SDK 单位或 stub 构建快照。"""
    uid = unit.id if hasattr(unit, "id") else unit.unit_id
    pos = _as_position(unit.position)
    hp = int(getattr(unit, "hp", max_hp))
    cargo = int(getattr(unit, "cargo", 0) or 0)
    return UnitSnapshot(
        id=uid,
        unit_type=unit_type,
        position=pos,
        hp=hp,
        cargo=cargo,
        max_hp=max_hp,
    )


def collect_enemy_positions(turn: Any) -> list[Position]:
    """从 turn.visible_enemies 提取敌方位置。"""
    enemies = getattr(turn, "visible_enemies", None) or ()
    result: list[Position] = []
    for e in enemies:
        pos = getattr(e, "position", None)
        if pos is not None:
            result.append(_as_position(pos))
    return result


def assign_roles(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
    core_position: Optional[Position] = None,
) -> RolePlan:
    """根据 turn 状态为所有己方单位分配角色。

    Args:
        turn: 真实 Turn 或测试 stub（需有 workers/vanguards/rangers）。
        config: 战术参数。
        core_position: 若已知 Core 位置可直接传入，否则从 turn.core 读取。
    """
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            core_position = (0, 0)
        else:
            core_position = _as_position(core.position)

    enemies = collect_enemy_positions(turn)
    near_threats = [
        p for p in enemies if manhattan(p, core_position) <= config.threat_radius
    ]
    far_threats = [
        p for p in enemies if manhattan(p, core_position) > config.threat_radius
    ]
    has_near = len(near_threats) > 0
    has_far = len(far_threats) > 0
    all_threats = near_threats + far_threats

    plan = RolePlan(
        threat_positions=all_threats,
        has_near_threat=has_near,
        has_far_threat=has_far,
    )

    # --- Workers ---
    workers = list(getattr(turn, "workers", None) or ())
    for w in workers:
        snap = snapshot_from_unit(w, "WORKER", config.worker_max_hp)
        role = Role.HARVESTER
        hint: Optional[Position] = None
        # 扇区 = worker 在列表中的下标 % sector_count（与 economy 探索一致）
        try:
            widx = workers.index(w)
        except (ValueError, TypeError):
            widx = 0
        sector_id = widx % max(1, config.sector_count)

        # 低血：优先回 Core 治疗
        if snap.hp <= config.unit_heal_hp_threshold and snap.hp < snap.max_hp:
            role = Role.HEAL
            hint = core_position
        else:
            # 软化撤退（经济优先）：禁止远距离/仅进视野就回城
            # 1) 空货：仅邻格贴身（≤ retreat_adjacent，默认 1）才撤
            # 2) 满货：距敌 ≤ retreat_radius 才撤（保货）
            # 3) 近 Core 威胁：仅当敌人 near Core 且 worker 贴身（≤ adjacent）才撤
            #    —— 空货中距离敌人改由 explore 避让，不打断外扩
            # 4) 满货已逼近 Core（man≤4）时禁止因敌人改 RETREAT：
            #    线上敌工贴 Core 导致 deposit 工人 man≈2 来回拉扯，资源永远 ≤8
            dist_core = manhattan(snap.position, core_position)
            near_core_deposit = snap.cargo > 0 and dist_core <= 4
            adjacent_danger = any(
                manhattan(snap.position, ep) <= config.retreat_adjacent
                for ep in enemies
            )
            cargo_danger = (
                snap.cargo > 0
                and not near_core_deposit
                and any(
                    manhattan(snap.position, ep) <= config.retreat_radius
                    for ep in enemies
                )
            )
            core_melee_danger = (
                not near_core_deposit
                and any(
                    manhattan(ep, core_position) <= config.threat_radius
                    and manhattan(snap.position, ep) <= config.retreat_adjacent
                    for ep in enemies
                )
            )
            # 满货冲 Core 时仅邻格贴身才被迫撤（真正被打）；否则继续 deposit
            if near_core_deposit:
                if adjacent_danger and dist_core > 0:
                    # 贴脸但尚未站上 Core：仍朝 Core 走（economy 会 return_deposit）
                    role = Role.RETREAT
                    hint = core_position
                # 已在 Core 或无人贴脸 → 保持 HARVESTER，走 deposit 分支
            elif adjacent_danger or cargo_danger or core_melee_danger:
                role = Role.RETREAT
                hint = core_position

        plan.assignments.append(
            RoleAssignment(
                unit_id=snap.id,
                role=role,
                unit_type=snap.unit_type,
                position=snap.position,
                hp=snap.hp,
                cargo=snap.cargo,
                hint_target=hint,
                sector_id=sector_id,
            )
        )

    # --- Vanguards：默认 GUARD ---
    vanguards = list(getattr(turn, "vanguards", None) or ())
    for i, v in enumerate(vanguards):
        snap = snapshot_from_unit(v, "VANGUARD", config.vanguard_max_hp)
        role = Role.GUARD
        hint = None
        if snap.hp <= config.unit_heal_hp_threshold and snap.hp < snap.max_hp:
            # 严重受伤且无邻格敌人时回城
            adjacent_enemy = any(
                manhattan(snap.position, ep) <= 1 for ep in enemies
            )
            if not adjacent_enemy:
                role = Role.HEAL
                hint = core_position
        plan.assignments.append(
            RoleAssignment(
                unit_id=snap.id,
                role=role,
                unit_type=snap.unit_type,
                position=snap.position,
                hp=snap.hp,
                cargo=0,
                hint_target=hint,
            )
        )

    # --- Rangers：默认 GUARD ---
    rangers = list(getattr(turn, "rangers", None) or ())
    for r in rangers:
        snap = snapshot_from_unit(r, "RANGER", config.ranger_max_hp)
        role = Role.GUARD
        hint = None
        if snap.hp <= config.unit_heal_hp_threshold and snap.hp < snap.max_hp:
            role = Role.HEAL
            hint = core_position
        plan.assignments.append(
            RoleAssignment(
                unit_id=snap.id,
                role=role,
                unit_type=snap.unit_type,
                position=snap.position,
                hp=snap.hp,
                cargo=0,
                hint_target=hint,
            )
        )

    return plan


def count_by_type(turn: Any) -> dict[str, int]:
    """统计当前各类型单位数量。"""
    return {
        "WORKER": len(list(getattr(turn, "workers", None) or ())),
        "VANGUARD": len(list(getattr(turn, "vanguards", None) or ())),
        "RANGER": len(list(getattr(turn, "rangers", None) or ())),
    }


def total_population(turn: Any) -> int:
    """当前人口 = W + V + R。"""
    counts = count_by_type(turn)
    return counts["WORKER"] + counts["VANGUARD"] + counts["RANGER"]
