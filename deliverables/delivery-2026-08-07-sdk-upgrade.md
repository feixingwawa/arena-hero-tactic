# Arena Hero Agent — SDK 升级与规则适配交付总结

**日期**：2026-08-07 | **项目**：`arena-hero-tactic/` | **仓库**：https://cnb.cool/arena-hero/arena-hero-tactic（私有）

## TL;DR
解决了「8+ 小时 ProtocolError 连不上服务器」的根本原因（服务端协议升级 + SDK 过旧），升级 SDK 0.2.9 后 agent 恢复收 turn；同时适配了新规则成本（WORKER=5），45/45 测试通过并已推送私有仓。当前世界因旧 Worker 全灭处于 pop=0 死锁，需**重开比赛**才能验证经济正循环。

## 关键发现与修复

### 1. ProtocolError 根因（非服务器故障，是 SDK 过旧）
- 服务端协议升级后，state 消息**不再包含 `population_tier` / `upkeep_next_tick`** 字段
- SDK 0.2.8 的 Pydantic 模型 `required + extra=forbid` 解析失败 → 每帧报 `ProtocolError: invalid Arena Hero WebSocket message` → 无限重连
- **修复**：升级 `arena-hero==0.2.9`（已移除这两个字段），ProtocolError 消除，agent 恢复正常收 turn（tick 63576+ 实测）

### 2. 规则成本适配（隐藏 bug）
- SDK 权威值 `UNIT_BASE_COSTS = {WORKER: 5, VANGUARD: 10, RANGER: 12}`，而 config 写 `worker_cost=3`
- **影响**：res=3~4 时我们以为能 spawn Worker，服务器实际要 5 → spawn 判断错误
- **修复**：config 三成本同步为 SDK 权威值；早期 reserve 逻辑与相关测试同步（resources>=5 出 WORKER）

### 3. 重生状态感知
- SDK 有 `PlayerStatus.RESPAWNING` + `respawn_at_tick`
- strategy.py 新增：RESPAWNING 时输出 `strategy:respawn_at=<tick>` 并跳过行动，不再死锁空转

## 验证结果
| 项 | 结果 |
|---|---|
| SDK | 0.2.8 → **0.2.9**（requirements/pyproject 已锁 >=0.2.9,<0.3） |
| 离线测试 | **45/45 通过**（新增 respawn_skip；成本用例改为 5） |
| 线上连接 | ProtocolError 消除，恢复收 turn |
| Git | commit `54e0a50` 已推送私有仓（含 smoke_test_029.py 协议冒烟测试） |
| 遗留 | 当前世界 pop=0 res=0 死锁，需重开比赛 |

## 修改文件
`bot/config.py`（成本 5/10/12）、`bot/strategy.py`（RESPAWNING 感知）、`requirements.txt` + `pyproject.toml`（SDK>=0.2.9）、`run_inline_tests.py`、`tests/test_economy.py`、`tests/test_strategy.py`、`tests/stubs.py`、`deliverables/smoke_test_029.py`（新增）

## 用户下一步
1. **在 Arena Hero 平台重开一场新比赛**（当前世界 Worker 全灭、res=0，无自救手段）
2. 重启 agent：`cd arena-hero-tactic && .venv/Scripts/python.exe -m bot.main -v --log-file logs/agent.log`
3. 关注日志应出现正循环：`to_resource → harvest → deposit → core:spawn:WORKER`
4. 若新比赛仍无初始资源，需确认游戏规则（开局是否赠送资源/单位）
5. ⚠️ 建议轮换 API Key（对话中出现过完整 Key）
