# -*- coding: utf-8 -*-
"""全池对照：净票 vs 加权分。哪里一致、哪里打架、各轴怎么投的。

【为什么值得看】L1b 的契约是"并列，不替代"。切换之前必须先看清：
  两套在多少个品种上结论不同、不同的时候是谁更靠近常识。
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:5000"
OUT = os.path.join(ROOT, "probe", "_l1b_compare.json")


def gj(url, timeout=180):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    pool = gj(BASE + "/api/varieties")
    codes = pool if isinstance(pool, list) else pool.get("varieties") or []
    # 该接口返回 [{code,name,exchange,...}]，不是纯字符串列表
    codes = [c.get("code") if isinstance(c, dict) else c for c in codes]
    codes = [c for c in codes if c]
    rows = []
    print("%-4s %-6s %-6s %-5s %-8s %s" % ("品种", "净票", "净票向", "加权", "加权向", "各轴(方向×证据量)"))
    for code in codes:
        try:
            d = gj(BASE + "/api/quant/verdict?variety=" + code)
        except Exception as e:  # noqa: BLE001
            print("%-4s FAIL %s" % (code, e))
            continue
        sd = ((d.get("stages") or {}).get("direction")) or {}
        w = sd.get("weighted") or {}
        net = (sd.get("vote_audit") or {}).get("net")
        if net is None:
            continue
        axes = w.get("axes") or {}
        # ★ 不能用首字母：price 与 profit 都是 P，表里会看不出谁是谁
        _lb = {"price": "技", "basis": "基", "inventory": "库", "profit": "利"}
        axes_txt = " ".join("%s%+.2f×%.2f" % (_lb.get(k, k), a["dir"], a["conv"])
                            for k, a in sorted(axes.items()))
        rows.append({"code": code, "net": net, "net_dir": sd.get("verdict"),
                     "score": w.get("score"), "w_dir": w.get("direction"),
                     "axes": axes, "unattributed": w.get("unattributed")})
        print("%-4s %+6d %-6s %+6.3f %-8s %s"
              % (code, net, sd.get("verdict"), w.get("score") or 0, w.get("direction"), axes_txt))

    json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 统计"打架"的情况：净票方向 vs 加权方向
    def norm(v):
        return {"做多": 1, "做空": -1}.get(v, 0)
    diff = [r for r in rows if norm(r["net_dir"]) != norm(r["w_dir"])]
    print("\n品种 %d；方向不一致 %d（%.0f%%）" % (len(rows), len(diff), 100 * len(diff) / max(1, len(rows))))
    for r in diff:
        print("  %-4s 净票 %+d(%s) vs 加权 %+.3f(%s)"
              % (r["code"], r["net"], r["net_dir"], r["score"], r["w_dir"]))
    bad = [r for r in rows if r["unattributed"]]
    print("有未归属票的品种：%s" % (bad or "无"))
    print("[saved]", OUT)


if __name__ == "__main__":
    main()
