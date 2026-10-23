# -*- coding: utf-8 -*-
"""L0 漂移对比：修哑票前后，净票与最终方向变了多少。

【做法】逐品种调 /api/quant/verdict 取**同一份 vote 列表**，然后：
  old = 旧 dirmap（只认 做多/单边做多/做空/单边做空）
  new = vote_semantics（额外认 偏多/偏空）
再比 old_verdict vs new_verdict。
  ★ 因为 vote 列表完全没变，两套口径的差异**只**来自"哪张票被计入"，
    所以这个对比能把因果锁死在 L0 这一处改动上。

【为什么不用重启服务跑两遍】服务重启一次要 30s+ 且可能撞上 16:40 的仓单采集；
  vote 是纯取数产物、与计数口径无关，本地算两遍即可。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import vote_semantics as vs  # noqa: E402

BASE = "http://127.0.0.1:5000"
OUT = os.path.join(ROOT, "probe", "_l0_drift.json")

_OLD = {"做多": 1, "单边做多": 1, "做空": -1, "单边做空": -1}


def get_json(url: str, timeout: int = 180) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def old_net(votes):
    return sum(_OLD.get(str(v.get("bias")), 0) for v in votes)


def dir_of(net: int) -> str:
    return "做多" if net > 0 else ("做空" if net < 0 else "暂不操作")


def main() -> None:
    pool = get_json(BASE + "/api/varieties")
    # 该接口直接返回 list；兼容 dict 包装是为了不被上游改成 {varieties:[...]} 时打断
    codes = pool if isinstance(pool, list) else (
        pool.get("varieties") or pool.get("pool") or pool.get("codes") or [])
    if not codes:
        print("拿不到品种池:", str(pool)[:200])
        return
    if isinstance(codes[0], dict):
        codes = [c.get("code") or c.get("variety") for c in codes]

    rows = []
    for k, code in enumerate(codes, 1):
        t = time.time()
        try:
            d = get_json(BASE + "/api/quant/verdict?variety=" + urllib.parse.quote(code))
        except Exception as e:  # noqa: BLE001
            print("[%d/%d] %s FAIL %s" % (k, len(codes), code, e), flush=True)
            continue
        sd = ((d.get("stages") or {}).get("direction") or {})
        votes = sd.get("votes") or []
        if not votes:
            print("[%d/%d] %-4s 无票(%s)" % (k, len(codes), code, str(d.get("error") or d.get("reason"))[:40]),
                  flush=True)
            continue
        o = old_net(votes)
        n, audit = vs.net_of(votes)
        dead = [f"{v.get('book')}={v.get('bias')}" for v in votes
                if vs.classify(v.get("bias"))[1] == vs.NON_DIRECTION
                and str(v.get("bias")) in ("偏多", "偏空")]
        rows.append({
            "code": code, "old_net": o, "new_net": n,
            "old_dir": dir_of(o), "new_dir": dir_of(n),
            "old_verdict": sd.get("verdict"),
            "double_break": bool((sd.get("geometry") or {}).get("double_break")),
            "n_votes": len(votes), "counted": audit["counted"],
            "newly_counted": dead,
            "unclassified": audit["unclassified"],
        })
        mark = "  <<< 方向变了" if dir_of(o) != dir_of(n) else ""
        print("[%d/%d] %-4s 票=%-2d 计入=%-2d old=%+d(%s) new=%+d(%s)%s %s"
              % (k, len(codes), code, len(votes), audit["counted"],
                 o, dir_of(o), n, dir_of(n), mark,
                 ("新增计入:" + ",".join(dead)) if dead else ""), flush=True)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    changed = [r for r in rows if r["old_dir"] != r["new_dir"]]
    print("\n===== 汇总 =====")
    print("品种 %d；净票数值变化 %d；**方向翻转 %d**"
          % (len(rows), sum(1 for r in rows if r["old_net"] != r["new_net"]), len(changed)))
    for r in changed:
        print("  %-4s %+d(%s) → %+d(%s)" % (r["code"], r["old_net"], r["old_dir"],
                                             r["new_net"], r["new_dir"]))
    unc = [(r["code"], x) for r in rows for x in r["unclassified"]]
    print("未登记取值：%d" % len(unc))
    for c, x in unc[:10]:
        print("  ", c, x)
    print("[saved]", OUT)


if __name__ == "__main__":
    main()
