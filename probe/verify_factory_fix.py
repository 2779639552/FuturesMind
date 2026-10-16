# -*- coding: utf-8 -*-
"""验证: 修好工厂后, **不显式传 base_url** 的生产调用方式能否拿到天翼云地址。

关键: 上一次的 verify_ctaigw_switch.py 是【假阳性】—— 它手动传了 base_url,
      而生产代码(commodity_demo.py / web_app.py 的十几处调用点)都不传。
      本脚本严格复刻生产调用签名: create_llm_client(provider, model)。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients.factory import create_llm_client  # noqa: E402

out = []
checks = []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    out.append(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


# 复刻 web_app.py 的启动动作: config = DEFAULT_CONFIG.copy(); set_config(config)
cfg = DEFAULT_CONFIG.copy()
set_config(cfg)

out.append("=" * 74)
out.append("A. 配置快照")
out.append("=" * 74)
for k in ("llm_provider", "deep_think_llm", "quick_think_llm", "backend_url"):
    out.append(f"  {k:<18} = {cfg.get(k)!r}")

out.append("")
out.append("=" * 74)
out.append("B. 关键: 按【生产调用签名】创建客户端(不传 base_url)")
out.append("=" * 74)

provider = cfg["llm_provider"]
model = cfg.get("quick_think_llm", cfg["deep_think_llm"])

# 完全复刻 commodity_demo.py:617 的写法 —— 只有两个位置参数
try:
    client = create_llm_client(provider, model)
    llm = client.get_llm()
    actual = getattr(llm, "openai_api_base", None)
    out.append(f"  客户端类 = {type(llm).__name__}")
    out.append(f"  解析到的 base_url = {actual}")
    chk(actual == "https://ai.ctaigw.cn/v1", "不传 base_url 也能落到天翼云地址", str(actual))

    r = llm.invoke("只回复两个字：收到")
    content = getattr(r, "content", r)
    out.append(f"  真实调用回复 = {content!r}")
    chk(bool(str(content).strip()), "经生产签名调用成功且有内容", repr(content)[:60])
except Exception as e:
    import traceback

    out.append(f"  [FAIL] {type(e).__name__}: {e}")
    out.append(traceback.format_exc())
    checks.append(False)

out.append("")
out.append("=" * 74)
out.append("C. 回归: 显式传 base_url 时必须优先于配置(不被兜底覆盖)")
out.append("=" * 74)
try:
    c2 = create_llm_client(provider, model, base_url="https://example.invalid/v1")
    got = getattr(c2.get_llm(), "openai_api_base", None)
    chk(got == "https://example.invalid/v1", "显式 base_url 优先级更高", str(got))
except Exception as e:
    out.append(f"  (行为说明) 显式地址不可达属预期, 只校验取值: {type(e).__name__}")
    chk(False, "显式 base_url 优先级更高", "构造即抛错, 需人工确认")

out.append("")
out.append("=" * 74)
out.append(f"结果: {sum(checks)} 通过 / {len(checks) - sum(checks)} 失败")
out.append("=" * 74)

text = "\n".join(out)
Path(ROOT / "probe" / "_factory_fix_result.txt").write_text(text, encoding="utf-8")
print(text)
