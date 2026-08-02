# FuturesMind Architecture

## High-Level Design

AgentSense extends the TradingAgents multi-agent framework with commodity futures specialization and social media sentiment fusion.

```
                         ┌──────────────────────────┐
                         │        User Input         │
                         │   (symbol, date, config)  │
                         └──────────┬───────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
              │ Technical │  │Fundamental│  │Macro/News │
              │  Analyst  │  │  Analyst  │  │  Analyst  │
              └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │  Sentiment Analyst   │
                         │  (Social Media NLP)  │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
              │   Bull    │  │   Bear    │  │ Moderator │
              │  Debater  │  │  Debater  │  │(FactCheck)│
              └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │     Synthesis       │
                         │  (Final Report)     │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   Scenario Proj.    │
                         │  (Price Scenarios)  │
                         └─────────────────────┘
```

## Agent Details

### 1. Technical Analyst
**Tools**: RSI, MACD, Bollinger Bands, SMA crossovers, volume analysis
**Data**: AKShare futures daily bars, technical indicators via stockstats
**Max iterations**: 6 rounds of tool calling

### 2. Fundamental Analyst
**Tools**: Supply/demand data, inventory levels, production schedules
**Data**: AKShare commodity fundamentals, industry reports
**Max iterations**: 6 rounds

### 3. Macro/News Analyst
**Tools**: News sentiment, policy analysis, geopolitical risk
**Data**: Financial news APIs, policy announcements, global macro indicators
**Max iterations**: 6 rounds

### 4. Sentiment Analyst
**Tools**: Social media sentiment, crowd情绪, author influence data
**Data**: Multi-platform social media pipeline (Weibo, Zhihu, Xueqiu, Xiaohongshu)
**Max iterations**: 4 rounds

### 5-7. Debate Layer (Bull / Bear / Moderator)
**Tools**: `get_realtime_price`, `get_verified_quote`, `get_futures_price`, `get_indicators`, `get_sentiment`, `get_variety_info`
**Pattern**: Tool-calling loop (max 3 iterations)
- Bull presents bullish case with data evidence
- Bear rebuts with bearish counter-evidence
- Moderator fact-checks claims against real-time market data

### 8-9. Synthesis & Scenario
**Model**: Deep-thinking LLM (deepseek-v4-pro)
**Output**: Structured report with BIAS/RATING headers, price scenarios, risk assessment

## Data Pipeline

```
Social Media Platforms (Weibo/Zhihu/Xueqiu/XHS)
        │
        ▼
   Data Collection (Playwright + Spider_XHS)
        │
        ▼
   Deduplication (content hash + edit distance)
        │
        ▼
   NER (50 commodities × multi-alias + contract codes)
        │
        ▼
   Sentiment (Rule engine + LLM dual-engine)
        │
        ▼
   Multimodal (VLM image → sentiment for posts with images)
        │
        ▼
   Aggregation (time-series by variety + author weighting)
        │
        ▼
   TradingAgents Sentiment JSON → Analyst tool input
```

## LLM Strategy

| Node | Model | Mode |
|------|-------|------|
| Analysts (Tech/Fund/Macro/Sentiment) | quick_llm | Parallel tool-calling |
| Bull/Bear/Moderator | quick_llm | Debate + real-time data verification |
| Synthesis + Scenario | deep_llm | Structured report generation |

## Key Design Decisions

1. **Tool-calling over pure text**: All analysts and debaters use LangGraph tool loops to fetch real data — no hallucinated numbers
2. **Adversarial debate**: Bull/Bear debate with independent tool access prevents confirmation bias
3. **Author-weighted sentiment**: Not all social media posts are equal — engagement, follower count, and domain expertise modulate influence
4. **Sector-aware analysis**: 21 varieties grouped into 4 sectors with sub-sector granularity (e.g., ferrous alloys vs. ferrous ores)
5. **Graceful degradation**: Tool call failures auto-fallback to simple LLM invoke; no run aborts mid-analysis
