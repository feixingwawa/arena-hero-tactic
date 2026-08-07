"""战术参数配置。

所有可调阈值集中在此，便于离线调参与单测注入。
目标：v0.14 规则下人口可健康突破 20（无维护费，使用 SDK 动态单位价格）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TacticConfig:
    """「均衡扩张 + 防守」战术参数（v0.14 优化版）。

    Attributes:
        max_population: 硬人口上限（v0.14 已无维护费，可上调至 30）。
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
        patrol_offset: 防守巡逻环上的相位偏移基数。
        explore_base_radius: 无资源时探索起始半径（兼容旧接口，探索已改螺旋扫掠）。
        explore_max_radius: 探索半径上限（兼容旧接口）。
        explore_expand_every: 每 N tick 扩大一次探索半径（兼容旧接口）。
        early_game_pop: 早期人口阈值，低于此值 spawn 时 reserve 视为 0。
        sector_count: 螺旋扫掠扇区数（默认 4 = Worker 分散度）。
        spiral_base_ring: 螺旋扫掠起始曼哈顿环半径。
        spiral_max_ring: 螺旋扫掠最大环半径，超过则软回撤回 base ring。
        recall_stall_ticks: 连续无进展 N tick 后执行软回撤。
        refresh_interval_ticks: 资源点刷新回补节拍（近似 4 resolved tick）。
        revisit_max_distance: 回访候选的最大曼哈顿距离。
    """

    max_population: int = 30
    target_workers: int = 14
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

    patrol_offset: int = 1

    # 兼容旧探索接口（经济模块已改螺旋扫掠，此处仅供旧测试/回退使用）
    explore_base_radius: int = 5
    explore_max_radius: int = 32
    explore_expand_every: int = 4
    # 早期人口：spawn 时 reserve 视为 0，便于 resources>=3 出 WORKER
    early_game_pop: int = 4

    # ---- 螺旋扫掠 + 地图记忆（v0.14 优化新增）----
    sector_count: int = 2  # 仅 2 个 Worker 时分 2 扇区，每个 Worker 覆盖更广
    spiral_base_ring: int = 3  # 近 Core 优先扫掠
    spiral_max_ring: int = 32
    recall_stall_ticks: int = 6
    refresh_interval_ticks: int = 4
    revisit_max_distance: int = 40

    # 单位最大 HP（官方 v0.13，用于治疗判断）
    worker_max_hp: int = 2
    vanguard_max_hp: int = 4
    ranger_max_hp: int = 2
    core_max_hp: int = 5
    core_max_shield: int = 5


DEFAULT_CONFIG: TacticConfig = TacticConfig()


def population_upkeep(population: int) -> int:
    """根据人口计算下一 tick 维护费。

    .. deprecated::
        v0.14 已移除维护费机制。该函数仅保留供旧测试/迁移期兼容，
        经济模块不再引用（T04 已移除 import）。计划在后续版本删除。

    官方旧规则：tier = floor(pop / 20)，upkeep = tier * (tier + 1) / 2
    0–19 → 0；20–39 → 1；40–59 → 3 …
    """
    if population < 0:
        return 0
    tier = population // 20
    return tier * (tier + 1) // 2
