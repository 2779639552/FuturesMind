# 新增 12 个市场/机构常用技术策略(2026-08-11)

> 一句话结论:模拟交易模块新增 **6 个技术指标 × (纯价格版 + 情绪确认版)= 12 个策略**。
> 情绪确认版**不用原始情绪方向做门控**,而是镜像 `adaptive_sent` 的**自适应因子**方法:
> 情绪在"顺势确认 / 逆向入场 / 分歧择信"间按趋势强度切换。43 个新测试 + 全量 622 绿,
> ruff 全绿,真机 12 端点冒烟通过。

## 选型(6 指标,OHLCV 数据齐全)

| 指标 | 纯价格入场/离场 | 情绪版 |
|---|---|---|
| 双均线交叉 ma_cross | 快线上穿/下穿慢线;反向下穿/上穿离场 | `ma_cross_sent` |
| MACD | hist 上穿/下穿 0;反向穿越离场 | `macd_sent` |
| RSI(均值回归) | <30 买 / >70 卖;回归 50 离场 | `rsi_sent` |
| 布林带 | 突破上/下轨;回归中轨离场 | `bollinger_sent` |
| 海龟交易法 | 突破 entry 日高/低;exit 日反向或 ATR 止损离场 | `turtle_sent` |
| ATR 通道突破 | 突破 mid±k·ATR;回归 mid 离场 | `atr_sent` |

## 架构:共享指标助手 + 统一接口 + 通用引擎(避免 12 份复制)

- **指标助手**:`_sma`/`_ema`/`_rsi`(Wilder)/`_macd`(dif/dea/hist)/`_bollinger`/`_atr`(Wilder),warmup 前为 None。
- **统一信号接口** `_tech_signal(indicator, i, ...) -> (enter_long, enter_short, exit_long, exit_short, strength, scale, reason)`,
  一次计算入场/离场,strength 为各指标原生幅度,scale 供 strength_pct 归一化。
- **情绪自适应包装** `_adapt_sentiment_signal(tsig, tech_strength, tech_scale, trend, ss) -> (action, strength, scale, reason)`:
  - `tsig==0` + 弱趋势(<1%)+ 极端情绪 → **逆向入场**;
  - 技术信号与情绪同向(|ss|>0.1)→ 顺势确认(强度取较大者);
  - 分歧:强趋势(>3%)**信技术**(动量市)/ 弱趋势(<1%)**逆情绪**(逆向市)/ 中等信技术;
  - 情绪中性 → 跟随技术。
- **通用回测引擎** `_run_technical_backtest(variety, indicator, sent_mode, start_date, end_date, **P)`,
  镜像 donchian 多空循环 + turtle ATR 止损;**离场保持纯技术**,入场才被情绪自适应调整(标准做法)。
- **12 个薄包装** `run_*_strategy`,每个 ~4 行;`TECH_KEYS` dict(12 键 → indicator+sent_mode)回测与今日信号共用。

## 今日信号

`latest_trading_signal` 顶部数据守卫后加 dispatch 块 → `_latest_technical_signal`,
`n <= warmup` 或 sent 版无情绪数据 → None;否则末根 K 线 `_tech_signal` + 自适应,
返回 `{"today_signal": <信号>, "today_signals": {}}`(单策略形状),复用 `_make_signal`
(strength_pct 归一化到 [0,1] 与既有 8 策略同标尺)。

## 接入面

- `web_app.py`:12 个 `/api/trading/{k}` 端点(donchian 端点后),import 块加 12 个包装。
- `web_template.html`:下拉加两个 `<optgroup>`(纯价格 / 情绪确认)、多选加 12 个 checkbox、
  `eps` 对象 + `runMultiCompare` if/else 链 + `names`/`colors` 各加 12 条中文名与颜色。
  新策略为单策略形状,前端走 `d.win_rate!==undefined` 单卡分支与 recent_trades 图表路径,零改动。
- 顺手修复:`signal_analyzer.py` :2274 重复的 `@dataclass` 装饰器删一行。

## 测试

`tests/test_technical_strategies.py`(新建,43 个):
- 6 指标 × up/down(用"先走平后单边移动"的制度切换序列——纯线性趋势下交叉/突破发生在
  warmup 前会被引擎漏掉,见注释)、5 指标 flat→0 交易(RSI 全平=100 恒超买,单测确认路径)、
  布林带突破、日期窗口收窄、空数据/数据不足/空品种守卫。
- `_adapt_sentiment_signal` 7 个纯函数分支(逆向/确认/三档分歧/中性)。
- 5 指标 sent 版 flat+看空情绪 → 逆向做多(纯价格版 0 交易对照)。
- 12 键今日信号结构 + strength_pct ∈ [0,1] + sent 版弱趋势逆向 buy + RSI 同向确认 sell + 守卫。

## 验证(全绿)

- 单测:43 新 + 13 今日信号 = 56 passed;全量 **622 passed / 1 skipped / 1 deselected**。
- ruff check + format 全绿(4 文件);前端完整 `<script>` 块 node --check 通过。
- 真机 curl(重启服务器,12 端点):
  - RB:纯价格 trades 4-11 / 今日 hold;情绪版 trades 6-23 / 今日 buy pct 0.167
    (= RB 当前弱趋势 + 看空情绪 → 逆向买入,自适应分支按设计触发)。
  - 空品种 → `today_signal: null`,回测跑全品种(50 笔)。
  - PP/PVC sent 版今日 sell/hold,无情绪品种守卫正常。
  - 响应保留全部回测字段(win_rate/advanced_metrics 13 项/recent_trades)+ 参数回显 + today_signal。

## 环境备忘

- 推送需 Clash 代理:`git -c http.proxy=http://127.0.0.1:7890 push origin main`
- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(622 passed)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚
