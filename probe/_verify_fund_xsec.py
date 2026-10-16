"""基本面多品种截面表 只读验收 —— 2026-10-16。

【只读】不改任何文件, 可反复运行。
【判据四层】
  A. 结构/契约：路由注册、字段齐全、量纲统一、id 完好、EOL 纯净、div 配平、内联 JS 语法
  B. 口径不新增：截面表复用的既有函数确实被调、比值只有一处算术、4 票 book 名与上游一致
  C. 月差别名修复：曾经失败的品种现在真的有数据（且区分"取值失败"与"确定无数据"）
  D. 真实数据抽查：全池覆盖 + 库存档位方向序 + 与 ④ 卡的虚实比**数值一致**

用法: python probe/_verify_fund_xsec.py
"""

import glob
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def ck(cond, what, detail=""):
    (PASS if cond else FAIL).append(what)
    print("  %s %s%s" % ("OK " if cond else "★  ", what, ("  [%s]" % detail) if detail else ""))
    return bool(cond)


def _depth(t: str):
    d = m0 = 0
    for m in re.finditer(r"<div\b|</div>", t):
        d += 1 if m.group(0) == "<div" else -1
        m0 = min(m0, d)
    return d, m0


def main() -> int:
    import fund_module as fm
    import quant_module as qm

    hs = (ROOT / "web_template.html").read_bytes().decode("utf-8")
    hb = (ROOT / "web_template.html").read_bytes()
    qs = (ROOT / "quant_module.py").read_text(encoding="utf-8")
    fs = (ROOT / "fund_module.py").read_text(encoding="utf-8")

    print("=== A. 结构 / 契约 ===")
    for fn in ("_vr_ratio_of", "_vr_ratio", "_fund_skill_votes", "_fund_xsection_one",
               "_fund_rank_all", "api_fund_ranking", "api_fund_contracts"):
        ck(fs.count("\ndef %s(" % fn) == 1, "fund_module 定义 %s 恰好一份" % fn)
    # ⚠ 判据要按**定义形式**写: 裸查 "_term_realtime_df " 会漏(它后面紧跟 "(", 不是空格)
    ck("_SINA_NAME_ALIASES: dict[str, list[str]] = {" in qs, "quant_module 有别名字典")
    ck("\ndef _term_realtime_df(" in qs, "quant_module 有 _term_realtime_df 定义")

    # 模板：新表容器各恰好一处
    for i in ("f-overview-view", "f-detail-view", "f-rk-table", "f-rk-rows", "f-rk-stats",
              "f-rk-metric", "f-rk-order", "f-rk-badge", "f-rk-note"):
        ck(hs.count('id="%s"' % i) == 1, "容器 #%s 恰好一处" % i, "实测 %d" % hs.count('id="%s"' % i))
    # 旧 id 一个没丢
    for i in ("f-variety", "f-strategy-body", "f-strategy-score", "f-votes-body", "f-cycle-body",
              "chart-f-inv", "f-inv-badge", "tab-fund-tools", "tab-warrant-tools", "wt-table"):
        ck(('id="%s"' % i) in hs, "既有 id 保留: #%s" % i)
    ck(hs.count('colspan="19"') >= 2, "19 列占位行在位", "实测 %d" % hs.count('colspan="19"'))

    c2, b2 = hb.count(b"\r\n"), hb.count(b"\n") - hb.count(b"\r\n")
    ck(c2 > 0 and b2 == 0, "web_template.html 纯 CRLF", "CRLF=%d bareLF=%d" % (c2, b2))
    for n, byt in (("quant_module.py", (ROOT / "quant_module.py").read_bytes()),
                   ("fund_module.py", (ROOT / "fund_module.py").read_bytes())):
        ck(byt.count(b"\r\n") == 0, "%s 纯 LF" % n)

    _bks = sorted(glob.glob(str(ROOT / "backup" / "web_template_pre_fundxsec_*.html")))
    if _bks:
        d0, m0 = _depth(Path(_bks[-1]).read_bytes().decode("utf-8"))
        d1, m1 = _depth(hs)
        ck(d1 == d0, "div 深度**差值**与改动前一致", "改前 %+d → 改后 %+d" % (d0, d1))
        ck(m1 >= m0, "div 最低深度未变差", "%d → %d" % (m0, m1))

    js = max(re.finditer(r"<script>(.*?)</script>", hs, re.S), key=lambda m: len(m.group(1))).group(1)
    tmp = ROOT / "probe" / "_verify_fx_inline.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    ck(r.returncode == 0, "内联 JS 通过 node --check", (r.stderr or "")[:150])

    print()
    print("=== B. 口径不新增（本表与各卡必然一致）===")
    route = fs[fs.index('@fund_bp.route("/api/fund/virtual-real")'):]
    ck("_vr_ratio_of(" in route, "④ 卡路由调 _vr_ratio_of（比值只有一处算术）")
    ck("/ float(inv_out[\"lots\"])" not in route, "④ 卡路由无内联比值算术")
    ck(fs.count("oi_f / lots_f") == 1, "比值除法全局仅一处", "实测 %d" % fs.count("oi_f / lots_f"))
    body = fs[fs.index("def _fund_xsection_one("):fs.index("def _fund_rank_all(")]
    for need in ("q._inv_dual(", "_vr_ratio(", "_profit_summary(", "_strategy_verdict("):
        ck(need in body, "截面行复用 %s" % need)
    ck("_grade(" not in body, "截面行未自算档位")
    sv = fs[fs.index("def _fund_skill_votes("):fs.index("def _fund_xsection_one(")]
    ck("qv.build_verdict(" in sv, "4 票调 quant_verdict.build_verdict（同源）")
    ck(not re.search(r"value_drive_matrix\(|term_structure_regime\(|inventory_cycle_phase\(", sv),
       "4 票未在本地重算判据")

    qv = (ROOT / "quant_verdict.py").read_text(encoding="utf-8")
    for _k, book in fm._FUND_VOTE_BOOKS:
        ck(('"book": "%s"' % book) in qv, "book 名与上游一致: %s" % book)
    for _k, pref in fm._FUND_GAP_PREFIX.items():
        ck(pref.rstrip(":") in qv, "缺口前缀与上游一致: %s" % pref.rstrip(":"))

    print()
    print("=== C. 月差别名修复（曾失败品种现有数据）===")
    for code in ("PX", "FU", "P", "PG"):
        ck(code in qm._SINA_NAME_ALIASES, "%s 在别名表中" % code)
    tbody = qs[qs.index("def _fetch_term_structure("):]
    tbody = tbody[:tbody.index("\ndef ", 10)]
    ck("_term_realtime_df(" in tbody, "_fetch_term_structure 走候选名重试")
    ck("ak.futures_zh_realtime(symbol=name)" not in tbody, "未直接传项目名")
    # 全池月差覆盖（联网）
    ok, fail, empty = [], [], []
    for code in sorted(__import__("tradingagents.dataflows.commodity_futures",
                                  fromlist=["ACTIVE_VARIETIES"]).ACTIVE_VARIETIES):
        t = qm._fetch_term_structure(code)
        (ok if t.get("ok") else fail).append(code)
    ck(len(ok) >= 20, "月差覆盖 ≥20/21", "%d/21 ok; 失败=%s" % (len(ok), ",".join(fail) or "无"))

    print()
    print("=== D. 真实数据抽查 ===")
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES
    rows = fm._fund_rank_all(sorted(ACTIVE_VARIETIES), force=True)
    ck(len(rows) == 21, "全池 21 行", str(len(rows)))
    # 量纲：三种分位都是 0~1
    bad = [(r["variety"], k, r.get(k)) for r in rows for k in
           ("basis_pct", "ck_pct", "soc_pct")
           if r.get(k) is not None and not (0 <= r[k] <= 1)]
    ck(not bad, "三种分位统一为 0~1 比率", str(bad[:3]) if bad else "无越界")
    # 库存档位方向序
    g2n = {"低": 1, "中": 0, "高": -1}
    bad2 = [r["variety"] for r in rows if r.get("inv_grade") in g2n
            and r.get("inv_rank") != g2n[r["inv_grade"]]]
    ck(not bad2, "库存档位方向序(低=+1 高=-1)", ",".join(bad2) or "无错")
    ck(all(r.get("inv_mode") for r in rows), "每行都带口径关系")
    ck(all(r.get("strategy") for r in rows), "每行都带综合结论")
    n4 = sum(1 for r in rows if r.get("skill_n") == 4)
    ck(n4 >= 10, "4 票齐备的品种 ≥10", "%d/21" % n4)
    # 票的取值必须来自方法论/研判文件（同源）
    srcs = ((ROOT / "quant_methodology.py").read_text(encoding="utf-8"), qv)
    drift = [(r["variety"], k, r.get(k)) for r in rows
             for k in ("skill_vd", "skill_term", "skill_cyc", "skill_prof")
             if r.get(k) and not any((f'"{r[k]}"' in s) or (f"'{r[k]}'" in s) for s in srcs)]
    ck(not drift, "4 票取值均同源于方法论/研判文件", str(drift[:2]) if drift else "无漂移")

    # ★ 与 ④ 卡**数值一致**（口径不漂移的最终证明）
    print()
    print("=== D2. 截面表 vs ④ 卡：虚实盘比数值必须相等 ===")
    try:
        import os
        os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
        import web_app
        cli = web_app.app.test_client()
        for code in ("TA", "CF"):
            rv = (cli.get("/api/fund/virtual-real?variety=%s" % code).get_json() or {}).get("ratio")
            rx = next((x.get("vr_ratio") for x in rows if x["variety"] == code), None)
            ck(rv is not None and rx is not None and abs(rv - rx) < 1e-6,
               "%s 虚实比两处相等" % code, "④卡=%s 截面=%s" % (rv, rx))
    except Exception as e:  # noqa: BLE001
        ck(False, "虚实比数值一致性检查执行完成", "%s: %s" % (type(e).__name__, e))

    print()
    print("=" * 62)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:")
        for x in FAIL:
            print("  ★ " + x)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
