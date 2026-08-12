# AgentSense 学习文档交付(2026-08-12)

> 一句话结论:按 `C:\Users\19168\Desktop\learning\` 既有文档格式(roadmap.md +
> study-plan.md + daily-plans/day-00~09),为**当前 AgentSense 仓库**生成整套学习文档,
> 交付于 `C:\Users\19168\Desktop\learning\AgentSense\`,共 12 个文件。
> 内容基于后台代码库测绘(Explore agent 实测)校准,非凭旧 v2.9 记忆撰写。

## 交付物(12 个文件)

```
learning/AgentSense/
├── roadmap.md          # 学习主路径图、两子系统对比、20策略全貌、6阶段路线、TOP-15速查、已知bug触发点
├── study-plan.md       # 10天总览表 + 每天目标/阅读清单/练习/关键问题/检验标准 + 时间建议
└── daily-plans/
    ├── day-00-environment.md      # 环境搭建 + 八个标签页
    ├── day-01-main-trace.md       # commodity_demo 分析主链路 + 与 commodity_debate 对比
    ├── day-02-data-layer.md       # AKShare 实时 vs 本地 JSON 两套数据源 + Hybrid Mode
    ├── day-03-agent-layer.md      # 分析师 + _run_tool_loop
    ├── day-04-debate-graph.md     # Bull/Bear 辩论 + LangGraph 拓扑
    ├── day-05-llm-layer.md        # LLM 工厂 + 端到端串联验证
    ├── day-06-backtest-engine.md  # 20策略 + 统一引擎 + 成本风控口径(★核心)
    ├── day-07-web-frontend.md     # web_app 路由 + web_template 双回测路径
    ├── day-08-data-collection.md # 5平台社媒情绪生产管线
    └── day-09-wrap-up.md          # 完整架构 + 测试 + 毕业自评
```

预计学习总时长 **31-35 小时**(全职约 1 周 / 业余 2-3 周)。

## 过程

1. 通读 `C:\Users\19168\Desktop\learning\` 的格式模板(v2.9 老文档,主题为 FuturesMind 分析管线)。
2. 启动 Explore agent 对当前仓库做全量测绘,产出结构报告(入口点 / tradingagents 包 /
   signal_analyzer / web_template / data_collection / tests / database / worklog)。
3. 先凭会话内深度知识起草(回测引擎、前端口径是本会话直接改过的代码),再用测绘报告校准。

## 测绘校准的关键事实(文档准确性来源)

| 维度 | 老文档(v2.9)曾写 | 当前实际(实测) |
|------|------------------|----------------|
| 前端标签页 | 5 个 | **8 个**(运行分析/情绪数据/分析工具/回测/模拟交易/数据更新/历史报告/系统) |
| 策略数 | 7 种 / 19+ | **20 个**(8 旧情绪 + 6 纯价格 + 6 情绪确认) |
| 回测数据路径 | `data/*.json` | `~/.tradingagents/external_data/*.json`,回退 `data/external_data/` |
| 采集平台 | 4 | **5**(微博/知乎/雪球/小红书/抖音) |
| 分析进度推送 | SSE | `/api/run_analysis` + `/api/progress` 轮询 |
| 分析师 | 4 位必选 | **3 必选(技术/基本/宏观)+ 1 可选(情绪)**,无情绪数据时降级 |
| 分析管线节点 | 圆桌讨论为主 | commodity_demo 圆桌讨论 + commodity_debate Bull/Bear 对抗并存 |

关键函数实测行号(signal_analyzer.py):`_load_sentiment`:27 / `_load_price`:74 /
`_build_tech_series`:1286 / `_adapt_sentiment_signal`:1396 / `_run_technical_backtest`:1432 /
`TECH_KEYS`:1606 / `apply_risk_management`:2364 / `TradeRecord`:2467 /
`compute_advanced_metrics`:2485(无风险利率=中国10年国债 2.5%)/ `_build_forward_filled_sent_map`:3303 /
`latest_trading_signal`:3347。

## 本会话近期 bug(已留档)与文档的呼应

- `2026-08-11-single-vs-multi-cost-risk.md`:单卡 vs 多策略口径分裂。文档在 Day 0/6/7
  均设了对应观察点与复现练习(「先风控后成本,每笔一次」、`cpt=(commRate*2)+(slipTick*0.02)`)。
- `2026-08-11-multi-compare-0pct-fix.md`:HC 别名。文档 Day 2/6 以 HC 追踪为例。
- `2026-08-11-technical-strategies.md`:12 技术策略接入。文档 Day 6 详述统一引擎。

## 附:全代码库中文注释批次(同次请求完成)

> 用户请求「更详细一些,并在所有代码中添加详细的备注方便学习,备注使用中文」。
> 11 个并行 agent 分批为全仓核心代码加中文注释,**纯注释插入,零逻辑改动**。

| 批次 | 文件 | 注释增量 |
|------|------|---------|
| 支持代码 | database.py / price_fetcher.py / scheduler.py / path_utils.py | +472 行 |
| dataflows | commodity_futures.py / external_data.py / commodity_futures_tools.py | +341 行 |
| graph+factory | setup.py / trading_graph.py / factory.py | +487 行 |
| analysts | commodity_analysts.py / sentiment_analyst.py | +219 行 |
| 核心·回测 | signal_analyzer.py(最大,~3900行→4476行) | +595 行 |
| 核心·Web | web_app.py(2799→3189) | +390 行 |
| 核心·前端 | web_template.html(3214→3593) | +379 行 |
| 核心·入口 | commodity_demo.py + commodity_debate.py | +293 行 |
| CLI | cli/main.py + cli/models.py | +311 行 |
| 采集核心 | hybrid_pipeline.py / batch_collect.py / sentiment.py / sentiment_deep.py / ner.py | +462 行 |
| 平台适配器 | platforms/base + weibo/zhihu/xueqiu/xhs + `__init__` | +463 行 |

**注释规范**(agent 统一决策):函数上方加 `# 【功能】【参数】【返回】【关键逻辑】` 中文注释块,
**不替换**既有英文 docstring(避免改动字符串字面量、不影响 Typer --help 等行为);
拿不准的遗留问题一律标 `【待确认】`(如 `create_discussion_node` 未被图使用、
`run_simulated_trading` 入场时点 vs T+1 文档差异、`build_pnl_curve` 内 `while...pass` 死代码等),不编造用途。

**验证**:每批 py_compile + ast.parse;web_template 抽取内联 `<script>` 用 node --check;
最后全量 `pytest -ra --strict-markers -m "not integration" --timeout=120` =
**625 passed / 1 skipped**(langchain_aws 未装,既有跳过;1 deselected=integration),与基准一致。

注:该 venv 原本未装 pytest(此前验证只用 py_compile),本次 `pip install pytest pytest-timeout pytest-subtests` 补齐。

## 附2:注释批次 QA 复核与修复(2026-08-12 稍后)

提交前对全部 29 个改动文件做「剥注释+剥 docstring+字符串置空」的 AST 与 HEAD 比对,
发现 **2 个文件被注释 agent 引入了重复代码块**(插入注释时误复制了原代码):
- `commodity_debate.py` create_bull_debater 的 node():10 语句整块重复(幂等,行为不变)
- `tradingagents/graph/setup.py` setup_graph():`plan`+`analyst_factories` 重复

已手动删除重复块;修复后复核:
- 29/29 文件可执行逻辑与 HEAD 完全一致(仅注释/docstring 差异)
- 连续重复语句块扫描 = 0
- 全量 `pytest -m "not integration"` = **625 passed / 1 skipped** 与基准一致

> 教训:agent 声称「纯注释」仍需独立结构级核验(git diff 会被大段注释插入干扰对齐,
> 肉眼 diff 不可靠)。核验方法:归一化 AST(剥 docstring、字符串置空)对比 HEAD。

## 未做 / 后续可选

- 未提交 git、未推送 GitHub(用户未要求)。
- 若后续需求文档更新,建议按天更新 `learning/AgentSense/daily-plans/` 并同步 roadmap。

## 环境备忘

- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(625 passed / 1 skipped)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚;push 需 Clash 代理
- web_template.html 每次请求重读,前端改动无需重启服务器
- 服务器:端口 5000(PID 6304,PowerShell Start-Process)
- 学习文档目录 `C:\Users\19168\Desktop\learning\AgentSense\` 位于仓库之外,git 不含
