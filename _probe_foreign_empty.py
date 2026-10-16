"""复刻 2026-09-29 失败的那条调用：META 路 prompt + 同一份 PDF 全文。
目的：确认 content 是否为空、reasoning_content 是否有内容（即"空响应"真因）。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:\AgentSense\AgentSense")

from dotenv import load_dotenv

load_dotenv(r"D:\AgentSense\AgentSense\.env", override=True)

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.config import set_config

set_config(DEFAULT_CONFIG)

import foreign_report as fr

PDF = Path(r"D:\AgentSense\AgentSense\data\foreign\uploads\9b63f10c8a9160d3.pdf")
print("PDF exists:", PDF.exists())

tmp = Path(tempfile.mkdtemp())
data = fr.extract_pdf(PDF, tmp)
full_text, truncated = fr._join_text(data["pages"])
print(f"页数={data['n_pages']} 字符={data['chars']} 图={len(data['images'])} 送入分析字符={len(full_text)} 截断={truncated}")

print("\n当前生效配置:")
print("  provider =", DEFAULT_CONFIG.get("llm_provider"))
print("  model    =", DEFAULT_CONFIG.get("quick_think_llm"))
print("  backend  =", DEFAULT_CONFIG.get("backend_url"))

llm = fr._make_llm()
print("  llm      =", type(llm).__name__, "| model:", getattr(llm, "model_name", "?"))

prompt = fr.PROMPT_META + "\n\n【原文正文】\n" + full_text
print(f"\nprompt 总长 {len(prompt)} 字符，调 LLM 中...")

r = llm.invoke(prompt)
content = getattr(r, "content", r)
reas = (getattr(r, "additional_kwargs", None) or {}).get("reasoning_content")

print("\n===== 结果 =====")
print("content 类型:", type(content).__name__)
print("content 长度:", len(str(content)) if content is not None else None)
print("content 前 200:", repr(str(content)[:200]))
print("reasoning_content 长度:", len(reas) if reas else 0)
if reas:
    print("reasoning 前 200:", repr(str(reas)[:200]))

if not str(content or "").strip() and reas:
    print("\n>>> 判定：**content 为空但 reasoning_content 有内容** —— 空响应真因确认。")
elif str(content or "").strip():
    print("\n>>> 判定：content 非空（当前配置下该路可正常返回）。")
    try:
        obj = fr._json_from(str(content))
        print("    JSON 解析成功，顶层键:", list(obj)[:8])
    except Exception as e:
        print("    JSON 解析失败:", e)
