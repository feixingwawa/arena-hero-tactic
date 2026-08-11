# 更新日志 — 2026-08-11

**项目**：`arena-hero-tactic`  
**规则**：Arena Hero 玩法 v0.14 / API v0.1 / SDK `arena-hero` ≥0.2.9  
**主提交**：`2a21cfc`（本日志随后续 commit 推送）  
**仓库**：https://github.com/feixingwawa/arena-hero-tactic

---

## TL;DR

本轮以线上问题驱动，完成五类修复/加固：

1. **腾出 Core 交付通道**（单位挤核导致 deposit 失败）
2. **Worker 治疗真正上核 heal**（不再停在「核心上一格」抖动）
3. **矿工绕开敌方矿工**（路径软障；仍不因敌工 RETREAT）
4. **信标集体推进改为 AND 门控**（探索度 ≥80% **且** 人口 ≥10）
5. **Dashboard 路径强制四连通** + **会话看门狗 / 部署自愈**

本地全量 `pytest`：**230 passed**（含未推送的测试用例）。生产代码已推 `origin/main`。

---

## 1. 问题与动机

| 现象 | 根因摘要 | 处理 |
|------|----------|------|
| 多人挤在 Core，满货无法交付 | 空货撤退/治疗/守环全员踩核 | `retreat_hold_radius`、治疗名额、战斗路径 Core 软障 |
| Dashboard/意图终点在「核心旁一格」，治不了 | `heal_hold` 把 HEAL 指到 hold 环；上核又 `yield_core` 与满货互让形成核↔邻格抖动 | HEAL 目标恒为 Core；上核本 tick 直接 `heal()` |
| 矿工遇敌方矿工不绕行 | 路径软障只含战斗单位 | `soft_enemy_obs` 含全部可见敌人（含 WORKER） |
| 探索或人数单条件就冲 Beacon | `_beacon_push_ready` 为 OR | 改为探索度 **且** 人口 |
| Dashboard 看起来像 45° 斜走 | 运行时本就四连通阶梯；滤障/降采样后 `lineTo` 会画非法斜跳 | 后端 `_ensure_cardinal_chain` + 前端 `expandCardinalWaypoints` |
| Agent 卡死 / 断线不恢复 | turns 阻塞、无硬杀 socket | session watchdog + hard-kill + deploy 拉起 watchdog |

---

## 2. 行为变更（铁律）

### 2.1 Core 占用

- **空货 RETREAT**：只逼近到 `retreat_hold_radius`（默认 2），在 hold 带外散，**不踩核**堵 deposit。
- **战斗单位**：非 `HEAL` 寻路把 Core 当软障；`max_core_healers`（默认 1）限制同时进核治疗的 V+R。
- **满货 Worker**：始终可走 Core 交付；交付路径可把其它己方单位当软障，减少核周叠堵。

### 2.2 治疗（HEAL）

- **必须站在 Core 格才能 `heal()`**（官方规则）。
- 离核 HEAL：目标 **始终是 Core**，禁止再改 `heal_hold` 环（否则终点停在「核心上一格」）。
- 已在 Core 的空货 HEAL：**本 tick 直接 heal**，不再因「场上有人满货」先 `yield_core`（Worker 一 tick 回满；满血后自然不再 HEAL，让出核）。
- 战斗单位 HEAL 仍受 `max_core_healers` 名额约束；上核 heal 后下一 tick 非 HEAL 则 `leave_core`。

### 2.3 敌方矿工

- **路径**：可见敌方 WORKER 进入软障碍，矿工绕行。
- **角色**：敌方 WORKER **仍不触发** Worker RETREAT / 近威胁（与既有战术一致）。

### 2.4 信标推进

- 集体向 Beacon 推进条件：  
  `本地探索度 ≥ beacon_push_explore_ratio(0.8)` **并且** `人口 ≥ beacon_push_population(10)`。  
- 早期 `beacon_min_workers` dedicated 逻辑仍独立存在。

### 2.5 路径与 Dashboard

- 运行时：`CARDINAL_DELTAS` 四连通 A\*/guided，**无对角 MOVE**。
- Dashboard `path_estimate`：与 runtime route 同源；相邻路点强制曼哈顿一步；前端绘制前展开阶梯，禁止对角 `lineTo`。
- 远看仍可能像 45°：那是 **右一步 + 上一步** 的视觉压缩，不是斜走。

### 2.6 运维

- `bot/main.py`：会话看门狗、硬杀底层 socket、stale/client-closed 可重连。
- `scripts/deploy.py`：部署后可自动拉起 `agent_watchdog`。

---

## 3. 代码变更清单（已推送）

| 文件 | 变更要点 |
|------|----------|
| `bot/config.py` | `retreat_hold_radius`、`max_core_healers`；信标 push 文档改为 AND |
| `bot/economy.py` | hold 撤退、HEAL 上核必治、敌工软障、`_beacon_push_ready` AND、交付软障 |
| `bot/combat.py` | `_combat_path_obstacles(allow_core=…)`；HEAL 用 `obstacles_heal` |
| `bot/roles.py` | 战斗治疗名额排序；满货 Worker 不派 HEAL |
| `bot/dashboard.py` | `_manhattan_staircase` / `_ensure_cardinal_chain`；滤障与降采样后强制四连通 |
| `bot/dashboard_static/index.html` | `expandCardinalWaypoints` 后再 stroke |
| `bot/main.py` | session watchdog / hard-kill / 断线自愈 |
| `scripts/deploy.py` | 启动 watchdog |
| `README.md` / `docs/STRATEGY.md` | 参数与策略说明同步 |

**未推送（仅本地）**：`tests/test_*.py` 增量用例（按仓库约定：不推测试脚本改动）。

---

## 4. 配置默认值（摘录）

```text
retreat_hold_radius      = 2
max_core_healers         = 1
unit_heal_hp_threshold   = 1   # Worker 残血
beacon_push_population   = 10
beacon_push_explore_ratio= 0.8  # 与人口同时满足
beacon_min_workers       = 3
beacon_max_chase         = 10000
```

---

## 5. 验证记录

| 项 | 结果 |
|----|------|
| `pytest tests/` | **230 passed**（约 2m04s，含本地测试增量） |
| 线上 HEAL | 重启后出现 `heal_at_core`，不再 `heal`↔`yield_core` 对抖 |
| 线上路径 API | multi-waypoint 路径 **non_cardinal 段 = 0** |
| 信标 | 日志 `beacon_push:pop=…:explore=…` 仅在双条件满足时出现 |
| Git | 生产修复 `2a21cfc` 已在 `origin/main`；本 CHANGELOG 另 commit 推送 |

---

## 6. 线上观察建议

1. Dashboard **硬刷新**（Ctrl+F5）加载新 `index.html`。
2. 伤员路径终点应落在 **Core 坐标**；日志应有 `heal_at_core`，随后满血离开。
3. 满货 `return_deposit` 时核周不应长期被空货/治疗单位堵死。
4. 若 tick 停滞，确认 `agent_watchdog` 与 deploy 是否同时在跑。

---

## 7. 相关历史交付

- `deliverables/HYBRID_EFFICIENT-2026-08-08.md` — 混合高效 / Beacon 门控初版  
- `deliverables/PROJECT_SUMMARY-2026-08-08.md` — 项目总览 + Dashboard  
- `deliverables/delivery-2026-08-07*.md` — 螺旋探索 / SDK / 振荡修复  

---

## 8. 一句话

> **交付优先腾核；治疗必须上核一 tick 回满；路径只走四连通；信标要人齐且探够；断线有看门狗。**
