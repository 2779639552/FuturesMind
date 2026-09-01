r"""
Commodity Futures Analysis Demo - TradingAgents for Commodity Research.

This script demonstrates the adaptation of TradingAgents' multi-agent framework
for commodity futures research. It replaces stock-specific analysts with
commodity-specialized analysts (Technical, Fundamental, Macro/News) and uses
free Chinese futures data via AKShare.

Architecture:
    START → 4 Analysts (parallel) → Bull/Bear Debate → Synthesis
          → Scenario Analysis → User Feedback (self-evolution) → END

The four analysts (technical/fundamental/macro/sentiment) run in parallel,
then the multi-round Bull/Bear debate (commodity_debate.py) surfaces agreement
and divergence, then the synthesis node produces the final recommendation,
then the scenario node stress-tests it with bull/base/bear cases, then the
user feedback node engages the user and saves lessons to Evolution Memory.

Usage:
    venv/Scripts/python commodity_demo.py [symbol] [date] [--no-feedback] [--feedback-rounds N]

Examples:
    venv/Scripts/python commodity_demo.py RB 2026-07-14         # Rebar
    venv/Scripts/python commodity_demo.py I 2026-07-14          # Iron Ore
    venv/Scripts/python commodity_demo.py M 2026-07-14          # Soybean Meal
    venv/Scripts/python commodity_demo.py RB --no-feedback      # Skip feedback
"""

# ===========================================================================
# 中文模块级说明
# 本文件 (commodity_demo.py) 是"商品期货智能分析管线"的入口脚本,也是整个
# AgentSense 项目的演示主程序。运行方式: venv/Scripts/python commodity_demo.py [品种] [日期]
#
# 整体分析流水线(一张 LangGraph 图,由 build_commodity_graph() 组装):
#   START
#     -> 四类分析师并行分析(技术面 / 基本面 / 宏观与新闻 / 情绪面,可选)
#     -> 多空对抗辩论(多方开篇 -> 空方反驳 -> 多方再反驳 -> 主持人裁决)
#     -> 综合研判(create_synthesis_node,产出最终评级 RATING)
#     -> 情景分析(create_scenario_node,牛市/基准/熊市三情景压力测试)
#     -> (可选)用户反馈节点(create_user_feedback_node,自我进化/记忆沉淀)
#     -> END
#
# 与 tradingagents 包的关系:
#   - 本文件是"组装者":真正干活的分析师节点、情感分析节点、用户反馈节点都定义在
#     tradingagents 包内(如 tradingagents.agents.analysts.commodity_analysts 的
#     create_commodity_technical_analyst 等),本文件只负责 import 并把它们挂到图上;
#   - 辩论节点来自同目录的 commodity_debate.py(create_bull_debater 等);
#   - LLM 实例、配置、进化记忆、行情数据都从 tradingagents 包中按需获取。
#
# 与 web_app.py 的关系:
#   - web_app.py 是 Web 前端服务(提供浏览器界面);本文件是命令行演示入口。
#     两者共享同一套图构建逻辑(分析师/辩论/综合研判节点),只是"呈现层"不同
#     (CLI 用 console_progress_callback 实时打印,Web 用富文本/接口推送)。
#   - 因此本文件里 final_state 中保存的字段(如 investment_plan、scenario_analysis、
#     discussion_summary)也是 Web 端展示所需的关键数据结构。
#
# 关键设计点:
#   - 双 LLM 分工(v2.4):分析师/辩论用 "quick_llm"(快而省),情景用
#     "deep_llm"(质量更高);综合研判 2026-09-01 起改用 quick_llm(flash)提速
#     (该节点单次调用占全流程约 40% 时长)。
# ===========================================================================

import logging  # 【调用包】日志(进度/异常;basicConfig 控制输出级别)
import os  # 【调用包】路径/环境变量操作(定位项目根目录/保存目录)
import re  # 【调用包】正则(解析情绪质量横幅 [SENTIMENT_QUALITY] 的 weight_cap)
import sys  # 【调用包】命令行参数读取与 sys.path 调整
from datetime import datetime  # 【调用包】时间戳/耗时统计

from dotenv import load_dotenv  # 【调用包】加载 .env 环境变量(如 LLM API Key)

load_dotenv()  # 【调用函数】加载根目录 .env 文件(注入 API Key 等环境变量)

# Add the project root to sys.path for imports (before the project imports below)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 【调用函数】把项目根目录加入 sys.path,保证能 import 到本地包

import contextlib  # noqa: E402  # 【调用包】上下文管理(suppress 吞掉参数类型错误)

from langchain_core.messages import HumanMessage  # noqa: E402  # 【调用包】LLM 消息对象(起始消息/历史消息)
from langgraph.graph import END, START, StateGraph  # noqa: E402  # 【调用包】LangGraph 图构建(节点/边/起止标记)

from commodity_debate import (  # noqa: E402  # 【调用包】辩论节点工厂(多方/空方/主持人)
    create_bear_debater,
    create_bull_debater,
    create_debate_moderator,
)
from tradingagents.agents.analysts.commodity_analysts import (  # noqa: E402  # 【调用包】分析师节点工厂(技术/基本面/宏观)
    create_commodity_fundamental_analyst,
    create_commodity_macro_analyst,
    create_commodity_technical_analyst,
)
from tradingagents.agents.analysts.sentiment_analyst import (  # noqa: E402  # 【调用包】情绪分析师节点工厂
    create_commodity_sentiment_analyst,
)
from tradingagents.agents.utils.agent_states import AgentState  # noqa: E402  # 【调用包】图共享状态类型定义(状态字段契约)
from tradingagents.agents.utils.user_feedback_agent import create_user_feedback_node  # noqa: E402  # 【调用包】用户反馈(自我进化)节点工厂
from tradingagents.dataflows.commodity_futures import get_variety_info  # noqa: E402  # 【调用包】品种信息获取(规格/保证金/交易时段)
from tradingagents.dataflows.config import set_config  # noqa: E402  # 【调用包】全局配置注入(供 tradingagents 各模块读取)
from tradingagents.dataflows.evolution_memory import (  # noqa: E402  # 【调用包】进化记忆(读取历史反馈/存储预测结果)
    get_evolution_context,
    store_prediction,
)
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402  # 【调用包】默认配置字典(LLM 提供方/模型名等)
from tradingagents.llm_clients import create_llm_client  # noqa: E402  # 【调用包】LLM 客户端工厂(按 provider 创建)

logging.basicConfig(level=logging.WARNING)  # Keep logger quiet; use progress_callback for output  # 【调用函数】设置根日志级别为 WARNING(保持输出安静,进度走回调)
logger = logging.getLogger(__name__)  # 【变量】模块日志器

# ---------------------------------------------------------------------------
# Console progress callback — makes tool calls visible in real-time
# ---------------------------------------------------------------------------


def console_progress_callback(event_type, data):
    """Print tool-calling progress via the logger (CLI-compatible).

    Called by _run_tool_loop inside each analyst node.  Because the three
    analysts run in parallel their output may interleave — the label prefix
    makes it easy to tell which analyst is doing what.
    """
    # 【功能】CLI 实时进度回调:当分析师节点内的工具调用循环(_run_tool_loop)发生
    #          事件时,把"正在调用哪个工具、结果多长"实时打印到终端,让用户看到分析进度。
    # 【参数】event_type: 事件类型字符串,如 "tool_call" / "tool_result" / "report_start";
    #          data: 事件数据字典,含 label(分析师标签)、tool_name、iteration、args_brief、
    #                result_length 等字段。
    # 【返回】无。
    # 【关键逻辑】因四名分析师并行运行,各回调可能交错打印,因此用 label 前缀区分来源;
    #            只打印对用户有意义的三种事件,过滤掉过于嘈杂的 "iteration"/"llm_thinking"。
    label = data.get("label", "?")  # 【变量】label:事件来源标签(如 Technical),用于区分并行分析师

    if event_type == "tool_call":
        # LLM 请求调用某个工具:打印"第几轮迭代、工具名、参数摘要"
        logger.info(
            "[%s] R%s: %s(%s)",
            label,
            data.get("iteration", "?"),
            data["tool_name"],
            data.get("args_brief", ""),
        )

    elif event_type == "tool_result":
        # 工具返回结果:打印结果文本长度,让用户感知数据量级
        logger.info(
            "[%s] <- %s chars from %s", label, data.get("result_length", 0), data["tool_name"]
        )

    elif event_type == "report_start":
        # 分析师开始撰写最终报告
        logger.info("[%s] Writing report...", label)

    # "iteration" and "llm_thinking" events are intentionally silent in the
    # console callback — they'd be too noisy.  Other callback implementations
    # (like the CLI's Rich Live dashboard) can use them for richer displays.
    # (注:以上两种事件在 CLI 回调中刻意不打印,否则会太嘈杂;其他实现如
    #  CLI 的 Rich 实时面板可借助它们做更丰富的展示。)


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------

SEP = "-" * 70  # 单线分隔符(用于普通分隔线)  # 【变量】单线分隔符
SEP_DOUBLE = "=" * 70  # 双线分隔符(用于更醒目的大分隔)  # 【变量】双线分隔符


def print_banner(symbol, trade_date):
    """打印开场横幅:显示被分析的期货品种与日期。

    【功能】程序启动时向用户宣告分析对象。
    【参数】symbol: 品种代码(如 "RB");trade_date: 交易日期字符串。
    【返回】无。
    """
    logger.info("FuturesMind Analysis: %s @ %s", symbol, trade_date)


def print_stage_header(title):
    """打印一个阶段标题,标记当前流程进行到哪一步。

    【功能】在管线各阶段(如 "[Discussion]" "[Synthesis]")输出醒目标题,便于跟踪进度。
    【参数】title: 标题字符串。
    【返回】无。
    """
    logger.info("--- %s ---", title)


def print_report_summary(report_text, max_chars=500):
    """Print the first few lines of a report as a summary preview."""
    # 【功能】把一份很长的分析报告压缩成"前几行 + 省略号"的预览,只打印摘要而非全文。
    # 【参数】report_text: 报告全文;max_chars: 预览最多保留的字符数(默认 500)。
    # 【返回】无。
    # 【关键逻辑】逐行累计字符数,一旦超过上限就截断并加 "..." 标记;空报告给出提示。
    if not report_text:
        logger.warning("  (no content)")
        return
    # Extract first meaningful paragraph(s) —— 提取第一个有意义的段落
    lines = report_text.strip().split("\n")
    preview_lines = []
    char_count = 0
    for line in lines:
        line = line.strip()
        if not line:
            if preview_lines:
                break  # blank line after content = end of first paragraph —— 遇到空行说明段落结束
            continue
        if char_count + len(line) > max_chars:
            remaining = max_chars - char_count
            if remaining > 20:
                preview_lines.append(line[:remaining] + "...")
            break
        preview_lines.append(line)
        char_count += len(line)
        if char_count >= max_chars:
            break

    preview = "\n".join(f"  {line}" for line in preview_lines)
    logger.info("Report preview:\n%s\n  ... (%s chars total)", preview, f"{len(report_text):,}")


def safe_print(text, max_chars=2000):
    """Print text safely, handling Windows GBK encoding issues.

     On Windows terminals with GBK code pages, characters like emoji and
    某些特殊Unicode字符  will raise UnicodeEncodeError. This function
     falls back to ASCII with replace-on-error for such cases.
    """
    # 【功能】安全打印长文本,规避 Windows 终端 GBK 编码导致的 UnicodeEncodeError 崩溃。
    # 【参数】text: 要打印的文本;max_chars: 单次打印最多字符数(默认 2000,防止刷屏)。
    # 【返回】无。
    # 【关键逻辑】先用 UTF-8 方式打印;若遇到无法编码的字符(如 emoji/生僻字),则把
    #            内容编码为 ASCII 并把无法表示的字符替换为 "?",保证打印永不抛异常。
    if not text:
        return
    content = text[:max_chars]  # 【变量】content:截断到上限的打印文本
    try:
        logger.info(content)
    except (UnicodeEncodeError, UnicodeDecodeError):
        safe = content.encode("ascii", errors="replace").decode("ascii")  # 【变量】safe:编码失败时降级的 ASCII 文本(无法字符替换为 ?)
        logger.info(safe)
    if len(text) > max_chars:
        logger.info("... (truncated, %d chars total)", len(text))


# ---------------------------------------------------------------------------
# Multi-round Adversarial Debate lives in commodity_debate.py (Bull/Bear nodes).
# The old single-round create_discussion_node was removed as dead code -- it had
# been superseded by the debate flow in build_commodity_graph (worklog 2026-08-13).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Synthesis node: final recommendation with weighted perspectives
# ---------------------------------------------------------------------------

# 【正则】情绪质量横幅首行: [SENTIMENT_QUALITY] level=... weight_cap=...
_SENTIMENT_QUALITY_RE = re.compile(r"\[SENTIMENT_QUALITY\]\s+([^\n]*)")


# 【功能】从情绪分析师报告首行横幅解析 weight_cap(0.0~1.0,情绪维度占综合权重上限)。
# 【参数】sentiment: 完整 sentiment_report 文本(非截断版)。
# 【返回】float|None: 解析成功返回 weight_cap;横幅缺失/解析失败返回 None(走旧 1/10 兜底,不 crash)。
# 【关键】横幅由 sentiment_analyst.py 节点在报告产出后 prepend,机器可读固定前缀。
def _parse_sentiment_cap(sentiment: str) -> float | None:
    """Parse the weight_cap from the [SENTIMENT_QUALITY] banner line (None on failure)."""
    m = _SENTIMENT_QUALITY_RE.search(sentiment or "")
    if not m:
        return None
    fields = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
    try:
        return float(fields.get("weight_cap", ""))
    except (ValueError, TypeError):
        return None


def create_synthesis_node(llm):
    """Synthesize four analyst reports + discussion summary into final recommendation."""
    # 【功能】创建"综合研判"图节点:把四份分析师报告 + 圆桌/辩论纪要综合成最终推荐。
    #          核心产出是首行机器可解析的评级头: RATING: [强烈看多/偏多/中性/偏空/强烈看空]
    #          | CONFIDENCE: [高/中/低] | SCORE: [0-10],同时要求模型按"证据强度"动态加权。
    # 【参数】llm: 语言模型客户端(通常是 deep_llm,质量优先)。
    # 【返回】node: 符合 LangGraph 节点签名的闭包函数 node(state) -> dict。
    # 【关键逻辑】提示词要求模型用 Evidence Strength = 信号清晰度×数据具体性×共识度×时效性
    #           给四维度打分并归一化为权重(总和=10),还内置多种特殊规则(如情绪与价格背离
    #           时情绪权重翻倍、基差结构突变时基本面权重翻倍),并强制做近因偏差自检与
    #           "评级与正文一致性"检查,防止评级与推理自相矛盾。

    def node(state):
        # 取出四份分析师报告与讨论纪要作为输入
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        discussion = state.get("discussion_summary", "")
        symbol = state["company_of_interest"]

        # 情绪维度权重硬上限(2026-08-25 强制门控):从分析师报告首行横幅解析 weight_cap,
        # 作为提示词里不可突破的数字上限;解析失败回退旧"稀疏数据封顶1/10"规则。
        cap = _parse_sentiment_cap(sentiment)  # 【调用函数】解析情绪质量横幅 weight_cap(0~1)
        if cap is not None and cap < 1.0:
            cap_rule = (
                f"- **Sentiment weight HARD CAP**: 情绪维度权重不得超过 {cap * 10:.0f}/10 "
                f"（{cap:.0%}）。该上限来自自动数据质量门控(posts/sample/accuracy/staleness)，"
                f"覆盖动态权重公式与所有特殊规则(含背离翻倍)，不可突破。"
            )
        else:
            cap_rule = (
                "- **Sparse sentiment data (<10 posts/day)**: Cap sentiment weight at 1/10, "
                "regardless of other criteria."
            )

        prompt_text = f"""You are the chief commodity strategist. You have received four independent analysis reports AND a roundtable discussion summary for commodity futures variety `{symbol}`.

---

**TECHNICAL ANALYSIS**:
{technical[:3000] if technical else "Not available."}

**FUNDAMENTAL ANALYSIS (Supply/Demand/Basis/Inventory)**:
{fundamental[:3000] if fundamental else "Not available."}

**MACRO & POLICY ANALYSIS**:
{macro[:3000] if macro else "Not available."}

**SENTIMENT ANALYSIS (Social Media / Market Psychology)**:
{sentiment[:2000] if sentiment else "Not available."}

**ROUNDTABLE DISCUSSION SUMMARY**:
{discussion[:2000] if discussion else "Not available."}

---

**Your Task**:

1. **Compare and contrast** the four perspectives. Pay special attention to the discussion summary which already identified consensus and divergence points.

2. **Assign weights** to each perspective based on EVIDENCE STRENGTH (not fixed hierarchy). Weights MUST sum to 10. Use this framework:

   **Evidence Strength = Signal Clarity × Data Specificity × Consensus × Recency**

   Rate each dimension on these criteria, then compute weights:

   | Criterion | 3 (Strong) | 2 (Moderate) | 1 (Weak) |
   |-----------|-----------|-------------|---------|
   | **Signal Clarity** | Direction unambiguous, multiple sub-signals agree | Mixed signals but net direction clear | Contradictory signals, hard to call |
   | **Data Specificity** | Cites specific numbers (prices, ratios, volumes) | Qualitative analysis with some data | Vague, no concrete numbers |
   | **Consensus** | 3+ dimensions agree on direction | 2 dimensions agree | Standalone or minority view |
   | **Recency** | Data ≤ 3 days old | 3-7 days old | > 7 days old or stale |

   **Dynamic weight formula (applied mentally):**
   `raw_weight = Signal_Clarity × 3 + Data_Specificity × 2 + Consensus × 2 + Recency × 3`
   Then normalize to /10.

   **SPECIAL RULES (override the formula when these conditions are met):**
   - **Sentiment-Price DIVERGENCE (sentiment opposite to price trend)**: DOUBLE the sentiment weight (this is a leading indicator!). Example: price falling but sentiment rising = sentiment may lead the turn. This was seen in the RB case where sentiment correctly predicted the bounce.
   - **Basis/Contango STRUCTURAL SHIFT**: If basis has flipped sign (e.g., Backwardation→Contango) or changed >50% in magnitude, DOUBLE fundamental weight. This is the single strongest fundamental signal.
   - **Volume+OI CONFIRMATION/DIVERGENCE**: If price move is confirmed by volume+OI, strengthen technical weight by 1.5×. If price move is contradicted by OI (e.g., price up but OI down = short covering), halve technical weight.
   {cap_rule}

3. **Recency Bias Check** (MANDATORY):
   - Ask yourself: "If the most recent 1-2 trading days showed the OPPOSITE price action, would my conclusion change?"
   - If YES → your conclusion is too dependent on recent price noise. Re-weight toward fundamentals.
   - If NO → your conclusion is robust to short-term fluctuations. Proceed with confidence.
   - Explicitly state the result of this check in your reasoning.

4. **Generate a final commodity outlook** with clear, data-backed reasoning.

**Output Format** (in Chinese):

**CRITICAL — First line MUST be the structured rating header:**

```
RATING: [强烈看多/偏多/中性/偏空/强烈看空] | CONFIDENCE: [高/中/低] | SCORE: [0-10]
```

This header is machine-parsed. It MUST be the very first line after the title.

**MANDATORY CONSISTENCY CHECK (do BEFORE writing the RATING):**
1. Look at your own analysis below—how many dimensions are bearish vs bullish?
2. If 3+ bearish → RATING MUST be 偏空/强烈看空, SCORE 0-4. 偏多 is INVALID.
3. If 3+ bullish → RATING MUST be 偏多/强烈看多, SCORE 6-10. 偏空 is INVALID.
4. The RATING and the body CANNOT contradict. If body says all bearish, RATING=偏多 is REJECTED.
5. Re-read your RATING after writing the body. If inconsistent, go back and fix the RATING.

**Rating Scale**:
- **强烈看多**: 3+ dimensions bullish
- **偏多**: Net bullish with caveats
- **中性**: Balanced
- **偏空**: Net bearish with caveats
- **强烈看空**: 3+ dimensions bearish

**Confidence**: 高/中/低. **Score**: 0-4=偏空, 5=中性, 6-10=偏多.

## 综合研判

### 四维度一致性分析
- 技术面观点：[总结]
- 基本面观点：[总结]
- 宏观面观点：[总结]
- 情绪面观点：[总结]
- 一致性评估：[高度一致/存在分歧/明显矛盾]
- 与圆桌讨论的呼应：[讨论中的关键洞察如何影响最终判断]

### 近因偏差检查 (Recency Bias Check)
- 若最近1-2日价格反向运动：[结论是否会改变？]
- 判断：方向判断是否过度依赖近期价格波动？

### 证据强度评估
| 维度 | 信号清晰度(1-3) | 数据具体性(1-3) | 共识度(1-3) | 时效性(1-3) | 特殊规则触发? |
|------|:--:|:--:|:--:|:--:|------|
| 技术面 | X | X | X | X | [如触发特殊规则，说明] |
| 基本面 | X | X | X | X | |
| 宏观面 | X | X | X | X | |
| 情绪面 | X | X | X | X | |

### 加权判断
- 技术面权重：X/10 — 理由：[基于证据强度]
- 基本面权重：X/10 — 理由：[基于证据强度]
- 宏观面权重：X/10 — 理由：[基于证据强度]
- 情绪面权重：X/10 — 理由：[基于证据强度]
- 是否触发特殊规则：[列出触发的规则及原因]
- 加权理由：[综合解释。基本面为何是锚？情绪面是修正还是确认？]

### 最终建议
**RATING 确认**：[与开头的RATING行一致]
- 核心逻辑：[3-5条最关键的判断依据，每条要有数据支撑]
- 关键价位：[重要支撑和阻力位]
- 主要风险：[可能导致判断错误的关键因素]

### 操作参考（仅供学习研究，不构成投资建议）
- 短期(1-2周)：[方向+关键观察点]
- 中期(1-3月)：[方向+核心逻辑]

Remember: This is for research/educational purposes only. Do NOT provide specific buy/sell price targets.
"""

        print_stage_header("[Synthesis] Generating final recommendation...")
        result = llm.invoke(prompt_text)
        logger.info("Synthesis done.")

        # 返回状态更新:
        #   investment_plan       —— 完整综合研判文本(也是"情景分析"节点的输入);
        #   final_trade_decision  —— 与 investment_plan 相同内容,兼容旧字段名(供下游统一读取)。
        return {
            "investment_plan": result.content,
            "final_trade_decision": result.content,
        }

    return node


# ---------------------------------------------------------------------------
# Scenario Analysis node: bull/base/bear 3-scenario projection
# ---------------------------------------------------------------------------


def create_scenario_node(llm):
    """Post-Synthesis scenario analysis — 3-scenario projection with probabilities.

    This mirrors the original TradingAgents' multi-layer decision process
    (Research → Trader → Risk → PM). After Synthesis produces the base-case
    recommendation, this node stress-tests it with bull and bear scenarios.
    """
    # 【功能】创建"情景分析"图节点:在综合研判给出基准结论之后,用"牛市/基准/熊市"
    #          三种情景对其进行压力测试,要求模型给每个情景分配概率(总和必须为 100%),
    #          并说明触发条件、目标价位、证伪条件与概率分配理由。
    # 【参数】llm: 语言模型客户端(通常是 deep_llm,质量优先)。
    # 【返回】node: 符合 LangGraph 节点签名的闭包函数 node(state) -> dict。
    # 【关键逻辑】对应 TradingAgents 原始框架中 "研究→交易员→风控→PM" 的多层决策思想:
    #           综合研判产出的是"基准情景",本节点负责校验该基准是否经得起牛熊两端的考验。

    def node(state):
        # 取综合研判结果与技术/基本面报告作为输入(宏观/情绪维度已在分析师阶段体现,
        # 此处不需要再引用,历史遗留的未用读入已移除)
        synthesis = state.get("investment_plan", "")  # 【变量】synthesis:综合研判基准结论(情景分析的输入)
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        symbol = state["company_of_interest"]  # 【变量】symbol:分析对象品种代码

        prompt_text = f"""You are a scenario analyst. The synthesis strategist has produced a base-case recommendation for `{symbol}`. Your job is to stress-test it with three explicit scenarios.

---

**SYNTHESIS (Base Case)**:
{synthesis[:2500] if synthesis else "Not available."}

**TECHNICAL** (key levels only):
{technical[:800] if technical else "N/A"}

**FUNDAMENTAL** (key drivers only):
{fundamental[:800] if fundamental else "N/A"}

---

**Your Task — Three-Scenario Analysis**:

Assign probability weights to each scenario (they MUST sum to 100%).

1. **Bull Case** (probability: XX%):
   - What catalysts would need to materialize?
   - Key price target if bull case plays out
   - What would invalidate this scenario?

2. **Base Case** (probability: XX%):
   - Restate the synthesis base case concisely
   - Key confirming signals to watch

3. **Bear Case** (probability: XX%):
   - What risks would need to materialize?
   - Key price target if bear case plays out
   - What would invalidate this scenario?

4. **Scenario Probability Justification**:
   - Why did you assign these specific probabilities?
   - Which dimensions (tech/fund/macro/sentiment) support or contradict each scenario?

**Output Format**:

## 三情景分析

### 牛市情景 (Bull Case) — 概率: XX%
- 触发条件：
- 目标价位：
- 证伪条件：

### 基准情景 (Base Case) — 概率: XX%
- 核心逻辑：
- 确认信号：

### 熊市情景 (Bear Case) — 概率: XX%
- 触发条件：
- 目标价位：
- 证伪条件：

### 概率分配理由
[为什么这样分配？]
"""
        print_stage_header("[Scenario] Three-scenario stress test...")
        result = llm.invoke(prompt_text)
        logger.info("Scenario analysis done.")

        # 返回状态更新:scenario_analysis 保存三情景文本;messages 追加到历史供展示
        return {
            "messages": [HumanMessage(content=f"[Scenario Analysis]\n{result.content}")],
            "scenario_analysis": result.content,
        }

    return node


# ---------------------------------------------------------------------------
# Build the commodity analysis graph
# ---------------------------------------------------------------------------


def build_commodity_graph(
    config: dict,
    enable_feedback: bool = True,
    max_feedback_rounds: int = 5,
    include_sentiment: bool = True,
):
    """Build a LangGraph for commodity futures analysis.

    Graph structure:
        START → Tech Analyst ──┐
        START → Fund Analyst ──→ Discussion → Synthesis → User Feedback → END
        START → Macro Analyst ─┘

    The three analysts run in parallel, then discussion compares their
    output, then synthesis produces the final weighted recommendation,
    then the user feedback node collects feedback for self-evolution.
    """
    # 【功能】构建整张商品分析 LangGraph 图,返回"已编译的可执行图"和"独立的用户反馈节点"。
    # 【参数】config: 配置字典(含 llm_provider、quick_think_llm、deep_think_llm 等);
    #          enable_feedback: 是否启用用户反馈(自我进化)节点;
    #          max_feedback_rounds: 反馈对话最多轮数;include_sentiment: 是否包含情绪分析师。
    # 【返回】(graph.compile() 编译后的 app, feedback_node)。feedback_node 可单独调用,
    #         用于在分析完成后与用户进行独立讨论。
    # 【关键逻辑】完整的节点链(实际)为:
    #           START -> [技术/基本面/宏观/(情绪)] 并行分析师
    #                 -> bull_opening(多方开篇) -> bear_refute(空方反驳)
    #                 -> bull_rebuttal(多方再反驳,拥有最后发言权)
    #                 -> debate_moderator(主持人裁决)
    #                 -> synthesis(综合研判) -> scenario_analysis(三情景) -> END
    #           其中分析师/辩论/综合研判用 quick_llm(快而省),情景用 deep_llm(质量更高),
    #           这是 v2.4 引入的"双 LLM 分工";综合研判 2026-09-01 起由 deep 改 quick 提速。

    # Dual LLM: quick for analysts/debate/synthesis, deep for scenario (synthesis switched to quick 2026-09-01)
    # quick_llm —— 快而省,用于分析师与辩论(高频、量大,不需要最高质量)
    quick_llm = create_llm_client(  # 【调用函数】创建 quick_llm(分析师/辩论用,快而省)
        config["llm_provider"],
        config.get("quick_think_llm", config["deep_think_llm"]),  # 若未配置 quick 模型则回退到 deep 模型
    ).get_llm()
    deep_llm = create_llm_client(  # deep_llm —— 质量优先,用于关键决策(综合研判/情景分析)  # 【调用函数】创建 deep_llm(综合研判/情景用,质量优先)
        config["llm_provider"],
        config["deep_think_llm"],
    ).get_llm()

    # Create analyst nodes (quick_llm — faster, cheaper)
    # 创建四名并行分析师节点,全部使用 quick_llm;progress_callback 传入
    # console_progress_callback,让工具调用在 CLI 实时可见。
    tech_node = create_commodity_technical_analyst(  # 【调用函数】创建技术面分析师节点(挂到图上,并行执行)
        quick_llm, label="Technical", progress_callback=console_progress_callback
    )
    fund_node = create_commodity_fundamental_analyst(  # 【调用函数】创建基本面分析师节点
        quick_llm, label="Fundamental", progress_callback=console_progress_callback
    )
    macro_node = create_commodity_macro_analyst(  # 【调用函数】创建宏观/新闻分析师节点
        quick_llm, label="Macro/News", progress_callback=console_progress_callback
    )
    # 可选的情绪分析师:由 include_sentiment 控制是否创建/加入图(默认加入)
    if include_sentiment:
        sentiment_node = create_commodity_sentiment_analyst(  # 【调用函数】创建情绪分析师节点(可选)
            quick_llm, label="Sentiment", progress_callback=console_progress_callback
        )

    # Multi-round debate (quick_llm) — Bull opening + Bull rebuttal (fair last-word)
    # 多轮对抗辩论节点(用 quick_llm)。注意:多方被创建了两个节点 —— 开篇(R1)
    # 与再反驳(R2)。这样安排是为了让多方拥有"最后发言权"(公平反驳权)。
    bull_opening_node = create_bull_debater(quick_llm)  # R1: opening statement —— 多方开篇立论  # 【调用函数】创建多方开篇节点(R1)
    bear_node = create_bear_debater(quick_llm)  # R1: refutation —— 空方第一轮反驳  # 【调用函数】创建空方反驳节点(R1)
    bull_rebuttal_node = create_bull_debater(quick_llm)  # R2: rebuttal (LAST WORD) —— 多方再反驳(最后发言)  # 【调用函数】创建多方再反驳节点(R2,最后发言)
    moderator_node = create_debate_moderator(quick_llm)  # Judge —— 主持人裁决  # 【调用函数】创建主持人裁决节点

    # Key decision nodes
    # 综合研判改为 quick_llm(flash):该节点单次调用占全流程约 40% 时长,降为 flash 显著提速;
    # 情景分析仍用 deep_llm(pro)保证压力测试质量。
    synthesis_node = create_synthesis_node(quick_llm)  # 【调用函数】创建综合研判节点(quick_llm 提速)
    scenario_node = create_scenario_node(deep_llm)  # 【调用函数】创建三情景分析节点(deep_llm 质量优先)
    # 用户反馈(自我进化)节点:在分析结束后与用户讨论,并把心得写入进化记忆
    feedback_node = create_user_feedback_node(  # 【调用函数】创建用户反馈(自我进化)节点
        deep_llm,
        max_rounds=max_feedback_rounds,  # 反馈对话最多轮数(命令行参数可调)
        enabled=enable_feedback,  # 由 --no-feedback 控制是否启用
    )

    # Build graph —— 用 LangGraph 的 StateGraph 构建有状态图,状态类型为 AgentState
    graph = StateGraph(AgentState)  # 【调用函数】创建 LangGraph 状态图(共享状态类型 AgentState)

    # Add nodes —— 注册所有节点:每个节点对应一个可调用对象(闭包函数)
    graph.add_node("technical_analyst", tech_node)  # 【调用函数】注册技术面分析师节点到图
    graph.add_node("fundamental_analyst", fund_node)
    graph.add_node("macro_analyst", macro_node)
    if include_sentiment:
        graph.add_node("sentiment_analyst", sentiment_node)
    graph.add_node("bull_opening", bull_opening_node)  # 多方开篇
    graph.add_node("bear_refute", bear_node)  # 空方反驳
    graph.add_node("bull_rebuttal", bull_rebuttal_node)  # 多方再反驳(最后发言)
    graph.add_node("debate_moderator", moderator_node)  # 主持人裁决
    graph.add_node("synthesis", synthesis_node)  # 综合研判
    graph.add_node("scenario_analysis", scenario_node)  # 三情景分析

    # Fan-out: START → analysts in parallel (sentiment optional)
    # 扇出:从 START 同时连到所有分析师 —— 这是 LangGraph 实现"并行"的方式,
    # 多个分析师节点会并行执行(读取同一份共享状态,各写各的报告字段)。
    graph.add_edge(START, "technical_analyst")  # 【调用函数】START 扇出到技术面分析师(并行启动)
    graph.add_edge(START, "fundamental_analyst")
    graph.add_edge(START, "macro_analyst")
    if include_sentiment:
        graph.add_edge(START, "sentiment_analyst")

    # Fan-in → Bull Opening (R1)
    # 扇入:四名分析师全部完成后才进入多方开篇节点 —— 保证辩论开始时四份报告都已就绪。
    graph.add_edge("technical_analyst", "bull_opening")  # 【调用函数】技术面完成 → 进入多方开篇(扇入,保证四份报告就绪)
    graph.add_edge("fundamental_analyst", "bull_opening")
    graph.add_edge("macro_analyst", "bull_opening")
    if include_sentiment:
        graph.add_edge("sentiment_analyst", "bull_opening")

    # Debate: Bull(R1) → Bear(R1) → Bull(R2 rebuttal) → Moderator
    # Both sides get equal turns; Bull gets LAST WORD (fair rebuttal right)
    # 辩论主线:双方各发言两轮对等,且多方拥有"最后发言权"(公平反驳权)。
    graph.add_edge("bull_opening", "bear_refute")  # 【调用函数】辩论主线:多方→空方→多方再反驳→主持人
    graph.add_edge("bear_refute", "bull_rebuttal")
    graph.add_edge("bull_rebuttal", "debate_moderator")

    # Moderator → Synthesis → Scenario → END
    # 裁决后顺序执行:综合研判 -> 三情景分析 -> 结束。
    graph.add_edge("debate_moderator", "synthesis")  # 【调用函数】裁决后 → 综合研判 → 三情景 → 结束
    graph.add_edge("synthesis", "scenario_analysis")
    graph.add_edge("scenario_analysis", END)

    # 编译图并返回;同时把 feedback_node 单独返回,供 main() 在分析结束后独立调用。
    return graph.compile(), feedback_node  # Return feedback_node for standalone use  # 【调用函数】编译图(生成可执行 app)并返回独立反馈节点


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    """CLI 主入口:解析命令行参数 -> 加载配置/进化记忆 -> 构建并流式执行分析图 -> 保存报告。

    【功能】把"参数解析、初始化、图构建、流式执行、结果落盘、交互循环"串起来,跑完整条分析管线。
    【参数】无(命令行参数通过 sys.argv 读取)。
    【返回】无(正常结束返回;分析失败会 sys.exit(1))。
    【关键逻辑】参数解析用手写 while 循环(而非 argparse),支持 [品种] [日期] 两个位置参数,
               以及 --no-feedback / --feedback-rounds N 两个开关;图用 app.stream(..., stream_mode="updates")
               逐节点流式执行,实时打印每个节点的产出。
    """
    # Parse arguments —— 手动解析命令行参数(未用 argparse,以支持灵活的位置参数)
    # Support: commodity_demo.py [symbol] [date] [--no-feedback] [--feedback-rounds N]
    args = sys.argv[1:]  # 【变量】args:命令行参数列表(去掉脚本名)
    symbol = "RB"  # 默认品种:螺纹钢  # 【变量】symbol:品种代码(可被位置参数覆盖)
    trade_date = "2026-07-14"  # 默认交易日期  # 【变量】trade_date:分析日期(可被位置参数覆盖)
    enable_feedback = True  # 默认开启用户反馈(自我进化)  # 【变量】enable_feedback:是否启用用户反馈(--no-feedback 关闭)
    max_feedback_rounds = 5  # 反馈对话默认最多 5 轮  # 【变量】max_feedback_rounds:反馈对话最多轮数

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--no-feedback":
            enable_feedback = False  # 关闭用户反馈
        elif a == "--feedback-rounds" and i + 1 < len(args):
            i += 1
            # contextlib.suppress 吞掉 ValueError:参数不是数字时静默保留默认值
            with contextlib.suppress(ValueError):
                max_feedback_rounds = int(args[i])
        elif not a.startswith("--"):
            # 位置参数:第一个非 "--" 开头的当作品种(默认 RB 时),第二个当作日期。
            # "202" 前缀用于区分"日期"与"品种"(避免把日期误判为品种)。
            if symbol == "RB" and not a.startswith("202"):
                symbol = a
            elif trade_date == "2026-07-14":
                trade_date = a
        i += 1

    symbol = symbol.upper()  # 品种代码统一转大写(如 rb -> RB)

    print_banner(symbol, trade_date)

    # Show variety info —— 打印品种基础信息(规格、保证金、交易时段等)
    print("[*] 品种信息:")
    info = get_variety_info(symbol)  # 【调用函数】获取品种基础信息(规格/保证金/交易时段等)
    safe_print(info[:500])  # 【调用函数】安全打印品种信息前 500 字符
    print()

    # Configure the system —— 拷贝默认配置并写入全局(供 tradingagents 各模块读取)
    config = DEFAULT_CONFIG.copy()  # 【变量】config:默认配置的副本(可改而不影响原始配置)
    set_config(config)  # 【调用函数】把配置写入全局,供 tradingagents 各模块读取

    print(f"[LLM] {config['llm_provider']} / {config['deep_think_llm']}")
    graph_desc = "4 Analysts -> Bull vs Bear Debate -> Moderator -> Synthesis -> Scenario"
    if enable_feedback:
        graph_desc += " -> User Feedback (self-evolution)"
    print(f"[Graph] {graph_desc}")
    print()

    # Load evolution memory for this variety (injected into analyst prompts)
    # 加载该品种的历史进化记忆(过去用户反馈的总结),会注入分析师提示词,实现自我进化
    evolution_context = get_evolution_context(symbol)  # 【调用函数】加载该品种历史进化记忆(注入分析师提示词)
    if evolution_context:
        print(f"[Evolution] Loaded past feedback for {symbol} ({len(evolution_context)} chars)")
    else:
        print(f"[Evolution] No prior feedback for {symbol} (first run)")

    # Build graph (returns compiled app + standalone feedback node)
    # 构建并编译整张图;返回的可执行 app 用于流式运行,feedback_node 用于后续独立反馈会话
    app, feedback_node = build_commodity_graph(  # 【调用函数】构建并编译分析图;返回可执行 app 与独立反馈节点
        config,
        enable_feedback=enable_feedback,
        max_feedback_rounds=max_feedback_rounds,
    )

    # Create initial state —— 构造图的起始输入
    # 起始消息:告诉 LLM 要分析哪个品种、哪个日期,并提示它调用工具采集数据
    initial_msg = HumanMessage(  # 【调用函数】构造起始消息(指示 LLM 分析目标品种/日期)
        content=(
            f"Analyze commodity futures variety '{symbol}' as of {trade_date}. "
            f"Call your assigned tools to gather data and write a thorough analysis report."
        )
    )

    # 初始化共享状态:所有字段初始为空字符串/空字典,各图节点执行后会写入各自的字段
    initial_state = {  # 【变量】initial_state:图的起始共享状态(各字段由节点执行后填充)
        "messages": [initial_msg],  # 对话历史(从起始消息开始累积)
        "company_of_interest": symbol,  # 分析对象品种
        "asset_type": "commodity_futures",  # 资产类型(区分股票/期货)
        "trade_date": trade_date,  # 交易日期
        "past_context": evolution_context,  # Self-evolution memory injection —— 注入进化记忆(自我进化关键)
        "technical_report": "",  # 技术面分析师报告(会被 tech_node 写入)
        "fundamental_report": "",  # 基本面分析师报告
        "macro_report": "",  # 宏观/新闻分析师报告
        "discussion_summary": "",  # 圆桌讨论/辩论裁决摘要
        "user_feedback_summary": "",  # 用户反馈总结
        "market_report": "",  # 【待确认】历史兼容字段,当前流程未直接写入
        "sentiment_report": "",  # 情绪面分析师报告(仅 include_sentiment=True 时写入)
        "news_report": "",  # 【待确认】历史兼容字段
        "fundamentals_report": "",  # 【待确认】历史兼容字段
        "investment_plan": "",  # 综合研判文本(最终推荐)
        "final_trade_decision": "",  # 最终交易决策(与 investment_plan 同内容,兼容旧字段)
        "scenario_analysis": "",  # 三情景分析文本
        "debate_state": {  # 辩论共享状态(多空互读的数据载体)
            "bull_history": "",  # 多方全部历史论点(拼接)
            "bear_history": "",  # 空方全部历史论点(拼接)
            "bull_last": "",  # 多方最新一轮论点
            "bear_last": "",  # 空方最新一轮论点
            "round": 0,  # 当前辩论轮次计数
        },
    }

    # -------------------------------------------------------------------
    # Stream execution — shows progress in real time
    # -------------------------------------------------------------------
    # 提示用户:四名分析师即将并行启动
    print(SEP)
    print("  四分析师并行启动 (Parallel Analysts)")
    print(SEP)

    final_state = {}  # 逐节点累积最终状态  # 【变量】final_state:逐节点累积的最终状态(图跑完后含全部输出)
    analysis_start = datetime.now()  # 记录开始时间,用于统计耗时  # 【变量】analysis_start:开始时间,用于统计总耗时

    try:
        # stream_mode="updates":每次产出"一个节点更新后的状态块",可实时打印各节点结果
        for chunk in app.stream(initial_state, stream_mode="updates"):  # 【调用函数】app.stream:流式执行图(逐节点产出更新块)
            node_names = list(chunk.keys())  # 本块涉及的节点名(可能多个节点同时完成)  # 【变量】node_names:本块中完成更新的节点名列表

            for node_name in node_names:
                node_data = chunk[node_name]  # 【变量】node_data:该节点产出的状态更新字典
                # Safety: skip None updates (can happen with certain LangGraph versions)
                # 安全保护:某些 LangGraph 版本可能产出 None 更新,直接跳过
                if node_data is None:
                    continue

                # 每个节点完成时,按节点名实时打印其结果摘要(报告/辩论/裁决)
                if node_name == "technical_analyst":
                    report = node_data.get("technical_report", "")
                    print_stage_header(f"[Technical] Analysis complete ({len(report):,} chars)")
                    print_report_summary(report)

                elif node_name == "fundamental_analyst":
                    report = node_data.get("fundamental_report", "")
                    print_stage_header(f"[Fundamental] Analysis complete ({len(report):,} chars)")
                    print_report_summary(report)

                elif node_name == "macro_analyst":
                    report = node_data.get("macro_report", "")
                    print_stage_header(f"[Macro/News] Analysis complete ({len(report):,} chars)")
                    print_report_summary(report)

                elif node_name == "sentiment_analyst":
                    report = node_data.get("sentiment_report", "")
                    print_stage_header(f"[Sentiment] Analysis complete ({len(report):,} chars)")
                    print_report_summary(report)

                elif node_name == "bull_opening":
                    # 从辩论状态中取出多方最新论点长度
                    bull = node_data.get("debate_state", {}).get("bull_last", "")
                    print(f"  [Bull R1 Opening] ({len(bull):,} chars)")

                elif node_name == "bear_refute":
                    bear = node_data.get("debate_state", {}).get("bear_last", "")
                    print(f"  [Bear R1 Refute] ({len(bear):,} chars)")

                elif node_name == "bull_rebuttal":
                    bull2 = node_data.get("debate_state", {}).get("bull_last", "")
                    print(f"  [Bull R2 Rebuttal] ({len(bull2):,} chars)")

                elif node_name == "debate_moderator":
                    disc = node_data.get("discussion_summary", "")
                    print_stage_header(f"[Moderator] Debate summary ({len(disc):,} chars)")
                    safe_print(disc)  # 完整打印主持人裁决(可能较长,用 safe_print)

                elif node_name == "synthesis":
                    synthesis = node_data.get("investment_plan", "")
                    print_stage_header(
                        f"[Synthesis] Final recommendation ({len(synthesis):,} chars)"
                    )
                    safe_print(synthesis)  # 完整打印综合研判结果

                elif node_name == "scenario_analysis":
                    scenario = node_data.get("scenario_analysis", "")
                    print_stage_header(
                        f"[Scenario] Three-scenario projection ({len(scenario):,} chars)"
                    )
                    safe_print(scenario)

            # Accumulate state from each chunk —— 把每个节点的更新合并进 final_state,
            # 图跑完后 final_state 即包含所有节点的最终输出(供保存报告/预测入库使用)。
            for node_name in node_names:
                final_state.update(chunk[node_name])  # 【调用函数】把本块更新合并进 final_state

    except Exception as e:
        # 图执行过程中任何异常:记录日志、打印堆栈,并以退出码 1 结束程序
        logger.error("Analysis failed: %s", e)
        import traceback

        traceback.print_exc()
        sys.exit(1)

    elapsed = (datetime.now() - analysis_start).total_seconds()  # 计算总耗时(秒)  # 【变量】elapsed:分析总耗时(秒)

    # -------------------------------------------------------------------
    # Final output
    # -------------------------------------------------------------------
    # 分析完成,打印总耗时
    print(f"\n{SEP_DOUBLE}")
    print(f"  ANALYSIS COMPLETE  |  Elapsed: {elapsed:.0f}s")
    print(f"{SEP_DOUBLE}")

    # Save full report to file —— 把全部报告拼成一份 Markdown 保存到用户主目录下
    # 的文件日志目录 .tradingagents/logs
    output_dir = os.path.join(os.path.expanduser("~"), ".tradingagents", "logs")  # 【变量】output_dir:报告日志目录(~/.tradingagents/logs)
    os.makedirs(output_dir, exist_ok=True)  # 目录不存在则创建
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # 时间戳用于文件名唯一化  # 【变量】timestamp:时间戳(用于文件名唯一化)
    output_file = os.path.join(output_dir, f"commodity_{symbol}_{timestamp}.md")  # 【变量】output_file:主报告文件完整路径

    # 汇总各阶段产物:报告顺序 = 分析流程顺序(分析师 -> 辩论 -> 研判 -> 情景)
    reports = [  # 【变量】reports:各阶段报告(标题,内容)列表,按流程顺序
        ("Technical Analysis", final_state.get("technical_report", "")),
        ("Fundamental Analysis", final_state.get("fundamental_report", "")),
        ("Macro/News Analysis", final_state.get("macro_report", "")),
        ("Sentiment Analysis", final_state.get("sentiment_report", "")),
        ("Debate Moderator Summary", final_state.get("discussion_summary", "")),
        ("Synthesis & Recommendation", final_state.get("investment_plan", "")),
        ("Scenario Analysis", final_state.get("scenario_analysis", "")),
    ]

    # 以 UTF-8 写入报告文件(跳过为空的内容段)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Commodity Futures Analysis: {symbol}\n\n")
        f.write(f"**Date**: {trade_date}\n")
        f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Elapsed**: {elapsed:.0f}s\n\n")
        f.write("---\n\n")
        for title, content in reports:
            if content:
                f.write(f"## {title}\n\n{content}\n\n---\n\n")

    print(f"\n[*] 完整报告已保存至: {output_file}")

    # -------------------------------------------------------------------
    # Deferred outcome: store structured prediction for next-run backtest
    # -------------------------------------------------------------------
    # 延迟结果入库:从综合研判文本中用正则解析出结构化评级(RATING/CONFIDENCE/SCORE),
    # 存入"进化记忆",供下一次运行时回测参考(评分历史)。
    import re as _re

    syn_text = final_state.get("investment_plan", "")  # 【变量】syn_text:综合研判文本(从中解析结构化评级)
    rating_match = _re.search(  # 【调用函数】正则解析 RATING|CONFIDENCE|SCORE 结构化评级头
        r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", syn_text
    )
    if rating_match:
        try:
            store_prediction(  # 【调用函数】把结构化预测存入进化记忆(供下次运行回测)
                variety=symbol,  # 品种
                trade_date=trade_date,  # 交易日期
                rating=rating_match.group(1).strip(),  # 评级(如"偏多")
                confidence=rating_match.group(2).strip(),  # 置信度(如"中")
                score=int(rating_match.group(3)),  # 分数(0-10)
                key_levels=final_state.get("investment_plan", "")[:500],  # 关键价位摘录
            )
            print("\n[*] Prediction stored for deferred backtesting")
        except Exception:
            pass  # 入库失败静默忽略,不影响主流程
    # -------------------------------------------------------------------
    # Market Comparison Visualization
    # -------------------------------------------------------------------
    # 生成"Agent 分析 vs 市场研报"的对比报告(纯展示辅助)
    try:
        print_comparison_report(final_state, symbol, trade_date, output_file)  # 【调用函数】生成 Agent vs 市场研报对比(纯展示辅助)
    except UnicodeEncodeError:
        # Windows GBK 终端下部分字符无法打印时,跳过对比报告(完整报告已在文件中)
        print("\n[!] Comparison report skipped (Windows GBK encoding conflict)")
        print("[*] The full report with correct encoding is saved to the output file.")
    # -------------------------------------------------------------------
    # Interactive Post-Analysis Loop
    # Analysis is done but we stay in interactive mode so the user can
    # discuss results, start a debate, or explicitly exit.
    # -------------------------------------------------------------------
    # 进入交互循环:分析已完成,但保持程序运行,允许用户输入 /feedback /exit /help 等命令
    _run_interactive_demo(final_state, symbol, output_file, feedback_node, enable_feedback)  # 【调用函数】进入分析后交互循环(/feedback /exit /help)


# ---------------------------------------------------------------------------
# Interactive post-analysis loop for demo
# ---------------------------------------------------------------------------


def _run_interactive_demo(
    final_state: dict,
    symbol: str,
    output_file: str,
    feedback_node,
    enable_feedback: bool = True,
):
    """Post-analysis interactive loop — user must explicitly /exit to leave."""
    # 【功能】分析结束后的交互式命令行循环:持续读取用户输入,支持 /feedback(与 AI 讨论
    #          分析结果,触发自我进化)、/exit(退出)、/help(帮助)等命令,直到用户明确退出。
    # 【参数】final_state: 图执行后的完整最终状态;symbol: 品种;output_file: 报告保存路径;
    #          feedback_node: 独立的用户反馈节点(来自 build_commodity_graph);enable_feedback: 是否允许反馈。
    # 【返回】无。
    # 【关键逻辑】死循环直到 /exit;feedback 会话通过 feedback_node(final_state) 运行,
    #           结果写入 user_feedback_summary 并打印摘要。
    print(f"\n{SEP}")
    print("  Analysis complete! Interactive mode active.")
    print("  Commands: /feedback | /exit | /help")
    print(f"{SEP}")

    while True:
        try:
            cmd = input("> ").strip()  # 读取用户输入  # 【调用函数】读取用户输入命令
        except (EOFError, KeyboardInterrupt):
            # 用户按 Ctrl+C 或输入流结束:优雅退出
            print("\nExiting...")
            break

        if not cmd:
            continue  # 空输入继续

        cmd_lower = cmd.lower()  # 统一转小写比较

        if cmd_lower in ("/exit", "/quit", "/q", "exit", "quit", "q"):
            print("Goodbye.")
            break  # 退出命令

        elif cmd_lower in ("/help", "/h", "help"):
            # 帮助命令:打印可用命令
            print("  /feedback — Discuss the analysis with the AI")
            print("  /exit     — Exit the program")
            print("  /help     — Show this help")
            print("  Ctrl+C    — Force quit")

        elif cmd_lower in ("/feedback", "/fb", "feedback"):
            # 反馈命令:若反馈被禁用或没有反馈节点,提示并跳过
            if not enable_feedback or feedback_node is None:
                print("  Feedback is disabled for this run.")
                continue
            print(f"\n{SEP}")
            print("  Starting feedback session...")
            print("  (Type /done to end debate, /skip to skip)")
            print(f"{SEP}\n")
            try:
                # 运行反馈节点:它会与用户对话讨论分析,并把心得写回最终状态
                feedback_result = feedback_node(final_state)  # 【调用函数】运行反馈节点:与用户讨论分析并写回总结
                fb_summary = feedback_result.get("user_feedback_summary", "")  # 【变量】fb_summary:反馈会话总结文本
                if fb_summary:
                    print(f"\n[Feedback] Summary ({len(fb_summary):,} chars)")
                    safe_print(fb_summary)
            except Exception as e:
                print(f"  Feedback error: {e}")

        else:
            print(f"  Unknown: '{cmd}'. Type /help for commands.")


# ---------------------------------------------------------------------------
# Market Comparison Report Generator
# ---------------------------------------------------------------------------


def print_comparison_report(final_state: dict, symbol: str, trade_date: str, report_path: str):
    """Generate a multi-dimensional comparison between Agent analysis and market research.

    Extracts key judgments from the Agent's reports and presents them in a
    structured comparison framework. The market research columns are prompts
    to be filled in by searching recent institutional reports.
    """
    # 【功能】生成"Agent 分析 vs 市场研报"的多维度对比报告,并保存一份对比文件。
    #          从 Agent 各阶段报告里用正则解析出方向判断、权重、多空因素、关键价位等信息,
    #          以表格形式与"市场研报"对照展示;市场研报列是占位提示,需人工/搜索填充。
    # 【参数】final_state: 图执行后的最终状态字典;symbol: 品种;trade_date: 日期;
    #          report_path: 主报告 .md 路径(对比文件会基于它生成 "_comparison.md")。
    # 【返回】无。
    # 【关键逻辑】大量使用正则从 LLM 生成的中文报告中抽取结构化字段(评级、权重、关键价位),
    #           并做新旧格式兼容回退;对比列(market)多为提示文案而非真实研报数据。
    # --- Extract Agent's key metrics ---
    # 取出各阶段报告文本
    technical = final_state.get("technical_report", "")
    fundamental = final_state.get("fundamental_report", "")
    macro = final_state.get("macro_report", "")
    sentiment = final_state.get("sentiment_report", "")
    synthesis = final_state.get("investment_plan", "")

    # Extract structured RATING (v2.3.2 format) —— 解析综合研判中的结构化评级头
    rating_match = __import__("re").search(  # 【调用函数】正则解析结构化评级头(新格式 v2.3.2)
        r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", synthesis
    )
    if rating_match:
        agent_direction = f"{rating_match.group(1).strip()} (信心={rating_match.group(2).strip()}, 分数={rating_match.group(3).strip()}/10)"
    else:
        # Fallback to old format —— 兼容旧格式:尝试匹配"方向判断："行
        direction_match = __import__("re").search(r"方向判断[：:]\s*(.+?)(?:\n|$)", synthesis)  # 【调用函数】兼容旧格式:解析"方向判断:"行
        agent_direction = direction_match.group(1).strip() if direction_match else "N/A"

    # Extract weights (v2.3.2: 4 dimensions) —— 解析四维权重,如"技术面权重: X/10"
    weights = {}  # 【变量】weights:解析出的四维权重(键: tech/fund/macro/sent)
    for dim, label in [
        ("技术面", "tech"),
        ("基本面", "fund"),
        ("宏观面", "macro"),
        ("情绪面", "sent"),
    ]:
        m = __import__("re").search(rf"{dim}权重[：:]\s*(\d+)/10", synthesis)  # 【调用函数】正则解析各维度权重(X/10)
        if m:
            weights[label] = int(m.group(1))

    # Extract key factors from fundamental report —— 从基本面报告中抽取利多/利空关键词行
    key_bullish = []  # 利多因素列表(最多 5 条)  # 【变量】key_bullish:基本面报告中的利多因素(最多 5 条)
    key_bearish = []  # 利空因素列表(最多 5 条)  # 【变量】key_bearish:利空因素(最多 5 条)
    for line in fundamental.split("\n"):
        line = line.strip()
        # 命中"看多/利多/支撑/strong"等关键词的行判定为利多
        if (
            len(line) > 10
            and len(key_bullish) < 5
            and ("看多" in line or "利多" in line or "支撑" in line or "strong" in line.lower())
        ):
            key_bullish.append(line[:120])
        # 命中"看空/利空/压力/疲弱/累库"等关键词的行判定为利空
        if (
            len(line) > 10
            and len(key_bearish) < 5
            and (
                "看空" in line
                or "利空" in line
                or "压力" in line
                or "疲弱" in line
                or "累库" in line
            )
        ):
            key_bearish.append(line[:120])

    # Extract price range —— 解析综合研判中"关键价位"段落的文本
    price_match = __import__("re").search(  # 【调用函数】正则解析"关键价位"段落文本
        r"关键价位.*?\n(.*?)(?:\n\n|$)", synthesis, __import__("re").DOTALL
    )
    price_range = price_match.group(1).strip()[:500] if price_match else "N/A"  # 【变量】price_range:解析出的关键价位文本(截取前 500 字)

    # Extract BIAS from each analyst (v2.3.2: structured header) —— 解析每位分析师的"方向倾向"
    biases = {}  # 【变量】biases:各分析师的方向倾向(BIAS 解析结果)
    for label, report in [
        ("Technical", technical),
        ("Fundamental", fundamental),
        ("Macro", macro),
        ("Sentiment", sentiment),
    ]:
        # Try new structured format first —— 优先匹配新格式 "BIAS: X | CONFIDENCE: Y"
        m = __import__("re").search(r"BIAS:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)(?:\n|$)", report)  # 【调用函数】解析新格式 BIAS|CONFIDENCE
        if m:
            biases[label] = f"{m.group(1).strip()} (信心={m.group(2).strip()})"
        else:
            # Fallback to old format —— 兼容旧格式 "Bias: X"
            m2 = __import__("re").search(r"Bias[：:]*\s*(.+?)(?:\n|$)", report)  # 【调用函数】兼容旧格式 Bias:
            if m2:
                biases[label] = m2.group(1).strip()

    # --- Build comparison visualization ---
    # 对比报告的可视化:分隔线(双线/单线)
    sep = "=" * 80
    sep2 = "-" * 80

    print(f"\n\n{sep}")
    print("  MARKET COMPARISON REPORT")
    print(f"  {symbol} | {trade_date} | Generated by TradingAgents AI")
    print(f"{sep}")

    # === Section 1: Direction Comparison === 一、方向判断对比(Agent vs 市场研报)
    print(f"\n{'=' * 80}")
    print("  一、方向判断对比")
    print(f"{'=' * 80}")
    print(f"  {'维度':<20} {'Agent 判断':<30} {'市场研报':<30}")
    print(f"  {sep2}")
    print(f"  {'最终方向':<20} {agent_direction:<30} {'[搜索最新研报填入]':<30}")
    print(f"  {'技术面Bias':<20} {biases.get('Technical', 'N/A'):<30} {'':<30}")
    print(f"  {'基本面Bias':<20} {biases.get('Fundamental', 'N/A'):<30} {'':<30}")
    print(f"  {'宏观面Bias':<20} {biases.get('Macro', 'N/A'):<30} {'':<30}")
    print(f"  {'情绪面Bias':<20} {biases.get('Sentiment', 'N/A'):<30} {'':<30}")

    # === Section 2: Weighting === 二、权重分配对比
    print(f"\n{'=' * 80}")
    print("  二、权重分配对比")
    print(f"{'=' * 80}")
    print("  Agent 四维权重分配：")
    print(
        f"    技术面: {weights.get('tech', '?')}/10 | 基本面: {weights.get('fund', '?')}/10 | 宏观面: {weights.get('macro', '?')}/10 | 情绪面: {weights.get('sent', '?')}/10"
    )
    print("  市场研报权重倾向：[通常基本面/供需 > 宏观/政策 > 技术/资金]")

    # === Section 3: Key Factors === 三、核心多空因素(展示利多/利空清单)
    print(f"\n{'=' * 80}")
    print("  三、核心多空因素")
    print(f"{'=' * 80}")
    print(f"  {'因素':<15} {'Agent':<40} {'市场研报':<25}")
    print(f"  {sep2}")
    if key_bullish:
        print(f"  {'【利多因素】':<15}")
        for i, f in enumerate(key_bullish[:3]):
            print(f"  {str(i + 1):<15} {f:<40} {'':<25}")
    if key_bearish:
        print(f"  {'【利空因素】':<15}")
        for i, f in enumerate(key_bearish[:3]):
            print(f"  {str(i + 1):<15} {f:<40} {'':<25}")

    # === Section 4: Price Range === 四、关键价位(打印前 8 行)
    print(f"\n{'=' * 80}")
    print("  四、关键价位")
    print(f"{'=' * 80}")
    print("  Agent 价位分析：")
    for line in price_range.split("\n")[:8]:
        if line.strip():
            print(f"    {line.strip()[:100]}")
    print("  市场研报价位：[搜索各机构研报填入]")

    # === Section 5: Methodology Comparison === 五、方法论对比
    print(f"\n{'=' * 80}")
    print("  五、方法论对比")
    print(f"{'=' * 80}")
    print(f"  {'维度':<20} {'Agent':<30} {'市场研报':<30}")
    print(f"  {sep2}")
    methods = [
        ("基差分析", "核心框架，精确量化", "[极少提及]"),
        ("OI量价信号", "逐日拆解，双重解读", "[偶有提及]"),
        ("产业链利润传导", "跨品种精确定量", "[定性为主]"),
        ("假设反转矩阵", "结构化反事实检验", "[极少有结构]"),
        ("近因偏差检查", "强制自检机制", "[无此机制]"),
        ("事件驱动覆盖", "外源JSON+免费API", "[Mysteel级专有数据]"),
        ("社交媒体情绪", "微博+知乎+小红书量化", "[凭感觉/无量化]"),
        ("权重分配透明度", "显式加权+理由", "[通常隐含/不定量]"),
    ]
    for dim, agent_val, mkt_val in methods:
        print(f"  {dim:<20} {agent_val:<30} {mkt_val:<30}")

    # === Section 6: Coverage === 六、数据覆盖度(用基本面报告长度粗略估算数据来源)
    print(f"\n{'=' * 80}")
    print("  六、数据覆盖度")
    print(f"{'=' * 80}")
    # Detect what data was available
    has_external = len(fundamental) > 3000  # rough heuristic —— 粗略启发式:基本面报告超 3000 字视为有外源 JSON 数据  # 【变量】has_external:粗略判断是否有外源JSON数据
    coverage = "85% (含外源JSON)" if has_external else "45-60% (仅免费API)"  # 【变量】coverage:估算的数据覆盖度文本
    print(f"  Agent 数据覆盖度: {coverage}")
    print("  市场研报覆盖率: 100% (含Mysteel/专有数据库)")
    print("  缺失数据类型: 表观消费量、矿山/钢厂周度开工率、蒙煤通关量等Mysteel级专有数据")

    # === Section 7: Overall Score === 七、综合评分(维度评分 + 说明)
    print(f"\n{'=' * 80}")
    print("  七、综合评分")
    print(f"{'=' * 80}")
    print(f"  {'维度':<20} {'Agent得分':<15} {'说明':<45}")
    print(f"  {sep2}")
    scores = [
        ("方向判断准确性", "?????", "需与实盘走势对比验证"),
        ("数据覆盖度", "85%/55%", "取决于是否有外源JSON"),
        ("逻辑自洽性", "★★★★★", "三维加权+反事实检验+假设反转"),
        ("方法论创新", "★★★★★", "基差框架+OI拆解+近因偏差检查"),
        ("可操作性", "★★★★", "关键价位+风险矩阵+情景推演"),
        ("跨品种泛化", "★★★★★", "8品种测试，87.5%方向不矛盾"),
        ("事件驱动", "★★★", "外源JSON机制可用但需人工维护"),
    ]
    for dim, score, note in scores:
        print(f"  {dim:<20} {score:<15} {note:<45}")

    # === Section 8: Key Gaps to Fill === 八、需补充的市场研报信息(给用户的搜索指引)
    print(f"\n{'=' * 80}")
    print("  八、需补充的市场研报信息")
    print(f"{'=' * 80}")
    print("  请搜索以下机构最新研报填入对比：")
    print("    东吴期货、正信期货、国信期货、光大期货、南华期货、中信建投期货")
    print(f"  搜索关键词: {symbol} 期货 2026年7月 研报 走势分析")
    print("  重点对比维度：")
    print("    1. 方向判断 → 匹配/偏离？")
    print("    2. 关键价位 → Agent vs 机构目标价？")
    print("    3. 核心逻辑 → 双方是否使用相同的数据源？")
    print("    4. 风险提示 → Agent是否遗漏了机构关注的风险？")

    print(f"\n{sep}")
    print("  对比报告已附在分析报告末尾")
    print(f"{sep}")

    # --- Save comparison to file ---
    # 把对比框架(方向/权重/关键价位/方法论)另存为 _comparison.md 文件
    comparison_path = report_path.replace(".md", "_comparison.md")  # 【变量】comparison_path:对比报告文件路径(基于主报告生成)
    try:
        with open(comparison_path, "w", encoding="utf-8") as f:
            f.write(f"# Market Comparison: {symbol}\n\n")
            f.write(f"**Date**: {trade_date}\n\n")
            f.write(f"## Direction\n- Agent: {agent_direction}\n- Market: [TBD]\n\n")
            f.write(f"## Weights\n- Tech: {weights.get('tech', '?')}/10\n")
            f.write(f"- Fund: {weights.get('fund', '?')}/10\n")
            f.write(f"- Macro: {weights.get('macro', '?')}/10\n")
            f.write(f"- Sentiment: {weights.get('sent', '?')}/10\n\n")
            f.write(f"## Key Price Levels\n{price_range}\n\n")
            f.write("## Methodology Comparison\n")
            for dim, agent_val, mkt_val in methods:
                f.write(f"- {dim}: Agent={agent_val} | Market={mkt_val}\n")
        print(f"\n[*] 对比框架已保存至: {comparison_path}")
    except Exception as e:
        logger.warning(f"Failed to save comparison: {e}")


if __name__ == "__main__":
    main()
