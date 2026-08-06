"""规则层单测：动态单位价格 / Core 容量 / chunk 配额。"""

from __future__ import annotations

import pytest

from bot.rules import chunk_quota, core_resource_capacity, unit_cost_for


def test_unit_cost_for_base_prices_pop_0_to_19() -> None:
    """pop 0–19：基础价 WORKER=5 / VANGUARD=10 / RANGER=12。"""
    for pop in (0, 1, 10, 19):
        assert unit_cost_for("WORKER", pop) == 5
        assert unit_cost_for("VANGUARD", pop) == 10
        assert unit_cost_for("RANGER", pop) == 12


def test_unit_cost_for_tier_pop_20() -> None:
    """pop 20（第一涨价档，×13/10，round-half-up）：Worker 7 / Vanguard 13 / Ranger 16。"""
    assert unit_cost_for("WORKER", 20) == 7
    assert unit_cost_for("VANGUARD", 20) == 13
    assert unit_cost_for("RANGER", 20) == 16


def test_unit_cost_for_tier_pop_21_to_24_same_as_20() -> None:
    """pop 21–24 仍在第一涨价档。"""
    for pop in (21, 24):
        assert unit_cost_for("WORKER", pop) == 7
        assert unit_cost_for("VANGUARD", pop) == 13


def test_unit_cost_for_tier_pop_25() -> None:
    """pop 25（第二涨价档，×13/10^2 = 1.69）：Worker 8 / Vanguard 17 / Ranger 20。"""
    assert unit_cost_for("WORKER", 25) == 8
    assert unit_cost_for("VANGUARD", 25) == 17
    assert unit_cost_for("RANGER", 25) == 20


def test_unit_cost_for_tier_pop_30() -> None:
    """pop 30（第三涨价档，×13/10^3 = 2.197）：Worker 11 / Vanguard 22 / Ranger 26。"""
    assert unit_cost_for("WORKER", 30) == 11
    assert unit_cost_for("VANGUARD", 30) == 22
    assert unit_cost_for("RANGER", 30) == 26


def test_unit_cost_for_matches_sdk_if_available() -> None:
    """本地回退与 SDK 一致（SDK 已安装时直接对比）。"""
    try:
        from arena_hero.enums import UnitType  # type: ignore
        from arena_hero.rules import unit_cost as sdk_unit_cost  # type: ignore
    except Exception:
        pytest.skip("arena-hero SDK 不可用")
    for name in ("WORKER", "VANGUARD", "RANGER"):
        for pop in (0, 19, 20, 25, 30):
            assert unit_cost_for(name, pop) == sdk_unit_cost(UnitType[name], pop), (
                f"{name} pop={pop}"
            )


def test_unit_cost_for_negative_population() -> None:
    """负人口按 0 处理（基础价）。"""
    assert unit_cost_for("WORKER", -3) == 5


def test_core_resource_capacity() -> None:
    """Core 容量 = max(10, pop * 5)。"""
    assert core_resource_capacity(0) == 10
    assert core_resource_capacity(1) == 10
    assert core_resource_capacity(2) == 10
    assert core_resource_capacity(3) == 15
    assert core_resource_capacity(10) == 50
    assert core_resource_capacity(30) == 150


def test_chunk_quota_bounds() -> None:
    """chunk_quota = max(2, floor(16*8 / (8+ring)))。"""
    assert chunk_quota(0) == 16  # 128 // 8
    assert chunk_quota(8) == 8  # 128 // 16
    assert chunk_quota(56) == 2  # 128 // 64 → 2
    assert chunk_quota(120) == 2  # 128 // 128 → 1 → max(2) → 2
    assert chunk_quota(-5) == 16  # 负数按 0


def test_chunk_quota_monotonic() -> None:
    """配额随 ring 单调不增。"""
    prev = 10**9
    for ring in range(0, 130, 4):
        q = chunk_quota(ring)
        assert q <= prev, f"quota increased at ring={ring}: {q} > {prev}"
        prev = q
        assert q >= 2
