# 运行分析输入数据小看板 + 研报上传Agent模块

## Context

用户(2026-09-01)提出两个功能需求,合并规划、一起批准实现:

1. **运行分析输入数据小看板**:在"运行分析"界面放一块小看板,展示所选品种**接下来运行分析将用到的真实数据**(价格/基差/库存/情绪/新闻/宏观)的最新可用快照。用户已确认:时间口径 = **最新可用数据**(与数据看板 tab 一致,标注"数据截至 X"),数据项 = 全部四类。
2. **研报上传Agent模块**:手动上传研报(PDF/图片/MD)→ 提取文本与结构化数据 → LLM 分析研报观点 → 输出结论并入库(SQLite `research_reports` 表 + `external_data/{SYM}_research.json`)→ **运行分析时可引用该数据**:新增 `get_research_report` 工具(基本面/宏观分析师可用)+ 研报数据并入对应方向(基差/库存/供需),**置信度与可信优先级最高**。已确认:文本型PDF用 pymupdf 提取、图片/扫描件复用仓库现有 Ollama VLM OCR(`data_collection/validate/image_pipeline_v2.py`,零新OCR依赖)。

两个功能都改 `web_app.py` + `web_template.html`,分开提交。

---

## Part A — 运行分析输入数据小看板

### A1 后端:新接口 `GET /api/run_input_data/<variety>`(web_app.py)

**入口路由分组注释**(L13-28 第3组后)补一行 `3.5) /api/run_input_data/<品种>`。

**imports**(L46-63 / L127-132):补 `import concurrent.futures`(isort 放 `import glob` 前);commodity_futures 导入块补 `get_futures_news`、`get_futures_macro`。

**4 个纯解析函数**(插在 `api_dashboard` 结束后、`api_dashboard_sector` 注释前),复用现有 `_inventory_points`/`_basis_points` 风格:
- `_run_input_basis_points(csv_text) -> (points, structure|None)`:解析 `get_futures_basis` CSV 全列(date/spot_price/dom_basis/dom_basis_rate/near_basis/near_basis_rate),从 `# Latest basis: x.xx — BACKWARDATION` 尾注取 structure。
- `_structure_from_basis(point) -> str|None`:兜底,按 dom_basis(无则 near_basis)正负判定 BACKWARDATION/CONTANGO/FLAT。
- `_inventory_trend(csv_text) -> str|None`:从 `# Warehouse receipt trend: BUILDING` 尾注取趋势词。
- `_parse_news_text(text) -> list[dict]`:按 `时间 [来源] 标题`(+可选缩进摘要续行)解析 `get_futures_news` 输出;`#`/空行跳过。
- `_parse_macro_text(text) -> (items, raw)`:按 `## 节` + 两空格缩进键值解析 `get_futures_macro` 输出;失败节 `## X: UNAVAILABLE (err)` → value None + note;无任何 `##` → `([], 原文)` 透传。

**路由 `api_run_input_data(variety)`**:返回六块 `{price, basis, inventory, sentiment, news, macro}` + `_meta{name, sector, data_as_of}`。各块 `available=false + note` 优雅降级,恒 200:
- 价格/基差/库存/新闻 → `ThreadPoolExecutor(max_workers=4)` 并行(api_dashboard 同款);end_date=`datetime.now()`,价格回看 30 天。
  - price:`get_futures_price` → `_adjusted_price_points` → 最新 close + change_pct。
  - basis:`get_futures_basis` → `_run_input_basis_points` → 最新 near/dom 基差 + 率 + structure。
  - inventory:`get_futures_inventory` → `_inventory_points` → 最新库存 + 日变化 + `_inventory_trend`。
  - news:`get_futures_news` → `_parse_news_text` → items;解析失败 `raw` 透传。
- 宏观:另起 `ThreadPoolExecutor(max_workers=1)`,`result(timeout=20)`;超时 → `MACRO_TIMEOUT` note;`shutdown(wait=False)` 不阻塞首拉。`get_futures_macro` → `_parse_macro_text`。
- 情绪:同步读 `SENTIMENT_DIR/{code}_sentiment.json`(与 api_sentiment 同口径)→ label/score/多空比/data_end;文件缺失 → `无情绪数据(code)` note。
- `data_as_of` = 各可用来源最新日期 max。

### A2 前端:运行分析 tab 面板(web_template.html)

**DOM**(插入配置行 `grid grid-2` 闭合后、`<!-- Pipeline Progress -->` 前):
```html
<div class="card" id="run-input-card" style="margin-bottom:16px">
  <h3>分析输入数据 <span class="badge badge-info" id="run-input-asof">数据截至: -</span></h3>
  <div id="run-input-body" style="font-size:0.85rem;color:var(--text-secondary)">加载中...</div>
</div>
```
复用 `.card/.grid/.stat-card/.stat-val/.stat-lbl/.badge`,无新 CSS。

**两个方法**(插在 `updateSentimentStats` 结束后):
- `async loadRunInputDash()`:`fetch('/api/run_input_data/{sym}')` → 设 `#run-input-asof` → `_renderRunInput(d)`;loading/错误处理。
- `_renderRunInput(d)` 渲染(纯字符串拼接,复用 `_escHtml`):
  1. `grid grid-4` 四张 stat-card:最新价(+涨跌%)/近月基差率(+结构中文)/仓单库存(+趋势)/情绪标签(+score·多空比)。
  2. 新闻滚动列表(前 8 条,`max-height:180px;overflow-y:auto`,时间/来源/标题/摘要)。
  3. 宏观摘要 chips(`badge badge-info`,name: value)。
  4. 各块 `unavailable` 的降级 note(琥珀色 chip);`raw` 原文 `<details><pre>` 透传。

**触发三处**:`#sym` onchange 追加 `App.loadRunInputDash()`;`_loadVarieties()` 内 `updateSentimentStats()` 后追加 `this.loadRunInputDash()`;`runAnalysis()` 在 `if (this._isAnalysisRunning) return;` 后加 `this.loadRunInputDash()`(fire-and-forget)。

### A3 测试:`tests/test_run_input_data.py`(新增,`@pytest.mark.unit`,不联网)

monkeypatch `web_app.get_futures_*` 返回合成 CSV/文本 + `_adjusted_price_points` + `SENTIMENT_DIR=tmp_path`。用例:解析(新闻基础/纯注释/占位时间、宏观成功+UNAVAILABLE+乱文本透传、基差尾注结构、库存趋势)、路由聚合成功(六块全 available + 精确值)、逐项降级(基差 NO_DATA / 情绪文件缺失 / 宏观全不可用 / 新闻解析失败 raw 透传 / 价格抛异常)。

---

## Part B — 研报上传Agent模块

### B0 数据模型与存储

**`database.py`:新增 `research_reports` 表**(仿 `trade_signals` 表 L225-238 风格,`_init_tables` 内加):
```sql
CREATE TABLE IF NOT EXISTS research_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  variety TEXT NOT NULL, title TEXT, source TEXT,
  filename TEXT, file_path TEXT,
  status TEXT DEFAULT 'processing',      -- processing/done/error
  extracted_text TEXT,                   -- 提取的原始文本(截断)
  structured_data TEXT,                  -- LLM 提取的结构化数据 JSON
  conclusion_md TEXT,                    -- LLM 观点分析结论(markdown)
  direction TEXT, confidence REAL,       -- 方向(看多/看空/中性)与置信度
  error TEXT, uploaded_at TEXT, created_at TEXT
)
```
新增方法:`insert_research_report(...)`、`update_research_report(id, **fields)`、`list_research_reports(variety=None)`、`get_research_report(id)`、`delete_research_report(id)`(仿 `insert_posts_batch` 风格)。

**`external_data/{SYM}_research.json`**(研报聚合,供分析师消费;新模块写):
```json
{"variety":"RB","updated":"...","reports":[
  {"id":1,"title":"...","source":"...","uploaded_at":"...","direction":"看多","confidence":0.8,
   "conclusion":"...","data_points":{...}}
]}
```
保留最近 N=10 份。`MAX_AGE_HOURS` 不用 168 过期(研报结论长期有效,但 tool 里标注更新时刻)。

### B1 新模块 `tradingagents/dataflows/research_data.py`

- `RESEARCH_DIR = Path.home()/".tradingagents"/"external_data"`;`RESEARCH_FILE = {RESEARCH_DIR}/{SYM}_research.json`;`_research_cache`(TTL 60s)+ `_load_research(variety)` / `_save_research(variety, dict)`。
- `load_research_data(variety) -> dict|None`(对外只读)。
- `get_research_report_text(variety) -> str`:格式化文本 `# RESEARCH 研报(高优先级)` + 每份报告方向/置信度/结论摘要 + 关键数据点;无数据返回 `RESEARCH_NO_DATA: 该品种暂无上传研报`。
- `upsert_research_report(variety, record)`:新报告插入列表头,截断到 10 份,写盘。
- `annotate_research(api_text, note) -> str`:`# DATA_SOURCE: RESEARCH (研报上传, 高优先级)` 头,供 merge 用。

### B2 文本提取:复用现有 OCR

`web_app.py` 新增:
- `RESEARCH_UPLOAD_DIR = Path.home()/".tradingagents"/"research_reports"`(按品种分目录存原始文件)。
- `_extract_report_text(path, ext) -> (text, used_ocr) -> str`:
  - `.pdf`:**pymupdf** `fitz.open(path)` → `page.get_text()` 拼接;若全篇文本过短(<200 字符,判定扫描件)→ 逐页 `page.get_pixmap(dpi=150)` 存临时 PNG → **复用 `data_collection/validate/image_pipeline_v2.py` 的 `stage1_classify_and_ocr(png)`** 聚合 OCR 文本。
  - `.png/.jpg/.jpeg`:直接 `stage1_classify_and_ocr(path)`。
  - `.md/.txt`:直接读文本。
- **OCR 优雅降级**:Ollama 不可用/超时(`call_ollama` 抛异常或空)→ 记 `error` 提示"图片OCR失败(需本地 Ollama),已保存PDF文本",不中断整条处理。
- 依赖变更:requirements.txt 新增 `PyMuPDF`(唯一新依赖;Ollama VLM 已存在)。

### B3 LLM 提取 + 观点分析

后台线程 `_process_research_report(report_id)`(仿 `run_analysis` 后台线程,`threading.Thread`):
1. 读 DB 行 → 按扩展名 `_extract_report_text` → 更新 `status='processing'` + `extracted_text`。
2. **LLM 复用现有封装**:`create_llm_client(config["llm_provider"], config.get("deep_think_llm", ...)).get_llm()`(参考 `web_app.py:1877-1882` 与 `commodity_demo.py:608-611`),`llm.invoke(prompt)`:
   - **结构化数据提取**:prompt 要求输出 JSON(字段:`spot_price{value,unit,date}`、`social_inventory{...}`、`supply{...}`、`demand{...}`、`costs{...}`、`target_price`、`key_events[{event,detail,impact,source}]`、`direction/confidence/rating`),用 `json` 块解析(复用 image_pipeline `parse_json_response` 同款逻辑)。
   - **观点分析结论**:第二段 prompt 要求 markdown 结论(研报核心观点摘要、数据支撑、与当前 Agent 分析可能分歧点、建议权重)。
3. 落库:`update_research_report(id, structured_data=..., conclusion_md=..., direction=..., confidence=..., status='done')`;`upsert_research_report(variety, record)` 写聚合 JSON。
4. 失败 → `status='error'` + `error` 字段。
- 进度:前端轮询 `GET /api/research` 看 status。

### B4 上传/列表/详情 API(web_app.py)

- `POST /api/research/upload`(multipart:`file` + `variety` + 可选 `title/source`):校验扩展名(.pdf/.png/.jpg/.jpeg/.md/.txt)与大小(≤20MB)、`secure_filename` 防穿越;存 `RESEARCH_UPLOAD_DIR/{variety}/{ts}_{safe}` → `insert_research_report(status='processing')` → 起后台线程处理 → 立即返回 `{id}`。
- `GET /api/research?variety=`:列表(状态/方向/置信度/时间)。
- `GET /api/research/<id>`:详情(structured_data + conclusion_md + extracted_text 摘要)。
- `DELETE /api/research/<id>`:删 DB 行 + 聚合 JSON 里对应记录 + 原始文件。
- 顶部路由分组注释补 4 组研报路由。

### B5 前端:研报 tab(web_template.html)

**新增第 9 个 tab**:
- 导航:`<button data-tab="research">研报</button>`(L585-592 按钮区,补到 dashboard 前或后);面板 `<div id="tab-research" class="tab">`。
- `App.switchTab`(L1381-1399)补 `t==='research' && this.loadResearch()` 懒加载分支。
- tab 内容:
  1. **上传卡**:品种 select(复用 `_populateVarietySelects`)+ `<input type="file" accept=".pdf,.png,.jpg,.jpeg,.md,.txt">` + 可选标题/来源 + `上传`按钮(`App.uploadResearch()`:`FormData` POST `/api/research/upload`,loading 态,`App.toast()`)。上传后提示"处理中",轮询刷新列表。
  2. **研报列表卡**:`App.loadResearch()` 渲染每份:标题/品种/来源/时间/状态 badge(processing/done/error)/方向/置信度;点击 `查看` → `App.viewResearch(id)`(拉详情:conclusion_md 用 `marked.parse` 渲染 + 结构化数据表格 + 提取文本摘要);`删除` → DELETE + 刷新。
- 复用现有样式与 `toast`;无 modal,inline 区段。

### B6 分析消费:新增工具 + 并入对应方向(最高优先级)

**新增工具**(commodity_futures_tools.py + interface.py):
- `@tool get_research_report(symbol) -> str`(`commodity_futures_tools.py`,参考 `get_futures_news` L145 风格)→ `route_to_vendor` → `research_data.get_research_report_text`。
- 加入工具白名单:`create_commodity_fundamental_analyst`(`commodity_analysts.py:373-380`)+ `create_commodity_macro_analyst`(`:541-547`)。

**并入对应方向 + 高优先级**(external_data.py / commodity_futures.py,小幅扩展):
- `merge_basis_data`(external_data.py:447):外部 JSON 无 spot_price 时,再查 `load_research_data` 的 `data_points.spot_price`;有则以研报现货价为**最高优先级**,输出 `# RESEARCH SPOT PRICE (研报,高优先级)` + 置信度。
- `merge_inventory_data`(:331):研究 `social_inventory`/`mill_inventory` 以最高优先级并入输出,标注 RESEARCH 来源。
- `get_futures_supply_demand`(commodity_futures.py:2878):末尾追加 `# RESEARCH 研报要点(高优先级)` 块(方向/置信度/关键数据点/key_events),标注"研报为人工上传,可信优先级最高"。
- 优先级链:研报 RESEARCH > 外部 EXTERNAL > 免费 API FREE_API(用户明确要求研报置信度最高)。

### B7 测试:`tests/test_research_module.py`(新增,`@pytest.mark.unit`,不联网不调 LLM)

- `research_data.py`:save/load/upsert 聚合 JSON、`get_research_report_text` 无数据占位、`annotate_research` 头。
- `database.py`:research_reports CRUD(insert/list/update/get/delete,隔离 tmp_path DB)。
- `_extract_report_text`:.md 直读;pymupdf 文本 PDF(monkeypatch fitz);图片走 OCR(mock `stage1_classify_and_ocr`);Ollama 异常降级。
- `_process_research_report`:mock 文本提取 + mock LLM(invoke 返回固定 JSON/结论)→ DB 行 done + `{SYM}_research.json` 写出;LLM 抛错 → status=error。
- 上传路由:multipart 文件 → 200 + {id} + DB 行 processing(后台线程 mock)。
- merge 高优先级:`merge_basis_data` 带研究 spot_price → 输出含 `RESEARCH SPOT PRICE`;`merge_inventory_data` 同理;`get_futures_supply_demand` 含 RESEARCH 块。
- 工具挂载:fundamental/macro 工厂的 tools 列表含 `get_research_report`。

---

## 验证

1. **ruff**:`venv\Scripts\ruff check web_app.py database.py external_data.py commodity_futures.py commodity_futures_tools.py commodity_analysts.py research_data.py tests\test_run_input_data.py tests\test_research_module.py`(注意 isort 顺序与 100 列)。
2. **全量 pytest**:`venv\Scripts\python -m pytest tests\test_run_input_data.py tests\test_research_module.py -v`,再 `venv\Scripts\python -m pytest tests -q` 无回归(重点 test_dashboard_route / test_dashboard_parsing / test_inventory_fallback / test_*database*)。
3. **重启服务**:`powershell -File scripts\restart_server.ps1`(可挂起则 kill 5000 后直接起新进程并轮询端口)。
4. **Part A 冒烟**:`curl -s "http://localhost:5000/api/run_input_data/RB" | python -m json.tool`(六块 + data_as_of);打开网页切运行分析 tab:配置行下方出现看板,切品种立即刷新,无基差/库存品种(WR/AO)对应块降级显示。
5. **Part B 冒烟**:网页切"研报"tab → 上传一份 MD/PDF → 状态 processing→done → 查看结论与结构化数据;`curl "http://localhost:5000/api/research?variety=RB"` 有记录;`curl "http://localhost:5000/api/run_input_data/RB"` 不受影响。跑一次运行分析,确认基本面/宏观分析师可调 `get_research_report`,merge 输出含 RESEARCH 高优先级标注(本地 LLM 需可用)。
6. **提交**(无 Co-Authored-By 页脚):Part A、Part B 各一个 commit;work-journal.md(project4 根目录)追加两段记录。

## 关键文件

- `web_app.py`:Part A 新路由 + 解析函数 + import;Part B 上传/列表/详情路由、`_extract_report_text`、`_process_research_report`、`RESEARCH_UPLOAD_DIR`。
- `web_template.html`:Part A 面板 DOM + `loadRunInputDash/_renderRunInput` + 触发;Part B 研报 tab + upload/list/view 方法 + switchTab 分支。
- `tradingagents/dataflows/research_data.py`(新):研报聚合 JSON 读写 + `get_research_report_text` + `annotate_research`。
- `database.py`:`research_reports` 表 + CRUD。
- `external_data.py`:`merge_basis_data`/`merge_inventory_data` 并入研报高优先级。
- `commodity_futures.py`:`get_futures_supply_demand` 追加研报块。
- `commodity_futures_tools.py` + `interface.py`:新增 `get_research_report` 工具与路由。
- `commodity_analysts.py`:fundamental/macro 工具白名单加 `get_research_report`。
- `data_collection/validate/image_pipeline_v2.py`:**只读复用** `stage1_classify_and_ocr`(不改)。
- `requirements.txt`:加 `PyMuPDF`。
- `tests/test_run_input_data.py`、`tests/test_research_module.py`(新增)。

## 关键复用(勿重造)

- `create_llm_client(...).get_llm()` + `llm.invoke`(`tradingagents/llm_clients/factory.py:27`、`web_app.py:1877-1882`)。
- `stage1_classify_and_ocr` / `call_ollama` / `prepare_image` / `parse_json_response`(`data_collection/validate/image_pipeline_v2.py:124/83/64/100`)。
- `_adjusted_price_points` / `_inventory_points` / `_basis_points` / `_dashboard_relationships`(web_app.py:916/923/948/1005)。
- `annotate_with_source` / `merge_basis_data` / `merge_inventory_data` / `load_external_data`(external_data.py:278/447/331/138)。
- `_run_tool_loop` / 分析师工厂(commodity_analysts.py:65/214/348/516)。
- `marked.parse`、`App.toast`、`_populateVarietySelects`、`switchTab`(web_template.html:1321/1406/2245/1381)。

## 后续优化(已完成讨论,等待研报模块收尾后实施)—— 并行提速 2026-09-02

背景:BU 分析 672s 中 4 分析师并行块 97.9s(14%)、辩论 187s(27%)、综合+情景 411s(59%)。
链条本身(分析师→辩论→裁决→综合→情景)是硬数据依赖,无法改并行;可挖的是"链内每次 LLM 往返的内部并行"。

- [ ] **优化A:工具调用并行** —— `_run_tool_loop`(commodity_analysts.py:144)每轮内多个 tool_calls 串行执行;
      分析师与三个辩论节点(commodity_debate.py:191)共用此循环。同一轮内 get_futures_price/basis/inventory
      互无依赖 → ThreadPoolExecutor 并发(结果按 tool_call id 键控,不影响"单卡==多策略"一致性)。
      收益:省掉数据获取延迟(大多有 5min/6h 磁盘缓存,收益有限但零风险)。
- [ ] **优化B:情景三路并行** —— create_scenario_node(commodity_demo.py:473)把牛市/基准/熊市三情景塞进
      一次 llm.invoke 串行生成,这是 411s 大头的来源(输出 token 主导延迟)。三情景互不依赖 →
      拆成 3 次 llm.invoke 并发再按序拼回,墙钟从"三份输出之和"降到"三份输出之 max"。
      收益最大,优先级最高。

实施顺序建议:B 先(A 顺带)。两处都不改图结构,只改节点内部,回归面小。
