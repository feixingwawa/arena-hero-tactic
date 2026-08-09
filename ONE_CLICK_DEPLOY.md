# Arena Hero Tactic — 一行命令全自动部署

## 最快：本机已有仓库

```bash
cd arena-hero-tactic && bash install.sh
```

会提示输入 API Key，然后自动装依赖、写 `.env`、启动 Agent + **公网 Dashboard**（`0.0.0.0:8765`）。

- 本机：http://127.0.0.1:8765/
- 公网：http://<服务器公网IP>:8765/（需放行 TCP 8765）

## 远程一行（curl）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.sh)
```

## 非交互

```bash
bash install.sh --api-key '你的真实KEY'
# 或
ARENA_HERO_API_KEY='你的真实KEY' bash install.sh
```

API Key：https://doc.arenahero.io/

---

## 等价写法

```bash
# 仓库内脚本
bash install.sh

# 非交互（管道 / CI / 无 TTY）
bash install.sh --api-key '你的真实KEY'

# 环境变量
ARENA_HERO_API_KEY='你的真实KEY' bash install.sh

# 只装环境不启动
bash install.sh --api-key '你的真实KEY' --no-start

# 跳过 pip（依赖已装好时）
bash install.sh --skip-pip
```

## 运维

```bash
# 日志
tail -f ./logs/agent.log

# 停止：再跑一次一键脚本会先杀旧进程，或：
pkill -f 'python.*-m bot.main'

# 健康检查
curl -s http://127.0.0.1:8765/health
```

## 说明

- **假 Key / 错误 Key** 会启动后因 WebSocket **HTTP 401** 退出，属正常；换成真实 Key 即可长期运行
- 默认 pip 源：清华 `https://pypi.tuna.tsinghua.edu.cn/simple`
- 项目目录：`.`
- 交互输入：优先读当前终端 stdin；stdin 被管道占用时再试 `/dev/tty`；最多 5 次重试，避免死循环
- 无 TTY 时必须用 `--api-key` 或环境变量

## 实现文件

| 文件 | 作用 |
|------|------|
| [`install.sh`](install.sh) | 本机一行入口（包装） |
| [`arena-hero-tactic/install.sh`](arena-hero-tactic/install.sh) | 拉源码 + 交互要 Key + 调 deploy |
| [`arena-hero-tactic/scripts/deploy.py`](arena-hero-tactic/scripts/deploy.py) | venv / pip / `.env` / 启停 agent |
| [`arena-hero-tactic/deploy.sh`](arena-hero-tactic/deploy.sh) | 选 Python ≥3.11 后跑 deploy.py |
