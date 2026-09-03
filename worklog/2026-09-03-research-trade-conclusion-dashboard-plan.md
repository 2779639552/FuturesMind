# 研报结论交易化(头寸·单边区间·逻辑链)+ 研报基本面指标进数据看板

> **来源**：上一会话(57cac442,2026-09-03 17:25 收尾)定稿于 `~/.claude/plans/valiant-plotting-stonebraker.md`,会话中断未开工;本会话(2026-09-03 晚)应用户要求存档至 worklog 以防丢失。全文照录,开工后按 A→B→测试→存量重跑→真机验证 顺序执行。

# 研报结论交易化(头寸·单边区间·逻辑链)+ 研报基本面指标进数据看板(计划已细化)

> 状态：**计划已定稿待批准**。用户暂缓实现：先换后端 LLM 并重启服务后再开工。
> 本版已并入 Plan 子代理对 `web_app.py / web_template.html / research_data.py / database.py / scripts/* / scheduler.py` 及本地 `~/.tradingagents/agentsense.db` 的逐行只读核实(51 行全部 done、51/51 structured_data 含 publish_date、仅 28/51 varieties 段带四键、dev DB 尚无 ingest_source 列——重启后 _migrate 才补上)。

## Context(为什么做)

1. **观点要点不够贴交易**:研报结论两段式里只有 direction 列与"1 个边际变量/风险",缺 头寸/头寸范围/方向/风险/单边及其区间,且要"更注重逻辑"(推理链而非罗列)。
2. **研报基本面读数(基差/现货价/库存/仓单/开工率/加工利润…)看板画不上**:数据入库时已被 LLM 抽出、落 DB(structured_data.varieties[] 段内 data_points,确定性补漏后四键),但 `/api/dashboard` 只消费市场 API,研报指标从不入图,也无"随每天研报更新"的落点。

**用户已拍板(勿再问)**:
- 头寸口径=两者都要:具体手数/手数区间优先 → 否则仓位建议(轻仓/逢低分批/主力持有) → 都没有写 `—`,禁编造。
- 存量 ~51 份 = 新增走新模板 + 存量后台批量重跑结论(workers≤4)。
- 看板集成=两者都要:能对上现有面板的研报指标叠加到对应图 + 其余指标另起独立研报指标卡。

## 关键核实结论(决策依据)

| 点 | 结论 |
|---|---|
| publish_date 存哪 | 仅在 DB `research_reports.structured_data` 顶层 JSON(`_llm_extract_structured` 产出),非独立列 |
| 聚合 {CODE}_research.json | 只存 uploaded_at、`MAX_REPORTS=10`(给 LLM 控 token),顶层无 publish_date → **看板时序必须读 DB**,不改聚合、不升 MAX_REPORTS |
| 结论形态 | 51/51 含 `## 供需格局`+`## 数据支撑` 且无失败占位;无 position/target_range 类键 |
| 存量四键 | 仅 28/51 的 varieties 段带四键 → 看板 series 与"新结论看得见四键"都依赖结构化 → **批量先跑 re_extract 补四键** |
| 多品种覆盖 | `database.list_research_reports(variety=code)`(:832)已按 varieties 逗号串匹配 → 看板按品种列行即可,无需新查询/新列 |

---

## 工作流 A:结论/观点要点交易化

### A1. `_llm_opinion_conclusion`(web_app.py:2995,提示词 3003-3030)
- 篇幅:"200 字左右,180~260" → "**340 字左右,300~400**"(装推理链+交易要素;测试断言同步改)。
- 小节清单(:3006-3008)在 `## 观点与依据` 之后、Part2 `## 数据支撑` 之前,**新增一个固定节**:
  `## 交易要素与风险`。首尾锚点(`## 供需格局` 起 / Part2 `## 数据支撑` 起)**不动**。
- 写作规则(给 LLM 的硬约束,五要素按拍板口径):
  - `## 观点与依据`:先给**一句推理链** `因<事实/依据> → 推演<逻辑> → 方向<看多/看空/中性> + 单边/区间`,再附 1 个需跟踪边际变量(保留现有要求)。
  - `## 交易要素与风险`:**必须单行、中文分号分隔**五个片段(保证 `_compact_md` 压成可读单行):
    1. `方向:看多/看空/中性`
    2. `形态与区间:单边<看多/看空, 运行或目标区间 a~b> 或 区间震荡(区间 a~b)`;研报没给区间写 `—`
    3. `头寸:` 具体手数/手数区间优先(如"多头 30 手""建仓 20~40 手");无手数则回退仓位建议(轻仓/逢低分批/主力持有/等回调分批);都没有写 `—`
    4. `头寸范围:加仓/减仓/止损或触发价位区间`(如"回落 7800 加仓""跌破 7500 减仓");没给写 `—`
    5. `风险:主要风险或需跟踪边际变量(1 条)`
  - 缺失一律写 `—`(不写"未披露"字样,避免触发前端占位判据)。
- 失败兜底(:3037)不变。
- 新增模块常量(解析端复用语义,防手抄漂移):
  ```python
  _TRADE_TITLE_HINTS = ("交易要素", "头寸与风险", "仓位与风险")
  ```
- 新增研报:入库 `_process_research_report`(:3096)自动用当前提示词;scheduler 08:10/18:00 采集(`_run_research_collection`/`_run_htfc_collection`)天然进新格式,**scheduler.py 不改**。

### A2. `_extract_key_opinion`(web_app.py:3466)/ `_fit_section_lines`(:3506)
目标:单元格**首行=交易要素**,其后才是推理链(供需→…→观点),首尾锚点不变、旧文档兜底不变。
- `_extract_key_opinion`:`max_len` 240→360(前端本就有 >110 字符"▼ 展开");块循环中把命中 `_TRADE_TITLE_HINTS` 的块**提到输出首行**,其余保持文档顺序;旧四段式/无交易节的老文档退化为旧行为。
- `_fit_section_lines`:新增参数 `must_keep: tuple[int,...] = ()`,与既有 `{0, len-1}` 首末保护并集;预算按 protect 集先扣、其余"占位行先出、短节先出"裁剪;超预算仍有 `…` 兜底。交易行(≤~110 字)+供需+观点 远小于 360,protect 不爆预算。

### A3. 前端(web_template.html)
- 观点总览表头(:2946-2947)与导出表头(:3063)文案 → `观点要点(交易要素→推理链:头寸/方向/区间→依据→观点)`;导出 `<colgroup>`(:3058)末列 34%→40%(key_opinion 带 `white-space:pre-line`,结构不改)。
- 单元格渲染路径零结构改动(loadResearchViews 已展示多行 + clamp + 展开);研报详情弹层走 `marked.parse(conclusion_md)`,新节自动显示,零改动。

### A4. 标记协同更新(四处一处不落)
| 位置 | 动作 |
|---|---|
| web_app.py `_llm_opinion_conclusion` :3006-3008 | 加 `## 交易要素与风险`(保持 供需格局 首行/数据支撑 Part2 首) |
| web_app.py `_extract_key_opinion` :3487/3494 | 起止标记**不改**,只做交易行前置 |
| scripts/reconclude_research.py `_NEW_FMT_MARKERS` :47 | → `("## 供需格局", "## 数据支撑", "## 交易要素与风险")`,加注释指回 web_app 常量 |
| 相关测试断言 | 见 §测试 |

同时 `scripts/reconclude_research.py` `main` :151 `workers = max(1, min(4, args.workers))`(硬约束并发≤5)。

### A5. 存量 ~51 份批量重跑(幂等/断点/成本)
1. **备份**:`~/.tradingagents/agentsense.db` 与 `external_data/*_research.json`。
2. **预检不调 LLM**:`reconclude_research.py --dry-run --all`、`re_extract_research.py --dry-run --all`。
3. **第 1 步:四键补漏**(供新结论"看得见"四键,也喂看板):`re_extract_research.py --all --skip-extracted --workers 4`(~23 行命中;幂等,不动方向/评级/结论)。
4. **第 2 步:交易结论重跑**:`reconclude_research.py --all --workers 4`(~120-131 次结论调用,约 10~15 分钟;upsert 按 id 覆盖,中断安全;`--skip-fresh` 借新 marker 断点续跑)。

---

## 工作流 B:研报基本面进数据看板

### B1. 后端纯函数 `_research_dashboard_series(db, code)`(放 api_dashboard 附近)
- 输入 code;`db.list_research_reports(code, limit=2000)` 取 done 行(多品种覆盖已支持)。
- 每行:解析 `structured_data`;`eff = structured.publish_date 或 uploaded_at[:10]`;取 `varieties[]` 中 `variety.upper()==code` 的段(缺则首段),段内 `data_points` 嵌套 + 段顶层合并(复用 `_row_research_fund_metrics`:3402 的合并模式,只取**确定性数值**、不捞启发式文本)。
- 白名单 `_RESEARCH_DASH_KEYS = (basis, warehouse_receipts, operating_rate, processing_margin, spot_price, social_inventory, mill_inventory)`;`_fund_value`(:3555)归一,有值才出点 `{date, value, unit, note, point_date, source, title, report_id}`;`(键,日期)` 去重留最新 uploaded_at;点按 date 升序。
- 返回 `{"available", "note", "overlay": {"basis":[...]}, "standalone": {key:[...]}}`;整体 try/except → `available:False`(看板绝不 500)。
- **叠加判定(口径安全)**:仅 `basis`(元/吨,与看板基差轴同口径)→ overlay;其余(仓单 张/手 与东财库存序列单位不确定一致)一律 standalone;代码预留 `overlay.warehouse_receipts` 槽,实测单位一致再挪。

### B2. `api_dashboard`(web_app.py:1222-1303)
- 加第 4 个 loader `_load_research()`(get_db() 线程局部、SQLite 并发安全;try/except 降级);`ThreadPoolExecutor(max_workers=3)`→`4`(:1281);`_meta` 加 `research_available`;返回体加 `"research"`。

### B3. 前端 #tab-dashboard(web_template.html)
- `#chart-dash-inv-chg` 卡(:1397)后插 `<div id="dash-research"></div>`。
- `loadDashboard`(:3473)尾部调 `_renderResearchSection(d)`。
- **叠加**:`_renderDashCharts`(:3537)给 chart2 的 series 追加 `研报基差` scatter(色 #a855f7,symbolSize 8,`data=[idx, value]` 空档 null;legend 补名);日期用新增小工具 `_snapAxisIndex(dates, target)` 吸附最近 ≤target 交易日(与现有非交易日回退语义一致),tooltip 带真实 date/value/unit。
- **独立卡**:`_renderResearchSection(d)` 对每个有数据的 standalone 指标输出一张 `<div class="card"><h3>研报{label}({unit})</h3><div class="chart-container" id="chart-dash-r-{safeKey}" style="height:240px"></div></div>`(容器 id 用连字符、全页唯一);`_initChart` 渲染 line+scatter,tooltip 带 note/point_date/source/title;渲染前遍历 `_chartInstances` 清理 `chart-dash-r-*` 旧实例(防指标消失留孤儿);无数据 → `#dash-research` 置空。
- display:none 时机:switchTab('dashboard') 先 active 再 loadDashboard,容器可见后才 init。

---

## 改动文件清单(含锚点)
- **web_app.py**:`_llm_opinion_conclusion`:2995(提示词 3003-3030+`_TRADE_TITLE_HINTS`)、`_extract_key_opinion`:3466、`_fit_section_lines`:3506、新增 `_research_dashboard_series`+`_RESEARCH_DASH_KEYS`、`api_dashboard`:1222(第 4 loader/ThreadPool 3→4/_meta/return)。
- **web_template.html**:`#tab-dashboard` 插 `#dash-research`;观点总览表头 :2946-2947 / 导出 :3063 / colgroup :3058;`loadDashboard`:3473 尾部调 `_renderResearchSection`;`_renderDashCharts`:3537 加"研报基差"scatter;新增 `_snapAxisIndex`、`_renderResearchSection`(含 chart-dash-r-* 清理)。
- **scripts/reconclude_research.py**:`_NEW_FMT_MARKERS`:47、`workers` 钳 4 (:151)。
- **scripts/re_extract_research.py / scheduler.py**:不改(仅作存量执行入口/自动新格式)。
- **database.py / research_data.py**:不改。

## 测试(全离线不触网)
- **A**:`tests/test_research_module.py`:
  - `TestOpinionConclusionPromptShape.test_prompt_has_fixed_sections_and_writing_rules`(:475):断言含 `## 交易要素与风险`、篇幅新表述、五要素引导词(头寸/头寸范围/单边/区间震荡/风险/方向)。
  - `TestResearchViewsHelpers._TWO_PART` fixture(:776-788)补交易节;`test_extract_two_part_opinion_six_sections`(:790)断言 `out.startswith("交易要素与风险：")` 且 `观点与依据` 仍在、`数据支撑` 不进单元格;新增 `test_extract_trade_section_blank_uses_dash`。
  - `test_fit_over_budget_keeps_last_section`(:830)/`test_fit_drops_placeholder_lines_first`(:848):适配 `must_keep`;新增"带交易行+预算紧 → 交易行首行保、供需保、观点末行保、占位行先出"。
  - 旧四段式兜底(:818)、`test_view_row_shape`(:875)等随 fixture 更新仍绿。
- **B**:`tests/test_dashboard_route.py` 的 `_mock_all_sources`(:19)补 `monkeypatch.setattr(web_app, "_research_dashboard_series", lambda *a,**k: {"available":False,"note":"","overlay":{},"standalone":{}})`(保 `set(calls)` 断言、不碰真实 dev DB);新增"mock 返回含 basis overlay+operating_rate standalone → 断言响应 research 结构与 research_available"。
- **新增 `tests/test_research_dashboard_series.py`**(monkeypatch `web_app.get_db` 指向 tmp_path AgentSenseDB):同品种两行不同 publish_date 按真实日期归键升序、(键,日期)去重留最新;publish_date 缺 → 回退 uploaded_at[:10];无四键旧行不进 series;available 判定。
- `tests/test_dashboard_parsing.py`:不改。
- ruff 0(web_app.py / scripts/reconclude_research.py);全量 pytest 回归(基线 ~1042)。

## 验证(真机)
1. `pytest` 全绿 + ruff 0。
2. 批量(备份→dry-run→re_extract 补四键→reconclude 重跑)跑完 0 失败;抽查 2-3 份:conclusion_md 含 `## 交易要素与风险`、观点要点首行为交易要素、四键已补。
3. 浏览器(先 stop_web.bat 停旧 → 重启 → Ctrl+F5):观点总览交易行前置 + 推理链;看板出现研报指标独立卡(按指标切换、tooltip 可核对)、基差图出现「研报基差」紫 scatter、随当日研报更新;数据仓库页回归无恙。
4. work-journal 逐阶段追加;commit 等用户指示(不带 Co-Authored-By)。

## 风险 / 未决
1. 单元格变长(~340 字):已有 clamp+展开;必要时 `.rv-tbl` 字号降到 0.8rem。
2. 交易节为自由文本,LLM 可能漏写 `—`:提示词硬约束"单行、分号分隔、无则 —";解析端不指望结构化,显示不炸。
3. `_NEW_FMT_MARKERS` 与 web_app 节标题常量双份手抄同步(scripts 刻意懒 import 不拉 web_app,固有限制;已要求交叉注释)。
4. 存量四键 28/51 → 批量必须先跑 re_extract;真没披露的品种该键自然不出(空态文案明确)。
5. 聚合(最近 10)与 DB(全量)行数不一致属既有限制,看板走 DB 规避;观点总览仍读聚合(日常同源采集下一致),本次不动。
6. 基差叠加用 `_snapAxisIndex` 吸附最近交易日,tooltip 带真实日期兜底;实施时实测吸附偏差。
7. 仓单是否叠加到 #chart-dash-pi:取决于东财库存与研报仓单单位(张 vs 手),默认独立卡,预留开关。
8. **dev DB 尚无 ingest_source 列**:dashboard 路径只走 `list_research_reports(code, limit=…)`(不带 ingest_source 筛选),安全;用户重启后 _migrate 自动补列。

## 下一步(等用户)
用户即将更换后端 LLM 并重启服务。重启后按 A→B→测试→批量重跑→真机验证顺序开工;work-journal 逐阶段追加;不 commit(等指示)。
