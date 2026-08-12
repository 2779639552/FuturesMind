"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

from datetime import datetime  # 【调用包】生成报告时间戳
from pathlib import Path  # 【调用包】跨平台路径操作与写盘


# 【功能】把一次完整运行的各段报告写入 save_path 目录树
#         (1_analysts / 2_research / 3_trading / 4_risk / 5_portfolio),
#         并生成合并的 complete_report.md。
# 【参数】final_state:运行终态字典(含 market_report / investment_debate_state 等报告字段);
#         ticker:资产代码(用于报告标题);save_path:保存目录(不存在时自动创建)。
# 【返回】Path:complete_report.md 的完整路径。
# 【关键】CLI 与 API(save_reports)共用此写入器,保证两种入口落盘结果一致;
#         各段只在对应报告字段存在时才写盘,缺段自动跳过。
def write_report_tree(final_state: dict, ticker: str, save_path) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path."""
    save_path = Path(save_path)  # 【变量】规整为 Path 对象(兼容 str / Path 两种入参)
    save_path.mkdir(parents=True, exist_ok=True)  # 【调用函数】递归创建目录,已存在不报错
    sections = []  # 【变量】累积各段 Markdown 文本,最后拼进 complete_report.md

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"  # 【变量】分析师报告子目录
    analyst_parts = []  # 【变量】(分析师名, 报告正文) 元组列表,用于拼 I 段
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(
            final_state["sentiment_report"], encoding="utf-8"
        )
        analyst_parts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(
            final_state["fundamentals_report"], encoding="utf-8"
        )
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"  # 【变量】研究辩论报告子目录
        debate = final_state["investment_debate_state"]  # 【变量】研究辩论状态(Bull/Bear/Manager)
        research_parts = []  # 【变量】(研究员名, 报告正文) 元组列表,用于拼 II 段
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(
            final_state["trader_investment_plan"], encoding="utf-8"
        )
        sections.append(
            f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}"
        )

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"  # 【变量】风控辩论报告子目录
        risk = final_state["risk_debate_state"]  # 【变量】风控辩论状态(激进/保守/中性/组合经理)
        risk_parts = []  # 【变量】(风控分析师名, 报告正文) 元组列表,用于拼 IV 段
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(
                risk["conservative_history"], encoding="utf-8"
            )
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(
                f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}"
            )

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"  # 【变量】合并报告标题与生成时间
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")  # 【调用函数】UTF-8 落盘合并报告
    return save_path / "complete_report.md"
