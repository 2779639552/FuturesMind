# 前端无情绪数据时降级为"三分析师"分析模式（2026-08-11）

> 一句话结论：无情绪数据时,分析不再强行跑 Sentiment 分析师(白白消耗 LLM 调用、产出"数据不足"
> 占位报告),而是**自动/手动降级为只跑 Technical/Fundamental/Macro 三分析师**,辩论/综合/情景照常。
> commit `3452653` 已推送,本地与远端同步,561 测试全绿。

## 需求

- 当某品种没有情绪(sentiment)数据时,前端仍应能发起有效分析 —— 此时只调用三个分析师。
- 触发方式:**自动降级 + 手动开关**两者都要。
- 此前"无法分析"的体感根因：无数据时 Sentiment 分析师照跑,浪费 LLM 调用并产出废报告混入结论。

## 关键发现（现状调查）

1. `commodity_demo.build_commodity_graph()` 固定并行跑 4 分析师,无跳过能力；`/api/run_analysis` 总是跑完整图。
2. **数据源不一致**：web_app 的 `SENTIMENT_DIR` 会回退到仓库 `data/external_data` 样本,前端能显示样本
   情绪；但 sentiment analyst 实际调用的 `sentiment_data.load_sentiment_data` **只读
   `~/.tradingagents/external_data`,不回退样本** → 前端"有情绪数据"、分析师却"拿不到"。自动降级判定
   必须先统一数据源。
3. CLI `cli/main.py` 已有"按选择条件 add_node/add_edge"的现成模式可参考；prompt 层用
   `if sentiment else "Not available."` 已优雅处理空值,无需改。

## 改动（commit 3452653）

| 文件 | 改动 |
|------|------|
| `commodity_demo.py` | `build_commodity_graph()` 新增 `include_sentiment=True` 参数;`False` 时不创建 sentiment 节点/注册/两条边(三处条件化) |
| `tradingagents/dataflows/sentiment_data.py` | `load_sentiment_data()` 增加**样本回退**：用户 `~/.tradingagents/external_data` → 仓库 `data/external_data`(与 web_app `SENTIMENT_DIR` 一致);新增 `_sentiment_dir()` 辅助 |
| `web_app.py` | `/api/run_analysis` 解析三态 `include_sentiment`(auto/include/exclude);`ProgressTracker` 新增 `stages` 参数动态化 `to_dict()`;`_run_agent_for_variety`/`_validate_one_variety` 同步 auto 降级;响应返回实际 `include_sentiment` + `stages` |
| `web_template.html` | 分析配置区加"情绪分析师"三态下拉(自动/始终包含/始终排除);`runAnalysis()` 用后端权威 stages 渲染 pipeline、动态计数、日志提示 Sentiment 是否跳过;`loadAnalysisResults` 已有空值守卫无需改 |
| `tests/test_commodity_graph_sentiment.py` | 新增 2 测试：排除时编译图不含 sentiment_analyst / 包含时保留 |

## 验证（全绿）

- 测试：**561 passed / 1 skipped / 1 deselected**(559 原有 + 2 新增,零回归)
- ruff check：全绿;ruff format：4 文件已格式化
- E2E(Flask test_client + 假 USERPROFILE 模拟全新环境,后台图用 stub 短路):

| 场景 | include_sentiment | 阶段数 | 结果 |
|------|:--:|:--:|:--:|
| 强制排除 exclude | false | 9 | OK |
| auto + 有样本品种 RB | true | 10 | OK |
| auto + 无样本品种 JM | false | 9 | 自动降级 OK |
| 强制包含 include | true | 10 | OK |

## 使用方式

- 默认"自动(无数据时跳过)"：有情绪数据跑 4 分析师,无数据自动只跑 3 分析师。
- 手动切"始终包含 / 始终排除(仅3分析师)"。
- `sentiment_report` 为空时,辩论/综合 prompt 自动显示 "Not available."。

## 环境备忘

- 推送需 Clash 代理：`git -c http.proxy=http://127.0.0.1:7890 push origin main`
- 测试：`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(561 passed)
- ruff：`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚
