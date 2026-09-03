
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
