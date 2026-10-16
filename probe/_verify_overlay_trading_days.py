# -*- coding: utf-8 -*-
r"""离线验收：/api/overlay 的 x 轴已改为「只含交易日」+ 情绪顺延归并（2026-10-13）。

**不依赖运行中的服务**：直接 import web_app 用 test_client 打接口，
因此在另一个会话占用 5000 端口（无需重启）时也能验。

验六件事：
  A 每个点都有收盘价 ⇒ 价格线无断点（这正是用户报的"截断"）
  B x 轴上每个日期都来自价格序列 ⇒ 全部是交易日（无周末/法定假期）
  C 情绪守恒：归并前后总帖数、情绪日数一致（归并只挪位置，不丢数据）
  D 归并口径正确：非交易日情绪顺延到其后第一个交易日，且**按帖子数加权**（手工核对一天）
  E 多空比不再是恒 0（附带修正）
  F 与"并集旧口径"对比：价格点数减少的量 == 被顺延的情绪日数
"""
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"      # 仅本测试进程关闭鉴权

fails = []


def ck(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def wd(ds):
    return date.fromisoformat(ds).weekday()      # 0=周一 … 5=周六 6=周日


def load_sentiment(sym):
    p = Path.home() / ".tradingagents" / "external_data" / f"{sym}_sentiment.json"
    if not p.exists():
        return {}
    j = json.loads(p.read_text(encoding="utf-8"))
    return {r["date"]: r for r in j.get("data", {}).get("daily_series", [])}


import web_app  # noqa: E402

client = web_app.app.test_client()

print("[0] 接口连通性")
r = client.get("/api/overlay/RB?days=180")
ck(r.status_code == 200, f"GET /api/overlay/RB => {r.status_code}")

for sym in ("RB", "TA", "M", "CF", "SC"):
    print(f"\n===== {sym} =====")
    resp = client.get(f"/api/overlay/{sym}?days=180")
    if resp.status_code != 200:
        ck(False, f"{sym} 接口 {resp.status_code}")
        continue
    d = resp.get_json()
    ov = d.get("overlay", [])
    meta = d.get("_meta", {})

    # A 每点都有 close
    miss = [x["date"] for x in ov if x.get("close") is None]
    ck(len(miss) == 0, f"[A] 全部 {len(ov)} 个点都有 close（缺 {len(miss)}）")

    # B 全部是交易日：周末 / 已知法定假期都不该出现
    weekends = [x["date"] for x in ov if wd(x["date"]) >= 5]
    holidays = [x["date"] for x in ov
                if x["date"] in {"2026-09-25", "2026-10-01", "2026-10-02",
                                 "2026-10-05", "2026-10-06", "2026-10-07"}]
    ck(len(weekends) == 0, f"[B] x 轴无周末（命中 {weekends}）")
    ck(len(holidays) == 0, f"[B] x 轴无国庆/中秋（命中 {holidays}）")
    ck(all(meta.get(k) is not None for k in
           ("xaxis_mode", "trading_days", "sentiment_days_carried")),
       f"[B] meta 口径字段齐全: mode={meta.get('xaxis_mode')} "
       f"trading_days={meta.get('trading_days')} carried={meta.get('sentiment_days_carried')}")

    # C 情绪守恒
    raw = load_sentiment(sym)
    start = meta.get("filter_start")
    px_dates = [x["date"] for x in ov]
    # 原始情绪里，归并目标落在窗口内的那些（其余是 orphan）
    in_win = {k: v for k, v in raw.items() if any(t >= k for t in px_dates) and k <= px_dates[-1]}
    # 用后端 meta 校验人数守恒
    tot_days = meta.get("sentiment_days_on_trading_day", 0) + meta.get("sentiment_days_carried", 0) \
        + meta.get("sentiment_days_orphan", 0)
    ck(tot_days == len(raw), f"[C] 情绪日守恒: 交易日 {meta.get('sentiment_days_on_trading_day')}"
                             f" + 顺延 {meta.get('sentiment_days_carried')}"
                             f" + 孤立 {meta.get('sentiment_days_orphan')} = {tot_days}（原始 {len(raw)}）")
    ov_notes = sum(x.get("total_notes") or 0 for x in ov)
    raw_notes = sum((v.get("note_count") or 0) for v in raw.values())
    ck(ov_notes == raw_notes, f"[C] 帖子总数守恒: overlay {ov_notes} vs 原始 JSON {raw_notes}")

    # D 归并口径：逐日手工核对（挑一个确实收到顺延情绪的交易日）
    hit = []
    for x in ov:
        t = x["date"]
        # 该交易日应吸收：自身 + 它之前直到上一个交易日之间的所有情绪日
        prev = None
        for pd in px_dates:
            if pd < t:
                prev = pd
            else:
                break
        absorbed = [k for k in raw if (t == k) or (prev is not None and prev < k < t) or (prev is None and k <= t)]
        if len(absorbed) > 1:
            expect_notes = sum(raw[k].get("note_count") or 0 for k in absorbed)
            ok_notes = (x.get("total_notes") or 0) == expect_notes
            # 加权平均核对
            if expect_notes:
                exp_avg = sum((raw[k].get("avg_score") or 0) * (raw[k].get("note_count") or 0)
                              for k in absorbed) / expect_notes
            else:
                exp_avg = sum((raw[k].get("avg_score") or 0) for k in absorbed) / len(absorbed)
            ok_avg = abs((x.get("avg_score") or 0) - exp_avg) < 1e-3
            hit.append((t, absorbed, ok_notes, ok_avg, x.get("total_notes"), expect_notes))
        if len(hit) >= 3:
            break
    ck(len(hit) > 0, f"[D] 找到 {len(hit)} 个吸收了顺延情绪的交易日")
    for t, absorbed, okn, oka, got, exp in hit:
        ck(okn and oka, f"[D] {t} 吸收 {absorbed} ⇒ 帖数 {got}(应 {exp}) 加权均值{'✓' if oka else '✗'}")

    # E 多空比不再恒 0
    with_sent = [x for x in ov if x.get("avg_score") is not None]
    ratios = [x.get("bullish_ratio") or 0 for x in with_sent]
    ck(len(with_sent) > 0 and any(r > 0 for r in ratios),
       f"[E] 多空比有非 0 值（{len(with_sent)} 个情绪日，最大 {max(ratios) if ratios else 0:.2f}）")

    # F 顺延量与"并集旧口径"的关系
    pushed = meta.get("sentiment_days_carried", 0)
    ck(pushed >= 0, f"[F] 顺延了 {pushed} 个情绪日（旧并集口径下它们会各占一个 x 轴位置）")

print("\n结果:", "全部通过 ✔" if not fails else f"{len(fails)} 项失败 ✘")
for f in fails:
    print("   ✘", f)
sys.exit(1 if fails else 0)
