# 混合高效战术落地

- 日期：2026-08-08
- 原则：螺旋 / MemoryMap / 防抖 / VISIBLE 采集；资源优先；远距不追 Beacon

## 代码变更

| 文件 | 变更 |
|------|------|
| `bot/config.py` | 默认 **12/4/4 max20**；`beacon_max_chase=64`；`beacon_min_workers=3`；`spiral_max_ring=24`；`sector_count=4`；`early_game_pop=6` |
| `bot/economy.py` | `_beacon_chase_allowed` / `_drop_to_local`；远距与人少禁止 dedicated；spawn 先 bootstrap 6 Worker |
| `tests/*` | 新用例 far abort / min_workers / drop dedicated；默认断言对齐 |
| `README.md` / `docs/STRATEGY.md` | 参数与 P0 勾选更新 |

## 能力对照

| 已落地 | 说明 | 未做 / 可选 |
|--------|------|-------------|
| 螺旋扫掠 + 软回撤 ring+1 | 本地探索不贴 Core 空转 | 完整威胁状态机 / Core 真迁徙 |
| MemoryMap VISIBLE harvest | 只采可见资源 | Docker/systemd 部署 |
| clamp_step_toward_memo 防抖 | 路径对抖抑制 | 模型监督 |
| 编制 12/4/4=20 | 基础价满编 | 最小费用全局匹配（P2） |
| 远距 Beacon 放弃 | `beacon_max_chase` | — |
| 早期全员采 | `beacon_min_workers` 后才 dedicated | — |

## 验证

- `pytest`：**111 passed**
- `run_inline_tests.py`：**70 passed**
- 线上重启后：无 `phase=beacon` 空跑；出现 `to_resource` / `return_deposit`；`res_vis≥1`

## 启动

```bash
python -B -m bot.main -v --log-file logs/agent.log
# 当前默认：max_pop=20 workers=12 vanguards=4 rangers=4
```
