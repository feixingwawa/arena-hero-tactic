# Arena Hero 游戏理解与 Agent 职责

- 版本：v0.14 规则 / API v0.1 / SDK ≥0.2.9
- 日期：2026-08-07
- 来源：[官方文档](https://doc.arenahero.io/zh-Hans/) · [规则速查](https://doc.arenahero.io/zh-Hans/reference/numbers) · [世界与 Tick](https://doc.arenahero.io/zh-Hans/rules/world-and-ticks)

---

## 1. 这是什么游戏

Arena Hero 是一个**永不重置、全服共享**的二维网格世界。没有赛季、没有 NPC、没有服务器托管舰队。每个账号同一时间最多一个存活 **Core**。

你的 **Agent** 不「玩 UI」，而是：

1. 通过 WebSocket 收到本 Tick 的私有 `state`（视野内世界切片）
2. 在 **15 秒全局命令窗口**内决定 Core 与全部 Unit 的动作
3. POST **一份** `CommandPlan`（同来源再提交会覆盖旧计划）
4. 下一 Tick 的 `state.events` 告诉你结算结果

世界在你离线时仍在推进；Agent 适合长期无人值守运行。

---

## 2. 怎么玩（核心循环）

```
tick 公告 → state 到达 → 决策 → 提交计划 → 服务端结算 → 下一 tick
```

### 2.1 经济循环（生存基础）

```
探索/侦察 → 发现 RESOURCE → Worker harvest → 回 Core deposit
  → Core 资源增加 → spawn 单位 / heal / repair_shield
  → 更大人口 → 更强采集与防守
```

关键数字：

| 项 | 值 |
|----|----|
| 命令窗口 | 15 秒（全服共用，`state` 到达前已开始） |
| 资源刷新 | 每 **4** 个已结算 Tick（约 1 分钟） |
| Chunk | 32×32；配额 `max(2, floor(16×8/(8+ring)))`，中心环 16 点/块 |
| 普通 harvest | 1 点 → **1** 资源；持有 Beacon 时 → **2** 资源 |
| Core 容量 | `max(10, population × 5)` |
| 初始 | Core HP5/盾5/资源5 + 免费 1 Worker |
| 维护费 | **v0.14 已取消** |

### 2.2 单位与生产

| 单位 | HP | 视野 | 基础价 (pop 0–19) | 作用 |
|------|----|------|-------------------|------|
| Worker | 2 | 3 | 5 | 采集、交付、侦察、捡 Beacon |
| Vanguard | 4 | 4 | 10 | 近战邻格 sweep |
| Ranger | 2 | 5 | 12 | 八向 1–3 格射击 |

人口 ≥20 后启动**动态价格**（每 5 人一档 ×1.3 向上取半）：

```
k = max(0, floor((pop - 20) / 5) + 1)
price = round_half_up(base × (13/10)^k)
```

**本仓库默认编制**：12W + 4V + 4R = **20**（用满基础价区间，不自动冲 21+；见 `STRATEGY.md` / `bot/config.py`）。

### 2.3 战斗与结算顺序（摘要）

固定顺序（不可跳过）：自毁 → 移动/Core 迁徙完成 → Beacon 拾放 → **harvest/deposit** → 战斗快照与同时结算伤害 → Core 自毁 → 治疗 → **spawn** → Core 重生尝试 → 每 4 tick 资源补额。

含义：

- 同 Tick 的 deposit 可以立刻资助同 Tick 的 heal/spawn（预算要自己算好）
- 移动在采集之前：Worker 必须先到位再 harvest
- Core 迁徙 **每格 4 Tick**，成本高，不能当「每 tick 挪一步」

### 2.4 视野与记忆

- Core 视距 5 / Worker 3 / Vanguard 4 / Ranger 5（曼哈顿）
- 障碍挡视线与 Ranger 弹道；单位/Core/资源**不挡**视线与射击
- 服务端**不回放**历史地图 → Agent 必须自建 `MemoryMap`
- 障碍永久正确；资源记忆会过期（被采、刷新、雾中变化）

### 2.5 Champion Beacon（冠军信标）

| 项 | 规则 |
|----|------|
| 初始位置 | 公开坐标 `[0, 0]`（可被搬动） |
| 持有效果 | 己方 Worker harvest **×2**；Core 盾上限 10 |
| 信息边界 | 坐标始终公开；`status`/`carrier_id` 仅在可见时完整 |
| SDK 注意 | `status=None` ≡ 地面未携带，**可写 position**；仅 `CARRIED` 应清空推进目标 |

**战略取舍（默认偏 retreat）**：

- 远距离 Beacon（常 d≈数百～上千）**不值得**全员冲刺
- Core 应**远离** Beacon 与可见威胁，优先本地采集与生存
- 抢 Beacon 是可选终局加成，不是开局目标

---

## 3. 目标是什么

官方没有「通关结局」。有效目标由玩家/Agent 自定，常见层次：

| 层级 | 目标 | 说明 |
|------|------|------|
| P0 生存 | Core 存活 | 被毁同 Tick 可重生，但舰队与库存风险极高 |
| P0 经济 | 稳定 deposit 正循环 | 无资源 = 无法 spawn/heal |
| P1 编制 | 接近 12/4/4=20 | 基础价满编，攻防兼备 |
| P1 地图 | 本地 chunk 资源与陈旧区侦察 | 记忆 + 回访刷新点 |
| P2 优势 | 拾取/持有 Beacon | 双倍采集，高风险高回报 |
| P2 对抗 | 有限清除孤立威胁 / 避活跃舰队 | 不为击杀而击杀 |

**本项目用户监控目标**：Core 库存资源达到 **≥100**（过程指标，非官方胜负条件）。

---

## 4. Agent 必须完成什么

### 4.1 协议职责（硬性）

1. 连接 API（`ARENA_HERO_API_KEY`），订阅 WebSocket
2. 每个 Tick 在窗口内提交**完整**计划（每个存活对象 ≤1 动作）
3. 决策必须轻量（通常 <1–2s；官方窗口 15s 且 state 到达后剩余更少）
4. 断线可恢复；空计划优于卡死不提交
5. SDK ≥**0.2.9**（0.2.8 会 ProtocolError）

### 4.2 战术职责（本仓库）

| 模块 | 职责 |
|------|------|
| `strategy.decide` | 观察 → 威胁评估 → 角色分配 → 经济/战斗指令排队 |
| `economy` | Worker 采集/交付/探索；Core spawn 优先级 |
| `combat` | Vanguard/Ranger 防守圈、sweep/shoot、Core heal/盾 |
| `memory` | 资源 VISIBLE/DEPLETED/REVISIT；障碍；已探 chunk |
| `pathing` | 曼哈顿步进 + 方向记忆防抖；螺旋/Beacon 目标 |
| `main` | I/O、日志、重连；**不**写战术 |

### 4.3 决策优先级（建议）

```
1. Core 危急 → heal / repair_shield / 迁徙远离威胁与 Beacon
2. 满货 Worker → 合法回城 deposit（可与 Core 同 Tick 预算联动）
3. 可见资源 → 一对一匹配 harvest（仅 VISIBLE，防空采）
4. 本地 spiral / 陈旧 chunk 侦察
5. 补 Worker → 再补 Vanguard/Ranger 至目标编制
6. （可选）单人 dedicated 接近 Beacon；禁止非 dedicated 全员冲 Beacon
7. 活跃敌舰队 → 避战收缩；孤立静止目标 → 有限清除
```

---

## 5. 与本仓库实现的对照

| 原则 | 本仓库实现 | 状态 |
|------|------------|------|
| 无维护费 v0.14 | 经济已去 upkeep；`population_upkeep` 仅 deprecated | ✅ |
| 动态单位价 | `bot/rules.py` + SDK `unit_cost` | ✅ |
| 资源记忆 + 视线耗尽 | `MemoryMap` VISIBLE-only harvest | ✅ |
| 防抖寻路 | `clamp_step_toward_memo` | ✅ |
| 螺旋本地探索 | `SpiralState` local phase | ✅ |
| 软回撤外扩 ring+1 | 已修（禁止 stall ring-1 陷阱） | ✅ |
| Beacon status=None 可写 | `strategy` 同步已修 | ✅ |
| 非 dedicated 不冲 Beacon | soft-recall 仅 dedicated；探索度/人口达标可集体推进 | ✅ |
| 编制 12/4/4=20 | `TacticConfig` 默认 12/4/4 max20 | ✅ |
| Core 远离 Beacon | 评估日志-only，默认不真正迁徙 | ⚠️ |
| 分层威胁状态机 | 基础 threat + 撤退 | ⚠️ |
| 资源优先于 Beacon 推进 | `beacon_max_chase` + min_workers + push 门控 | ✅ |
| 台阶型 12W/4V/4R 爬坡节奏 | `choose_spawn` v2 | ✅ |
| 负载 Worker 发现矿→调度择优自采 vs 派工 | dispatch_mine + estimate_path_steps | ✅ |
| 双中心螺旋（内环 Core / 外环 Beacon） | dual_spiral_target + beacon_oriented_spiral | ✅ |
| 历史障碍 ≥3 次主动降权避障 | clamp_score 降权 + obstacle_cache | ✅ |

线上典型病态（日志）：`pop=2 res=1~2`，一名 Worker `dedicated_beacon` 远征，另一名偶发 `phase=beacon` 或遇威胁 retreat，**本地 harvest 饥饿**。

---

## 6. 文档索引

| 文档 | 内容 |
|------|------|
| 本文 | 游戏怎么运行、怎么玩、目标、Agent 职责 |
| `docs/STRATEGY.md` | 本仓库战术原则与改造路线 |
| `docs/system_design.md` | 优化完善架构与任务分解 |
| `docs/system_design-explore.md` | Beacon 导向探索设计 |
| `README.md` | 安装、启动、默认参数（须保持 v0.14 同步） |
| 官方中文 | https://doc.arenahero.io/zh-Hans/ |

---

## 7. 一句话总结

> **Arena Hero = 共享永久网格上的实时策略沙盒。**  
> Agent 每 15 秒看一次私有视野、交一份计划；胜负不靠「通关」，靠 **Core 存活 + 资源正循环 + 编制扩张**，Beacon 是可选乘数。  
> 本 Agent 的首要交付是：**稳定采集交付 → 人口成型 → 防守不崩**；任何导致全员远征 Beacon、本地 res 卡死的设计都是错误方向。
