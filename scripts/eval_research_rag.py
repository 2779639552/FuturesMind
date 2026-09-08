"""scripts/eval_research_rag.py — 研报 RAG 批量评测(检索 / 忠实度 / 答案相关性)

【模块角色】
  给研报 RAG(/api/research/ask)建立回归基准:黄金问答集人工标注期望,
  本脚本逐题调用运行中的 web_app(默认 http://127.0.0.1:5000)并自动统计:

  检索层(规则计算,零 LLM 消耗):
    1. 检索命中 Hit Rate / MRR:citations 品种是否落在 expect_varieties 内。
    2. 上下文召回 Recall:expect_keywords 应出现在检索片段全文中(ask 请求带
       include_context=true 拿全文,snippet 只有 200 字会低估)。
    3. 日期时效:命中片段 publish_date >= expect_date_min。
    4. 引用一致性:回答 [n] ⊆ citations 编号;citations 内 report_id 不重复。

  生成层(LLM-as-judge,与 ask 同款 quick 模型):
    5. 忠实度 Faithfulness:judge 逐条核对答案论断是否被所引片段支撑,
       unsupported=0 记通过(防编造/照抄过期观点)。
    6. 答案相关性 Relevance:judge 打 1~5 分,>=4 记通过;should_refuse 题
       以拒答正确性代替(回答含拒答字样且不给编造内容)。

【用法】(在 AgentSense 根目录跑,web_app 须已启动)
  python scripts/eval_research_rag.py                        # 跑全部用例
  python scripts/eval_research_rag.py --workers 3            # 并发(≤4,防 LLM 429)
  python scripts/eval_research_rag.py --cases 1,3,5          # 只跑指定序号(1 起)
  python scripts/eval_research_rag.py --no-judge             # 只跑检索层(零 LLM)

【注意】
  黄金集在 scripts/rag_golden_set.json,每题人工标注 expect_* 与 should_refuse;
  每题消耗:1 次 ask(quick 模型)+ 2 次 judge 调用;10 题 workers 3 约 5~8 分钟。
  指标只在终端汇总输出,不进前端(2026-09-08 用户要求)。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GOLDEN_PATH = Path(__file__).resolve().parent / "rag_golden_set.json"
DEFAULT_BASE = "http://127.0.0.1:5000"

# 拒答字样(生成层"材料不足直说"的行为验证)
_REFUSE_PAT = re.compile(r"不足|无法|未检索|没有相关|材料中未|未提及|不足以")

_CITE_PAT = re.compile(r"\[(\d+)\]")

# LLM judge 输出 JSON 解析:取首个 {...} 平衡块
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _ask(base: str, case: dict, timeout: int = 240) -> dict:
    body = json.dumps(
        {
            "question": case["question"],
            "variety": case.get("variety") or "",
            "report_type": case.get("report_type") or "",
            "top_k": 6,
            "include_context": True,  # recall 要对片段全文算,snippet 会低估
        }
    ).encode("utf-8")
    req = Request(f"{base}/api/research/ask", data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _make_judge():
    """与 ask 路由同款 quick 模型(读 ~/.tradingagents/web_config.json 覆盖默认)。"""
    import web_app
    from tradingagents.llm_clients.factory import create_llm_client

    cfg = dict(web_app.config)
    saved = Path.home() / ".tradingagents" / "web_config.json"
    if saved.exists():
        cfg.update(json.loads(saved.read_text(encoding="utf-8")) or {})
    client = create_llm_client(
        cfg["llm_provider"], cfg.get("quick_think_llm", cfg["deep_think_llm"])
    )
    return client.get_llm()


def _invoke_json(llm, prompt: str) -> dict | None:
    """调 LLM 并解析 JSON 输出;解析失败返回 None(该题该指标记 n/a,不硬失败)。"""
    try:
        result = llm.invoke(prompt)
        text = result.content if hasattr(result, "content") else str(result)
    except Exception as e:  # noqa: BLE001  judge 挂了不影响其他指标
        return {"_error": str(e)[:120]}
    m = _JSON_RE.search(text)
    if not m:
        return {"_error": f"no json: {text[:80]}"}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {"_error": f"bad json: {text[:80]}"}


_FAITH_PROMPT = (
    "你是严格的评测裁判。根据「参考片段」逐条核对「回答」中的可验证论断"
    "(事实、数据、观点归属)。判定标准:\n"
    "- 论断被片段直接支撑,或为片段内容的同义概括/合理归纳 → supported;\n"
    "- 片段中没有依据的新事实/新数据/新观点归属(编造、张冠李戴)→ unsupported;\n"
    "- 回答中对材料局限的说明(如\"片段未给出X\"\"未提供最新数据\")不是论断,不计入;\n"
    "- 纯衔接语、小标题、总结性复述不计入。"
    "注意片段可能含表格数字,请仔细在其中找依据。\n\n"
    "【回答】\n{answer}\n\n【参考片段】\n{context}\n\n"
    '只输出 JSON:{{"total": 论断数, "unsupported": [不被支撑的论断原文,…]}}'
)

_RELEV_PROMPT = (
    "你是评测裁判。判断「回答」是否切实回应了「问题」:覆盖了问题的各主要方面、"
    "正面作答而非回避。完全跑题/答非所问=1,部分回应=3,充分回应=5。\n\n"
    "【问题】{question}\n\n【回答】\n{answer}\n\n"
    '只输出 JSON:{{"score": 1~5, "reason": "一句话理由"}}'
)


def _eval_case(idx: int, case: dict, base: str, llm) -> dict:
    out = {"no": idx, "question": case["question"], "checks": {}, "ok": True}
    t0 = time.time()
    try:
        d = _ask(base, case)
    except (URLError, HTTPError, TimeoutError) as e:
        out["ok"] = False
        out["error"] = f"请求失败: {e}"
        return out
    out["latency_s"] = round(time.time() - t0, 1)
    citations = d.get("citations") or []
    answer = d.get("answer") or ""
    checks = out["checks"]

    # 1) 检索命中(Hit Rate + MRR 代理);拒答题无意义,不判
    expect_codes = [c.strip().upper() for c in (case.get("expect_varieties") or []) if c.strip()]
    if case.get("should_refuse"):
        checks["hit"] = None
        checks["mrr"] = None
    elif expect_codes:
        ranks = [
            i
            for i, c in enumerate(citations, 1)
            if str(c.get("variety") or "").upper() in expect_codes
        ]
        checks["hit"] = bool(ranks)
        checks["mrr"] = round(1.0 / ranks[0], 3) if ranks else 0.0
    else:
        checks["hit"] = bool(citations)
        checks["mrr"] = None

    # 2) 上下文召回 Recall:期望关键词出现在片段全文的比例
    keywords = [k for k in (case.get("expect_keywords") or []) if k.strip()]
    full_texts = [c.get("text") or "" for c in citations]
    if keywords and full_texts:
        found = [k for k in keywords if any(k in t for t in full_texts)]
        checks["recall"] = round(len(found) / len(keywords), 2)
        checks["recall_missed"] = [k for k in keywords if k not in found]
    else:
        checks["recall"] = None

    # 3) 日期时效(防引用过期观点)
    date_min = (case.get("expect_date_min") or "").strip()
    if date_min and citations:
        in_range = [c for c in citations if (c.get("publish_date") or "") >= date_min]
        checks["date_ok"] = bool(in_range)
    else:
        checks["date_ok"] = None

    # 4) 引用一致性:回答 [n] ⊆ citations 编号;同篇引用 ≤2 块
    #    (2026-09-08 起 retrieve_hits 去重改 max_per_report=2,每篇允许 2 条引用)
    cite_nos = {c.get("no") for c in citations}
    used_nos = {int(m) for m in _CITE_PAT.findall(answer)}
    checks["cite_valid"] = used_nos <= cite_nos if (answer and citations) else None
    rids = [c.get("report_id") for c in citations]
    counts: dict = {}
    for rid in rids:
        counts[rid] = counts.get(rid, 0) + 1
    checks["no_dup_reports"] = all(v <= 2 for v in counts.values()) if citations else None

    # 5) 拒答能力(should_refuse 题替代 relevance/faithfulness)
    if case.get("should_refuse"):
        checks["refused"] = bool(_REFUSE_PAT.search(answer)) or not citations
    else:
        checks["refused"] = None

    # 6) LLM-as-judge:忠实度 + 答案相关性
    if llm is not None and answer and not case.get("should_refuse"):
        cited = [c for c in citations if c.get("no") in used_nos] or citations
        context = "\n".join(
            f"[{c['no']}] {c.get('publish_date','')}《{c.get('title','')}》:{c.get('text') or c.get('snippet','')}"
            for c in cited
        )
        faith = _invoke_json(
            llm, _FAITH_PROMPT.format(answer=answer, context=context[:8000])
        )
        if "_error" in faith:
            checks["faithful"] = None
            out["judge_error"] = f"faithfulness: {faith['_error']}"
        else:
            n_unsup = len(faith.get("unsupported") or [])
            checks["faithful"] = n_unsup == 0
            checks["unsupported"] = faith.get("unsupported") or []

        relev = _invoke_json(llm, _RELEV_PROMPT.format(question=case["question"], answer=answer))
        if "_error" in relev:
            checks["relevance"] = None
            out["judge_error"] = out.get("judge_error", "") + f" relevance: {relev['_error']}"
        else:
            try:
                checks["relevance"] = int(relev.get("score", 0))
            except (TypeError, ValueError):
                checks["relevance"] = None
    elif not case.get("should_refuse"):
        checks["faithful"] = None
        checks["relevance"] = None

    failed = [k for k, v in checks.items() if v is False]
    out["ok"] = not failed
    out["failed_checks"] = failed
    out["answer_head"] = answer[:120].replace("\n", " ")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="研报 RAG 批量评测(需 web_app 已启动)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"web_app 地址(默认 {DEFAULT_BASE})")
    ap.add_argument("--workers", type=int, default=2, help="并发数(≤4,与 LLM 并发上限 5 兼容)")
    ap.add_argument("--cases", default="", help="只跑指定序号,如 1,3,5(1 起)")
    ap.add_argument("--no-judge", action="store_true", help="跳过 LLM judge(只跑检索层)")
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = list(enumerate(golden["cases"], 1))
    if args.cases:
        wanted = {int(x) for x in args.cases.split(",")}
        cases = [(i, c) for i, c in cases if i in wanted]

    llm = None
    if not args.no_judge:
        try:
            llm = _make_judge()
        except Exception as e:  # noqa: BLE001  judge 不可用则退化为纯检索层
            print(f"⚠ judge 初始化失败,退化为纯检索层: {e}")
    print(f"评测 {len(cases)} 题(并发 {args.workers}, judge={'开' if llm else '关'},目标 {args.base})")

    def run(item):
        i, c = item
        print(f"  [{i}] {c['question'][:30]}…", flush=True)
        return _eval_case(i, c, args.base, llm)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(run, cases))

    # 汇总
    n_ok = sum(1 for r in results if r.get("ok"))
    lat = [r["latency_s"] for r in results if "latency_s" in r]
    recalls = [r["checks"]["recall"] for r in results if r["checks"].get("recall") is not None]
    faithfuls = [r["checks"]["faithful"] for r in results if r["checks"].get("faithful") is not None]
    relevs = [r["checks"]["relevance"] for r in results if r["checks"].get("relevance") is not None]
    refusals = [r["checks"]["refused"] for r in results if r["checks"].get("refused") is not None]

    print("\n===== 汇总 =====")
    head = f"通过: {n_ok}/{len(results)}"
    if lat:
        head += f"   平均延迟: {sum(lat) / len(lat):.1f}s"
    print(head)
    if recalls:
        print(f"Recall(上下文关键词召回): 平均 {sum(recalls) / len(recalls):.2f}   满召回 {sum(1 for x in recalls if x == 1.0)}/{len(recalls)} 题")
    if faithfuls:
        print(f"Faithfulness(答案有据): {sum(1 for x in faithfuls if x)}/{len(faithfuls)}")
    if relevs:
        print(f"Relevance(答案切题 1~5): 平均 {sum(relevs) / len(relevs):.2f}   >=4 分 {sum(1 for x in relevs if x >= 4)}/{len(relevs)} 题")
    if refusals:
        print(f"拒答正确: {sum(1 for x in refusals if x)}/{len(refusals)}")

    for r in results:
        flag = "✅" if r.get("ok") else "❌"
        extra = f" 失败项={r['failed_checks']}" if not r.get("ok") else ""
        err = f" ({r['error']})" if r.get("error") else ""
        print(f"{flag} #{r['no']} [{r.get('latency_s', '?')}s]{err} {r['question'][:24]}{extra}")
        c = r["checks"]
        brief = {
            k: c[k]
            for k in ("hit", "mrr", "recall", "date_ok", "cite_valid", "faithful", "relevance", "refused")
            if k in c
        }
        print(f"    {brief}")
        if c.get("recall_missed"):
            print(f"    召回缺失关键词: {c['recall_missed']}")
        if c.get("unsupported"):
            print(f"    无据论断: {c['unsupported']}")
        if r.get("answer_head"):
            print(f"    答: {r['answer_head']}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
