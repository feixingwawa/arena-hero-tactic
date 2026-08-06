"""规则层：动态单位价格 / Core 容量 / chunk 配额（SDK 包装 + 本地回退）。

设计约定：动态价格唯一入口 `unit_cost_for(type_name, population)`，
`choose_spawn` 一律用 spawn 后人口 `pop + 1`；禁止在别处写死 cost 常量。

优先使用 arena-hero SDK（arena_hero.rules.unit_cost / core_resource_capacity），
SDK 不可用时回退到本地同公式实现（含 round-half-up），保证纯逻辑可单测。
"""

from __future__ import annotations

from typing import Optional

# 本地回退用的基础成本（与 SDK arena_hero.rules.UNIT_BASE_COSTS 一致）
_LOCAL_BASE_COSTS: dict[str, int] = {
    "WORKER": 5,
    "VANGUARD": 10,
    "RANGER": 12,
}

# 涨价档：人口 >= 20 后每 5 人口升一档，基础价乘 (13/10)^exponent
_UNIT_COST_TIER_START = 20
_UNIT_COST_TIER_STEP = 5
_UNIT_COST_MULTIPLIER_NUM = 13
_UNIT_COST_MULTIPLIER_DEN = 10


def _round_half_up(numerator: int, denominator: int) -> int:
    """round-half-up 整除：round(x) = floor(x + 0.5)。"""
    return (2 * numerator + denominator) // (2 * denominator)


def _local_unit_cost(type_name: str, population: int) -> int:
    """本地回退实现（与 SDK arena_hero.rules.unit_cost 完全一致）。"""
    if population < 0:
        raise ValueError("population must not be negative")
    base = _LOCAL_BASE_COSTS.get(type_name)
    if base is None:
        raise ValueError(f"unknown unit type: {type_name}")
    if population < _UNIT_COST_TIER_START:
        return base
    exponent = (population - _UNIT_COST_TIER_START) // _UNIT_COST_TIER_STEP + 1
    numerator = base * (_UNIT_COST_MULTIPLIER_NUM**exponent)
    denominator = _UNIT_COST_MULTIPLIER_DEN**exponent
    return _round_half_up(numerator, denominator)


def _sdk_unit_cost(type_name: str, population: int) -> int:
    """优先走 SDK；任何异常都回退本地实现。"""
    try:
        from arena_hero.enums import UnitType  # type: ignore
        from arena_hero.rules import unit_cost  # type: ignore

        return int(unit_cost(UnitType[type_name], population))
    except Exception:
        return _local_unit_cost(type_name, population)


def unit_cost_for(type_name: str, population: int) -> int:
    """返回指定类型单位在给定人口下的生产成本（动态价格唯一入口）。

    Args:
        type_name: "WORKER" | "VANGUARD" | "RANGER"。
        population: 人口（调用方应传 spawn 后人口 pop+1）。

    Returns:
        生产成本（整数，round-half-up）。
    """
    if population < 0:
        population = 0
    try:
        return _sdk_unit_cost(type_name, int(population))
    except Exception:
        return _local_unit_cost(type_name, int(population))


def core_resource_capacity(population: int) -> int:
    """Core 资源容量：max(10, population * 5)（SDK 优先，本地回退）。"""
    if population < 0:
        population = 0
    try:
        from arena_hero.rules import core_resource_capacity as _cap  # type: ignore

        return int(_cap(int(population)))
    except Exception:
        return max(10, int(population) * 5)


def chunk_quota(ring: int) -> int:
    """资源点刷新配额：max(2, floor(16*8 / (8 + ring)))。

    ring 为资源点所在 chunk 相对 Core chunk 的曼哈顿环序号。
    配额用于回访调度时「每 chunk 每批最多派 N 个 Worker」的软约束（防扎堆），
    不精确模拟服务端。
    """
    if ring < 0:
        ring = 0
    return max(2, (16 * 8) // (8 + int(ring)))
