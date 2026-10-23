# 逐模块审核 · 2026-10-23

> 承接「未来数据污染」审计，把检查从单一模式扩展到**六类口径/边界问题**，
> 逐模块过一遍。判定原则：**只抓客观缺失**（量纲、分母、时点、单位、空值），
> 不评判模型输出的观点对错。

## 一、检查维度

| # | 维度 | 典型错误 |
|---|---|---|
| A | 时间边界 | 用了截止日之后的数据；交易日当自然日 |
| B | 单位 / 量纲 | 小数当百分数；元/吨 标到库存上 |
| C | 分母 / 口径 | 同一「基差率」两处分母不同 |
| D | 缺失语义 | 0 与「没数据」混用；空串补默认值 |
| E | 并发 / 状态 | 非原子写导致读到半截 |
| F | 计算边界 | 除零、空集均值、NaN 参与比较 |

## 二、逐模块结果

| 模块 | 检查重点 | 结论 |
|---|---|---|
| `tradingagents/dataflows/commodity_futures.py` | A 时间边界、F 计算边界 | **发现 2 处**（`get_verified_quote` 窗口、`get_futures_supply_demand` 宏观指数），均已修 |
| `tradingagents/dataflows/analysis_bundle.py` | B 单位、C 口径 | **发现 2 处**（库存继承基差单位「元/吨」；库存无单位），均已修 |
| `quant_module.py` | C 口径 | **发现 1 处**（`basis_rate` 小数 + 期货分母，与 `analysis_bundle` 的百分数 + 现货分母并存），已加 `basis_rate_pct` 与量纲说明 |
| `fund_module.py` | C 口径 | 内部按**小数**用（`bs/price`、阈值同量纲），**自洽**；与 quant 一致，与 analysis_bundle 不同 |
| `analyst_views.py` | A 时间边界 | **架构问题**：三个视图无 `date` 参数，永远取最新（详见第三节） |
| `signal_analyzer.py` | A 时间边界、F 计算边界 | 无 `shift(-)` / `future_return`，回测无前视偏差；`latest_trading_signal` 取最新是**有意设计** |
| `tradingagents/dataflows/fred.py` | A 时间边界 | 安全：`end_dt` 明确过滤，docstring 写明不含后续观测 |
| `tradingagents/dataflows/market_data_validator.py` | A 时间边界 | 安全：`_verified_rows` 有 `df["Date"] <= curr_date` |
| 采集器（wechat / oilchem / warrant / research） | E 并发 | 非原子写已在 2026-10-23 上午修完并加护栏；本轮无新增 |
| `web_app.py` / `scheduler.py` | B、D | 参数校验齐全；三态（ok / 无覆盖 / 失败）在注入层统一 |
| 前端 `web_template.html` | 布局 | 版式错乱（`:has(> h3)` 命中正文）已在 10-23 上午修完并加渲染级护栏 |

## 三、本轮新发现（3 个，均已修）

### ① 库存卡继承基差单位
`analysis_bundle.build_cards` 里 `unit=ind.get("unit") or unit` —— `unit` 是**基差**的单位。
国君库存指标的 `unit` 字段是**空的**，于是社会库存被标成「1,385,241 **元/吨**」。
库存不可能是价格单位。改为 `or "吨"`（后进一步改成量级推断，见下）。

### ② 国君库存不提供单位，各品种差一个数量级

| 品种 | 原值 | 应该是 | 判断依据 |
|---|---|---|---|
| RB | 522.16 | **万吨** | 历史峰值 2,176.89（万吨） |
| TA | 1,385,241 | **吨** | 历史峰值 > 10,000 |
| LC | 134,372 | **吨** | 同上 |

国君 `inventoryValue` 数据集的 `unit` 恒为空。新增 `_infer_inventory_unit()`，
**按历史峰值**（不是当前值）判量级：峰值 < 10,000 → 万吨，否则 吨。
卡片 `note` 与文本块都标明「单位按历史峰值推断」——不能让它看起来像数据源给的确定信息。

文本块同步带上单位：`◆ 社会库存 522.2 万吨 | 5日 -17.8% | 分位 9%`。

### ③ 「基差率」有三套形态，其中两处分母不同

| 位置 | TA 实测 | 量纲 | 分母 |
|---|---|---|---|
| `quant_module._fetch_basis().basis_rate` | 0.0914 | **小数** | **期货价**（国君 `dom_basis_rate` 原值） |
| `quant_module._fetch_basis().basis_rate_pct`（新增） | 9.14 | 百分数 | 期货价 |
| `analysis_bundle` 卡片 `gtja.basis.rate_spot` | 8.37 | 百分数 | **现货价**（自算 `basis/spot*100`） |

- 把 0.0914 当百分数 → 差 100 倍；
- 把 9.14 当现货分母 → 差 0.77 个百分点。

两者都是**没有异常、只是数错**的静默错误。

处理：`_fetch_basis` 保持接口原值（兼容 `fund_module` 的小数约定），新增
`basis_rate_pct`（百分数、期货分母），并把量纲/分母说明写进 docstring；
转换逻辑抽成 `_basis_row()` 纯函数，使**不联网也能测**。

## 四、架构问题（未修）

`analyst_views.technical_view / fundamental_view / reconcile_view` 只有 `symbol`、没有 `date`，
内部 `_daily_bars_cached(sym, 250)` 永远取最新 250 根。

**影响**：`run_analysis(date=2026-10-22)` 时报告标题写「截至 10-22」，但技术视图与三轴加权分
用的是**今天**的数据。当天分析无影响，**回看历史会串**。

**修法**：把 `date` 一路传到 `_build_payload` → `_daily_bars_cached(code, days, end_date)`，
磁盘缓存按 `(code, days, end_date)` 分键（否则今天的缓存会被「截至昨天」的请求复用）。
牵动 5 个函数 + 缓存键设计，建议独立排期。

## 五、检查方法沉淀

**扫不出问题不等于没问题。** 本轮 3 个发现里，前两个是"跑真实数据看输出"发现的
（谁会想到去核对库存的单位？），第三个是"同一指标跨模块对比"发现的 ——
都不是静态扫描能抓到的。

所以往后每加一个取数/展示字段，三问：

1. **它的单位是什么**？数据源给的单位字段可信吗（国君库存恒为空）？
2. **同一概念在别处叫什么、是什么量纲**？（基差率三套形态）
3. **它对应哪个时点**？（截止日 / 最新 / 数据本身滞后几天）
