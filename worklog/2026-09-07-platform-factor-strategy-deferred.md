# 挂起:按平台数据特点构造情绪策略(2026-09-07,数据不足暂缓)

> 状态:**挂起**。触发条件见"验证门槛"。本文档保留全部已探明的结论与实现路径,
> 届时按此文档直接开工,无需重新探索。

## 1. 动机与现状

平台回测(思路2/validate/backtest_weights.py,全局池化,前向收益配对)显示平台间
预测特性分化明显:

| 平台 | 方向准确率 | pearson r | 样本 n | softmax 权重 |
|---|---|---|---|---|
| 雪球 | 52.9% | +0.088 | 136 | 0.1904 |
| 东财股吧 | 51.4% | +0.132 | 280 | 0.1879 |
| 微博 | 51.4% | +0.061 | 551 | 0.1772 |
| 小红书 | 48.9% | -0.034 | 217 | 0.1600 |
| 知乎 | 45.1% | -0.149 | 91 | 0.1554 |
| **抖音** | **37.5%** | **-0.220** | **24** | 0.1292 |

抖音是唯一显著低于 50% 的平台(散户评论区,反向指标特征),与"散户一致性看多≈见顶"
假设吻合。**但 n=24 不足以定论。**

### 极端情绪初步检验(2026-09-07,read-only 分析)

全品种平台-日记录(≥3 条/日)仅 180 条。抖音极端看多(score≥0.5)n=5:
fwd1 = -0.49%(胜率 0%,1 日反向迹象),但 fwd3/fwd5 转正(+0.53%/+1.26%)。
**结论:方向上有信号苗头,统计上完全不足。**

## 2. 假设清单(验证门槛:n≥100/平台,滚动窗口)

1. **H1 抖音反向因子**:抖音平台情绪极端(≥0.5 或 ≤-0.5)时,取反向信号做空/做多。
   验证:极端分组 fwd1/fwd3 收益差与胜率,滚动窗口稳定为负相关才启用。
2. **H2 雪球正向确认**:雪球(trader 集中)方向准确率 >50%,作顺势确认因子。
3. **H3 散户-聪明钱分歧信号**:抖音与雪球情绪差值(douyin_score - xueqiu_score)
   极端时,押注聪明钱一侧。类比既有 `analyze_cross_platform`(signal_analyzer.py:481)
   的"weibo 看多 + zhihu 看跌 → 情绪泡沫"规则,但升级为可回测因子。
4. **平台角色自动估计**(设计原则):每平台的 +1/-1/0 角色(顺势/反向/弃用)从滚动
   回测的方向准确率自动估计,**不写死平台名**——数据积累后角色会变。

## 3. 实现路径(已探明,2026-09-07 探索结论)

### 数据源
- **逐平台分数只在思路2 trends 文件**:`思路2/validate/output/trends/{品种}_sentiment.json`
  的 `series[].platform_scores.{plat}.{avg_score,note_count,bull,bear}`
  (external_data 的 daily_series.platforms 只有条数没有分数——已验证,勿踩坑)。
- 价格:`output/trends/{品种}_price.json`。
- AgentSense 侧加载器:`signal_analyzer.py` 的 `_load_trends`(4 级回退)、
  `_load_price`、`_build_forward_filled_sent_map`(:3878,整体分数前向填充——
  新策略需做**逐平台**版,新增 per_platform 参数或镜像新函数)。

### 策略集成(4 触点,无引擎改动)
1. 策略函数:`signal_analyzer.py` 加独立函数 `run_platform_sentiment_strategy(...)`
   (仿旧系列如 `run_contrarian_sentiment`:753;不走 TECH_KEYS 技术引擎)。
   信号 = Σ role_p × platform_score(role_p 从滚动回测估计)+ 极端情绪反向叠加;
   输出标准结果 dict(`_trade` :1941 + `compute_advanced_metrics` :2991,
   前端卡片即兼容)。
2. 今日信号:`latest_trading_signal`(:3942)加 `if strategy == "platform_sent"`
   分支,用 `_make_signal`(:3920)。
3. Flask 路由:`web_app.py` 加 `/api/trading/platform_sent`(仿 :6513 turtle 路由)+
   导入块 :110-134。
4. 前端 `web_template.html`:下拉 option(:1220-1235)、multi-cb 复选框(:1308)、
   `runMultiCompare` if/else(:5348-5367)、names/colors(:5334/:5335)。

### 测试
仿 `tests/test_technical_strategies.py`:`@patch` `sa._load_price`/`sa._load_trends`,
mock 价格制度(平台→趋势)+ mock 逐平台情绪,断言交易方向符合平台角色、
win_rate/strength_pct 合法。

## 4. 开工条件

- 每日采集持续 → 抖音平台-日样本 ≥100(n≥3 过滤后)后重跑第 1 节检验;
- 若 H1 成立(滚动 fwd1 反向收益差稳定),按第 3 节实现并回测对比
  (`run_strategy_comparison` :3418 加一项);
- 若不成立,把抖音权重交给 softmax 自然衰减即可(现状已如此)。
