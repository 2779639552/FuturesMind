# 多策略对比 0.0% 收益率根因修复(2026-08-11)

> 一句话结论:多策略对比仍显示 0.0% 的**真实根因**是 HC 品种价格数据加载失败
> (`_load_price('HC')` 返回 None → 0 交易 → total_pnl=0)。加 code→文件名别名后 HC 全部恢复;
> 另修前端:真正 0 笔交易的策略改为显示「无数据」,不再误导性显示「0.0%」。

## 追加:海龟 5.58% vs 多策略 -0.1%(同日二轮)

用户报告「海龟单独回测 5.58%,多策略中仍为 -0.1%」。排查结论:

- **后端 100% 一致**:`recent_trades` = 全部交易,`total_pnl` = sum(pnls) = sum(recent_trades.pnl),恒等。
- **前端多策略卡片** = sum(recent_trades pnl),与单卡读的 total_pnl 必然相等。全量扫描
  **19 策略 × 21 品种 = 399 组合,0 异常**,且无任何 entry/exit 为空的交易。
- **真机复现**:CF 海龟在单卡卡片、多策略卡片、多策略图表中均为 5.58。
- **真凶**:单卡回测 `runTradingBacktest` 只在 `d.curves` 存在时画图;turtle 等单策略形状
  (只有 recent_trades)不画图 → **上次多策略图表残留**,海龟线的旧终点(-0.1%)误导用户。
  即 -0.1% 是「沿用多策略图表」的旧值,不是当前多策略算错。

### 修复(`web_template.html`)

1. **单卡回测总是重绘图**(`#chart-trading-pnl`):多曲线形状取代表曲线;单策略形状从
   `recent_trades` 构建累计 PnL 曲线(按 entry/exit 日期、价格日期轴前向填充),并叠加
   **品种价格线**(`/api/price/{v}?days=730` 覆盖回测窗口)。标题 `${strategy} 回测`。
2. **0 笔交易(message-only 响应)时清空图表**:dispose + `display:none`,不再残留。

## 根因

浏览器真机复现:**品种下拉选 HC,勾选全部 18 个策略 → 每个都显示 0.0%**。

追到数据层,HC 是下拉 21 个品种中**唯一** `_load_price` 与 `_load_trends` 都加载失败的品种:

| 项 | 值 | UTF-8 hex |
|---|---|---|
| 磁盘文件名 | `热卷_price.json` | `e783ad e58db7` |
| `VARIETY_METADATA['HC']['name']` | 「热轧卷板」 | `e783ad e8bda7 e58db7 e69dbf` |

meta 全称「热轧卷板」与文件名简称「热卷」不匹配,且**两者互不为子串**(轧 插入其中),
`_load_price` 的三级 fallback(code → 中文名 → glob)全部失配 → None → 0 交易 → pnlMap 空 → 0.0%。
其它 20 个品种 meta 名与文件名一致,所以只有 HC 复现。

## 修复

### 1. 后端 `signal_analyzer.py` — 数据文件名别名

```python
# 数据文件名的中文简称与 VARIETY_METADATA 全称不一致的品种(code -> 文件名用名)。
# 目前仅 HC:meta name「热轧卷板」,数据文件「热卷_price.json」/「热卷_sentiment.json」。
_DATA_FILE_NAME_ALIASES = {"HC": "热卷"}
```

`_load_price` 与 `_load_trends` 在中文名 fallback 后、glob 前,各插一段:
`alias = _DATA_FILE_NAME_ALIASES.get(variety)` → 存在则试 `{alias}_price.json` / `{alias}_sentiment.json`。

### 2. 前端 `web_template.html` — 0 笔交易显示「无数据」

- **多策略对比卡片**(`runMultiCompare`):`st.trades` 为 0 时主数字区渲染
  `无数据`(灰),不再渲染 `${pnl.toFixed(1)}%`;底部收益条同理(0 笔时不再显示 vs市场)。
- **单卡**(`runTradingBacktest`):主标题与子卡片 `s.trades` 为 0 时显示 `无数据`。
  单卡的 message-only 响应本就走既有 2432-2444 分支显示「No trades generated」,此改动是兜底。

## 验证(全绿)

- 单测:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120` → **625 passed / 1 skipped**。
- ruff(仅 Python):`signal_analyzer.py` All checks passed。
- 前端 JS:提取最大 `<script>` 块 `node --check` 通过。
- 真机 Playwright(chromium,重启服务器后):
  - **HC + 全 18 策略**:收益率全部为真实值(+9.1% MACD ～ -6.4% 海龟+情绪),无一 0.0%;
    底部收益条全部带 vs市场 对比。
  - **UR + fixed/trailing**(真实 0 交易):卡片与收益条均显示「无数据 0笔」,不再 0.0%。
  - **单卡回归**:UR/fixed → 「No trades generated」+今日买入;RB/ma_cross → -1.67% 6 笔,正常。

## 环境备忘

- 推送需 Clash 代理:`git -c http.proxy=http://127.0.0.1:7890 push origin main`
- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(625 passed)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚
