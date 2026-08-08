"""战术参数配置。

所有可调阈值集中在此，便于离线调参与单测注入。

混合高效版（本仓库逻辑 × Drew-Z 资源优先，非照搬）：
- 保留：螺旋扫掠 / MemoryMap / 防抖寻路 / VISIBLE 采集 / 软回撤外扩
- 吸收：基础价满编 12/4/4=20、远距 Beacon 不追、早期全员采
- 目标：本地经济正循环优先，近距 Beacon 才派 1 名 dedicated
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.pathing import Position


@dataclass(frozen=True)
class TacticConfig:
    """「资源优先 + 均衡防守」混合战术参数（v0.14）。

    Attributes:
        max_population: 硬人口上限（默认 20 = 基础价满编，不自动冲动态涨价）。
        target_workers / target_vanguards / target_rangers: 编制目标（Drew-Z 12/4/4）。
        defense_radius / ranger_radius / threat_radius: 防守与威胁半径。
        retreat_adjacent / retreat_radius: Worker 遇敌撤退阈值。
        core_heal_hp_threshold / core_shield_threshold / unit_heal_hp_threshold: 治疗阈值。
        reserve_resources: spawn 前应急预留。
        early_game_pop: 低于此人口时 reserve=0，加速首批 Worker。
        sector_count: 螺旋/回访扇区数（Worker 分散）。
        spiral_base_ring / spiral_max_ring / recall_stall_ticks: 本地螺旋与软回撤。
        refresh_interval_ticks / revisit_max_distance: 资源记忆回访。
        loop_window_ticks / loop_min_unique / loop_bbox_diameter /
        loop_static_ticks / loop_repath_cooldown: 小范围重复行走 → 强制重寻路。
        beacon_step_radius: Beacon 阶段单步目标距离。
        beacon_max_chase: Core→Beacon 曼哈顿超过此值则**全体不追**（防 d≈1000 空跑）。
        beacon_min_workers: 至少 N 名 Worker 才允许 1 人 dedicated（否则全员 local）。
        beacon_position: 运行期由 decide() 写入；economy 只读。
    """

    # 编制：基础价满编，不冲 21+ 动态价（可按需上调 max）
    max_population: int = 20
    target_workers: int = 12
    target_vanguards: int = 4
    target_rangers: int = 4

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
    # 早期人口：spawn 时 reserve 视为 0，便于 resources 刚够动态价即可出 WORKER
    early_game_pop: int = 6

    # ---- 螺旋扫掠 + 地图记忆 ----
    sector_count: int = 4  # 多 Worker 分散扇区
    spiral_base_ring: int = 3  # 近 Core 优先扫掠
    spiral_max_ring: int = 24  # 本地环略收，减少空转；远距靠记忆回访
    recall_stall_ticks: int = 6
    refresh_interval_ticks: int = 4
    revisit_max_distance: int = 48

    # ---- 范围循环检测 + 强制重寻路（防 return_deposit/to_resource 局部空转）----
    # 最近 loop_window_ticks 步内：唯一格 ≤ loop_min_unique 且包围盒直径 ≤ loop_bbox_diameter
    # → 判定「小范围重复行走」，清空 last_dir、把近期足迹当软障碍、强制换路。
    loop_window_ticks: int = 12
    loop_min_unique: int = 4
    loop_bbox_diameter: int = 3  # 曼哈顿包围盒 max(dx)+max(dy) 上限
    loop_static_ticks: int = 4  # 连续同格不动也触发（服务端拒步/贴墙）
    loop_repath_cooldown: int = 5  # 触发后冷却，避免每 tick 抖动

    # ---- Beacon：近距可选乘数，远距放弃（混合高效）----
    beacon_step_radius: int = 8
    # Core 到 Beacon 超过此曼哈顿距离 → 不派 dedicated、不追（线上 d≈900+ 必须放弃）
    beacon_max_chase: int = 64
    # 至少这么多 Worker 才允许 widx==0 dedicated；早期 1～2 人全采
    beacon_min_workers: int = 3
    # 运行期 Beacon 位置：**仅 decide() 每 tick 写入**，economy 只读。
    # GROUND / None → 写位置；CARRIED → 清 None。
    beacon_position: Optional[Position] = None

    # 单位最大 HP（官方，用于治疗判断）
    worker_max_hp: int = 2
    vanguard_max_hp: int = 4
    ranger_max_hp: int = 2
    core_max_hp: int = 5
    core_max_shield: int = 5


DEFAULT_CONFIG: TacticConfig = TacticConfig()


def set_beacon_position(config: TacticConfig, pos: Optional[Position]) -> None:
    """运行期写入 `beacon_position`（frozen dataclass 用 `object.__setattr__`）。

    共享知识约定：**仅 `decide()` 可调用**（每 tick 从 `turn.beacon` 同步）；
    economy 只读该字段。语义：
    - `"GROUND"` → 写 Beacon 位置；
    - `"CARRIED"`（己方/敌方拾取）或缺失 → 清 `None`（Worker 自动停止向旧位推进）。
    """
    object.__setattr__(config, "beacon_position", pos)


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
