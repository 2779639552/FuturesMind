# 修复:模拟交易回测日期选择不生效(2026-08-11)

> 一句话结论:「模拟交易」tab 的「开始日期/结束日期」原本完全不影响任何策略——
> 三层断链(前端不传、后端不接、策略函数无 end_date)。已完整修复三层,
> 566 测试全绿,ruff 全绿,真机 curl 冒烟验证日期窗口改变结果。

## 需求

- 用户在「模拟回测」(tab-trading,即"模拟交易")改开始/结束日期,期望影响各策略回测区间。
- 现状:怎么改日期,策略结果都不变。

## 关键发现(三层断链)

1. **前端 `runTradingBacktest`(`web_template.html:2289`)**:
   6 个策略的请求体只传 variety/horizon/threshold,**完全没有 start_date/end_date 字段**,
   `#trade-start`/`#trade-end` 输入框从未被读取。
2. **前端 `runMultiCompare`(`web_template.html:2586`)**:
   读了 `startDate`/`endDate`,但逐策略 POST 的 body 里一个日期字段都没带——读了等于白读。
3. **后端 8 个 `/api/trading/*` 端点(`web_app.py:1694-1966`)**:
   除 `api_trading_multi`(且前端未调用它)外,全部只解析 variety/horizon/threshold,
   从不读 `data.get("start_date"/"end_date")`。
4. **核心策略函数(`signal_analyzer.py`)**:
   8 个策略函数(run_simulated_trading / run_contrarian_sentiment / run_adaptive_sentiment /
   run_donchian_strategy / run_momentum_strategy / run_momentum_adaptive /
   run_trailing_strategy / run_strategy_comparison)**只有 `start_date="2025-01-01"`,
   全文件 grep `end_date` → 0 处命中**。日期过滤只做 `d >= start_date`,结束日期概念不存在。

**结果**:策略内部永远按 2025-01-01 到数据尽头运行,改任何日期都无效。

## 改动(三层联动)

### Layer 1 — `signal_analyzer.py`(8 个策略函数)
- 全部函数签名增加 `end_date: str = ""`(空串=不限,向后兼容 CLI/旧调用)。
- 日期过滤从 `d >= start_date` / `if d < start_date: continue` 改为
  `d >= start_date and (not end_date or d <= end_date)` / `if d < start_date or (end_date and d > end_date)`。
- `run_strategy_comparison` 原本连 start_date 都没有,一并补齐两个参数,在"价格×情绪合并"处过滤。

### Layer 2 — `web_app.py`(8 个端点 + multi)
- run/contrarian/adaptive_sentiment/momentum_strat/momentum_adaptive/donchian/trailing/compare
  全部解析 `start_date`(默认 2025-01-01)/`end_date`(默认 "")并透传给策略函数。
- `api_trading_multi` 内层对 trailing/adaptive_sent/contrarian/momentum_ad 的调用
  此前漏传日期,已补上 start_date/end_date(该端点本来就解析日期)。

### Layer 3 — `web_template.html`(2 个前端函数)
- `runTradingBacktest`:读取 `#trade-start`/`#trade-end`,用 `Object.assign(body, D)` 给 6 个策略请求体加日期。
- `runMultiCompare`:把已读的 startDate/endDate 放进每个策略请求体。
- 旧的 `_old_runMultiCompare` 本就调 multi_compare 且带日期,未动。

### 测试
- 新增 `tests/test_trading_backtest_dates.py`(5 个测试):mock 价格/情绪数据(60 天),
  验证 fixed / momentum / trailing / compare 各策略在收窄日期窗口后交易数严格减少、
  且所有交易 entry 落在窗口内。

## 验证(全绿)

- 全量测试:**566 passed / 1 skipped / 1 deselected**(561 原 + 5 新增,零回归)
- ruff check:全绿;ruff format:signal_analyzer.py / web_app.py / 测试文件均已格式化
- 真机 curl 冒烟(重启服务器后):

| 策略 | 日期窗口 | 结果 |
|---|---|---|
| momentum_strat RB | 2025-01-01→2026-07-22 | trades=45,首入场 2025-11-25 |
| momentum_strat RB | 2025-01-01→2025-06-30 | trades=0(该窗口无数据) |
| momentum_strat RB | 2026-01-01→2026-07-22 | trades=38,首入场 2026-01-05 |
| trading/run RB | 2026-01-01→2026-07-22 | trades=8,全部 entry 在窗口内 ✅ |

日期截断与平移均真实生效。

## 使用方式

「模拟交易」tab 修改「开始日期/结束日期」后点「运行回测」或「多策略对比」,
所有策略回测区间随之变化(此前完全无效)。

## 环境备忘

- 推送需 Clash 代理:`git -c http.proxy=http://127.0.0.1:7890 push origin main`
- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(566 passed)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚
