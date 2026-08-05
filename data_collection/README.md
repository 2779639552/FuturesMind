# 期货社交媒体信息采集系统（FuturesSentiment）

> 多平台期货社媒情绪数据采集与分析管道，为 **[FuturesMind](https://github.com/2779639552/FuturesMind)** 商品期货投研系统提供社交媒体情绪数据源。

规模化抓取 **小红书 / 微博 / 知乎 / 雪球** 平台的期货品种相关信息，经过 NER 品种识别、双引擎情感分析、多模态图片分析，最终生成「情绪 vs 价格」对比看板，并可导出为 FuturesMind 情绪分析师直接读取的情绪 JSON。

---

## 目录

- [核心能力](#核心能力)
- [全链路 Pipeline](#全链路-pipeline)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [使用说明（分模块）](#使用说明分模块)
- [数据格式](#数据格式)
- [本地模型池](#本地模型池)
- [平台适配状态](#平台适配状态)
- [与 FuturesMind 的关系](#与-futuresmind-的关系)
- [目录结构](#目录结构)
- [已知问题](#已知问题)

---

## 核心能力

| 能力 | 说明 |
|------|------|
| 多平台采集 | 小红书 / 微博 / 知乎 / 雪球（+抖音/B站适配层已就绪） |
| NER 品种识别 | **50 品种** × 多别名，支持合约代码匹配（RB2501/i2505）、品种共现、板块归属 |
| 双引擎情感分析 | 规则引擎（期货专用词库，7级分类，品种级 aspect-based）+ LLM 引擎（Claude/GPT/DeepSeek） |
| 多模态图片分析 | Ollama 本地 VL 模型两阶段分析（图片分类 + OCR + 结构化情感） |
| 情绪 vs 价格看板 | 55 品种情绪时序 + 39 品种价格，离线 HTML 交互看板 |
| 情绪权重回测 | 多平台情绪方向准确率回测，支撑平台权重分配 |
| FuturesMind 对接 | 一键导出 47 品种情绪 JSON，供投研系统情绪分析师使用 |

## 全链路 Pipeline

```
采集(3平台) → 去重 → NER(50品种) → 情感(规则+LLM) → 多模态(VLM) → 聚合(时序) → 看板(HTML)
                                                                              └→ FuturesMind 情绪JSON
```

## 环境要求

- **系统**: Windows 11 / Linux
- **Python**: 3.12
- **Node.js**: v22（小红书签名引擎 Spider_XHS 需要）
- **浏览器**: Playwright (Chromium)，安装后需执行 `playwright install chromium`
- **Ollama**（多模态分析可选）：本地 VL 模型，见[本地模型池](#本地模型池)
- **RTX 5060 8GB** 已实测（granite2b + qwen2.5vl:3b 可流畅运行）

## 快速开始

```bash
cd validate
pip install -r requirements.txt
playwright install chromium

# 1️⃣ 采集数据（默认小红书，可用 --platform 切换）
python batch_collect.py --platform xhs --per-kw 30 --max-detail 10

# 2️⃣ NER + 情感分析（终端 + HTML报告）
python analyze.py output/batch_xxx.jsonl

# 3️⃣ 多模态图片分析（可选）
python image_pipeline_v2.py output/batch_xxx.jsonl --mode fast   # 规则快速 (~5min)
python image_pipeline_v2.py output/batch_xxx.jsonl --mode deep   # 模型深度 (~90min)

# 4️⃣ 一键更新：采集 → 聚合 → 价格 → 看板
python daily_update.py

# 5️⃣ 打开看板
#    output/trends/dashboard.html
```

## 使用说明（分模块）

### 1. 数据采集

```bash
# 小红书批量采集（FAST/TURBO/SAFE 三档）
python batch_collect.py --platform xhs --per-kw 30 --max-detail 10
python batch_collect.py --platform xhs --turbo          # 加速模式
python batch_collect.py --platform xhs --safe-mode      # 安全限速模式

# 支持平台: xhs / weibo / zhihu / xueqiu
python batch_collect.py --platform weibo --per-kw 20

# 刷新小红书登录态（扫码）
python xhs_scraper.py

# 平台独立采集器
python validator_weibo.py
python validator_xiaohongshu.py
python validator_xueqiu.py
python validator_douyin.py   # 实验性
```

### 2. NER + 情感分析

```bash
# 终端 + HTML 报告（5大分析模块可单独开关）
python analyze.py output/batch_xxx.jsonl
python analyze.py output/batch_xxx.jsonl --no-html      # 仅终端文本
python analyze.py output/batch_xxx.jsonl -m 1 3 5       # 只跑指定模块

# 情感深度分析（高确信信号 + 时间维度）
python sentiment_deep.py output/batch_xxx.jsonl
```

### 3. 多模态图片分析

```bash
# fast 模式: 本地规则分类+OCR, 约5分钟
# deep 模式: 本地 VL 模型结构化情感, 约90分钟
python image_pipeline_v2.py output/batch_xxx.jsonl --mode fast
python image_pipeline_v2.py output/batch_xxx.jsonl --mode deep
```

### 4. 情绪 vs 价格看板

```bash
# 一键更新（推荐）：采集→去重→NER→情感→聚合→价格→看板
python daily_update.py

# 分步执行
python trend_aggregator.py      # 仅聚合情绪时序
python price_fetcher.py         # 仅拉取期货价格 (akshare)
python dashboard.py             # 仅生成 HTML 看板

# 打开 output/trends/dashboard.html
```

### 5. 情绪权重回测

```bash
# 多平台情绪方向准确率回测（支持网格搜索）
python backtest_weights.py --grid-search
python backtest_weights.py --min-points 30 --horizons 1 3 5
```

### 6. FuturesMind 情绪数据对接

```bash
# 生成 47 品种情绪 JSON，输出到 ~/.tradingagents/external_data/{SYMBOL}_sentiment.json
python generate_tradingagents_sentiment.py
python generate_tradingagents_sentiment.py --variety 螺纹钢   # 指定品种
python generate_tradingagents_sentiment.py --dry-run          # 预演不写入
```

### 7. 生产级混合管道（小红书深度采集）

```bash
# Spider_XHS 签名引擎 + 浏览器 API 双通道
python production_hybrid.py --keywords 螺纹钢期货 铁矿石期货 --deep spider_xhs
python final_hybrid.py --keywords 原油期货 --max-depth 5
```

## 数据格式

每条采集数据（JSONL 一行一条笔记）：

```
基础:   note_id, title, desc, url, keyword, publish_time, ip_location, image_urls
作者:   author_name, author_id, author_fans
互动:   like_count, comment_count, collect_count, share_count
NER:    varieties[{name, matched, sector, exchange}], contracts[], variety_count
情感:   sentiment(7级), sentiment_score, sentiment_confidence, variety_sentiments[]
图片:   image_analysis[{image_type, ocr_text, sentiment, sentiment_score, route}]
```

## 本地模型池

| 模型 | 大小 | 用途 |
|------|:--:|------|
| `granite3.2-vision:2b` | 2.4GB | Stage1: 快速分类 + OCR (~10s) |
| `qwen2.5vl:3b` | 3.2GB | Stage2: JSON 结构化情感 (~13s) |
| `qwen3-vl:4b` | 3.3GB | 备选: 质量最好但慢（Thinking） |
| `qwen3-vl:2b` | 1.9GB | 备选: 过小过慢（Thinking） |

```bash
ollama pull granite3.2-vision:2b
ollama pull qwen2.5vl:3b
```

## 平台适配状态

| 平台 | 状态 | 说明 |
|------|:--:|------|
| 小红书 | ⚠️ 可用 | 反爬严格，需 Spider_XHS 签名引擎 + 定时刷新 Cookie |
| 微博 | ✅ 稳定 | 免登录 API，1,069 条已验证 |
| 知乎 | ✅ 稳定 | 需 Cookie 登录态 |
| 雪球 | ✅ 可用 | Playwright 反检测接入（阿里云 WAF） |
| 抖音 | 🚧 实验 | MediaCrawler 方案已评估，适配器雏形存在 |
| B站 | 🚧 未实施 | API 端点已配置 |

## 与 FuturesMind 的关系

```
FuturesSentiment (本仓库)                  FuturesMind (投研系统)
├─ 多平台采集 → 情绪JSON ──────────────→ 情绪分析师 (Sentiment Analyst)
├─ NER + 情感分析                         ├─ Bull/Bear 辩论 (工具调用实时数据)
├─ 多模态图片分析                         ├─ Synthesis → Scenario
└─ 情绪vs价格看板                         └─ 回测体系 (52.5% 准确率)
```

本仓库的输出通过 `generate_tradingagents_sentiment.py` 写入 `~/.tradingagents/external_data/`，由 FuturesMind 的情绪分析师读取。

## 目录结构

```
思路2/
├── README.md                     # 本文档
├── FEASIBILITY_REPORT.md         # 平台可行性调研
├── Spider_XHS/                   # 小红书 API 签名引擎
│   ├── apis/                     # XHS_Apis 接口封装
│   ├── xhs_utils/                # X-s/X-t 签名生成
│   ├── static/                   # 签名核心 JS 文件
│   └── .env                      # COOKIES 登录态（不入库）
└── validate/                     # 主要工作目录
    ├── batch_collect.py          # 批量采集 (多平台)
    ├── xhs_scraper.py            # 小红书 Playwright 登录
    ├── ner.py                    # 50品种 NER 识别
    ├── sentiment.py              # 规则情感引擎
    ├── llm_sentiment.py          # LLM 情感引擎
    ├── multimodal_analyzer.py    # 多模态三通道融合
    ├── image_pipeline_v2.py      # 两阶段图片分析
    ├── analyze.py                # 5模块分析主入口
    ├── daily_update.py           # 一键更新
    ├── trend_aggregator.py       # 情绪时序聚合
    ├── price_fetcher.py          # 期货价格 (akshare)
    ├── dashboard.py              # HTML 看板生成
    ├── backtest_weights.py       # 情绪权重回测
    ├── generate_tradingagents_sentiment.py  # FuturesMind 对接
    ├── config.py                 # 品种词典 + API 端点
    ├── platforms/                # 平台适配层
    └── output/                   # 采集/分析产物（不入库）
```

## 已知问题

- **小红书反爬严格**：空批次频发，需定期刷新 Cookie，首次扫码 `python xhs_scraper.py`
- **知乎数据量偏少**：关键词匹配率低，建议放宽 `PLATFORM_KEYWORDS["zhihu"]`
- **抖音未落地**：MediaCrawler 方案已评估但尚未编码实现
- **无实时推送**：目前为离线 HTML 看板，如需实时需接入流式处理

---

**License**: MIT（Spider_XHS 部分遵循其上游许可证）
