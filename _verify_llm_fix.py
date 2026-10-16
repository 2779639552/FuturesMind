"""验证 .env 修复是否真的生效 —— 完全复刻 web_app.py 的 LLM 调用链路。

链路: load_dotenv -> DEFAULT_CONFIG(内建 env 覆盖) -> set_config -> create_llm_client -> get_llm().invoke
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"), override=True)

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.config import set_config
from tradingagents.llm_clients import create_llm_client

set_config(DEFAULT_CONFIG)

cfg = DEFAULT_CONFIG
print("=== 生效中的配置 ===")
print("llm_provider  :", cfg.get("llm_provider"))
print("backend_url   :", cfg.get("backend_url"))
print("deep_think_llm:", cfg.get("deep_think_llm"))
print("quick_think_llm:", cfg.get("quick_think_llm"))
print("api_key 来源  : OPENAI_COMPATIBLE_API_KEY =", (os.environ.get("OPENAI_COMPATIBLE_API_KEY") or "")[:10] + "...")
print()

# 复刻 web_app.py:7324 的写法: provider + quick_think_llm, 不传 base_url(靠工厂兜底)
t0 = time.time()
try:
    client = create_llm_client(
        cfg["llm_provider"],
        cfg.get("quick_think_llm", cfg["deep_think_llm"]),
        no_thinking=True,
    )
    llm = client.get_llm()
    print("[1/2] 客户端构造成功 ->", type(llm).__name__)
    print("      model =", getattr(llm, "model_name", getattr(llm, "model", "?")))
    print("      base_url =", getattr(llm, "openai_api_base", getattr(llm, "base_url", "?")))
except Exception as exc:
    print("[1/2] 客户端构造失败:", type(exc).__name__, exc)
    raise SystemExit(1)

try:
    result = llm.invoke("只回复两个字:成功")
    body = str(result.content if hasattr(result, "content") else result).strip()
    print(f"[2/2] 真实调用成功 ({time.time() - t0:.1f}s) -> {body!r}")
    print()
    print(">>> 结论: LLM 链路已恢复正常。")
except Exception as exc:
    print(f"[2/2] 真实调用失败 ({time.time() - t0:.1f}s):", type(exc).__name__)
    print("   ", str(exc)[:400])
    raise SystemExit(2)
