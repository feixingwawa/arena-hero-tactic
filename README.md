# Arena Hero 战术框架 —「资源优先 + 均衡防守」

面向 [Arena Hero](https://doc.arenahero.io/zh-Hans/) 的**可长期运行** Python 战术客户端。  
基于官方 SDK [`arena-hero`](https://pypi.org/project/arena-hero/)，你掌控游戏循环；本仓库只做决策。

# **社区：[linux.do](https://linux.do)**

> 社区战术示例，非官方客户端。仓库内**不包含**任何真实 API Key。  
> 规则基准：**玩法 v0.14** · API v0.1 · **SDK ≥ 0.2.9**（0.2.8 会 ProtocolError）。

## 游戏一句话

共享永久网格世界；Agent 每 Tick 看私有视野、交一份 15 秒窗口内的计划。  
**没有官方通关**——有效目标是 Core 存活、资源正循环、编制扩张；Champion Beacon 是可选双倍采集乘数。

详细理解见：[`docs/GAME_UNDERSTANDING.md`](docs/GAME_UNDERSTANDING.md)
战术原则见：[`docs/STRATEGY.md`](docs/STRATEGY.md)

### v2 升级速览

本次 v2 围绕「**经济更快正循环 + 更早满编 20 + 探索不空转 + 工人智能分工 + 真实朝 Beacon 推进 + 本地 Dashboard 实时观测**」做了多轮升级：

| 升级项 | 要点 |
|--------|------|
| 🚀 **台阶型 12W/4V/4R 爬坡** | W 达 3/6/9/12 → 插排 V/R，节奏明确不返工 |
| 🧠 **矿点智能调度** | Worker 满载发现矿时，含障碍寻路估算对比「自采往返」vs「派最近空闲」，执行更短路径 |
| 🌀 **双中心螺旋探索** | 内环（d≤24）Core 中心螺旋 → 环爆到上限自动切相位→ **Beacon 导向外环推进**（不再小范围死循环）|
| 🧱 **障碍历史主动避障** | 同方向 ≥3 次被挡评分 -100，**避开"老堵墙"** 节省空转 tick |
| 🔎 **P1-P3 观测性** | Core 迁徙评估/守环分散/陈旧回访/火力 ledger/SDK 版本自检/经济健康 stall 诊断 |
| 👁 **官方视野已探** | `explored_cells` 按单位 FOV 写入：Core 5 / Worker 3 / Vanguard 4 / Ranger 5（曼哈顿），障碍遮挡 LOS |
| 🗺 **本地 Dashboard** | `--dashboard` 启 Flask 地图：单位/路径/障碍/已探/资源；500ms 轮询 + `Cache-Control: no-store` 实时刷新 |
| 🛤 **近 Core 优先 deposit / 贴墙绕行** | 满货近家优先上缴；主轴堵墙时 `wall_follow_step` 贴墙绕行，减少口袋振荡 |

## 战术目标

| 阶段 | 行为 |
|------|------|
| 早期 | 优先生产 Worker，VISIBLE 采集 → deposit，打通经济 |
| 中期 | 向 **12 Worker + 若干 Vanguard/Ranger** 满编（建议总 pop≤20） |
| 全程 | Core 周边防守；威胁时撤退/反击优先于扩张 |
| Beacon | **最多 1 名 dedicated** 侦察；远距放弃；Core 不宜追信标 |
| 生存 | Core/单位低血有条件 heal、修盾；v0.14 **无维护费** |
| 监控指标 | 用户目标：Core 库存 **resources ≥ 100** |

## 项目结构

```
arena-hero-tactic/
  README.md
  deploy.bat / deploy.sh # 一键部署入口（Windows 双击 / Unix）
  docs/
    GAME_UNDERSTANDING.md   # 游戏怎么运行 / 目标 / Agent 职责
    STRATEGY.md             # 战术原则与改造路线
    system_design*.md       # 架构与探索设计
  bot/
    main.py              # 入口：Key → 连接 → turns 循环（可选 --dashboard）
    config.py            # TacticConfig
    strategy.py          # decide(turn)
    economy.py           # 采集/交付/生产/螺旋探索
    combat.py            # 威胁、防守圈、sweep/shoot
    pathing.py           # 防抖步进、螺旋、beacon 目标、贴墙绕行
    memory.py            # 资源/障碍/chunk/官方 FOV 已探
    roles.py             # 角色分配
    rules.py             # 动态单位价 / 容量 / chunk 配额
    dashboard.py         # 可选本地观测：快照环缓冲 + Flask API + SSE 日志
    dashboard_static/    # Dashboard 前端（地图 / 趋势 / 日志）
  scripts/
    deploy.py            # 一键：venv / 依赖 / .env / 启 Dashboard / health
    restart_agent.py     # 环境已就绪时仅后台重启 agent
  tests/
  deliverables/
```

## 环境要求

- Python **3.11+**
- `arena-hero>=0.2.9,<0.3`、`python-dotenv`（可选）、`pytest`（测试）
- **可选 Dashboard**：`flask>=3.0`（`requirements.txt` 中默认注释；仅 `--dashboard` 时需要）
- **SDK 版本自检（v2 P3-1）**：启动时 `main.run_loop` 会强制校验 arena-hero 版本 ≥ 0.2.9 且 < 0.3；不满足直接 `SystemExit(1)`，避免 `ProtocolError` 到线上才报错

## 一键部署（推荐）— Windows / macOS / Linux 通用

**统一入口 `install.py`**（纯 Python）：提示输入 API Key 后，自动装依赖、写 `.env`、启动 Agent + **对公网开放**的 Dashboard。

```bash
# 已克隆仓库（三系统通用）
cd arena-hero-tactic
python install.py                 # Windows 也可用: py install.py
# 或双击 / 脚本包装
#   Windows:  install.bat
#   Unix:     bash install.sh
```

远程一行拉起（下载源码到当前目录 `arena-hero-tactic/`）：

```bash
# Linux / macOS
python3 <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py)

# Windows PowerShell
# irm https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py -OutFile install.py
# py install.py
```

非交互 / CI：

```bash
python install.py --api-key '你的_API_KEY'
ARENA_HERO_API_KEY='你的_API_KEY' python install.py
python install.py --api-key '你的_API_KEY' --no-start
```

`install.py` 会完成：检查 Python ≥ 3.11 → 拉取或复用源码 → **交互输入 API Key** → 创建 `.venv` → pip 安装 → 写 `.env` → 杀旧进程 → 后台启动  
`python -m bot.main -v --dashboard --dashboard-host 0.0.0.0 --dashboard-port 8765` → 探测 `GET /health`。

仓库内仅部署（不拉源码，同样默认公网 Dashboard）：

```bash
# Windows（也可资源管理器双击 deploy.bat / install.bat）
install.bat
deploy.bat --api-key 你的_API_KEY
deploy.bat --no-start          # 只装环境不启动
deploy.bat --skip-pip          # 已装好依赖时跳过 pip
deploy.bat --no-kill           # 不结束已在跑的 agent
deploy.bat --port 8765

# Linux / macOS
chmod +x deploy.sh install.sh install.py
./install.sh
./deploy.sh --api-key 你的_API_KEY
./deploy.sh --host 0.0.0.0 --port 8765

# 等价直接调用
python scripts/deploy.py
python scripts/deploy.py --foreground   # 前台跑，Ctrl+C 停
python scripts/deploy.py --quiet        # 启动不加 -v
python scripts/deploy.py --host 127.0.0.1   # 仅本机访问
```

成功后：

- 本机：**http://127.0.0.1:8765/**
- 公网：**http://\<服务器公网IP\>:8765/**（需放行防火墙/安全组 **TCP 8765**）
- PID：`logs/agent.pid`；日志：`logs/agent.log`

**不要**把 `.env` 或真实 Key 提交到 Git；`--api-key` 只写入本地 `.env` 且不会在终端打印明文。  
更多说明见 [`ONE_CLICK_DEPLOY.md`](ONE_CLICK_DEPLOY.md)。

仅重启（环境已就绪）：`python scripts/restart_agent.py`。

## 手动安装

```bash
cd arena-hero-tactic
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
# 若启用 Dashboard：
# pip install "flask>=3.0"
```

## 配置 API Key

```bash
copy .env.example .env          # Windows
# cp .env.example .env          # Unix
```

编辑 `.env`：

```
ARENA_HERO_API_KEY=你的_API_KEY
```

**不要**把 `.env` 或真实 Key 提交到 Git。

## 启动

```bash
python -m bot.main
python -m bot.main -v --log-file logs/agent.log
python -m bot.main --max-turns 50

# Dashboard 默认 0.0.0.0:8765（对公网开放）
python -m bot.main --dashboard
python -m bot.main --dashboard --dashboard-host 0.0.0.0 --dashboard-port 8765
# 仅本机：
python -m bot.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8765
```

每个 Tick 收到 `state` 后尽快 `decide(turn)` 并 `turn.submit()`。命令窗口约 15 秒，决策须轻量。

### Dashboard（可选观测，零污染主循环）

- **默认关闭**：不加 `--dashboard` 时不导入 Flask、不启 HTTP 线程，决策路径与线上一致。
- **公网开放**：加 `--dashboard` 后默认监听 **`0.0.0.0:8765`**，可用公网 IP 访问；仅本机请加 `--dashboard-host 127.0.0.1`。请自行在防火墙/云安全组放行 **TCP 8765**。
- **启后能力**：
  - 地图：Core / Worker / Vanguard / Ranger / 敌人 / 资源 / 障碍 / **官方 FOV 已探格子** / 路径 dry-run 路点
  - 顶栏：tick、资源、编制、live/paused/stale 模式、距上次成功拉取的年龄
  - 历史趋势：`/api/state/history` 返回 `{ok, frames, count}`；前端 `normalizeHistory` 解包
  - 日志：SSE `/api/logs/stream` + 轮询兜底
- **实时性**：前端约 **500ms** 轮询 `latest`+`history`，请求带 `cache: no-store` 与 bust query；后端对页面与状态 API 写 `Cache-Control: no-store`。
- **健康检查**：`GET /health` → `{ok, ...}`；静态资源在 `/static/assets/`。

## 战术逻辑摘要

### 生产优先级（Core 空闲且非危急治疗）

1. Worker < 目标 → `spawn WORKER`（动态价格）
2. 可见威胁且战斗单位不足 → `VANGUARD` / `RANGER`
3. 和平期补齐目标战斗单位
4. 资源不足应急储备 → 跳过 spawn
5. 人口触及 `max_population` → 停扩

### Worker

- 有 cargo → 优先回 Core `deposit`（**近 Core 时进一步提高 deposit 优先级**，减少「门口徘徊」）
- 站在可见 `resource_cells` → `harvest`
- **矿点智能调度（v2）**：
  - 空载发现矿 → 直接采集（与 v1 相同）
  - **满载发现矿** → 用 `estimate_path_steps`（**含障碍寻路估算**）精确对比：
    - 选项 A「自采往返」= 送回 Core → 再回矿 的总步数
    - 选项 B「派最近空闲 Worker」= 其他 idle Worker 到矿 → 回 Core 的最短步数
    - 选更短的：A 胜 → 写入预约（送完 cargo 下一 tick 优先返程采）；B 胜 → 立刻指派最优 idle Worker
    - 预约 TTL 16 tick；RETREAT/HEAL 角色立刻释放
- **双中心螺旋探索（v2，修复不再小范围循环）**：
  - 内环：距 Core ≤ 24 格 → Core 中心螺旋，扇区分散 + 跳过已探 chunk
  - 环推进到上限后 **自动切 Beacon 相位** → 外环以 Beacon 为中心螺旋，不再回退到内环（v2 关键 bugfix）
  - 距离 Beacon 太远（>64）或工人不足（<3）→ 守门不追，防止饿死经济
- **历史障碍主动避障（v2）**：寻路时若某邻格历史被挡 ≥3 次 → 评分 -100 主动绕开；1-2 次 -30 降权
- **贴墙绕行**：主轴被硬墙挡住时 `wall_follow_step` 选侧向贴墙，减轻口袋来回抖
- 附近敌人 → 向 Core 撤退；低血 → 回城 heal

### Vanguard / Ranger

- 邻格 / 射程内攻击；否则驻守 Core 防守环
- **守环位分散（v2 P1-2）**：多单位同相位 slot 冲突时，+1 偏移避让，不再堆同格堵 Core 入口
- Ranger 射击：**火力 ledger 轻量（v2 P2-2）**，预计伤害已满 HP 的目标跳过，避免 overkill（日志 `shoot_avoid_overkill`）

### Core

- HP/盾过低 → `heal` / `repair_shield`（战斗后结算，可预排）
- 否则按**台阶型生产节奏（v2）** `spawn`：W=3→V；W=6→V+R；W=9→V+R；W=12→V+R+R；达到 12/4/4 停扩
- **Core 迁徙评估（v2 P1-1 日志-only）**：邻格敌且 Core HP≤3 / Beacon 距 Core≤4 时，写入评估日志但**不真正 start_move**（避免移动 bug）
- 默认不主动追 Beacon；后续可实现「Core 远离信标」迁徙

### 地图记忆与官方视野

- 进程内 `MemoryMap`：资源状态机、永久障碍、掉落 cargo、16×16 chunk 已探与陈旧回访
- **已探格子 = 官方 FOV 历史**（非「走过的足迹」）：
  - 半径（曼哈顿）：**Core 5 / Worker 3 / Vanguard 4 / Ranger 5**
  - 障碍遮挡视线（`has_line_of_sight`）后写入 `explored_cells`
  - Dashboard 导出格子级已探，便于对照真实可见范围

## 默认参数（`bot/config.py`，以代码为准）

| 参数 | 典型默认 | 说明 |
|------|----------|------|
| `max_population` | **20** | 基础价满编硬顶 |
| `target_workers` | **12** | 目标工人 |
| `target_vanguards` | **4** | 目标先锋 |
| `target_rangers` | **4** | 目标游侠 |
| `spiral_base_ring` | 3 | 本地螺旋起始环 |
| `spiral_max_ring` | **24** | 本地螺旋上限（收紧空转） |
| `sector_count` | **4** | Worker 扇区分散 |
| `beacon_max_chase` | **64** | 超距不追 Beacon |
| `beacon_min_workers` | **3** | 早期全员采，够人再 1 人侦察 |
| `beacon_push_population` | **10** | 总人口 ≥ 此值 → 向信标推进 |
| `beacon_push_explore_ratio` | **0.8** | 本地（spiral_max_ring）探索度 ≥ 此比例 → 向信标推进 |
| `recall_stall_ticks` | 6 | 无进展软回撤 |
| `retreat_adjacent` | 1 | 空货贴身才撤 |
| `retreat_radius` | 3 | 满货保护半径 |
| `beacon_step_radius` | 8 | Beacon 阶段步距 |
| `CHUNK_SIZE` | **16** | 地图块尺寸（MemoryMap explored 标记粒度；v2 从 32 → 16，切 chunk 更频密）|
| `refresh_interval_ticks` | 4 | 资源回补节拍 / 陈旧 chunk 回访基准（陈旧阈值 = refresh_interval * 50 ≈ 200 tick）|

调参：改 `TacticConfig` 或 `decide(turn, config=...)`。

**建议：** 总编制保持 **≤20**，用满 v0.14 基础价格区间；第 21 个单位开始动态涨价。

> **生产节奏（v2）**：Worker 达 3/6/9/12 台阶时按序插排 VANGUARD 与 RANGER，最终目标 12W / 4V / 4R（基础价满编 20）。

## 官方规则速查（v0.14）

- 命令窗口 **15s**；每对象每 tick **一个**动作
- **无**每 tick 维护费；动态价：`k=max(0,floor((pop-20)/5)+1)`，`price≈base×1.3^k`
- 基础价（pop 0–19）：Worker **5** / Vanguard **10** / Ranger **12**
- 资源：每 4 resolved tick 按 chunk 配额补点；持 Beacon 时 harvest ×2
- 结算顺序要点：移动 → harvest/deposit → 战斗 → heal → spawn
- Beacon：坐标公开；SDK `status=None` 视为地面可追踪
- **视野（曼哈顿）**：Core **5** / Worker **3** / Vanguard **4** / Ranger **5**；障碍可挡视线

更多：[游戏规则](https://doc.arenahero.io/zh-Hans/rules/world-and-ticks) · [规则速查](https://doc.arenahero.io/zh-Hans/reference/numbers) · [Python SDK](https://doc.arenahero.io/zh-Hans/sdk/quickstart)

## 离线测试

```bash
# 建议在 venv 中
pytest -q
pytest tests/test_pathing.py tests/test_economy.py tests/test_memory.py -v
pytest tests/test_dashboard.py -q   # 需 flask；API 包装/环缓冲/零污染 等机制测
```

```python
from bot.strategy import decide
result = decide(turn)   # 只排队，不 submit
print(result.summary())
```

**观测性验证（v2 专用）**：用仓库内 stub 仿真或线上 `--dashboard` 对照：

- `core:spawn:WORKER / VANGUARD / RANGER` → 台阶节奏正确
- `dispatch:option=self / option=other` → 矿点调度生效
- `:ring=`（内环）与 `phase=beacon`（外环）同时出现 → 双中心切换正常
- `new_chunk=` → 探索在推进；`pickup_beacon=` → 已实际到 Beacon
- Dashboard 地图 tick 随时间递增、已探格随单位移动扩张
- `ERROR` / `ProtocolError` = 0

## 设计说明

- **I/O 分离**：`strategy.decide` 可纯测；`main` 只连接与 submit
- **地图记忆**：进程内 `MemoryMap`，服务端不回放历史；含资源/障碍/chunk 三维 + chunk_last_seen 陈旧判定（200 tick）+ **官方 FOV 已探**
- **防抖寻路**：`clamp_step_toward_memo` 避免障碍对抖；v2 **叠加历史障碍降权**（≥3 次被挡 -100）；硬墙主轴堵时贴墙绕行
- **路径估算（v2）**：`estimate_path_steps` / `reconstruct_path` dry-run，只用于矿点调度与 Dashboard 可视化，不改变真实状态
- **台阶生产（v2）**：`choose_spawn` 按 3/6/9/12 四档台阶插排 V/R，终态 12/4/4 基础价满编 20
- **双中心螺旋（v2）**：`dual_spiral_target` 内环 Core + 外环 Beacon，环爆后自动切相位，**不再回退到内环小范围死循环**
- **经济健康（v2 P3-2）**：连续 50 tick 无 deposit → 打结构化预警日志，回 40 防刷屏；256 tick GC 死亡 Worker 相关模块字典 4 个
- **Dashboard 零污染**：仅 flag 开启时后台线程 + 环形缓冲；`safe_push_snapshot` 异常吞掉，不阻断 `submit`
- **失败安全**：单 turn 异常记日志并尽量不卡死循环

## License

MIT
