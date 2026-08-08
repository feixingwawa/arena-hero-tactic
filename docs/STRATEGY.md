# 本仓库战术原则（对齐 Drew-Z + 官方 v0.14）

- 日期：2026-08-07
- 参考：[Drew-Z strategy](https://github.com/Drew-Z/arena-hero-agent/blob/main/docs/strategy.md)
- 游戏理解：`docs/GAME_UNDERSTANDING.md`

---

## 1. 战略定位

| 维度 | 选择 |
|------|------|
| 主目标 | **资源优先 + Core 生存**（非 Beacon 竞速） |
| 编制 | **12 Worker + 4 Vanguard + 4 Ranger = 20** |
| Beacon | 默认 **retreat**：Core 远离；最多 1 名 dedicated 侦察，远距放弃 |
| AI | **不进 Tick 循环**（本仓库亦无模型决策） |
| 人口 | 默认不自动冲 21+（动态涨价）；`max_population` 作硬顶 |

---

## 2. 与 Drew-Z 对照

| 主题 | Drew-Z | 本仓库 | 改造优先级 |
|------|--------|--------|------------|
| 编制 | 12/4/4=20 | 14/3/2 max30 | **P0** 改 config 默认 |
| Core vs Beacon | 迁徙远离 Beacon | 默认静止 | **P1** 可选 `start_move` 远离 |
| 探索 | 陈旧 chunk 侦察 + 最小费用匹配 | 螺旋 + MemoryMap + 可选 beacon phase | 保留螺旋；**限制 beacon** |
| 威胁 | 分层：警戒/预撤/交战/多轴突围 | threat_radius + retreat | P2 增强 |
| 经济 stall | 专项诊断与释放路由 | 软回撤 stall | 已有；加强 local 优先 |
| 版本兼容 | 监控 hold | 无 | P3 |
| 部署 | Docker/systemd | 本地 `python -m bot.main` | 按需 |

---

## 3. 本仓库必须遵守的铁律

1. **Local harvest > Beacon march**  
   - `widx==0` 才可 `dedicated` beacon。  
   - 非 dedicated 禁止 soft-recall 切入 beacon；残相 `phase=beacon` 强制回 local。  
   - `d_beacon` 过大（建议 > 64 或 > `spiral_max_ring*2`）时 dedicated 也应降级 local。

2. **只采 VISIBLE 资源**  
   - 记忆点仅作导航提示；站上且 `pos in resource_cells` 才 `harvest`。

3. **满货优先 deposit**  
   - 高于探索、高于 Beacon；回城用 `guided_step_toward`（内含 `clamp_step_toward_memo` 防抖 + **范围循环检测**）。  
   - 若单位在小范围（窗口内唯一格少、包围盒小、或连续同格不动）重复行走 → `:repath:loop` 强制换路（清 last_dir、足迹软障、禁旧方向）。

4. **软回撤只外扩**  
   - stall → `ring += 1` + 对侧跳点；禁止 `ring -= 1` 贴 Core 空转。

5. **Beacon 同步语义**  
   - `status in (GROUND, None)` → 写 position；`CARRIED` → 清 None。

6. **动态价格 spawn**  
   - 用 `unit_cost_for(type, pop)`（spawn 后人口口径）；早期 `early_game_pop` 可放宽 reserve。

7. **战斗不挡经济预算**  
   - Core heal/盾优先于 spawn；同 Tick 预留单位 heal 费用，避免超支。

---

## 4. 建议默认参数（目标态）

```python
# bot/config.py — 目标默认（对齐 Drew-Z 基础价满编）
max_population: int = 20
target_workers: int = 12
target_vanguards: int = 4
target_rangers: int = 2   # 或 4；本仓库可先 2→4 分阶段
# Beacon（已落地）
beacon_max_chase: int = 64          # Core→Beacon 超距放弃
beacon_min_workers: int = 3         # 早期全员采
# 探索
spiral_base_ring: int = 3
spiral_max_ring: int = 24
sector_count: int = 4
recall_stall_ticks: int = 6
# 范围循环 → 强制重寻路
loop_window_ticks: int = 12
loop_min_unique: int = 4
loop_bbox_diameter: int = 3
loop_static_ticks: int = 4
loop_repath_cooldown: int = 5
retreat_adjacent: int = 1
retreat_radius: int = 3
```

---

## 5. 改造路线（文档驱动，按优先级）

### P0 — 经济止血（混合高效已落地 2026-08-08）

- [x] soft-recall ring 外扩  
- [x] beacon status=None 可写  
- [x] soft-recall 进 beacon 仅 dedicated  
- [x] **远距 Beacon 放弃**：`beacon_max_chase=64`（Core→Beacon），超限降级 local  
- [x] **早期不追**：`beacon_min_workers=3`，人少全员 local 采  
- [x] **强制非 dedicated 清 beacon 相**（每 tick `_drop_to_local`）  
- [x] 编制默认 **12/4/4**，`max_population=20`  
- [x] spawn 爬坡：先凑 6 Worker 再补战斗单位

### P1 — Core 安全（Drew-Z retreat）

- [x] 可见威胁或 Beacon 在 Core 近侧时，评估 `start_move` 向**远离**方向  
- [ ] 迁徙中暂停非必要 spawn；Worker 仍向 Core 预测位置交付  
- [x] 守卫分布在环上，不堆 Core 格堵路

### P2 — 威胁与匹配

- [ ] Worker–资源最小费用一对一匹配（减抢点）  
- [x] 侦察优先 least-recently-seen chunk  
- [x] 同 Tick 火力 ledger 减 overkill（可选）

### P3 — 运维

- [x] SDK/规则版本自检  
- [x] 经济 stall 结构化日志窗口导出

---

## 6. 验收标准（与用户目标对齐）

| 指标 | 通过条件 |
|------|----------|
| 经济正循环 | 新局 200 tick 内出现多次 `deposit`，`res` 上升 |
| 人口 | 无强敌时 pop 向 ≥6 再向 12+ 增长 |
| 用户目标 | Core `resources ≥ 100` |
| Beacon | 非 dedicated 不出现长期 `phase=beacon`；dedicated `d_beacon` 超限回 local |
| 回归 | `pytest -q` 全绿 |

---

## 7. 决策一句话

> **先活、先采、先满编 20；Beacon 是乘数不是主线；Core 离信标远一点更安全。**
