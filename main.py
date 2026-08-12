from tradingagents.default_config import DEFAULT_CONFIG  # 【调用包】默认运行配置(已应用 TRADINGAGENTS_* 环境变量覆盖)
from tradingagents.graph.trading_graph import TradingAgentsGraph  # 【调用包】核心分析图(多 Agent 交易决策图)


# 【功能】命令行最小入口:用默认配置创建分析图,对一只示例股票(NVDA)跑一次完整前向传播并打印决策。
# 【参数】无
# 【返回】无
# 【关键】这是最简调用示例,与 CLI / Web 入口不同,不做任何交互;换模型/换端点只需改 .env。
def main():
    # DEFAULT_CONFIG already applies TRADINGAGENTS_* env-var overrides
    # (llm_provider, deep_think_llm, quick_think_llm, backend_url, etc.),
    # so users can switch models or endpoints purely via .env without
    # editing this script. Override individual keys here only when you
    # want a hard-coded value that should ignore the environment.
    config = DEFAULT_CONFIG.copy()  # 【变量】运行配置副本(复制以免污染全局默认值)

    # Initialize with custom config
    ta = TradingAgentsGraph(debug=True, config=config)  # 【变量】分析图实例,debug 模式便于排查

    # forward propagate
    _, decision = ta.propagate("NVDA", "2024-05-10")  # 【调用函数】跨模块图调用:前向传播整条分析链,返回(状态, 决策)
    print(decision)  # 【调用函数】终端输出最终投资决策

    # Memorize mistakes and reflect
    # ta.reflect_and_remember(1000) # parameter is the position returns


if __name__ == "__main__":
    main()
