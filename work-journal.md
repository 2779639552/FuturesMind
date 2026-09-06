
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
