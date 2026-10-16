# -*- coding: utf-8 -*-
"""外文研报功能前置探针：确认 LLM 工厂可达 + 可用模型，并打时间戳。

用途：新增「外文研报」tab 前的可行性验证。只读，不改任何生产数据。
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402

cfg = DEFAULT_CONFIG.copy()
out = []
out.append("provider=%s" % cfg.get("llm_provider"))
out.append("deep=%s" % cfg.get("deep_think_llm"))
out.append("quick=%s" % cfg.get("quick_think_llm"))
out.append("DEEPSEEK_API_KEY=%s" % ("set" if os.environ.get("DEEPSEEK_API_KEY") else "MISSING"))
out.append("ARK_API_KEY=%s" % ("set" if os.environ.get("ARK_API_KEY") else "MISSING"))

for label, model in (("quick", cfg.get("quick_think_llm")), ("deep", cfg.get("deep_think_llm"))):
    t0 = time.time()
    try:
        client = create_llm_client(cfg["llm_provider"], model, no_thinking=True)
        r = client.get_llm().invoke("只回答两个字：可用")
        body = str(getattr(r, "content", r)).strip()
        out.append("[%s] OK %.1fs -> %r" % (label, time.time() - t0, body[:60]))
    except Exception as e:
        out.append("[%s] FAIL %.1fs -> %s: %s" % (label, time.time() - t0, type(e).__name__, str(e)[:300]))

Path(ROOT / "probe" / "_foreign_smoke.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
