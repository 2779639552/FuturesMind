"""数据流包: 各类外部数据源的取数与清洗实现。

涵盖行情、新闻、情绪、基本面与预测市场等外部数据源:
- Yahoo Finance (yfinance 行情/新闻, stockstats 指标)
- Alpha Vantage (行情/基本面/技术指标/新闻情绪/内部人交易)
- Reddit / StockTwits (社交媒体情绪)
- FRED (宏观经济数据)
- Polymarket (预测市场概率)
- 通用工具 (symbol_utils 符号归一化, market_data_validator 行情校验快照,
  stockstats_utils 指标计算, utils 路径安全/日期工具)
"""
