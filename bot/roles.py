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
    STRIKE = "strike"  # 突击敌方 Core（2V+2R 编制）


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
    # 敌方 Core 位置（可见时）；供 combat 派 2V+2R 突击
    enemy_core_position: Optional[Position] = None

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


def _enemy_unit_type(enemy: Any) -> str:
    """规范化敌方 unit_type / type 字段为大写字符串。"""
    raw = getattr(enemy, "unit_type", None)
    if raw is None:
        raw = getattr(enemy, "type", None)
    if raw is None:
        return ""
    return str(getattr(raw, "value", raw) or "").upper()


def collect_enemy_positions(
    turn: Any,
    *,
    exclude_workers: bool = False,
    combat_only: bool = False,
) -> list[Position]:
    """从 turn.visible_enemies 提取敌方位置。

    Args:
        exclude_workers: True 时忽略敌方 WORKER（不触发撤退/威胁）。
        combat_only: True 时仅保留战斗威胁（VANGUARD/RANGER/CORE 等非工人）。
    """
    enemies = getattr(turn, "visible_enemies", None) or ()
    result: list[Position] = []
    for e in enemies:
        et = _enemy_unit_type(e)
        if exclude_workers or combat_only:
            if et == "WORKER":
                continue
        pos = getattr(e, "position", None)
        if pos is not None:
            result.append(_as_position(pos))
    return result


def find_enemy_core_position(turn: Any) -> Optional[Position]:
    """可见敌方 Core 的位置（unit_type/type 含 CORE）。"""
    enemies = getattr(turn, "visible_enemies", None) or ()
    for e in enemies:
        et = _enemy_unit_type(e)
        if "CORE" in et:
            pos = getattr(e, "position", None)
            if pos is not None:
                return _as_position(pos)
    return None


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

    战术约定：
    - 敌方 WORKER 不触发 Worker RETREAT；战斗敌人仍触发撤退/绕路。
    - 发现敌方 Core 时，最多 2 Vanguard + 2 Ranger 标记 STRIKE 突击。
    """
    core = getattr(turn, "core", None)
    if core_position is None:
        if core is None:
            core_position = (0, 0)
        else:
            core_position = _as_position(core.position)

    # 撤退/威胁：忽略敌方工人，只对战斗单位（及 CORE）敏感
    combat_enemies = collect_enemy_positions(turn, combat_only=True)
    # 兼容：全部敌人位置（含工人）仍可用于「全图敌情」；threat 用战斗敌人
    all_enemy_positions = collect_enemy_positions(turn)
    near_threats = [
        p for p in combat_enemies if manhattan(p, core_position) <= config.threat_radius
    ]
    far_threats = [
        p for p in combat_enemies if manhattan(p, core_position) > config.threat_radius
    ]
    has_near = len(near_threats) > 0
    has_far = len(far_threats) > 0
    # threat_positions：战斗威胁（供 explore 绕路 / 战斗模块）；工人不列入
    all_threats = near_threats + far_threats
    enemy_core_pos = find_enemy_core_position(turn)

    plan = RolePlan(
        threat_positions=all_threats,
        has_near_threat=has_near,
        has_far_threat=has_far,
        enemy_core_position=enemy_core_pos,
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

        # 低血：仅空货可 HEAL；满货必须先 deposit（交付优先于治疗，避免占核）
        if (
            snap.cargo <= 0
            and snap.hp <= config.unit_heal_hp_threshold
            and snap.hp < snap.max_hp
        ):
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
            # 5) 敌方 WORKER 不计入撤退威胁（combat_enemies 已过滤）
            dist_core = manhattan(snap.position, core_position)
            near_core_deposit = snap.cargo > 0 and dist_core <= 4
            adjacent_danger = any(
                manhattan(snap.position, ep) <= config.retreat_adjacent
                for ep in combat_enemies
            )
            cargo_danger = (
                snap.cargo > 0
                and not near_core_deposit
                and any(
                    manhattan(snap.position, ep) <= config.retreat_radius
                    for ep in combat_enemies
                )
            )
            core_melee_danger = (
                not near_core_deposit
                and any(
                    manhattan(ep, core_position) <= config.threat_radius
                    and manhattan(snap.position, ep) <= config.retreat_adjacent
                    for ep in combat_enemies
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

    # --- Vanguards / Rangers 战斗角色 ---
    # 治疗：半血阈值；但全军同时进核 heal 名额有限（max_core_healers），
    # 优先已在 Core 上的伤员，其次更残、更近 Core 的，其余继续 GUARD 守环。
    # 避免线上 5+ Ranger 同时 to_heal 堵死 deposit。
    vanguards = list(getattr(turn, "vanguards", None) or ())
    rangers = list(getattr(turn, "rangers", None) or ())
    v_heal_th = max(
        int(config.unit_heal_hp_threshold),
        max(1, int(config.vanguard_max_hp) // 2),
    )
    r_heal_th = max(
        int(config.unit_heal_hp_threshold),
        max(1, int(config.ranger_max_hp) // 2),
    )
    heal_budget = max(0, int(getattr(config, "max_core_healers", 1) or 0))

    # 候选伤员：(priority, kind, index, unit, snap)
    # priority 越小越优先：已在 Core → 更残 → 更近 Core → 稳定下标
    heal_candidates: list[tuple] = []
    for i, v in enumerate(vanguards):
        snap = snapshot_from_unit(v, "VANGUARD", config.vanguard_max_hp)
        if snap.hp <= v_heal_th and snap.hp < snap.max_hp:
            adjacent_enemy = any(
                manhattan(snap.position, ep) <= 1 for ep in combat_enemies
            )
            if not adjacent_enemy:
                on_core = 0 if snap.position == core_position else 1
                dist = manhattan(snap.position, core_position)
                heal_candidates.append(
                    (on_core, snap.hp, dist, 0, i, "V", v, snap)
                )
    for i, r in enumerate(rangers):
        snap = snapshot_from_unit(r, "RANGER", config.ranger_max_hp)
        if snap.hp <= r_heal_th and snap.hp < snap.max_hp:
            on_core = 0 if snap.position == core_position else 1
            dist = manhattan(snap.position, core_position)
            heal_candidates.append(
                (on_core, snap.hp, dist, 1, i, "R", r, snap)
            )
    heal_candidates.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]))
    heal_ids: set = set()
    for cand in heal_candidates[:heal_budget]:
        heal_ids.add(cand[7].id)

    strike_v_budget = 2 if enemy_core_pos is not None else 0
    strike_v_assigned = 0
    for i, v in enumerate(vanguards):
        snap = snapshot_from_unit(v, "VANGUARD", config.vanguard_max_hp)
        role = Role.GUARD
        hint = None
        if snap.id in heal_ids:
            role = Role.HEAL
            hint = core_position
        elif strike_v_assigned < strike_v_budget and enemy_core_pos is not None:
            role = Role.STRIKE
            hint = enemy_core_pos
            strike_v_assigned += 1
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

    strike_r_budget = 2 if enemy_core_pos is not None else 0
    strike_r_assigned = 0
    for r in rangers:
        snap = snapshot_from_unit(r, "RANGER", config.ranger_max_hp)
        role = Role.GUARD
        hint = None
        if snap.id in heal_ids:
            role = Role.HEAL
            hint = core_position
        elif strike_r_assigned < strike_r_budget and enemy_core_pos is not None:
            role = Role.STRIKE
            hint = enemy_core_pos
            strike_r_assigned += 1
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

    # all_enemy_positions 保留引用避免 unused（诊断/扩展）
    _ = all_enemy_positions
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
