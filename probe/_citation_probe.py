"""注入块引用率探针（2026-10-22）。

【问什么】
注入块写进 system prompt 之后，模型**真的用了**吗？还是当成背景字读过去了？

【怎么判】
不能用「报告里出现了基差 550」当证据 —— 那个数字工具也能查到，分不清来源。
所以用**硬锚点**：只在注入块里出现、现有 15 个 @tool 拿不到的结论型措辞。

  技术视图   ：价格轴 / 加权分 / 闸门 / 价格轴票
  基本面三轴 ：三指标策略 / 虚实盘比 / 产业轴票
  国君四源   ：国君四源 / 三态 / 权重：与公开接口

硬锚点命中 = 模型读了块。软锚点（数字、分位、基差等）另列，只作辅助参考。

【前提】
必须先 `warm()` 把块算出来（当前竞态 bug 下首个节点拿不到块），否则测的是空块。

【用法】
  ./venv/Scripts/python.exe probe/_citation_probe.py [TA]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "probe" / "_citation_probe.json"

# 只可能在注入块里出现的结论型措辞（工具返回的是原始序列，不含这些词）
# ⚠ 「国君四源」不能只用块名做锚点：实测模型引用了块的内文（"注入块 7.89%（以现货为分母）"）
#   却从不复述块名，按块名判会误判成 0 命中。故把「注入块 / 四源」也算进去。
HARD = {
    "技术视图": ["价格轴", "加权", "闸门", "价格轴票"],
    "基本面三轴": ["三指标策略", "虚实盘比", "产业轴票", "结构性结论"],
    "国君四源": ["国君四源", "注入块", "四源"],
}
# 块里有、但工具查询也可能给出的措辞（软证据，只作参考）
SOFT = ["分位", "基差", "仓单", "社会库存", "加工利润"]


def _hits(text: str, words: list[str]) -> list[dict]:
    out = []
    for w in words:
        idx = text.find(w)
        if idx >= 0:
            lo, hi = max(0, idx - 28), min(len(text), idx + 58)
            out.append({"word": w, "ctx": text[lo:hi].replace("\n", " ")})
    return out


def main() -> int:
    sym = (sys.argv[1] if len(sys.argv) > 1 else "TA").upper()

    # 1) 预热（阻塞到算完），确保块真的存在
    from tradingagents.dataflows import analysis_bundle as B
    from tradingagents.dataflows import analyst_views as V

    print(f">>> 预热注入块 {sym} ...", flush=True)
    t0 = time.time()
    V.warm(sym)
    gtja = B.gtja_context(sym)
    tech_block = V.technical_view(sym)
    fund_block = V.fundamental_view(sym)
    print(
        f"    技术视图 {len(tech_block)} 字 | 三轴 {len(fund_block)} 字 | 四源 {len(gtja)} 字"
        f"（{time.time() - t0:.1f}s）",
        flush=True,
    )
    if not tech_block:
        print("    !! 技术视图为空，引用率无从测起（预热失败或数据源异常）")

    # 2) 构建真实 LLM
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.llm_clients import create_llm_client

    cfg = DEFAULT_CONFIG
    client = create_llm_client(
        provider=cfg["llm_provider"],
        model=cfg["deep_think_llm"],
        base_url=cfg.get("backend_url"),
    )
    llm = client.get_llm()
    print(f">>> LLM: {cfg['llm_provider']} / {cfg['deep_think_llm']}", flush=True)

    from tradingagents.agents.analysts.commodity_analysts import (
        create_commodity_fundamental_analyst,
        create_commodity_technical_analyst,
    )

    base_state = {
        "trade_date": time.strftime("%Y-%m-%d"),
        "company_of_interest": sym,
        "messages": [],
        "past_context": "",
    }

    results = {}
    for label, factory, key in (
        ("技术分析师", create_commodity_technical_analyst, "technical_report"),
        ("基本面分析师", create_commodity_fundamental_analyst, "fundamental_report"),
    ):
        t1 = time.time()
        print(f">>> 真跑 {label}（含工具循环，可能 1~3 分钟）...", flush=True)
        try:
            node = factory(llm=llm)
            out = node(dict(base_state))
            report = out.get(key, "") or ""
            err = None
        except Exception as exc:  # noqa: BLE001 - 单节点失败不中断
            report, err = "", f"{type(exc).__name__}: {exc}"
        dt = round(time.time() - t1, 1)
        results[label] = {"report": report, "secs": dt, "error": err}
        print(f"    {label}: {len(report)} 字 / {dt}s" + (f"  !! {err}" if err else ""), flush=True)

    # 3) 锚点比对
    pairs = {
        "技术分析师": ("技术视图", tech_block),
        "基本面分析师": ("基本面三轴", fund_block),
    }
    summary = {}
    for label, (block_name, block_text) in pairs.items():
        rep = results[label]["report"]
        hard = _hits(rep, [w for w in HARD[block_name] if w in block_text])
        soft = _hits(rep, [w for w in SOFT if w in block_text])
        summary[label] = {
            "report_chars": len(rep),
            "secs": results[label]["secs"],
            "error": results[label]["error"],
            "hard_hits": hard,
            "hard_all": HARD[block_name],
            "soft_hits": soft,
            "block_chars": len(block_text),
        }

    # 四源块（基本面节点同一份报告）
    rep = results["基本面分析师"]["report"]
    summary["基本面分析师"]["gtja_hard_hits"] = _hits(rep, [w for w in HARD["国君四源"] if w in gtja])

    print("\n=== 引用率（硬锚点 = 只可能来自注入块）===")
    for label, s in summary.items():
        if label == "基本面分析师":
            continue
        total = len(s["hard_all"])
        used = [h["word"] for h in s["hard_hits"]]
        print(f"\n[{label}] 报告 {s['report_chars']} 字，块 {s['block_chars']} 字")
        print(f"  硬锚点 {len(used)}/{total}: {used or '（一个都没命中）'}")
        for h in s["hard_hits"]:
            print(f"    · {h['word']} → …{h['ctx']}…")
        print(f"  软锚点 {sorted({h['word'] for h in s['soft_hits']})}")

    s = summary["基本面分析师"]
    used = [h["word"] for h in s.get("gtja_hard_hits") or []]
    print(f"\n[基本面分析师 · 国君四源] 硬锚点 {len(used)}/{len(HARD['国君四源'])}: {used or '（一个都没命中）'}")
    for h in s.get("gtja_hard_hits") or []:
        print(f"    · {h['word']} → …{h['ctx']}…")

    OUT.write_text(
        json.dumps(
            {"symbol": sym, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "summary": summary, "reports": {k: v["report"] for k, v in results.items()},
             "blocks": {"technical": tech_block, "fundamental": fund_block, "gtja": gtja}},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
