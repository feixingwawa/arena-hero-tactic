# Arena Hero Tactic — 项目总结报告

> 交付日期：2026-08-08
> 规则版本：Arena Hero v0.14 / API v0.1 / SDK `arena-hero` ≥0.2.9
> 战术定位：资源优先 + 均衡防守（本仓库自研实现）

---

## 1. 项目一句话

本仓库是一个 **Python 战术 Agent**：每 tick 读取官方 SDK 的 `Turn` 状态，在 15s 窗口内为 Core / Worker / Vanguard / Ranger 排队动作并 `submit()`。没有官方「胜利条件」——以 **存活、经济滚雪球、满编阵容、可选 Beacon 争夺** 为持续目标。

---

## 2. 游戏与 Agent 职责

| 维度 | 内容 |
|------|------|
| 地图 | 永久共享 2D 网格，障碍/资源/敌人/信标可见性受视野约束 |
| 时间 | 每 tick 决策窗口约 15s；过期提交被跳过 |
| 经济 | 无维护费；动态单位价格；Worker 采集 → Core 交付 |
| 人口 | 默认 `max_pop=20`，目标编制约 12W / 4V / 4R |
| Beacon | 可选；最多 1 名专职 Worker；追逐距离上限约 64 |
| Agent | `decide(turn) → CommandPlan`；本仓库额外挂 Dashboard 快照 |

核心循环：

```
SDK turns() → MemoryMap.observe → roles.assign_roles
  → economy.command_workers / combat.* / economy.command_core_economy
  → DecisionResult → turn.submit()
  → (可选) dashboard.safe_push_snapshot
```

---

## 3. 架构总览

```
bot/
  main.py         CLI / 重连循环 / Dashboard 钩子
  strategy.py     decide() 编排
  roles.py        角色分配 HARVESTER/SCOUT/RETREAT/HEAL/GUARD
  economy.py      采集/交付/生产/螺旋探索 + WorkerIntent 导出
  combat.py       威胁评估、防守圈、sweep/shoot、Core 治疗
  pathing.py      几何、guided 寻路、螺旋目标、reconstruct_path
  memory.py       资源状态机、障碍缓存、掉落 cargo、chunk 记忆
  rules.py        动态 unit_cost 等规则
  config.py       TacticConfig 不可变参数
  dashboard.py    环缓冲 + Flask REST/SSE + build_snapshot
  dashboard_static/
    index.html    可视化前端（官网风格）
    assets/       logo.svg / favicon.svg / official-styles.css
```

### 3.1 数据流（Dashboard）

1. `main.run_session` 在 `decide` 后调用 `get_worker_states()`（若存在）  
2. `safe_push_snapshot(turn, result, WORLD_MEMORY, econ_states)`  
3. `build_snapshot` 序列化 core/units/beacon/resources/obstacles/memory  
4. 对每个有 `econ.target` 的单位调用 `pathing.reconstruct_path` 填 `path_estimate`  
5. 前端轮询 `/api/state/latest` + SSE `/api/logs/stream` 渲染 Canvas

### 3.2 关键模块职责

| 模块 | 业务逻辑要点 |
|------|----------------|
| **economy** | 资源 claim 去重；return 路径矿点预约；曼哈顿启发式 + estimate 消歧；双中心螺旋探索；Beacon 阶段推进；`WorkerIntent` 记录本 tick 目的地 |
| **pathing** | `guided_step_toward` 防对抖/环路；`reconstruct_path` dry-run 输出 waypoints；螺旋/扇区几何 |
| **memory** | 资源 depleted/revisit；障碍 block 计数；explored chunks；dropped cargo |
| **combat** | 近威胁 → Core heal 优先；Vanguard 环位；Ranger 射程射击 |
| **roles** | 按血量/威胁/扇区分配角色 |
| **dashboard** | 零伪造：缺 tick/result → `None`；`path_estimate={steps,waypoints,blocked,destination}` |

---

## 4. 战术原则（摘要）

1. **经济止血优先**：有 cargo 先回 Core；空载优先可见矿 / 记忆回访 / cargo 回收 / 螺旋探索  
2. **满编不超编**：动态价格下按 `WORKER → VANGUARD → RANGER` 优先级补位  
3. **Core 安全**：近威胁时治疗优先于生产  
4. **探索**：local 螺旋 + beacon 方向推进；软回撤替代硬 recall 边界  
5. **路径质量**：LoopTracker 检测小范围徘徊 → 强制 repath

默认参数见 [`bot/config.py`](../bot/config.py)（以代码为准）。

---

## 5. Dashboard 可视化能力（本轮交付）

| 能力 | 实现 |
|------|------|
| 官网视觉 | Docusaurus dark tokens：`#1b1b1d` / `#242526` / primary `#3578e5` / logo stroke `#4591c5` |
| 官方素材 | 自 docs 站合法取得 `logo.svg`、`favicon.svg`；CSS tokens 参考 `official-styles.css` |
| 目的地 | 单位 `econ.target` + 阶段标签（deposit / to_resource / local / beacon …） |
| 完整导航路径 | `path_estimate.waypoints` 折线；无估计时回退直线虚线 |
| 障碍物 | memory.obstacles 实心格 + obstacle_blocks 热图 |
| 交互 | 帧回放、缩放、单位 Popover（含 path steps / destination）、日志 SSE |

---

## 6. 后端导出契约（路径可视化）

### 6.1 `bot.economy.WorkerIntent` / `get_worker_states()`

```python
@dataclass
class WorkerIntent:
    target: Optional[Position]
    ring: Optional[int]
    sector: Optional[int]
    phase: Optional[str]   # deposit|to_resource|retreat|local|beacon|...
    role: Optional[str]
    dedicated: Optional[bool]
```

- `command_workers` 每 tick 清空 `_worker_intents`，各分支 `_set_intent(...)`  
- `get_worker_states()` 合并 `_spiral_state` + intent（intent 优先）  
- `main` 已按名探测 `get_worker_states` / `WORKER_STATES` 等

### 6.2 `path_estimate` 字段

```json
{
  "steps": 12,
  "waypoints": [[4,8],[4,7],[3,7],...],
  "blocked": [[2,3]],
  "destination": [1,2]
}
```

由 `dashboard._build_path_estimate` 调用 `pathing.reconstruct_path(..., memory=None)`，**不写回** memory。

---

## 7. 测试与运行

```bash
# 离线单测（Dashboard / pathing / economy 等）
python -m pytest tests/test_dashboard.py tests/test_pathing.py -q

# Agent + Dashboard
python -m bot.main --dashboard --dashboard-port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

严格性约定：

- 无真实 `tick` / `result` → 快照丢弃（禁止 0 伪造）  
- Flask 为可选依赖；未安装时不污染主循环  

---

## 8. 本轮代码改动清单

| 文件 | 变更 |
|------|------|
| [`bot/pathing.py`](../bot/pathing.py) | 新增 `reconstruct_path`；`estimate_path_steps` 委托之 |
| [`bot/economy.py`](../bot/economy.py) | `WorkerIntent`、`_set_intent`、`get_worker_states`；command 分支写意图 |
| [`bot/dashboard.py`](../bot/dashboard.py) | 障碍集合 + path_estimate 填充 |
| [`bot/dashboard_static/index.html`](../bot/dashboard_static/index.html) | 官网风格全量重构 UI + 路径/目的地/障碍渲染 |
| [`bot/dashboard_static/assets/*`](../bot/dashboard_static/assets/) | logo / favicon / official CSS 参考 |
| 本文件 | 项目总结报告 |

---

## 9. 风险与后续

1. **路径 dry-run 上限 64 步**：极远目标可能截断，前端仍绘已有 waypoints + destination 标记  
2. **意图仅覆盖 command_workers 主分支**：Core 生产/战斗单位无 path_estimate（符合设计）  
3. **官方素材**：仅使用公开文档站 SVG/CSS token，无版权敏感商业贴图  
4. 后续可做：单位朝向箭头、多选对比路径、历史帧 diff 热力  

---

## 10. 一句话结论

**Arena Hero Tactic** 是一套以经济与生存为中心的 tick 决策框架；本轮在完整架构梳理之上，打通了 **意图导出 → 路径重建 → 官网风格 Dashboard** 的可视化闭环，使每个 Worker 入口的目的地、导航折线与障碍形态均可被准确观察与回放。
