"""临时启动器:以完全分离方式启动 web_app.py,stdin 置空避免 input() 阻塞。"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out = ROOT / f"server_run_{stamp}.log"
err = ROOT / f"server_run_{stamp}.err.log"

flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
p = subprocess.Popen(
    [PY, "web_app.py"],
    cwd=str(ROOT),
    stdin=subprocess.DEVNULL,
    stdout=open(out, "w", encoding="utf-8"),
    stderr=open(err, "w", encoding="utf-8"),
    creationflags=flags,
    close_fds=True,
)
print(f"started web pid={p.pid} out={out.name} err={err.name}", flush=True)
