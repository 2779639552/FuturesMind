"""Multi-round adversarial debate nodes for commodity futures (v2.8).

v2.8: Debate agents now have tool access via _run_tool_loop — they can
fetch live prices, verify claims with data, and check sentiment during
the debate. This replaces the old LLM-only invoke pattern.

Replaces the single-round Discussion node with Bull/Bear/Moderator three-node
debate system, mirroring the original TradingAgents' Bull Researcher/Bear
Researcher/Research Manager pattern.
"""

# ===========================================================================
# 中文模块级说明
# 本文件 (commodity_debate.py) 负责"多空对抗辩论"三个核心节点的实现:
#   1. create_bull_debater()     —— 多方(看涨)辩论者,引用数据反驳空方论点;
#   2. create_bear_debater()     —— 空方(看跌)辩论者,引用数据反驳多方论点;
#   3. create_debate_moderator() —— 辩论主持人,复核双方论点、事实核查、裁定胜负。
#
# 在整个项目中的角色:
#   - 它是商品分析管线 (commodity_demo.py) 中"圆桌讨论"的升级版。
#     commodity_demo.py 的 build_commodity_graph() 会 import 本文件提供的
#     三个创建函数,把"分析师并行分析 -> 多空辩论 -> 综合研判 -> 情景分析"
#     串成一张 LangGraph 图。本文件只负责"产出图节点",不负责图的组装。
#   - 与 tradingagents 包的关系: 本文件直接调用 tradingagents 包内的
#     _run_tool_loop(工具调用循环)与 commodity_futures_tools(实时行情、
#     历史核价、情绪工具等),但不直接使用 tradingagents 的图形框架。
#   - 与 web_app.py 的关系: web_app.py(Web 前端服务)通过 commodity_demo.py
#     间接使用到本文件的辩论节点。因此本文件的输出数据结构
#     (debate_state 字典字段、discussion_summary 字段)是前后端约定的关键契约,
#     修改字段名会影响前端展示。
#
# 设计要点:
#   - 辩论双方共用 _run_tool_loop 工具循环,最多 DEBATE_MAX_ITERATIONS=3 轮,
#     让辩论者在发言前实时查价/核证,而非纯靠 LLM 记忆与推测。
# ===========================================================================

import logging  # 【调用包】日志记录(辩论进度/异常)

from langchain_core.messages import HumanMessage  # 【调用包】LLM 消息对象(把提示词封装为人类消息)

from tradingagents.agents.analysts.commodity_analysts import (
    _run_tool_loop,  # 【调用包】工具调用循环(LLM 可反复请求调用工具查证)
)
from tradingagents.agents.utils.commodity_futures_tools import (  # 【调用包】期货数据工具集(实时行情/核价/技术指标/情绪/品种信息)
    get_futures_indicators,
    get_futures_price,
    get_futures_sentiment,
    get_realtime_price,
    get_variety_info,
    get_verified_quote,
)

logger = logging.getLogger(__name__)

# Debate tool loop: fewer iterations than analysts (debate needs quick fact-checks)
# 辩论中的工具调用轮次上限:比分析师更少,因为辩论只需要"快速查证",不需要长篇研报。
DEBATE_MAX_ITERATIONS = 3  # 【变量】辩论中工具调用轮次上限(辩论只需快速查证,故比分析师少)

# Tools available to debaters (fact-checking + live data)
# 辩论者可用工具清单:覆盖"实时行情 + 精确历史核价 + 技术指标 + 情绪 + 品种信息"。
DEBATE_TOOLS = [  # 【变量】辩论者可用的工具清单(实时行情+核价+指标+情绪+品种信息)
    get_realtime_price,  # Live market price —— 当前实时市场价
    get_verified_quote,  # Precise historical snapshot —— 指定日期精确 OHLCV(用于核证)
    get_futures_price,  # Recent price history —— 近期价格历史
    get_futures_indicators,  # Technical levels (support/resistance) —— 技术指标/支撑阻力位
    get_futures_sentiment,  # Social sentiment check —— 社交媒体情绪方向
    get_variety_info,  # Variety metadata —— 品种基础信息(规格、交易时段等)
]

# Moderator tools: narrower set, focused on fact-checking
# 主持人工具清单:比辩论者更窄,只聚焦"事实核查"(价格相关),不需要技术指标/情绪等。
MODERATOR_TOOLS = [  # 【变量】主持人工具清单(只留事实核查相关,比辩论者更窄)
    get_realtime_price,
    get_verified_quote,
    get_futures_price,
]


# Helper — stage header (shared with commodity_demo.py)
# When a progress_callback is provided, output is routed there;
# otherwise falls back to structured logging.
# 阶段标题打印辅助函数:若外部传入 progress_callback,则把阶段标题通过回调上报
# (供 CLI 的富文本面板或 Web 前端实时展示);否则退回普通日志输出。
def _print_stage_header(title, progress_callback=None):
    """打印/上报一个"阶段标题",用于在流程中标记当前进行到哪一步。

    【功能】在辩论流程的关键节点输出标题,便于用户看到"主持人正在复核辩论"等阶段。
    【参数】title: 要显示的阶段标题字符串;progress_callback: 可选的进度回调函数,
            签名形如 callback(event_type, data),传入时会优先走回调而非日志。
    【返回】无。
    【关键逻辑】有回调走回调,无回调走 logger.info,两者择一,保证任何环境都能输出。
    """
    if progress_callback:
        progress_callback("stage_header", {"title": title})  # 【调用函数】通过回调上报阶段标题(供 CLI 面板/Web 前端展示)
    else:
        logger.info("=== %s ===", title)


def _debate_progress_callback(event_type, data):
    """Lightweight progress callback for debate tool calls."""
    # 【功能】辩论过程的轻量级进度回调:把辩论者/主持人的每次工具调用实时打日志。
    # 【参数】event_type: 事件类型("tool_call" 或 "tool_result");data: 事件数据字典,
    #          通常含 label(角色标签)、tool_name(工具名)、args_brief(参数摘要)、result_length(结果长度)。
    # 【返回】无。
    # 【关键逻辑】与 commodity_demo.py 的 console_progress_callback 不同,这里用 logger.debug
    #            (默认不打印),避免辩论过程中的多次查证刷屏;仅调试时需要。
    label = data.get("label", "Debate")  # 【变量】label:角色标签(如 Bull-R1),用于日志区分
    if event_type == "tool_call":
        logger.debug("[%s] %s(%s)", label, data["tool_name"], data.get("args_brief", ""))
    elif event_type == "tool_result":
        logger.debug(
            "[%s] <- %s chars from %s", label, data.get("result_length", 0), data["tool_name"]
        )


def create_bull_debater(llm, label="Bull"):
    """Build the strongest possible bullish case from all four analyst reports.

    v2.8: Now has TOOL ACCESS — can fetch live prices, verify claims,
    and check sentiment data to strengthen arguments during debate.
    Engages directly with the bear's last argument (multi-round), cites
    specific data points, and acknowledges valid bear concerns while
    arguing the bullish thesis outweighs them.
    """
    # 【功能】创建一个"多方(看涨)"辩论者图节点。返回的 node(state) 会在图执行时被调用,
    #          它读取共享状态里的四份分析师报告与对手(空方)上一条论点,通过工具循环查证数据,
    #          生成一段 200-400 字的看涨论点,并写回 debate_state 供下一节点(空方)阅读。
    # 【参数】llm: 语言模型客户端,用于生成辩论论点;label: 角色标签,默认 "Bull"。
    # 【返回】node: 一个符合 LangGraph 节点签名的闭包函数 node(state) -> dict(状态更新)。
    # 【关键逻辑】"互读"机制:通过 debate_state["bear_last"] 读取空方上一条论点,
    #           从而形成"多方开篇 -> 空方反驳 -> 多方再反驳"的多轮对抗。

    def node(state):
        # 从共享状态中取出四份分析师报告(均为文本字符串)
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        symbol = state["company_of_interest"]  # 当前分析的期货品种,如 "RB"  # 【变量】symbol:分析对象品种代码
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})  # 【变量】debate_state:辩论共享状态(多空互读的数据载体)
        # 读取空方上一轮论点 —— 这就是"辩论互读"的关键:多方要针对空方发言进行反驳
        bear_last = debate_state.get("bear_last", "")  # 【变量】bear_last:空方上一条论点(多方需反驳)
        round_num = debate_state.get("round", 0) + 1  # 当前辩论轮次 +1

        # 若有空方论点则构造"需反驳"上下文,否则是开篇立论
        if bear_last:
            opponent_context = f"空方论点（需要反驳）：\n{bear_last[:1000]}"  # 【变量】opponent_context:需反驳的对手论点上下文(无对手则开篇立论)
        else:
            opponent_context = "请发表你的开篇多方立论。"

        system_message = f"""You are the BULL (多方) debater for commodity futures variety `{symbol}` (as of {trade_date}).

**Your Role**: Build the strongest possible bullish (看涨/看多) case. You have access to data tools — use them to fact-check claims and find supporting evidence.

**Rules**:
1. Cite SPECIFIC data (prices, levels, ratios) — use tools to verify
2. Engage directly with the bear's arguments — point out flaws or missing context
3. Acknowledge valid bear concerns, but argue why the bullish thesis outweighs them
4. Be conversational and persuasive, like a real debate
5. **Use tools BEFORE making claims** — verify with get_realtime_price() or get_verified_quote()

**Tool Guidance**:
- `get_realtime_price("{symbol}")` — check the current live market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels for fact-checking
- `get_futures_price("{symbol}", ...) ` — get recent price history
- `get_futures_indicators("{symbol}", ...) ` — get technical levels (SMA, RSI, MACD, Bollinger)
- `get_futures_sentiment("{symbol}")` — check social media sentiment direction
- `get_variety_info("{symbol}")` — variety metadata (specs, trading hours)

**Analyst Reports (for context)**:
---
Technical: {technical[:1200] if technical else "N/A"}
Fundamental: {fundamental[:1200] if fundamental else "N/A"}
Macro/News: {macro[:800] if macro else "N/A"}
Sentiment: {sentiment[:600] if sentiment else "N/A"}
---

{opponent_context}

**Output**: Write 200-400 characters in Chinese. Start with "多方(R{round_num})：". Structure your argument with data, reasoning, and a rebuttal to the bear case.
"""
        logger.info("Bull R%d (with tools)...", round_num)

        # Build initial message —— 把构造好的 system_message 作为唯一一条初始消息发给 LLM
        initial_msg = HumanMessage(content=system_message)  # 【调用函数】封装 system_message 为 LLM 初始消息

        # Run tool-calling loop —— 调用工具循环:LLM 在回答前可反复请求调用工具(查价/核证),
        # 最多 DEBATE_MAX_ITERATIONS=3 轮,结果会通过 _debate_progress_callback 实时上报。
        try:
            result = _run_tool_loop(  # 【调用函数】运行工具调用循环:LLM 最多 3 轮查证后给出论点
                llm,
                DEBATE_TOOLS,
                [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label=f"Bull-R{round_num}",
            )
        except Exception as e:
            logger.error("Bull debater failed: %s", e)
            # Fallback to simple invoke —— 工具循环一旦报错(如网络/模型异常),降级为"纯 LLM 直接生成",
            # 保证辩论流程不会因为一次工具调用失败而中断。
            fallback = llm.invoke(  # 【调用函数】llm.invoke:工具循环异常时降级为纯 LLM 生成
                f"用中文为 {symbol} 商品期货构建最强看涨论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        if not result:
            # 兜底:工具循环返回空字符串时,用更简单的提示词再试一次(可能模型抽风返回空)
            logger.warning("Bull returned empty — retrying with simpler prompt")
            fallback = llm.invoke(
                f"用中文为 {symbol} 商品期货构建最强看涨论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        # 更新辩论共享状态:
        #   bull_history —— 追加本轮论点(历史拼接,供主持人/后续轮次查看);
        #   bull_last     —— 本轮论点,供"空方"下一轮读取反驳;
        #   bear_last     —— 原样保留空方上一条论点(多方不修改它);
        #   round         —— 推进轮次。这就是"辩论互读"的数据载体。
        new_debate_state = {  # 【变量】new_debate_state:更新后的辩论状态(写回本轮论点并推进轮次)
            "bull_history": debate_state.get("bull_history", "") + "\n" + result,
            "bear_history": debate_state.get("bear_history", ""),
            "bull_last": result,
            "bear_last": debate_state.get("bear_last", ""),
            "round": round_num,
        }

        # 返回状态更新:debate_state 供图内其他节点读取;messages 供历史记录/前端展示
        return {
            "debate_state": new_debate_state,
            "messages": [HumanMessage(content=f"[Bull R{round_num}]\n{result}")],
        }

    return node


def create_bear_debater(llm, label="Bear"):
    """Build the strongest possible bearish case from all four analyst reports.

    v2.8: Now has TOOL ACCESS — can fetch live prices, verify claims,
    and check sentiment data to strengthen arguments during debate.
    Mirrors the bull debater. Must engage with the bull's specific arguments,
    point out flaws or missing context, and explain why the bearish thesis
    is more convincing.
    """
    # 【功能】创建一个"空方(看跌)"辩论者图节点。与多方(market)结构完全镜像:
    #          读取分析师报告与多方上一条论点,通过工具循环查证数据,生成看跌论点,
    #          并把论点写回 debate_state 供主持人/后续节点使用。
    # 【参数】llm: 语言模型客户端;label: 角色标签,默认 "Bear"。
    # 【返回】node: 符合 LangGraph 节点签名的闭包函数 node(state) -> dict。
    # 【关键逻辑】"互读"对象是 debate_state["bull_last"];轮次计算与多方相同,
    #           从而形成"多方开篇 -> 空方反驳"的攻防对仗。

    def node(state):
        # 从共享状态中取出四份分析师报告
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        symbol = state["company_of_interest"]  # 【变量】symbol:分析对象品种代码
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})  # 【变量】debate_state:辩论共享状态(多空互读的数据载体)
        # 读取多方上一条论点 —— 空方必须针对多方发言进行反驳
        bull_last = debate_state.get("bull_last", "")  # 【变量】bull_last:多方上一条论点(空方需反驳)
        round_num = debate_state.get("round", 0) + 1

        # 若有多方论点则构造"需反驳"上下文,否则是开篇立论
        if bull_last:
            opponent_context = f"多方论点（需要反驳）：\n{bull_last[:1000]}"  # 【变量】opponent_context:需反驳的对手论点上下文(无对手则开篇立论)
        else:
            opponent_context = "请发表你的开篇空方立论。"

        system_message = f"""You are the BEAR (空方) debater for commodity futures variety `{symbol}` (as of {trade_date}).

**Your Role**: Build the strongest possible bearish (看跌/看空) case. You have access to data tools — use them to fact-check claims and find supporting evidence.

**Rules**:
1. Cite SPECIFIC data (prices, levels, ratios) — use tools to verify
2. Engage directly with the bull's arguments — point out flaws or missing context
3. Acknowledge valid bull concerns, but argue why the bearish thesis outweighs them
4. Be conversational and persuasive, like a real debate
5. **Use tools BEFORE making claims** — verify with get_realtime_price() or get_verified_quote()

**Tool Guidance**:
- `get_realtime_price("{symbol}")` — check the current live market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels for fact-checking
- `get_futures_price("{symbol}", ...) ` — get recent price history
- `get_futures_indicators("{symbol}", ...) ` — get technical levels (SMA, RSI, MACD, Bollinger)
- `get_futures_sentiment("{symbol}")` — check social media sentiment direction
- `get_variety_info("{symbol}")` — variety metadata (specs, trading hours)

**Analyst Reports (for context)**:
---
Technical: {technical[:1200] if technical else "N/A"}
Fundamental: {fundamental[:1200] if fundamental else "N/A"}
Macro/News: {macro[:800] if macro else "N/A"}
Sentiment: {sentiment[:600] if sentiment else "N/A"}
---

{opponent_context}

**Output**: Write 200-400 characters in Chinese. Start with "空方(R{round_num})：". Structure your argument with data, reasoning, and a rebuttal to the bull case.
"""
        logger.info("Bear R%d (with tools)...", round_num)

        # Build initial message —— 把 system_message 作为初始消息发送给 LLM
        initial_msg = HumanMessage(content=system_message)  # 【调用函数】封装 system_message 为 LLM 初始消息

        # Run tool-calling loop —— 与多方相同:最多 3 轮工具查证,失败则降级
        try:
            result = _run_tool_loop(  # 【调用函数】运行工具调用循环:空方最多 3 轮查证后给出论点
                llm,
                DEBATE_TOOLS,
                [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label=f"Bear-R{round_num}",
            )
        except Exception as e:
            logger.error("Bear debater failed: %s", e)
            # 降级:工具循环异常时直接用 LLM 生成看跌论点,避免流程中断
            fallback = llm.invoke(  # 【调用函数】llm.invoke:工具循环异常时降级为纯 LLM 生成
                f"用中文为 {symbol} 商品期货构建最强看跌论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        if not result:
            # 兜底:结果为空时用更简单的提示词再试一次
            logger.warning("Bear returned empty — retrying with simpler prompt")
            fallback = llm.invoke(
                f"用中文为 {symbol} 商品期货构建最强看跌论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        # 更新辩论共享状态(与多方镜像):
        #   bear_history —— 追加本轮空方论点;
        #   bear_last     —— 本轮论点,供"多方"下一轮反驳;
        #   bull_last     —— 原样保留多方上一条论点;
        #   round         —— 推进轮次。
        new_debate_state = {  # 【变量】new_debate_state:更新后的辩论状态(与多方镜像,写回空方论点)
            "bull_history": debate_state.get("bull_history", ""),
            "bear_history": debate_state.get("bear_history", "") + "\n" + result,
            "bull_last": debate_state.get("bull_last", ""),
            "bear_last": result,
            "round": round_num,
        }

        return {
            "debate_state": new_debate_state,
            "messages": [HumanMessage(content=f"[Bear R{round_num}]\n{result}")],
        }

    return node


def create_debate_moderator(llm):
    """Review the complete bull/bear debate and produce a structured summary.

    v2.8: Now has TOOL ACCESS — can fact-check claims made by debaters
    using live prices and verified quotes. Uses a narrower tool set
    focused on data verification.

    Replaces the old Discussion node output. Takes the full debate history
    plus the original analyst reports for fact-checking.
    """
    # 【功能】创建"辩论主持人"图节点:在多方与空方各完成发言后,主持人通读双方完整历史
    #           (bull_history / bear_history),用更窄的工具集(MODERATOR_TOOLS)核查事实,
    #           最后输出结构化的"辩论裁决"总结,写入 discussion_summary 供综合研判节点使用。
    # 【参数】llm: 语言模型客户端,用于主持裁决。
    # 【返回】node: 符合 LangGraph 节点签名的闭包函数 node(state) -> dict。
    # 【关键逻辑】这是辩论的收尾节点:它取代了旧版单轮 Discussion 节点的输出位置,
    #           输出字段名 discussion_summary 与 commodity_demo.py 中的"综合研判"节点严格对齐,
    #           是图内节点间传递讨论结果的关键契约。

    def node(state):
        # 读取分析师报告(供主持人在裁决时对照)
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        symbol = state["company_of_interest"]  # 【变量】symbol:分析对象品种代码
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})  # 【变量】debate_state:辩论共享状态(多空互读的数据载体)
        # 读取双方完整辩论历史(主持人要看全程,而不只是最后一轮)
        bull_history = debate_state.get("bull_history", "")  # 【变量】bull_history:多方完整辩论历史(主持人需通读)
        bear_history = debate_state.get("bear_history", "")  # 【变量】bear_history:空方完整辩论历史

        system_message = f"""You are the debate MODERATOR for {symbol} commodity futures (as of {trade_date}).

A bull vs bear debate just finished. Your job: summarize, fact-check, and judge.

**You have FACT-CHECKING TOOLS** — use them to verify disputed claims:
- `get_realtime_price("{symbol}")` — check the current market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels
- `get_futures_price("{symbol}", ...) ` — recent price history for trend verification

**Debate Transcript**:

BULL ARGUMENTS:
{bull_history[:2000] if bull_history else "None."}

BEAR ARGUMENTS:
{bear_history[:2000] if bear_history else "None."}

**Analyst Reports (for context)**:
Technical: {technical[:500] if technical else "N/A"}
Fundamental: {fundamental[:500] if fundamental else "N/A"}
Macro: {macro[:400] if macro else "N/A"}

**Tasks**:
1. FACT-CHECK: Use tools to verify 1-2 key factual claims from the debate (price levels, direction claims)
2. WINNER: Declare winner — bull / bear / draw — with reasoning
3. CONSENSUS: 2-4 points both sides agree on
4. DIVERGENCE: 2-4 points of disagreement, and why
5. KEY RISK: The single most important risk factor
6. COUNTERFACTUAL: If recent prices moved the opposite direction, would the conclusion change?

**Output Format** (in Chinese markdown):

## 辩论裁决
**Winner**: [bull/bear/draw]

### 事实核查
[Verified claims with tools — which claims were accurate, which were not]

### 共识点
### 分歧点
### 关键风险
### 反事实检验
### 辩论总结
"""
        _print_stage_header("[Moderator] Reviewing debate (with fact-check tools)...")  # 【调用函数】上报阶段标题(有回调走回调,否则打日志)

        initial_msg = HumanMessage(content=system_message)  # 【调用函数】封装 system_message 为 LLM 初始消息

        # Run tool-calling loop for fact-checking —— 主持人用更窄的 MODERATOR_TOOLS
        # 核查双方论点中的关键事实(如价格水平、方向断言),最多 3 轮。
        try:
            result = _run_tool_loop(  # 【调用函数】主持人工具循环:核查双方论点关键事实(最多 3 轮)
                llm,
                MODERATOR_TOOLS,
                [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label="Moderator",
            )
        except Exception as e:
            logger.error("Moderator failed: %s", e)
            # 降级:工具循环异常时直接用 LLM 总结辩论,保证后续节点能拿到讨论结果
            fallback = llm.invoke(  # 【调用函数】llm.invoke:工具循环异常时降级为纯 LLM 总结
                f"Summarize the bull vs bear debate for {symbol}. Bull: {bull_history[:1000]} Bear: {bear_history[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        if not result:
            # 兜底:结果为空时重试一次
            logger.warning("Moderator returned empty — retrying")
            fallback = llm.invoke(
                f"Summarize the bull vs bear debate for {symbol}. Bull: {bull_history[:1000]} Bear: {bear_history[:1000]}"
            )
            result = fallback.content if hasattr(fallback, "content") else str(fallback)

        logger.info("Moderator done.")

        # 返回状态更新:
        #   discussion_summary —— 辩论裁决文本,是"综合研判"节点的核心输入之一;
        #   messages           —— 追加到历史,便于前端/日志展示主持人结论。
        return {
            "discussion_summary": result,
            "messages": [HumanMessage(content=f"[Debate Moderator]\n{result}")],
        }

    return node
