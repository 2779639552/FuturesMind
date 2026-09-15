# FuturesMind Changelog

All notable changes to FuturesMind (formerly AgentSense), the
commodity futures multi-agent research framework with social media
sentiment fusion.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

FuturesMind is built on top of [TradingAgents](https://github.com/TauricResearch/TradingAgents);
see `CHANGELOG.md` for the upstream framework history.

---

## [2.9.0] — 2026-07-30

### Added

- **ZCE variety expansion**: 8→21 supported varieties covering ferrous, non-ferrous,
  energy/chemical, and agricultural sectors (FG, SA, UR, PF, CF, SR, OI, RM, AP, CJ,
  PK, SM, SF).
- **Sector sub-categories**: `sector_cn` field for all 21 varieties (was missing,
  `_get_sector()` previously returned "其他" for everything).
- **Debate agent tool access**: Bull/Bear/Moderator agents now use `_run_tool_loop()`
  with 6 real-time tools (live price, verified quote, futures price, indicators,
  sentiment, variety info) instead of text-only `llm.invoke()`.
- 26 CLI variety codes (up from 14).

### Changed

- CLI code list expanded: `_COMMODITY_CODES` 14→26.
- Live price varieties: `DEFAULT_LIVE_VARIETIES` 19→24.

---

## [2.8.0] — 2026-07-29

### Added

- **Xueqiu platform**: Playwright-based adapter (`xueqiu_adapter.py`), 842 raw posts
  across 3 collection rounds, covering 32 varieties.
- **Advanced backtesting metrics**: 13 institutional-grade indicators (Sharpe,
  Sortino, Calmar ratios, annualized return/volatility, profit factor, avg win/loss,
  drawdown duration) via `compute_advanced_metrics()`.
- **Momentum+Adaptive fusion strategy**: `run_momentum_adaptive()` with quality gate
  that tracks rolling baseline PnL and reduces contrarian intervention when baseline
  performs well.
- **Dual-Y-axis charts**: All price-overlay charts now show strategy PnL (left axis)
  + price change % (right axis).
- **Real-time prices**: AKShare-based live price fetching for 17 varieties with
  5-min refresh during trading hours (`price_fetcher.py` + `warm_cache.py`).

### Changed

- **DMAC strategies removed**: All DMAC variants deleted (180-day window produced
  only 2-3 trades). Cleaned from `signal_analyzer.py`, `web_app.py`, and
  `web_template.html`.
- **Strategy renamed**: "顺情绪(跟随/基线)" → "情绪共识";
  "动量(5/3)" → "动量(纯价格)".
- **Unified frontend renderer**: `_renderUnifiedResult()` replaces 5 separate
  render functions with a lookup-table router.
- **Backtest weight tuning**: `dir_weight` 0.5→0.8, `softmax temperature` 0.5→0.25.
- **Data filtered to 2026**: 562 pre-2026 records removed, 8,966 retained.

### Fixed

- Multi-strategy comparison `const prefer =` ReferenceError.
- `index()` missing `return resp` causing HTTP 500.
- Damaged if/else chain in JS (missing opening `if`).
- Xueqiu frontend registration (CSS, checkbox, platform selector, name mapping).

---

## [2.7.0] — 2026-07-22

### Added

- **Strategy comparison system**: 7 strategies on one chart with checkbox toggles,
  market baseline (buy-and-hold), per-strategy stats (trades/win rate/cumulative
  return/excess return).
- **Agent validation module**: `/api/agent_validation/start|status` endpoints,
  full-variety RATING/SCORE/CONFIDENCE vs actual prices, confidence calibration
  (high/medium/low), SCORE-return scatter plot.
- **Discussion evolution**: `/api/feedback` endpoint, user sends viewpoint → Agent
  replies with evolution memory context, chat UI in web frontend.
- **Evolution memory persistence**: 3 varieties with stored history.
- **3-platform full collection** (2026-01-01~2026-07-20): 546 new posts (Weibo 326,
  Zhihu 125, XHS 95). Cumulative: 1,666 deduplicated, 998 authors, 55 varieties.
- 58 API routes, 8 tab pages, local static files (zero CDN dependency).

### Changed

- **Frontend refactored**: 8-panel analysis tools → 5-panel (merged 6 modules,
  removed cross-platform). Framework explanation panel with original vs FuturesMind
  comparison + 11-node pipeline diagram.
- **Event cards enhanced**: variety direction arrows (↑↓→) + sentiment labels.
- **CDN elimination**: All Chart.js and CSS moved to `/static/`, eliminating
  CDN dependency completely.

### Fixed

- Lock→RLock deadlock in `to_dict()` nested calls.
- Divergence 100% bug (reading `social_sentiment` instead of `daily_series`).
- Sentiment ranking 0-score bug (bullish-bearish calculation).
- Author numeric ID error (field name mismatch: posts/fans/avg_engagement).
- Sentiment events empty (reading JSONL instead of database).
- Chart curves missing (jsdelivr CDN→bootcdn→local static).
- Win rate 0% (`allCurves` residual→`allPnlMaps`).
- `marketReturn` undefined JS error.

---

## [2.4.0] — 2026-07-20

### Added

- **4th sentiment analyst**: Social media sentiment fusion via `sentiment_analyst.py`
  (303 lines) with `sentiment_data.py` (240 lines) for prompt formatting.
- **Author influence weighting v3**: 3-dimensional fusion (engagement × followers ×
  domain relevance), implemented across all 3 platform adapters.
- **Multi-round adversarial debate**: Bull/Bear/Moderator 3-node debate system
  (`commodity_debate.py`) replacing single-round Discussion node. Bull has final
  rebuttal right.
- **11-node LangGraph pipeline**: START → 4 Analysts (parallel) → Bull(R1) →
  Bear(R1) → Bull(R2) → Moderator → Synthesis → Scenario → END.
- **Dual LLM tier**: `quick_llm` (analysts + debate) + `deep_llm` (synthesis +
  scenario analysis).
- **Pydantic structured output** (`schemas_commodity.py`, 207 lines): CommodityBias,
  Confidence, AnalystReport, SynthesisReport, DebateModeratorReport.
- **Web frontend** (`web_app.py` 465 lines + `web_template.html`): Flask + SSE +
  Chart.js, 5 tab pages (analysis, sentiment, backtest, data update, history).
- **Mandatory markdown summary tables** in all 4 analyst prompts.

### Changed

- 14 files modified across TradingAgents core (interface, config, tools, CLI)
  and 思路2 (platform adapters, trend aggregator, backtest).
- `trend_aggregator.py` rewritten to v3 with 3D weight fusion.
- `backtest_weights.py` rewritten to v2 with multi-horizon + signal comparison +
  grid search.

---

## [2.3.0] — 2026-07-16

### Added

- **P0 macro & supply-demand data**: `get_futures_macro()` (GDP, PMI, FAI, REI, IP,
  Construction Index) and `get_futures_supply_demand()` (production, transaction,
  hot metal, BF/EAF rates, mill profit, social inventory).
- **Dual-source news**: Eastmoney 7x24 (30 articles) + SHMET (10 articles) with
  keyword filtering. CLS endpoint noted as dead (404).
- **Recency bias control**: 3-layer checks — anti-recency rule in technical analyst,
  counterfactual challenge in discussion node, mandatory recency bias check in
  synthesis node.
- **Fundamentals-first weighting**: Default ratio Fundamentals ≥ Macro ≥ Technicals.
- **Multi-variety testing**: 8 varieties validated against market research
  (87.5% directional match).
- **Comparison visualization**: `print_comparison_report()` with 8-dimension
  comparison, auto-saved as `*_comparison.md`.

### Fixed

- **Critical visibility bug**: `app.stream()` defaulting to `"updates"` mode instead
  of `"values"` — analyst reports never reached the Live display.
- Emoji GBK crash on Windows (all console output now ASCII-safe).
- `init_for_analysis` adding wrong agents, `app.invoke()` double-execution.
- Report counting and `report_sections` filtering.

---

## [2.2.0] — 2026-07-15

### Added

- **AKShare data layer rewrite**: `commodity_futures.py` (820 lines), 5 interfaces
  (price, indicators, basis, inventory, news), 8 varieties.
- **External data injection**: `external_data.py` (458 lines), JSON/YAML-based
  Mysteel-grade data injection with staleness check (168h fallback).
- **3 commodity analysts**: Technical, Fundamental, Macro/News, each with
  `_run_tool_loop()` (max 6 iterations).
- **CLI integration**: 3 new `AnalystType` entries, auto-detect commodity tickers,
  14 variety codes.
- **Architecture documentation**: 5 documents (570+ lines each).

### Changed

- Core modifications: `agent_states.py` (+3 fields), `interface.py` (+4 categories,
  +6 methods), `default_config.py` (+4 vendor defaults).

---

## [2.1.0] — 2026-07-19 (思路2)

### Added

- **Multi-platform social media collection**: Weibo, Zhihu, Xiaohongshu adapters.
- **NER variety recognition**: `ner.py` (775 lines), 50 varieties × multiple aliases,
  contract code matching, variety co-occurrence detection.
- **Dual-engine sentiment**: Rule-based (`sentiment.py`, 600 lines, 7-level
  classification) + LLM-based (`llm_sentiment.py`, 326 lines).
- **Multimodal image analysis**: `multimodal_analyzer.py` (582 lines) +
  `image_pipeline_v2.py` (395 lines), Stage1 classification + Stage2 structured
  sentiment via local VLM (granite2B + qwen2.5vl:3b).
- **Sentiment vs price dashboard**: `dashboard.py` + `trend_aggregator.py`, 55
  variety sentiment time series + 39 variety price data, 5.4 MB interactive HTML.
- **One-click pipeline**: `daily_update.py` — collect→dedupe→NER→sentiment→aggregate→
  price→dashboard.
- **TradingAgents bridge**: `generate_tradingagents_sentiment.py` → 47 variety
  sentiment JSON → `~/.tradingagents/external_data/`.
