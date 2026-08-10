# Arena Hero Tactic — 跨平台一键部署（Win / macOS / Linux）

统一入口：**`install.py`**（纯 Python，三系统通用）。  
包装脚本：`install.bat`（Windows）、`install.sh`（Unix，内部转调 `install.py`）。

---

## 最快：本机已有仓库

```bash
# 任意系统（推荐）
cd arena-hero-tactic
python install.py          # 或 python3 install.py
```

```bat
REM Windows 也可双击
install.bat
```

```bash
# Linux / macOS 也可用
bash install.sh
```

会提示输入 API Key，然后自动装依赖、写 `.env`、启动 Agent + **公网 Dashboard**（`0.0.0.0:8765`）。

- 本机：http://127.0.0.1:8765/
- 公网：http://\<服务器公网IP\>:8765/（需放行 TCP 8765）

---

## 远程一行

### Linux / macOS

```bash
python3 <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py)
```

或仍用 bash 包装（会下载并执行同一套 `install.py`）：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.sh)
```

### Windows（PowerShell）

```powershell
irm https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py -OutFile install.py
py install.py
```

### Windows（CMD）

```bat
curl -fsSL -o install.py https://raw.githubusercontent.com/feixingwawa/arena-hero-tactic/main/install.py
py install.py
```

---

## 非交互 / CI

```bash
python install.py --api-key '你的真实KEY'
# 或
ARENA_HERO_API_KEY='你的真实KEY' python install.py

# 只装环境不启动
python install.py --api-key '你的真实KEY' --no-start

# 跳过 pip（依赖已装好时）
python install.py --skip-pip
```

Windows：

```bat
install.bat --api-key 你的真实KEY
install.bat --no-start
set ARENA_HERO_API_KEY=你的真实KEY && install.bat
```

API Key：https://doc.arenahero.io/

---

## 等价入口（仓库已就绪时）

```bash
# 直接调部署核心（不拉源码）
python scripts/deploy.py
python scripts/deploy.py --api-key KEY --no-start

# Unix
./deploy.sh

# Windows
deploy.bat
```

---

## 运维

```bash
# 日志
# Windows: type logs\agent.log
tail -f ./logs/agent.log

# 停止：再跑一次一键脚本会先杀旧进程
# 或手动结束 bot.main

# 健康检查
curl -s http://127.0.0.1:8765/health
```

---

## 说明

- **假 Key / 错误 Key** 会启动后因 WebSocket **HTTP 401** 退出，属正常；换成真实 Key 即可长期运行
- 默认 pip 源：清华 `https://pypi.tuna.tsinghua.edu.cn/simple`（可用 `PIP_INDEX_URL` 覆盖）
- 需要 **Python ≥ 3.11**
- 无 TTY 时必须用 `--api-key` 或环境变量 `ARENA_HERO_API_KEY`
- 远程安装默认目录：当前工作目录下的 `arena-hero-tactic/`（可用 `--install-dir` / `INSTALL_DIR` 覆盖）

---

## 实现文件

| 文件 | 作用 |
|------|------|
| [`install.py`](install.py) | **跨平台主入口**：拉/复用源码 + API Key + 调 deploy |
| [`install.bat`](install.bat) | Windows 双击/CMD 包装 |
| [`install.sh`](install.sh) | Unix 包装（转调 install.py） |
| [`scripts/deploy.py`](scripts/deploy.py) | venv / pip / `.env` / 启停 agent（已支持 Win/Unix） |
| [`deploy.sh`](deploy.sh) / [`deploy.bat`](deploy.bat) | 仓库内仅部署（不拉源码） |
