"""运行分析注入块体检（2026-10-22）。

【为什么要有这个探针】
P0/P1 的验收只证明「块存在」，没证明四件事：
  1. 块在 system prompt 里的**位置**是否符合设计（计划写的是"全部放最前"）；
  2. 块与块之间的**相对顺序**是否合理（结论块 vs 读数块谁先）；
  3. 块的实际长度是否超预算（计划：三块合计 ≤1.2k tokens；单块 ≤800 字）；
  4. 首调竞态：`warm()` 只起线程不等待，第一个节点是否拿得到块。

【做法】
双通道测量，互不依赖：
  A. **块级直测**：直接调用每个块函数（gtja_context / technical_view / ...）量字符数与耗时。
     准确 —— 不受 prompt 里空行/分隔符影响。
  B. **提示捕获**：真实构建节点，只拦 LLM 出口拿最终 system prompt，用 `find()` 定位每块
     实际出现的位置与先后顺序。真实 —— 模型看到的就是这一段。

【用法】
  ./venv/Scripts/python.exe probe/_run_prompt_audit.py [TA RB ...]     默认 TA
  ./venv/Scripts/python.exe probe/_run_prompt_audit.py --race          只跑首调竞态复现
输出：
  probe/_prompt_audit.json   机器可读（含全文，便于二次分析）
  控制台报告                 块长度/耗时/顺序/预算超限 + 竞态结论
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_JSON = ROOT / "probe" / "_prompt_audit.json"

# 块头形如【国君四源 · ...】/【方法论读数 · 技术面】(说明文字)
BLOCK_HEAD = re.compile(r"^【([^】\n]{2,60})】", re.M)

# 明显的数据污染：模型看到这些会当成真读数
POLLUTION = re.compile(r"\b(None|nan|NaN|inf)\b|None%|nan%|\bundefined\b")

# 设计预算（来自 docs/run-analysis-enrichment-plan-2026-10-19.html §06）
BUDGET = {
    "【国君四源": 800,        # 单块 ≤800 字
    "【技术面量化": 800,
    "【基本面量化": 800,
    "【两方结论对照": 800,
    "三块合计": 2400,         # 三块合计 ≤1.2k tokens ≈ 2400 字
}


# ---------------------------------------------------------------------------
# A. 块级直测
# ---------------------------------------------------------------------------
def measure_blocks(symbol: str) -> list[dict]:
    """逐个调用注入块函数，量字符数/行数/耗时。异常不外抛，记为 error 继续。"""
    from tradingagents.dataflows import analysis_bundle as B
    from tradingagents.dataflows import analyst_views as V
    from tradingagents.dataflows.methodology_context import (
        fundamental_methodology_context,
        technical_methodology_context,
    )
    from tradingagents.agents.utils.commodity_futures_tools import variety_db_context

    plan = [
        ("技术·方法论读数", "technical_methodology_context", lambda: technical_methodology_context(symbol), "技术分析师"),
        ("技术·价格轴视图", "technical_view", lambda: V.technical_view(symbol), "技术分析师"),
        ("基本面·方法论读数", "fundamental_methodology_context", lambda: fundamental_methodology_context(symbol), "基本面分析师"),
        ("基本面·品种数据库", "variety_db_context", lambda: variety_db_context(symbol), "基本面分析师"),
        ("基本面·三轴视图", "fundamental_view", lambda: V.fundamental_view(symbol), "基本面分析师"),
        ("基本面·国君四源", "gtja_context", lambda: B.gtja_context(symbol), "基本面分析师"),
        ("研究经理·两方对照", "reconcile_view", lambda: V.reconcile_view(symbol), "研究经理"),
    ]

    out: list[dict] = []
    for label, fn_name, fn, owner in plan:
        t0 = time.time()
        try:
            text = fn() or ""
            err = None
        except Exception as exc:  # noqa: BLE001 - 单块失败不中断体检
            text, err = "", f"{type(exc).__name__}: {exc}"
        secs = round(time.time() - t0, 2)
        head = BLOCK_HEAD.match(text.strip()) if text.strip() else None
        out.append(
            {
                "label": label,
                "func": fn_name,
                "owner": owner,
                "chars": len(text),
                "lines": text.count("\n") + 1 if text else 0,
                "secs": secs,
                "head": head.group(1) if head else "",
                "pollution": POLLUTION.findall(text)[:5],
                "error": err,
                "text": text,
            }
        )
        print(f"  {label:<18} {len(text):>5}字 {secs:>6.2f}s", flush=True)
    return out


# ---------------------------------------------------------------------------
# B. 提示捕获（真实节点）
# ---------------------------------------------------------------------------
def _capture_analyst(factory_name: str, symbol: str, trade_date: str) -> dict:
    from tradingagents.agents.analysts import commodity_analysts as mod

    cap: dict = {}

    def _fake_loop(llm, tools, initial_messages, **_kw):
        cap["prompt"] = initial_messages[0].content
        cap["tools"] = [t.name for t in tools]
        return "REPORT"

    orig = mod._run_tool_loop
    mod._run_tool_loop = _fake_loop
    try:
        node = getattr(mod, factory_name)(llm=object())
        node(
            {
                "trade_date": trade_date,
                "company_of_interest": symbol,
                "messages": [],
                "past_context": "",
            }
        )
    finally:
        mod._run_tool_loop = orig
    return cap


def _capture_manager(symbol: str) -> dict:
    from tradingagents.agents.managers import research_manager as rm

    cap: dict = {}

    def _fake_invoke(structured_llm, llm, prompt, render, agent_name):
        cap["prompt"] = prompt
        cap["tools"] = []
        return "PLAN"

    orig = rm.invoke_structured_or_freetext
    rm.invoke_structured_or_freetext = _fake_invoke
    try:
        node = rm.create_research_manager(llm=object())
        node(
            {
                "company_of_interest": symbol,
                "trade_date": "",
                "investment_debate_state": {
                    "history": "(辩论历史占位：真实链路此处为多空完整对话)",
                    "bull_history": "",
                    "bear_history": "",
                    "count": 1,
                },
            }
        )
    finally:
        rm.invoke_structured_or_freetext = orig
    return cap


def locate_blocks(prompt: str) -> list[dict]:
    """在最终 prompt 里定位每个块头的出现位置（字符偏移 + 百分比 + 行号）。"""
    total = max(1, len(prompt))
    out = []
    for m in BLOCK_HEAD.finditer(prompt):
        name = m.group(1)
        out.append(
            {
                "name": name,
                "pos": m.start(),
                "pct": round(m.start() * 100.0 / total, 1),
                "line": prompt.count("\n", 0, m.start()) + 1,
            }
        )
    return out


# ---------------------------------------------------------------------------
# C. 首调竞态复现
# ---------------------------------------------------------------------------
def race_repro(symbol: str) -> dict:
    """复现 run_analysis 的真实时间线：起预热线程、不等待，紧接着取块。

    只看一件事：**第一个节点**（技术分析师）在预热未完成时能不能拿到块。
    """
    from tradingagents.dataflows import analyst_views as A
    from tradingagents.dataflows import analysis_bundle as B

    A._SNAP.clear()
    A._EVENTS.clear()
    B.clear_cache()

    t0 = time.time()
    threading.Thread(target=B.gtja_context, args=(symbol,), daemon=True, name="gtja-warm").start()
    threading.Thread(target=A.warm, args=(symbol,), daemon=True, name="views-warm").start()

    tv = A.technical_view(symbol)
    t_tv = round(time.time() - t0, 2)
    fv = A.fundamental_view(symbol)
    t_fv = round(time.time() - t0, 2)
    g = B.gtja_context(symbol)
    t_g = round(time.time() - t0, 2)
    rv = A.reconcile_view(symbol)
    t_rv = round(time.time() - t0, 2)

    return {
        "symbol": symbol,
        "technical_view": {"chars": len(tv), "at": t_tv},
        "fundamental_view": {"chars": len(fv), "at": t_fv},
        "gtja_context": {"chars": len(g), "at": t_g},
        "reconcile_view": {"chars": len(rv), "at": t_rv},
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def print_block_table(blocks: list[dict], prompt_by_node: dict) -> None:
    print(f"\n{'块':<20}{'归属':<10}{'字数':>7}{'行':>6}{'耗时':>8}  提示内位置")
    print("-" * 78)
    for b in blocks:
        pos = "未注入"
        p = prompt_by_node.get(b["owner"], "")
        if b["head"] and b["chars"]:
            i = p.find(f"【{b['head']}】")
            if i >= 0:
                pos = f"第 {p.count(chr(10), 0, i) + 1} 行（{i * 100 // max(1, len(p))}%）"
        flag = ""
        if b["error"]:
            flag = f"  !! {b['error']}"
        elif b["pollution"]:
            flag = f"  !! 污染 {b['pollution']}"
        print(f"{b['label']:<20}{b['owner']:<10}{b['chars']:>7}{b['lines']:>6}{b['secs']:>7.2f}s  {pos}{flag}")

    print("\n预算检查（计划 §06：单块 ≤800 字，三块合计 ≤2400 字）")
    big = [b for b in blocks if b["chars"] > 800]
    core = [b for b in blocks if b["label"] in (
        "基本面·国君四源", "技术·价格轴视图", "基本面·三轴视图", "研究经理·两方对照")]
    total = sum(b["chars"] for b in core)
    print(f"  单块超 800 字: {len(big)} 个" + (f" → {[b['label'] + ':' + str(b['chars']) for b in big]}" if big else ""))
    print(f"  四个结论/读数块合计 {total} 字（预算 2400）" + ("  ⚠ 超预算" if total > 2400 else "  ok"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_race = "--race" in sys.argv
    symbols = args or ["TA"]
    trade_date = time.strftime("%Y-%m-%d")
    payload: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "budget": BUDGET, "symbols": {}}

    for sym in symbols:
        print(f"\n>>> {sym} 块级直测（真实取数）", flush=True)
        blocks = measure_blocks(sym)

        print(f"\n>>> {sym} 提示捕获（真实节点构建）", flush=True)
        prompts: dict[str, str] = {}
        for label, fname in (
            ("技术分析师", "create_commodity_technical_analyst"),
            ("基本面分析师", "create_commodity_fundamental_analyst"),
        ):
            try:
                cap = _capture_analyst(fname, sym, trade_date)
                prompts[label] = cap.get("prompt", "")
            except Exception as exc:  # noqa: BLE001
                prompts[label] = ""
                print(f"  !! {label} 构建失败: {type(exc).__name__}: {exc}")
        try:
            prompts["研究经理"] = _capture_manager(sym).get("prompt", "")
        except Exception as exc:  # noqa: BLE001
            prompts["研究经理"] = ""
            print(f"  !! 研究经理 构建失败: {type(exc).__name__}: {exc}")

        print_block_table(blocks, prompts)

        for label, p in prompts.items():
            print(f"\n  [{label}] system prompt 共 {len(p)} 字，块顺序：")
            locs = locate_blocks(p)
            if not locs:
                print("    （无块头）")
            for i, l in enumerate(locs, 1):
                print(f"    {i}. 第 {l['line']:>3} 行（{l['pct']:>5}%）  {l['name']}")

        race = None
        if do_race:
            print(f"\n>>> {sym} 首调竞态复现（起预热线程后立即取块）", flush=True)
            race = race_repro(sym)
            tv, fv, g = race["technical_view"], race["fundamental_view"], race["gtja_context"]
            print(f"  技术分析师 technical_view : {tv['at']:>6.2f}s → {tv['chars']:>5} 字"
                  + ("   ⚠ 空（预热未完成即超时）" if tv["chars"] == 0 else "   ok"))
            print(f"  基本面分析师 fundamental_view: {fv['at']:>6.2f}s → {fv['chars']:>5} 字"
                  + ("   ⚠ 空" if fv["chars"] == 0 else "   ok"))
            print(f"  基本面分析师 gtja_context  : {g['at']:>6.2f}s → {g['chars']:>5} 字"
                  + ("   ⚠ 空" if g["chars"] == 0 else "   ok"))

        payload["symbols"][sym] = {
            "blocks": [{k: v for k, v in b.items() if k != "text"} for b in blocks],
            "block_text": {b["label"]: b["text"] for b in blocks},
            "prompts": prompts,
            "locate": {k: locate_blocks(v) for k, v in prompts.items()},
            "race": race,
        }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n-> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
