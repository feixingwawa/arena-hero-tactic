# Task: Dashboard 展示与真实行动对齐

> 状态：**A 已完成** · B 轻量完成 · C 后端轻量完成  
> 创建：2026-08-11  
> 完成：2026-08-11  
> 目标：解决「前端单位行动与实际不一致 / 慢约 1 tick / 货箱标签语义不清」  
> 原则：**指令层真源** — 展示本 tick 实际 submit 的命令，而非仅 phase 意图 + 多步估路  
> 验证：`pytest tests/` → **241 passed**

---

## 0. 背景与根因

当前管线（改造前）：

```
Turn(决策前位置)
  → decide() 写 intent(phase/target/route)
  → submit()
  → 异步 build_snapshot(旧位置 + 意图 + 全程 path_estimate)
  → 前端 ~500ms 轮询 /api/state/latest
```

| 现象 | 根因 |
|------|------|
| 比官方慢约 1 tick | 快照用 Turn 输入位（结算前）+ 异步队列 + 轮询 |
| 行动与黄线/标签不像 | 画的是 phase + 多步计划，不是 command + 一步 |
| 「货箱」难懂 | `to_cargo` 被译成「货箱」；属性 cargo 也叫「货箱」 |

**观测模型 ≠ 执行模型。** 主修方向：指令真源 + 默认只画本 tick 一步。

相关代码：

- [`bot/command_ledger.py`](bot/command_ledger.py) — 本 tick 指令真源（新增）
- [`bot/strategy.py`](bot/strategy.py) — `decide()` clear + instrument + export commands
- [`bot/dashboard.py`](bot/dashboard.py) — `build_snapshot` / `_build_unit` 合并 action/step_path
- [`bot/dashboard_static/index.html`](bot/dashboard_static/index.html) — 决策观测 UI
- [`bot/main.py`](bot/main.py) — submit 后 `dashboard_push`
- [`bot/economy.py`](bot/economy.py) / [`bot/combat.py`](bot/combat.py) — 经 instrument 自动记令

---

## 1. 里程碑总览

| 里程碑 | 名称 | 优先级 | 状态 |
|--------|------|--------|------|
| **A** | 指令真源 + 一步路径 + 文案（P0+P1 核心） | P0 | [x] **已完成** |
| **B** | 观感接近官方（延迟标明、推送、计划路径开关） | P1 | [x] **轻量完成**（poll 150ms + 计划开关；SSE 状态通道未做） |
| **C** | 跨 tick 结算回放 / 失败高亮（可选） | P2 | [~] **后端轻量**（prev_commands/prev_tick）；UI 双缓冲 / receipt 未做 |

**不建议：** 无 receipt 预测结算位置当真实；阻塞 submit 做全图 BFS；只加快 poll 不改 intent≠command；继续把 phase 当本 tick 行动。

---

## 2. 里程碑 A — 指令真源（先做，打中主诉）

### A1. Command Ledger（单一真相）

- [x] 新增模块 [`bot/command_ledger.py`](bot/command_ledger.py)
- [x] 定义结构 `CommandRecord`（unit_id/tick/action/direction/next_cell/target/phase/role/source）
- [x] 每 tick 决策开始时 `clear(tick)`（归档 prev）
- [x] **instrument_unit / instrument_turn**：与 SDK 调用同一处自动写入（move/deposit/heal/harvest/sweep/shoot/wait/spawn/repair*）
- [x] 导出：`get_commands()` / `DecisionResult.commands` + `prev_commands` / `prev_tick`
- [x] economy + combat + core 经 instrument 全覆盖；`enrich_from_intents` 回填 phase/role
- [x] 方向：运行时 **UP/DOWN/LEFT/RIGHT**；N/E/S/W 别名映射

**验收：** 每个实际 SDK 动作调用 ↔ ledger 一条；无「有线无令」或「有令无线」。✅

### A2. Snapshot 接入 commands

- [x] `decide()` 导出 commands；`build_snapshot` 读 `result.commands` / ledger
- [x] `_build_unit` 合并：`action`, `direction`, `next_cell`, `step_path`, `command`
- [x] 顶层：`commands`, `data_kind: "command"`, `prev_commands`, `prev_tick`
- [x] move → `step_path: [pos, next_cell]` 默认绘制；plan 仍走 path_estimate 可选
- [x] 非 move：不生成误导性长 path
- [x] `_compact_history_frame` 保留 action/direction/next_cell/step_path/commands/data_kind

**验收：** `/api/state/latest` 中单位 `action` 与 `commands[]` 一致；move 必有 `next_cell`。✅

### A3. 前端：action 主展示 + 默认一步路径

- [x] 主标签：`unitCommandLabel`（action 优先）> phase
- [x] phase 次要：「任务：前往掉落」等
- [x] 路径默认只画 `step_path`（实线）
- [x] 全程计划：工具栏开关，虚线 + 图例
- [x] 非 move：动作 chip，不画假长线

**验收：** 任意 tick 点单位 → 展示动作 = 本 tick 真实命令；黄线只指邻格。✅

### A4. 文案与模式降误导（P0）

- [x] 「实时」→「决策观测 · tick N」
- [x] 显示 tick、poll 年龄、`data_kind` · 令数
- [x] `to_cargo` →「前往掉落」
- [x] 单位属性 `cargo` →「载货」
- [x] 图例：实线 = 本 tick 一步；虚线 = 多 tick 计划
- [x] DIR_ZH 含 UP/DOWN/LEFT/RIGHT 与 N/E/S/W

**验收：** 新用户不再把 phase/计划线当成「已经走到的路」。✅

### A5. 测试与回归（里程碑 A）

- [x] [`tests/test_command_ledger.py`](tests/test_command_ledger.py)：record / instrument / clear-prev / decide 导出 / vanguard / step_path / deposit 无长 path / snapshot data_kind / compact history / 前端字符串
- [x] [`tests/conftest.py`](tests/conftest.py) autouse `reset_command_ledger`
- [x] 全量 `pytest tests/`：**241 passed**
- [x] **按仓库约定：测试脚本默认不推送远程**（本地验证即可）

---

## 3. 里程碑 B — 观感接近官方

### B1. 延迟可观测

- [x] UI：决策 tick、距上次成功拉取年龄、`data_kind`·令数
- [x] stale 判定保持清晰

### B2. 传输

- [ ] 状态通道 SSE/WebSocket（未做，可后续）
- [x] 权宜：`pollMs` **150ms**（live）
- [ ] 快照 worker 轻重帧拆分（未专项；现有异步 worker 保留）

### B3. 计划路径产品化

- [x] 工具栏：「显示计划路径（虚线）」
- [x] 开：虚线 plan；关：仅 step / 动作 chip
- [x] 默认关计划路径

### B4. 路径稳定（P3 部分）

- [x] Vanguard/Ranger 同样挂 action + 一步（instrument）
- [~] 估路/同源障碍/抖线：沿用既有 path_estimate；未专项 repath 缓存

**验收（轻量）：** 默认不再「长线指矿但单位在 deposit」；延迟数字可见。✅

---

## 4. 里程碑 C — 结算硬核对齐（可选）

### C1. 双缓冲 / 跨 tick

- [x] ledger：`clear` 归档 → `prev_commands` / `prev_tick`
- [x] `DecisionResult` + snapshot 导出 prev_*
- [ ] UI 双缓冲回放模式（未做）

### C2. Receipt / 事件（若 SDK 可得）

- [ ] 未做（SDK receipt 可后续接入）

### C3. 回放轴

- [ ] 未做；history 已保留 action/step_path 字段便于后续

**验收（后端轻量）：** 可从 snapshot 回答「上 tick 下了什么令」。✅ 部分

---

## 5. 实现切片（建议开工顺序）

```text
1. bot/command_ledger.py ✅
2. instrument_turn 覆盖 economy/combat/core ✅
3. DecisionResult.commands + prev_* ✅
4. dashboard.build_snapshot 合并 action/next_cell/step_path ✅
5. index.html：文案 + action 主标签 + 默认一步路径 ✅
6. tests/test_command_ledger.py + 全量 241 ✅
7. 里程碑 B 轻量 / C 后端轻量 ✅
```

提交策略（与现有约定一致）：

- 推送：**生产代码 + 文档**（`bot/*`、`task.md`、CHANGELOG）
- **不推送** 仅测试脚本改动（除非用户明确要求）— 本里程碑测试可本地保留

---

## 6. 验收清单（总）

### 必须（里程碑 A 完成即算主诉关闭）

- [x] 单位主标签 = 本 tick 真实 `action`（不是仅 phase）
- [x] move 默认路径 = 邻格一步，不是矿点/Core 全程实线
- [x] `commands[]` 与 SDK 调用一致（instrument）
- [x] 「货箱」语义拆开：掉落回收 vs 载货量
- [x] UI 不再暗示「与官方同帧实时渲染」→「决策观测」

### 加分（B/C）

- [x] 延迟数字可见；poll 150ms
- [x] 计划路径可选、虚线、可关闭
- [~] 跨 tick prev_commands 导出；receipt / UI 双缓冲未做

---

## 7. 文案对照表（实现时统一）

| 内部值 | 旧文案 | 新文案 |
|--------|--------|--------|
| phase `to_cargo` | 货箱 | 前往掉落 |
| unit `cargo` | 货箱 | 载货 |
| phase `to_resource` | 采矿 | 前往矿点（任务） |
| phase `deposit` | 交付 | 任务：交付（若 action 已是 deposit 可省略） |
| action `move` | （无） | 移动·北/东/南/西 |
| action `deposit` | （无） | 交付 |
| action `heal` | （无） | 治疗 |
| action `harvest` | （无） | 采集 |
| mode live | 实时 | 决策观测 · tick N |
| 方向 | N/E/S/W 仅 | UP/DOWN/LEFT/RIGHT + 别名 |

---

## 8. 完成定义（DoD）

1. [x] 里程碑 **A** 全部勾选，本地跑通相关测试  
2. [x] `task.md` 状态更新为「A 已完成」并注明日期  
3. [x] CHANGELOG Unreleased 记 Dashboard 指令真源对齐  
4. [x] 里程碑 B 轻量 / C 后端轻量已注明；SSE/receipt/UI 双缓冲保持可选未做项  

---

## 9. 状态跟踪

| 日期 | 事项 | 结果 |
|------|------|------|
| 2026-08-11 | 根因分析 + 方案 | 已输出方案；本 task 落盘 |
| 2026-08-11 | 里程碑 A | **完成**：ledger + instrument + snapshot + 前端 + 单测 |
| 2026-08-11 | 里程碑 B | **轻量完成**：poll 150ms、延迟展示、计划路径开关 |
| 2026-08-11 | 里程碑 C | **后端轻量**：prev_commands/prev_tick；UI/receipt 可选未做 |
| 2026-08-11 | 全量测试 | **241 passed**（约 123s） |

---

## 10. 一句话

> 把 Dashboard 从「意图 + 多步估路浏览器」改成「本 tick 指令真源浏览器」：  
> 位置用当前 Turn，标签与箭头只用真实 action + next_cell，全程路径降为可选虚线计划。
