# =====================================================================
# cli/main.py —— TradingAgents 的「交互式命令行入口」(CLI / TUI)
#
# 本项目有三大并列入口,作用各不相同:
#   1. cli/main.py      —— 本文件,交互式命令行(Typer 交互 + Rich Live 实时仪表板)
#   2. commodity_demo.py —— 商品期货演示脚本(简化的并行分析图)
#   3. web_app.py       —— Web 界面入口
#
# 本文件的职责:只负责「与用户交互 + 实时展示进度」,
# 真正的分析计算在 tradingagents.graph.* 等底层模块中完成。
#
# 整体交互流程(用户从终端一步步选择):
#   ① 选资产(ticker,自动识别股票 / 加密 / 商品期货)
#   ② 选分析日期 → ③ 选输出语言 → ④ 选分析师
#   → ⑤ 选研究深度 → ⑥ 选 LLM 提供商 → ⑦ 选思考 Agent → ⑧ 提供商专属参数
#   → 运行分析(期间用 Rich Live 仪表板实时显示 Agent 状态 / 消息 / 报告进度)
#   → 分析完成后进入「反馈循环」,用户可输入 /feedback /exit /help
# =====================================================================
import contextlib
import datetime
import os
import time
from collections import deque
from functools import wraps
from pathlib import Path

import typer
from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from cli.announcements import display_announcements, fetch_announcements
from cli.models import AssetType
from cli.stats_handler import StatsCallbackHandler
from cli.utils import (
    ask_anthropic_effort,
    ask_gemini_thinking_config,
    ask_glm_region,
    ask_minimax_region,
    ask_openai_reasoning_effort,
    ask_output_language,
    ask_qwen_region,
    confirm_ollama_endpoint,
    detect_asset_type,
    ensure_api_key,
    get_commodity_ticker,
    get_ticker,
    prompt_openai_compatible_url,
    resolve_backend_url,
    select_analysts,
    select_commodity_analysts,
    select_deep_thinking_agent,
    select_llm_provider,
    select_research_depth,
    select_shallow_thinking_agent,
)
from tradingagents.agents.utils.user_feedback_agent import create_user_feedback_node
from tradingagents.dataflows.evolution_memory import get_evolution_context
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
    get_initial_analyst_node,
    sync_analyst_tracker_from_chunk,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree

console = Console()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


# ---------------------------------------------------------------------
# MessageBuffer —— 仪表板的「UI 状态缓冲池」
# 作用:把分析过程中需要展示给用户的数据暂存在内存里,包括:
#   - messages        :普通消息(时间, 类型, 内容)
#   - tool_calls      :工具调用记录(时间, 工具名, 参数)
#   - agent_status    :每个 Agent 的实时状态(pending / in_progress / completed / error)
#   - report_sections :各报告段落的最新内容
#   - current_report / final_report :拼装好的报告
# Rich Live 仪表板每次刷新时都从 message_buffer 里取数据来渲染。
# 理解要点:它本身不执行任何分析,只是一个供 UI 读取的"记事本"。
# ---------------------------------------------------------------------
# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    # Fixed teams that always run (not user-selectable)
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Analyst name mapping
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Sentiment Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
        # Commodity futures analysts
        "commodity_technical": "Technical Analyst",
        "commodity_fundamental": "Fundamental Analyst",
        "commodity_macro": "Macro/News Analyst",
        "commodity_sentiment": "Sentiment Analyst",
    }

    # Commodity analyst report mapping (different state keys)
    COMMODITY_REPORT_MAP = {
        "commodity_technical": "technical_report",
        "commodity_fundamental": "fundamental_report",
        "commodity_macro": "macro_report",
        "commodity_sentiment": "sentiment_report",
    }

    # Report section mapping: section -> (analyst_key for filtering, finalizing_agent)
    # analyst_key: which analyst selection controls this section (None = always included)
    # finalizing_agent: which agent must be "completed" for this report to count as done
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
        # Commodity futures
        "technical_report": ("commodity_technical", "Technical Analyst"),
        "fundamental_report": ("commodity_fundamental", "Fundamental Analyst"),
        "macro_report": ("commodity_macro", "Macro/News Analyst"),
        "sentiment_report": ("commodity_sentiment", "Sentiment Analyst"),
        "user_feedback_summary": (None, "User Feedback"),
    }

    # 【功能】初始化消息缓冲池:创建存放各类数据的容器。
    # 【参数】max_length:deque(双端队列)的最大长度,超过后自动丢弃最旧元素。
    # 【返回】无
    # 【关键逻辑】deque(maxlen=...) 满员时从头部自动弹出旧元素,
    #            确保长时间分析不会让内存无限增长。
    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._processed_message_ids = set()

    # 【功能】开始一次新分析前,重置缓冲池,并按所选分析师构建 Agent 状态与报告段落。
    # 【参数】selected_analysts:分析师类型字符串列表(如 ["market", "news"]);
    #         asset_type:"stock" / "crypto" / "commodity_futures"。
    # 【返回】无
    # 【关键逻辑】
    #   - 分析师 key 统一转小写后存入 selected_analysts;
    #   - 为选中的分析师建立 "pending"(待处理)状态(依据 ANALYST_MAPPING);
    #   - 股票/加密资产还会加入固定团队(研究/交易/风控/组合管理)的 Agent;
    #     商品期货有自己更简单的图,不加入这些固定团队;
    #   - 按 REPORT_SECTIONS 建立本次会生成的报告段落映射;商品期货路径会
    #     跳过股票专属、永远不会被填充的段落(如 market_report 等);
    #   - 最后清空历史消息 / 工具调用 / 已处理消息 ID,确保每次分析互不干扰。
    def init_for_analysis(self, selected_analysts, asset_type="stock"):
        """Initialize agent status and report sections based on selected analysts.

        Args:
            selected_analysts: List of analyst type strings (e.g., ["market", "news"])
            asset_type: "stock", "crypto", or "commodity_futures"
        """
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # Build agent_status dynamically
        self.agent_status = {}

        # Add selected analysts
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # Add fixed teams (stock/crypto only — commodity has its own simpler graph)
        if asset_type != "commodity_futures":
            for team_agents in self.FIXED_AGENTS.values():
                for agent in team_agents:
                    self.agent_status[agent] = "pending"

        # Build report_sections dynamically
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                # Commodity path: skip stock-only sections that are never filled
                if asset_type == "commodity_futures" and section in (
                    "market_report",
                    "sentiment_report",
                    "news_report",
                    "fundamentals_report",
                    "trader_investment_plan",
                ):
                    continue
                self.report_sections[section] = None

        # Reset other state
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._processed_message_ids.clear()

    # 【功能】统计「已经完成」的报告数量(用于仪表板底部的 Reports 计数)。
    # 【参数】无
    # 【返回】int:已完成的报告数。
    # 【关键逻辑】一份报告算"完成"需同时满足:
    #   ① 报告段落有内容(不是 None);② 负责定稿该报告的 Agent 状态为 "completed"。
    #   这样可避免把中间过程(如辩论轮次的临时更新)误算成完成。
    def get_completed_reports_count(self):
        """Count reports that are finalized (their finalizing agent is completed).

        A report is considered complete when:
        1. The report section has content (not None), AND
        2. The agent responsible for finalizing that report has status "completed"

        This prevents interim updates (like debate rounds) from counting as completed.
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # Report is complete if it has content AND its finalizing agent is done
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    # 【功能】向消息队列追加一条普通消息(带时间戳)。
    # 【参数】message_type:消息类型(如 "System" / "Agent" / "User");
    #         content:消息正文。
    # 【返回】无
    # 【关键逻辑】时间戳取当前本地时间 %H:%M:%S;消息会显示在仪表板的
    #            "Messages & Tools" 面板中。
    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    # 【功能】向工具调用队列追加一条工具调用记录(带时间戳)。
    # 【参数】tool_name:被调用的工具名称;args:传给工具的参数字典。
    # 【返回】无
    # 【关键逻辑】与 add_message 类似,专门记录 Agent 调用过哪些工具,
    #            便于在仪表板上展示工具使用轨迹。
    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    # 【功能】更新某个 Agent 的状态,并把它记为"当前正在运行的 Agent"。
    # 【参数】agent:Agent 名称;status:新状态("pending"/"in_progress"/"completed"/"error")。
    # 【返回】无
    # 【关键逻辑】只有 agent 已存在于 agent_status 中才更新;同时把 current_agent
    #            指向它,供仪表板高亮当前活跃的 Agent。
    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    # 【功能】更新某个报告段落的内容。
    # 【参数】section_name:段落名(如 "market_report" / "technical_report");
    #         content:新的报告内容。
    # 【返回】无
    # 【关键逻辑】写回报告段落后,调用 _update_current_report() 刷新"当前报告"面板。
    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    # 【功能】根据最新更新的报告段落,重新组装"当前报告"(面板只展示最近更新的那一段)。
    # 【参数】无
    # 【返回】无
    # 【关键逻辑】
    #   - 遍历 report_sections,取最后一个有内容的段落作为 current_report;
    #   - 通过 section_titles 把内部段落名翻译成人类可读标题;
    #   - 商品期货模式下,把股票专属标题覆盖为商品专属标题;
    #   - 最后调用 _update_final_report() 维护完整最终报告。
    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content

        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
                "technical_report": "Technical Analysis",
                "fundamental_report": "Fundamental Analysis",
                "macro_report": "Macro/News Analysis",
                "user_feedback_summary": "User Feedback & Evolution",
            }
            # Commodity mode: override stock-specific labels
            if "technical_report" in self.report_sections:
                section_titles["investment_plan"] = "Synthesis & Recommendation"
                section_titles["final_trade_decision"] = "Final Recommendation"
            self.current_report = (
                f"### {section_titles.get(latest_section, latest_section)}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    # 【功能】把所有已有报告段落拼接成一份完整的 Markdown 最终报告(final_report)。
    # 【参数】无
    # 【返回】无
    # 【关键逻辑】按"分析师团队 → 研究团队 → 交易团队 → 组合管理"的顺序拼接;
    #            用 .get() 跳过缺失的段落;没有任何段落时 final_report 为 None。
    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports - use .get() to handle missing sections
        analyst_sections = [
            "market_report",
            "sentiment_report",
            "news_report",
            "fundamentals_report",
        ]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(f"### Market Analysis\n{self.report_sections['market_report']}")
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(f"### News Analysis\n{self.report_sections['news_report']}")
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


# message_buffer:全局唯一的 UI 状态缓冲池实例,整个 CLI 运行期间各函数都读写它。
message_buffer = MessageBuffer()


# 【功能】创建 Rich Live 仪表板的整体布局(header / main / footer 三行)。
# 【参数】commodity_mode:True 表示商品期货模式,会在布局对象上打一个标记。
# 【返回】Rich Layout 对象。
# 【关键逻辑】用 split_column / split_row 把终端屏幕切分:
#   header(3 行) → main(upper + analysis) → footer(3 行);
#   upper 再分成 progress(进度) 与 messages(消息) 两栏;
#   并把 _commodity_mode 标记挂在 Layout 对象上,供 update_display 读取。
def create_layout(commodity_mode=False):
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5))
    layout["upper"].split_row(Layout(name="progress", ratio=2), Layout(name="messages", ratio=3))
    # Tag for display function to detect commodity mode
    layout._commodity_mode = commodity_mode
    return layout


# 【功能】把 token 数量格式化为易读的字符串(≥1000 时显示为 x.xk)。
# 【参数】n:token 数(整数)。
# 【返回】str:格式化后的字符串(如 "1500" → "1.5k")。
def format_tokens(n):
    """Format token count for display."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


# 【功能】根据 message_buffer 里的最新状态,重新渲染 Rich Live 仪表板的各个面板。
# 【参数】layout:Rich Layout 对象;spinner_text:保留参数(签名中有但函数体未使用)【待确认】;
#         stats_handler:统计回调(提供 LLM 调用次数 / 工具次数 / token 数);
#         start_time:开始时间(用于计算已运行时长)。
# 【返回】无
# 【关键逻辑】这是仪表板的核心刷新函数,分析流每产出一个新 chunk 都会被调用一次:
#   - header  :固定欢迎横幅;
#   - progress:按 agent_status 渲染"团队 / Agent / 状态"表格,运行中的 Agent 显示转圈动画;
#   - messages:把工具调用与普通消息合并、按时间倒序、截断超长文本,显示最近 12 条;
#   - analysis:展示当前报告(Markdown 渲染)或等待占位文本;
#   - footer  :汇总 Agent 完成数、LLM / 工具统计、报告完成数、已运行时间。
def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team - filter to only include agents in agent_status
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Sentiment Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Commodity mode: use a simplified team structure
    commodity_teams = {
        "Commodity Analysts": [
            "Technical Analyst",
            "Fundamental Analyst",
            "Macro/News Analyst",
        ],
    }

    # Detect commodity mode from layout flag
    is_commodity = getattr(layout, "_commodity_mode", False)
    if is_commodity:
        teams = {}
        for team, agents in commodity_teams.items():
            active_agents = [a for a in agents if a in message_buffer.agent_status]
            if active_agents:
                teams[team] = active_agents
    else:
        # Filter teams to only include agents that are in agent_status
        teams = {}
        for team, agents in all_teams.items():
            active_agents = [a for a in agents if a in message_buffer.agent_status]
            if active_agents:
                teams[team] = active_agents

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            spinner = Spinner("dots", text="[blue]in_progress[/blue]", style="bold cyan")
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner("dots", text="[blue]in_progress[/blue]", style="bold cyan")
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp descending (newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # Calculate how many messages we can show based on available space
    max_messages = 12

    # Get the first N messages (newest ones)
    recent_messages = all_messages[:max_messages]

    # Add messages to table (already in newest-first order)
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    # Agent progress - derived from agent_status dict
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # Report progress - based on agent completion (not just content existence)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # Build stats parts
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM and tool stats from callback handler
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token display with graceful fallback
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # Elapsed time
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


# 【功能】在分析启动前,通过一轮交互式问答收集用户的全部选择。
# 【参数】无
# 【返回】dict:选择结果字典。股票 / 加密路径包含 ticker、asset_type、analysis_date、
#             analysts、research_depth、llm_provider、backend_url、shallow_thinker、
#             deep_thinker、各提供商专属推理配置、output_language;
#             商品期货路径返回更精简的字段(ticker / analysis_date / asset_type /
#             analysts / output_language)。
# 【关键逻辑】
#   股票 / 加密主流程(Step1~Step8):
#     Step1 输入 ticker → 自动识别资产类型 → Step2 分析日期 → Step3 输出语言
#     Step4 选分析师 → Step5 研究深度 → Step6 选 LLM 提供商
#       (qwen / minimax / glm 还会追问区域;ollama 会确认端点;
#        openai_compatible 无默认地址时会询问地址)
#     Step7 选思考 Agent(快 / 慢)→ Step8 提供商专属推理配置(如 Gemini thinking / OpenAI reasoning)
#   商品期货分支:检测到 AssetType.COMMODITY_FUTURES 时走精简流程(见下方),
#     跳过股票专属的 Step5~Step8。
#   每个步骤都支持用对应的 TRADINGAGENTS_* 环境变量"免交互"跳过,遵循
#   "环境变量优先于交互选择"的规则。
def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    # 【功能】生成一个带标题和提示语的小方框(Panel),用于逐步提问。
    # 【参数】title:步骤标题;prompt:提示语;default:可选默认值(展示在底部)。
    # 【返回】Rich Panel 对象,可直接 console.print 输出。
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # 【功能】若设置了环境变量则直接用配置值并跳过提问;否则展示问题框并交互询问。
    # 【参数】env_var:环境变量名;config_key:DEFAULT_CONFIG 中对应的键;
    #         label:展示给用户的标签;box_title / box_body:问题框的标题与正文;
    #         prompt_fn:具体的交互提问函数(如 ask_gemini_thinking_config)。
    # 【返回】用户最终使用的值(来自环境变量或交互提问)。
    # 【关键逻辑】体现"环境变量优先于交互"规则:只要设置了 env_var,就不打扰用户。
    def thinking_value_or_prompt(env_var, config_key, label, box_title, box_body, prompt_fn):
        """Return the env-configured reasoning/thinking value, or prompt for it.

        When ``env_var`` is set the interactive choice is skipped and the value
        the env overlay placed on DEFAULT_CONFIG is used — mirroring the
        env-precedence rule applied to the other selection steps.
        """
        if os.environ.get(env_var):
            value = DEFAULT_CONFIG[config_key]
            console.print(f"[green]✓ {label} from environment:[/green] {value}")
            return value
        console.print(create_question_box(box_title, box_body))
        return prompt_fn()

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the ticker, with exchange suffix when needed (e.g. SPY, 0700.HK, BTC-USD)\n"
            "For commodity futures, enter variety code: RB (螺纹钢), I (铁矿石), M (豆粕)...",
            "SPY",
        )
    )
    selected_ticker = get_ticker()
    asset_type = detect_asset_type(selected_ticker)
    # Only announce when it's not the default stock path, to avoid printing
    # "stock" on every run.
    if asset_type.value != "stock":
        console.print(f"[green]Detected asset type:[/green] {asset_type.value}")

    # --- Commodity futures branch ---
    # 【商品期货路径】检测到 ticker 是商品期货代码(AssetType.COMMODITY_FUTURES)时,
    # 走更精简的交互流程:重新校验商品代码 → 分析日期 → 输出语言 → 选商品分析师,
    # 然后直接返回,跳过股票 / 加密路径的 Step5~Step8。
    if asset_type == AssetType.COMMODITY_FUTURES:
        # Re-prompt with commodity-specific validation
        selected_ticker = get_commodity_ticker()

        # Step 2: Analysis date
        default_date = datetime.datetime.now().strftime("%Y-%m-%d")
        console.print(
            create_question_box(
                "Step 2: Analysis Date",
                "Enter the analysis date (YYYY-MM-DD)",
                default_date,
            )
        )
        analysis_date = get_analysis_date()

        # Step 3: Output language
        if os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE"):
            output_language = DEFAULT_CONFIG["output_language"]
        else:
            output_language = ask_output_language()

        # Step 4: Select commodity analysts
        console.print(
            create_question_box(
                "Step 4: Commodity Analysts Team",
                "Select your commodity analyst agents (3 parallel analysts → synthesis)",
            )
        )
        selected_analysts = select_commodity_analysts()

        # Minimal selections for commodity path
        selections = {
            "ticker": selected_ticker,
            "analysis_date": analysis_date,
            "asset_type": asset_type.value,
            "analysts": selected_analysts,
            "output_language": output_language,
        }
        return selections
    # --- End commodity branch ---

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 3: Output language (skipped when set via TRADINGAGENTS_OUTPUT_LANGUAGE)
    if os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE"):
        output_language = DEFAULT_CONFIG["output_language"]
        console.print(f"[green]✓ Output language from environment:[/green] {output_language}")
    else:
        console.print(
            create_question_box(
                "Step 3: Output Language",
                "Select the language for analyst reports and final decision",
            )
        )
        output_language = ask_output_language()

    # Step 4: Select analysts
    console.print(
        create_question_box(
            "Step 4: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts(asset_type)
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 5: Research depth (skipped when both round counts are set via env).
    # Research depth maps to the debate + risk round counts; when both are
    # supplied through TRADINGAGENTS_MAX_DEBATE_ROUNDS / _MAX_RISK_ROUNDS we keep
    # the run non-interactive and honor the env values (#977).
    depth_from_env = bool(os.environ.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS")) and bool(
        os.environ.get("TRADINGAGENTS_MAX_RISK_ROUNDS")
    )
    if depth_from_env:
        selected_research_depth = DEFAULT_CONFIG["max_debate_rounds"]
        console.print(
            f"[green]✓ Research depth from environment:[/green] "
            f"{DEFAULT_CONFIG['max_debate_rounds']} debate / "
            f"{DEFAULT_CONFIG['max_risk_discuss_rounds']} risk rounds"
        )
    else:
        console.print(
            create_question_box("Step 5: Research Depth", "Select your research depth level")
        )
        selected_research_depth = select_research_depth()

    # Step 6: LLM Provider (skipped when set via TRADINGAGENTS_LLM_PROVIDER).
    # The backend URL comes from TRADINGAGENTS_LLM_BACKEND_URL when set,
    # otherwise the provider's default endpoint — the same value the menu
    # would have picked.
    provider_from_env = bool(os.environ.get("TRADINGAGENTS_LLM_PROVIDER"))
    if provider_from_env:
        selected_llm_provider = DEFAULT_CONFIG["llm_provider"].lower()
        backend_url = resolve_backend_url(
            selected_llm_provider, env_url=DEFAULT_CONFIG["backend_url"]
        )
        console.print(f"[green]✓ LLM provider from environment:[/green] {selected_llm_provider}")
        console.print(f"[green]✓ Backend URL:[/green] {backend_url}")
        # Still confirm/persist the API key so the run doesn't fail later.
        ensure_api_key(selected_llm_provider)
    else:
        console.print(create_question_box("Step 6: LLM Provider", "Select your LLM provider"))
        selected_llm_provider, backend_url = select_llm_provider()

        # Providers with regional endpoints prompt for the region as a secondary
        # step so the main dropdown stays clean (mainland China and international
        # accounts cannot share API keys).
        if selected_llm_provider == "qwen":
            selected_llm_provider, backend_url = ask_qwen_region()
        elif selected_llm_provider == "minimax":
            selected_llm_provider, backend_url = ask_minimax_region()
        elif selected_llm_provider == "glm":
            selected_llm_provider, backend_url = ask_glm_region()

        # Honor an explicit env backend URL even when the provider was chosen
        # interactively, so it isn't overwritten by the menu default (#978).
        backend_url = resolve_backend_url(
            selected_llm_provider, backend_url, env_url=DEFAULT_CONFIG["backend_url"]
        )

        # The generic OpenAI-compatible endpoint has no default; ask for it if
        # neither the menu nor the environment supplied one.
        if selected_llm_provider == "openai_compatible" and not backend_url:
            backend_url = prompt_openai_compatible_url()

        # For Ollama, surface the resolved endpoint (OLLAMA_BASE_URL vs default)
        # before model selection so it's obvious where we're connecting.
        if selected_llm_provider == "ollama":
            confirm_ollama_endpoint(backend_url)

        # Confirm the provider's API key is present; prompt the user to paste
        # one and persist it to .env if it's missing, so the analysis run
        # doesn't fail later at the first API call.
        ensure_api_key(selected_llm_provider)

    # Step 7: Thinking agents (skipped when either model is set via environment)
    if os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM") or os.environ.get(
        "TRADINGAGENTS_DEEP_THINK_LLM"
    ):
        selected_shallow_thinker = DEFAULT_CONFIG["quick_think_llm"]
        selected_deep_thinker = DEFAULT_CONFIG["deep_think_llm"]
        console.print(
            f"[green]✓ Thinking agents from environment:[/green] "
            f"quick={selected_shallow_thinker}, deep={selected_deep_thinker}"
        )
    else:
        console.print(
            create_question_box(
                "Step 7: Thinking Agents", "Select your thinking agents for analysis"
            )
        )
        selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
        selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 8: Provider-specific reasoning/thinking configuration. Each knob is
    # settable via its TRADINGAGENTS_* env var; when that var is set (or the
    # provider itself came from env) the prompt is skipped and the configured
    # value is used — same env-precedence rule as the steps above. None = each
    # provider's own default.
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_from_env:
        thinking_level = DEFAULT_CONFIG["google_thinking_level"]
        reasoning_effort = DEFAULT_CONFIG["openai_reasoning_effort"]
        anthropic_effort = DEFAULT_CONFIG["anthropic_effort"]
    elif provider_lower == "google":
        thinking_level = thinking_value_or_prompt(
            "TRADINGAGENTS_GOOGLE_THINKING_LEVEL",
            "google_thinking_level",
            "Gemini thinking mode",
            "Step 8: Thinking Mode",
            "Configure Gemini thinking mode",
            ask_gemini_thinking_config,
        )
    elif provider_lower == "openai":
        reasoning_effort = thinking_value_or_prompt(
            "TRADINGAGENTS_OPENAI_REASONING_EFFORT",
            "openai_reasoning_effort",
            "Reasoning effort",
            "Step 8: Reasoning Effort",
            "Configure OpenAI reasoning effort level",
            ask_openai_reasoning_effort,
        )
    elif provider_lower == "anthropic":
        anthropic_effort = thinking_value_or_prompt(
            "TRADINGAGENTS_ANTHROPIC_EFFORT",
            "anthropic_effort",
            "Claude effort",
            "Step 8: Effort Level",
            "Configure Claude effort level",
            ask_anthropic_effort,
        )

    return {
        "ticker": selected_ticker,
        "asset_type": asset_type.value,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
    }


# 【功能】交互式获取分析日期(YYYY-MM-DD),并校验格式正确且不能在未来。
# 【参数】无
# 【返回】str:合法的日期字符串(如 "2026-08-12")。
# 【关键逻辑】用 while 循环反复提问,直到输入合法;失败时打印红色错误并重新询问。
def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt("", default=datetime.datetime.now().strftime("%Y-%m-%d"))
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print("[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]")


# 【功能】把完整分析报告写入磁盘。
# 【参数】final_state:最终状态字典;ticker:资产代码;save_path:保存目录。
# 【返回】write_report_tree 的返回值(通常是写出的报告文件信息)。
# 【关键逻辑】这是 CLI 与 API 共用的报告写入函数,具体实现见 write_report_tree。
def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save the complete analysis report to disk (shared CLI/API writer)."""
    return write_report_tree(final_state, ticker, save_path)


# 【功能】在终端按顺序完整展示最终分析报告(避免长报告被终端截断)。
# 【参数】final_state:最终状态字典。
# 【返回】无
# 【关键逻辑】
#   - 商品期货路径:展示技术 / 基本面 / 宏观 / 讨论 / 综合建议 / 用户反馈等商品专属段落;
#   - 股票 / 加密路径:按 Ⅰ分析师团队 → Ⅱ研究团队 → Ⅲ交易团队
#     → Ⅳ风控团队 → Ⅴ组合管理决策 的顺序逐段输出;
#   - 只展示 final_state 中"存在且有内容"的段落。
def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # Commodity futures path: show commodity-specific reports
    if final_state.get("asset_type") == "commodity_futures":
        commodity_sections = [
            ("📈 Technical Analysis", final_state.get("technical_report")),
            ("🏭 Fundamental Analysis", final_state.get("fundamental_report")),
            ("🌍 Macro/News Analysis", final_state.get("macro_report")),
            ("🫱 Discussion Summary", final_state.get("discussion_summary")),
            ("📋 Synthesis & Recommendation", final_state.get("investment_plan")),
            ("🧬 User Feedback & Evolution", final_state.get("user_feedback_summary")),
        ]
        for title, content in commodity_sections:
            if content:
                console.print(
                    Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2))
                )
        return

    # Stock/crypto path: original display logic
    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(
                Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2))
            )

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(
                    Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2))
                )

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(
            Panel(
                Markdown(final_state["trader_investment_plan"]),
                title="Trader",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(
                Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red")
            )
            for title, content in risk_reports:
                console.print(
                    Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2))
                )

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(
                Panel(
                    Markdown(risk["judge_decision"]),
                    title="Portfolio Manager",
                    border_style="blue",
                    padding=(1, 2),
                )
            )


# 【功能】批量更新研究团队成员(Bull Researcher / Bear Researcher / Research Manager)的状态。
# 【参数】status:要设置的状态字符串(如 "in_progress" / "completed")。
# 【返回】无
# 【关键逻辑】注意不含 Trader,Trader 由交易团队逻辑单独控制。
def update_research_team_status(status):
    """Update status for research team members (not Trader)."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# 分析师执行顺序(股票 / 加密路径):决定状态流转时谁先运行、谁是下一个。
# Ordered list of analysts for status transitions
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
# 分析师 key → 展示用的 Agent 名称 的映射。
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
# 分析师 key → 报告段落 key 的映射。
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


# 【功能】根据「累积」的报告段落状态,更新各分析师在仪表板上的运行状态。
# 【参数】message_buffer:UI 状态缓冲池;chunk:当前流式返回的数据块;
#         wall_time_tracker:可选,用于同步分析师真实耗时统计。
# 【返回】无
# 【关键逻辑】
#   - 判断状态时优先看累积的 report_sections,而不只看当前 chunk;
#   - 有报告的 = "completed";第一个没报告的 = "in_progress";其余没报告的 = "pending";
#   - 当所有选中的分析师都完成后,把 Bull Researcher 置为 "in_progress",
#     从而把进度推进到下一阶段(研究辩论)。
def update_analyst_statuses(message_buffer, chunk, wall_time_tracker=None):
    """Update analyst statuses based on accumulated report state.

    Logic:
    - Store new report content from the current chunk if present
    - Check accumulated report_sections (not just current chunk) for status
    - Analysts with reports = completed
    - First analyst without report = in_progress
    - Remaining analysts without reports = pending
    - When all analysts done, set Bull Researcher to in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    if wall_time_tracker is not None:
        sync_analyst_tracker_from_chunk(wall_time_tracker, chunk)

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # Capture new report content from current chunk
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # Determine status from accumulated sections, not just current chunk
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # When all analysts complete, transition research team to in_progress
    if (
        not found_active
        and selected
        and message_buffer.agent_status.get("Bull Researcher") == "pending"
    ):
        message_buffer.update_agent_status("Bull Researcher", "in_progress")


# 【功能】从各种消息格式(str / dict / list)中提取出文本内容;没有有效文本则返回 None。
# 【参数】content:消息内容,可能是字符串、字典(含 "text" 键)、或列表(openai 风格的 content 块)。
# 【返回】str 或 None。
# 【关键逻辑】用 is_empty() 判断"看似有值实为空"的输入;列表则把所有 text 片段拼接起来。
def extract_content_string(content):
    """Extract string content from various message formats.
    Returns None if no meaningful text content is found.
    """
    import ast

    # 【功能】判断一个值是否"为空"(None、空串、全空白、可解析为空结构等)。
    # 【参数】val:任意值。
    # 【返回】bool:True 表示空。
    # 【关键逻辑】字符串先去空白(strip)再尝试 ast.literal_eval,
    #            能解析出空结构(如 "{}" / "[]")也算空;解析失败说明是真实文本。
    def is_empty(val):
        """Check if value is empty using Python's truthiness."""
        if val is None or val == "":
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # Can't parse = real text
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get("text", "")
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get("text", "").strip()
            if isinstance(item, dict) and item.get("type") == "text"
            else (item.strip() if isinstance(item, str) else "")
            for item in content
        ]
        result = " ".join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


# 【功能】把 LangChain 消息分类为展示类型,并抽取文本内容。
# 【参数】message:LangChain 消息对象(HumanMessage / ToolMessage / AIMessage 等)。
# 【返回】(type, content):type 为 "User" / "Agent" / "Data" / "Control" / "System";
#             content 为抽取出的字符串或 None。
# 【关键逻辑】
#   - HumanMessage → "User"(内容恰为 "Continue" 时归为 "Control",用于触发继续);
#   - ToolMessage  → "Data"(工具返回的数据);
#   - AIMessage    → "Agent"(模型产出);
#   - 未知类型     → "System"。
def classify_message_type(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content.

    Returns:
        (type, content) - type is one of: User, Agent, Data, Control
                        - content is extracted string or None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, "content", None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # Fallback for unknown types
    return ("System", content)


# 【功能】把工具参数格式化为适合终端显示的字符串,超长则截断加省略号。
# 【参数】args:工具参数;max_length:最大显示长度(默认 80)。
# 【返回】str:格式化后的字符串。
def format_tool_args(args, max_length=80) -> str:
    """Format tool arguments for terminal display."""
    result = str(args)
    if len(result) > max_length:
        return result[: max_length - 3] + "..."
    return result


# 【功能】分析完成后的交互式命令循环,持续等待用户输入命令,直到显式退出。
# 【参数】llm:语言模型对象;final_state:最终状态字典;ticker:资产代码;
#         report_dir:报告保存目录;feedback_node:用户反馈节点(可为 None);
#         feedback_enabled:是否启用反馈功能。
# 【返回】无
# 【关键逻辑】
#   - 因为系统已支持用户反馈与辩论,分析结束后不再自动退出,而是进入循环;
#   - /feedback 或 /fb:调用 feedback_node 启动一轮"用户反馈辩论"会话,
#     结果(若有)写入 report_dir/user_feedback_summary.md;
#   - /exit /quit /q 或 exit / quit / q:退出程序;
#   - /help /h 或 help:打印可用命令;
#   - Ctrl+C 或文件结束(EOFError)也能安全退出;
#   - 未知命令给出黄色提示,并继续循环。
def _run_interactive_loop(
    llm,
    final_state: dict,
    ticker: str,
    report_dir,
    feedback_node,
    feedback_enabled: bool = True,
):
    """Post-analysis interactive loop — user must explicitly /exit to leave.

    Because the system now supports user feedback and debate (interactive
    capabilities), auto-exit after analysis is no longer appropriate.
    Instead we enter a command loop that stays alive until the user
    explicitly requests exit.

    Commands:
      /feedback  — Start a user feedback debate session
      /help      — Show available commands
      /exit      — Exit the program (or Ctrl+C)
    """
    console.print()
    console.print(
        Panel(
            "[bold green]Analysis complete![/bold green]\n\n"
            "[bold]Interactive mode active.[/bold] Type a command:\n\n"
            "  [cyan]/feedback[/cyan]  — Discuss the analysis with the AI\n"
            "  [cyan]/exit[/cyan]      — Exit the program\n"
            "  [cyan]/help[/cyan]      — Show this help\n"
            "  [cyan]Ctrl+C[/cyan]     — Force quit at any time",
            title="Interactive Mode",
            border_style="green",
            padding=(1, 2),
        )
    )

    while True:
        try:
            cmd = typer.prompt("", default="", prompt_suffix="> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Exiting...[/yellow]")
            break

        if not cmd:
            continue

        cmd_lower = cmd.lower()

        if cmd_lower in ("/exit", "/quit", "/q", "exit", "quit", "q"):
            console.print("[green]Goodbye.[/green]")
            break

        elif cmd_lower in ("/help", "/h", "help"):
            console.print(
                "\n[bold]Available commands:[/bold]\n"
                "  [cyan]/feedback[/cyan]  Start user feedback & debate session\n"
                "  [cyan]/exit[/cyan]      Exit the program\n"
                "  [cyan]/help[/cyan]      Show this help\n"
                "  [cyan]Ctrl+C[/cyan]     Force quit\n"
            )

        elif cmd_lower in ("/feedback", "/fb", "feedback"):
            if not feedback_enabled:
                console.print("[yellow]Feedback is disabled for this run.[/yellow]")
                continue
            if feedback_node is None:
                console.print("[yellow]Feedback node not available.[/yellow]")
                continue

            console.print("\n[bold cyan]Starting feedback session...[/bold cyan]")
            console.print("[dim](Type /done to end debate, /skip to skip)[/dim]\n")
            try:
                feedback_result = feedback_node(final_state)
                fb_summary = feedback_result.get("user_feedback_summary", "")
                if fb_summary:
                    fb_path = report_dir / "user_feedback_summary.md"
                    fb_path.write_text(fb_summary, encoding="utf-8")
                    console.print(f"\n[green]Feedback saved to {fb_path}[/green]")
                else:
                    console.print("[dim]Feedback session ended without summary.[/dim]")
            except Exception as e:
                console.print(f"[red]Feedback session error: {e}[/red]")

        else:
            console.print(
                f"[yellow]Unknown command: '{cmd}'. Type /help for available commands.[/yellow]"
            )


# 【功能】运行商品期货分析:用 LangGraph 构建「并行分析师 → 综合研判」的简化图,
#         并用 Rich Live 仪表板实时展示进度。
# 【参数】selections:get_user_selections() 返回的商品选择字典;
#         config:运行配置(已由 _build_run_config 组装);
#         stats_handler:LLM / 工具调用统计回调(用于仪表板 footer 统计)。
# 【返回】无
# 【关键逻辑】
#   - 依据用户选中的分析师,动态向 StateGraph 添加节点:技术 / 基本面 / 宏观 /
#     情绪分析师各自独立运行,最终都汇入 "synthesis" 综合节点;
#   - synthesis_node 把四份报告拼进一个中文 prompt,让 LLM 给出带
#     RATING / CONFIDENCE / SCORE 结构化头部的综合研判,写入 investment_plan 与
#     final_trade_decision;
#   - cli_progress_callback 把工具调用事件喂进 message_buffer,供仪表板展示;
#   - 使用 Live(layout, refresh_per_second=4) 实时刷新;Windows GBK 终端若启动失败
#     则回退到简单的文本输出(use_live=False);
#   - 流式执行:app.stream(..., stream_mode="values") 每个 chunk 都是完整状态,
#     用 final_state.update(chunk) 逐步累积;
#   - 结束后把各报告段落写入 .md 文件并生成 complete_report.md,
#     调用 display_complete_report() 展示,最后进入 _run_interactive_loop 反馈循环。
def _run_commodity_analysis(selections: dict, config: dict, stats_handler):
    """Run commodity futures analysis with the simplified parallel graph."""
    from tradingagents.dataflows.commodity_futures import get_variety_info
    from tradingagents.dataflows.config import set_config
    from tradingagents.llm_clients import create_llm_client

    set_config(config)

    ticker = selections["ticker"]
    trade_date = selections["analysis_date"]

    # Build commodity graph (same as commodity_demo.py)
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph

    from tradingagents.agents.analysts.commodity_analysts import (
        create_commodity_fundamental_analyst,
        create_commodity_macro_analyst,
        create_commodity_technical_analyst,
    )
    from tradingagents.agents.analysts.sentiment_analyst import (
        create_commodity_sentiment_analyst,
    )
    from tradingagents.agents.utils.agent_states import AgentState

    # Create LLM with stats tracking for the Rich Live dashboard
    llm_client = create_llm_client(
        config["llm_provider"],
        config["deep_think_llm"],
        callbacks=[stats_handler] if stats_handler else None,
    )
    llm = llm_client.get_llm()

    # CLI progress callback: feeds tool-call events into the Rich Live dashboard
    # 【功能】工具调用回调:把"工具调用 / 工具结果"事件写入 message_buffer,
    #         供 Rich Live 仪表板实时展示。
    # 【参数】event_type:"tool_call"(工具被调用)或 "tool_result"(工具返回结果);
    #         data:事件数据(含 tool_name / args / label / preview 等)。
    # 【返回】无
    def cli_progress_callback(event_type, data):
        if event_type == "tool_call":
            message_buffer.add_tool_call(data["tool_name"], data.get("args", {}))
        elif event_type == "tool_result":
            message_buffer.add_message(
                "Data",
                f"[{data.get('label', '?')}] {data['tool_name']}: {data.get('preview', '')[:180]}",
            )

    # Create analyst nodes for selected analysts
    selected = {a.value for a in selections["analysts"]}

    graph = StateGraph(AgentState)
    has_any = False

    if "commodity_technical" in selected:
        graph.add_node(
            "technical_analyst",
            create_commodity_technical_analyst(
                llm, label="Technical", progress_callback=cli_progress_callback
            ),
        )
        graph.add_edge(START, "technical_analyst")
        graph.add_edge("technical_analyst", "synthesis")
        has_any = True

    if "commodity_fundamental" in selected:
        graph.add_node(
            "fundamental_analyst",
            create_commodity_fundamental_analyst(
                llm, label="Fundamental", progress_callback=cli_progress_callback
            ),
        )
        graph.add_edge(START, "fundamental_analyst")
        graph.add_edge("fundamental_analyst", "synthesis")
        has_any = True

    if "commodity_macro" in selected:
        graph.add_node(
            "macro_analyst",
            create_commodity_macro_analyst(
                llm, label="Macro/News", progress_callback=cli_progress_callback
            ),
        )
        graph.add_edge(START, "macro_analyst")
        graph.add_edge("macro_analyst", "synthesis")
        has_any = True

    if "commodity_sentiment" in selected:
        graph.add_node(
            "sentiment_analyst",
            create_commodity_sentiment_analyst(
                llm, label="Sentiment", progress_callback=cli_progress_callback
            ),
        )
        graph.add_edge(START, "sentiment_analyst")
        graph.add_edge("sentiment_analyst", "synthesis")
        has_any = True

    if not has_any:
        console.print("[red]No commodity analysts selected![/red]")
        return

    # Synthesis node
    # 【功能】商品期货「综合研判」节点:把四份独立分析合成一份最终建议。
    # 【参数】state:当前 AgentState,含 technical / fundamental / macro / sentiment 报告。
    # 【返回】dict:{"investment_plan": 综合结论, "final_trade_decision": 同一结论}。
    # 【关键逻辑】用一段中文 prompt 让 LLM 做四维度加权判断,要求第一行必须是
    #            "RATING: [...] | CONFIDENCE: [...] | SCORE: [...]" 结构化头部;
    #            情绪数据过少(<10 条)时权重≤5%,情绪极端时按反向信号处理。
    def synthesis_node(state):
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        symbol = state["company_of_interest"]

        prompt_text = f"""You are the chief commodity strategist. Below are independent analysis reports for `{symbol}`.

**TECHNICAL ANALYSIS**:
{technical[:3000] if technical else "Not available."}

**FUNDAMENTAL ANALYSIS (Supply/Demand/Basis/Inventory)**:
{fundamental[:3000] if fundamental else "Not available."}

**MACRO & POLICY ANALYSIS**:
{macro[:3000] if macro else "Not available."}

**SENTIMENT ANALYSIS (Social Media / Market Psychology)**:
{sentiment[:2000] if sentiment else "Not available."}

Synthesize the four perspectives. Assign weights (explain why). For sentiment: if data is sparse (<10 posts), weight ≤ 5%. If sentiment is extreme, treat as contrarian signal.

**CRITICAL — First line after the title MUST be exactly this structured header:**

```
RATING: [强烈看多/偏多/中性/偏空/强烈看空] | CONFIDENCE: [高/中/低] | SCORE: [0-10]
```

Rating scale: 强烈看多/偏多/中性/偏空/强烈看空. Score: 0=max bearish, 5=neutral, 10=max bullish.

Output in Chinese:

## 综合研判
RATING: [...] | CONFIDENCE: [...] | SCORE: [...]
### 四维度一致性分析
### 加权判断 (技术/基本面/宏观/情绪)
### 最终建议
### 操作参考
"""
        result = llm.invoke(prompt_text)
        return {"investment_plan": result.content, "final_trade_decision": result.content}

    graph.add_node("synthesis", synthesis_node)

    # User Feedback node (self-evolution) — created but NOT added to graph.
    # It runs standalone after streaming completes so interactive input()
    # gets a clean terminal. Enable/disable via selections.
    feedback_enabled = not selections.get("no_feedback", False)
    feedback_node = create_user_feedback_node(llm, max_rounds=5, enabled=feedback_enabled)

    graph.add_edge("synthesis", END)

    app = graph.compile()

    # Show info
    console.print(f"\n[bold]Variety:[/bold] {ticker}")
    info = get_variety_info(ticker)
    console.print(Panel(info[:500], title="Variety Info", border_style="blue"))

    # Load evolution memory for prompt injection
    evolution_context = get_evolution_context(ticker)

    # Initial state
    initial_msg = HumanMessage(
        content=f"Analyze commodity futures variety '{ticker}' as of {trade_date}."
    )
    initial_state = {
        "messages": [initial_msg],
        "company_of_interest": ticker,
        "asset_type": "commodity_futures",
        "trade_date": trade_date,
        "past_context": evolution_context,  # Self-evolution memory
        "technical_report": "",
        "fundamental_report": "",
        "macro_report": "",
        "discussion_summary": "",
        "user_feedback_summary": "",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_plan": "",
        "final_trade_decision": "",
    }

    # Layout and display (Rich Live may fail on Windows GBK terminals;
    # fall back to simple console output on encoding errors)
    start_time = time.time()

    message_buffer.init_for_analysis(
        [a.value for a in selections["analysts"]], asset_type="commodity_futures"
    )
    # Set selected analysts to in_progress so the progress panel shows spinners
    for a in selections["analysts"]:
        agent_name = MessageBuffer.ANALYST_MAPPING.get(a.value, a.value)
        if agent_name in message_buffer.agent_status:
            message_buffer.update_agent_status(agent_name, "in_progress")
        else:
            # Ensure the agent is tracked even if not in the pre-built team mapping
            message_buffer.agent_status[agent_name] = "in_progress"

    # Commodity path: add virtual agents for report-counting purposes.
    # The synthesis node writes to investment_plan / final_trade_decision, whose
    # REPORT_SECTIONS finalizing agents are "Research Manager" / "Portfolio Manager".
    # Without them, get_completed_reports_count() never counts those sections.
    message_buffer.agent_status["Research Manager"] = "pending"
    message_buffer.agent_status["Portfolio Manager"] = "pending"

    # Log startup messages so the Messages panel is not empty
    message_buffer.add_message("System", f"Ticker: {ticker}")
    message_buffer.add_message("System", f"Date: {trade_date}")
    message_buffer.add_message("System", f"Analysts: {len(selections['analysts'])} selected")

    # 启动 Rich Live 实时仪表板:refresh_per_second=4 表示每秒重绘约 4 次。
    # 部分 Windows GBK 终端不支持 Rich Live,启动失败则回退到简单文本输出。
    try:
        layout = create_layout(commodity_mode=True)
        live_ctx = Live(layout, refresh_per_second=4)
        live_ctx.start(refresh=live_ctx._renderable is not None)
        use_live = True
    except Exception:
        use_live = False
        console.print(f"[dim]Running commodity analysis for {ticker} on {trade_date}...[/dim]")

    # Initial display update before streaming starts
    if use_live:
        try:
            update_display(layout, stats_handler=stats_handler, start_time=start_time)
        except Exception:
            use_live = False

    # Accumulate final state from stream chunks (avoids re-running analysis)
    final_state = {}
    try:
        # 流式执行:app.stream(..., stream_mode="values") 逐块产出最新完整状态。
        # 每收到一个 chunk 就更新 message_buffer 并刷新仪表板,形成"实时进度"效果。
        # Stream execution — each chunk IS the full state (stream_mode="values" default)
        for chunk in app.stream(initial_state, stream_mode="values"):
            # chunk IS the full AgentState dict (stream_mode="values")
            final_state.update(chunk)

            # Update analyst reports and status
            for report_key, analyst_key in [
                ("technical_report", "commodity_technical"),
                ("fundamental_report", "commodity_fundamental"),
                ("macro_report", "commodity_macro"),
                ("sentiment_report", "commodity_sentiment"),
            ]:
                if chunk.get(report_key):
                    agent_name = MessageBuffer.ANALYST_MAPPING.get(analyst_key, analyst_key)
                    message_buffer.update_report_section(report_key, chunk[report_key])
                    message_buffer.update_agent_status(agent_name, "completed")
                    message_buffer.add_message(
                        "Agent", f"[{agent_name}] Report complete ({len(chunk[report_key])} chars)"
                    )

            if chunk.get("investment_plan"):
                message_buffer.update_report_section("investment_plan", chunk["investment_plan"])
                message_buffer.update_report_section(
                    "final_trade_decision", chunk["investment_plan"]
                )
                # Mark synthesis-related agents as completed for report counting
                message_buffer.update_agent_status("Research Manager", "completed")
                message_buffer.update_agent_status("Portfolio Manager", "completed")
                message_buffer.add_message("Agent", "[Synthesis] Final recommendation ready")

            if chunk.get("user_feedback_summary"):
                message_buffer.update_report_section(
                    "user_feedback_summary", chunk["user_feedback_summary"]
                )
                message_buffer.add_message("Agent", "[Feedback] User debate session recorded")

            # Update the Rich Live display (or simple dots)
            if use_live:
                try:
                    update_display(layout, stats_handler=stats_handler, start_time=start_time)
                except Exception:
                    use_live = False
            else:
                # Simple mode: just print status dots
                completed = sum(
                    1
                    for k in ["technical_report", "fundamental_report", "macro_report"]
                    if message_buffer.report_sections.get(k)
                )
                total_analysts = len(list(selections["analysts"]))
                console.print(f"[dim]... {completed}/{total_analysts} analysts completed[/dim]")
    finally:
        if use_live:
            with contextlib.suppress(Exception):
                live_ctx.stop()

    # Save results
    results_dir = Path(config["results_dir"]) / ticker / trade_date
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_sections = [
        ("technical_report", "Technical Analysis"),
        ("fundamental_report", "Fundamental Analysis"),
        ("macro_report", "Macro/News Analysis"),
        ("investment_plan", "Synthesis & Recommendation"),
        ("user_feedback_summary", "User Feedback & Debate"),
    ]

    full_report = f"# Commodity Futures Analysis: {ticker}\n\n**Date**: {trade_date}\n\n---\n\n"
    for key, title in report_sections:
        content = final_state.get(key, "")
        if content:
            full_report += f"## {title}\n\n{content}\n\n---\n\n"
            (report_dir / f"{key}.md").write_text(content, encoding="utf-8")

    complete_path = report_dir / "complete_report.md"
    complete_path.write_text(full_report, encoding="utf-8")

    console.print(f"\n[green]Report saved to:[/green] {complete_path}")
    display_complete_report(final_state)

    # --- Interactive Post-Analysis Loop ---
    # Because the system is now interactive (user feedback, debate), we no longer
    # auto-exit. The user must explicitly type /exit to leave.
    _run_interactive_loop(llm, final_state, ticker, report_dir, feedback_node, feedback_enabled)


# 【功能】根据用户交互选择(并结合环境变量优先规则)组装最终运行配置 dict。
# 【参数】selections:get_user_selections() 返回的选择字典;
#         checkpoint:bool | None,是否启用断点续跑(由 --checkpoint/--no-checkpoint 传入)。
# 【返回】dict:最终运行配置(基于 DEFAULT_CONFIG 复制并覆盖)。
# 【关键逻辑】
#   - 商品期货路径:只覆盖 output_language,跳过辩论 / 风控 / LLM 等股票专属配置;
#   - 轮次与 checkpoint 遵循"显式设置优先"规则:环境变量若已设置则不覆盖交互值;
#   - 把思考 Agent、后端 URL、LLM 提供商、提供商专属推理配置写入 config;
#   - checkpoint 只有在显式传入(非 None)时才覆盖 config["checkpoint_enabled"]。
def _build_run_config(selections: dict, checkpoint: bool | None) -> dict:
    """Assemble the run config from interactive selections, honoring env precedence.

    Round counts and checkpoint follow "explicit env/flag wins": an env-applied
    value on DEFAULT_CONFIG is preserved unless the user overrode it on the CLI.
    """
    config = DEFAULT_CONFIG.copy()

    # Commodity futures: minimal config, skip debate/risk/llm selections
    if selections.get("asset_type") == "commodity_futures":
        if selections.get("output_language"):
            config["output_language"] = selections["output_language"]
        return config

    # Research depth sets both round counts, but an explicit env override
    # (TRADINGAGENTS_MAX_DEBATE_ROUNDS / _MAX_RISK_ROUNDS) wins over the
    # interactive selection — leave the env-applied value in place (#977).
    if not os.environ.get("TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        config["max_debate_rounds"] = selections["research_depth"]
    if not os.environ.get("TRADINGAGENTS_MAX_RISK_ROUNDS"):
        config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # Provider-specific thinking configuration
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    # --checkpoint/--no-checkpoint overrides only when explicitly given; omitting
    # the flag preserves TRADINGAGENTS_CHECKPOINT_ENABLED / the default (#976).
    if checkpoint is not None:
        config["checkpoint_enabled"] = checkpoint
    return config


# 【功能】主分析流程:收集用户选择 → 组装配置 → 建图 → 流式运行 → 保存与展示。
# 【参数】checkpoint:bool | None,是否启用断点续跑(默认 None,遵从环境变量)。
# 【返回】无
# 【关键逻辑】
#   - 商品期货分支:直接交给 _run_commodity_analysis() 处理并返回;
#   - 股票 / 加密分支:
#     ① 规范化分析师顺序,构建执行计划与分析耗时跟踪器;
#     ② 用 TradingAgentsGraph 创建分析图,绑定 stats_handler 回调;
#     ③ 用装饰器给 message_buffer 的写入方法附加"写日志 / 落盘"功能
#        (message_tool.log 与各报告段落 .md);
#     ④ 进入 with Live(...) 实时渲染上下文,逐 chunk 流式运行图;
#     ⑤ 每个 chunk:去重消息、更新分析师 / 团队状态、刷新仪表板;
#     ⑥ 结束后合并各 chunk 为 final_state,把全部 Agent 置为 completed;
#     ⑦ 退出 Live 后询问:是否保存报告、是否在屏幕上完整展示报告。
def run_analysis(checkpoint: bool | None = None):
    # First get all user selections
    selections = get_user_selections()

    config = _build_run_config(selections, checkpoint)

    # Create stats callback handler for tracking LLM/tool calls
    stats_handler = StatsCallbackHandler()

    # ================================================================
    # Commodity Futures Path
    # ================================================================
    # 商品期货路径:复用 commodity_demo.py 的简化并行分析图,在此单独分流。
    if selections.get("asset_type") == "commodity_futures":
        _run_commodity_analysis(selections, config, stats_handler)
        return

    # Normalize analyst selection to predefined order (selection is a 'set', order is fixed)
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]
    analyst_execution_plan = build_analyst_execution_plan(selected_analyst_keys)
    analyst_wall_time_tracker = AnalystWallTimeTracker(analyst_execution_plan)

    # Initialize the graph with callbacks bound to LLMs
    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    # Initialize message buffer with selected analysts
    message_buffer.init_for_analysis(selected_analyst_keys)

    # Track start time for elapsed display
    start_time = time.time()

    # Create result directory
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    # 【功能】包装 message_buffer.add_message:调用原方法后,把最新一条消息
    #         追加写入 log 文件(message_tool.log)。
    # 【参数】obj:被包装的对象(message_buffer);func_name:方法名。
    # 【返回】wrapper 函数(替代原方法)。
    # 【关键逻辑】把正文中的换行替换为空格,按 "时间 [类型] 内容" 格式逐行追加。
    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        # 【功能】装饰后的替代函数:先执行原 add_message,再把最新一条消息写进日志。
        # 【参数】*args, **kwargs:原 add_message 的参数。
        # 【返回】原方法的返回值。
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")

        return wrapper

    # 【功能】包装 message_buffer.add_tool_call:调用原方法后,把工具调用
    #         以 "时间 [Tool Call] 工具名(参数)" 格式追加写入 log 文件。
    # 【参数】obj:被包装的对象;func_name:方法名。
    # 【返回】wrapper 函数(替代原方法)。
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        # 【功能】装饰后的替代函数:先执行原 add_tool_call,再把最新工具调用写进日志。
        # 【参数】*args, **kwargs:原 add_tool_call 的参数。
        # 【返回】原方法的返回值。
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")

        return wrapper

    # 【功能】包装 message_buffer.update_report_section:调用原方法后,
    #         把对应报告段落单独保存为 <段落名>.md 文件。
    # 【参数】obj:被包装的对象;func_name:方法名。
    # 【返回】wrapper 函数(替代原方法)。
    # 【关键逻辑】内容若是 list 则逐项拼成字符串;段落存在且有内容时才写文件。
    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        # 【功能】装饰后的替代函数:先执行原 update_report_section,
        #         再把该报告段落单独落盘为 <段落名>.md。
        # 【参数】section_name:段落名;content:报告内容。
        # 【返回】原方法的返回值。
        def wrapper(section_name, content):
            func(section_name, content)
            if (
                section_name in obj.report_sections
                and obj.report_sections[section_name] is not None
            ):
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = (
                        "\n".join(str(item) for item in content)
                        if isinstance(content, list)
                        else content
                    )
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)

        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(
        message_buffer, "update_report_section"
    )

    # Now start the display layout
    layout = create_layout()

    # Rich Live 仪表板入口:进入 Live 上下文后,每次调用 update_display() 更新面板,
    # Rich 会以约 4 次/秒的节奏把新画面重绘到终端,形成"实时仪表板"效果。
    with Live(layout, refresh_per_second=4):
        # Initial display
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Add initial messages
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        if selections["asset_type"] != "stock":
            message_buffer.add_message("System", f"Detected asset type: {selections['asset_type']}")
        message_buffer.add_message("System", f"Analysis date: {selections['analysis_date']}")
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Update agent status to in_progress for the first analyst
        first_analyst = get_initial_analyst_node(analyst_execution_plan)
        message_buffer.update_agent_status(first_analyst, "in_progress")
        analyst_wall_time_tracker.mark_started(selected_analyst_keys[0])
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Create spinner text
        spinner_text = f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        # Initialize state and get graph args with callbacks.
        # Resolve the instrument identity once here so all agents anchor to
        # the real company (#814); the CLI builds state directly rather than
        # going through propagate(), so this must happen on the CLI path too.
        instrument_context = graph.resolve_instrument_context(
            selections["ticker"], selections["asset_type"]
        )
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"],
            selections["analysis_date"],
            asset_type=selections["asset_type"],
            instrument_context=instrument_context,
        )
        # Pass callbacks to graph config for tool execution tracking
        # (LLM tracking is handled separately via LLM constructor)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # Stream the analysis
        trace = []
        # 流式运行分析图:graph.graph.stream(...) 每个 chunk 是当前节点的增量更新。
        # 每收到一块就处理消息、更新分析师/团队状态并调用 update_display() 重绘
        # 仪表板,让用户实时看到分析进度。
        for chunk in graph.graph.stream(init_agent_state, **args):
            # Process all messages in chunk, deduplicating by message ID
            for message in chunk.get("messages", []):
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in message_buffer._processed_message_ids:
                        continue
                    message_buffer._processed_message_ids.add(msg_id)

                msg_type, content = classify_message_type(message)
                if content and content.strip():
                    message_buffer.add_message(msg_type, content)

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(tool_call["name"], tool_call["args"])
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

            # Update analyst statuses based on report state (runs on every chunk)
            update_analyst_statuses(
                message_buffer,
                chunk,
                wall_time_tracker=analyst_wall_time_tracker,
            )

            # Research Team - Handle Investment Debate State
            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                # Only update status when there's actual content
                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            # Trading Team
            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            # Risk Management Team - Handle Risk Debate State
            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                    )
                if judge and message_buffer.agent_status.get("Portfolio Manager") != "completed":
                    message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                    )
                    message_buffer.update_agent_status("Aggressive Analyst", "completed")
                    message_buffer.update_agent_status("Conservative Analyst", "completed")
                    message_buffer.update_agent_status("Neutral Analyst", "completed")
                    message_buffer.update_agent_status("Portfolio Manager", "completed")

            # Update the display
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            trace.append(chunk)

        # Streamed chunks are per-node deltas, not full state. Merge them
        # so every report field populated across the run is present.
        final_state = {}
        for chunk in trace:
            final_state.update(chunk)

        # Update all agent statuses to completed
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )
        message_buffer.add_message("System", analyst_wall_time_tracker.format_summary())

        # Update final report sections
        for section in message_buffer.report_sections:
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")
    console.print(f"[dim]{analyst_wall_time_tracker.format_summary()}[/dim]")

    # Prompt to save report
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)", default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # Prompt to display full report
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


# =====================================================================
# analyze —— 本 CLI 的「真正入口命令」(注册到 Typer)。
# 命令行用法示例:
#   python -m cli.main analyze                      # 默认交互式运行
#   python -m cli.main analyze --no-checkpoint      # 禁用断点续跑
#   python -m cli.main analyze --clear-checkpoints  # 先清空断点再运行
#
# 【功能】CLI 命令入口:可选清空已保存断点,然后启动完整分析流程。
# 【参数】
#   checkpoint:bool | None —— 三态布尔选项:
#     --checkpoint        启用断点续跑(每个节点后保存状态,崩溃后可恢复);
#     --no-checkpoint     禁用断点续跑;
#     不传                遵从 TRADINGAGENTS_CHECKPOINT_ENABLED 环境变量或默认值。
#   clear_checkpoints:bool —— 传 --clear-checkpoints 时先删除所有已保存断点
#                            (强制从零开始的分析)。
# 【返回】无
# 【关键逻辑】若指定 --clear-checkpoints,先调用 clear_all_checkpoints 清空
#            data_cache_dir 下的断点,再调用 run_analysis(checkpoint=checkpoint)。
# =====================================================================
@app.command()
def analyze(
    checkpoint: bool | None = typer.Option(
        None,
        "--checkpoint/--no-checkpoint",
        help="Enable/disable checkpoint-resume (save state after each node so a "
        "crashed run can resume). Omit to honor TRADINGAGENTS_CHECKPOINT_ENABLED.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
):
    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints

        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
    run_analysis(checkpoint=checkpoint)


# 当本文件被直接运行(python cli/main.py)时,进入 Typer 应用入口。
# 实际会解析命令行参数并调度到 analyze 命令。
if __name__ == "__main__":
    app()
