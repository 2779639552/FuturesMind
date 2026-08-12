# 细粒度中文注释 + 学习文档函数/变量级细化(2026-08-12)

> 一句话结论:在全代码库函数级注释(08-12 上轮,12 份学习文档基线)基础上,再往下
> 细到**调用包(import)/调用函数(调用点)/变量**三级,并把 `Desktop/learning/AgentSense/`
> 的 12 份文档细化到**函数/变量级**,新增《函数/变量参考手册》`REFERENCE.md`。
> **零逻辑改动**(AST 归一化门禁 120/120 全过),一次性 commit 收口。

## 门禁数字(最终 HEAD,全部绿色)

| 门禁 | 结果 |
|---|---|
| AST 归一化对比(git show HEAD) | **120/120 PASS**,逻辑差异 = 0(注释不进 AST) |
| 全量 pytest | **625 passed / 1 skipped / 1 deselected**(22.2s) |
| 注释-only diff 启发式 | **0 红牌**(所有新增行均为注释/docstring/行尾注释) |
| dup-block 扫描 | 无新增重复代码块;`_run_tool_loop` 恒为 **2 份**(见下) |
| node --check(web_template 内联 JS) | PASS |
| diff 总量 | 121 文件,+4643 / -3243(120 .py + web_template.html) |

## 注释批次(13 批并行 agent,文件清单)

| 批 | 子系统 | 文件 | 说明 |
|---|---|---|---|
| 1 | Web 后端 | `web_app.py` | 3245 行;`/api/trading/*` 24 条 |
| 2 | Web 前端 | `web_template.html` | 3610 行;JS 方法逐条标注 |
| 3 | 回测引擎 | `signal_analyzer.py` | 4475 行;阈值/口径一个数没动 |
| 4 | 入口+底座 | `commodity_demo.py`、`commodity_debate.py`、`database.py`、`scheduler.py`、`price_fetcher.py`、`path_utils.py` | debate 上轮有重复块前科,dup 重点复查 |
| 5 | CLI+收尾 | `main.py`、`cli/*`(7)、`tradingagents/reporting.py`、`tradingagents/default_config.py` | |
| 6 | Graph+接口 | `graph/*`(9)+ `dataflows/{__init__,config,errors,interface,sentiment_data}` | |
| 7 | dataflows 主体 | `commodity_futures.py`、`external_data.py`、`evolution_memory.py` | VARIETY_METADATA=21 品种 |
| 8+9 | LLM+Agents | `llm_clients/*`(12)+ `agents/{analysts,managers,researchers,risk_mgmt,trader}`(共 28) | `_run_tool_loop` 两处注释必须一致 |
| 10 | Agents utils/schemas | `agents/{schemas,schemas_commodity}.py` + `agents/utils/*`(15) | |
| 11 | 采集大文件 | `batch_collect.py`、`sentiment.py`、`hybrid_pipeline.py`、`ner.py` | |
| 12 | 采集剩余 | `validate/{analyze,author_analysis,backtest_weights,config,content_analysis,daily_update,dashboard,data_cleaner,dedupe,event_discovery,llm_sentiment,price_fetcher,sentiment_deep,trend_aggregator}.py` | |
| 13 | 平台适配器 | `platforms/{__init__,base,weibo,xhs,xueqiu,zhihu}`(6) | 落实"4 平台" |
| — | 排除 | `xhs_scraper/`、`validator_*`、`image_pipeline*`、`production_hybrid/`、`final_hybrid/`、`run_validation/`、`weibo_login/`、`zhihu_login/`、`Spider_XHS/`、`tests/`、`data/` | 外围遗留 |

标记体系(全仓统一):`# 【调用包】`(import,写用途不写翻译)、`# 【调用函数】`(跨模块/
外部API/有数据转换的调用点)、`# 【变量】`(配置/阈值/单位/中间结果/字段)、
`# 【功能】/【参数】/【返回】/【关键】`(函数块,docstring 上方)。JS 用 `// 【功能】`。

**诚实标注**:全仓 17 处 `# 【待确认】`(拿不准的用途标出来不编造),含
`compute_advanced_metrics` 的 ±0.1% 分档口径 vs `_trade` ±0.15% 的历史不一致(见 08-11
"海龟 5.58 vs -0.1"根因调查),claude/main.py:415 的未使用参数等。

## 校验工具(scripts/verify_annotations/,一次性 QA,已 commit)

| 工具 | 作用 |
|---|---|
| `ast_normalize_compare.py` | **权威门禁**:docstring 归一化删除 + 字符串置空 → AST dump 对比 HEAD,证明零逻辑改动(自测 3/3) |
| `dup_block_scan.py` | tokenize 去注释、滑动窗口 k=5 找"工作区新增重复代码块";断言 `_run_tool_loop` 恰 2 份 |
| `comment_only_diff.py` | `git diff -U0` 逐条判定新增行 = 注释/docstring/原行+行尾注释(启发式,以 AST 为准) |
| `node_check_html.py` | 提取 web_template 内联 `<script>` 后 `node --check` |
| `annotation-spec.md` | 批次作业规范(规则 A-E + 门禁 + 汇报格式) |
| `README.md` | 工具文档 |

## P4 学习文档细化(仓库外 `Desktop/learning/AgentSense/`)

3 个文档 agent 并行细化 12 份文档 + **新建 REFERENCE.md**;全部行号在最终 HEAD 实测,
不推算。已独立抽查核验(不轻信 agent 自报):signal_analyzer 20+ 处、web_app 24 路由、
web_template 4 方法、conftest/scheduler 行号等均与代码一致。

### 事实校正清单(全部落地)
1. **平台数**:"5 平台含抖音" → **4 个适配器**(weibo/zhihu/xueqiu/xhs)。
   `validator_douyin.py` 实测在 `data_collection/validate/`(非根目录),是实验脚本、无适配器。
   — 我最初给 agent 的"根目录遗留脚本"说法**是错的**,D1 agent 实测纠正;D2 沿用了错说法,
   已手动修 day-08 第 26 行脚注。
2. **交易路由**:`/api/trading/*` = **24 条**(2056–2759),非 20。
3. **`_run_tool_loop`**:恰 **2 份**(commodity_analysts.py:57 / sentiment_analyst.py:67);
   commodity_debate.py 是 import 复用(:41),非第 3 份。
4. **测试文件**:`test_commodity_analysts.py` 不存在 → day-03 改指 `tests/test_analyst_execution.py`。
5. **目录树**:`tradingagents/prompts/`、`backends/`、`memory/`、`outputs/` 均已不存在;
   system prompt 为内联(commodity_analysts.py:239/374/541)。
6. **数据源**:`_load_price` 读 `THINK2_TRENDS`(resolve_think2_dir()/output/trends),非
   `~/.tradingagents/external_data`(那是情绪目录 SENTIMENT_DIR)。day-02 已改。

### 关键实测行号(signal_analyzer.py,4475 行)
`_load_sentiment`:75 / `_load_trends`:93 / `_load_price`:145 / `_build_tech_series`:1546 /
`_tech_signal`:1609 / `_adapt_sentiment_signal`:1691 / `_run_technical_backtest`:1733 /
`_trade`:1921(±0.15% 分档) / `TECH_KEYS`:1948 / `apply_risk_management`:2817 /
`TradeRecord`:2937(class) / `compute_advanced_metrics`:2964(±0.1% 分档,历史不一致) /
`RISK_FREE_RATE`:3003(2.5%) / `run_simulated_trading`:3133 / `run_strategy_comparison`:3398 /
`_build_forward_filled_sent_map`:3864 / `latest_trading_signal`:3914 / `get_all_variety_scores`:4428

### 交付物
- `roadmap.md`(410 行)、`study-plan.md`(419 行):新增"函数/变量级速查"小节 + 对照代码注释问题。
- `daily-plans/day-00~09`(10 个):每文件加**函数表**(函数/签名/返回/关键变量/实测位置)+
  **变量表**(变量/位置/值单位/语义),约 133 条。
- **`REFERENCE.md`(464 行,新建)**:8 章(入口/数据层/Agent/LangGraph/LLM/回测/Web/采集),
  25 张表 / 227 条,每章含"入口→调用链速览",回测引擎按组(数据加载/情绪指标/技术指标/
  风控/12 策略/模拟交易)。

## 修复记录
- day-08 第 26 行:`validator_douyin.py` 位置描述"根目录"→ 修正为 `data_collection/validate/`
  (该文件实测位置,非根目录)。
- 两处被 agent 重换行的语句恢复单行+行尾注释(schemas_commodity.py:157 narrative 字段、
  alpha_vantage_fundamentals.py:28 列表推导),AST 门禁无法发现这类字符串相关改动,靠
  comment-only diff 兜住。
- Batch 3/12 agent 误删可执行行(`si = 0`、结构行)已自纠 + 独立复验 net-0。

## 收尾
- 一次 commit 收口全部注释 + scripts/verify_annotations/ + 本 worklog;
  **不含 "Co-Authored-By: Claude" 页脚**。
- `.gitignore` 已含 `*.log`,server*.log 不混入。
- 推送 GitHub 待用户明确要求(需 Clash 代理)。
