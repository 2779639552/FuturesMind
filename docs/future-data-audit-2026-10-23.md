# 未来数据污染审计 · 2026-10-23

> 起因：LC 技术报告里 `get_verified_quote` 的 SMA20 与其他工具差 2,981 点。
> 查证后确认**数据源完全一致**（逐日对比 19 个交易日差值全为 0），差异来自
> 「收了截止日，却取序列尾」。据此对全仓做了一次同类模式排查。

## 一、判据

「未来数据污染」= 拿某一天的分析，用了那一天**之后**才产生的数据。典型形态：

| 形态 | 代码特征 | 后果 |
|---|---|---|
| ① 取序列尾 | 函数收了 `end_date` / `date`，体内用 `[-N:]` / `.tail()` / `.iloc[-1]`，**不按该日期截断** | 指标含未来值 |
| ② 缓存越界 | 缓存键不含日期，命中时只校验「覆盖到 end_date」，不校验「是否超出」 | 历史请求拿到最新数据 |
| ③ 负向位移 | `shift(-1)` / `future_return` | 量化前视偏差 |

## 二、扫描结果

用 AST 扫「有日期类参数（date/end_date/trade_date/as_of/curr_date/target_date）+ 体内取序列尾」的
函数，命中 **12 个**。逐个核对后分三类：

### A. 真问题（2 个，均已修）

| 函数 | 问题 | 修复 |
|---|---|---|
| `commodity_futures.get_verified_quote` | 窗口是 `[target-30, target+5]`，`all_closes[-20:]` 取的是「最新 20 根」而不是「截至目标日 20 根」；且 35 天窗口遇长假不足 20 个交易日，分母会变小 | 窗口 30→**60 天**；记 `target_idx`，`all_closes` **截断到目标日** |
| `commodity_futures.get_futures_supply_demand` | 建筑业指数 `ci.iloc[-1]`、房地产景气指数 `re.iloc[-1]` 直接取最新，**完全忽略 `end_date`** | 新增 `_clip_series_to_end()`，两处按 `end_date` 裁剪（日期解析失败的行保留，宁可多给不可丢数据） |

**验证**：修复后 `verified SMA20 = 113,362.00`，`indicators = 113,362.0` —— **完全一致**。

### B. 架构问题（1 个，未修）

| 位置 | 问题 |
|---|---|
| `analyst_views.technical_view / fundamental_view / reconcile_view` | 三个视图**只有 `symbol` 参数，没有 `date`**。它们内部走 `_compute(sym)` → `_daily_bars_cached(sym, 250)` → **永远取最新 250 根** |

**影响**：`/api/run_analysis` 传入 `date=2026-10-22` 时，报告标题写「截至 10-22」，
但技术视图/三轴加权分实际用的是**今天（10-23）**的数据。当天分析无影响，**回看历史日期会串**。

**为什么没修**：不是加一个参数的事 —— 要把 `date` 一路传到 `_build_payload` →
`_daily_bars_cached(code, days, end_date)`，并让磁盘缓存按 `(code, days, end_date)` 分键。
牵动 5 个函数 + 磁盘缓存键设计 + 前端调用点，属于独立改造，需要单独排期。

**缓解措施**：报告头部已写「数据截至：基差 10-21 · 仓单 10-21 · 库存 10-09 · 利润 10-20」，
但那是**注入块**的时点，不含技术视图。建议先在前端对「分析日期 ≠ 今天」的情况加一条提示。

### C. 误报（9 个）

| 函数 | 尾部取值 | 为什么不算 |
|---|---|---|
| `market_data_validator.build_verified_market_snapshot` | `df.iloc[-1]` | `_verified_rows()` 里有 `df[df["Date"] <= curr_date]` 明确截断，且输出注明「Rows after the requested analysis date are excluded」 |
| `fred.get_macro_data` | `[-1]` | docstring 明确 "no later observations are included"，`end_dt` 用于过滤 |
| `signal_analyzer._make_signal` | 无 | 只是拼 dict，不取数 |
| `signal_analyzer.latest_trading_signal` | `[-1]` | 有意为之：docstring 写明「按数据最新交易日（而非用户选择的 end_date）」 |
| `user_signals.build_signals` | `real[-1]` | `as_of_date` 只用于算 `stale_days`（数据滞后天数），不是截断；`NaT` 已被 `max(0, ...)` 兜住 |
| `evolution_memory.store_prediction` | `[-20:]` | 保留最近 20 条**记录**，不是取数 |
| `trading_graph._run_graph` | `messages[-1]` | 消息列表 |
| `warrant/dce.py collect` | `[-1]` | 抓取结果 |
| `research_collector_*` ×3、`user_data._tail_data_rows` | 无 | 无尾部取值（`date_col` 是列名，不是日期值） |

## 三、另外两项检查

**② 缓存越界** —— `get_futures_price` 安全：
- 缓存命中时用 `_cached_covers_end(df, end_date)` 判断是否覆盖；
- 命中后仍然执行 `mask = (df["date"] >= start_dt) & (df["date"] <= end_dt)` 再裁剪。

所以「缓存比请求日期新」不会泄漏 —— **`get_verified_quote` 那个 bug 是它自己拉的窗口太宽，
不是 `get_futures_price` 的问题**。

**③ 负向位移** —— 全仓 `grep "shift(-"` / `future_return` / `next_close`：**0 命中**，回测无前视偏差。

## 四、结论

- 全仓**没有第二处**「取序列尾」式的取数污染（除已修的两处）；
- 真正需要排期的是 **B 类架构问题**：量化视图没有日期参数，回看历史时会用最新数据；
- 这类问题的共同特征是 **`写方不报错、坏在读数方`**，只能靠审计和护栏发现。

## 五、可复用的检查清单

以后新增取数函数，自查三条：

1. 函数收了截止日 → 体内每一处 `[-N:]` / `.tail()` / `.iloc[-1]` 都要问「这是截至该日的吗」；
2. 缓存命中 → 除了「够不够新」，还要「有没有超出」；
3. 裁剪失败（日期列解析不了）→ **保留原行**，不要静默清空。
