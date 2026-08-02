<p align="center">
  <img src="https://img.shields.io/badge/FuturesMind-v2.9-2563eb?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-Apache%202.0-green?style=for-the-badge" alt="License">
</p>

<h1 align="center">🧠 FuturesMind</h1>
<h3 align="center">Multi-Agent LLM Framework for Commodity Futures Research<br>with Social Media Sentiment Fusion</h3>

<p align="center">
  <a href="#-overview">Overview</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-features">Features</a> •
  <a href="#-performance">Performance</a> •
  <a href="#-citation">Citation</a>
</p>

---

## 📖 Overview

**FuturesMind** is a multi-agent LLM framework that adapts the [TradingAgents](https://github.com/TauricResearch/TradingAgents) architecture for **Chinese commodity futures markets**, fusing traditional quantitative analysis with **real-time social media sentiment** from Weibo, Zhihu, Xueqiu, and Xiaohongshu.

> ⚡ 11 specialized agents collaborate in a structured debate to produce market analysis reports — covering 21 commodity varieties across ferrous metals, non-ferrous metals, energy/chemicals, and agricultural products.

### Why AgentSense?

| Challenge | FuturesMind Solution |
|-----------|-------------------|
| Commodity markets driven by policy & sentiment | Social media NLP + multi-platform sentiment fusion |
| Single-analyst bias | Bull vs. Bear adversarial debate with tool-calling verification |
| Data scattered across platforms | Unified pipeline: scrape → NER → sentiment → aggregate |
| Generic models miss commodity specifics | 21-variety custom metadata, 50-entity NER, sector-aware analysis |

---

## 🏗 Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            START                         │
                    └──────────┬───┬───┬───┬──────────────────┘
                               │   │   │   │
                    ┌──────────┘   │   │   └──────────┐
                    ▼              ▼   ▼               ▼
              ┌──────────┐  ┌──────────────┐  ┌──────────────┐
              │Technical │  │ Fundamental  │  │   Macro/News │
              │ Analyst  │  │   Analyst    │  │   Analyst    │
              └────┬─────┘  └──────┬───────┘  └──────┬───────┘
                   │               │                  │
                   └───────────────┼──────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │     Sentiment Analyst        │
                    │  (Social Media + Crowd)      │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │       Bull vs. Bear          │
                    │    Adversarial Debate        │
                    │  (6 tools × real-time data)  │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │        Moderator             │
                    │    Fact-checking & Verdict   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │    Synthesis → Scenario      │
                    │  Final Report & Projections  │
                    └─────────────────────────────┘
```

**11 Nodes** — 4 parallel analysts → Sentiment fusion → Adversarial debate (Bull/Bear/Moderator) → Synthesis → Scenario projection

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- DeepSeek API key ([get one free](https://platform.deepseek.com))

### Installation

```bash
git clone https://github.com/2779639552/FuturesMind.git
cd FuturesMind

# Create virtual environment
python -m venv venv

# Install with all dependencies
venv\Scripts\pip install -e .      # Windows
# source venv/bin/pip install -e .  # macOS/Linux
```

### Configure

```bash
cp .env.example .env
# Edit .env — add your DEEPSEEK_API_KEY
```

### Run

```bash
# Web Dashboard (recommended)
venv\Scripts\python web_app.py
# Open http://localhost:5000

# CLI Interactive Mode
venv\Scripts\tradingagents

# Single Commodity Deep Analysis
venv\Scripts\python commodity_demo.py SA 2026-08-02   # Soda Ash
venv\Scripts\python commodity_demo.py RB 2026-08-02   # Rebar
```

> **Windows users**: Double-click `start_web.bat` or `start_web.ps1` in the project root.

---

## ✨ Features

### 🔬 Multi-Agent Analysis
- **4 Parallel Analysts**: Technical (RSI, MACD, Bollinger), Fundamental (supply/demand, inventory), Macro/News (policy, geopolitics), Sentiment (social media)
- **Adversarial Debate**: Bull vs. Bear with 6 real-time tool calls (price, indicators, sentiment, verified quotes)
- **Moderator**: Fact-checks debate claims against live market data

### 📊 Social Media Sentiment Fusion
- **4 Platforms**: Weibo, Zhihu, Xueqiu (Snowball), Xiaohongshu
- **50 Entity NER**: Commodity-specific named entity recognition with multi-alias support
- **Dual-Engine Sentiment**: Rule-based (domain lexicon) + LLM-based deep understanding
- **VLM Image Analysis**: Granite + Qwen2.5-VL two-stage multimodal pipeline
- **Author Influence Weighting**: 3D weights (engagement × followers × domain expertise)

### 📈 Real-Time Data & Backtesting
- **AKShare Integration**: 24 varieties live pricing with 15-min caching
- **7 Trading Strategies**: Momentum, Adaptive, DMAC, Donchian, Contrarian, Trailing, Comparison
- **13 Backtest Metrics**: Accuracy, Sharpe, Win Rate, Max Drawdown, Cross-platform weights

### 🖥️ Web Dashboard
- **SSE Streaming**: Real-time 11-node progress visualization
- **Sentiment vs. Price**: Interactive Chart.js plots with variety filtering
- **Backtest Panel**: Platform weight charts, variety rankings, KPI cards
- **Report Archive**: 20+ historical analysis reports with prediction-vs-actual comparison

---

## 📦 Supported Commodities (21 Varieties)

| Sector | Varieties |
|--------|-----------|
| 🏗 Ferrous Metals | RB (Rebar), I (Iron Ore), HC (HRC), JM (Coking Coal), J (Coke), SM (Mn-Si), SF (Si-Fe) |
| 🔩 Non-Ferrous | CU (Copper), AL (Aluminum), ZN (Zinc), NI (Nickel), PB (Lead), SN (Tin), AU (Gold), AG (Silver) |
| ⚡ Energy/Chemical | FG (Glass), SA (Soda Ash), UR (Urea), PF (Polyester Staple), MA (Methanol), TA (PTA) |
| 🌾 Agricultural | M (Soybean Meal), CF (Cotton), SR (Sugar), OI (Rapeseed Oil), RM (Rapeseed Meal), AP (Apple), PK (Peanut) |

---

## 📊 Performance

| Metric | Value |
|--------|:----:|
| Data Collected | **8,966 posts** (2026) |
| Varieties Covered | **21** (with real-time pricing) |
| Backtest Accuracy | **52.5%** (author-weighted) |
| Best Platform Signal | Weibo 34.3% |
| Strategies | **7** implemented |
| CLI Tools | **26** commands |

---

## 🗂 Project Structure

```
AgentSense/
├── tradingagents/            # Core library (agents, dataflows, graph, LLM clients)
│   ├── agents/               # Analyst agents + debate (Bull/Bear/Moderator)
│   ├── dataflows/            # Data pipeline, commodity futures, sentiment, tools
│   ├── graph/                # LangGraph DAG orchestration
│   └── llm_clients/          # Multi-provider LLM support
├── cli/                      # 26 CLI tools (Typer-based)
├── web_app.py                # Flask + SSE streaming dashboard
├── web_template.html         # Dashboard frontend (Chart.js, ECharts)
├── commodity_demo.py         # 11-node analysis entry point
├── commodity_debate.py       # Adversarial debate with tool-calling
├── price_fetcher.py          # AKShare real-time pricing (24 varieties)
├── signal_analyzer.py        # Signal detection, strategy backtesting
├── database.py               # SQLite persistence layer
├── scheduler.py              # Automated data refresh tasks
├── pyproject.toml            # Build config & dependencies
└── .env.example              # Configuration template
```

---

## 🔧 Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | LangGraph 1.2+, LangChain |
| LLM Backend | DeepSeek V4 Pro (primary), OpenAI, Anthropic, Google, Qwen, GLM |
| Data | AKShare, yfinance, FRED, Polymarket |
| Web | Flask + SSE + Chart.js + ECharts + Waitress |
| Backtesting | backtrader, stockstats, pandas |
| NLP | Custom NER (50 entities), dual sentiment engine |
| Vision | Ollama (Granite 3.2-Vision + Qwen2.5-VL) |

---

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Areas where help is especially valued:
- New commodity variety support
- Additional social media platform adapters
- Strategy development and backtesting improvements
- Documentation and translations

---

## 📄 License

AgentSense is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for details.

Based on [TradingAgents](https://github.com/TauricResearch/TradingAgents) by TauricResearch.

---

## 📝 Citation

If you use AgentSense in your research, please cite:

```bibtex
@software{futuresmind2026,
  title     = {FuturesMind: Multi-Agent LLM Framework for Commodity Futures Research},
  author    = {FuturesMind Contributors},
  year      = {2026},
  url       = {https://github.com/2779639552/FuturesMind}
}

@misc{tradingagents2025,
  title     = {TradingAgents: Multi-Agents LLM Financial Trading Framework},
  author    = {TauricResearch},
  year      = {2025},
  url       = {https://github.com/TauricResearch/TradingAgents}
}
```

---

<p align="center">
  <sub>Built with ❤️ for the commodity futures research community</sub>
</p>
