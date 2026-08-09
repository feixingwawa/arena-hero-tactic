# 交付：游戏理解总结 + 文档修订

- 日期：2026-08-07
- 请求：阅读 https://doc.arenahero.io/ ，总结游戏运行/玩法/目标/Agent 职责

## 交付文件

| 文件 | 动作 |
|------|------|
| `docs/GAME_UNDERSTANDING.md` | **新建** — 完整游戏理解 |
| `docs/STRATEGY.md` | **新建** — 战术原则与改造路线 |
| `README.md` | **重写** — 纠正 v0.13/维护费过时描述，对齐 v0.14 |

## 摘要（给用户）

### 游戏怎么运行

- 全服一个永久 2D 网格；无赛季重置、无 NPC。
- 每 Tick：`tick` → `state` → 你提交 1 份计划 → 结算 → 下一 Tick。
- 命令窗口 **15 秒**（全服共用，state 到达前已开始）。
- 资源每 **4** tick 按 chunk 配额刷新；Chunk 32×32。

### 怎么玩

1. Worker 采可见资源 → 回 Core 交付  
2. 用资源 spawn / heal / 修盾  
3. 扩到约 12 工 + 战斗单位，守住 Core  
4. （可选）抢 Beacon 拿双倍采集  

### 目标

- 官方无通关；有效目标 = **存活 + 资源正循环 + 编制**。  
- 本项目监控指标：**Core 资源 ≥ 100**。
- Beacon 是乘数，不是开局主线（默认策略：Core **远离** Beacon）。

### Agent 要完成什么

- 每 Tick 轻量决策并提交完整计划（SDK ≥0.2.9）。
- 自建地图记忆（服务端不回放）。
- **资源优先**；限制 Beacon 远征；防抖寻路；动态价格 spawn。

### 后续代码重点

1. 编制默认 12/4/4、max_pop=20
2. 远距 Beacon 放弃（`beacon_max_chase`）
3. 每 tick 强制非 dedicated 回 local（探索度/人口推进除外）
4. Core 远离 Beacon 的迁徙策略

## 线上快照（文档修订时）

- Agent 仍在跑：`pop=2 res=1→2`，出现过 `deposit`  
- 仍有 dedicated `d_beacon≈900+` 与偶发第二 Worker `phase=beacon` → 经济偏慢，需 P0 代码止血
