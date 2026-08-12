"""
User Feedback Agent for TradingAgents Commodity Futures.

Provides an interactive post-analysis debate node that:
1. Presents the analysis conclusions to the user
2. Collects structured feedback via terminal input
3. Runs an LLM-powered debate loop — the agent defends its analysis with data
   and logic, acknowledges valid user points, but also pushes back against
   unsupported claims
4. Evaluates user feedback objectively: accepts what is data-backed, rejects
   what contradicts evidence, and records disputed claims for future review
5. Persists a balanced debate record to Evolution Memory

DESIGN PRINCIPLE: The agent is a NEUTRAL investment assistant, not a yes-man.
It treats the user as a knowledgeable peer whose opinions must be tested
against data — not as an authority whose word is automatically accepted.
"""

import logging  # 【调用包】日志:记录 LLM 调用失败与进化记忆保存异常
import sys  # 【调用包】sys.stdout.isatty():判断是否处于交互式终端
from collections.abc import Callable  # 【调用包】回调类型:声明返回的 LangGraph 节点函数类型
from typing import Any  # 【调用包】类型注解:状态字典等动态类型

from langchain_core.messages import HumanMessage  # 【调用包】LangChain 消息:把辩论总结封装为 HumanMessage 注入图状态

from tradingagents.dataflows.evolution_memory import (  # 【调用包】进化记忆模块:构建/读取/保存辩论记录并更新用户画像
    build_debate_record,
    get_evolution_context,
    save_debate,
    update_profile_from_debate,
)

logger = logging.getLogger(__name__)  # 【变量】模块级日志器

MAX_DEBATE_ROUNDS = 5  # safety cap on back-and-forth exchanges【变量】辩论往返回合数上限(安全阀,防止无限往返)

# ---------------------------------------------------------------------------
# Safe print helper (handles Windows GBK terminals)
# ---------------------------------------------------------------------------


# 【功能】安全打印终端文本:编码报错时回退为 ASCII 替换字符,超长时截断并提示。
# 【参数】text: 要打印的文本;max_chars: 单次打印最大字符数,默认 3000。
# 【返回】无;直接副作用打印。
# 【关键】兼容 Windows GBK 终端,避免中文打印触发 UnicodeEncodeError。
def _safe_print(text: str, max_chars: int = 3000) -> None:
    """Print text safely, falling back to ASCII on encoding errors."""
    if not text:
        return
    content = text[:max_chars]  # 【变量】截断后的内容,防止终端输出过长
    try:
        print(content)
    except (UnicodeEncodeError, UnicodeDecodeError):
        safe = content.encode("ascii", errors="replace").decode("ascii")  # 【变量】GBK 终端下的 ASCII 兜底文本(非法字符替换为 ?)
        print(safe)
    if len(text) > max_chars:
        print(f"\n... (truncated, {len(text):,} chars total)")


# 【功能】安全读取一行终端输入;EOF/中断时返回空串而非抛异常。
# 【参数】prompt: 提示文字。
# 【返回】用户输入行(去换行),异常时返回空串。
def _safe_input(prompt: str = "") -> str:
    """Read a line from stdin safely."""
    try:
        return input(prompt)  # 【调用函数】读取用户键盘输入
    except (EOFError, KeyboardInterrupt):
        return ""


# ---------------------------------------------------------------------------
# Debate prompt templates
#
# DESIGN PHILOSOPHY (Neutral Investment Assistant):
#   1. The agent collected REAL DATA — it must defend conclusions grounded in
#      that data, not automatically defer to the user's opinion.
#   2. The user may have timely information (e.g., breaking policy news) that
#      the free API missed — this is valuable and should be acknowledged.
#   3. The user may also hold incorrect beliefs, over-react to anecdotes, or
#      have confirmation bias — the agent must challenge these with evidence.
#   4. When uncertain, the agent should ask clarifying questions rather than
#      assuming the user is right or wrong.
#   5. The goal is not "make the user happy" but "arrive at the most accurate
#      analysis possible given all available information."
# ---------------------------------------------------------------------------

DEBATE_SYSTEM_PROMPT = """You are a commodity futures analyst. You have just completed a data-driven analysis of **{symbol}** using real market data (prices, indicators, basis, inventory, news, macro).

**Your Analysis Conclusion**:
- Direction: {direction}
- Weight Allocation: {weights}
- Summary: {summary}

**Your Role in This Discussion**:

You are engaging with a knowledgeable user who has reviewed your analysis. Treat them as a peer — their perspective may add value, but it may also be incorrect or biased. Your job is NOT to agree with everything they say. Your job is to arrive at the most ACCURATE assessment possible.

CRITICAL RULES:

1. **Ground yourself in YOUR data**:
   - You collected specific numbers (prices, inventory levels, basis values, PMI figures, etc.). When the user makes a claim, check it against the data you gathered.
   - If the user says "inventory is surging" but your data shows inventory declining — POLITELY CORRECT them with the actual number.
   - If the user mentions a factor you didn't consider, ask yourself: would this factor actually change my conclusion given the data I have?

2. **Distinguish between facts, interpretations, and opinions**:
   - FACT (user): "July 15 customs data showed steel exports dropped 12% MoM" → This is verifiable. Acknowledge it. Ask if they can share the source.
   - INTERPRETATION (user): "The export drop means supply pressure will crush prices" → This is debatable. Challenge it if your data shows domestic demand absorbing the supply.
   - OPINION (user): "I feel the market is going to crash" → Ask for their reasoning. Don't accept sentiment as analysis.

3. **Do NOT be a pushover**:
   - If the user's claim contradicts your data, say: "I understand your concern, but the data I collected shows [X]. Could you help me reconcile this discrepancy?"
   - If the user makes an unsupported assertion, ask: "What data or events lead you to that conclusion?"
   - If the user is clearly wrong about a factual matter, correct them politely but firmly with the evidence.

4. **DO be open to being wrong**:
   - If the user provides a SPECIFIC data point or logical chain that genuinely undermines your analysis, acknowledge it: "That's a valid point that I didn't fully consider. Here's how it would affect my assessment..."
   - If the user identifies a blind spot in your methodology, admit it: "You're right that I didn't account for [X]. That would change the weight of [Y]."

5. **When uncertain, investigate rather than concede**:
   - Say: "That's an interesting angle. Can you elaborate on [specific aspect] so I can better evaluate how it fits with the data I have?"
   - Don't say: "You're right, I was wrong" unless you are GENUINELY convinced.

{evolution_note}

The user has shared their feedback on your analysis. Respond following the rules above. Reply in Chinese (中文). Keep your response focused and substantive (2-4 paragraphs)."""  # 【变量】辩论首轮系统提示词模板:以数据为纲、对用户主张分类评估,占位符 {symbol}/{direction}/{weights}/{summary}/{evolution_note}

CONTINUATION_PROMPT = """Previous discussion:
{conversation_history}

The user says:
{user_message}

Continue the discussion. Follow these guidelines:

- If the user raised a VALID point backed by concrete data/logic → acknowledge it and explain how it adjusts your assessment.
- If the user made a CLAIM that contradicts your collected data → challenge it politely with the specific data point.
- If the user expressed a vague OPINION without evidence → ask them to elaborate with data or specific reasoning.
- If the user appears to be conceding or agreeing with your counter-arguments → acknowledge the mutual understanding.
- If you genuinely changed your mind on something → state clearly what changed and why.
- If you maintain your position → state why, citing your data.

Do NOT just say "you have a good point" as a platitude. Every acknowledgment must be tied to a specific reason.

Reply in Chinese (中文). Keep it substantive."""  # 【变量】辩论后续回合续接提示词模板:占位符 {conversation_history}/{user_message}

SUMMARY_PROMPT = """The user feedback debate has ended. Based on the full conversation below, produce a BALANCED and OBJECTIVE summary.

Conversation:
{conversation_history}

Output the following sections in Chinese:

## 辩论总结

### 用户核心观点
[2-3 sentences: what the user argued, what data/reasoning they provided]

### 系统回应与立场
[2-3 sentences: how the system responded, whether it maintained or changed its position, and why]

### 观点评估 (CRITICAL — be honest)
对于用户的每个主要论点，分类评估：

**采纳的观点** (系统认可，将纳入未来分析):
- [Specific claim] — 采纳原因：[data-based reason]
- (若无，写"无")

**部分采纳的观点** (有道理但需调整):
- [Specific claim] — 调整方式：[how to incorporate]

**未采纳的观点** (系统持有异议):
- [Specific claim] — 未采纳原因：[data/logic-based reason]
- (若无，写"无")

**待验证的观点** (需要更多数据才能判断):
- [Specific claim] — 需要什么数据来验证

### 经验教训
- [What the SYSTEM can improve — specific, actionable]
- [What data blind spots were exposed]
- (If the user was wrong about something, note what the SYSTEM should watch for in similar future claims)

### 下次分析调整建议
- [Concrete adjustment — only for ACCEPTED or PARTIALLY ACCEPTED points]
- [Factor weight changes — only if justified by the debate]

IMPORTANT: Do NOT automatically conclude the user was right. Evaluate each claim independently against the data. If the user made unsupported claims, say so. If the system maintained its position, say so. Be objective.

Keep the entire summary under 500 words."""  # 【变量】辩论总结提示词模板:要求客观评估每个论点(采纳/部分采纳/未采纳/待验证),占位符 {conversation_history}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


# 【功能】创建"用户反馈辩论节点"的工厂函数:返回一个可被 LangGraph StateGraph 调用的节点。
# 【参数】llm: 驱动辩论与总结的 LLM 客户端;max_rounds: 辩论往返回合上限(默认 5);
#        enabled: 为 False 时节点为空操作(--no-feedback 模式)。
# 【返回】LangGraph 兼容的节点函数。
def create_user_feedback_node(
    llm,
    max_rounds: int = MAX_DEBATE_ROUNDS,
    enabled: bool = True,
) -> Callable:
    """Create a LangGraph node for interactive user feedback collection.

    Args:
        llm: The LLM client used for debate responses.
        max_rounds: Maximum number of debate back-and-forth rounds.
        enabled: If False, the node is a no-op (for --no-feedback mode).

    Returns:
        A node function compatible with LangGraph's StateGraph.
    """

    # 【功能】交互式用户反馈节点本体:展示结论→结构化提问→LLM 辩论→总结并写入进化记忆,返回更新后的状态。
    # 【参数】state: 图状态字典(含标的/日期/分析师结论)。
    # 【返回】{"user_feedback_summary": 辩论总结, "messages": [HumanMessage(...)]};非交互模式返回空摘要。
    # 【关键】只在交互终端运行;跳过(非交互)时返回空摘要不阻塞流程。
    def node(state: dict[str, Any]) -> dict[str, Any]:
        """Interactive user feedback node.

        Reads analysis results from state, engages the user in debate,
        persists the results to Evolution Memory, and returns an updated
        state dict.
        """
        if not enabled:
            return {"user_feedback_summary": ""}

        symbol = state.get("company_of_interest", "?")  # 【变量】当前分析品种(状态中取,缺省 '?')
        trade_date = state.get("trade_date", "?")  # 【变量】当前交易日期(缺省 '?')
        synthesis = state.get("investment_plan", "") or state.get("final_trade_decision", "")  # 【变量】分析师结论文本(优先投资计划,其次最终交易决定)

        # Check if we are in an interactive terminal
        if not sys.stdout.isatty():  # 【调用函数】检测标准输出是否连接交互终端;非交互(如日志脚本/CI)时跳过反馈
            logger.info("Non-interactive mode, skipping user feedback.")
            return {"user_feedback_summary": ""}

        # ---- Phase 1: Display conclusions ----
        _print_separator("=")
        _safe_print("")
        _safe_print("  Analysis Complete -- Please Review the Conclusions")
        _safe_print("")
        _print_separator("=")
        _safe_print("")

        # Extract key info from synthesis for display
        direction = _extract_direction(synthesis)  # 【变量】从结论文本中提取的方向判断(看多/看空/震荡)
        _safe_print(f"  Direction: {direction}")
        _safe_print(f"  Variety:   {symbol}")
        _safe_print(f"  Date:      {trade_date}")
        _safe_print("")
        _safe_print("  Core Logic (first 500 chars):")
        _safe_print(f"  {synthesis[:500]}")
        _safe_print("")

        # ---- Phase 2: Structured questions ----
        _print_separator("-")
        _safe_print("  Feedback Phase -- Structured Questions")
        _print_separator("-")
        _safe_print("")

        # Q1: Direction agreement
        _safe_print("Q1: Do you agree with the direction judgment?")
        _safe_print("    [1] Fully agree  [2] Agree but more conservative")
        _safe_print("    [3] Agree but more aggressive  [4] Partially disagree")
        _safe_print("    [5] Completely disagree  [/skip] Skip all feedback")
        q1 = _safe_input("> ").strip()

        if q1.lower() in ("/skip", "skip", "s"):
            _safe_print("")
            _safe_print("[Feedback] Skipped. Analysis complete.")
            return {"user_feedback_summary": ""}

        direction_map = {  # 【变量】Q1 数字选项→英文短语映射(未命中时保留用户原文)
            "1": "Fully agree",
            "2": "Agree but more conservative (agree but more conservative)",
            "3": "Agree but more aggressive (agree but more aggressive)",
            "4": "Partially disagree (partially disagree)",
            "5": "Completely disagree (completely disagree)",
        }
        user_direction = direction_map.get(q1, q1)  # 【变量】用户对方向判断的立场(映射后的文本)

        # Q2: Under/over-weighted factors
        _safe_print("")
        _safe_print("Q2: Which factors were under-weighted or over-weighted?")
        _safe_print("    (Type your answer, multi-line OK. Empty line to finish.)")
        q2_lines = []
        while True:
            line = _safe_input("> ")
            if not line.strip():
                break
            q2_lines.append(line)
        user_factors = "\n".join(q2_lines)

        # Q3: Missing information
        _safe_print("")
        _safe_print("Q3: Any key information we missed?")
        _safe_print("    (Type your answer, empty line to finish.)")
        q3_lines = []
        while True:
            line = _safe_input("> ")
            if not line.strip():
                break
            q3_lines.append(line)
        user_missing = "\n".join(q3_lines)

        # ---- Phase 3: Debate mode ----
        _print_separator("-")
        _safe_print("  Debate Phase -- Discuss with the AI")
        _safe_print("  (Type /done to end, /skip to skip)")
        _print_separator("-")
        _safe_print("")

        debate_rounds: list[dict[str, str]] = []  # 【变量】辩论回合记录:[{"speaker": "agent"/"user", "content": ...}, ...]
        conversation_history: list[str] = []  # 【变量】可读对话历史行("AI: ..." / "User: ..."),用于续接与总结提示词

        # Build user opinion from Q1-Q3
        user_opinion_parts = [f"Direction view: {user_direction}"]
        if user_factors:
            user_opinion_parts.append(f"Factor concerns: {user_factors}")
        if user_missing:
            user_opinion_parts.append(f"Missing info: {user_missing}")
        user_opinion = "; ".join(user_opinion_parts)  # 【变量】把 Q1-Q3 拼成一段用户观点文本,供 LLM 辩论

        # Load evolution context for richer debate
        evolution_ctx = get_evolution_context(symbol)  # 【调用函数】读取进化记忆:该品种过往辩论沉淀的用户偏好上下文
        evolution_note = ""  # 【变量】注入系统提示词的"历史用户偏好"段落;无历史则为空
        if evolution_ctx:
            evolution_note = (
                f"\n**Historical User Preferences (from past debates):**\n{evolution_ctx[:1500]}\n"
            )

        # Initial debate prompt
        weights_str = _extract_weights(synthesis)  # 【变量】从结论文本提取的四维度权重分配(如 技术面=4/10)
        debate_prompt = DEBATE_SYSTEM_PROMPT.format(
            symbol=symbol,
            direction=direction,
            weights=weights_str,
            summary=synthesis[:1500],
            evolution_note=evolution_note,
        )
        debate_full = debate_prompt + f"\n\nUser feedback: {user_opinion}"  # 【变量】首轮完整提示词:系统提示 + 用户观点

        try:
            response = llm.invoke(debate_full)  # 【调用函数】LLM 调用:生成辩论首轮回应
            agent_msg = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error("Debate LLM call failed: %s", e)
            agent_msg = (
                "Sorry, I couldn't process your feedback due to a technical issue. "
                "Your feedback has been recorded and will be used for future analyses."
            )

        _safe_print(f"[AI]: {agent_msg}")
        _safe_print("")
        debate_rounds.append({"speaker": "agent", "content": agent_msg})
        conversation_history.append(f"AI: {agent_msg}")

        # Debate loop
        for round_num in range(1, max_rounds + 1):
            user_msg = _safe_input(f"[Round {round_num}/{max_rounds}] Your response: ").strip()

            if not user_msg:
                continue

            if user_msg.lower() in ("/done", "/exit", "/end", "done", "quit", "q"):
                _safe_print("[Debate] Ending by user request.")
                break

            if user_msg.lower() in ("/skip",):
                _safe_print("[Debate] Skipped.")
                break

            debate_rounds.append({"speaker": "user", "content": user_msg})
            conversation_history.append(f"User: {user_msg}")

            # Build continuation prompt
            cont_prompt = CONTINUATION_PROMPT.format(  # 【变量】续接提示词:截取最近 10 条历史 + 当前用户消息
                conversation_history="\n".join(conversation_history[-10:]),
                user_message=user_msg,
            )

            try:
                response = llm.invoke(cont_prompt)  # 【调用函数】LLM 调用:生成辩论续接回应
                agent_msg = response.content if hasattr(response, "content") else str(response)
            except Exception as e:
                logger.error("Debate continuation LLM call failed: %s", e)
                agent_msg = "I see. Thank you for sharing your perspective."

            _safe_print("")
            _safe_print(f"[AI]: {agent_msg}")
            _safe_print("")
            debate_rounds.append({"speaker": "agent", "content": agent_msg})
            conversation_history.append(f"AI: {agent_msg}")

            # Auto-detect end: short user acknowledgment
            if len(user_msg) <= 10 and any(  # 【变量】自动结束启发式:用户短回复且含致谢/认可词(ok/good/thanks 等)即认为满意
                w in user_msg
                for w in ("ok", "good", "thanks", "xiexie", "hao", "keyi", "OK", "Good")
            ):
                _safe_print("[Debate] User appears satisfied, concluding.")
                break

        # ---- Phase 4: Summarize and save ----
        _print_separator("-")
        _safe_print("  Summarizing debate and saving to Evolution Memory...")
        _print_separator("-")
        _safe_print("")

        # Generate summary via LLM
        summary_prompt = SUMMARY_PROMPT.format(  # 【变量】总结提示词:要求 LLM 客观评估每个用户论点并输出中文总结
            conversation_history="\n".join(conversation_history),
        )
        try:
            response = llm.invoke(summary_prompt)  # 【调用函数】LLM 调用:生成辩论总结(客观评估采纳/未采纳观点)
            debate_summary = response.content if hasattr(response, "content") else str(response)  # 【变量】辩论总结文本(展示给用户并存入状态)
        except Exception as e:
            logger.error("Debate summary LLM call failed: %s", e)
            debate_summary = (
                "## Debate Summary\n\n"
                f"User feedback received on {symbol}. "
                "Key points have been recorded for future analysis calibration."
            )

        _safe_print(debate_summary)
        _safe_print("")

        # Extract lessons (simple heuristic: look for bullet points in summary)
        lessons = _extract_lessons(debate_summary)  # 【变量】从总结中启发式提取的经验教训列表(最多 5 条)

        # Build and save debate record
        agent_conclusion = {  # 【变量】系统自己的结论摘要(方向/权重/核心逻辑),存入辩论记录用于比对
            "direction": direction,
            "weights": weights_str,
            "summary": synthesis[:500],
        }

        debate_record = build_debate_record(  # 【调用函数】跨模块构建结构化辩论记录(含用户观点/回合/结论/解决类型)
            variety=symbol,
            trade_date=trade_date,
            agent_conclusion=agent_conclusion,
            user_direction=user_direction,
            user_factors_text=user_factors,
            user_missing_text=user_missing,
            debate_rounds=debate_rounds,
            resolution=_determine_resolution(debate_rounds, user_direction),
            lessons=lessons,
        )

        # Add factor weight calibration
        cal = debate_record.setdefault("calibration_delta", {})  # 【变量】校准增量字典:记录因子权重/方向偏好的建议调整
        cal["factor_weights"] = _infer_factor_deltas(user_factors, user_missing)  # 【调用函数】启发式推断各因子权重增减(TENTATIVE)
        cal["direction_bias"] = (
            "agent_tends_bullish" if "conservative" in user_direction.lower() else ""
        )

        try:
            save_debate(symbol, debate_record)  # 【调用函数】把辩论记录持久化到进化记忆
            update_profile_from_debate(symbol, debate_record)  # 【调用函数】用辩论结果更新用户画像(偏好校准)
            _safe_print("[Evolution] Debate saved to evolution memory.")
            _safe_print(f"           Next analysis of {symbol} will incorporate these lessons.")
        except Exception as e:
            logger.error("Failed to save evolution memory: %s", e)
            _safe_print(f"[Evolution] Warning: Could not save debate: {e}")

        _print_separator("=")
        _safe_print("  Analysis + Feedback Complete. Thank you!")
        _print_separator("=")

        return {
            "user_feedback_summary": debate_summary,
            "messages": [HumanMessage(content=f"[User Feedback Session]\n{debate_summary}")],
        }

    return node


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


# 【功能】安全打印分隔线(char 重复 width 次)。
# 【参数】char: 分隔线字符,默认 "-";width: 长度,默认 60。
def _print_separator(char: str = "-", width: int = 60) -> None:
    """Print a separator line safely."""
    _safe_print(char * width)


# 【功能】从综合结论文本中提取方向判断(看多/看空/震荡)。
# 【参数】synthesis: 分析师综合结论文本。
# 【返回】命中方向判断原文或该行值;全部未命中返回 "unknown"。
# 【关键】按优先级匹配显式标签,最后退回关键词"看多|看空|震荡"。
def _extract_direction(synthesis: str) -> str:
    """Extract the direction judgment from synthesis text."""
    import re  # 【调用包】正则:从结论文本定位方向判断

    patterns = [  # 【变量】方向判断匹配模式列表(先显式标签,后关键词兜底)
        r"方向判断[：:]\s*(.+?)(?:\n|$)",
        r"最终建议[：:]\s*(.+?)(?:\n|$)",
        r"\*\*方向判断[：:]\*\*\s*(.+?)(?:\n|$)",
        r"看多|看空|震荡",
    ]
    for pat in patterns:
        m = re.search(pat, synthesis)  # 【调用函数】正则搜索当前模式在结论文本中的首个命中
        if m:
            return m.group(0) if m.lastindex is None else m.group(1).strip()
    return "unknown"


# 【功能】从综合结论文本中提取四维度权重分配(如"技术面=4/10")。
# 【参数】synthesis: 分析师综合结论文本。
# 【返回】形如 "技术面=4/10, 基本面=3/10" 的字符串;未命中返回 "not specified"。
def _extract_weights(synthesis: str) -> str:
    """Extract weight allocation from synthesis text."""
    import re  # 【调用包】正则:从结论文本定位权重分配

    weights = re.findall(  # 【调用函数】正则查找所有"维度 + N/10"片段
        r"(技术[面盘]|基本[面盘]|宏观[面盘]|政策[面盘]).*?(\d+)/10",
        synthesis,
    )
    if weights:
        return ", ".join(f"{w[0]}={w[1]}" for w in weights)
    return "not specified"


# 【功能】从辩论总结中启发式提取"经验教训"条目(取经验/教训小节下的列表项)。
# 【参数】summary: LLM 生成的辩论总结文本。
# 【返回】清理后的教训字符串列表,最多 5 条;无命中返回空列表。
# 【关键】仅当小节标题含 经验教训/Lessons/调整建议/改进 时才采集,空行结束小节。
def _extract_lessons(summary: str) -> list[str]:
    """Extract lessons learned from the debate summary."""
    import re  # 【调用包】正则:去掉列表项前缀符号

    lessons: list[str] = []  # 【变量】提取出的教训列表
    # Find bullet points under lessons/experience sections
    in_section = False  # 【变量】当前行是否处于"经验教训"小节内的标志
    for line in summary.split("\n"):
        stripped = line.strip()
        if any(kw in stripped for kw in ("经验教训", "Lessons", "调整建议", "改进")):
            in_section = True
            continue
        if in_section and stripped.startswith(("-", "*", "•", "1.", "2.", "3.")):
            lesson = re.sub(r"^[-*•\d.]+\s*", "", stripped).strip()  # 【调用函数】正则去掉列表项前缀符号并取正文
            if lesson and len(lesson) > 5:  # 【变量】过滤过短条目,保证教训有信息量
                lessons.append(lesson)
        elif in_section and not stripped:
            in_section = False
    return lessons[:5]


# 【功能】判定辩论的解决类型——客观评估,不默认用户是对的。
# 【参数】debate_rounds: 辩论回合记录列表;user_direction: 用户对方向的立场文本。
# 【返回】解决类型字符串:user_persuaded_agent / agent_persuaded_user / mutual_understanding /
#        agent_maintained_position / insufficient_evidence / no_debate。
# 【关键】用短语命中计数(substantive_ack / agent_pushbacks / user_concessions)组合判定。
def _determine_resolution(debate_rounds: list[dict[str, str]], user_direction: str) -> str:
    """Determine how the debate resolved — objectively, not assuming user is right.

    Resolution types:
    - agent_maintained_position: Agent held its ground with data, user did not persuade
    - user_persuaded_agent: User provided compelling data/logic, agent changed its view
    - agent_persuaded_user: Agent's data-based reasoning convinced the user
    - mutual_understanding: Both sides found common ground on some points, disagreed on others
    - insufficient_evidence: User's claims could not be verified; no resolution reached
    - no_debate: No debate rounds occurred
    """
    if not debate_rounds:
        return "no_debate"

    agent_text = " ".join(  # 【变量】系统方全部发言拼接后的小写文本,用于短语匹配
        r["content"] for r in debate_rounds if r.get("speaker") == "agent"
    ).lower()

    user_text = " ".join(r["content"] for r in debate_rounds if r.get("speaker") == "user").lower()  # 【变量】用户方全部发言拼接后的小写文本

    # Count agent acknowledgments (substantive, not platitudes)
    substantive_ack = sum(  # 【变量】系统实质性认错的短语命中数(如 "you're right that" / "i didn't consider")
        1
        for phrase in (
            "you're right that",
            "you are right that",
            "valid point because",
            "that changes",
            "i didn't consider",
            "i was wrong about",
            "i agree because",
        )
        if phrase in agent_text
    )

    # Count agent pushbacks (challenging user with data)
    agent_pushbacks = sum(  # 【变量】系统用数据反驳用户的短语命中数(如 "but the data" / "my data shows")
        1
        for phrase in (
            "however",
            "but the data",
            "my data shows",
            "i disagree because",
            "that doesn't align with",
            "the evidence suggests otherwise",
            "does not align",
        )
        if phrase in agent_text
    )

    # Count user concessions
    user_concessions = sum(  # 【变量】用户让步/认可的短语命中数(如 "that makes sense" / "fair enough")
        1
        for phrase in (
            "i see",
            "that makes sense",
            "you're right",
            "good point",
            "fair enough",
            "agreed",
        )
        if phrase in user_text
    )

    # Determine resolution
    if substantive_ack >= 2 and agent_pushbacks <= 1:
        return "user_persuaded_agent"
    elif agent_pushbacks >= 2 and user_concessions >= 1:
        return "agent_persuaded_user"
    elif agent_pushbacks >= 2 and substantive_ack >= 1:
        return "mutual_understanding"
    elif agent_pushbacks >= 2 and substantive_ack == 0:
        return "agent_maintained_position"
    elif substantive_ack == 0 and agent_pushbacks == 0:
        return "insufficient_evidence"
    else:
        return "mutual_understanding"


# 【功能】根据用户反馈(权重担忧/缺失信息)启发式推断各因子权重的建议增减。
# 【参数】user_factors: Q2 的权重担忧文本;user_missing: Q3 的缺失信息文本。
# 【返回】{因子名: "+1/-1 (tentative — pending debate confirmation)"} 字典。
# 【关键】结果为 TENTATIVE:仅当辩论确认用户观点有效后才会被 update_profile_from_debate 应用。
def _infer_factor_deltas(user_factors: str, user_missing: str) -> dict[str, str]:
    """Heuristically infer factor weight adjustments from user feedback.

    IMPORTANT: These deltas are marked as TENTATIVE. They only become active
    after the debate confirms the user's point was valid. The delta values are
    stored in the debate record but the Evolution Memory update_profile_from_debate
    function will check the resolution before applying them.
    """
    combined = (user_factors + " " + user_missing).lower()  # 【变量】Q2+Q3 合并小写文本,用于关键词匹配
    deltas: dict[str, str] = {}  # 【变量】因子→建议增减 的映射(增量)
    factor_map = {  # 【变量】中英文关键词→标准因子名 的映射(含拼音,如 chukou→export_data)
        "export": "export_data",
        "chukou": "export_data",
        "inventory": "inventory",
        "kucun": "inventory",
        "basis": "basis",
        "jicha": "basis",
        "cost": "cost_transmission",
        "chengben": "cost_transmission",
        "policy": "policy_risk",
        "zhengce": "policy_risk",
        "technical": "technical_signals",
        "jishu": "technical_signals",
        "macro": "macro",
        "hongguan": "macro",
        "profit": "profit_margin",
        "lirun": "profit_margin",
        "seasonal": "seasonal_patterns",
        "jijie": "seasonal_patterns",
    }

    for keyword, factor in factor_map.items():
        if keyword in combined:
            if any(
                w in combined
                for w in ("underweight", "低估", "不足", "missing", "遗漏", "忽略", "缺少")
            ):
                # Mark as tentative — only confirmed after debate resolution
                deltas[factor] = "+1 (tentative — pending debate confirmation)"
            elif any(w in combined for w in ("overweight", "高估", "过度", "太重")):
                deltas[factor] = "-1 (tentative — pending debate confirmation)"

    return deltas
