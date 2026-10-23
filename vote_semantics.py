# -*- coding: utf-8 -*-
"""方向票语义的**唯一权威来源**。

【为什么必须单独成模块】
  `dirmap = {做多, 单边做多, 做空, 单边做空}` 曾在 4 个生产文件各抄一份：
    quant_verdict.py:290 / fund_module.py:994 / fund_module.py:1272 / quant_methodology.py:1558
  外加 1 份前端映射（web_template.html `_FRK_TEXT_RANK`）与 1 份测试抄写
  （tests/test_fund_xsection.py:593）。抄一份表 = 给同一个 bug 复制 5 个入口。

【实测后果（2026-10-19）】
  期限结构票返回的是 `偏多/偏空`，不在上面那张表里 ⇒ 被 `dict.get(x, 0)` 静默吞成 0。
  实跑 /api/quant/verdict?variety=TA：9 张票只有 3 张进净票，4 张是哑票（44%）。
  而前端 `_FRK_TEXT_RANK` 早期就把 `偏多` 当 +1 —— 前后端口径从一开始就不一致，
  前端注释还写着"与 skill_net 同一映射"。本模块的作用是**让这句话变成真的**。

【三条纪律】
  1. 取值必须**穷举并显式**分类：要么给方向符号，要么明确标注"非方向票"。
     **禁止 `table.get(x, 0)` 式兜底** —— 那正是哑票的成因。
  2. 认不出的取值必须**外显**（进 unclassified + 告警），不得静默当 0。
  3. 符号只表达方向（±1/0），**不表达强度**；强度留在各票的 detail 里。
     票与票之间也不做加权——加权是聚合层的事（见 P1 的按轴归并）。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── 分类种类 ──────────────────────────────────────────────────────────
LONG = "long"                  # 明确看多，计 +1
SHORT = "short"                # 明确看空，计 -1
NEUTRAL = "neutral"            # 明确无方向（暂不操作/打平），计 0，但**是有效表态**
NON_DIRECTION = "non_direction"  # 本来就不是方向票（regime 过滤/预警/套利形态），计 0
WEAK_DIRECTION = "weak_direction"  # 表述里含方向暗示但不达方向票标准，计 0 且登记
UNKNOWN = "unknown"            # 认不出 → 必须外显

# ── 显式分类表（新增 bias 取值时必须在此登记，否则会被记为 unknown）──
_LONG = frozenset({"做多", "单边做多", "偏多"})
_SHORT = frozenset({"做空", "单边做空", "偏空"})
_NEUTRAL = frozenset({
    "中性", "暂不操作", "阶段不明", "未共振", "平水", "无方向", "不适用",
})
# 这些字符串**不是方向**，但历史上都被 `dict.get(...,0)` 混在"计 0"里，
# 与"识别失败"无法区分。分开登记后，审计能一眼看出"这张票本来就不该计票"。
_NON_DIRECTION = frozenset({
    # 墨菲 ADXR：只判"该不该用趋势系统"，不判多空
    "趋势系统可用", "趋势系统不可用(无趋势)", "趋势系统不可用",
    # 墨菲背离 / 突破过滤：预警与口径对照
    "反转预警", "提示", "提示(突破口径对照)",
    # 二维四象在指标矛盾时给的是**策略形态**，不是方向（"勿做单边"）
    "跨期正套", "跨期反套",
    # 三指标缺数据时的降级态
    "降级",
})

# 动态拼接的取值（无法逐个登记，只能按前缀归类）。
# 【怎么发现这两个前缀的】L0 上线前跑 21 品种漂移对比，审计报出 10 次未登记，
#   全部是这里的两类 —— 也就是说它们此前同样一直在静默计 0。
#   ★ 这正是"把未登记取值外显"的价值：靠人工读代码是发现不了的。
_WEAK_PREFIX = ("部分共振",)            # 如「部分共振(2/3 偏多)」：有方向暗示但不达方向票
_NON_DIRECTION_PREFIX = ("套利/对冲",)   # 如「套利/对冲(适合正套)」：明确要求**不做单边**


def classify(bias: Any) -> tuple[int, str]:
    """bias 文本 → (方向符号, 分类)。

    Args:
        bias: 票的方向文本，如 "做多" / "偏多" / "暂不操作" / "提示(突破口径对照)"。

    Returns:
        (sign, kind)：sign ∈ {-1, 0, 1}；kind ∈ 上面 6 个常量之一。

    【为什么不返回 bool】调用方需要区分"有效中性"与"识别失败"——前者是结论，
      后者是 bug。把两者都映射成 0 却不加区分，就是本项目 10-19 那次 44% 哑票的根因。
    """
    s = "" if bias is None else str(bias).strip()
    if s in _LONG:
        return 1, LONG
    if s in _SHORT:
        return -1, SHORT
    if s in _NEUTRAL:
        return 0, NEUTRAL
    if s in _NON_DIRECTION:
        return 0, NON_DIRECTION
    if any(s.startswith(p) for p in _NON_DIRECTION_PREFIX):
        return 0, NON_DIRECTION
    if any(s.startswith(p) for p in _WEAK_PREFIX):
        return 0, WEAK_DIRECTION
    return 0, UNKNOWN


def sign(bias: Any) -> int:
    """只要符号的便捷入口（供"找多头票/空头票"这类筛选用）。"""
    return classify(bias)[0]


def net_of(votes: Iterable[dict], key: str = "bias") -> tuple[int, dict]:
    """方向净票 + 计入审计。

    Args:
        votes: 票列表，每项至少含 {book, bias}。
        key: 方向字段名（默认 "bias"）。

    Returns:
        (net, audit)。audit 结构见下方，可直接挂进 API 响应给前端/日志用。

    【审计为什么要带上 unclassified】净票是个标量，一旦某张票被漏计，从标量上看
      不出来（只是数值偏小）。必须把"谁没被认出来"一起带出去，才可能在真实请求里
      发现"新加了一张票但忘了登记"。
    """
    audit: dict[str, Any] = {
        "counted": 0, "long": [], "short": [],
        "neutral": [], "non_direction": [], "weak_direction": [], "unclassified": [],
    }
    net = 0
    for v in votes or ():
        if not isinstance(v, dict):
            continue
        book = str(v.get("book") or "?")
        raw = v.get(key)
        sg, kind = classify(raw)
        net += sg
        if kind == UNKNOWN:
            audit["unclassified"].append({"book": book, "bias": str(raw)})
            logger.warning("[vote] 未登记的方向取值: book=%s bias=%r —— 已按 0 处理，"
                           "请在 vote_semantics 显式登记", book, raw)
            continue
        if kind == LONG:
            audit["long"].append(book)
        elif kind == SHORT:
            audit["short"].append(book)
        elif kind == NEUTRAL:
            audit["neutral"].append(book)
        elif kind == NON_DIRECTION:
            audit["non_direction"].append(book)
        elif kind == WEAK_DIRECTION:
            audit["weak_direction"].append(book)
    audit["counted"] = len(audit["long"]) + len(audit["short"])
    audit["net"] = net
    return net, audit
