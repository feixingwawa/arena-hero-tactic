"""主策略：decide(turn) -> 为所有单位/Core 排队动作。

决策与 I/O 完全解耦：不调用 submit()，不读写网络。
真实运行由 main.py 负责 turn.submit()。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from bot.combat import (
    assess_threats,
    command_core_defense,
    command_rangers,
    command_vanguards,
    should_core_heal_first,
)
from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.economy import command_core_economy, command_workers
from bot.pathing import manhattan
from bot.roles import assign_roles, count_by_type, total_population, _as_position


@dataclass
class DecisionResult:
    """单回合决策结果摘要（便于日志与测试）。"""

    tick: int = 0
    population: int = 0
    resources: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    has_near_threat: bool = False
    core_action: str = "none"
    logs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        threat = "THREAT" if self.has_near_threat else "CLEAR"
        return (
            f"tick={self.tick} pop={self.population} res={self.resources} "
            f"W{self.counts.get('WORKER', 0)}/V{self.counts.get('VANGUARD', 0)}/"
            f"R{self.counts.get('RANGER', 0)} [{threat}] core={self.core_action}"
        )


def _core_position(turn: Any) -> Optional[tuple[int, int]]:
    core = getattr(turn, "core", None)
    if core is None:
        return None
    return _as_position(core.position)


def decide(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
) -> DecisionResult:
    """对当前 Turn 执行「均衡扩张 + 防守」决策并排队全部动作。

    Args:
        turn: arena_hero.Turn 或兼容 stub（需提供 workers/vanguards/rangers/
              core/resources/visible_enemies/resource_cells 等属性）。
        config: 可注入的战术参数。

    Returns:
        DecisionResult 决策摘要。不会调用 turn.submit()。
    """
    tick = int(getattr(turn, "tick", 0) or 0)
    resources = int(getattr(turn, "resources", 0) or 0)
    counts = count_by_type(turn)
    pop = total_population(turn)
    core_pos = _core_position(turn)

    result = DecisionResult(
        tick=tick,
        population=pop,
        resources=resources,
        counts=counts,
    )

    # 重生状态：RESPAWNING 时不做任何行动，等待 respawn_at_tick
    state = getattr(turn, "state", None)
    status = getattr(state, "status", None) if state is not None else None
    if status is not None and str(status).upper() == "RESPAWNING":
        respawn_at = getattr(state, "respawn_at_tick", None)
        respawn_at = int(respawn_at) if respawn_at is not None else tick
        result.logs.append(f"strategy:respawn_at={respawn_at}")
        result.core_action = "respawn"
        return result

    # Core 不存在（重生中）→ 无法行动
    if core_pos is None:
        result.logs.append("strategy:no_core")
        result.core_action = "absent"
        return result

    # 1) 角色分配 + 威胁评估
    role_plan = assign_roles(turn, config=config, core_position=core_pos)
    threat = assess_threats(turn, core_pos, config=config)
    result.has_near_threat = bool(threat["has_near_threat"])
    # 调试：最近敌人相对 Core / 相对最近 Worker 的距离，便于判断误撤
    min_core_d = None
    min_worker_d = None
    if threat["positions"]:
        min_core_d = min(manhattan(p, core_pos) for p in threat["positions"])
        worker_positions = [
            _as_position(w.position)
            for w in (getattr(turn, "workers", None) or ())
        ]
        if worker_positions:
            min_worker_d = min(
                manhattan(wp, ep)
                for wp in worker_positions
                for ep in threat["positions"]
            )
    resource_n = len(list(getattr(turn, "resource_cells", None) or ()))
    result.logs.append(
        f"threat:count={threat['count']}:near={len(threat['near'])}"
        f":min_core={min_core_d}:min_w={min_worker_d}:res_vis={resource_n}"
    )

    # 2) 战斗单位优先（有威胁时先排攻击/就位）
    v_logs = command_vanguards(
        turn, role_plan, config=config, core_position=core_pos
    )
    r_logs = command_rangers(
        turn, role_plan, config=config, core_position=core_pos
    )
    result.logs.extend(v_logs)
    result.logs.extend(r_logs)

    # 3) Worker 经济
    w_logs = command_workers(
        turn, role_plan, config=config, core_position=core_pos
    )
    result.logs.extend(w_logs)

    # 4) Core：治疗优先，否则生产
    heal_first = should_core_heal_first(turn, config=config)
    # 若 Core 邻格有敌人且低血，更应治疗
    if threat["core_under_fire"] and heal_first:
        heal_first = True

    if heal_first:
        acted, c_logs = command_core_defense(turn, config=config)
        result.logs.extend(c_logs)
        if acted:
            result.core_action = c_logs[-1] if c_logs else "heal"
        else:
            # 治疗条件不满足则回退生产
            e_logs = command_core_economy(
                turn, role_plan, config=config, prefer_heal=False
            )
            result.logs.extend(e_logs)
            result.core_action = e_logs[-1] if e_logs else "none"
    else:
        # 先尝试非紧急治疗？默认扩张优先，仅紧急才 heal
        e_logs = command_core_economy(
            turn, role_plan, config=config, prefer_heal=False
        )
        result.logs.extend(e_logs)
        result.core_action = e_logs[-1] if e_logs else "none"

        # 若无 spawn 且 Core 不满血/盾，用空闲动作修整
        if result.core_action in ("core:no_spawn", "none"):
            acted, c_logs = command_core_defense(turn, config=config)
            if acted:
                result.logs.extend(c_logs)
                result.core_action = c_logs[-1] if c_logs else "heal"

    return result


def decide_and_describe(
    turn: Any,
    config: TacticConfig = DEFAULT_CONFIG,
) -> str:
    """decide 的便捷包装，返回摘要字符串。"""
    return decide(turn, config=config).summary()
