# 环节耗时测量:复刻 web_app.run_analysis 的装配方式(图构建 + stream updates 逐节点驱动),
# 对每个环节(节点)记录墙钟耗时,输出"自身耗时 / 累计耗时 / 环节占比"三列。
# 不修改任何生产代码;需要 DeepSeek API key(从 .env 读取),跑一次完整分析约 5~10 分钟。
#
# 并行语义说明:
#   四名分析师(技术/基本面/宏观/情绪)从 START 并行启动 → 各自 yield 时刻 − 流开始时刻 = 该分析师真实耗时;
#   辩论/综合/情景是串行节点,自身耗时 = 本节点 yield − 前一节点 yield。
#
# 用法:
#   venv/Scripts/python scripts/measure_stage_timing.py [品种] [日期]
#   例: venv/Scripts/python scripts/measure_stage_timing.py RB 2026-07-14

import sys
import time

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])  # 项目根目录

from langchain_core.messages import HumanMessage

from commodity_demo import build_commodity_graph
from tradingagents.agents.utils.agent_states import AgentState  # noqa: F401  (类型契约,保持与图一致)
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.evolution_memory import get_evolution_context
from tradingagents.dataflows.sentiment_data import should_include_sentiment
from tradingagents.default_config import DEFAULT_CONFIG

# 环节名 → 中文显示名(与 web_app.PIPELINE_STAGES 一一对应)
STAGE_NAMES = {
    "technical_analyst": "技术分析",
    "fundamental_analyst": "基本面",
    "macro_analyst": "宏观/新闻",
    "sentiment_analyst": "情绪分析",
    "bull_opening": "多方立论",
    "bear_refute": "空方反驳",
    "bull_rebuttal": "多方反驳",
    "debate_moderator": "辩论裁决",
    "synthesis": "综合研判",
    "scenario_analysis": "情景分析",
}
PARALLEL_ANALYSTS = {"technical_analyst", "fundamental_analyst", "macro_analyst", "sentiment_analyst"}


def main():
    args = sys.argv[1:]
    symbol = "RB"
    trade_date = "2026-07-14"
    pos = [a for a in args if not a.startswith("--")]
    if pos:
        symbol = pos[0].upper()
    if len(pos) > 1:
        trade_date = pos[1]

    t_env = time.time()
    config = DEFAULT_CONFIG.copy()
    set_config(config)

    include_sentiment = should_include_sentiment(symbol)
    print(f"[*] 品种={symbol} 日期={trade_date} include_sentiment={include_sentiment}")
    print(f"[*] LLM: {config.get('llm_provider')} / quick={config.get('quick_think_llm')} / deep={config.get('deep_think_llm')}")

    t_build0 = time.time()
    app, _ = build_commodity_graph(
        config, enable_feedback=False, include_sentiment=include_sentiment
    )
    t_build = time.time() - t_build0
    print(f"[*] 图构建耗时 {t_build:.1f}s(无 LLM 调用,仅工厂+编译)")

    evo_ctx = get_evolution_context(symbol)
    initial_state = {
        "messages": [HumanMessage(content=f"Analyze {symbol} as of {trade_date}.")],
        "company_of_interest": symbol,
        "asset_type": "commodity_futures",
        "trade_date": trade_date,
        "past_context": evo_ctx,
        "technical_report": "",
        "fundamental_report": "",
        "macro_report": "",
        "sentiment_report": "",
        "discussion_summary": "",
        "user_feedback_summary": "",
        "investment_plan": "",
        "final_trade_decision": "",
        "scenario_analysis": "",
        "debate_state": {"bull_history": "", "bear_history": "", "bull_last": "",
                         "bear_last": "", "round": 0},
    }

    # 流开始时刻:四名分析师从 START 并行启动,这就是并行块的第 0 时刻。
    t_stream0 = time.time()
    yields = []  # [(node, cum_sec)]
    for chunk in app.stream(initial_state, stream_mode="updates"):
        now = time.time() - t_stream0
        for node_name, node_data in chunk.items():
            if node_name in STAGE_NAMES:
                yields.append((node_name, now))
    t_total = time.time() - t_stream0

    # ---- 计算各环节真实耗时 ----
    order = [n for n, _ in yields]
    cum = {n: c for n, c in yields}
    times = {}          # node -> 真实耗时
    last_parallel = None
    for i, (n, c) in enumerate(yields):
        if n in PARALLEL_ANALYSTS:
            times[n] = c                      # 并行:从流开始就算
            last_parallel = max(last_parallel, c) if last_parallel is not None else c
        else:
            prev = yields[i - 1][1] if i > 0 else 0.0
            times[n] = c - prev

    # 串行节点起点修正:fan-in 后第一个串行节点从"最后一名分析师完成"才开始
    # (即 bull_opening 起点 = last_parallel 而非上一条 yield)
    serial_keys = [n for n in order if n not in PARALLEL_ANALYSTS]
    if serial_keys:
        first = serial_keys[0]
        idx = order.index(first)
        times[first] = cum[first] - (last_parallel or 0.0)

    # ---- 输出表格 ----
    print("\n=== 各环节耗时(真实墙钟,含并行) ===")
    print(f"{'环节':<12}{'类型':<6}{'自身耗时(s)':<12}{'累计(s)':<10}{'占总分析%'}")
    for n, c in yields:
        typ = "并行" if n in PARALLEL_ANALYSTS else "串行"
        dur = times[n]
        pct = dur / t_total * 100 if t_total else 0
        print(f"{STAGE_NAMES[n]:<12}{typ:<6}{dur:<12.1f}{c:<10.1f}{pct:.1f}%")

    print("-" * 52)
    print(f"图构建         {t_build:.1f}s")
    print(f"分析总耗时(stream) {t_total:.1f}s  (含 {len(order)} 个节点, "
          f"并行块占 {max(cum[n] for n in order if n in PARALLEL_ANALYSTS) or 0:.0f}s)")
    print(f"串行段(辩论+综合+情景)合计 {sum(times[n] for n in serial_keys):.1f}s")

    # 汇总留档(便于对比)
    with open("scripts/_stage_timing_result.txt", "w", encoding="utf-8") as f:
        f.write(f"symbol={symbol} date={trade_date} include_sentiment={include_sentiment}\n")
        f.write(f"build={t_build:.1f}s total_stream={t_total:.1f}s\n")
        for n, c in yields:
            f.write(f"{n}\t{STAGE_NAMES[n]}\t{times[n]:.1f}\t{c:.1f}\n")


if __name__ == "__main__":
    main()
