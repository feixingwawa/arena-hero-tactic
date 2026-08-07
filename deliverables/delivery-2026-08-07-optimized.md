# Arena Hero 战术 Agent — 官方 v0.14 规则优化交付总结

**日期**：2026-08-07 | **项目**：`arena-hero-tactic/` | **仓库**：https://cnb.cool/arena-hero/arena-hero-tactic（私有）

## TL;DR
参考官方文档（doc.arenahero.io，规则 v0.14）完成「优化完善」改造：**彻底修复 Worker 横跳**（螺旋扫掠 + 软回撤替代硬边界 recall），并实现地图记忆、动态单位价格、去维护费等官方规则适配。pytest 83/83 + inline 58/58 全过，线上已重启验证——三个 Worker 分扇区扫掠、零横跳、采集循环正常。

## 官方规则 v0.14 关键发现（与旧代码差异）
| 规则 | 官方 | 旧代码 |
|---|---|---|
| 维护费 | **已移除** | upkeep_soft_cap/hard_cap + population_upkeep（过时） |
| 单位价格 | 动态 `round_half_up(base×(13/10)^k)` | 固定 worker_cost=5 等 |
| 资源刷新 | 每 4 resolved tick 按 chunk quota 回补（quota=max(2,floor(16*8/(8+ring)))） | 一次性耗尽 |
| 视野 | Worker3/Vanguard4/Ranger5/Core5 | 未利用 |
| Beacon | [0,0]，持有者采集 1 点得 2 资源 | 未利用 |
| 地图记忆 | 服务器只发当前视野，Agent 需自存 | 无 |

## 交付内容（标准 SOP：PM→架构→工程）
1. **PRD**：`deliverables/PRD-增量优化-2026-08-07.md`（数据实证：d=36/37 横跳 29 次、recall 154 次、res_vis=0 占 93%）
2. **系统设计**：`docs/system_design.md`（软回撤/螺旋扫掠/MemoryMap/unit_cost_for 架构 + T01-T05 任务分解）
3. **实现**（T01-T05）：
   - `bot/rules.py`（新）：动态单位价格（SDK 包装 + 本地回退）
   - `bot/memory.py`（新）：资源点状态机 VISIBLE→DEPLETED→REVISIT_DUE、4 tick 回访、障碍累积、cargo 掉落
   - `bot/pathing.py`：曼哈顿环螺旋（ring_points/sector_points/spiral_target）+ chunk 辅助
   - `bot/economy.py`：软回撤探索 + 记忆驱动采集回访 + 去维护费 + 动态价格
   - `bot/roles.py`：RoleAssignment + sector_id（多 Worker 分扇区）
   - `bot/strategy.py`：decide 注入 MemoryMap、Beacon 提取

## 验证结果
| 项 | 结果 |
|---|---|
| pytest | **83/83 通过**（新增 test_rules/test_memory） |
| run_inline_tests | **58/58 通过**（新增 cargo_reclaim 等） |
| d=36/37 模拟 | 12 tick 连续 LEFT，**零横跳** |
| 线上（RESTART optimized） | 三 Worker `ring=5:sec=0/1/2` 分扇区扫掠，d 36→24 收敛、stall=0；`res_vis=1`→并行 harvest→return_deposit 稳定；Beacon pos=(-82,-186) 已提取 |
| Git | commit `fa1ce27` 已推送私有仓 |

## 用户下一步
1. agent 正在后台持续运行（RESTART optimized），观察 pop 是否增长到 14+ Worker、防御单位是否 spawn
2. 可进一步调参：`bot/config.py` 的 TacticConfig（sector_count/spiral_max_ring/refresh_interval_ticks 等）
3. 待明确项：chunk_quota 的 ring 语义（Core chunk 环）与 unit_cost 计费口径（pop vs pop+1）需真机日志校验
4. ⚠️ 建议轮换 API Key（对话中出现过完整 Key）
