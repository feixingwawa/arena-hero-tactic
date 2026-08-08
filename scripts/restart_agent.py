"""Restart bot.main in background; print status and log tail."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ["PYTHONPATH"] = str(ROOT)

# Prefer project venv (has arena-hero SDK); fall back to current interpreter.
_venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
if not _venv_py.exists():
    _venv_py = ROOT / ".venv" / "bin" / "python"
PY = str(_venv_py) if _venv_py.exists() else sys.executable
print("python", PY)

try:
    import psutil
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil", "-q"])
    import psutil

for p in psutil.process_iter(["pid", "cmdline"]):
    try:
        cl = p.info.get("cmdline") or []
        if any("bot.main" in str(x) for x in cl):
            p.kill()
            print("killed", p.pid)
    except Exception:
        pass

logs = ROOT / "logs"
logs.mkdir(exist_ok=True)
out = open(logs / "agent.stdout", "w", encoding="utf-8")
err = open(logs / "agent.stderr", "w", encoding="utf-8")
proc = subprocess.Popen(
    [PY, "-m", "bot.main", "-v", "--dashboard", "--log-file", "logs/agent.log"],
    stdout=out,
    stderr=err,
    cwd=str(ROOT),
    env=os.environ.copy(),
)
(logs / "agent.pid").write_text(str(proc.pid), encoding="ascii")
print("started", proc.pid)
time.sleep(12)
print("poll", proc.poll())
print("---stderr---")
print((logs / "agent.stderr").read_text(encoding="utf-8", errors="replace")[-2500:])
print("---stdout---")
print((logs / "agent.stdout").read_text(encoding="utf-8", errors="replace")[-2000:])
print("---log tail---")
log_path = logs / "agent.log"
if log_path.exists():
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-30:]))
else:
    print("(no agent.log)")
