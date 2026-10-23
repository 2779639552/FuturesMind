"""研究内容检查（2026-10-22）——纯规则，零 LLM 成本。

【为什么需要】
项目现有的 3200+ 条测试全是**格式/结构**护栏：字段契约、模板锚点、块是否注入。
没有一条检查**研究内容**本身。2026-10-22 体检测「引用率」，还得人工写探针 + 真跑一次
LLM 才看得出问题 —— 这种问题无法进 CI。

【和「系统检查」的分工】
  · 系统检查：格式、字段、工具、安全 —— 现有测试已经覆盖；
  · 研究内容检查：**关键变量是否遗漏、结论是否有证据、口径是否注明、缺口是否被顶替** —— 本文件。
参考弘则 ETAi 的公开方法论（「系统检查关注格式工具，研究内容需要另外评估」）。

【为什么先做纯规则、不做 LLM 复核】
  1. 零成本、零延迟：每次分析多一次 LLM 调用 = +15~30s，而分析本身已经 ~280s；
  2. 可进 CI：规则是纯函数，喂文本就能断言，不依赖模型随机性；
  3. 误报可控：六条规则都只抓「客观缺失」，不评判「观点对错」。
  需要主观判断的（方法是否合理、结论是否成立）留给后续 LLM 复核层，且只对规则命中的项做二次判断。

【六条规则（对应实测踩过的坑）】
  R1 缺口顶替：四源全挂（无数据卡）却给出了方向 —— 「不知道」被伪装成「没有倾向」
  R2 关键变量缺失：本品种可用的核心读数在报告里一个都没提
  R3 口径未注明：出现「基差率 X%」却没说分母是现货还是期货（2026-10-22 实测踩过）
  R4 证据悬空：方向句邻近没有任何数值/指标名 —— 空话
  R5 失效条件缺失：给了方向却没写什么情况下作废
  R6 周频滞后未标注：用「今天的库存」口吻描述十几天前的周频数据

【用法】
    from tradingagents.research_review import review
    res = review(synthesis_text, symbol="TA", cards=build_cards("TA"))
    if not res["pass"]:
        logger.warning("研究内容检查未通过: %s", res["issues"])
"""
from __future__ import annotations

import re
from typing import Any

# 方向词（用于 R1/R4/R5）
_DIRECTION_WORDS = re.compile(r"(偏多|偏空|看多|看空|做多|做空|买入|卖出|建议\s*做)")
# 中性立场（也要求给出"什么情况下转向"，但只算 medium —— 见 R5）
_STANCE_WORDS = re.compile(r"(中性|观望|暂不操作|不建议操作|维持中性)")
# 失效条件的表达（**必须与条件/数字同句**才算声明，见 _declares_invalidation）
_INVALIDATION = re.compile(r"(失效|作废|被推翻|反向条件|止损|以下情况.{0,6}改变|推翻)")
# 条件框架：出现它才说明这句话在**给触发条件**，而不是在描述现状
#   ⚠ 不含「转为/改为」—— 实测「现已被突破并转为支撑」是描述，不是条件（14 份里误放行 2 份）
_COND_MARK = re.compile(
    r"(若|如果|一旦|除非|只要|则|方可|失效条件|作废条件|反向条件|推翻条件)")
# 触发标记 + 数字 = 一种不含"失效"二字的合法写法（"跌破 6100 则转空"）
_TRIGGER_MARK = re.compile(
    r"(跌破|跌穿|突破|站上|失守|升破|下破|上穿|下穿|高于|低于|达到|超过|不足|不及)")
# 日期型数字不算"价位"：`2026-10-09` 里的 2026 不该让一句话被判成"有触发条件"
_DATE_LIKE = re.compile(r"\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?|\d{1,2}[-/月]\d{1,2}日?")
# 句子切分（R5 逐句判定）
_SENT_SPLIT = re.compile(r"[。！？；\n]")
# 周频/数据时点的表达
_STALENESS = re.compile(r"(周频|滞后|截至|数据日|截至\s*\d{2}-\d{2}|\d{4}-\d{2}-\d{2})")

# 指标名 → 报告里可能出现的别名（写"库存"就算提到了"社会库存"）
_ALIASES: dict[str, tuple[str, ...]] = {
    "社会库存": ("库存",),
    "期货库存": ("库存",),
    "交易所注册仓单": ("仓单",),
    "注册仓单": ("仓单",),
    "加工利润": ("利润",),
    "基差（绝对值）": ("基差",),
    "基差": ("基差",),
    "现货价": ("现货",),
    "期货价（参考合约）": ("期货",),
}

_SEVERITY_WEIGHT = {"high": 25, "medium": 12, "low": 5}


def _near(text: str, idx: int, span: int = 100) -> str:
    return text[max(0, idx - span): idx + span]


def _issue(code: str, severity: str, detail: str, evidence: str = "") -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail, "evidence": evidence.strip()[:160]}


def _mentioned(label: str, text: str) -> bool:
    """指标名（或其别名）是否在报告里出现过。"""
    if label in text:
        return True
    return any(a in text for a in _ALIASES.get(label, ()))


# ---------------------------------------------------------------------------
# 六条规则
# ---------------------------------------------------------------------------
def _r1_gap_masked(text: str, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """四源全挂却给了方向 —— 典型的「拿中性顶替缺失」。"""
    if cards:
        return []
    m = _DIRECTION_WORDS.search(text)
    if not m:
        return []
    hint = re.search(r"(无数据|取数失败|缺失|未纳入|缺口)", text)
    if hint:
        return []  # 已经说明了缺口，允许给建议
    return [_issue(
        "R1_gap_masked", "high",
        "四源读数全部缺失，报告仍给出了方向建议，且未说明缺口",
        _near(text, m.start()),
    )]


def _r2_missing_key_variable(text: str, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """本品种可用的核心读数，报告里一个都没提（允许漏一个）。"""
    labels = [c["label"] for c in cards if c.get("kind") in ("qty", "price_diff")]
    if len(labels) < 2:
        return []
    missing = [lb for lb in labels if not _mentioned(lb, text)]
    if len(missing) < 2:  # 漏 1 个容忍，漏 ≥2 个才算关键变量缺失
        return []
    return [_issue(
        "R2_missing_key_variable", "medium",
        f"关键读数未提及：{'、'.join(missing)}",
        "",
    )]


def _r3_caliber_unstated(text: str) -> list[dict[str, Any]]:
    """出现「基差率 X%」却没写分母是现货还是期货。

    2026-10-22 实测：注入块 7.89%（现货分母）与研报 8.56%（期货分母）是同一个基差的
    两种表达，报告引用时若不注明分母，读者无法判断它到底在说哪个数。
    """
    out = []
    for m in re.finditer(r"基差率?\s*(?:为|是|:|：)?\s*(\d+(?:\.\d+)?)\s*%", text):
        tail = text[m.end(): m.end() + 6]
        # 【误报模式】「基差率 84% 分位」= 基差率**处于** 84% 分位，84 是分位不是基差率值。
        #   实测（2026-10-22 17:17 那份报告）两条 R3 全是这个模式，必须排除。
        if tail.startswith("分位") or tail.startswith(" 分位"):
            continue
        win = _near(text, m.start(), 80)
        if re.search(r"(现货|期货|分母|spot|futures)", win):
            continue
        out.append(_issue(
            "R3_caliber_unstated", "high",
            f"基差率 {m.group(1)}% 未注明分母（现货分母与期货分母不可混比）",
            win,
        ))
    return out[:3]  # 同一缺陷不刷屏


# 主观判断的「依据」表达：有数字**或**有具体指标/比较词，都不算空话
_EVIDENCE = re.compile(
    r"(\d|基差|库存|仓单|利润|持仓|成交|分位|研报|社媒|情绪|背离|相反|高于|低于|环比|同比|价差|升水|贴水)"
)


def _r4_evidence_void(text: str) -> list[dict[str, Any]]:
    """方向句附近既无数值、也无任何具体指标/比较词 —— 只有观点。

    【为什么不用「有数字」一刀切】实测误报：情绪段的
      「机构研报净方向看空，而散户社媒看多，两群体方向相反——警惕反转」
      没有任何数字，但「研报/社媒/背离」已经是明确依据。用数字单一判据会把它误判成空话。
    """
    out = []
    for m in _DIRECTION_WORDS.finditer(text):
        win = _near(text, m.start(), 80)
        if _EVIDENCE.search(win):
            continue
        out.append(_issue(
            "R4_evidence_void", "medium",
            "方向性表述附近既无数值、也无具体指标依据",
            win,
        ))
    return out[:2]


def _has_number(s: str) -> bool:
    """句子里有没有"价位/幅度"型数字（日期不算）。"""
    return bool(re.search(r"\d", _DATE_LIKE.sub(" ", s)))


def _declares_invalidation(text: str) -> tuple[bool, str]:
    """判断是否**声明**了失效条件（而不是只提到"失效"二字）。

    【为什么必须逐句】2026-10-23 实测：报告里写了
        「双方**失效**价位均未触发」「不会根本**推翻**中性判断」
    这是叙述性提及，不是条件声明；而旧实现是全文任意位置命中就算有 ——
    于是一份没写"什么情况下本结论作废"的报告也能通过 R5。
    这与 10-22 那次把「84% 分位」当成基差率值是同一类错误：**只匹配词，不看语境**。

    【两种合法写法】
      ① 失效词 + （条件标记 或 数字）："若库存连续两周累库，本结论作废" / "失效条件：基差率跌破 3%"
      ② 触发标记 + 条件标记 + 数字（不含"失效"二字）："跌破 6,100 则转空"

    【判据（2026-10-23 第三版，前两版都被实测打回）】
      必须出现「条件框架」（若/如果/一旦/除非/只要/则/失效条件…），并且句中还有
      失效词或触发标记 —— 二者齐了才算声明。

      退化写法也认：失效词 + 数字（"止损位 6,100"），不带框架但确实在给位置。

    【两版是怎么被打回的】
      v1 只要「触发标记 + 数字」⇒「价格低于 MA20（5,207）约 171 点」这种**描述现状**
         的句子全被判成条件，14 份里 7 份误放行。
      v2 加了「条件框架」但仍保留「转为/改为」⇒「现已被突破并**转为**支撑」
         「日度减仓 6,390 手是短线多头**止损**」又漏了 2 份。
      ⇒ 教训同 10-22 的 R3：**匹配词不等于理解语境**，判据要窄、要有反例钉住。
    """
    for raw in _SENT_SPLIT.split(text):
        s = raw.strip()
        if not s:
            continue
        has_frame = bool(_COND_MARK.search(s))
        if has_frame and (_INVALIDATION.search(s) or _TRIGGER_MARK.search(s)):
            return True, s
        if _INVALIDATION.search(s) and _has_number(s):
            return True, s
    return False, ""


def _r5_no_invalidation(text: str) -> list[dict[str, Any]]:
    """给了立场却没写失效条件。

    【两档严重度】有明确方向（偏多/偏空/做多…）却没有失效条件 = high；
      只表达中性/观望 = medium —— 中性不是方向性建议，罚得轻一档更合理，
      否则"维持中性"也要被当成硬伤，会把告警变成噪音。
    """
    directional = bool(_DIRECTION_WORDS.search(text))
    stance = bool(_STANCE_WORDS.search(text))
    if not directional and not stance:
        return []
    if _declares_invalidation(text)[0]:
        return []
    return [_issue(
        "R5_no_invalidation", "high" if directional else "medium",
        "给出了方向性判断，但全文没有失效/作废条件"
        if directional else
        "给出了中性/观望立场，但没说什么情况下会转向（失效条件）",
        "",
    )]


def _node_keyword(node: str) -> str:
    """「聚酯（切片/瓶片/长丝）」→「聚酯」；「CBOT 大豆」→「CBOT」。"""
    k = re.split(r"[（(]", node)[0].strip()
    return k.split(" ")[0] if " " in k else k


def _r7_chain_gap(text: str, symbol: str) -> list[dict[str, Any]]:
    """报告没涉及产业链上「高风险且无数据」的关键环节。

    【为什么用「无数据」的环节做判据】本系统有数的环节，注入块已经摆在模型面前，
      漏提属偶发；而**没数据的环节**恰恰是最容易被整段跳过的地方 —— 石脑油、聚酯、
      CBOT 大豆这些成本/需求端核心，模型要么不提，要么提了就编数。
    """
    if not symbol:
        return []
    try:
        from tradingagents.dataflows import commodity_chain as C
    except Exception:  # noqa: BLE001
        return []
    if not C.chain_of(symbol):
        return []
    high = [g for g in C.gaps() if g["variety"] == symbol.upper() and g["impact"] == "high"]
    if len(high) < 2:  # 风险环节太少时不强制覆盖
        return []
    hit = [g for g in high if _node_keyword(g["node"]) in text]
    if hit:
        return []
    names = "、".join(g["node"] for g in high)
    return [_issue(
        "R7_chain_gap", "medium",
        f"报告未涉及产业链关键环节（且这些环节本系统无数据）：{names}",
        "",
    )]


def _r6_stale_not_flagged(text: str, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用当日口吻描述周频的滞后数据。"""
    stale = [c for c in cards
             if c.get("kind") == "qty" and (c.get("age_days") or 0) >= 3]
    if not stale:
        return []
    if "库存" not in text and "仓单" not in text:
        return []  # 报告压根没提这些维度，不构成"用当日口吻描述"
    if _STALENESS.search(text):
        return []
    worst = max(stale, key=lambda c: c.get("age_days") or 0)
    return [_issue(
        "R6_stale_not_flagged", "medium",
        f"{worst['label']}实际截至 {worst['as_of']}（滞后 {worst.get('age_days')} 天），报告未注明数据时点",
        "",
    )]


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------
def review(
    text: str,
    *,
    symbol: str = "",
    cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对一份报告做研究内容检查。

    【参数】
      text   ：要检查的报告正文（通常是 synthesis，或多份报告拼接）
      symbol ：品种代码（仅用于返回）
      cards  ：`analysis_bundle.build_cards(symbol)` 的结果；缺失时 R1/R2/R6 会退化
    【返回】
      {ok, pass, score, symbol, issues[], checked_chars}
      `pass=False` 不等于报告不能用，而是「有 X 处需要复核」。
    """
    cards = cards or []
    if not text or not text.strip():
        return {
            "ok": False, "pass": False, "score": 0, "symbol": symbol,
            "issues": [_issue("R0_empty", "high", "报告正文为空", "")],
            "checked_chars": 0,
        }

    issues: list[dict[str, Any]] = []
    for fn in (
        lambda: _r1_gap_masked(text, cards),
        lambda: _r2_missing_key_variable(text, cards),
        lambda: _r3_caliber_unstated(text),
        lambda: _r4_evidence_void(text),
        lambda: _r5_no_invalidation(text),
        lambda: _r6_stale_not_flagged(text, cards),
        lambda: _r7_chain_gap(text, symbol),
    ):
        try:
            issues.extend(fn())
        except Exception:  # noqa: BLE001 - 单条规则失败不能拖垮整份检查
            continue

    penalty = sum(_SEVERITY_WEIGHT.get(i["severity"], 5) for i in issues)
    score = max(0, 100 - penalty)
    return {
        "ok": True,
        "pass": not any(i["severity"] == "high" for i in issues),
        "score": score,
        "symbol": symbol,
        "issues": issues,
        "checked_chars": len(text),
    }


def summarize(result: dict[str, Any]) -> str:
    """一行摘要（日志/前端用）。"""
    if not result.get("ok"):
        return "研究内容检查未执行"
    issues = result.get("issues") or []
    if not issues:
        return f"研究内容检查通过（{result['score']} 分，无问题）"
    codes = "、".join(f"{i['code']}({i['severity']})" for i in issues)
    return f"研究内容检查 {result['score']} 分，{len(issues)} 个问题：{codes}"


# ---------------------------------------------------------------------------
# 闭环：把检查结果变成下游可执行的东西
# ---------------------------------------------------------------------------
# 【为什么需要这一段】2026-10-23 之前 `review()` 只被 `logger.warning` 消费 ——
#   检查出来的问题只进日志，不影响评级、不要求改写、前端也看不到（实测
#   `web_template.html` 里搜 `research_review` 零命中）。那不叫"检查节点"，叫"自检日志"。
#   现在分两处真正接上：
#     · 事前（synthesis 之前）→ `gate_block()` 作为硬约束注入综合研判提示词；
#     · 事后（synthesis 之后）→ `acceptance_section()` 落进报告正文，读者可见。
_FIX_HINT: dict[str, str] = {
    "R0_empty": "正文不能为空",
    "R1_gap_masked": "取数失败必须写明「未取到」，不许用「影响有限/整体中性」顶替",
    "R2_missing_key_variable": "若漏提的读数确实不影响结论，须在正文说明理由，不得静默略过",
    "R3_caliber_unstated": "基差率必须注明分母（现货/期货），两者不可混比",
    "R4_evidence_void": "方向性表述必须挂一个具体数值或指标，不能只有观点",
    "R5_no_invalidation": "必须给出失效条件（什么价格或数据出现则本结论作废）",
    "R6_stale_not_flagged": "引用周频/滞后数据必须写明数据时点与滞后天数",
    "R7_chain_gap": "本品种产业链关键环节未被提及，须提及或说明为何不影响结论",
}

# 【预算】方法论块已经 1804 字（预算 800），这一块必须短。
#   实测 4 条 issue + 3 条硬性要求约 420 字，留 700 的余量兜底。
_GATE_MAX_ISSUES = 4
_GATE_MAX_CHARS = 700


def gate_block(result: dict[str, Any] | None) -> str:
    """把检查结果渲染成**给下游模型的硬约束块**；通过或没结果时返回 ""。

    【只输出 high/medium】low 级（如 R4 偶发误报）不配占用提示词预算。
    【作用点】`synthesis_node` 把它插在「Your Task」之前 —— 那是模型还在
      决定"怎么判"的位置，不是已经写完的位置。
    """
    if not result or not result.get("ok") or result.get("pass"):
        return ""
    issues = [i for i in (result.get("issues") or [])
              if i.get("severity") in ("high", "medium")]
    if not issues:
        return ""
    order = {"high": 0, "medium": 1}
    issues.sort(key=lambda i: order.get(i.get("severity"), 2))

    lines = [
        "⚠️ 质量门：研究内容检查未通过（%d 分）。给出评级之前必须先处理下列问题："
        % result.get("score", 0)
    ]
    for n, it in enumerate(issues[:_GATE_MAX_ISSUES], 1):
        detail = (it.get("detail") or "").strip()[:60]
        hint = _FIX_HINT.get(it.get("code", ""), "")
        lvl = "HIGH" if it.get("severity") == "high" else "MED"
        lines.append("%d. [%s] %s%s" % (n, lvl, detail, (" —— " + hint) if hint else ""))
    lines += [
        "硬性要求：",
        "① 每条要么在正文补齐，要么在「本次局限」段说明为何无法补，不许静默略过；",
        "② HIGH 级一条都不许忽略；",
        "③ 数据缺口写「未取到」，不许用「影响有限 / 整体中性」顶替。",
    ]
    text = "\n".join(lines)
    if len(text) > _GATE_MAX_CHARS:
        text = text[:_GATE_MAX_CHARS].rstrip() + "\n…（其余问题见检查明细）"
    return text


def apply_acceptance(
    reports: dict[str, Any], result: dict[str, Any] | None
) -> tuple[dict[str, Any], str]:
    """把验收小节拼进 synthesis 报告（返回新 dict，不改原对象）。

    【为什么单独抽出来】这段本来内联在 Flask 路由里，测不到；
      抽成纯函数后可以直接断言"未通过时报告末尾必须出现质量门小节"。
    """
    out = dict(reports or {})
    sec = acceptance_section(result)
    if sec and out.get("synthesis"):
        out["synthesis"] = str(out["synthesis"]).rstrip() + "\n" + sec
    return out, sec


def acceptance_section(result: dict[str, Any] | None) -> str:
    """事后验收：把**仍然存在**的问题渲染成报告末尾的可见小节；通过时返回 ""。

    【为什么不改 RATING 头】评级是模型的领域判断，按条数机械降档会制造
      "看起来更保守、实际没有依据"的假象。检查器能确定的只有「这些点没交代清楚」，
      所以它只补一段说明，把判断权交回给读报告的人。
    """
    if not result or not result.get("ok") or result.get("pass"):
        return ""
    issues = result.get("issues") or []
    if not issues:
        return ""
    order = {"high": 0, "medium": 1, "low": 2}
    issues = sorted(issues, key=lambda i: order.get(i.get("severity"), 3))
    lines = [
        "",
        "## ⚠️ 质量门：未通过（%d 分）" % result.get("score", 0),
        "",
        "以下问题在综合研判后仍然存在，**请复核后再使用本结论**：",
        "",
    ]
    for it in issues:
        lvl = {"high": "HIGH", "medium": "MED", "low": "LOW"}.get(it.get("severity"), "")
        detail = (it.get("detail") or "").strip()
        lines.append("- **[%s]** %s" % (lvl, detail))
    lines += ["", "> 本节由规则检查自动生成（`tradingagents/research_review.py`），不参与评级计算。"]
    return "\n".join(lines)
