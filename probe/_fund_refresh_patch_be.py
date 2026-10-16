# -*- coding: utf-8 -*-
"""后端补丁：基本面截面表改为「后台计算 + 缓存 + 定期刷新」（2026-10-16）。

【改动 4 处】
  B1 追加后台刷新机制全套（_FRK_STATE / _frk_state_key / _frk_worker / _frk_start / _frk_snapshot）
  B2 `_TTL_FUND_RANK` 300 → 900（与技术面 `_TTL_OVERVIEW` 对齐，前端周期也据此对齐）
  B3 `/api/fund/ranking` 改走 `_frk_start/_frk_snapshot`（不再同步阻塞 40s）
  B4 响应加 ready/total/done/refreshing/stale/refreshed/ts 并**修正 cached 语义**

【为什么 cached 要改】原为 `not refresh` —— 只要不是显式刷新就报"命中缓存"，
  哪怕就是**刚刚现算出来**的（技术面那轮实测第一轮就 cached=true，是假的）。
  改为如实反映"本次拿到的是一份**没在刷新**的新鲜结果"。

【不幂等】重跑会重复插入 ⇒ 开头检测标记并拒绝。要从 backup 还原后再跑。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FM = ROOT / "fund_module.py"
P = ROOT / "probe"

MARK = "_frk_worker"

E1_OLD = '''    rows = [out[c] for c in codes if c in out]
    q._cache_set(key, rows)
    return rows
'''
E1_NEW = '''    rows = [out[c] for c in codes if c in out]
    q._cache_set(key, rows)
    return rows


HELPERS_HERE
'''

E2_OLD = '_TTL_FUND_RANK = 300  # 【变量】基本面截面整体缓存 5 min(21 品种 × 多源, 避免重复全量拉)'
E2_NEW = '''# 【变量】基本面截面整体缓存 15 min —— **与技术面 `quant_module._TTL_OVERVIEW` 对齐**
#   （用户 2026-10-16 要求"更新模式也按照技术量化的方法"）。前端 `_FRK_REFRESH_MS`
#   必须与这个值一致，否则要么永远命中旧缓存、要么每次都白重算。
_TTL_FUND_RANK = 900'''

E3_OLD = '''    rows = _fund_rank_all(codes, force=refresh)'''
E3_NEW = '''    # ── 后台计算 + stale-while-revalidate（2026-10-16）──────────────────────
    # 原为同步 `_fund_rank_all(codes, force=refresh)` ⇒ 首次干等约 40s，且 TTL 一到
    # 就把结果丢掉重算（用户每次回来都得再等一轮）。现改为：
    #   起后台线程（幂等）+ 立刻返回当前快照 ⇒ 前端秒开（有旧结果时）或显示进度。
    skey = _frk_start(codes, force=refresh)
    rows, done, err, meta = _frk_snapshot(skey, codes)'''

E4_OLD = '''    return jsonify(_q()._json_safe({"ok": True, "metric": metric, "order": order,
                               "count": len(rows), "cached": not refresh,
                               "pools": ["全部品种"] + _pools, "rows": rows}))'''
E4_NEW = '''    return jsonify(_q()._json_safe({
        "ok": True, "metric": metric, "order": order,
        # ready/total/done 供前端做**渐进渲染**（同技术面总览的字段名, 前端判据可复用）
        "count": len(rows), "ready": len(rows), "total": len(codes), "done": done,
        # 【★ cached 语义修正】原为 `not refresh` —— 只要不是显式刷新就报"命中缓存",
        #   哪怕就是**刚刚现算出来**的(实测第一轮就为真, 是假的)。
        #   现如实反映"本次拿到的是一份**没在刷新**的新鲜结果"。
        "cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),
        "refreshing": meta["refreshing"], "stale": meta["stale"],
        "refreshed": meta["refreshed"], "ts": meta["ts"], "err": err,
        "pools": ["全部品种"] + _pools, "rows": rows}))'''


def main(write: bool) -> int:
    raw = FM.read_bytes()
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2
    helpers = (P / "_frk_refresh_helpers.py.txt").read_text(encoding="utf-8").rstrip("\n")

    edits = [
        (E1_OLD, E1_NEW.replace("HELPERS_HERE", helpers), 1, "B1 追加后台刷新机制"),
        (E2_OLD, E2_NEW, 1, "B2 _TTL_FUND_RANK 300→900"),
        (E3_OLD, E3_NEW, 1, "B3 ranking 改走后台机制"),
        (E4_OLD, E4_NEW, 1, "B4 响应加字段 + cached 语义修正"),
    ]
    ok = True
    new = txt
    for old, rep, n, tag in edits:
        got = new.count(old)
        flag = "OK " if got == n else "★  "
        print("  %s %-34s 命中 %d(期望 %d)" % (flag, tag, got, n))
        if got != n:
            ok = False
            continue
        new = new.replace(old, rep, 1)
    if not ok:
        print("  ⇒ 有锚点未命中, **未写盘**")
        return 1
    # ── 写盘前硬自检 ──────────────────────────────────────────────────────────
    # 关键函数/常量计数不变 + 长度变化在合理区间（防锚点打偏吃掉内容）
    for probe in ("def api_fund_ranking(", "def _fund_xsection_one(", "def _fund_rank_all(",
                  "def api_fund_xsection(", "def _fund_vote_inputs(",
                  "@fund_bp.route"):
        a, b = txt.count(probe), new.count(probe)
        if a != b:
            print("  ★ 写盘前自检失败: %r 计数 %d → %d ⇒ 拒绝写盘" % (probe, a, b))
            return 1
    dl = len(new) - len(txt)
    if not (2000 <= dl <= 12000):
        print("  ★ 写盘前自检失败: 长度变化 %+d 字符, 超出合理区间 ⇒ 拒绝写盘" % dl)
        return 1
    print("  ✔ 写盘前自检: 关键函数计数不变 / 长度 %+d 字符" % dl)
    if not write:
        print("  ⇒ 空跑通过(未写盘)。加 --write 落盘。")
        return 0
    FM.write_bytes(new.encode("utf-8"))
    b = FM.read_bytes()
    assert b.count(b"\r\n") == 0, "fund_module 应保持 LF"
    print("  落盘后 md5=%s (LF 保持)" % hashlib.md5(b).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
