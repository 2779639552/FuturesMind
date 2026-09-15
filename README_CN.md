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
| 信息传播 | Bloomberg/Reuters + 机构研报为主流 | 微博/知乎/雪球等社交平台成为散户信息获取主渠道 |
| 政策影响 | 货币政策（美联储加减息）为主驱动 | 产业政策（供给侧改革、环保限产、进出口配额）频繁直接冲击 |
| 参与者结构 | 机构投资者占主导，程序化交易普及 | 个人投资者占比显著更高，情绪放大效应强 |
| 品种特点 | CME/CBOT 品种成熟，合约标准化 | 产业需求驱动的细分品种（合金、建材、生鲜），部分为中国特色品种 |
| 数据生态 | 终端（Bloomberg）提供统一 API，接口规范 | 数据分散在多个本地平台，缺乏统一接口 |

> **FuturesMind** 基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 多智能体框架进行中国本土化改造：将原有的股票分析管线重构为商品期货专用管线，新增 AKShare 数据源替代 yfinance，引入微博/知乎/雪球/小红书四平台社交媒体情绪分析模块，并将品种覆盖从初始的 8 个扩展到 20 个活跃品种（62 品种元数据注册表）。

### 解决的痛点

- 🚫 **传统研报滞后**：人工撰写需数小时，AI 分钟级生成
- 🚫 **单一视角偏差**：单分析师易有盲区，Bull vs. Bear 对抗辩论消偏
- 🚫 **情绪数据缺失**：现有框架忽略了中国特色的社交媒体情绪
- 🚫 **品种覆盖不足**：从 8 品种扩展到 20 个活跃品种（能源/化工/农业），62 品种元数据注册表备查

---

## 🏗 核心架构

```
                    ┌──────────────────────────────────────────┐
                    │            START （用户输入品种+日期）       │
                    └──────────┬───┬───┬───┬──────────────────┘
                               │   │   │   │
                    ┌──────────┘   │   │   └──────────┐
                    ▼              ▼   ▼               ▼
              ┌──────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
              │Technical │ │ Fundamental  │ │   Macro/News │ │  Sentiment   │
              │ 技术面   │ │  基本面       │ │   宏观/新闻  │ │  社交媒体情绪│
              └────┬─────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                   │              │                │                │
                   └──────────────┼────────────────┼────────────────┘
                                  │                │
                                  └────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    │         Bull vs. Bear 多空对抗辩论            │
                    │    Bull 多方陈述 → Bear 空方反驳 → Bull 再反驳  │
                    │         （6工具 × 实时数据验证）               │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    │              Moderator 裁判                  │
                    │              事实核查 + 裁决                 │
                    └──────────────────────┬──────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    │          Synthesis → Scenario               │
                    │           综合研判 → 情景推演                │
                    └─────────────────────────────────────────────┘
```

**11 节点流程**：START → 4 分析师全线并行（技术面 ∥ 基本面 ∥ 宏观新闻 ∥ 社交媒体情绪）→ 多空对抗辩论（Bull陈述 → Bear反驳 → Bull再反驳 → Moderator裁决）→ 综合研判 → 情景推演 → END

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

- **AKShare 实时行情**：24 品种主力合约，60 秒缓存，交易时段 5 分钟刷新
- **7 种交易策略**：动量(纯价格)、动量+自适应、自适应情绪、逆情绪(背离)、情绪反转出场、情绪固定(共识)、基本面vs情绪对比
- **13 项回测指标**：准确率、夏普比率、胜率、最大回撤、平台权重分析

### 🖥️ Web 看板

- **SSE 流式分析**：实时展示 11 节点执行进度
- **情绪 vs. 价格曲线**：交互式 Chart.js 图表，支持品种筛选
- **回测面板**：平台权重排行、品种横向对比、KPI 指标卡
- **历史报告**：20+ 份分析报告存档，预测 vs 实盘价格对比

### 📚 研报 RAG 问答

- **三源研报采集**：华泰期货、国泰君安、东证期货 —— 14,000+ 份研报自动采集归档
- **版面感知 PDF 解析**：聚簇式表格/图表提取，32 份位图 PDF 已识别并标记
- **多轮问答**：会话持久化、LLM 基于对话上下文改写检索词、日期筛选
- **自进化闭环**：👍/👎 反馈信号 → 错题本（失败轮自动浮出）→ 黑话词典（口语词→检索词）
- **单品种总结**：自动生成研报综述，附来源报告交叉引用

### 📱 微信问答 Bot

- **独立进程服务**：Flask + Waitress 端口 5001，只走 QA API、不直接碰数据库
- **5 秒双段式回复**：签名 ACK + 客服消息异步推送（长答案按 UTF-8 字节分片）
- **按用户会话**：openid → 会话映射、白名单控制、👍/👎 回接 QA 反馈接口

---

## 📦 品种覆盖（20 个活跃品种）

当前活跃分析池 —— 能源、化工与农业（上期所 / 能源中心 / 大商所 / 郑商所 / 广期所）：

| 板块 | 品种 |
|------|------|
| ⛽ 能源 | SC原油, FU燃料油, LU低硫燃料油, BU沥青, PG液化石油气 |
| 🧪 芳烃与聚酯链 | TA PTA, PX对二甲苯, EG乙二醇, EB苯乙烯, BZ纯苯 |
| 🧴 树脂/橡胶/硅 | PS多晶硅树脂(工业硅下游), SI工业硅, RU天胶, NR20号胶, BR丁二烯橡胶 |
| 🌾 农产品 | M豆粕, CF棉花, CJ红枣, LH生猪 |

> 代码中保留 **62 品种元数据注册表**（黑色、有色、贵金属俱全）—— 仅从界面过滤，不做删除。

---

## 📊 性能指标

| 指标 | 数值 |
|------|:----:|
| 采集数据量 | **8,966 条**（2026年） |
| 品种覆盖 | **20 个活跃**（62 品种注册表） |
| 研报库索引 | **14,000+ 份**（三源采集） |
| 测试规模 | **1,363 项**全绿 |
| 回测准确率 | **52.5%**（作者加权） |
| 最佳平台权重 | 雪球 0.278 |
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
├── data/                     # 前端内置样本数据（情绪/帖子流/趋势，供全新克隆直接浏览）
├── data_collection/          # 多平台社媒采集子项目（FuturesSentiment）
│   ├── CONFIG_GUIDE.md       # 🔑 采集密钥/配置说明（小红书Cookie、LLM Key 等，克隆后必读）
│   └── .env.example          # 采集相关密钥模板
├── README.md                 # 英文文档
└── README_CN.md              # 中文文档（本文）
```

> 💡 **数据采集配置**：`data_collection/` 采集小红书/微博/知乎/雪球数据需要个人密钥
> （小红书 `COOKIES`、LLM `API_KEY`、`HF_TOKEN` 等），且不随仓库提交。
> 克隆后请按 **[`data_collection/CONFIG_GUIDE.md`](data_collection/CONFIG_GUIDE.md)** 配置后再运行采集。
> 前端 `web_app.py` 已内置仓库 `data/` 样本数据回退，无密钥也能浏览分析界面。

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
