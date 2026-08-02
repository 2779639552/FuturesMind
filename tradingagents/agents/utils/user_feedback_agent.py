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

import logging
import sys
from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage

from tradingagents.dataflows.evolution_memory import (
    build_debate_record,
    get_evolution_context,
    save_debate,
    update_profile_from_debate,
)

logger = logging.getLogger(__name__)

MAX_DEBATE_ROUNDS = 5  # safety cap on back-and-forth exchanges

# ---------------------------------------------------------------------------
# Safe print helper (handles Windows GBK terminals)
# ---------------------------------------------------------------------------


def _safe_print(text: str, max_chars: int = 3000) -> None:
    """Print text safely, falling back to ASCII on encoding errors."""
    if not text:
        return
    content = text[:max_chars]
    try:
        print(content)
    except (UnicodeEncodeError, UnicodeDecodeError):
        safe = content.encode("ascii", errors="replace").decode("ascii")
        print(safe)
    if len(text) > max_chars:
        print(f"\n... (truncated, {len(text):,} chars total)")


def _safe_input(prompt: str = "") -> str:
    """Read a line from stdin safely."""
    try:
        return input(prompt)
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

The user has shared their feedback on your analysis. Respond following the rules above. Reply in Chinese (中文). Keep your response focused and substantive (2-4 paragraphs)."""

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

Reply in Chinese (中文). Keep it substantive."""

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

Keep the entire summary under 500 words."""


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


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

    def node(state: dict[str, Any]) -> dict[str, Any]:
        """Interactive user feedback node.

        Reads analysis results from state, engages the user in debate,
        persists the results to Evolution Memory, and returns an updated
        state dict.
        """
        if not enabled:
            return {"user_feedback_summary": ""}

        symbol = state.get("company_of_interest", "?")
        trade_date = state.get("trade_date", "?")
        synthesis = state.get("investment_plan", "") or state.get(
            "final_trade_decision", ""
        )

        # Check if we are in an interactive terminal
        if not sys.stdout.isatty():
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
        direction = _extract_direction(synthesis)
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
        _safe_print(
            "Q1: Do you agree with the direction judgment?"
        )
        _safe_print(
            "    [1] Fully agree  [2] Agree but more conservative"
        )
        _safe_print(
            "    [3] Agree but more aggressive  [4] Partially disagree"
        )
        _safe_print(
            "    [5] Completely disagree  [/skip] Skip all feedback"
        )
        q1 = _safe_input("> ").strip()

        if q1.lower() in ("/skip", "skip", "s"):
            _safe_print("")
            _safe_print("[Feedback] Skipped. Analysis complete.")
            return {"user_feedback_summary": ""}

        direction_map = {
            "1": "Fully agree",
            "2": "Agree but more conservative (agree but more conservative)",
            "3": "Agree but more aggressive (agree but more aggressive)",
            "4": "Partially disagree (partially disagree)",
            "5": "Completely disagree (completely disagree)",
        }
        user_direction = direction_map.get(q1, q1)

        # Q2: Under/over-weighted factors
        _safe_print("")
        _safe_print(
            "Q2: Which factors were under-weighted or over-weighted?"
        )
        _safe_print(
            "    (Type your answer, multi-line OK. Empty line to finish.)"
        )
        q2_lines = []
        while True:
            line = _safe_input("> ")
            if not line.strip():
                break
            q2_lines.append(line)
        user_factors = "\n".join(q2_lines)

        # Q3: Missing information
        _safe_print("")
        _safe_print(
            "Q3: Any key information we missed?"
        )
        _safe_print(
            "    (Type your answer, empty line to finish.)"
        )
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

        debate_rounds: list[dict[str, str]] = []
        conversation_history: list[str] = []

        # Build user opinion from Q1-Q3
        user_opinion_parts = [f"Direction view: {user_direction}"]
        if user_factors:
            user_opinion_parts.append(f"Factor concerns: {user_factors}")
        if user_missing:
            user_opinion_parts.append(f"Missing info: {user_missing}")
        user_opinion = "; ".join(user_opinion_parts)

        # Load evolution context for richer debate
        evolution_ctx = get_evolution_context(symbol)
        evolution_note = ""
        if evolution_ctx:
            evolution_note = (
                "\n**Historical User Preferences (from past debates):**\n"
                f"{evolution_ctx[:1500]}\n"
            )

        # Initial debate prompt
        weights_str = _extract_weights(synthesis)
        debate_prompt = DEBATE_SYSTEM_PROMPT.format(
            symbol=symbol,
            direction=direction,
            weights=weights_str,
            summary=synthesis[:1500],
            evolution_note=evolution_note,
        )
        debate_full = debate_prompt + f"\n\nUser feedback: {user_opinion}"

        try:
            response = llm.invoke(debate_full)
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
            cont_prompt = CONTINUATION_PROMPT.format(
                conversation_history="\n".join(conversation_history[-10:]),
                user_message=user_msg,
            )

            try:
                response = llm.invoke(cont_prompt)
                agent_msg = (
                    response.content
                    if hasattr(response, "content")
                    else str(response)
                )
            except Exception as e:
                logger.error("Debate continuation LLM call failed: %s", e)
                agent_msg = "I see. Thank you for sharing your perspective."

            _safe_print("")
            _safe_print(f"[AI]: {agent_msg}")
            _safe_print("")
            debate_rounds.append({"speaker": "agent", "content": agent_msg})
            conversation_history.append(f"AI: {agent_msg}")

            # Auto-detect end: short user acknowledgment
            if len(user_msg) <= 10 and any(
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
        summary_prompt = SUMMARY_PROMPT.format(
            conversation_history="\n".join(conversation_history),
        )
        try:
            response = llm.invoke(summary_prompt)
            debate_summary = (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
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
        lessons = _extract_lessons(debate_summary)

        # Build and save debate record
        agent_conclusion = {
            "direction": direction,
            "weights": weights_str,
            "summary": synthesis[:500],
        }

        debate_record = build_debate_record(
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
        cal = debate_record.setdefault("calibration_delta", {})
        cal["factor_weights"] = _infer_factor_deltas(user_factors, user_missing)
        cal["direction_bias"] = (
            "agent_tends_bullish" if "conservative" in user_direction.lower() else ""
        )

        try:
            save_debate(symbol, debate_record)
            update_profile_from_debate(symbol, debate_record)
            _safe_print("[Evolution] Debate saved to evolution memory.")
            _safe_print(
                f"           Next analysis of {symbol} will incorporate these lessons."
            )
        except Exception as e:
            logger.error("Failed to save evolution memory: %s", e)
            _safe_print(f"[Evolution] Warning: Could not save debate: {e}")

        _print_separator("=")
        _safe_print("  Analysis + Feedback Complete. Thank you!")
        _print_separator("=")

        return {
            "user_feedback_summary": debate_summary,
            "messages": [
                HumanMessage(
                    content=f"[User Feedback Session]\n{debate_summary}"
                )
            ],
        }

    return node


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _print_separator(char: str = "-", width: int = 60) -> None:
    """Print a separator line safely."""
    _safe_print(char * width)


def _extract_direction(synthesis: str) -> str:
    """Extract the direction judgment from synthesis text."""
    import re

    patterns = [
        r"方向判断[：:]\s*(.+?)(?:\n|$)",
        r"最终建议[：:]\s*(.+?)(?:\n|$)",
        r"\*\*方向判断[：:]\*\*\s*(.+?)(?:\n|$)",
        r"看多|看空|震荡",
    ]
    for pat in patterns:
        m = re.search(pat, synthesis)
        if m:
            return m.group(0) if m.lastindex is None else m.group(1).strip()
    return "unknown"


def _extract_weights(synthesis: str) -> str:
    """Extract weight allocation from synthesis text."""
    import re

    weights = re.findall(
        r"(技术[面盘]|基本[面盘]|宏观[面盘]|政策[面盘]).*?(\d+)/10",
        synthesis,
    )
    if weights:
        return ", ".join(f"{w[0]}={w[1]}" for w in weights)
    return "not specified"


def _extract_lessons(summary: str) -> list[str]:
    """Extract lessons learned from the debate summary."""
    import re

    lessons: list[str] = []
    # Find bullet points under lessons/experience sections
    in_section = False
    for line in summary.split("\n"):
        stripped = line.strip()
        if any(
            kw in stripped
            for kw in ("经验教训", "Lessons", "调整建议", "改进")
        ):
            in_section = True
            continue
        if in_section and stripped.startswith(("-", "*", "•", "1.", "2.", "3.")):
            lesson = re.sub(r"^[-*•\d.]+\s*", "", stripped).strip()
            if lesson and len(lesson) > 5:
                lessons.append(lesson)
        elif in_section and not stripped:
            in_section = False
    return lessons[:5]


def _determine_resolution(
    debate_rounds: list[dict[str, str]], user_direction: str
) -> str:
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

    agent_text = " ".join(
        r["content"]
        for r in debate_rounds
        if r.get("speaker") == "agent"
    ).lower()

    user_text = " ".join(
        r["content"]
        for r in debate_rounds
        if r.get("speaker") == "user"
    ).lower()

    # Count agent acknowledgments (substantive, not platitudes)
    substantive_ack = sum(
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
    agent_pushbacks = sum(
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
    user_concessions = sum(
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


def _infer_factor_deltas(
    user_factors: str, user_missing: str
) -> dict[str, str]:
    """Heuristically infer factor weight adjustments from user feedback.

    IMPORTANT: These deltas are marked as TENTATIVE. They only become active
    after the debate confirms the user's point was valid. The delta values are
    stored in the debate record but the Evolution Memory update_profile_from_debate
    function will check the resolution before applying them.
    """
    combined = (user_factors + " " + user_missing).lower()
    deltas: dict[str, str] = {}

    factor_map = {
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
            elif any(
                w in combined
                for w in ("overweight", "高估", "过度", "太重")
            ):
                deltas[factor] = "-1 (tentative — pending debate confirmation)"

    return deltas
