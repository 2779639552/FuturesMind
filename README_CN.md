<p align="center">
  <img src="https://img.shields.io/badge/FuturesMind-v2.9-2563eb?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-Apache%202.0-green?style=for-the-badge" alt="License">
</p>

<h1 align="center">🧠 FuturesMind</h1>
<h3 align="center">面向中国商品期货市场的多智能体 LLM 投研框架<br>融合多平台社交媒体情绪感知</h3>

<p align="center">
  <a href="#-项目背景">项目背景</a> •
  <a href="#-核心架构">核心架构</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-核心特性">核心特性</a> •
  <a href="#-品种覆盖">品种覆盖</a> •
  <a href="#-性能指标">性能指标</a>
</p>

---

## 📖 项目背景

### 为什么做这个项目？

中国商品期货市场与西方市场有着显著差异：

| 差异维度 | 西方市场 | 中国市场 |
|---------|---------|---------|
| 信息传播 | 机构研报为主 | 社交媒体（微博/知乎/雪球）影响巨大 |
| 政策敏感度 | 相对独立 | 产业政策、环保限产直接冲击价格 |
| 散户参与度 | 机构主导 | 散户占比高，情绪波动放大效应强 |
| 品种特性 | 标准化程度高 | 细分品种多（黑色系/能化/农产品共21个主力品种） |
| 数据可得性 | Bloomberg/Reuters 统一接口 | 数据碎片化，需多平台聚合 |

**FuturesMind** 是在 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 多智能体框架基础上，针对中国商品期货市场深度改造的投研系统。通过 11 个专业化 AI Agent 的协作辩论，融合微博、知乎、雪球、小红书四大平台的社交媒体情绪，为中国期货市场提供智能化分析。

### 解决的痛点

- 🚫 **传统研报滞后**：人工撰写需数小时，AI 分钟级生成
- 🚫 **单一视角偏差**：单分析师易有盲区，Bull vs. Bear 对抗辩论消偏
- 🚫 **情绪数据缺失**：现有框架忽略了中国特色的社交媒体情绪
- 🚫 **品种覆盖不足**：从 8 品种扩展到 21 品种，覆盖郑商所主要品种

---

## 🏗 核心架构

```
                    ┌──────────────────────────────────────────┐
                    │            START（用户输入品种+日期）       │
                    └──────────┬───┬───┬───┬──────────────────┘
                               │   │   │   │
                    ┌──────────┘   │   │   └──────────┐
                    ▼              ▼   ▼               ▼
              ┌──────────┐  ┌──────────────┐  ┌──────────────┐
              │ Technical │  │ Fundamental  │  │   Macro/News │
              │ 技术分析师 │  │  基本面分析师  │  │  宏观/新闻   │
              └────┬─────┘  └──────┬───────┘  └──────┬───────┘
                   │               │                  │
                   └───────────────┼──────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │   Sentiment Analyst          │
                    │   社交媒体情绪分析师           │
                    │   (微博/知乎/雪球/小红书)      │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │     Bull vs. Bear 辩论        │
                    │   多方 vs 空方 对抗辩论        │
                    │   (6工具 × 实时数据验证)      │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │       Moderator 裁判          │
                    │   事实核查 + 裁决              │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │   Synthesis → Scenario        │
                    │   综合研判 → 情景推演           │
                    └─────────────────────────────┘
```

**11 节点流程**：4 分析师并行 → 情绪融合 → 多空对抗辩论 → 裁判裁决 → 综合研判 → 情景推演

### 技术栈

| 层级 | 技术选型 |
|------|---------|
| Agent 框架 | LangGraph 1.2+, LangChain |
| 大模型 | DeepSeek V4 Pro（主力），兼容 OpenAI/Anthropic/Google/通义千问/智谱 |
| 行情数据 | AKShare（24品种实时行情，15分钟缓存） |
| 社交媒体 | 微博、知乎、雪球、小红书（Playwright + Spider_XHS） |
| Web 前端 | Flask + SSE 流式推送 + Chart.js + ECharts |
| 回测引擎 | backtrader + stockstats + pandas |
| NLP | 自定义 NER（50实体 × 多别名），规则+LLM 双引擎情感 |
| 多模态 | Ollama（Granite 3.2-Vision + Qwen2.5-VL 两阶段分析） |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[免费获取](https://platform.deepseek.com)）

### 安装

```bash
git clone https://github.com/2779639552/FuturesMind.git
cd FuturesMind

# 创建虚拟环境
python -m venv venv

# 安装依赖
# Windows
venv\Scripts\pip install -e .
# macOS/Linux
source venv/bin/pip install -e .
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

### 三种启动方式

```bash
# 1. Web 看板（推荐）
venv\Scripts\python web_app.py
# 浏览器打开 http://localhost:5000

# 2. CLI 命令行
venv\Scripts\tradingagents

# 3. 单品种深度分析
venv\Scripts\python commodity_demo.py SA 2026-08-02   # 纯碱
venv\Scripts\python commodity_demo.py RB 2026-08-02   # 螺纹钢
```

> **Windows 用户**：双击项目根目录的 `start_web.bat` 或 `start_web.ps1` 一键启动。

---

## ✨ 核心特性

### 🔬 多智能体协作分析

- **4 位并行分析师**：技术面（RSI/MACD/布林带）、基本面（供需/库存）、宏观面（政策/地缘）、情绪面（社交媒体）
- **多空对抗辩论**：Bull 多方 vs. Bear 空方，各持有 6 个实时数据工具，通过数据而非幻觉论证
- **裁判事实核查**：Moderator 调用实时行情验证辩论中的每个数据主张
- **结构化输出**：BIAS 分析方向 + RATING 评级，统一 Markdown 汇总表

### 📊 中文社交媒体情绪融合

- **4 平台全覆盖**：微博（1,069条）、知乎（266条）、雪球（842条）、小红书（56条）
- **50 实体 NER**：品种 × 别名 × 合约代码（如 RB2501 → 螺纹钢）
- **双引擎情感分析**：规则引擎（期货专用词库，7级分类）+ LLM 深度语义理解
- **多模态图片分析**：Granite3.2-Vision 快速分类 + Qwen2.5-VL 结构化情感提取（129篇 × 265张图）
- **作者影响力加权**：三维权重（互动量 × 粉丝数 × 领域专业度）而非简单平均

### 📈 实时数据与回测

- **AKShare 实时行情**：24 品种主力合约，15 分钟缓存更新
- **7 种交易策略**：动量、自适应动量、DMAC、唐奇安通道、逆向情绪、自适应情绪、品种对比
- **13 项回测指标**：准确率、夏普比率、胜率、最大回撤、平台权重分析

### 🖥️ Web 看板

- **SSE 流式分析**：实时展示 11 节点执行进度
- **情绪 vs. 价格曲线**：交互式 Chart.js 图表，支持品种筛选
- **回测面板**：平台权重排行、品种横向对比、KPI 指标卡
- **历史报告**：20+ 份分析报告存档，预测 vs 实盘价格对比

---

## 📦 品种覆盖（21 个）

| 板块 | 品种 | 细分 |
|------|------|------|
| 🏗 黑色系 | RB螺纹钢, I铁矿石, HC热卷, JM焦煤, J焦炭, SM锰硅, SF硅铁 | 矿石/成材/炉料/合金 |
| 🔩 有色金属 | CU铜, AL铝, ZN锌, NI镍, PB铅, SN锡, AU黄金, AG白银 | 基本金属/贵金属 |
| ⚡ 能源化工 | FG玻璃, SA纯碱, UR尿素, PF短纤, MA甲醇, TA PTA | 建材/化工/农化/纺织 |
| 🌾 农产品 | M豆粕, CF棉花, SR白糖, OI菜油, RM菜粕, AP苹果, PK花生 | 软商品/生鲜/油脂/饲料/油料 |

---

## 📊 性能指标

| 指标 | 数值 |
|------|:----:|
| 采集数据量 | **8,966 条**（2026年） |
| 品种覆盖 | **21 个**（全部含实时行情） |
| 回测准确率 | **52.5%**（作者加权） |
| 最佳平台信号 | 微博 34.3% |
| 策略数量 | **7 种** |
| CLI 工具 | **26 个**命令 |
| 架构节点 | **11 节点** |

---

## 🗂 项目结构

```
├── tradingagents/            # 核心库（agents, dataflows, graph, LLM clients）
│   ├── agents/               # 分析师 + 辩论 Agent (Bull/Bear/Moderator)
│   ├── dataflows/            # 数据管道、商品期货、情绪数据、工具
│   ├── graph/                # LangGraph DAG 编排
│   └── llm_clients/          # 多厂商 LLM 支持
├── cli/                      # 26 个 CLI 工具（基于 Typer）
├── web_app.py                # Flask + SSE 流式看板
├── web_template.html         # 前端界面（Chart.js, ECharts）
├── commodity_demo.py         # 11 节点分析入口
├── commodity_debate.py       # 多空对抗辩论（工具调用模式）
├── price_fetcher.py          # AKShare 实时行情（24品种）
├── signal_analyzer.py        # 信号检测 + 策略回测
├── database.py               # SQLite 持久化层
├── scheduler.py              # 定时数据刷新
├── pyproject.toml            # 构建配置与依赖
├── .env.example              # 配置模板
├── README.md                 # 英文文档
└── README_CN.md              # 中文文档（本文）
```

---

## 📄 许可证

**Apache License 2.0**。详见 [LICENSE](LICENSE)。

基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（TauricResearch）改造。

---

## 📝 引用

```bibtex
@software{futuresmind2026,
  title     = {FuturesMind: 面向中国商品期货的多智能体LLM投研框架},
  author    = {FuturesMind Contributors},
  year      = {2026},
  url       = {https://github.com/2779639552/FuturesMind}
}
```

---

<p align="center">
  <sub>面向中国商品期货市场 · 让智能投研触手可及</sub>
</p>
