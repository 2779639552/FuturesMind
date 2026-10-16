"""注入三层验证 —— 证明"方法论读数真的进了分析师的 system prompt"。

【为什么要三层, 一层不够】
  只看代码: Edit 成功 ≠ 提示里真有 —— 变量可能被覆盖、函数可能早退。
  只看输出: 线上分析变长了 ≠ 注入生效 —— 可能只是 LLM 话变多。
  所以必须: ①截获真实 prompt ②验证节点边界 ③变异测试证伪敏感度。

【三层】
  层1 真 prompt 截获: 用假 LLM 拦截 node 实际送给模型的 system 文本, 断言注入块在场。
  层2 节点边界: 技术面块**不得**含产业数据(库存周期), 基本面块**不得**含几何周期绑定 ——
       防止"只给该用的分析师"这条纪律被破坏(否则模型会拿库存解释 K 线)。
  层3 变异(证伪): 猴子补丁把注入函数改成返回 "" → 断言层1的断言**必须变红**。
       不变红说明层1是永真断言, 等于没验证。

【用法】python _verify_injection.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage  # noqa: E402

from tradingagents.agents.analysts import commodity_analysts as ca  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, PASS if cond else FAIL, detail))


class _CapturingLLM:
    """假 LLM: 捕获送给模型的 system 文本, 立即结束(不调工具)。"""

    def __init__(self) -> None:
        self.captured: str = ""
        self.invoke_count = 0

    def bind_tools(self, tools):  # noqa: ANN001 - 只需返回 self 即可
        return self

    def invoke(self, messages):  # noqa: ANN001
        self.invoke_count += 1
        # 系统提示是第一条; 把所有 message 的文本拼起来更能兜住"注入到别处"
        parts = []
        for m in messages:
            c = getattr(m, "content", "")
            parts.append(c if isinstance(c, str) else str(c))
        self.captured = "\n".join(parts)
        return AIMessage(content="MOCK_REPORT_OK", tool_calls=[])


def _run_node(factory, symbol: str = "RB") -> str:
    llm = _CapturingLLM()
    node = factory(llm)
    node({"trade_date": "2026-09-29", "company_of_interest": symbol, "messages": []})
    return llm.captured


# ──────────────────────────────────────────────────────────────────────
# 层 1 + 层 2: 截获真实 prompt, 校验在场与边界
# ──────────────────────────────────────────────────────────────────────
def layer_1_and_2() -> dict[str, str]:
    captured: dict[str, str] = {}

    tech = _run_node(ca.create_commodity_technical_analyst)
    captured["technical"] = tech
    _check("L1-技术面: 注入块在场", "【方法论读数 · 技术面】" in tech,
           f"len={len(tech)}")
    _check("L1-技术面: 新增周期绑定卡到场", "◆ 周期绑定" in tech and "cap-trend-definition" in tech)
    _check("L1-技术面: 新增排除法卡到场",
           "◆ 方向排除法裁决" in tech and "cap-direction-exclusion" in tech)
    _check("L1-技术面: 注入块置于正文之前(优先阅读)",
           tech.find("【方法论读数 · 技术面】") < tech.find("**Your Role**")
           if "**Your Role**" in tech else "【方法论读数 · 技术面】" in tech)
    # 层 2 边界
    _check("L2-技术面: 不含产业数据(库存周期四阶段)",
           "库存周期四阶段" not in tech and "cap-inventory-cycle" not in tech)

    fund = _run_node(ca.create_commodity_fundamental_analyst)
    captured["fundamental"] = fund
    _check("L1-基本面: 注入块在场", "【方法论读数 · 基本面】" in fund, f"len={len(fund)}")
    _check("L1-基本面: 新增期限结构卡到场",
           "◆ 期限结构" in fund and "cap-term-structure" in fund)
    _check("L1-基本面: 新增库存周期四阶段卡到场",
           "◆ 库存周期四阶段" in fund)
    # 层 2 边界
    _check("L2-基本面: 不含技术面几何块(周期绑定)",
           "◆ 周期绑定" not in fund and "cap-trend-definition" not in fund)

    macro = _run_node(ca.create_commodity_macro_analyst)
    captured["macro"] = macro
    _check("L1-宏观: 注入块在场", "【方法论读数 · 宏观口径】" in macro, f"len={len(macro)}")
    _check("L1-宏观: 不含技术面几何块", "◆ 趋势几何" not in macro)
    _check("L1-宏观: 不含库存周期块", "◆ 库存周期四阶段" not in macro)

    # 体量体检: 注入块不该把提示撑爆(经验阈值)
    _check("L1-体量: 技术面提示 < 40k 字符", len(tech) < 40_000, f"len={len(tech)}")
    _check("L1-体量: 基本面提示 < 40k 字符", len(fund) < 40_000, f"len={len(fund)}")
    return captured


# ──────────────────────────────────────────────────────────────────────
# 层 3: 变异测试 —— 关掉注入, 层1 的断言必须变红
# ──────────────────────────────────────────────────────────────────────
def layer_3_mutation() -> None:
    orig_tech = ca.technical_methodology_context
    orig_fund = ca.fundamental_methodology_context
    try:
        ca.technical_methodology_context = lambda symbol: ""
        ca.fundamental_methodology_context = lambda symbol: ""
        tech = _run_node(ca.create_commodity_technical_analyst)
        fund = _run_node(ca.create_commodity_fundamental_analyst)
        # 注入关掉后, 注入块必须消失 —— 这证明层1的断言对注入敏感, 不是永真
        _check("L3-变异: 关注入后技术面块消失", "【方法论读数 · 技术面】" not in tech)
        _check("L3-变异: 关注入后基本面块消失", "【方法论读数 · 基本面】" not in fund)
        _check("L3-变异: 关注入后正文仍在(证明注入非唯一内容)",
               "**Your Role**" in tech)
    finally:
        ca.technical_methodology_context = orig_tech
        ca.fundamental_methodology_context = orig_fund


def main() -> int:
    try:
        layer_1_and_2()
    except Exception:  # noqa: BLE001
        _check("L1/L2 执行", False, traceback.format_exc(limit=3))
    try:
        layer_3_mutation()
    except Exception:  # noqa: BLE001
        _check("L3 执行", False, traceback.format_exc(limit=3))

    bad = [r for r in _results if r[1] == FAIL]
    for name, st, detail in _results:
        print(f"[{st}] {name}" + (f"  ({detail})" if detail else ""))
    print("-" * 60)
    print(f"共 {len(_results)} 项, PASS {len(_results) - len(bad)}, FAIL {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
