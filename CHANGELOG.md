# Changelog

本文件记录 `arena-hero-tactic` 面向用户的版本变更（Keep a Changelog 风格）。  
规则基准：Arena Hero **玩法 v0.14** · API v0.1 · SDK **`arena-hero` ≥ 0.2.9**。

格式约定：

- **Added** — 新能力
- **Changed** — 既有行为调整
- **Fixed** — 缺陷修复
- **Docs** — 文档 / 交付说明

详细日更长文见 `deliverables/`（如 `CHANGELOG-2026-08-11.md`）。

---

## [Unreleased]

（无）

---

## [2026-08-11] — Core 腾核 / 治疗 / 路径 / 信标 / 看门狗

生产提交：`2a21cfc` · 日更文档：`b49c072`  
详述：[`deliverables/CHANGELOG-2026-08-11.md`](deliverables/CHANGELOG-2026-08-11.md)

### Fixed

- **单位挤 Core 无法交付**：空货 `RETREAT` 只逼近到 `retreat_hold_radius`（默认 2）后外散，不踩核；战斗非 HEAL 寻路把 Core 当软障；`max_core_healers`（默认 1）限制同时进核治疗的 V+R。
- **治疗终点停在「核心上一格」**：删除 HEAL 的 `heal_hold`；离核 HEAL 目标恒为 Core；已在 Core 的空货 HEAL **本 tick 直接 `heal()`**，不再因场上满货工人 `yield_core` 核↔邻格抖动。
- **矿工遇敌方矿工不绕行**：路径软障纳入全部可见敌人（含敌方 WORKER）；敌工仍不触发 Worker RETREAT。
- **Dashboard 路径像 45° 斜走**：后端 `_ensure_cardinal_chain` / `_manhattan_staircase`；前端 `expandCardinalWaypoints`；滤障与降采样后禁止非四连通 `lineTo`。运行时本就只走 UP/DOWN/LEFT/RIGHT。
- **会话卡死 / 断线不恢复**：`bot/main.py` session watchdog、硬杀底层 socket、stale/client-closed 可重连；`scripts/deploy.py` 可自动拉起 `agent_watchdog`。

### Changed

- **信标集体推进门控**：由「探索度 ≥80% **或** 人口 ≥10」改为「探索度 ≥80% **并且** 人口 ≥10」（`beacon_push_explore_ratio` ∧ `beacon_push_population`）。早期 `beacon_min_workers` dedicated 逻辑不变。
- 满货 Worker 不分配 `HEAL`（交付优先于治疗占核）。

### Docs

- 新增 `deliverables/CHANGELOG-2026-08-11.md`
- 同步 `README.md` / `docs/STRATEGY.md` 中信标与腾核相关说明

### 验证

- 本地 `pytest tests/`：**230 passed**（测试增量可仅存本地、未强制入仓）
- 线上：`heal_at_core` 出现且无 `yield_core` 对抖；路径 API `non_cardinal=0`

---

## [2026-08-09] — 战术规则与部署

### Added

- 突击编制 **2V+2R** 朝敌方 Core（`STRIKE`）
- 目标编制已满且资源充裕时，按比例继续生产（受 `max_population` 硬顶）
- **跨平台一键部署**（Win / macOS / Linux）：`deploy.bat` / `deploy.sh` / `scripts/deploy.py`、`ONE_CLICK_DEPLOY.md`
- 交互式 API Key 安装（`install.py` / one-click）

### Changed

- 敌方 **WORKER 不计入近威胁**（不触发无意义撤退）
- 路径对敌方单位绕行策略加固

### Fixed

- Vanguard / Ranger 使用与 Worker 同级的 guided 寻路；Core 上短暂 heal 后让位
- Dashboard 地图空白（过大 history 轮询约 40MB）

### Added (Dashboard / 运维)

- Dashboard 默认可绑 `0.0.0.0` 公网观测
- README 一键安装说明

相关提交（摘录）：`e72fabe`、`c4ab72f`、`8993237`、`cf9b2a5`、`8226bca`、`ffab4e6`

---

## [2026-08-08] — 混合高效 + 项目总览

详述：[`deliverables/HYBRID_EFFICIENT-2026-08-08.md`](deliverables/HYBRID_EFFICIENT-2026-08-08.md)、[`deliverables/PROJECT_SUMMARY-2026-08-08.md`](deliverables/PROJECT_SUMMARY-2026-08-08.md)

### Changed

- 默认编制 **12W / 4V / 4R，max_population=20**（基础价满编）
- `beacon_max_chase` 默认极大（≈不限距，由人口/探索与 `beacon_min_workers` 门控）
- `spiral_max_ring=24`、`sector_count=4`、`early_game_pop=6`
- 远距 / 人少时禁止或丢弃 dedicated Beacon 追逐，优先本地采集

### Added

- Worker 状态导出供 Dashboard（intent / spiral / route）
- 本地 Dashboard 能力总览（单位、路径、障碍、已探、资源）

### 验证（当时）

- pytest **111 passed**；inline **70 passed**

---

## [2026-08-07] — v0.14 规则适配与探索止血

详述：

- [`deliverables/delivery-2026-08-07-optimized.md`](deliverables/delivery-2026-08-07-optimized.md)
- [`deliverables/delivery-2026-08-07-sdk-upgrade.md`](deliverables/delivery-2026-08-07-sdk-upgrade.md)
- [`deliverables/delivery-2026-08-07.md`](deliverables/delivery-2026-08-07.md)
- [`deliverables/GAME_SUMMARY-2026-08-07.md`](deliverables/GAME_SUMMARY-2026-08-07.md)
- PRD：`PRD-增量优化-2026-08-07.md`、`PRD-探索优化-2026-08-07.md`

### Added

- `bot/rules.py`：动态单位价格（SDK + 本地回退）
- `bot/memory.py`：资源点记忆、回访、障碍累积、cargo 掉落
- 螺旋扫掠 / 扇区分散 / chunk 辅助（`pathing` + `economy`）
- 地图记忆驱动采集与回访

### Changed

- **去除维护费**逻辑（对齐 v0.14）
- 硬边界 recall → **软回撤**（stall 后 ring+1 等），抑制横跳
- SDK ≥0.2.9 自检与规则适配

### Fixed

- Worker 远距横跳 / 探索跑飞 / 撤退振荡
- 可见资源采集与 return_deposit 正循环

### 验证（当时）

- pytest **83/83**；inline **58/58**；模拟 d=36/37 零横跳

---

## 配置速查（当前默认，以 `bot/config.py` 为准）

| 参数 | 默认 | 含义 |
|------|------|------|
| `max_population` | 20 | 人口硬顶 |
| `target_workers/vanguards/rangers` | 12/4/4 | 编制目标 |
| `retreat_hold_radius` | 2 | 空货撤退停靠半径（不进核） |
| `max_core_healers` | 1 | 同时进核治疗的战斗单位上限 |
| `beacon_push_population` | 10 | 信标推进人口阈值（须与探索度同时满足） |
| `beacon_push_explore_ratio` | 0.8 | 本地探索度阈值 |
| `beacon_min_workers` | 3 | 允许 dedicated 的最少 Worker |
| `beacon_max_chase` | 10000 | Core→Beacon 曼哈顿上限（≈不限） |

---

## 链接

- 仓库：https://github.com/feixingwawa/arena-hero-tactic
- 官方文档：https://doc.arenahero.io/zh-Hans/
- 战术原则：[`docs/STRATEGY.md`](docs/STRATEGY.md)
- 游戏理解：[`docs/GAME_UNDERSTANDING.md`](docs/GAME_UNDERSTANDING.md)
