# Day 0 演示:复刻前端「单卡回测」的成本/风控口径,跑出四种组合
# 与 web_template.html:2798-2821 完全同逻辑:先风控(apply_risk 重算 pnl),再每笔减 cpt。
import json
import urllib.request

BASE = "http://localhost:5000"
VARIETY = "CF"
# 前端默认值(web_template.html:1123/1124/1132/1133)
COMM_RATE = 3 / 10000          # 手续费 万分之3
SLIP_TICK = 1                  # 滑点 1 tick
CPT = (COMM_RATE * 2) + (SLIP_TICK * 0.02)   # 每笔往返成本(%)
STOP_LOSS = 5                  # 止损 %
TRAIL_STOP = 8                 # 移动止盈 %


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


# 1) 海龟回测(原始,无成本无风控)
d = post("/api/trading/turtle", {"variety": VARIETY})
trades = d.get("recent_trades", [])
raw_pnl = d.get("total_pnl", 0)

# 2) 风控:用真实价格重算每笔 pnl
risk_trades = post("/api/trading/apply_risk",
                   {"variety": VARIETY, "trades": trades,
                    "stop_loss": STOP_LOSS, "trail_stop": TRAIL_STOP}).get("trades", trades)

# 四种组合(全部与前端同逻辑)
none = raw_pnl
cost_only = sum(t.get("pnl", 0) - CPT for t in trades)
risk_only = sum(t.get("pnl", 0) for t in risk_trades)
both = sum(t.get("pnl", 0) - CPT for t in risk_trades)

print(f"CF 海龟 | 交易数={len(trades)} | cpt(每笔成本%)={CPT:.4f}")
print(f"组合 1 什么都不勾  : {none:+.4f}%")
print(f"组合 2 只勾交易成本 : {cost_only:+.4f}%  (Δ {cost_only-none:+.4f})")
print(f"组合 3 只勾风控     : {risk_only:+.4f}%  (Δ {risk_only-none:+.4f})")
print(f"组合 4 两个都勾     : {both:+.4f}%  (Δ {both-none:+.4f})")
print(f"\n今日信号: {d.get('today_signal')}")
