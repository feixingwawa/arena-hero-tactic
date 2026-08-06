"""战术参数配置。

所有可调阈值集中在此，便于离线调参与单测注入。
默认目标：人口压在 20 以下，保持 upkeep = 0。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TacticConfig:
    """「均衡扩张 + 防守」战术参数。

    Attributes:
        max_population: 硬人口上限（建议 15–18，避免维护费）。
        target_workers: 目标 Worker 数量。
        target_vanguards: 目标 Vanguard 数量。
        target_rangers: 目标 Ranger 数量。
        defense_radius: Core 周边防守环曼哈顿半径。
        ranger_radius: Ranger 稍外圈防守半径。
        threat_radius: 判定「有威胁」的曼哈顿半径。
        retreat_adjacent: 空货 Worker 贴身撤退距离（默认 1=邻格）。
        retreat_radius: 满货 Worker 遇敌保护撤退半径。
        core_heal_hp_threshold: Core HP 低于此值时优先治疗。
        core_shield_threshold: Core 盾低于此值时优先修盾。
        unit_heal_hp_threshold: 单位 HP 低于此值且在 Core 上时治疗。
        reserve_resources: 预留资源，用于治疗/应急（spawn 前检查）。
        worker_cost: Worker 生产成本（官方规则）。
        vanguard_cost: Vanguard 生产成本。
        ranger_cost: Ranger 生产成本。
        upkeep_soft_cap: 人口达到此值后停止常规扩军。
        upkeep_hard_cap: 人口达到此值后除非严重缺防否则不 spawn。
        patrol_offset: 防守巡逻环上的相位偏移基数。
        explore_base_radius: 无资源时探索起始半径。
        explore_max_radius: 探索半径上限。
        explore_expand_every: 每 N tick 扩大一次探索半径。
        early_game_pop: 早期人口阈值，低于此值 spawn 时 reserve 视为 0。
    """

    max_population: int = 18
    target_workers: int = 12
    target_vanguards: int = 3
    target_rangers: int = 2

    defense_radius: int = 3
    ranger_radius: int = 4
    threat_radius: int = 8
    retreat_adjacent: int = 1  # 空货 worker 仅邻格（真正贴身）才撤
    retreat_radius: int = 3  # 满货遇敌保护半径（略收紧，减少误撤）

    core_heal_hp_threshold: int = 3
    core_shield_threshold: int = 2
    unit_heal_hp_threshold: int = 1

    reserve_resources: int = 2
    worker_cost: int = 3
    vanguard_cost: int = 10
    ranger_cost: int = 12

    upkeep_soft_cap: int = 18
    upkeep_hard_cap: int = 19

    patrol_offset: int = 1

    # 无可见资源时的探索参数（略激进，尽快摸到资源）
    explore_base_radius: int = 5
    # Worker 离 Core 的探索上限，防止单 worker 跑飞
    explore_max_radius: int = 32
    explore_expand_every: int = 4
    # 早期人口：spawn 时 reserve 视为 0，便于 resources>=3 出 WORKER
    early_game_pop: int = 4

    # 单位最大 HP（官方 v0.13，用于治疗判断）
    worker_max_hp: int = 2
    vanguard_max_hp: int = 4
    ranger_max_hp: int = 2
    core_max_hp: int = 5
    core_max_shield: int = 5


DEFAULT_CONFIG: TacticConfig = TacticConfig()


def population_upkeep(population: int) -> int:
    """根据人口计算下一 tick 维护费。

    官方规则：tier = floor(pop / 20)，upkeep = tier * (tier + 1) / 2
    0–19 → 0；20–39 → 1；40–59 → 3 …
    """
    if population < 0:
        return 0
    tier = population // 20
    return tier * (tier + 1) // 2
