
## 2026-09-03 (深夜) — 研报交易化+看板集成 开工:A/B/测试全绿

**状态**:✅ 完成(存量批量重跑进行中)

**做了什么**:
- 按存档计划(worklog/2026-09-03-research-trade-conclusion-dashboard-plan.md)开工,锚点逐行核实全部一致。
- **A 结论交易化**:`_llm_opinion_conclusion` 篇幅 200→340 字、第一部分固定七小节新增 `## 交易要素与风险`(五要素单行分号分隔:方向/形态与区间/头寸/头寸范围/风险,缺失写「—」禁编造)、观点与依据改推理链式;新增 `_TRADE_TITLE_HINTS` 常量。`_extract_key_opinion` max_len 240→360、交易行前置首行(先结论后论据);`_fit_section_lines` 加 `must_keep` 参数(与首末行并集保护,占位行先出局语义不变)。
- **A3/A4 前端与脚本**:观点总览/导出表头改「观点要点(交易要素→推理链:头寸/方向/区间→依据→观点)」、导出末列 34%→40%;reconclude_research.py `_NEW_FMT_MARKERS` 加第三标记「## 交易要素与风险」(与 web_app 常量交叉注释)、workers 硬钳 ≤4。
- **B 看板集成**:新增 `_RESEARCH_DASH_KEYS`(7 键白名单)+ `_RESEARCH_DASH_OVERLAY_KEYS`(仅 basis 叠加)+ `_dash_num`(数值归一,文本值不进图)+ `_research_dashboard_series`(读 DB 不读聚合、publish_date 缺回退 uploaded_at、(键,日期)去重留最新、整体 try/except 绝不 500);api_dashboard 第 4 loader + ThreadPool 3→4 + `_meta.research_available/research_note`。前端:`#dash-research` 容器、`_snapAxisIndex` 交易日吸附、基差图叠加「研报基差」紫 scatter(#a855f7, tooltip 带研报真实日期/来源/标题)、`_renderResearchSection` 独立卡(line+散点, chart-dash-r-* 孤儿实例清理;市场基差不可用时研报基差并进独立卡)。
- **测试**:test_research_module 断言更新(七小节/340 字/五要素/交易行前置/must_keep 预算用例);test_dashboard_route 补 research mock + 透传/降级 2 用例;新增 test_research_dashboard_series.py 9 用例(FakeDB 控制时间戳 + tmp_path 真库集成)。ruff 改动 5 文件全 0;全量 **1055 passed + 1 环境 skip**(基线 1042 + 13 新增)。
- **存量批量**:已备份 `AgentSense/backup/2026-09-03-trade-dashboard/`(DB 3MB + 54 份聚合 JSON);re_extract/reconclude 双 dry-run 预检 51 行 done 确认;re_extract --all --skip-extracted --workers 4 后台执行中。

**下一步**:
- re_extract 完成 → reconclude --all --workers 4(~120-131 次结论调用)→ 抽查新结论含交易节 → 停旧重启 web_app 真机验收 → work-journal 收口。

## 2026-09-04 (凌晨) — 存量重跑 48/51 完成 + 看板/观点总览真机验收通过(3 行待 LLM 充值后补跑)

**状态**:🔄 主体完成(堵点:DeepSeek 余额不足)

**做了什么**:
- **批量重跑**:re_extract --all --skip-extracted 仅 #25 一行缺四键(其余 50 行此前已补),103s 成功;reconclude --all --workers 4 跑完 51 行 0 失败(3082s),DB 中 48/51 行 conclusion_md 含「## 交易要素与风险」且质量抽查良好(#47:交易行前置+五要素+推理链,缺失写「—」未编造)。
- **发现并修复一个提取 bug**:交易行前置后 `_fit_section_lines` 首行保护位被交易行占走,超预算时供需格局行(最长)先出局;修复 = must_keep 显式保护 交易行+供需行+末行,85 项相关测试全绿,#47 复验供需行回归。
- **3 行失败占位(#55/#58/#60)**:批量末尾及定点重跑撞 **DeepSeek 402 Insufficient Balance**(账户余额耗尽,~4s/次瞬时失败,且 55/58 的好内容被占位覆盖)。备份可用(backup/2026-09-03-trade-dashboard/),充值后 `reconclude_research.py --ids "55 58 60" --workers 3` 即可补齐。
- **真机验收(web_app 已重启)**:/api/dashboard/MA research_available=True,overlay.basis 在列,standalone 5 指标(开工率 73.91%/加工利润 880 元/吨/现货 3220/社库 68.56 万吨/厂库 31.2 万吨,均带 source);SC 降级正常(仅开工率)、RB 空态正常;/api/research/views MA 三行观点要点**首行均为交易要素行**;HTTP 全 200。

**下一步**:
- 用户给 DeepSeek 充值 → 我跑 `reconclude_research.py --ids "55 58 60"` 补齐 51/51。
- 用户浏览器 Ctrl+F5 验收:观点总览新表头+交易行前置;数据看板基差图紫 scatter「研报基差」+ 研报指标独立卡;数据仓库页回归。
- 全部改动待 commit(等指示,不带 Co-Authored-By)。

## 2026-09-04 — LLM 切换火山方舟 Coding Plan(替代 DeepSeek)+ 存量批量收口 51/51

**状态**:✅ 完成

**做了什么**:
- **provider 接入**:新增 `doubao`(火山方舟 Coding Plan)到 LLM 兼容层三处单一事实来源——api_key_env(ARK_API_KEY)、openai_client 注册表(base_url=`https://ark.cn-beijing.volces.com/api/coding/v3`,chat_class 复用 DeepSeekChatOpenAI round-trip,实测 Ark 兼容 reasoning_content 回传)、model_catalog(doubao 段 4 个实测可用模型)。
- **端点实测踩坑**:Coding Plan 额度只从 `/api/coding/v3` 扣;`/api/v3` 是按量付费且该账户模型全未开通(ModelNotOpen)。套餐内实测可用:ark-code-latest(Auto 智能选型,实测路由 deepseek-v4-flash)/doubao-seed-code/doubao-seed-2-0-pro-260215/doubao-seed-2-0-mini-260215;2-1-pro 与 seed-1-6 报 UnsupportedModel。
- **配置切换**:.env `TRADINGAGENTS_LLM_PROVIDER=doubao`、deep=`doubao-seed-2-0-pro-260215`、quick=`ark-code-latest`、新增 `ARK_API_KEY`(DeepSeek key 保留未删);/api/config 实测已生效。
- **存量收口**:用新 LLM 补跑 #55/#58/#60(60.7s,3/3 ok)→ **全库 51/51 结论含「## 交易要素与风险」,失败占位 0**,抽查 #55/#60 内容正常(缺失写「—」)。
- 回归:LLM 客户端相关 114 passed + 72 subtests;ruff 改动 3 文件中 openai_client.py 的 I001 为存量问题(stash 对比验证,改动集外)。
- web_app 已按规程停旧重启(独立分离进程),新配置生效。

**下一步**:
- 用户浏览器验收(观点总览交易行 + 数据看板研报指标卡/研报基差紫点)。
- 全部改动(含本次 provider 接入)待 commit,等指示(不带 Co-Authored-By)。

## 2026-09-04 — LLM 模型下调:quick/deep 均切火山套餐内 deepseek-v4-flash(省额度)

**状态**:✅ 完成

**做了什么**:
- 用户反馈 doubao-seed-2-0-pro 消耗偏高,quick/deep 均改为套餐内直调 `deepseek-v4-flash`(curl 实测 /api/coding/v3 直调可用,与原 DeepSeek 命名一致)。
- model_catalog doubao 段同步补 v4-flash 直调项(quick/deep 首选),注释更新为"五个 ID 实测调通"。
- web_app 按规程停旧重启,/api/config 实测 provider=doubao、quick=deep=deepseek-v4-flash,真调用 OK;LLM 客户端相关测试全绿。

**下一步**:
- 浏览器验收;全部改动待 commit(等指示)。

## 2026-09-04 — 国君同名日报重复根因+删旧迎新去重落地,web_app 重启

**状态**:✅ 完成

**做了什么**:
- **查清"两篇一样研报"根因**:国君日报标题不带日期且观点不变则连日同名(如《尿素：区间运行》),采集窗口 `days=1` 实为 `now-1d~now` 两天闭区间,昨日版+今日版都拉回;infoId 不同 → seen/文件名两层防重均不命中,run_titles 只防同日。逐对比对正文:2 组真重复(#105 纯碱逐字节同 #81(我 E2E 验证篇+正式采集各进一次)、#114 橡胶只差日期戳),其余为跨日两版(相似度 0.87~0.97,只差一天数据);#111/#104 短纤、#55/#77 华泰标题自带日期,非重复。
- **删旧迎新去重**:web_app 删除路由抽成 `_delete_research_report_full()`(聚合 JSON+原件+DB 行+孤儿清扫,路由与采集器共用);database.py 新增 `list_research_reports_since(since)`;research_collector_gtja 新增 `_norm_title`+`_supersede_same_title`(近 SUPERSEDE_TITLE_DAYS=3 天同归一化标题 → 删旧迎新),挂 `_ingest_one` 新入库分支,processing 自愈分支不受影响。
- **清理**:删 #105/#114(含原件+聚合 JSON),/api/research 实测纯碱/橡胶各剩 1 行;#117/#118(processing 残留)infoId 不在 seen,18:00 定时采集会走 filename 幂等自愈补跑。
- **测试**:test_research_collector_gtja 新增 4 用例(_norm_title/同名删旧/空白归一命中/DB 故障容错,桩打 web_app 侧因懒导入);全量 1097 passed+1 skip(基线 1093+4);ruff 4 文件 0。test_deepseek_reasoning 1 个实况用例 402 失败=DeepSeek 余额耗尽(已知环境问题,与改动无关,已 deselect 计入。
- **重启**:发现 5000 端口无监听(上一后台重启任务 exit 127 实际未起)→ 重新后台启动;错峰时刻表核实生效(18:00/18:15/18:30、明日 08:10/08:25/08:40)。

**下一步**:
- 18:00 错峰首跑观察:fxbaogao→HTFC(+15min)→GTJA(+30min) 依次执行,GTJA 应触发同名删旧迎新+#117/#118 自愈。
- 全部改动待 commit(等指示,不带 Co-Authored-By)。

## 2026-09-04 (下午) — 研报采集支持按品种筛选(防全量 LLM 耗时过长)

**状态**:✅ 完成(等采集空闲后自动重启生效)

**做了什么**:
- **后端** /api/research/collect POST 新增可选 `varieties`(品种代码列表):大写归一后按 TARGET_VARIETIES(21品种,与华泰同源)白名单校验,未知代码 400;requested 透传 `ingest_today(requested=)`(华泰)与 `ingest_recent(requested=)`(国君);**按品种采集时排除发现报告源**(用户明确要求——它按机构抓取、入库前无法预判品种,筛了也白跑),响应 message/label 同步标注。空/缺省/全空白串 = 全部品种,行为不变。
- **前端** 采集按钮旁新增 "🔬 品种" 下拉面板(checkbox 双列,this._varieties 填充,只填一次;"全部品种"总开关;按钮文案同步如 "🔬 品种: MA/TA 等3个");collectResearch 把所选品种随 body 提交,面板外点击自动收起。web_template.html 按请求即时生效,路由部分待重启。
- **测试**:test_research_collect_route fake 采集器记录 requested + gtja fake 带 TARGET_VARIETIES;新增 3 用例(品种透传且排除 fxbaogao/未知代码 400/空白串=全部)。全量 **1100 passed + 1 skip**(deselect DeepSeek 402 实况用例=余额耗尽环境问题);ruff 0。
- **重启**:用户手动触发的采集尚在运行 → 挂后台哨兵 _restart_when_idle.sh 轮询 collecting 标志,空闲后 taskkill + PowerShell Start-Process 分离进程重启(venv 转发器 run_in_background 方式会随任务壳退出 127 带走进程树,弃用)。

**下一步**:
- 哨兵重启后验证 /api/research/collect 存活;用户浏览器 Ctrl+F5 验收品种面板。
- 全部改动待 commit(等指示,不带 Co-Authored-By)。
- **收口(13:3x)**:哨兵在采集结束后自动重启成功(停 288892 → 分离进程新起);POST {"varieties":["XX"]} 实测 400「未知品种代码」=新路由已生效;错峰调度表(18:00/18:15/18:30)正常注册。按品种采集功能全链路上线。
- **研报页模块说明(下午)**:tab-research 顶部新增折叠"📖 模块说明"(复用 expander 组件,默认收起点击展开):模块简介(三源采集→LLM 结构化提取→三张视图)+ 置信度评分语义分档表(0.75~0.95/0.55~0.75/0.4~0.6/0.3~0.5+证据冲突取低档)与"—(未给)"口径说明;模板即时生效无需重启,已验证线上。
- **研报弹层直达原件(下午)**:抽 `_researchFileButtons(id,fp)` 助手(pdf/图片→在线查看+下载,md→下载),viewResearch 总结弹层头部右侧注入按钮组(详情接口本就返回 file_path),viewResearchOriginal 原文弹层改为复用同一助手;研报页点研报名称即可在线查看/下载原件,无需绕数据仓库。模板即时生效,线上验证 3 处引用。

## 2026-09-04 (傍晚) — 研报宏观事件注入宏观/情绪分析师,全量回归+重启收口

**状态**:✅ 完成

**做了什么**:
- **数据层**:research_data.py 新增 `summarize_research_macro_events(variety, days=3)`——扫 RESEARCH_DIR 全品种聚合 JSON 的 `data_points.key_events`,产出两段确定性文本:①宏观共性事件(同一归一化事件被 ≥2 品种研报提及=宏观级驱动,按利多/利空/中性票数排序,最多 8 条);②本品种研报事件与观点(事件+影响+细节截断 80 字,关联该研报方向/置信度,最多 10 条);无数据返回空串。database.py 补 `list_research_reports_since(since)`。
- **注入链路**:`research_macro_context(symbol)` 桥接函数放 commodity_futures_tools.py(遵守"分析师不直调 dataflows"分层,注释注明这是有意例外;不用 @tool 因工具调用不保证发生,确定性前置=100% 可靠零工具轮成本);宏观分析师提示新增"第 0 节"使用指引(研报观点是主观票数须交叉验证/方向置信度是卖方观点不作自己偏见/空块如实说明不要编造),情绪分析师第 8 节(机构群体)扩研报事件驱动条目;两节点在 evolution_ctx 注入块之后同通道前置注入,空串不注入。
- **测试**:test_research_module 追加 4 用例(共性事件聚合/天数过滤/无数据空串/桥接静默降级);新增 test_research_macro_injection.py 4 用例(宏/情注入到位且先于第 0 节/无事件不注入/第 0 节常驻)。踩坑:①ChatPromptTemplate 包前言→不能断言 startswith,改 index 比较位置;②第 0 节指引文字本身含 "RESEARCH 宏观事件" 字样→无事件断言用块标记头 "# RESEARCH 宏观事件"。ruff 4 个 I001 经 stash 对比=存量(长中文注释超行宽),改动集外。
- **收口**:全量 **1108 passed + 1 skip + 1 deselected**(DeepSeek 402 实况用例继续 deselect);web_app 停旧重启(PowerShell 分离进程,venv 转发器 run_in_background 会 127 带走进程树,弃用),scheduler 18:00 错峰三任务完好。探测插曲:误 POST `{}` 触发一次采集,`collecting:false` 秒回=今日源已采过空跑无副作用。
- 待办观察:18:00/18:15/18:30 错峰采集应触发国君同名删旧迎新+#117/#118 自愈。

**下一步**:
- 用户下一次分析运行即可看到研报事件进入宏观/情绪报告;全部改动待 commit(等指示,不带 Co-Authored-By)。

## 2026-09-07 — 自传数据接口:上传→LLM格式识别→分析师最高权重注入(仅上传电脑可用+服务器不留原件)

**状态**:✅ 完成(全量回归中,web_app 已重启)

**做了什么**:
- **数据层** database.py 新建 `user_datasets` 表(filename/variety/client_tag/data_type/spec/data/row_count/status/error)+ 六个方法(insert/update/list/get/list_active/delete);`list_user_datasets` 双条件过滤曾有一段残留重复 WHERE 代码块(variety+client_tag 同筛会拼出非法 SQL),已清。
- **解析管线** 新文件 tradingagents/dataflows/user_data.py(~250 行):确定性读行(xlsx/xls/csv 走 pandas 多 sheet,md/txt 走内置管道表格解析器,NaN→None,datetime→ISO,上限 2000 行)→ LLM 看前 8 行样张产严格 JSON 规格(品种/数据类型/日期列/列含义/单位/频率,手选品种优先,非 JSON 报错)→ 归一化(**只转日期列,数值列绝不改写**,防 LLM 编造)→ 回写 done/error。`_norm_date` 支持 2026/9/1 等非补零格式(fromisoformat 之外补 strptime 兜底)。
- **注入链路** 与 research_macro_context 同模式:macro/sentiment 两节点在研报事件块**之上**前置注入(最高优先级置顶);注入头写明权重规则——数据覆盖范围内以自传数据为准(>akshare 实时数据,不低于研报渠道),冲突采用用户数据并指出差异,未覆盖指标按常规取数。commodity_futures_tools.py 加 `user_data_context(symbol, client_tag)` 桥接。
- **隔离(client_tag)** 用户要求"自传数据仅在上传电脑上使用":web_app 加 `_client_tag()`(CF-Connecting-IP→XFF 首跳→remote_addr,归一 ::ffff:/::1);上传打标,列表/详情/删除只对同 tag 客户端可见(跨机 404),`render_user_data_context` 空 tag 直接返回 "";**只有交互式 /api/run_analysis 注入**(initial_state 加 client_tag),批量回测/校验跑历史行情不注入防失真。
- **隐私** 用户要求"不下载到服务器端,或下载后在服务器端删除":原文件落盘解析,**无论成败 finally 即删**(服务器只留归一化数据行+错误原因);前端卡片说明同步标注。
- **前端** 数据仓库页新增「📤 自传数据」卡(品种留空自动识别,≤20MB,状态徽标 已接入/解析失败/解析中,详情预览 100 行,删除带确认)。
- **踩坑修复**:纯中文文件名被 secure_filename 清成空 → 落盘无扩展名 → 解析器按扩展名分流报"不支持的文件类型";修复=落盘名保住原始扩展名,补测试。
- **测试** 新增 tests/test_user_data.py 17 用例(md/xlsx/csv 解析、围栏 JSON 规格、hint 覆盖、日期归一、client_tag 隔离、ingest 成败双路径、上传打标/跨机 404/中文文件名/非法扩展、macro/sentiment 注入与空 tag 不注入,镜像 test_research_macro_injection 打桩法);test_alert_resilience.py 3 用例 + test_htfc_collector.py 周报 9 用例同批。改动文件 ruff 全 0(4 个 I001 经 stash 对比=存量)。
- **真机 E2E**:上传《甲醇社会库存.md》→ LLM 识别 MA/周度/万吨口径 → done 2 行;验证原文件已删、同 tag 注入 389 字、异 tag 0 注入;测试数据已清理不留库。

**下一步**:
- 用户浏览器 Ctrl+F5 验收:数据仓库页「📤 自传数据」卡,传一份真实 Excel/MD 试试。
- 全部改动(含 HTFC 周报通道)待 commit,等指示(不带 Co-Authored-By)。

## 2026-09-07 — 全量拷贝备份(自传数据功能完成后)

**状态**:✅ 完成

**做了什么**:
- 备份到项目外 `C:\Users\19168\Desktop\project4\backup_AgentSense_2026-09-07\`(防递归):
  - `AgentSense/`(50MB,943 文件):全部源码+测试+worklog+.git+.env,robocopy 排除 venv(667MB 可重装)/__pycache__/.pytest_cache;
  - `tradingagents-data/`(199MB,465 文件):~/.tradingagents 整目录(agentsense.db、研报原件与聚合 JSON、自传数据、采集 seen 等)。
- 抽验:DB 在、user_data.py/test_user_data.py 等本次新文件在、web_app.py 含 client_tag 新代码(7 处)= 备份确为含今日全部未提交改动的最新版本。

**下一步**:
- 用户浏览器 Ctrl+F5 验收自传数据卡;全部改动待 commit,等指示。

## 2026-09-07 13:45 — 华泰研报未录入排查(403 key 无效)

**状态**:✅ 排查完成,根因已定位且已修复

**结论**:
- 今日唯一一次 HTFC 定时采集(02:16)失败:403「key无效」——旧 API key 已在服务端作废(告警中心 htfc_error 有记录),当天再无华泰官方渠道采集。
- 上午已换新 key(.env + 注册表 10:22),实测有效;web_app 11:38 重启已注入新 key,今晚 18:15 定时任务可正常采。
- 截至现在天玑「日报」栏目今日仅上架 1 篇:RE17600 液化天然气日报(不命中 21 品种,按设计跳过)。其余若为周报/晚上架,18:15 任务会一并采。

**经验**:进程环境变量里的 HTFC_API_KEY 优先于 .env(load_dotenv override=False)——换 key 后必须重启 web_app/新开 shell,否则子进程仍带旧 key 报「key无效」。

## 2026-09-07 14:30 — 抖音评论首采成功(MediaCrawler 全链路打通)+ 置信度/方向两处修复

**状态**:✅ 完成

**做了什么**:
- **抖音首采**:MediaCrawler CDP 模式被抖音登录面板拦点击 → 关 CDP 改标准 Playwright;自动点"登录"仍被全屏面板拦 → 新增 `MediaCrawler/dy_prelogin.py`(持久化 context 停在 douyin.com 首页,面板自带二维码,轮询 sessionid 240-300s)。用户扫码一次,登录态落 `browser_data/dy_user_data_dir`。采集 3 试点关键词 → **41 视频 + 808 评论**。
- **转换接入**:`douyin_mediacrawler_import.py` 转出 `batch_douyin_20260907_140818.jsonl` 782 条(741 评论+41 视频),NER 命中 98%(甲醇 302/螺纹钢 291/纯碱 191),情感分布合理。trend_aggregator 60 品种(douyin 782 条入聚合)→ generate 后 **MA_sentiment.json source_platforms 含 douyin(125 条,MA 第一大来源)**。
- **每日总结置信度修复**:`_collect_daily_report_items` 原把 DB 行级置信度无差别塞给多品种研报每个品种段(LU 冒用 FU 的 45%)。改为逐品种取聚合 JSON 置信度,回退:主品种 DB 值、次品种"—"。回归测试 test_collect_items_confidence_per_variety。
- **多品种方向**:机制本就逐品种(同报告 EG 看多/TA 中性各自显示);id 165"多PX 空PTA"被判中性是提取提示词缺价差腿规则 → 第一步 prompt 加 3d(价差腿按腿记方向,正文单边明确冲突时以正文为准);165 已重提取(置信度 0.45→0.6,TA 仍中性——该报告同时给"PTA:单边偏强",源文本自身多空并存)。
- **HTFC 403**:根因旧 key 服务端作废+进程环境变量优先于 .env;换 key 后未重启进程的 shell 仍带旧 key。web_app 已按规程重启(PID 65820,新 key 注入)。
- **测试**:全量 **1167 passed + 1 skip**,ruff 全绿。

**下一步**:
- reconclude(165) 完成后 force 重生成今日总结;真机一键更新勾抖音验证(注意:web 一键更新走的是 DOM 兜底适配器,MediaCrawler 路线目前脚本驱动,后续可做 bridge)。

## 2026-09-07 15:00 — 抖音并入平台回测权重

**状态**:✅ 完成

**做了什么**:
- 抖音数据入聚合后回测一直没跑(_global_weights.json 是旧值)→ 手动跑 `backtest_weights.py`:全局池化 douyin **n=24, acc=0.375, r=-0.2204**(反向指标特征,与"散户一致性≈反向"假设吻合),softmax 后 **douyin 权重 0.1292**(六平台最低,合理)。
- 品种级 {品种}_weights.json 复用全局权重 → 60 品种全部带上 douyin;weighted_sentiment 已按新权重重算。
- 重跑 generate_tradingagents_sentiment → external_data/*_sentiment.json 的 platform_weights 含 douyin(source=global_backtest)。
- 确认自动化无缺口:AgentSense scheduler.py:215 与 web_app.py:5708 的聚合任务都会调 backtest_weights.run_all,之前只是抖音入库后尚未轮到调度。

## 2026-09-07 (下午) — 抖音系列收口 + 观点要点重构(综述→利多/利空)

**状态**:🔄 主体完成(存量示例重跑 16 份进行中)

**做了什么**:
- **抖音收口**:web 一键更新勾 douyin E2E 走通(7 步无 500;DOM 兜底适配器因无登录态产出空文件=预期形态,MediaCrawler 为主力路线);策略探索结论落盘 `worklog/2026-09-07-platform-factor-strategy-deferred.md`(平台回测表/假设清单/4 触点实现路径,门槛 n≥100/平台再启动)。
- **观点要点新口径**(用户要求:综述→利多/利空,各附逻辑支持/风险来源):`_llm_opinion_conclusion` 第一部分新增第八节『## 多空要点』(综述≤30字 + 利多/利空各 0~2 条,格式"利多：<因素>(逻辑：…；风险：…)",逐行纯文本禁表格),篇幅 340→360 字;`_extract_key_opinion` 双口径渲染(有该节→综述首行→利多/利空→交易行收尾(include_trade=True),无→七小节推理链兜底);新增 `_parse_duo_points`(前缀切分+半角冒号归一+markdown 表格行还原,LLM 实测真会写表格);reconclude `_NEW_FMT_MARKERS` 加第四标记『## 多空要点』;前端两处列头改"观点要点(综述→利多/利空:逻辑/风险)"。
- **测试**:test_research_module 提示词断言(八小节/360 字/多空要点格式)+ 渲染新用例(综述首/利多利空/交易行收尾/解析容错含表格);全量 **1170 passed + 1 skip**,ruff 0;web_app 已重启加载新代码。
- **存量示例重跑**:用户定仅重跑研报日期=今天的(exclude 华泰 8.30 周报 141-155),共 16 份(156 东证晨报 + 164-178 国泰君安),`--ids --skip-fresh --workers 4` 后台执行中(第一轮误含华泰周报,及时掐断未写入任何结论)。

**下一步**:批量完成→查表格形态残留并以终版提示词补跑→force 重生成今日每日总结→真机验收观点总览单元格→收尾记录。

## 2026-09-07 (下午·续) — 观点要点新口径存量示例重跑完成 + 真机验收通过

**状态**:✅ 完成(改动未 commit)

**做了什么**:
- 16 份(publish_date=2026-09-07,华泰 8.30 周报按用户要求排除)全部带『## 多空要点』;首轮 4 份(156/164/171/173)被 LLM 写成 markdown 表格,以"逐行纯文本禁表格"终版提示词补跑后全部转纯文本条目;解析器同步做了表格行还原兜底。
- web_app 重启(彻底停旧组×4,9 点三组僵尸进程一并清掉);今日每日总结 force 重生成(cached: False,逐品种置信度口径正确);真机验收 SA 单元格 = 综述→利多/利空(逻辑/风险) 结构正确。
- 全量测试 1170 passed + 1 skip,改动文件 ruff 0。

**遗留**:① 存量其余 35 份(华泰 8.30 周报等)仍为七小节旧口径——渲染有兜底,展示不劣化,用户未要求重跑;② 今晚 18:15 HTFC 新 key 首次自动触发,届时验证 htfc_complete 告警;③ 抖音平台策略挂起文档 worklog/2026-09-07-platform-factor-strategy-deferred.md。

## 2026-09-07 (下午·三) — 备份同步 + 迁移准备

**状态**:✅ 完成

**做了什么**:
- `backup_AgentSense_2026-09-07/` 增量同步(robocopy /E,不删已有):AgentSense 13 文件更新(观点要点新口径/抖音测试/douyin web 接入/worklog);tradingagents-data 刷到 210MB(DB 含 16 份新口径结论,无 wal 残留);新增 **MediaCrawler/** 154MB(排 venv 602MB,含 dy_user_data_dir 抖音登录态)与 **思路2/** 143MB(含 .git + output 全部数据)。.env 校验一致(HTFC 新 key 早间已入)。
- 抽验:备份 web_app.py 含「多空要点」11 处、reconclude 四标记、备份 DB 新口径 16/23 与源一致。
- 写 `backup_AgentSense_2026-09-07/迁移说明.md`:四目录对应目标位置、三处 venv 重建(含 playwright install chromium)、目标机专属配置清单(注册表 HTFC key/计划任务/cloudflared)、验收步骤。

**下一步**:迁移包就绪(~557MB,项目外防递归),拷贝到新机按迁移说明执行;今日 18:15 HTFC 新 key 首次定时触发待验证。

## 2026-09-07 (下午·四) — 备份打包 zip

**状态**:✅ 完成
- `project4/backup_AgentSense_2026-09-07.zip` 344MB(源 557MB,Optimal 压缩);Compress-Archive(.NET,中文文件名带 UTF-8 标记,Windows 互拷不乱码)。
- 抽验 4952 条目:思路2 2345 项、dy_user_data_dir 1130 文件、迁移说明.md/web_app.py/agentsense.db 均在(注意:Compress-Archive 条目内用 `\` 分隔符,Windows 解压无碍;跨 Linux 解压才需注意)。

## 2026-09-07 (下午·五) — zip"压缩文件夹无效"修复

**状态**:✅ 完成
- 根因:Compress-Archive 条目内用 `\` 分隔符,违反 zip 规范(PKWARE 要求 `/`),Win11 资源管理器拒开报"无效"(7-Zip/Python 可容忍);git-bash tar 是 GNU tar 不能写 zip,System32 bsdtar 无 zip writer 模块。
- 终解:Python zipfile 重打(强制 `/`、UTF-8 标记、deflate level 6,14s);`backup_AgentSense_2026-09-07.zip` 396MB,5286 条目,反斜杠条目 0,CRC 全过,迁移说明/DB/抖音登录态(1113 文件)/思路2(2368 项)齐备。
- 坑:GNU tar -a -cf x.zip 产出的是假 zip(tar 字节流),已删。

## 2026-09-08 (下午) — 方案三落地:版面感知图表重提取纳入 RAG + 运行分析"request context"崩溃修复

**状态**:✅ 完成(1600→3175 向量,评测 10/10 零回退)

**做了什么**:
- **运行分析崩溃修复**(用户反馈 ERROR: Working outside of request context):根因 = `run_analysis` 后台线程里调 `_client_tag()` 读 `request.headers`(web_app:2365,自传数据隔离功能引入),线程无请求上下文必炸,分析一启动就 mark_error。修复 = 在 `api_run_analysis` 请求阶段取好 `client_tag` 传入线程。
- **方案三·新模块** `tradingagents/dataflows/pdf_layout.py`:① web_app 的词→行版面还原逻辑整体下沉(`words_to_lines`/`_page_lines`/`extract_plain_text`,block 分组防双栏串行语义不变);② 新增 `extract_layout_text` —— `get_drawings()` 矢量矩形**扩张相交聚簇**(`_REGION_GAP=10`),簇内文字(轴/图例)与最近图题行(上/下皆可,实测两类排版并存)、资料来源行聚成`【图】`块,表N 标题后数字行聚成`【表】`块;过滤:单元素数<4 装饰线、簇面积>85% 整页装饰/巨型簇(国君封面页侧边饰条实测合并出 area%1.05+ 的巨簇,后置面积过滤兜底)。期间修了自查发现的聚簇算法丢簇 bug(合并后未放回)与题注行纵向重叠时 `min()` 空迭代崩溃。
- **接线**:database.py `layout_text` 列(建表+ALTER 迁移)+ update 白名单;`_process_research_report` 落库 layout_text(60k 上限,失败回退空);chunking.py `build_chunks` 优先 layout_text(无 20000 截断),`【图】/【表】`块豁免数字占比过滤(点线目录过滤仍生效);`scripts/reextract_layout.py` 回填(76/76 成功,32 份位图图表报告为 0 图块属预期——图内无文本可提,图题作为普通行仍在)。
- **测试**:新建 test_pdf_layout.py 10 用例(纯 tuple 几何 + pymupdf 生成真 PDF roundtrip,注意 insert_text 须 `fontname="china-s"` 否则 CJK 落点符);test_rag_chunking.py +3(layout 优先/图块豁免/回退)。全量 **1232 passed + 1 skip**,改动文件 ruff 0。
- **真机验证**:backfill --force 重建 **3175 向量/159 报告**(原 1600);停旧重启 web_app;图表主题提问"原油布伦特和WTI价格走势/内外盘价差"命中带【图】块(含 WTI-布伦特价差 10→2.8 美元等图旁注释文字);运行分析触发后 10s error=None(修复前必现)+ 已停;eval --no-judge **10/10、Recall 1.00、拒答 2/2** 零回退。

**遗留/下一步**:① Phase B 候选:图表块 LLM 重述(把【图】块轴数字转成自然语言,可再提检索质量)、黄金集扩题;② 位图图表(32 份)只能走 OCR,方案三覆盖不到;③ 改动未 commit(等指示,不带 Co-Authored-By)。

## 2026-09-08 (下午·续) — 位图图表视觉重述钩子 + 东证繁微(Fiona)接入

**状态**:✅ 完成(位图批量回填进行中;东证真机单份验证通过)

**做了什么**:
- **视觉重述钩子**(用户"好,挂吧"):核心逻辑下沉 `tradingagents/dataflows/chart_vision.py`(脚本 `chart_describe.py` 改薄 CLI 共用);web_app 加薄适配 `_vision_describe_safely(report_id, file_path, layout_text)`——挂在 `_process_research_report` 的 status=done 之后、RAG 索引之前(重述节进本次索引,不拖慢用户看到结论);describe_for_hook 全兜底(Ollama 不可达 2s 放行/图表数限 RAG_VISION_MAX_CHARTS=8/单图失败跳过/绝不抛);conftest 禁用方式=桩 `ollama_available=False`(真实钩子代码每个测试都跑、毫秒放行,不替换 web_app 函数)。test_chart_vision.py 9 用例。全量 1243 passed+1 skip。
- **东证繁微接入**(用户装 fiona-futures-expectation skill 并"按国君华泰相同的方法处理"):
  - **技能安装**:zip 解包 → `~/.claude/skills/fiona-futures-expectation/`(zip 内无 token,4 个 mcp.json 全是占位符)。
  - **MCP 注册**(user scope,坑:`~/.claude.json` 是只读的,`claude mcp add` 静默失败——chmod u+w 后重注成功):4 端点 Authorization 用 `${FIONA_MCP_TOKEN}` 环境变量展开。
  - **实测发现(重要)**:4 端点里 3 个**匿名全通**(viewpoint 观点库/futures_market_data 行情/futures_ranking 排名),`rating_prediction`(市场预期)+news/price_structure/dzlabel 要 token(401);额外探测出 **`report` 端点也匿名可用**——研报库 14357 份,report_search(日期/关键词/品种过滤)→report_get_detail(摘要全文)→report_get_url(带时效 token 的 PDF 直链,requests 可下)。skill 文档说 viewpoint 无研报能力需另接 report MCP,但没说 report 也开放匿名。
  - **新模块** `tradingagents/dataflows/dongzheng_api.py`:无状态 MCP streamable-http 客户端(JSON-RPC,SSE/裸 JSON 自适应解析,Accept 须带 text/event-stream);viewpoint(viewpoint_search_dynamics/search_views/get_enums)+report(report_search/get_detail/get_url/get_enums)。
  - **采集器** `research_collector_dongzheng.py`(仿 gtja 架构):**研报为主源**(report_search 按撰写日期增量 → PDF 下载 → 与国君完全相同的提取/版面/视觉重述/LLM/RAG 管线,report_id 前缀文件名幂等,processing 残留自愈,PDF 失败/过短降级 summary 落 .md);--dynamics 动态快评(.md 纯文本,品种 LLM 识别);--views 周期/年度观点(product_code 前缀 M.DCE→M 映射 VARIETY_METADATA)。seen 键 r{id}/d{id}/v{id}_{freq}。PARALLEL_WORKERS=2(GTJA +40 错峰,2+4≤并发上限 5)。
  - **接线**:web_app /api/research/collect source 加 "dongzheng"(requested 品种筛选时跳过,与发现报告同);scheduler.py 四源错峰注册(0/15/30/40 分钟,job 前缀 research_dz_,告警前缀 dz_*)。
  - **测试**:test_dongzheng_api.py 9 用例(假 SSE/JSON 流、token 头、错误路径)+ test_research_collector_dongzheng.py 15 用例(sys.modules 桩法,幂等/自愈/seen 键/品种映射/类型映射/摘要降级)。ruff 0。
  - **真机验证**:dry-run(研报 4 篇/日,快评 76 条,观点 168+168);单份端到端 135.6s → report_id=181 done(周报,品种 P)。**发现附件未必是 PDF**:"数据周报(英文)"类型附件是 .xlsx,已加 pdf_url 后缀前置检查避免无效下载,直接走摘要降级。
  - **token**:压缩包与端点均无;`FIONA_MCP_TOKEN` env 预留(现在匿名可用,随时可能收紧)。

**遗留/下一步**:① 位图批量(b2t5vhr1o,1048 图,15/76 进行中)→ 完成后 backfill --force 重建索引 → 停旧重启 web_app → 位图问题真机检索验证;② 东证首次全量:建议 `research_collector_dongzheng.py --days 7 --dynamics --views --max-reports 100`(观点 336 条会受单次上限约束,可分次);③ 改动未 commit(等指示,不带 Co-Authored-By)。

## 2026-09-08 (傍晚) — pytest 慢根因修复 + 位图批量砍半提速

**状态**:🔄 批量推进中(预计 ~3 小时收口)

**做了什么**:
- **pytest 20+ 分钟根因修复**:`tests/test_research_collect_route.py::_fake_collectors` 漏桩 `research_collector_dongzheng`,`source=all` 路由测试在后台线程跑**真**东证 MCP 网络+PDF 下载+LLM。补 stub(记录 `("dz", days, dry_run)`)+ `test_collect_all_runs_both_collectors` 增第四源断言。全量回归 **1267 passed + 1 skip,33 秒**恢复。
- **位图批量卡死发现与处置**:原批量(b2t5vhr1o)2.5h 零推进,日志为 pymupdf 渲染大页 PDF `malloc failed`(系统内存耗尽,与 torch/多进程叠加有关);杀掉后带 `-u` 无缓冲重启(0 字节输出是 python 缓冲假象,DB 才是真进度)。
- **用户拍板砍量**:chart_describe.py 新增 `--max-charts`(默认 0=全部;--dry-run 同步计入封顶),用户选 8(=web_app 钩子 HOOK_MAX_CHARTS 上限,口径一致)。1049→**303** 图表(-71%)。已重述的 28 份(含 >8 图的完整版)保留不重跑。
- **吞吐实测**:重启后 8 分钟完成 3 份 ≈ 22 份/小时 → 剩余 ~74 份 **预计 2.5~3.5 小时**(GPU 95% 满负荷,RTX 5060 Laptop 8G,qwen3-vl:4b)。

**遗留/下一步**:批量(buqlbvy36)完成 → `backfill_rag_index.py --force` 重建 → 彻底停旧重启 web_app → 位图问题真机检索验证 → 东证首次全量回填(见上条)。改动未 commit。

## 2026-09-08 (晚) — 位图批量收口:存量放弃,只保当日 + web_app 重启上线

**状态**:✅ 完成(用户拍板:存量 74 份旧报告的重述放弃,太耗时)

**做了什么**:
- 停掉存量批量(buqlbvy36)。核查**今日 4 份**(179/181/182/183):3 份是 .md 摘要降级(无 PDF 无位图),唯一 PDF(182 欧线集装箱周报)此前批量已重述完成——今日实际零欠账。
- `backfill_rag_index.py --force` 全量重建:**3351 向量 / 162 份 / 失败 0**(较此前 3175 净增,含位图重述节与新报告)。
- 彻底停旧(95000)重启 web_app(barrht5ky),真机核验:`/api/research/rag/status` = 3351 chunks/162 reports,首页 200。
- 至此位图视觉重述管线闭环:上传钩子(新报告,≤8 图)+ 今日已清,存量按用户决定不再回填。

**遗留/下一步**:① 东证首次全量回填:`research_collector_dongzheng.py --days 7 --dynamics --views --max-reports 100`(观点 336 条受单次上限约束可分次);② 全部改动未 commit(等指示,不带 Co-Authored-By)。

## 2026-09-08 (晚·续) — 采集→embed→图片读取自动链路真机核验

**状态**:✅ 完成(链路本已接好,本轮核验落库证据)

**做了什么**:核验 4 源采集统一走 `_process_research_report`(done 后先 `_vision_describe_safely` 位图重述、再 `_rag_index_report_safely` embed);实查 Chroma:今日 4 份(179/181/182/183)全部自动索引在库,182 共 34 chunk 其中 28 个含【图】/视觉重述节——采集进入即自动 embed+图片读取确认闭环。

## 2026-09-08 (晚·续2) — 前端模块调整:研报页调序 + 三组合并/删除(有备份)

**状态**:✅ 完成并真机验证

**做了什么**:
- **研报页调序**(用户要求):卡片顺序改为 每日总结(首) → 研报问答 → 国君周度观点 → 逐品种观点总览(末)。共享品种下拉 #res-views-variety 随末卡下移,JS 全按 getElementById 取,顺序无关;国君卡注释同步改「与下方共用」。
- **模块合并/删除**(用户指令,备份 `backup/web_template_pre_tab_merge_2026-09-08.html`):
  ① 数据更新并入情绪数据页(页尾「🔄 数据更新」小节,switchTab 懒加载加 loadUpdateStats);
  ② 历史报告并入数据仓库页(页尾「📜 历史分析报告」小节,switchTab 懒加载加 loadHistory);
  ③ 系统页整体删除(tab-system + loadSystemTab/loadSchedulerStatus/schedulerControl/loadDbStats/ackAlert/loadAlerts 六函数;后端 /api/scheduler/*、/api/db/* 保留,scheduler.py 后台照跑)。
- **导航 9→6 按钮**:运行分析/研报/情绪数据/模拟交易/数据看板/数据仓库;Ctrl 快捷键 1~7(旧 7 页含已删页,本就缺 research/warehouse)修正为 Ctrl+1~6 与导航一致;文件头索引、JS 区注释同步。
- **验证**:页面结构(6 按钮/6 tab div、合并内容在位、系统元素 0 残留、id 零重复)、div 开闭平衡与旧文件同差(+3,即模板原有惯例);全量 **1267 passed + 1 skip**(77s);web_app 已停旧重启,真机 curl 核验通过。

**遗留/下一步**:① 东证首次全量回填(`research_collector_dongzheng.py --days 7 --dynamics --views`);② 全部改动未 commit(等指示,不带 Co-Authored-By)。

## 2026-09-08 (晚·续3) — 盘面利润落地:Agent 工具 + 数据看板卡 全链路完成

**状态**:✅ 完成(测试全绿 + 真机三品种验收通过)

**做了什么**:
- **核心模块** `tradingagents/dataflows/futures_margin.py`:配方表 MARGIN_FORMULAS(RB/HC 盘面利润=品种−1.6×I−0.5×J;J 焦化利润=J−1.3×JM,行业简化配比)、`compute_margin_series`(各腿走 get_futures_price CSV 出口复用价格缓存,inner-join 对齐,任一腿缺数据→None 宁可不给)、`format_margin_text`(公式口径/最新值/腿收盘/窗口统计/历史分位+极低<10%等五档描述+哨兵文本)。涉及外盘的配方(豆粕榨利/LM 炼厂)刻意不做。
- **Agent 工具**:`@tool get_futures_margin`(commodity_futures_tools.py)→ interface.py 新分类 futures_margin + VENDOR_METHODS 注册 → 基本面分析师接入:工具清单+提示词第7节(量化链利润引用格式"盘面利润 180 元/吨,处近1年12%分位")/第8节(禁止目测价格比)/Workflow/输出要求(c)。
- **数据看板**:api_dashboard 第 5 loader `_load_margin`(ThreadPool 4→5,无配方品种 available=false+MARGIN_NO_FORMULA note);前端 `#dash-margin` 卡 + `_renderMarginSection`(统计条=最新值/历史分位着色/近5日变动/窗口最小~最大,橙色折线+tooltip 腿收盘,无配方整卡隐藏)。
- **测试**:新增 tests/test_futures_margin.py 13 用例(配方数学/日期对齐/缺腿降级/分位wow/文本哨兵/工具注册)+ test_dashboard_route.py 补 3 用例(透传/无配方/缺腿);ruff 全 0;全量 **1282 passed + 1 环境跳过**。
- **真机验收**:web_app 重启后 RB=890.05 元/吨(64 点,分位 15.6%@90日窗)、J=−4.85(分位 9.4% 极低)、CU=MARGIN_NO_FORMULA 优雅降级;前端模板含 margin 卡代码。

**下一步**:
- 改动未提交(等用户指令,不带 Co-Author 页脚);东证首次全量回填仍待跑;分析师真实 run 观察一次 get_futures_margin 调用效果(可选)。

**留档**: 同日提交 `64ab2a6`(52 文件,含 RAG/视觉重述/盘面利润/东证采集器/前端重构全部改动;已扫无 token;临时脚本 _restart_when_idle.sh 未入库)。
