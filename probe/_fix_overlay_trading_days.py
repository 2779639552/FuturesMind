# -*- coding: utf-8 -*-
r"""/api/overlay 的 x 轴口径改为「只保留交易日」（2026-10-13 用户拍板）。

## 背景（用户报障）
「情绪得分 vs 价格走势」图里价格曲线有**截断**。实测取证：
  · 后端 x 轴原为「价格日期 ∪ 情绪日期」并集；
  · 情绪按**自然日**聚合（周末/假期也有人发帖），价格只有**交易日**
    ⇒ 合并序列里混入非交易日，其 close 为 None；
  · 前端价格序列 `connectNulls: false` ⇒ **价格线在这些点被切断**。
  · 扫 RB/TA/M/CF/SC/CU/I/J 八品种：缺价日期 100% 是周末或法定假期
    （09-25 中秋、10-01/02/05/06/07 国庆），**没有任何交易日缺价**
    ⇒ 价格数据本身完整，属**展示口径**问题，不是取数缺陷。

## 修法（用户三选一选了 C）
x 轴只保留交易日；落在非交易日（周末/法定假期）的情绪**归并到其后的第一个交易日**，
同日多条按**帖子数加权**合并（多空比按计数求和后再相除，不对比率求平均）。

## 本次附带修正
`daily_series` 里从来没有 `bullish_ratio` / `bearish_ratio` 字段（只有 bull_count /
bear_count / note_count）⇒ 原 `d.get("bullish_ratio", 0)` **恒为 0**，
tooltip 的「多空比」一直显示 `0% / 0%`。改为由计数现算。

## 影响面（已核）
· 只有前端的 `loadOverlayChart` 消费本接口；
· 底层 `{CODE}_sentiment.json` **不动** ⇒ 情绪信号/回测等其他模块口径不变；
· 前端**无需改动**（x 轴、series、均线全是数据驱动，自动跟随新口径）。

用法: venv/Scripts/python.exe probe/_fix_overlay_trading_days.py [--apply]
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "web_app.py"
BAK = "backup/web_app_pre_overlay_tradingdays_20261013_1646.py"
APPLY = "--apply" in sys.argv

# ── ① 新增：合并辅助函数（插在 _adaptive_factor 之后）────────────────────
ANCHOR_FN = '''    if abs(trend_pct) < 1:                  # 弱趋势/震荡 → 逆向
        return -1
    return 1                                # 默认顺势
'''

NEW_FN = ANCHOR_FN + '''

def _merge_sentiment_rows(rows: list) -> dict:
    """把同一交易日上的多条自然日情绪行合并成一条(按帖子数加权)。

    【为什么需要】情绪按**自然日**聚合(周末/法定假期也发帖), 而行情只有**交易日**。
    为使「情绪 vs 价格」图的 x 轴只保留交易日, 落在非交易日的情绪要**归并到其后的
    第一个交易日**; 于是一个交易日可能同时收到「当天自身 + 周末两天 + 假期数天」多条
    记录, 必须合并成一条才能挂到该交易日的一个点上。

    【口径】
      · avg_score / simple_avg: 按各自 total_notes **加权平均** —— 帖子多的那天影响更大,
        与"日聚合时每条帖子等权"的原始口径一致(不加权会让只有 1 帖的周末与有 50 帖的
        交易日等权, 明显失真)。
      · 多空比: 先把 bull/bear **计数求和**再相除。**不能对比率再求平均** ——
        那样会忽略各天样本量差异(1 帖 100% 看多 与 50 帖 60% 看多 等权是错的)。
      · total_notes: 求和(该交易日实际覆盖的帖子总数)。
    【退化】若所有天的 note_count 均为 0(缺字段) → 退回**等权平均**, 且不产生除零。
    【返回】dict: avg_score / simple_avg / bullish_ratio / bearish_ratio /
            total_notes / bull_count / bear_count。
    """
    n = len(rows)
    total = sum(int(r.get("total_notes") or 0) for r in rows)

    if total > 0:
        def _wavg(key):
            return sum(float(r.get(key) or 0) * int(r.get("total_notes") or 0)
                       for r in rows) / total
    else:
        def _wavg(key):
            return sum(float(r.get(key) or 0) for r in rows) / n

    bull = sum(int(r.get("bull_count") or 0) for r in rows)
    bear = sum(int(r.get("bear_count") or 0) for r in rows)
    return {
        "avg_score": round(_wavg("avg_score"), 4),
        "simple_avg": round(_wavg("simple_avg"), 4),
        "bullish_ratio": (bull / total) if total else 0,
        "bearish_ratio": (bear / total) if total else 0,
        "total_notes": total,
        "bull_count": bull,
        "bear_count": bear,
    }
'''

# ── ② sentiment_map 构造：补计数 + 修正恒 0 的多空比 ────────────────────
SENT_OLD = '''        for d in sd["data"]["daily_series"]:
            sentiment_map[d["date"]] = {
                "avg_score": d.get("avg_score", 0),
                "simple_avg": d.get("simple_avg", 0),
                "bullish_ratio": d.get("bullish_ratio", 0),
                "bearish_ratio": d.get("bearish_ratio", 0),
                "total_notes": d.get("note_count", d.get("total_notes", 0)),
            }
'''

SENT_NEW = '''        for d in sd["data"]["daily_series"]:
            _n = d.get("note_count", d.get("total_notes", 0)) or 0
            _bull = d.get("bull_count", 0) or 0
            _bear = d.get("bear_count", 0) or 0
            # 【2026-10-13 修】原写法取 d.get("bullish_ratio", 0) **恒为 0** ——
            #   daily_series 里从来没有 bullish_ratio/bearish_ratio 字段(实测 64 个品种
            #   1117 条记录, 字段只有 date/avg_score/simple_avg/note_count/bull_count/
            #   bear_count/weighted_score/platforms/top_authors), 于是 tooltip 的
            #   「多空比」一直显示 0% / 0%。改为由 bull/bear 计数与总帖数现算。
            sentiment_map[d["date"]] = {
                "avg_score": d.get("avg_score", 0),
                "simple_avg": d.get("simple_avg", 0),
                "bullish_ratio": (_bull / _n) if _n else 0,
                "bearish_ratio": (_bear / _n) if _n else 0,
                "total_notes": _n,
                "bull_count": _bull,
                "bear_count": _bear,
            }
'''

# ── ③ 合并逻辑：并集 x 轴 → 交易日 x 轴 + 情绪归并 ──────────────────────
MERGE_OLD = '''    # Merge by date, then filter to the requested window
    all_dates = sorted(set(list(price_map.keys()) + list(sentiment_map.keys())))
    overlay = []
    for date in all_dates:
        point = {"date": date}
        if date in price_map:
            point["close"] = price_map[date]
        if date in sentiment_map:
            point.update(sentiment_map[date])
        # 【2026-09-24 自适应因子】同日有价格+情绪且趋势可算 → 附离散因子; 否则无该键
        trend_pct = trend_pct_by_date.get(date)
        if trend_pct is not None and date in sentiment_map:
            point["adaptive"] = _adaptive_factor(trend_pct, sentiment_map[date]["avg_score"])
        # Apply days filter: only include dates within the lookback window
        if date >= start_date:
            overlay.append(point)
'''

MERGE_NEW = '''    # ── x 轴 = 交易日; 非交易日的情绪归并到其后第一个交易日 ──────────────────
    # 【2026-10-13 用户拍板改口径】原实现: all_dates = 价格日期 ∪ 情绪日期 的**并集**,
    #   于是"只有情绪、没有价格"的非交易日(周末/法定假期)也占 x 轴位置, 其 close 为
    #   None; 而前端价格序列 connectNulls=false ⇒ **价格线在这些点被切断** ——
    #   即用户报的"曲线有截断"。实测(RB/TA/M/CF/SC/CU/I/J): 缺价日期 100% 是周末或
    #   法定假期(09-25 中秋、10-01/02/05/06/07 国庆), **没有任何交易日缺价**
    #   ⇒ 价格数据本身完整, 属展示口径问题。
    #   改为: x 轴只保留交易日; 落在非交易日的情绪**顺延到其后第一个交易日**
    #   (周末/假期不能交易, 这些帖子反映的情绪归到下一个可交易的时点), 同日多条加权合并。
    import bisect

    window_dates = [d for d in px_dates if d >= start_date]  # 【变量】窗口内交易日(x 轴)
    merged: dict = {}          # 【变量】交易日 → 归并到该日的原始情绪行列表
    sent_self = 0              # 【变量】本来就落在交易日上的情绪日数
    sent_carried = 0           # 【变量】从非交易日顺延过来的情绪日数
    orphan_dates: list = []    # 【变量】找不到窗口内归属交易日的情绪日(仅作透明统计)
    for _sd in sorted(sentiment_map.keys()):
        _i = bisect.bisect_left(px_dates, _sd)
        _t = px_dates[_i] if _i < len(px_dates) else None
        if _t is None or _t < start_date:
            orphan_dates.append(_sd)
            continue
        if _t == _sd:
            sent_self += 1
        else:
            sent_carried += 1
        merged.setdefault(_t, []).append(sentiment_map[_sd])

    overlay = []
    for date in window_dates:
        # 每个点都是交易日 ⇒ 必有收盘价 ⇒ 价格线不再有断点
        point = {"date": date, "close": price_map[date]}
        if date in merged:
            point.update(_merge_sentiment_rows(merged[date]))
        # 【2026-09-24 自适应因子】该交易日有(归并后的)情绪且趋势可算 → 附离散因子
        trend_pct = trend_pct_by_date.get(date)
        if trend_pct is not None and date in merged:
            point["adaptive"] = _adaptive_factor(trend_pct, point["avg_score"])
        overlay.append(point)
'''

# ── ④ meta 补口径说明 ───────────────────────────────────────────────────
META_OLD = '''        "adjust_method": "backward",  # 【变量】复权方式:后复权
        "rollover_dates": overlay_roll_dates,  # 【变量】换月日期(真实日历查证 or 8% 启发式)
        "rollover_method": overlay_method,  # 【变量】换月来源(calendar=真实日历 / heuristic=8%启发式)
'''

META_NEW = '''        "adjust_method": "backward",  # 【变量】复权方式:后复权
        "rollover_dates": overlay_roll_dates,  # 【变量】换月日期(真实日历查证 or 8% 启发式)
        "rollover_method": overlay_method,  # 【变量】换月来源(calendar=真实日历 / heuristic=8%启发式)
        # 【2026-10-13】x 轴口径改为只含交易日, 非交易日情绪已顺延归并 —— 这几个字段
        #   供前端与排查了解"画了几个交易日、顺延了多少天、丢了多少天"。
        "xaxis_mode": "trading_days",
        "trading_days": len(window_dates),
        "sentiment_days_on_trading_day": sent_self,
        "sentiment_days_carried": sent_carried,
        "sentiment_days_orphan": len(orphan_dates),
'''
CMT_OLD = '''#   前端需自行容错;随后按 start_date 过滤到请求的时间窗内。'''
CMT_NEW = '''#   前端需自行容错;随后按 start_date 过滤到请求的时间窗内。
# 【2026-10-13 口径变更】x 轴由「价格日期 ∪ 情绪日期」并集**改为只含交易日** ——
#   非交易日(周末/法定假期)的情绪顺延归并到其后第一个交易日(同日多条按帖子数加权)。
#   原因: 并集会让"只有情绪没有价格"的非交易日占位 ⇒ close 为 None ⇒ 前端
#   connectNulls=false 把价格线切断(用户报的"曲线有截断")。价格数据本身并无缺口。'''

REPL = [
    ("A 合并辅助函数", ANCHOR_FN, NEW_FN),
    ("B sentiment_map 补计数+修多空比", SENT_OLD, SENT_NEW),
    ("C 交易日 x 轴 + 情绪归并", MERGE_OLD, MERGE_NEW),
    ("D meta 口径字段", META_OLD, META_NEW),
    ("E 块注释同步", CMT_OLD, CMT_NEW),
]


def _code_only(s: str) -> str:
    """剥掉整行注释后的源码，供"残留检查"用。

    ⚠️ 为什么必须剥：残留检查是**子串匹配**，而本次新增的注释里正好照抄了被删掉的
    旧写法（"原写法取 d.get(\\"bullish_ratio\\", 0) 恒为 0 …"）⇒ 直接匹配会命中
    注释本身、把正确的改动误判成"残留"。今天在 JSON 那条断言上已因同类原因翻过一次车。
    """
    return "\n".join(l for l in s.split("\n") if not l.lstrip().startswith("#"))


def main() -> int:
    raw = TARGET.read_bytes()
    if raw.count(b"\r\n") == 0 or (raw.count(b"\n") - raw.count(b"\r\n")) != 0:
        print("✘ web_app.py 非纯 CRLF，中止")
        return 2
    text = raw.decode("utf-8")
    src = text.replace("\r\n", "\n")

    problems = []
    for tag, old, new in REPL:
        n = src.count(old)
        if n != 1:
            problems.append(f"{tag}: 锚点命中 {n} 次 (预期 1)")
            continue
        src = src.replace(old, new)

    # 残留检查：只在**活代码**里判（注释里的历史写法不算残留）
    code = _code_only(src)
    if "all_dates = sorted(set(list(price_map.keys()) + list(sentiment_map.keys())))" in code:
        problems.append("残留旧并集 all_dates 逻辑")
    if 'd.get("bullish_ratio", 0)' in code:
        problems.append("残留恒 0 的 bullish_ratio 取法")
    if "_merge_sentiment_rows" not in code:
        problems.append("未插入 _merge_sentiment_rows")
    if "for date in window_dates:" not in code:
        problems.append("未改为按交易日遍历")

    if problems:
        print("\n✘ 校验未通过，未写文件：")
        for p in problems:
            print("   ·", p)
        return 1

    out = src.replace("\n", "\r\n")
    bare = out.count("\n") - out.count("\r\n")
    if bare != 0:
        print(f"✘ 写回自检 bareLF={bare}（应为 0）")
        return 2

    if not APPLY:
        print("✔ dry-run 全部校验通过（未写文件）。加 --apply 落盘。")
        return 0

    shutil.copy2(TARGET, ROOT / BAK)
    TARGET.write_bytes(out.encode("utf-8"))
    chk = TARGET.read_bytes()
    print("✔ 已写入: %d → %d 字节 (+%d)" % (len(raw), len(chk), len(chk) - len(raw)))
    print("   行尾自检: CRLF=%d bareLF=%d" % (chk.count(b"\r\n"),
                                             chk.count(b"\n") - chk.count(b"\r\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
