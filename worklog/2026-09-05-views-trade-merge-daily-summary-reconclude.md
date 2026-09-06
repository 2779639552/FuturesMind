# 观点总览列合并「头寸」+ 每日总结全品种覆盖 + 国君周度观点迁移 + 存量结论重跑 102/102(完成留档)

> **留档时间**:2026-09-05 晨,任务已全部完成并验收。work-journal.md 已有三条过程记录(01:35 / 03:00 / 03:40 + 晨间收口),本文为该轮工作的**技术细节终档**,供日后回溯"为什么这么改/坑在哪"。
> **状态**:全部改动已在真机生效,但**未 commit**(等用户指示,不带 Co-Authored-By)。

## 一、观点总览三列并一列「头寸」(用户拍板:单边/头寸/研报建议语义重复)

- **行字段**:删 `trade_range` / `trade_position` / `report_advice` 三键,新增 `trade` = `_merge_trade_cell(te, report_advice)`。
- **`_merge_trade_cell(te, report_advice)`**(web_app.py):
  - 源顺序:`形态与区间` → `头寸`(回退`头寸范围`)→ `研报建议原句`;
  - `re.split(r"[;；,，、\n]")` 拆片段、strip 尾标点;「—」「未披露」片段直接出局;
  - 双向整含去重(len≥2,保留信息量大的一方);`; ` 连接。
  - 期望形态:`"单边看多; 运行区间 540~560; 轻仓"`。
- **`_extract_key_opinion(..., include_trade=False)`**:新增参数,默认 True(每日总结不受影响);观点总览传 False,**不再带交易要素行**(交易信息已单独成列,防同格重复)。
- **前端**(web_template.html):观点总览表 3 列→1 列「头寸」(`txtCell(rp.trade, 220)`);导出 `_buildViewsExport` 同步(colgroup 9%:19%:6%:7%:14%:9%:36%)。
- **注意**:`_parse_trade_elements` 靠正则 `##\s*交易要素[^\n]*\n([^\n]+)` 取节,交易行**必须**带「## 交易要素与风险」标题头,否则解析为空 → 该行头寸列显「—」(存量 #85 曾踩,补跑修复)。

## 二、结论提示词去重(1b 条)

`_llm_opinion_conclusion` prompt 新增 1b:小节互不重复、观点与依据用短语指代不复述数字(推理链式:`因A+B → 推演D → 方向`)。**只对新结论生效**——存量消除靠本轮 102 条全量重跑,已抽查 #22/#97 确认生效。

## 三、每日总结第二节逐品种全覆盖(总表保持原格式)

- **只改第二节**:「观点对比与冲突」覆盖材料**全部品种**逐条(≤40 字,无分歧也保留);第一节总表保持全品种一行一品种、同品种多家合并「/」逐份对应、同机构多份发行方只写一次。
- 提示词硬约束:总表「全品种列出,严禁『其余从略』」+ 第二节「覆盖材料全部品种,不受总表取舍限制」双保险;总篇幅放宽到 **1800 字左右**。
- 真机复验(09-05 重生成后):09-02 总表 44 行/第二节 55 条,09-04 总表 42 行/52 条,**无「从略/其余省略」残留**。
- 坑(已知,勿回退):每日总结按日期缓存于 `~/.tradingagents/research_daily/{date}.md`,**改提示词后必须 force**(`POST /api/research/daily/generate {date, force:true}`)才有修改痕迹。

## 四、国君周度观点迁移 queryByCode → .query(修复"一直返回无")

**根因**:原 `queryByCode` 端点只回**最新一帧**,而 GTJA 提前建下期帧(如 09-06)且逐品种补内容,最新帧多数品种为空 → 全查空。

- gtja_api.py:`EP_VIEW_WEEKLY = "commodity.weekly.viewpoint.query"`(列表端点);
  `fetch_viewpoint` 重写:窗口 `today-28d ~ today+7d`、`page=1/size=1000/startReportDate/endReportDate`;**服务端忽略 code 过滤(与研报接口同契约),客户端按 code 大小写不敏感筛**;`max(mine, key=reportDate)` 取最新帧;score 字符串→int;无帧 → `error="近 4 周无 {code} 周度观点帧"`。
- 测试:test_gtja_api.py 新增 picks_latest_frame / no_frame 两用例(桩 `_request`,不触网)。

## 五、reconclude 路由 + OOM 下的批量重跑架构(可复用)

- **新路由** `POST /api/research/reconclude`(web_app.py):`{"ids":[...]}` 白名单数组,单批 ≤10,逐条调 `reconclude_research_report(int(i))`;LLM 在 web_app 进程线程内执行。
- **为什么走 HTTP 而不是脚本**:本轮系统低内存 5 次杀后台 Python(TinyDL64 等 1GB 级任务在跑,Memory Compression ~975MB)。curl 驱动 = 只有一个 web_app 进程;驱动 bash 用 PowerShell `Start-Process` 分离启动,脱离 harness 的 OOM 杀循环。
- **可复用流程**(今后批量/补跑失败占位):
  1. `/tmp/rids.txt` 存 id 清单;
  2. `comm` 剔除已完成(注意 CRLF,先 `tr -d '\r'`)→ rids_rest.txt;
  3. bash 循环:每批 3 条并发 `curl -m 900 -X POST .../reconclude -d '{"ids":[...]}'` → progress.log;
  4. 断点续跑按 id 幂等(upsert 覆盖);
  5. 完成后 `get_db().list_research_reports(limit=2000)` 扫 status=done 且 conclusion_md 含「观点生成失败」= 占位清单,再走同路由补跑。
- **坑**:
  - 驱动脚本 grep 判成引用 `"ok": true`(带空格)匹配不到紧凑响应 `"ok":true` → 成功全被记 FAIL 标签;无害,监控改用 `grep -c '"ok":true'`;
  - Git Bash `/tmp` 是 MSYS 路径,venv 的 Windows Python 读不到,须 `cygpath -w /tmp` 转换;
  - 81/82 等早期轮次完成的 id 不在新日志里,终验必须以 DB 为准,不能只数日志。

## 六、终验结论(2026-09-05 08:4x)

| 项 | 结果 |
|---|---|
| 102 行 conclusion_md 含「## 交易要素与风险」 | 102/102(#85 定点补跑 1 次后) |
| 全库 status=done 失败占位 | 0 |
| 新结论 1b 去重抽查(#22/#97) | 推理链生效,小节不复述数字 |
| 每日总结 09-02 / 09-04 force 重生成 | 完成,全品种覆盖无省略 |
| 国君周度观点 | .query 端点迁移 + 两新测试 |
| 测试 | 全量 1112 passed;ruff 改动文件 0 |

## 七、未 commit 清单(等指示)

- web_app.py:`_extract_key_opinion(include_trade)`、`_merge_trade_cell`、`_research_view_row`、1b prompt、每日总结 prompt、`/api/research/reconclude` 路由
- web_template.html:观点总览/导出列合并、表头文案
- tradingagents/dataflows/gtja_api.py:fetch_viewpoint 迁移
- tests/test_research_module.py、tests/test_gtja_api.py:对应更新/新增

关联:work-journal 2026-09-05 三条过程记录;`worklog/2026-09-03-research-trade-conclusion-dashboard-plan.md`(上一轮交易化定稿计划,本轮是其二次演进)。
