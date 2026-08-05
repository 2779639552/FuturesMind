# 期货社交媒体信息采集系统

## 项目概述
规模化抓取小红书平台的期货品种相关信息，包含数据采集、NER品种识别、情感分析、多模态图片分析、情绪vs价格对比看板。

## 环境
- Python 3.12, Node.js v22, Windows 11, RTX 5060 8GB
- Ollama (本地VL模型: granite2B + qwen2.5vl:3b)
- Playwright (Chromium), Spider_XHS (API签名引擎)

## 项目结构
```
思路2/
├── Spider_XHS/                  # 小红书API引擎
│   ├── apis/xhs_pc_apis.py      # XHS_Apis: search_some_note(), get_note_info()
│   ├── xhs_utils/xhs_util.py    # X-s/X-t签名生成
│   ├── .env                     # COOKIES='...' (登录态)
│   └── static/                  # 签名核心JS文件
│
├── validate/                    # 主要工作目录
│   ├── [采集] batch_collect.py      # 批量采集 (FAST/TURBO/SAFE三档)
│   ├── [采集] xhs_scraper.py        # Playwright浏览器登录
│   ├── [NER]  ner.py                # 50品种×多别名+合约代码+价格
│   ├── [情感] sentiment.py           # 规则引擎: 7级分类, 品种级aspect-based
│   ├── [情感] llm_sentiment.py       # LLM引擎: Claude/GPT/DeepSeek API
│   ├── [多模态] multimodal_analyzer.py # Emoji+文本+图片三通道融合
│   ├── [多模态] image_pipeline_v2.py  # 两阶段图片分析 (fast/deep双模式)
│   │
│   ├── [分析] analyze.py             # 5模块分析主入口
│   ├── [分析] variety_dashboard.py   # 品种热度×情绪仪表盘
│   ├── [分析] sentiment_deep.py      # 情感深度(高确信信号/时间维度)
│   ├── [分析] author_analysis.py     # KOL排行/Gini集中度
│   ├── [分析] content_analysis.py    # 内容策略(形式/长度/时段)
│   ├── [分析] event_discovery.py     # 品种共现/情感异常/爆款
│   ├── [分析] report_utils.py        # 数据加载/Plotly图表/HTML报告
│   │
│   ├── [看板] dashboard.py           # 情绪vs价格对比HTML看板
│   ├── [看板] trend_aggregator.py    # 情绪时序聚合
│   ├── [看板] price_fetcher.py       # akshare期货价格获取
│   ├── [日常] daily_update.py        # 一键更新: 采集→聚合→价格→看板
│   │
│   ├── config.py                 # 品种词典+API端点
│   └── output/
│       ├── batch_*.jsonl              # 采集数据 (344条/3批次)
│       ├── multimodal_v2_*.jsonl      # 多模态分析结果 (129篇/265张图)
│       ├── trends/                    # 时序数据+看板
│       │   ├── dashboard.html         # 情绪vs价格对比看板 (离线)
│       │   ├── *_sentiment.json       # 品种情绪时序 (47品种)
│       │   ├── *_price.json           # 品种价格数据 (39品种)
│       │   └── _index.json
│       ├── reports/                   # HTML分析报告
│       └── images/                    # 图片缓存
│
└── FEASIBILITY_REPORT.md        # 平台可行性调研
```

## 核心数据文件
| 文件 | 内容 |
|------|------|
| `batch_20260715_160642.jsonl` | 215条, 第1批采集 |
| `batch_20260716_084428.jsonl` | 215条, 第2批采集 (FAST 5.7min) |
| `batch_20260716_103023.jsonl` | 129条, 第3批采集 (含image_urls) |
| `multimodal_v2_*.jsonl` | 129篇多模态分析 (265张图, 70%文字/30%深度) |

## 数据字段
基础: note_id, title, desc, url, keyword, publish_time, ip_location, image_urls
作者: author_name, author_id, author_fans
互动: like_count, comment_count, collect_count, share_count
NER: varieties[{name,matched,sector,exchange}], contracts[], variety_count
情感: sentiment(7级), sentiment_score, sentiment_confidence, variety_sentiments[]
图片: image_analysis[{image_type, ocr_text, sentiment, sentiment_score, route}]

## 本地模型池 (Ollama, RTX 5060 8GB)
| 模型 | 大小 | 用途 |
|------|:--:|------|
| `granite3.2-vision:2b` | 2.4GB | Stage1: 快速分类+OCR (~10s) |
| `qwen2.5vl:3b` | 3.2GB | Stage2: JSON情感分析 (~13s) |
| `qwen3-vl:4b` | 3.3GB | 备选: 最好质量但慢(Thinking) |
| `qwen3-vl:2b` | 1.9GB | 备选: 太小太慢(Thinking) |

## 常用命令
```bash
# === 日常采集 ===
cd validate && python batch_collect.py --per-kw 30 --max-detail 5

# === 数据分析 ===
python analyze.py output/batch_xxx.jsonl                    # 终端+HTML报告

# === 图片分析 (双模式) ===
python image_pipeline_v2.py data.jsonl --mode fast           # 规则快速 (~5min)
python image_pipeline_v2.py data.jsonl --mode deep           # 模型深度 (~90min)

# === 情绪vs价格看板 ===
python daily_update.py                                       # 一键:采集→聚合→价格→看板
python trend_aggregator.py                                   # 仅聚合情绪
python price_fetcher.py                                      # 仅拉价格
python dashboard.py                                          # 仅生成看板
# 打开 output/trends/dashboard.html

# === 刷新Cookie ===
python xhs_scraper.py  # 扫码登录
```

## 下一步方向
- 微博/雪球多平台扩展
- 品种级时间序列预测模型
- FinBERT/FinGPT微调替代规则引擎
- 实时流式处理 (Kafka/Flink)
- 数据标注→蒸馏小模型
