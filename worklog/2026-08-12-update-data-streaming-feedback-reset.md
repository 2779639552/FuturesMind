# update_data 采集流式子进度 + 讨论进化换品种刷新修复(2026-08-12)

> 一句话结论:修复两个前端观感 bug —— ① `/api/update_data` 采集阶段被
> `subprocess.run` 阻塞,前端"1/7 采集数据"几十秒无输出像卡死;② 讨论进化对话框
> 在换品种分析时不清空,残留上一个品种的辩论内容。前端无需改(本就渲染 log 事件)。
> 全量 `pytest -m "not integration"` = **625 passed / 1 skipped**,与基准一致。

## Bug 1:采集阶段卡在"1/7"(`web_app.py` `/api/update_data` collect 分支)

**根因**:collect 用 `subprocess.run(batch_collect.py, timeout=600)` 同步阻塞整条 SSE
生成器,期间前端收不到任何消息(一个关键词几十秒,总耗时数分钟),看起来像死机。

**修复**:
- 采集子进程放进**后台线程**,主生成器只轮询推进度;
- 每 4 秒对比一次 `THINK2_OUTPUT` 下的 `batch_*.jsonl`(一个关键词 = 一个批次文件),
  **批次计数变化才上报**(避免同一文件重复上报刷屏):
  `[采集中] 已完成 N 个关键词批次,最新文件 xxx (M 条)`;
- 计数不变且超过 15 秒推一条心跳 `[采集中] 关键词批处理进行中,已等待 Xs (完成批次: N)`;
- 子进程结束(含 600 秒超时转异常)后照旧推送 stdout/stderr 关键行。

**验证**:py_compile 通过;模拟脚本分别验证「计数上报(1→2→3 各一次)」与「心跳(15s 触发)」
两个分支;真机:服务器重启后 `/api/update_data` 采集阶段实时滚进度。

## Bug 2:讨论进化换品种不刷新(`web_template.html`)

**根因**:`_feedbackHistory` 是全局 App 状态,换品种再分析时**从不清空**。

**修复**:
- 新增 `App._resetFeedback()`,清空 `_feedbackHistory` 与 `#feedback-msgs`;
- `#sym` 下拉 onchange 立即调用 → 品种一换,旧讨论当场消失;
- `runAnalysis()` 加保险:仅当新品种与 `_feedbackSymbol` 不同才清空,**同一品种重复分析
  保留已有讨论**(避免每次重跑都丢上下文)。

**验证**:`node --check` 通过;curl 确认线上页面包含 `_resetFeedback`(web_template.html
每次请求重读,前端改动即时生效,无需重启)。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `web_app.py` | collect 分支改为后台线程 + 轮询上报(计数 + 心跳) |
| `web_template.html` | 新增 `_resetFeedback()`;`#sym` onchange 与 `runAnalysis()` 接入 |

## 环境备忘

- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(625 passed / 1 skipped)
- 服务器:端口 5000,`venv\Scripts\python.exe` 是启动器,会派生 `C:\Program Files\Python312\python.exe` 子进程;重启要 kill 启动器(子进程随它一起死),再 `Start-Process`。
- 本次提交为**整批会话改动**(29 个注释批次文件 + QA 去重 + 本两个修复 + 学习文档 worklog)一次提交;GitHub 推送需 Clash 代理 `git -c http.proxy=http://127.0.0.1:7890 push origin main`。
- commit 不带 "Co-Authored-By: Claude" 页脚。
