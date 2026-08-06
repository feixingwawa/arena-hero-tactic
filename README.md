# Arena Hero 战术框架 —「均衡扩张 + 防守」

面向 [Arena Hero](https://doc.arenahero.io/) 的**可长期运行** Python 战术客户端。  
基于官方 SDK [`arena-hero`](https://pypi.org/project/arena-hero/)，你掌控游戏循环；本仓库只做决策。

> 社区战术示例，非官方客户端。仓库内**不包含**任何真实 API Key。

## 战术目标

| 阶段 | 行为 |
|------|------|
| 早期 | 优先生产 Worker，采集与交付，快速攒资源 |
| 中期 | 维持经济，同时生产少量 Vanguard / Ranger 做周边防守 |
| 全程 | Core 周边形成防守圈；有威胁时优先防守/反击，再考虑扩张 |
| 人口 | 默认压在 18 以下，**upkeep = 0**，避免维护费自杀式膨胀 |
| 生存 | Core / 单位低血时有条件治疗、修盾 |

## 项目结构

```
arena-hero-tactic/
  README.md
  pyproject.toml
  requirements.txt
  .env.example
  bot/
    __init__.py
    main.py         # 入口：读 Key → 连接 → turns 循环
    config.py       # 战术参数
    strategy.py     # decide(turn) 主策略
    economy.py      # 采集/交付/生产/维护费
    combat.py       # 威胁、防守圈、sweep/shoot、治疗
    pathing.py      # 曼哈顿移动、环位、射程判定
    roles.py        # harvester/guard/retreat/heal 角色分配
  tests/
    conftest.py     # 离线 Turn/Unit stub
    test_economy.py
    test_combat.py
    test_strategy.py
```

## 环境要求

- Python **3.11+**
- 依赖：`arena-hero`（运行时）、`python-dotenv`（可选）、`pytest`（测试）

## 安装

```bash
cd arena-hero-tactic
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
```

开发/测试额外依赖已包含在 `requirements.txt` 的 pytest 中。

## 配置 API Key

1. 复制环境变量模板：

```bash
copy .env.example .env          # Windows
# cp .env.example .env          # Unix
```

2. 编辑 `.env`，填入你的 Key：

```
ARENA_HERO_API_KEY=你的_API_KEY
```

3. **不要**把 `.env` 或真实 Key 提交到 Git。

也可直接设置环境变量（优先级高于 `.env`）：

```bash
set ARENA_HERO_API_KEY=你的_API_KEY          # Windows CMD
# export ARENA_HERO_API_KEY=你的_API_KEY    # Unix
```

## 启动

```bash
python -m bot.main
```

常用参数：

```bash
python -m bot.main -v                 # 调试日志（打印每单位动作）
python -m bot.main --max-turns 50     # 只跑 50 个 turn 后退出
```

程序会在每个 Tick 收到 `state` 后尽快调用 `decide(turn)` 并 `turn.submit()`。  
官方命令窗口约 15 秒，请保持决策轻量。

## 战术逻辑摘要

### 生产优先级（Core 空闲时）

1. Worker 数量 < 目标 → `spawn WORKER`
2. 有可见威胁且战斗单位不足 → `spawn VANGUARD`（近防、更便宜）或 `RANGER`（远距）
3. 和平期补齐目标 Vanguard / Ranger
4. 资源不足以保留应急储备 → 跳过 spawn
5. 人口接近 20 / 已有 upkeep → 停止常规扩军（严重零防除外）

### Worker

- 有 cargo → 优先回 Core `deposit`
- 站在 `resource_cells` → `harvest`
- 否则走向最近可见资源（目标去重）
- 附近有敌人 → 撤退向 Core；低血 → 回城 `heal`

### Vanguard

- 邻格敌人 → `sweep` 该方向
- 有近威胁 → 有限距离拦截
- 否则驻守 Core 周边 `DEFENSE_RADIUS` 环

### Ranger

- 射程内（直线/斜线 1–3 格）→ `shoot` / `shoot_cell`
- 否则驻守稍外圈 `RANGER_RADIUS`

### Core

- HP / 盾过低且有资源 → `heal` / `repair_shield`（战斗后结算，可预排）
- 否则按生产优先级 `spawn`
- 默认**不** `start_move`（防守型静止）

## 默认参数（`bot/config.py`）

| 参数 | 默认 | 说明 |
|------|------|------|
| `MAX_POPULATION` | 18 | 硬人口上限 |
| `TARGET_WORKERS` | 12 | 目标工人 |
| `TARGET_VANGUARDS` | 3 | 目标先锋 |
| `TARGET_RANGERS` | 2 | 目标游侠 |
| `DEFENSE_RADIUS` | 3 | 先锋防守环 |
| `RANGER_RADIUS` | 4 | 游侠防守环 |
| `THREAT_RADIUS` | 8 | 威胁判定半径 |
| `RETREAT_RADIUS` | 4 | 工人遇敌撤退半径 |
| `RESERVE_RESOURCES` | 2 | spawn 前预留资源 |
| Core 治疗阈值 | HP≤3 / 盾≤2 | 优先占用 Core 动作 |

调参：直接改 `bot/config.py` 中 `TacticConfig` 默认值，或在代码里构造新配置传给 `decide(turn, config=...)`。

**建议：** 总编制保持 `< 20`，这样 `upkeep = 0`（官方：`tier = floor(pop/20)`）。

## 离线测试

决策与网络解耦，使用 stub Turn 即可单测：

```bash
pytest -q
```

关键入口：

```python
from bot.strategy import decide

result = decide(turn)   # 只排队动作，不 submit
print(result.summary())
```

## 官方规则速查（v0.13）

- 命令窗口 15 秒；每个 unit/core 每 tick **一个**动作，后写覆盖前写
- 结算顺序要点：自毁 → 维护费 → 移动 → 采集交付 → 战斗 → 治疗 → spawn
- Worker 成本 3 / Vanguard 10 / Ranger 12
- 维护费：`upkeep = tier*(tier+1)/2`，`tier = floor(pop/20)`
- 欠费伤害超额单位（最近 19 个保护），不伤 Core

更多：[游戏规则](https://doc.arenahero.io/) · [Python SDK](https://doc.arenahero.io/sdk/quickstart)

## 设计说明

- **I/O 分离**：`strategy.decide` 可纯函数式测试；`main` 只负责连接与 `submit`
- **类型友好**：核心数据结构用 `dataclass`；对 SDK 对象用 duck-typing，便于 stub
- **失败安全**：单 turn 异常会打日志并尝试空提交，避免卡死循环

## License

MIT
