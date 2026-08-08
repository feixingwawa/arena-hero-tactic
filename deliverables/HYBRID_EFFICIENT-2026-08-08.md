# 混合高效战术落地（参考 ≠ 照搬）

- 日期：2026-08-08
- 原则：保留本仓库螺旋 / MemoryMap / 防抖 / VISIBLE 采集；吸收 Drew-Z 资源优先与远距不追 Beacon

## 代码变更

| 文件 | 变更 |
|------|------|
| `bot/config.py` | 默认 **12/4/4 max20**；`beacon_max_chase=64`；`beacon_min_workers=3`；`spiral_max_ring=24`；`sector_count=4`；`early_game_pop=6` |
| `bot/economy.py` | `_beacon_chase_allowed` / `_drop_to_local`；远距与人少禁止 dedicated；spawn 先 bootstrap 6 Worker |
| `tests/*` | 新用例 far abort / min_workers / drop dedicated；默认断言对齐 |
| `README.md` / `docs/STRATEGY.md` | 参数与 P0 勾选更新 |

## 取长补短对照

| 保留（本仓库） | 吸收（Drew-Z 思路） | 未照搬 |
|----------------|---------------------|--------|
| 螺旋扫掠 + 软回撤 ring+1 | 编制 12/4/4=20 基础价满编 | 完整威胁状态机 / Core 迁徙 |
| MemoryMap VISIBLE harvest | 远距 Beacon 放弃 | Docker/systemd 部署 |
| clamp_step_toward_memo 防抖 | 早期资源爬坡优先于战斗 | 模型监督 |
| dedicated 单人通道 | min_workers 后才侦察 | 最小费用全局匹配（P2） |

## 验证

- `pytest`：**111 passed**
- `run_inline_tests.py`：**70 passed**
- 线上重启后：无 `phase=beacon` 空跑；出现 `to_resource` / `return_deposit`；`res_vis≥1`

## 启动

```bash
python -B -m bot.main -v --log-file logs/agent.log
# 当前默认：max_pop=20 workers=12 vanguards=4 rangers=4
```
