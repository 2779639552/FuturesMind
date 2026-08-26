# 移动止损「入场日必触发」bug —— 定位记录(2026-08-13)

> 状态:✅ **已修复(2026-08-25)**。采用建议方向 1「跳过入场日」:
> `apply_risk_management` 检查区间由 `[entry_d, exit_d]` 改为 `(entry_d, exit_d]`(左开右闭,
> `signal_analyzer.py:2875`),入场日收盘价不再参与移动止损判定,保本钳制不再"入场即触发"。
> 修复后新增 `tests/test_trailing_stop_entryday.py`(5 用例)+ 相关 23 用例全绿;
> 原 32 项 Playwright 一致性矩阵中"全部 0.0/-0.1%"的预期值需按新口径重验。
> 当前 worklog `2026-08-11-single-vs-multi-cost-risk.md` 记录的「仅风控 → 全部 0.0」行为
> 即为本 bug 的副作用,当时被视为预期,未深究。

## 现象

CF 海龟回测(5 笔交易),`POST /api/trading/apply_risk` 传 `stop_loss=5, trail_stop=8`:

| 参数 | 结果 |
|------|------|
| 只固定止损 5% | ✅ 正常(5 笔均未触发,亏损都在 5% 内) |
| 只移动止盈 8% | ❌ **5/5 全部 stopped_out,pnl=0.0,exit 被改写为入场日** |
| 两个都开 | ❌ 同左(固定止损先查不触发,移动止损后触发) |

前端默认参数(web_template.html:1123/1124/1132/1133):手续费 3/万、滑点 1 tick、止损 5%、移动止盈 8%。

## 根因

`signal_analyzer.py:2886-2895` `apply_risk_management` 移动止损逻辑:

```python
for d in dates:              # dates 含入场日当天
    px = price_map[d]        # 入场日 px == entry_px(同日收盘价)
    if px > peak_px:
        peak_px = px         # 入场日 px==peak_px,不更新,peak 仍=entry_px
    trail_level = peak_px * (1 - trailing_stop_pct / 100)  # = entry_px*0.92
    trail_level = max(trail_level, entry_px)  # 保本钳制 → = entry_px
    if trailing_stop_pct and px <= trail_level:   # entry_px <= entry_px 恒真!
        stopped_out = True                        # → 入场日必触发
```

- **入场日**被纳入止损检查区间;
- **保本钳制** `trail_level = max(trail_level, entry_px)` 把移动止损线下沿抬到入场价;
- 收盘价 `px == entry_px`,`<=` 判定恒真。

三者叠加 → 任何带移动止盈的交易在**入场当天**无条件止损,pnl=0。固定止损因判定是
`px <= entry_px*0.95`(严格更低)不受影响,故只有移动止损路径崩。

## 影响面

- Web「模拟交易」勾选「风控」→ `apply_risk_management` 把所有交易清零为 pnl=0;
- 单卡 / 多策略两路径同时受影响 → 恰好"一致"(-0.1% = 0 + 每笔扣成本);
- **风控功能(止损/移动止盈)实际未生效**,只是抹平了交易。

## 建议修复方向(待 Day 6 实施,三选一)

1. **跳过入场日**:`dates` 从 `entry_d` 次日开始(最贴近交易语义——当天入场不可能当天被移动止损);
2. 判定改**严格小于** `px < trail_level`(代价:回撤到精确保本价时不触发);
3. 保本钳制在入场日不生效(peak 首次更新前不做移动止损判定)。

修复后需重新验证:单卡==多策略口径一致性(32 项 Playwright 矩阵)+ `-0.1%` 预期值会变。

## 复现脚本

`scripts/day0_cost_risk_demo.py`(Day 0 演示,复刻前端口径)+ 手动 curl:

```bash
curl -s -X POST http://localhost:5000/api/trading/turtle -H "Content-Type: application/json" \
  -d '{"variety":"CF"}' | ...   # 取 recent_trades
curl -s -X POST http://localhost:5000/api/trading/apply_risk -H "Content-Type: application/json" \
  -d '{"variety":"CF","trades":[...],"stop_loss":5,"trail_stop":8}'
```

## 关联

- worklog/2026-08-11-single-vs-multi-cost-risk.md(把「全被立即止损」当预期的那次修复)
- Day 6 学习主题:`apply_risk_management`(signal_analyzer.py:2817)
