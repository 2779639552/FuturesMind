# -*- coding: utf-8 -*-
"""verify_spot_fallback.py — 现货源兜底 + 来源标注 的四层验证(第 3 层: test_client)。

【验证目标】生意社 100ppi 兜底接入后:
  A 覆盖度: 全池 20 品种的基差可取数个数应从 14 提升到 17(补 FU/PX/BZ)。
  B 符号口径: 两源 dom_basis 必须都是「现货 − 期货」(否则估值轴方向会反)。
  C 接口: /api/fund/meta 的逐品种 spot_source / 矩阵行的 basis_source_cn 均正确。
  D 页面: 来源标注元素存在, 且不存在"无现货指数"品种被误标为"取数失败"。

运行: venv/Scripts/python.exe -m probe.verify_spot_fallback
"""
from __future__ import annotations

import os
import sys

os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    print("=" * 74)
    print("A. 回退链与覆盖度(直调 _fetch_basis, 不走 HTTP)")
    print("=" * 74)
    import quant_module as Q

    expect_ppi = ["PX", "FU", "BZ"]
    # 【2026-09-30 更新】品种池由 20 扩至 21(新增 P 棕榈油), 断言改为动态对齐池,
    # 避免池扩容一次就要改测试一次。P 走国君(主路径), 但在 ppi_basis 里做了**冗余登记**,
    # 故这里单独校验"P 有兜底能力"(supports=True), 不代表它现在就走兜底。
    expect_gtja = ["CF", "M", "TA", "EG", "RU", "BU", "EB", "SI", "PS", "LC", "LH", "PG", "BR", "NR", "P"]
    expect_none = ["SC", "LU", "CJ"]

    got_ppi, got_gtja, got_none = [], [], []
    for c in expect_ppi + expect_gtja + expect_none:
        r = Q._fetch_basis(c, 180, batch=True)
        if r.get("ok"):
            (got_ppi if r.get("source") == "100ppi" else got_gtja).append(c)
        else:
            got_none.append(c)

    check(sorted(got_ppi) == sorted(expect_ppi), "100ppi 兜底命中 PX/FU/BZ",
          f"实得 {got_ppi}")
    # 【动态断言】不写死数量, 只校验"国君覆盖 = 池 - 兜底 - 无源"这一自洽关系
    check(len(got_gtja) == len(expect_gtja),
          f"国君覆盖 {len(got_gtja)} 品种(期望 {len(expect_gtja)})", f"{got_gtja}")
    check(sorted(got_none) == sorted(expect_none), "两源皆无 = SC/LU/CJ",
          f"实得 {got_none}")
    total = len(got_ppi) + len(got_gtja)
    _all = len(expect_ppi + expect_gtja + expect_none)
    check(total == _all - len(expect_none),
          f"现货覆盖 {total}/{_all}(应为池 - 无现货集合)", f"实得 {total}")

    print()
    print("=" * 74)
    print("B. 符号口径: 生意社取负后必须与国君一致(basis == spot − futures)")
    print("=" * 74)
    import ppi_basis

    for c in expect_ppi:
        rows = ppi_basis.basis_rows(c, 180)
        bad = [r for r in rows
               if r["spot_price"] is not None and r["futures_price"] is not None
               and r["basis"] is not None
               and abs((r["spot_price"] - r["futures_price"]) - r["basis"]) > 0.02]
        check(not bad, f"{c} 100ppi 取负后符号一致(spot-fut==basis)", f"{len(bad)} 行不符")
        # 若不加负号, 同一批数据应**大面积不符** —— 反证取负是必要的(防未来被人"顺手删掉")
        nobad = [r for r in rows
                 if r["spot_price"] is not None and r["futures_price"] is not None
                 and r["basis"] is not None
                 and abs((r["spot_price"] - r["futures_price"]) + r["basis"]) <= 0.02]
        check(len(nobad) == 0, f"{c} 反证: 不取负号则符号相反(故取负必要)", f"竟有 {len(nobad)} 行相符")
        if rows:
            last = rows[-1]
            side = "期货贴水(现货升水)" if last["basis"] > 0 else "期货升水(现货贴水)"
            print(f"       {c} 最新 {last['date']}: 现货 {last['spot_price']} / "
                  f"期货 {last['futures_price']} / 基差 {last['basis']:.2f} → {side}")

    print()
    print("=" * 74)
    print("C. 接口层(test_client): meta / matrix 的来源标注")
    print("=" * 74)
    from web_app import app

    client = app.test_client()

    r = client.get("/api/fund/meta")
    check(r.status_code == 200, "GET /api/fund/meta 200", f"实得 {r.status_code}")
    meta = r.get_json() or {}
    vs = {v["code"]: v for v in (meta.get("varieties") or [])}
    # 【动态断言】与品种池实际规模对齐; 不再写死具体数字(池扩容是常态)。
    check(len(vs) == len(expect_ppi + expect_gtja + expect_none),
          f"返回全部 {len(expect_ppi + expect_gtja + expect_none)} 个品种(实得 {len(vs)})")
    for c in expect_ppi:
        check(vs.get(c, {}).get("spot_source") == "gtja+100ppi",
              f"meta: {c} 标为 gtja+100ppi", f"实得 {vs.get(c, {}).get('spot_source')}")
    for c in expect_none:
        check(vs.get(c, {}).get("spot_source") == "none",
              f"meta: {c} 标为 none(无现货指数)", f"实得 {vs.get(c, {}).get('spot_source')}")
    # 【2026-09-30 口径拍板(用户确认)】spot_source 标注的是"**本次实际用了哪个源**",
    # 而非"该品种有哪些源可用"。故 CF/P 走国君即标 gtja, 不因"另有生意社覆盖"而标 +100ppi;
    # 兜底能力另由 spot_source_cn 的括号说明体现(如 P: "生意社 100ppi 可兜底")。
    # 只有 FU/PX/BZ 这类**国君确实缺、实际由生意社兜底**的才标 gtja+100ppi。
    check(vs.get("CF", {}).get("spot_source") == "gtja",
          "meta: CF 标为 gtja(实际走国君)", f"实得 {vs.get('CF', {}).get('spot_source')}")
    # 真正只在国君有的品种(100ppi 不收)才标纯 gtja —— NR 20号胶即此类
    check(vs.get("NR", {}).get("spot_source") == "gtja",
          "meta: NR 标为 gtja(100ppi 不收, 无兜底)", f"实得 {vs.get('NR', {}).get('spot_source')}")
    # P 为"国君正常但 100ppi 有覆盖"的冗余登记: 主标注仍 gtja, 括号里说明可兜底
    _p = vs.get("P", {})
    check(_p.get("spot_source") == "gtja", "meta: P 标为 gtja(实际走国君)",
          f"实得 {_p.get('spot_source')}")
    check("可兜底" in (_p.get("spot_source_cn") or ""),
          "meta: P 的说明里保留'可兜底'冗余信息", f"实得 {_p.get('spot_source_cn')}")
    check(sorted(meta.get("no_spot") or []) == sorted(expect_none),
          "meta.no_spot 列出 SC/LU/CJ", f"实得 {meta.get('no_spot')}")
    cov = {s["name"]: s["coverage"] for s in (meta.get("sources") or [])}
    # 【动态断言】覆盖度随池规模变化, 校验"N/M 结构 + 与池自洽"而非写死具体数字。
    import re as _re_cov
    _mm = _re_cov.match(r"^(\d+)/(\d+)$", cov.get("基差/现货价") or "")
    check(bool(_mm), "meta.sources 基差覆盖为 N/M 形式", f"实得 {cov.get('基差/现货价')}")
    if _mm:
        _n_ok, _n_all = int(_mm.group(1)), int(_mm.group(2))
        check(_n_all == len(vs) and _n_ok == _n_all - len(expect_none),
              f"基差覆盖 {_n_ok}/{_n_all} 与池({len(vs)})及无现货集合自洽")

    r = client.get("/api/fund/value-drive/matrix?refresh=1")
    check(r.status_code == 200, "GET /api/fund/value-drive/matrix 200", f"实得 {r.status_code}")
    mx = r.get_json() or {}
    rows = {x.get("variety"): x for x in (mx.get("rows") or [])}
    check(mx.get("scored", 0) >= 17,
          f"矩阵可评级 {mx.get('scored')} 个(原 14, 应>=17)", f"实得 {mx.get('scored')}")
    for c in expect_ppi:
        check(rows.get(c, {}).get("basis_source_cn", "").startswith("生意社"),
              f"矩阵: {c} basis_source_cn 标为生意社", f"实得 {rows.get(c, {}).get('basis_source_cn')}")
        check(rows.get(c, {}).get("vd_ok") is True, f"矩阵: {c} 现在可评级(vd_ok)")
    for c in expect_none:
        check(rows.get(c, {}).get("basis_source") == "none",
              f"矩阵: {c} basis_source=none(非'取数失败')",
              f"实得 {rows.get(c, {}).get('basis_source')}")

    r = client.get("/api/fund/strategy?variety=PX")
    check(r.status_code == 200, "GET /api/fund/strategy?variety=PX 200", f"实得 {r.status_code}")
    bs = (r.get_json() or {}).get("basis") or {}
    check(bs.get("ok") is True, "PX 策略卡的基差块现在有数据(原先空)")
    check(bs.get("source_cn") == "生意社 100ppi",
          "PX 基差块透传 source_cn", f"实得 {bs.get('source_cn')}")

    r = client.get("/api/fund/strategy?variety=SC")
    sc = ((r.get_json() or {}).get("basis") or {}) if r.status_code == 200 else {}
    check(sc.get("ok") is False, "SC 策略卡基差块仍为空(数据源边界, 非故障)")

    print()
    print("=" * 74)
    print("D. 页面层: 来源标注元素与文案")
    print("=" * 74)
    tpl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
    html = open(tpl, encoding="utf-8").read()
    check('id="f-source-badge"' in html, "页面存在 #f-source-badge(逐品种来源标注)")
    check("_renderFundSource" in html, "存在 _renderFundSource 方法")
    check("两源回退" in html, "页面解释了两源回退策略")
    check("生意社 100ppi" in html, "页面点名了生意社 100ppi")
    check("renderNoScore" in html, "矩阵有'未评级品种逐条点名'表")
    check("basis_source_cn" in html, "矩阵表渲染了'基差源'列")

    print()
    print("=" * 74)
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
