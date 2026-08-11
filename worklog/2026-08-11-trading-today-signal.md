# 新增:模拟交易各策略"今日操作"信号(2026-08-11)

> 一句话结论:「模拟交易」tab 每个策略(单品种)现在会显示一条**今日操作**:
> 在数据最新交易日(prices[-1]["date"])该策略会 买入/卖出/不动。
> 与用户选的回测日期窗口无关。8 个策略全部接入,578 测试全绿,ruff 全绿,真机冒烟验证通过。

## 需求与语义(用户确认)

- 在模拟交易模块下各策略中,添加一条"在今天时该策略的操作"(买入/卖出/不动)。
- **"今天"** = 该品种 price 数据最后一条记录的日期(`prices[-1]["date"]`),
  与用户选的 start_date / end_date / 系统日期无关。
- **展示粒度** = 仅限单品种;品种下拉为「全部品种」(`variety=""`)时跳过不显示。

## 实现:新增独立信号函数,8 个回测函数零改动

### `signal_analyzer.py` — `latest_trading_signal()`(:2582 前,run_trailing 之后)

- 新增两个小助手:
  - `_build_forward_filled_sent_map(sent_data, price_dates)`:按价格日期向前填充情绪
    (对 fixed/trailing/compare 的今日信号是有意改进——回测是精确日期对齐,today 需要情绪填充到该日)。
  - `_make_signal(variety, date, action, strength, reason)`:构建信号 dict。
- 返回 `None`:variety 空 / 无价格 / 数据不足(各策略回看窗不同)。
- 返回结构:`{"today_signal": <主信号>, "today_signals": {子键: 信号, ...}}`。
  - 单策略(fixed/trailing/momentum/donchian):today_signals 为空 dict。
  - contrarian/adaptive_sent → `{主, consensus}`;momentum_ad → `{adaptive, momentum_baseline}`;
    compare → `{fund_only, fund_plus_sentiment}`。today_signal = today_signals[主键]。
- 每个信号含 `strength`(各策略原生幅度,量纲不同不可跨策略比较)与 `strength_pct`
  (**归一化到 [0,1] 的跨策略可比强度**):情绪/基本面得分 scale=1.0(得分本身 ≈ [-1,1]);
  动量收益/唐奇安突破幅度 scale=0.05(回看累计 5% 视为极强),截断到 1.0。
- 情绪加载器按各策略原样:`fixed/trailing`→`_load_sentiment`;
  `contrarian/adaptive_sent/momentum_ad`→`_load_trends or _load_sentiment`;`compare`→`_load_trends`。
- 各策略判定规则逐一镜像回测逻辑(见下表)。

| 策略 | 今日信号规则(镜像回测) |
|---|---|
| fixed | `ss>+threshold`→buy / `<-threshold`→sell / 否则 hold |
| trailing | 与 fixed 同(入场规则一致) |
| momentum | `ret=closes[-1] vs closes[-1-lookback]`,`ret>0.005`→buy / `<-0.005`→sell / hold |
| donchian | `close>前period最高`→buy / `<前period最低`→sell / hold |
| contrarian | 主:趋势跌+情绪看多→buy,反之 sell;子 consensus 顺势动量式 |
| adaptive_sent | ①diverge_bearish→sell ②diverge_bullish→(trend_pct>3?hold:buy) ③⑤trend_pct≥1 动量式 ④<1 逆向式 |
| momentum_ad | ①diverge_bearish→sell ②diverge_bullish→(baseline_is_good?mom_sig:trend_pct>3?hold:buy) ③⑤按 mom_sig ④baseline_is_good→mom_sig ⑤否则 ss<-0.1→buy/ss>0.1→sell;子 momentum_baseline=mom_sig |
| compare | fund_only 纯阈值;fund_plus_sentiment 需情绪同向 |

- **momentum_ad 运行态依赖**:`baseline_is_good` 用 O(n) 纯动量基线重放复刻
  (仅维护 pos/entry/最近 10 笔已平仓 pnl>0.15 记胜),得到与回测一致的 `len>=5 and sum>0 and wr>=0.45`。

### `web_app.py` — 8 个 `/api/trading/*` 端点

- import 加 `latest_trading_signal`。
- 每端点 `return jsonify(result)` 前合并 `result["today_signal"] = latest_trading_signal(...)`;
  有多子策略时另合并 `today_signals`。
- `variety=""` → 函数返回 None → JSON `today_signal: null`,天然满足"全部品种跳过"。
- 端点→策略映射:run→fixed;trailing→trailing;contrarian→contrarian;adaptive_sentiment→adaptive_sent;
  momentum_strat→momentum;momentum_adaptive→momentum_ad;donchian→donchian;compare→compare。

### `web_template.html` — 前端渲染

- `runTradingBacktest()`:摘要追加"今日操作"行(**用下拉值 `v` 门控**——compare 会把空品种强制成 RB,
  必须按前端下拉判断才能落实"全部品种跳过");子策略卡循环渲染子信号行。
- 边界补强:回测 0 交易时(:2315 提前 return 分支)仍显示今日操作,否则原样显示 message。
- `runMultiCompare()`:逐策略存 `allToday[s] = v ? d.today_signal : null`,统计卡 + 摘要 chips 渲染。
- **强度条**:新增 App 方法 `fmtStrengthPct(ts)`,按 `strength_pct` 渲染 36px 小进度条 + 百分比
  (颜色随 action:买绿/卖红/不动灰),用于主今日行、子策略行、多策略统计卡与摘要 chip。
- 视觉约定:buy→`var(--green)`(买入);sell→`var(--red)`(卖出);hold→`var(--text-muted)`(不动)。

### 测试 — `tests/test_trading_today_signal.py`(13 个,unit)

覆盖:fixed/trailing 三态、momentum 涨跌平、donchian 突破、contrarian 背离、adaptive_sent 决策、
momentum_ad 子键、compare fund+combo、空品种→None、无价格→None、数据不足→None、多子策略主键匹配、
**strength_pct 归一化**(情绪分 0.8→pct 0.8;动量 >5%→封顶 1.0;唐奇安突破 0<pct≤1)。

### 测试 — `tests/test_trading_today_signal.py`(12 个,unit)

覆盖:fixed/trailing 三态、momentum 涨跌平、donchian 突破、contrarian 背离、adaptive_sent 决策、
momentum_ad 子键、compare fund+combo、空品种→None、无价格→None、数据不足→None、多子策略主键匹配。

## 验证(全绿)

- 单测:13 今日信号 + 5 日期回归 = 18 passed。
- 全量:**579 passed / 1 skipped / 1 deselected**(566 原 + 13 新增,零回归)。
- ruff check + format 全绿(三个文件)。
- 前端 JS:完整 `<script>` 块(94KB)`node --check` 通过。
- 真机 curl 冒烟(重启服务器后,8 端点):
  - PP:fixed/trailing→buy;momentum/momentum_ad→sell;contrarian/adaptive/donchian/compare→hold;
    全部含 `today_signal`(date/action/strength/strength_pct/reason)。
  - strength_pct 归一化实测:情绪类 0.50、基本面 0.787、动量 0.7% 收益→0.148——同一 [0,1] 标尺。
  - 空品种 → `today_signal: null`。
  - RB 完整响应:保留全部原字段(PnL/curves/decisions)+ today_signal,零回归。
  - PP compare 回测因趋势情绪 <20 序列返回 error,但 today_signal 仍工作(仅需 forward-fill 情绪)。

## 使用方式

「模拟交易」tab 选单品种 → 点任意策略「运行回测」或「多策略对比」,结果顶部/摘要会出现
"今日操作 买入/卖出/不动 @ 数据最新日期",表示该策略在数据最新交易日的即时信号。
旁边是归一化强度条(0–100%,跨策略可比):情绪/基本面分直接映射,动量/突破按 5% 封顶;
原生的 `strength` 保留在后端字段中。选「全部品种」时不显示(设计如此)。

## 环境备忘

- 推送需 Clash 代理:`git -c http.proxy=http://127.0.0.1:7890 push origin main`
- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(578 passed)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚
