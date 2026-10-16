# -*- coding: utf-8 -*-
"""验证: 天翼云中转经「项目真实客户端链路」是否可用

关键点: 不是直接 requests 打端点, 而是走 create_llm_client() 工厂
—— 这样能验证 .env 配置是否被正确读取、base_url 是否正确拼接、
   以及 LangChain 封装层(消息格式/参数)是否兼容。
只读验证, 不发真实业务请求。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 载入 .env (与 web_app.py 同款方式)
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    print("[.env] 已载入")
except Exception as e:
    print(f"[.env] 载入失败(继续用现有环境变量): {e}")

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients.factory import create_llm_client  # noqa: E402

print()
print("=" * 74)
print("A. DEFAULT_CONFIG 是否已吃到 .env 覆盖")
print("=" * 74)
for k in ("llm_provider", "deep_think_llm", "quick_think_llm", "backend_url"):
    print(f"  {k:<18} = {DEFAULT_CONFIG.get(k)!r}")

provider = DEFAULT_CONFIG["llm_provider"]
model = DEFAULT_CONFIG["quick_think_llm"]
base_url = DEFAULT_CONFIG.get("backend_url")
print()
print("=" * 74)
print("B. 建立客户端(走真实工厂链路)")
print("=" * 74)
print(f"  provider = {provider}")
print(f"  model    = {model}")
print(f"  base_url = {base_url}")

checks = []


def chk(cond, label, detail=""):
    checks.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


chk(provider == "openai_compatible", "provider 已是 openai_compatible", provider)
chk(model == "deepseek-v4.1-flash", "模型名为 deepseek-v4.1-flash", model)
chk(base_url == "https://ai.ctaigw.cn/v1", "backend_url 已设为 /v1(不带 chat/completions)", str(base_url))
chk(os.environ.get("OPENAI_COMPATIBLE_API_KEY"), "密钥环境变量存在")

print()
print("=" * 74)
print("C. 真实调用一次(经 LangChain 封装)")
print("=" * 74)
try:
    # 【2026-09-30 修正】这里刻意**不传 base_url**, 与生产调用签名一致。
    # 旧版传了 base_url, 是【假阳性】—— 当时工厂不读 config["backend_url"],
    # 生产里十几处 create_llm_client(provider, model) 全都拿不到地址, 一调就抛
    # "requires a base_url"。工厂已补配置兜底, 故此处按真实签名验证。
    # 深链路验证见 verify_factory_fix.py。
    client = create_llm_client(provider, model, no_thinking=True)
    llm = client.get_llm()
    print(f"  客户端类 = {type(llm).__name__}")
    print(f"  实际 base_url = {getattr(llm, 'openai_api_base', None)}")
    r = llm.invoke("只回复两个字：收到")
    content = getattr(r, "content", r)
    print(f"  回复 = {content!r}")
    chk(bool(content), "经封装层调用成功且有内容", repr(content)[:60])
except Exception as e:
    import traceback

    print(f"  [FAIL] 调用异常: {type(e).__name__}: {e}")
    traceback.print_exc()
    checks.append(False)

print()
print("=" * 74)
print(f"结果: {sum(checks)} 通过 / {len(checks) - sum(checks)} 失败")
print("=" * 74)
