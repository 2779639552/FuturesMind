"""analyze_one.py — 独立分析师调用脚本(不依赖 LangGraph 图 / 不依赖本机其他服务)

直接调用 tradingagents 里的分析师工厂函数(create_commodity_*_analyst),
单独输出某一品种的技术/基本面/宏观/情绪分析报告。适合打包发给其他人,
在他们自己的电脑上用他们自己的 API Key 运行。

用法:
    python analyze_one.py RB                          # 三个核心分析师(技术+基本面+宏观)
    python analyze_one.py RB --analyst technical      # 只跑技术面
    python analyze_one.py RB --analyst sentiment      # 只跑情绪面
    python analyze_one.py RB --date 2026-09-02 --analyst all
    python analyze_one.py RB --json                   # 以 JSON 输出全部报告

对方电脑需要:
    1. Python 3.10+
    2. pip install -r min-requirements.txt
    3. 与本脚本同目录放 .env(可参考仓库 .env.example),填你自己的 API Key
    4. 能访问所配置 LLM 的 API(默认 DeepSeek: https://api.deepseek.com)
    5. 能访问国内行情站点(akshare 实时取数,取不到时工具会返回 DATA_ERROR 文本)

注意: .env 必须先于任何 tradingagents 导入加载 —— DEFAULT_CONFIG 在模块
导入时就用环境变量覆盖默认值,顺序错了 key 不会生效。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# 让脚本从任意工作目录启动都能 import 到同目录的 tradingagents 包
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ⚠️ 顺序关键: 必须在 import tradingagents 之前加载 .env
# load_dotenv 显式指定脚本所在目录的 .env —— 否则对方在别的目录运行会找不到 key
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

from langchain_core.messages import HumanMessage  # noqa: E402
from tradingagents.agents.analysts.commodity_analysts import (  # noqa: E402
    create_commodity_fundamental_analyst,
    create_commodity_macro_analyst,
    create_commodity_technical_analyst,
)
from tradingagents.agents.analysts.sentiment_analyst import (  # noqa: E402
    create_commodity_sentiment_analyst,
)
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402

# 分析师工厂注册表:名称 → (工厂函数, 输出报告的 dict 键)
ANALYSTS: dict[str, tuple] = {
    "technical": (create_commodity_technical_analyst, "technical_report"),
    "fundamental": (create_commodity_fundamental_analyst, "fundamental_report"),
    "macro": (create_commodity_macro_analyst, "macro_report"),
    "sentiment": (create_commodity_sentiment_analyst, "sentiment_report"),
}
# "all" = 三个核心分析师;情绪面需要该品种有情绪数据,默认不包含(可显式 --analyst sentiment)
CORE_ANALYSTS = ["technical", "fundamental", "macro"]

# provider → 用于取 API Key 的环境变量名
PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


def console_progress_callback(event_type: str, data):
    """把工具调用过程打印到终端,让对方看到分析在干什么。"""
    if event_type == "tool_call":
        name = data.get("tool_name") if isinstance(data, dict) else data
        args = data.get("args_brief") if isinstance(data, dict) else None
        print(f"  · 调工具: {name} {args or ''}")
    elif event_type == "report_start":
        print("  · 开始撰写报告...")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="独立分析师调用脚本: 单品种 · 单个/全部分析师 · 输出报告或 JSON",
    )
    ap.add_argument("variety", help="品种代码, 如 RB / CU / SA")
    ap.add_argument(
        "--date",
        default=None,
        help="交易日 YYYY-MM-DD, 默认今天",
    )
    ap.add_argument(
        "--analyst",
        default="all",
        choices=["all", *ANALYSTS.keys()],
        help="要运行的分析师; 默认 all(技术+基本面+宏观)",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    config = DEFAULT_CONFIG.copy()
    provider = config.get("llm_provider", "").lower()
    key_env = PROVIDER_KEY_ENV.get(provider)

    if key_env and not os.environ.get(key_env):
        print(
            f"[ERROR] 缺少 API Key。请在 .env 里设置 {key_env}"
            f"(当前 provider={provider or '(未设置, 默认 openai)'})",
            file=sys.stderr,
        )
        print("提示: 复制 .env.example 为 .env, 填 DEEPSEEK_API_KEY 即可。", file=sys.stderr)
        return 1

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    symbol = args.variety.upper()

    print(f"品种: {symbol}  交易日: {trade_date}  provider: {provider}")

    # 与 commodity_demo.py 一致: 分析师用 quick_llm(快而省)
    llm = create_llm_client(
        config["llm_provider"],
        config.get("quick_think_llm", config["deep_think_llm"]),
    ).get_llm()

    chosen = list(ANALYSTS.keys()) if args.analyst == "all" else [args.analyst]
    if args.analyst == "all":
        chosen = CORE_ANALYSTS

    reports: dict[str, str] = {}
    # 与 commodity_demo.py 的 initial_state 一致:节点需要 messages(对话历史)
    state = {
        "trade_date": trade_date,
        "company_of_interest": symbol,
        "messages": [HumanMessage(content=f"Analyze {symbol} as of {trade_date}.")],
    }
    for name in chosen:
        factory, report_key = ANALYSTS[name]
        print(f"\n=== {name.upper()} 分析师 ===")
        node = factory(llm, label=name.capitalize(), progress_callback=console_progress_callback)
        out = node(state)
        reports[name] = out.get(report_key, "ANALYSIS_ERROR: 未返回报告")

    if args.json:
        payload = {
            "variety": symbol,
            "trade_date": trade_date,
            "provider": provider,
            "reports": reports,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for name, report in reports.items():
            print(f"\n{'=' * 16} {name.upper()} {'=' * 16}\n{report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
