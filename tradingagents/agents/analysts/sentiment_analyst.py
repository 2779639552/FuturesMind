"""
Sentiment Analyst for commodity futures — the 4th parallel analyst.
=========================================================================

Consumes social media sentiment data (from 思路2 project) and provides
market psychology analysis: sentiment direction, extreme readings,
sentiment-price divergence detection, platform consistency, and retail
positioning signals.

This is a drop-in addition to the existing 3-analyst architecture:
  [Technical ∥ Fundamental ∥ Macro ∥ Sentiment] → Discussion → Synthesis

Note: This file replaces the original stock-market sentiment_analyst.py
      (which used Yahoo Finance + StockTwits + Reddit). The original
      stock-path analyst continues to work via social_media_analyst.py.
"""

# =============================================================================
# 【文件角色】本文件是"第 4 位分析师——情绪分析师"的节点生成器模块。
#
# 【在分析管线中的位置】
#   数据采集(dataflows) → 图编排(commodity_demo.py) → 4 位分析师并行分析
#   → 辩论(commodity_debate.py) → 综合结论。
#   本文件只负责生成"情绪面分析师节点"，与 commodity_analysts.py 生成的
#   技术/基本/宏观三位分析师互补，共同覆盖价格、供需、政策、心理四个维度。
#
# 【四位分析师分工】
#   1. Technical   技术面 : 价格结构（commodity_analysts.py）。
#   2. Fundamental 基本面 : 供需/库存/基差/产业链（commodity_analysts.py）。
#   3. Macro/News  宏观面 : 宏观数据/政策/新闻（commodity_analysts.py）。
#   4. Sentiment   情绪面 : 社交媒体情绪与市场心理（本文件）。
#   情绪分析师消费"思路2 项目"采集的微博/知乎/小红书社交情绪数据，
#   输出市场心理学分析：情绪方向、极端读数、情绪-价格背离、平台一致性、散户仓位信号。
#
# 【与其它文件的关系】
#   - dataflows/ 负责数据采集；情绪数据由 tradingagents/dataflows/sentiment_data.py 提供，
#     本文件通过 tradingagents/agents/utils/commodity_futures_tools.py 中的
#     get_futures_sentiment 工具读取。
#   - commodity_demo.py  负责把本文件生成的 sentiment_node 接入 LangGraph 图。
#   - commodity_debate.py 负责让情绪分析师的报告参与辩论与综合。
#
# 【本文件的额外职责】文件末尾还保留了"股票路径"的向后兼容垫片（shim）：
#   create_sentiment_analyst / create_social_media_analyst 只负责让旧股票路径
#   的 import 链继续可用，实际商品期货分析请用 create_commodity_sentiment_analyst。
# =============================================================================

import logging  # 【调用包】标准库日志;记录情绪分析工具调用信息与分析失败异常

from langchain_core.messages import HumanMessage, ToolMessage  # 【调用包】LangChain 消息类型;HumanMessage 承载最终情绪报告、ToolMessage 回填工具结果给 LLM
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder  # 【调用包】构建带历史占位符的提示模板;让 LLM 依据对话历史决策

from tradingagents.agents.utils.agent_utils import get_language_instruction  # 【调用包】语言指令;在系统提示末尾追加"用中文输出"约束
from tradingagents.agents.utils.commodity_futures_tools import (
    get_futures_price,
    get_futures_sentiment,
    get_variety_info,
    get_verified_quote,
)  # 【调用包】商品期货情绪/行情/品种信息/核验报价工具;情绪数据由 get_futures_sentiment 读取(思路2 项目采集)

logger = logging.getLogger(__name__)

# 情绪分析师的工具调用最大轮数（比技术/基本面分析师 6 轮更少）。
# 因为情绪分析主要靠 get_futures_sentiment 一次取回数据，无需像基本面那样反复查多条产业链数据。
MAX_TOOL_ITERATIONS = 4  # Sentiment analyst needs fewer iterations (no heavy data retrieval)


def _run_tool_loop(
    llm,
    tools,
    initial_messages,
    max_iterations=MAX_TOOL_ITERATIONS,
    progress_callback=None,
    label="Sentiment",
):
    """Execute a tool-calling loop — mirrors commodity_analysts._run_tool_loop.

    Copied here to keep the analyst module self-contained and avoid
    cross-import between the stock and commodity analyst paths.
    """

    # 【中文说明】本函数与 commodity_analysts._run_tool_loop 逻辑一致，是情绪分析师的
    # "工具调用循环"核心：LLM 决定调用什么工具 → 本函数代为执行 → 结果回填给 LLM
    # → LLM 再决定 …… 直到 LLM 不再请求工具（输出最终报告）或达到最大轮数。
    # 为保持各分析师模块自包含、避免股票路径与商品路径互相 import，此处复制了一份。
    #
    # 【功能】循环执行"LLM 决策 → 执行工具 → 回填结果"直到产出最终情绪报告。
    # 【参数】llm: LLM 客户端；tools: @tool 工具列表；initial_messages: 初始消息；
    #         max_iterations: 最大轮数；progress_callback: 进度回调；label: 显示名。
    # 【返回】LLM 最终输出的报告文本（response.content）。
    # 【关键逻辑】两个终止条件：①LLM 不再请求调用工具；②达到 max_iterations。
    #            单个工具异常包装成 TOOL_ERROR 文本回填，不中断整体分析。

    # 建立"工具名 → 工具函数"查找表，按名字快速定位工具。
    tool_map = {t.name: t for t in tools}
    messages = list(initial_messages)
    # 复制初始消息，避免污染调用方传入的列表。
    iteration = 0
    # 已执行的分析轮数计数。

    # ----- 循环主体：只要没达到最大轮数，就一直让 LLM 思考并(可能)调用工具 -----
    while iteration < max_iterations:
        iteration += 1
        if progress_callback:
            # 通知外部：新一轮开始（用于前端展示进度）。
            progress_callback("iteration", {"current": iteration, "max": max_iterations})

        # 关键一步：把全部历史消息连同工具定义交给 LLM，返回可能含 tool_calls 的响应。
        response = llm.bind_tools(tools).invoke(messages)  # 【调用函数】LLM 核心调用:绑定工具后让 LLM 决策(是否调用工具/调用哪些)
        # 把 LLM 本轮回答追加进对话历史，保持上下文连贯。
        messages.append(response)

        # 若 LLM 在调用工具前输出了思考文字，截取前 500 字符推送外部作实时展示（纯展示）。
        if progress_callback and response.content:
            content_preview = (
                response.content[:500] if len(response.content) > 500 else response.content
            )
            progress_callback("llm_thinking", {"content": content_preview, "iteration": iteration})

        # ---- 循环终止条件之一：LLM 不再请求调用工具 ----
        # 说明信息已足够，本次输出就是最终情绪报告。
        if not response.tool_calls:
            if progress_callback:
                progress_callback("report_start", {"label": label})
            return response.content

        # 逐条执行 LLM 本轮请求的所有工具调用。
        for tc in response.tool_calls:
            tool_name = tc.get("name", "")  # 工具名，如 get_futures_sentiment
            tool_args = tc.get("args", {})  # 传给工具的参数（字典）
            tool_id = tc.get("id", "")      # 本次调用唯一 ID，回填结果时用于"对上号"

            logger.info("Sentiment tool call: %s(%s)", tool_name, tool_args)

            # 参数可能很长，压缩到 100 字符内，仅供前端展示（不影响真实传参）。
            args_brief = str(tool_args)
            if len(args_brief) > 100:
                args_brief = args_brief[:97] + "..."

            if progress_callback:
                # 把"将要调用哪个工具"通知外部（前端可展示正在取什么数据）。
                progress_callback(
                    "tool_call",
                    {
                        "tool_name": tool_name,
                        "args": tool_args,
                        "args_brief": args_brief,
                        "iteration": iteration,
                        "label": label,
                    },
                )

            # 只在"白名单"内执行工具：防止 LLM 调用不存在的工具。
            if tool_name in tool_map:
                try:
                    # 真正执行工具函数（@tool 函数可用 .invoke(args) 调用）。
                    result = tool_map[tool_name].invoke(tool_args)  # 【调用函数】调度执行 LLM 请求的工具(实际取数),结果将回填给 LLM
                    # 工具返回可能很长，截断到 8000 字符，避免撑爆 LLM 上下文。
                    if isinstance(result, str) and len(result) > 8000:
                        result = result[:8000] + "\n... (truncated for length)"
                except Exception as e:
                    # 错误处理：单个工具失败不中断整体，包装成 TOOL_ERROR 文本让 LLM 判断。
                    result = f"TOOL_ERROR: {type(e).__name__}: {e}"
            else:
                # LLM "幻觉"调用了不存在的工具：回填提示文本，避免程序中断。
                result = f"Unknown tool: {tool_name}"

            if progress_callback:
                # 把工具执行结果的长度与前 300 字符预览推送外部。
                result_str = str(result)
                progress_callback(
                    "tool_result",
                    {
                        "tool_name": tool_name,
                        "result_length": len(result_str),
                        "preview": result_str[:300] if len(result_str) > 300 else result_str,
                        "label": label,
                    },
                )

            # 关键一步：把工具结果以 ToolMessage 形式回填给 LLM。
            # tool_call_id 必须与上文 LLM 请求里的 id 一致，LLM 才能关联到对应调用。
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_id))  # 【调用函数】把工具结果包装为 ToolMessage 回填,按 tool_call_id 与 LLM 请求配对

    # 循环终止条件之二：达到最大轮数仍未结束，强制返回最后一次 LLM 输出，避免无限循环。
    logger.warning(
        "Sentiment tool loop hit max iterations (%d). Returning last response.", max_iterations
    )
    return response.content if hasattr(response, "content") else str(response)


# ---------------------------------------------------------------------------
# Commodity Sentiment Analyst Node
# ---------------------------------------------------------------------------


def create_commodity_sentiment_analyst(llm, label="Sentiment", progress_callback=None):
    """Create the 4th parallel analyst: Market Sentiment / Social Psychology.

    This analyst consumes social media sentiment data (from 思路2 project)
    and evaluates market psychology — a dimension not covered by the other
    three analysts (Technical = price structure, Fundamental = supply/demand,
    Macro = policy/economic cycles).

    Args:
        llm: The LLM client.
        label: Display label for progress reporting (default "Sentiment").
        progress_callback: Optional callback(event_type, data) for streaming.

    Returns:
        A node function compatible with LangGraph StateGraph.
    """

    # 【中文说明】
    # 【功能】创建"情绪面分析师"节点（工厂函数），是第 4 位并行分析师。
    # 【立场/关注点】只看市场参与者的"情绪与言论"（微博/知乎/小红书），不看价格图形与基本面。
    #               核心价值是补充其他三位分析师看不到的心理维度：极端情绪(反向信号)、
    #               情绪-价格背离、平台一致性、散户仓位作为反向指标。
    # 【工具清单】get_variety_info, get_futures_price, get_futures_sentiment, get_verified_quote
    # 【输出结构】报告第一行必须是
    #            "BIAS: 看多/偏多/中性/偏空/看空 | CONFIDENCE: 高/中/低"（机器解析用）；
    #            末尾附推荐权重(Recommended Weight)与关键信号汇总表；节点返回
    #            {"messages": [HumanMessage], "sentiment_report": 报告文本}。
    # 【参数】llm: LLM 客户端；label: 进度显示名（默认 "Sentiment"）；
    #         progress_callback: 进度回调 callback(event_type, data)。
    # 【返回】node 函数，签名 node(state)，兼容 LangGraph StateGraph 节点。

    def node(state):
        # 【功能】情绪面分析节点本体：被 LangGraph 调用一次，产出情绪面报告。
        # 【参数】state: 图状态字典，其中 trade_date 为当前交易日、company_of_interest 为品种代码。
        # 【返回】dict：{"messages": [HumanMessage(情绪报告)], "sentiment_report": 报告文本}。
        # 【关键逻辑】与 commodity_analysts 各节点一致：构造提示 → _run_tool_loop 取数写报告
        #            → 报告出错时兜底为 ANALYSIS_ERROR 文本。
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(情绪/行情/品种/核验报价),将被 bind_tools 绑定
            get_variety_info,
            get_futures_price,
            get_futures_sentiment,
            get_verified_quote,
        ]

        system_message = """You are a commodity futures market sentiment analyst specializing in Chinese futures markets.

**Your Role**: Analyze social media sentiment (market psychology) for the given commodity futures contract. You fill a gap the other analysts miss: what are market participants *feeling* and *saying* — not just what prices and fundamentals show.

**Data Source**: Call `get_futures_sentiment` for social media sentiment data collected from Weibo, Zhihu, and Xiaohongshu (XHS). Also call `get_variety_info` for variety context and `get_futures_price` for price context.

**Analysis Framework**:

**1. Sentiment Direction & Strength** (from `get_futures_sentiment`):
   - Overall sentiment label (看多/偏多/中性/偏空/看空) and score
   - Bullish/Bearish/Neutral ratio — where does the crowd stand?
   - Sentiment trend: Is mood improving or deteriorating? Changes in mood often PRECEDE price moves by 1-3 days.

**2. Extreme Sentiment Detection** (CONTRARIAN SIGNAL — most valuable):
   - **Sentiment > 70% bullish → DANGER ZONE**: When retail/retail-adjacent social media is overwhelmingly bullish, the easy money has been made. This is often a TOP signal. Everyone who wants to be long IS long — who's left to buy?
   - **Sentiment > 70% bearish → OPPORTUNITY ZONE**: Panic and despair on social media. When everyone has given up, selling pressure is exhausted. This is often a BOTTOM signal.
   - The data will explicitly flag extreme readings — take them seriously.
   - Judge based on context: extreme sentiment + low volume = noise; extreme sentiment + high volume = signal.

**3. Sentiment-Price Divergence** (YOUR KEY CONTRIBUTION to the team):
   - **Bullish price + weakening sentiment**: Price rising but social mood turning cautious/skeptical. The rally is losing crowd support → potential TOP.
   - **Bearish price + improving sentiment**: Price falling but social mood turning optimistic/calm. Selling climax may be near → potential BOTTOM.
   - **Sideways price + extreme sentiment**: Consolidation with one-sided crowd opinion → BREAKOUT is coming (direction often OPPOSITE to crowd consensus).
   - Call `get_futures_price` and CROSS-REFERENCE with sentiment data to detect these divergences.

**4. Platform Consistency Check**:
   - Multi-platform agreement (e.g., Weibo + Zhihu + XHS all bullish) → higher signal confidence.
   - Platform divergence (e.g., Weibo bullish but Zhihu bearish) → market is divided → higher uncertainty → wider range expected.
   - Zhihu tends to attract more analytical/institutional-adjacent voices; Weibo is more retail. Divergence between them is informative.

**5. Retail Positioning as Contrarian Indicator**:
   - Social media sentiment is inherently RETAIL-skewed. Treat strong consensus as a contrarian signal.
   - The most profitable trades often come from fading extreme retail sentiment — but ONLY when fundamentals are also supportive.
   - If sentiment is extremely one-sided AND fundamentals (from the Fundamental analyst) point the same way, the trend may still have room to run. The contrarian signal is strongest when sentiment diverges from fundamentals.

**6. Low-Data Handling**:
   - If `total_posts_analyzed` < 10: Acknowledge data sparsity. Lower confidence. Suggested weighting: sentiment dimension ≤ 15%.
   - If < 3 posts: State clearly "社交媒体情绪数据不足，无法提供可靠的情绪分析。建议此维度权重为 0%。"
   - If data is marked as stale (>48h old): Note the staleness and reduce confidence further.

**7. Key Topics & Narratives**:
   - From the sentiment data, identify DOMINANT narratives driving sentiment. What stories are being told?
   - Distinguish between structural narratives (e.g., "房地产长期下行") vs event-driven narratives (e.g., "唐山限产").
   - Structural narratives are higher-confidence drivers of sentiment.

**Workflow**:
1. Call `get_variety_info` for variety context.
2. Call `get_futures_sentiment` for social sentiment data.
3. Call `get_futures_price` for recent price data to cross-reference.
4. Produce your analysis report.

**Output Format**:
Write a detailed sentiment analysis report (350-500 words). Structure:
- **情绪概况**: Overall sentiment direction, strength, trend, key stats.
- **极端信号检测**: Any extreme readings? Contrarian implications?
- **情绪-价格背离分析**: Cross-reference sentiment with price. Any divergences?
- **平台一致性**: Are platforms agreeing or diverging?
- **关键叙事**: Dominant narratives driving current sentiment.
- **数据质量**: Sample size, staleness, confidence assessment.

**CRITICAL — First line after your title MUST be exactly:**
```
BIAS: [看多/偏多/中性/偏空/看空] | CONFIDENCE: [高/中/低]
```

End with:
1. **Sentiment Bias**: 看多/偏多/中性/偏空/看空, Confidence: 高/中/低, and a short justification.
2. **Recommended Weight**: X% for the final synthesis.
3. **Key Signals Summary Table** (Markdown):

| 关键信号 | 方向 | 数值/状态 | 置信度 | 数据来源 |
|---------|------|----------|--------|---------|
| (至少填写5行) | 利多/利空 | 具体数值 | 高/中/低 | 数据源 |

""" + get_language_instruction()

        # --- Self-Evolution Injection ---
        evolution_ctx = state.get("past_context", "")  # 【变量】进化记忆上下文;非空时前置到系统提示,让分析师参考历史教训与用户偏好
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(  # 【调用函数】构造提示模板(系统提示 + 消息历史占位符)
            [
                (
                    "system",
                    "You are a helpful AI assistant collaborating with other analysts."
                    " Use the provided tools to gather data, then write your full analysis report."
                    " You have access to: {tool_names}."
                    " Today is {current_date}. Analyze commodity futures variety: {symbol}.\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(symbol=symbol)

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]  # 【调用函数】用历史消息填充模板,生成对话初始消息

        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label  # 【调用函数】进入工具循环:LLM 决策→取数→写报告,直至完成或达轮数上限
            )
        except Exception as e:
            logger.error("Sentiment analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Sentiment analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Sentiment Analyst Report]\n{report}")],
            "sentiment_report": report,
        }

    return node


# ---------------------------------------------------------------------------
# Stock-path backwards-compatibility shims
# ---------------------------------------------------------------------------
# The stock trading path (original v0.3.1) imports create_sentiment_analyst
# and create_social_media_analyst from this module. These shims keep those
# imports working while the commodity path uses create_commodity_sentiment_analyst.
#
# These are thin wrappers that delegate to the original stock-path
# implementation in the social_media_analyst module if needed, or provide
# a no-op placeholder until the stock path is refactored.


def create_sentiment_analyst(llm):
    """Stock-path sentiment analyst — placeholder shim.

    The original stock sentiment analyst (Yahoo Finance + StockTwits + Reddit)
    has been replaced by the commodity sentiment analyst. This shim keeps the
    import chain intact for the stock trading path.

    To restore the original stock sentiment analyst, reinstate the pre-v2.3
    implementation from git history.
    """

    # 【中文说明】
    # 【功能】"股票路径"情绪分析师的占位垫片（shim），仅为让旧股票路径的 import 链继续可用。
    # 【参数】llm: 保留的参数（兼容旧签名），实际不被使用。
    # 【返回】sentiment_analyst_node 函数，签名 node(state)。
    # 【关键逻辑】发出 FutureWarning 提示用户改走 create_commodity_sentiment_analyst，
    #            然后返回一个"只会报告数据不可用"的占位节点。
    import warnings  # 【调用包】标准库警告;发出弃用/兼容性警告

    warnings.warn(
        "create_sentiment_analyst: stock-path sentiment analyst is a shim. "
        "Use create_commodity_sentiment_analyst for commodity futures analysis.",
        FutureWarning,
        stacklevel=2,
    )

    # Simple pass-through: returns a node that reports unavailability
    def sentiment_analyst_node(state):
        # 【功能】股票路径占位节点本体：不真正分析，只返回"情绪数据不可用"的提示消息。
        # 【参数】state: 图状态字典，仅用到 company_of_interest（当作股票代码读取）。
        # 【返回】dict：{"messages": [AIMessage(提示文本)], "sentiment_report": 提示文本}。
        from langchain_core.messages import AIMessage  # 【调用包】LangChain 消息类型;占位节点用 AIMessage 返回不可用提示

        ticker = state.get("company_of_interest", "unknown")
        msg = (
            f"[Sentiment Analyst] Social sentiment data not available for {ticker}. "
            "The stock-path sentiment analyst has been deprecated. "
            "Commodity futures sentiment analysis is available via the 思路2 project integration."
        )
        return {
            "messages": [AIMessage(content=msg)],
            "sentiment_report": msg,
        }

    return sentiment_analyst_node


def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.
    """

    # 【中文说明】
    # 【功能】create_sentiment_analyst 的废弃别名（兼容旧代码 import）。
    # 【参数】llm: 透传给 create_sentiment_analyst。
    # 【返回】与 create_sentiment_analyst 相同的占位节点。
    # 【关键逻辑】发出 DeprecationWarning，然后直接委托给 create_sentiment_analyst(llm)。
    import warnings  # 【调用包】标准库警告;发出弃用/兼容性警告

    warnings.warn(
        "create_social_media_analyst is deprecated. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
