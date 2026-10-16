"""probe/verify_dashboard_merge_fund.py — 「看板并入 + 基本面量化」端到端验收脚本

【跑法】(在 D:\\AgentSense\\AgentSense 下)
    set AGENTSENSE_AUTH_DISABLE=1 && python probe\\verify_dashboard_merge_fund.py

【验什么】四层里的第 3 层(接口层), 用 Flask test_client 直接打路由, 不起服务、不动线上进程:
  A. 页面层: GET / 必须含 tab-fund / 已无 tab-dashboard; nav 有 8 个 tab; 无孤儿 id 残留
  B. 基本面量化 4 个子模块: meta / strategy / value-drive / profit / virtual-real 全部 200
  C. 关键口径回归(★不通过视为缺陷):
     C1 棉花 CF 的虚实盘比换算系数必须为 8(郑商所「张」→「手」的地基, 不换算会高估 8 倍)
     C2 原油 SC 的换算系数必须为 0.001(上期所「桶」÷1000 桶/手, 不换算会低估 1000 倍)
     C3 基差符号: 基差 = 现货 − 期货 ⇒ >0 必须翻译成「期货贴水」(不得输出裸「贴水」)
     C4 池外品种必须 400(不是 500, 也不是静默空数据)
  D. 看板并入部分: /api/dashboard/{v} 与 /api/dashboard/sector/{sector} 仍可用(迁入卡的数据源)

【退出码】0=全部通过; 1=有断言失败(打印明细)。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSES.append(name)
        print(f"  PASS  {name}")
    else:
        FAILS.append(f"{name} — {detail}")
        print(f"  FAIL  {name} — {detail}")


def main() -> int:
    from web_app import app  # noqa: E402  延迟导入(需要 cwd 在项目根)

    app.config["TESTING"] = True
    c = app.test_client()

    # ── A. 页面层 ────────────────────────────────────────────────────────────
    print("\n[A] 页面层")
    r = c.get("/")
    html = r.get_data(as_text=True)
    check("GET / 200", r.status_code == 200, f"HTTP {r.status_code}")
    check("nav 含 基本面量化(data-tab=fund)", 'data-tab="fund"' in html)
    check("已无 数据看板 tab 按钮", 'data-tab="dashboard"' not in html)
    check("含 tab-fund 容器", 'id="tab-fund"' in html)
    check("已无 tab-dashboard 容器", 'id="tab-dashboard"' not in html)
    check("量化页含迁入卡 #q-analysis", 'id="q-analysis"' in html)
    check("量化页含迁入卡 #chart-q-pi", 'id="chart-q-pi"' in html)
    check("量化页含迁入卡 #q-sector", 'id="q-sector"' in html)
    check("量化页含迁入卡 #q-research", 'id="q-research"' in html)
    # 看板孤儿 id 必须清零(残留 = 静默失败风险)
    orphans = [i for i in ("dash-variety", "dash-days", "dash-analysis-body", "dash-badge",
                           "dash-research", "dash-margin", "dash-sector-panel",
                           "chart-dash-pi", "chart-dash-basis", "chart-dash-inv-chg") if f'id="{i}"' in html]
    check("看板孤儿 id 清零", not orphans, f"残留 {orphans}")

    # ── B. 基本面量化接口 ────────────────────────────────────────────────────
    print("\n[B] 基本面量化接口")
    r = c.get("/api/fund/meta")
    check("GET /api/fund/meta 200", r.status_code == 200, f"HTTP {r.status_code}")
    meta = r.get_json() or {}
    check("meta.ok 且品种池 20", bool(meta.get("ok")) and len(meta.get("varieties") or []) == 20,
          f"ok={meta.get('ok')} n={len(meta.get('varieties') or [])}")
    check("meta 有 4 个子模块", len(meta.get("modules") or []) == 4, f"{len(meta.get('modules') or [])}")

    r = c.get("/api/fund/strategy?variety=CF")
    check("GET /api/fund/strategy 200", r.status_code == 200, f"HTTP {r.status_code}")
    st = r.get_json() or {}
    if st.get("ok"):
        print(f"        CF 策略: {st.get('verdict', {}).get('action')} / "
              f"库存={st.get('inv', {}).get('grade')} 利润={st.get('profit', {}).get('grade')} "
              f"基差={st.get('basis', {}).get('side_cn')}")
        v = st.get("verdict") or {}
        check("strategy.verdict 有 action/reason", bool(v.get("action")) and bool(v.get("reason")))
    else:
        check("strategy 返回 ok", False, str(st.get("error")))

    r = c.get("/api/fund/value-drive?variety=CF")
    check("GET /api/fund/value-drive 200", r.status_code == 200, f"HTTP {r.status_code}")
    vd = r.get_json() or {}
    if vd.get("ok"):
        print(f"        CF 四象: value={vd.get('value')} drive={vd.get('drive')} "
              f"{vd.get('quadrant')} → {vd.get('advice')}")
        if vd.get("vd_ok"):
            check("value-drive 估值轴符号(深贴水应得负分)",
                  not (vd.get("evidence", {}).get("basis_rate", 0) > 0 and (vd.get("value") or 0) > 0),
                  f"基差率={vd.get('evidence', {}).get('basis_rate')} value={vd.get('value')}")
    else:
        check("value-drive 返回 ok", False, str(vd.get("error")))

    r = c.get("/api/fund/profit?variety=CF")
    check("GET /api/fund/profit 200", r.status_code == 200, f"HTTP {r.status_code}")
    pf = r.get_json() or {}
    if pf.get("ok"):
        print(f"        CF 利润: {[(s['label'], s['grade']) for s in (pf.get('series') or [])]} 汇总={pf.get('grade')}")
        # 多口径必须分色并列(不得合并成一条)
        labs = [s.get("label") for s in (pf.get("series") or [])]
        check("profit 口径不合并(series 条数 == 唯一 label 数)",
              len(labs) == len(set(labs)), f"labels={labs}")
    else:
        check("profit 返回 ok", False, str(pf.get("error")))

    # ── C. 关键口径回归 ──────────────────────────────────────────────────────
    print("\n[C] 关键口径回归")
    r = c.get("/api/fund/virtual-real?variety=CF")
    check("GET /api/fund/virtual-real 200", r.status_code == 200, f"HTTP {r.status_code}")
    vr = r.get_json() or {}
    if vr.get("ok"):
        inv = vr.get("inv") or {}
        print(f"        CF 虚实盘比={vr.get('ratio')} 系数={inv.get('lot_factor')} "
              f"({inv.get('unit')}) 依据={inv.get('factor_basis')}")
        print(f"        CF 判定: {vr.get('verdict')} | 距交割 {vr.get('months_to_delivery')} 月")
        if inv.get("ok"):
            # C1 ★ 棉花: 郑商所「张」, spec「8手合约*5吨/手=40吨」⇒ 40÷5 = 8
            check("C1 棉花仓单换算系数 == 8", abs(float(inv.get("lot_factor") or 0) - 8.0) < 1e-9,
                  f"实测 {inv.get('lot_factor')} (应为 8; 不等于 8 则虚盘比高估 8 倍)")
        else:
            check("C1 棉花仓单可用", False, str(inv.get("error")))
    else:
        check("virtual-real 返回 ok", False, str(vr.get("error")))

    r = c.get("/api/fund/virtual-real?variety=SC")
    sc = r.get_json() or {}
    if sc.get("ok") and (sc.get("inv") or {}).get("ok"):
        f = float((sc.get("inv") or {}).get("lot_factor") or 0)
        print(f"        SC 虚实盘比={sc.get('ratio')} 系数={f} 依据={(sc.get('inv') or {}).get('factor_basis')}")
        # C2 ★ 原油: 上期所能源中心「桶」, 1000 桶/手 ⇒ 1÷1000 = 0.001
        check("C2 原油仓单换算系数 == 0.001", abs(f - 0.001) < 1e-12,
              f"实测 {f} (应为 0.001; 不等于则虚盘比低估 1000 倍)")
    else:
        print(f"        (SC 仓单不可用, 跳过 C2: {(sc.get('inv') or {}).get('error')})")

    # C3 基差符号文案: 凡出现「贴水/升水」必须带主体词
    bad_cases = []
    for code in ("CF", "TA", "RU", "M"):
        rr = c.get(f"/api/fund/strategy?variety={code}")
        dd = rr.get_json() or {}
        bs = dd.get("basis") or {}
        if not bs.get("ok"):
            continue
        side_now = bs.get("side_cn") or ""
        latest = bs.get("latest")
        # 符号 → 文案 的映射必须一致: >0 ⇒ 期货贴水
        expect = "期货贴水" if (latest or 0) > 0 else ("期货升水" if (latest or 0) < 0 else "平水")
        if expect != "平水" and expect not in side_now:
            bad_cases.append(f"{code}: 基差={latest} 文案={side_now} 期望含「{expect}」")
    check("C3 基差符号→文案映射一致(>0=期货贴水)", not bad_cases, " | ".join(bad_cases))

    # C4 池外品种 400
    r = c.get("/api/fund/strategy?variety=RB")
    check("C4 池外品种 400(RB)", r.status_code == 400, f"HTTP {r.status_code}")
    r = c.get("/api/fund/virtual-real?variety=ZZZ")
    check("C4 非法品种 400(ZZZ)", r.status_code == 400, f"HTTP {r.status_code}")

    # ── D. 看板并入的数据源仍可用 ────────────────────────────────────────────
    print("\n[D] 看板并入数据源")
    r = c.get("/api/dashboard/CF?days=180")
    check("GET /api/dashboard/CF 200", r.status_code == 200, f"HTTP {r.status_code}")
    db = r.get_json() or {}
    check("dashboard 返回 price 与 _meta", bool(db.get("price")) and bool(db.get("_meta")),
          f"price={len(db.get('price') or [])}")
    an = db.get("analysis") or {}
    print(f"        CF 关联: R={an.get('price_inventory_r')} 库存趋势={an.get('inventory_trend')} "
          f"基差率={an.get('basis_rate_latest')}")
    r = c.get("/api/dashboard/sector/%E8%83%BD%E5%8C%96")
    check("GET /api/dashboard/sector/能化 200", r.status_code == 200, f"HTTP {r.status_code}")
    sec = r.get_json() or {}
    print(f"        能化板块: {sec.get('count')} 品种有效, 前 3 = "
          f"{[(x.get('code'), x.get('r')) for x in (sec.get('rows') or [])[:3]]}")

    # 矩阵(限 4 品种, 避免全池十秒级)
    r = c.get("/api/fund/value-drive/matrix?varieties=CF,TA,M,RU")
    check("GET /api/fund/value-drive/matrix 200", r.status_code == 200, f"HTTP {r.status_code}")
    mx = r.get_json() or {}
    print(f"        矩阵: {mx.get('scored')}/{mx.get('count')} 可评级")
    for row in (mx.get("rows") or []):
        print(f"          {row.get('variety'):>3} value={row.get('value')} drive={row.get('drive')} {row.get('quadrant')}")

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(f"通过 {len(PASSES)} 项, 失败 {len(FAILS)} 项")
    if FAILS:
        print("\n失败明细:")
        for f in FAILS:
            print("  -", f)
    print("=" * 62)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
