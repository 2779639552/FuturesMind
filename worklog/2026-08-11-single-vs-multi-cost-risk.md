# 单卡回测 vs 多策略对比 不一致 —— 真实根因与修复(2026-08-11)

> 一句话结论:用户报「海龟单独回测 5.58%、多策略中 -0.1%」的**真实根因**是
> 前端两条路径对 **交易成本/风控 开关的处理不一致**:多策略对比(`runMultiCompare`)
> 应用成本扣减 + apply_risk,而单卡回测(`runTradingBacktest`)完全忽略这两个开关、
> 只读后端 `total_pnl`。开启「交易成本」+「风控」后,同一策略两视图必然不同。
> 修复:单卡也应用同样口径,并修正多策略的**双重成本扣减** bug。

## 复现(Playwright 真机,CF 海龟,开启 成本+风控)

| 视图 | 修复前 | 修复后 |
|---|---|---|
| 单卡回测 | **+5.58%**(忽略开关,读 d.total_pnl) | **-0.1%**(≈多策略) |
| 多策略卡片 | 海龟 **-0.1%**(应用成本+风控) | 海龟 -0.1% |

-0.1% = 4 笔全部被风控立即止损(pnl=0)后,每笔仍付一次往返成本(≈0.025%)
的净损失。

## 根因(两层)

1. **单卡不回读开关**:`runTradingBacktest` 只取 `d.total_pnl`(原始),从不读取
   `#cost-enabled`/`#risk-enabled`,也不调用 `/api/trading/apply_risk`。多策略路径则
   对 `recent_trades` 扣成本 + apply_risk。开关一开,两视图口径就分裂。
2. **多策略双重扣成本**(修复单卡后暴露):`runMultiCompare` 在 apply_risk **之前**
   就对 trades 扣一次成本,apply_risk 对止损单会**用价格重算 pnl**(抹掉预扣成本),
   之后又在 pnlMap 累计后**再扣一次**。非止损单被扣两次成本;止损单则只剩第二次
   扣减。→ 与单卡(只扣一次)仍有 -0.1 vs 0 的差。

## 关键后端事实(`apply_risk_management`)

- 对 **stopped_out** 的交易:`t["pnl"] = (stop_px - entry_px)/entry_px*100` —— **重算**,
  完全覆盖传入的(可能已扣成本的)pnl。
- 对未止损的交易:原样透传(保留传入 pnl)。
- 因此成本**必须**在 apply_risk 之后扣,否则止损单的成本被抹掉。

## 修复(`web_template.html`)

1. **单卡 `runTradingBacktest`**:
   - 顶部加读开关:`riskOn/stopLoss/trailStop/costOn/commRate/slipTick`(与多策略同款)。
   - message-only 分支后、collect subs 前,构造 `tradesAdj` + `totalAdj`:
     **先 apply_risk,再按 `cpt=(commRate*2)+(slipTick*0.02)` 逐笔扣成本**,最后 `totalAdj=Σpnl`。
   - `subs[0].total_pnl` 用 `totalAdj`(替代 d.total_pnl);图表累计曲线与交易明细表
     改用 `tradesAdj`。
2. **多策略 `runMultiCompare`**:删掉 apply_risk 前的「对 trades 预扣成本」块
   (双重扣减的来源),成本统一由下方 `pnlMap[d] -= costPerTrade` 一次扣减。

两路径统一为:**风控(apply_risk 重算止损单 pnl)→ 成本逐笔/逐日期扣减 → 求和**。

## 追加:曲线形状策略(adaptive_sent/contrarian/momentum_ad)同样不一致(同日三轮)

单策略形状修复后,发现曲线形状策略单卡 vs 多策略仍有差,且分两种成因:

1. **子策略错选**(`contrarian` 最严重,+7.12 vs -2.5):单卡 `runTradingBacktest`
   按固定顺序 `["adaptive","consensus","contrarian","momentum_baseline"]` 取第一个
   存在的子策略;contrarian 响应没有 `adaptive` 键 → 头版显示 **consensus**(+7.12),
   而多策略 `prefer='contrarian'` 显示 contrarian(-2.5)。同名子策略被 consensus 顶替。
   `adaptive_sent`/`momentum_ad` 因响应里 adaptive 恰好排第一,侥幸一致。
2. **成本未扣**(cost 开启时 +0.3~0.7 的差):单卡曲线子卡片显示后端原始 `d[k].total_pnl`,
   多策略对曲线 delta 逐项扣成本。

### 修复(web_template.html)

- 顶部按策略计算 `prefer`(adaptive_sent→adaptive、contrarian→contrarian、momentum_ad→adaptive,
  与多策略 prefer 同口径);子策略循环把 prefer 排最前;图表选曲线 key 也用 prefer。
- 曲线子卡片总收益在 cost 开启时扣 `cpt × 该子策略笔数`。
- `cpt` 上提到函数顶部,交易成本块与曲线子卡片共用。

### 验证(全绿)

- 前端 JS:最大 `<script>` 块 `node --check` 通过。
- Playwright 真机,**4 开关组合 × 8 品种策略(含 4 个曲线形状)= 32 项**:单卡 vs 多策略卡片
  全部一致(round 到 1 位小数比较):
  - 关/关:turtle 5.6、ma_cross -1.7、macd 1.2、atr 8.5、adaptive_sent 2.3、contrarian -2.5、
    momentum_ad 6.7、MA/adaptive_sent -7.8
  - 仅成本:5.5、-1.8、1.0、8.4、2.0、-2.6、6.0、-8.3
  - 仅风控:turtle/macd/atr 全部 0.0(全被立即止损);曲线形状因无 recent_trades、
    apply_risk 无效 → 与关/关相同(两视图一致地不受风控影响)
  - 成本+风控:turtle -0.1、ma_cross -0.1、macd -0.2、atr -0.1、曲线形状同仅成本
- 后端 Python 未改,单测无需重跑。

## 环境备忘

- 测试:`python -m pytest -ra --strict-markers -m "not integration" --timeout=120`(625 passed)
- ruff:`github_debug\FuturesMind\venv_fresh\Scripts\python.exe -m ruff check .`
- commit 不带 "Co-Authored-By: Claude" 页脚;push 需 Clash 代理
- web_template.html 每次请求重读,前端改动无需重启服务器
