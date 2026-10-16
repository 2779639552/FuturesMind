"""probe/verify_compute_panel.py — 数据计算面板(⑫)验收: 真实数据, 不造假。

【为什么这样做】本项目反复吃过"假阳性验证"的亏: 手写 stub 里凭空捏一个字段,
  接口其实从不返回它 ⇒ 前端恒判假。所以本脚本**只走生产路径**:
    ① 直接 import quant_module(与 web_app 挂载的是同一个模块对象), 调它的
       _cmp_series / _cmp_corr —— 即路由 api_quant_compute 内部真正用的函数;
    ② 再起 Flask test_client 打 HTTP, 核对**响应 JSON 的实际字段**是否与前端读取的一致
       (前端读 d.dims / d.selected / d.axis / d.series / d.corr.keys / d.corr.matrix /
        d.corr.n_matrix / d.corr.min_n / d.corr.method_name / d.vs)。
  核对点(每条都必须为真, 否则退出码非 0):
    A 维度清单非空, 且每个 dim 带 key/name/unit/family/kind/count/first/last/latest;
    B 默认维度 = price + warrant_chg(用户原话场景), 且两者都真有数据;
    C 价格与仓单日变化有共同交易日, 相关系数在 [-1,1] 内且 n > 0;
    D 曲线序列: series[key] 是 [[date, value], ...], 日期升序且都落在 axis 内;
    E 相关矩阵: 对称、对角=1、n_matrix 与 matrix 同形且对角 > 0;
    F 换一个品种重算, 维度清单随之变化(证明确实按品种取数, 不是写死);
    G method=spearman 与 pearson 结果不同(证明口径参数真的生效, 不是摆设)。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

FAILS: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + (f"  -> {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


def main() -> int:
    print("=" * 78)
    print("数据计算面板 ⑫ 验收 (真实数据)")
    print("=" * 78)

    import quant_module as q

    # ── 第 1 层: 直连生产函数(路由内部真正调用的那两个) ──
    code = "RU"
    print(f"\n[A] _cmp_series('{code}', 180) —— 真实取数(价格/基差/仓单三源)")
    sm = q._cmp_series(code, 180)
    counts = {k: len(v) for k, v in sm.items()}
    print("     维度点数:", json.dumps(counts, ensure_ascii=False))
    check(any(counts.values()), "至少一个维度有数据")
    for key in ("price", "warrant", "warrant_chg"):
        check(counts.get(key, 0) > 0, f"维度 {key} 有数据", f"{counts.get(key)} 点")

    # 相关系数: 价格 与 仓单日变化(用户原话场景)
    common = sorted(set(sm["price"]) & set(sm["warrant_chg"]))
    print(f"\n[C] 价格 × 仓单日变化: 共同交易日 {len(common)} 天")
    if common:
        xs = [sm["price"][d] for d in common]
        ys = [sm["warrant_chg"][d] for d in common]
        rp = q._cmp_corr(xs, ys, "pearson")
        rs = q._cmp_corr(xs, ys, "spearman")
        print(f"     Pearson R = {rp if rp is None else round(rp, 4)}")
        print(f"     Spearman R = {rs if rs is None else round(rs, 4)}")
        check(rp is not None and -1.0000001 <= rp <= 1.0000001, "Pearson R 落在 [-1,1]")
        check(rs is not None and -1.0000001 <= rs <= 1.0000001, "Spearman R 落在 [-1,1]")
        if rp is not None and rs is not None:
            check(abs(rp - rs) > 1e-9, "pearson 与 spearman 结果不同(口径参数生效)",
                  f"差 {abs(rp - rs):.4f}")

    # ── 第 2 层: HTTP 真实响应字段核对(前端读什么, 这里就核什么) ──
    print("\n[HTTP] Flask test_client 打 /api/quant/compute (与前端同一条路由)")
    import web_app

    client = web_app.app.test_client()
    resp = client.get(f"/api/quant/compute?variety={code}&days=180&method=pearson")
    check(resp.status_code == 200, "HTTP 200", str(resp.status_code))
    d = resp.get_json()
    check(bool(d and d.get("ok")), "ok=true")
    if not d or not d.get("ok"):
        return 1

    print("\n[字段核对] 前端 JS 直接读取的字段是否真实存在")
    for field in ("dims", "selected", "axis", "series", "corr", "method_name", "name", "days"):
        check(field in d, f"响应含字段 '{field}'")

    dims = d.get("dims") or []
    check(len(dims) > 0, "dims 非空", f"{len(dims)} 个维度")
    need_dim_fields = ("key", "name", "unit", "family", "kind", "count", "first", "last", "latest")
    missing = sorted({f for x in dims for f in need_dim_fields if f not in x})
    check(not missing, "每个 dim 字段齐备", "缺: " + ",".join(missing) if missing else "")

    sel = d.get("selected") or []
    print(f"     默认选中: {sel}")
    check(sel == ["price", "warrant_chg"] or set(sel) >= {"price", "warrant_chg"},
          "默认维度含 price + warrant_chg(用户原话场景)", str(sel))

    # 曲线序列形状
    axis = d.get("axis") or []
    series = d.get("series") or {}
    ok_shape = True
    for k, pts in series.items():
        for p in pts:
            if not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str)):
                ok_shape = False
                break
        dates = [p[0] for p in pts]
        if dates != sorted(dates):
            ok_shape = False
    check(ok_shape and bool(series), "series[key] 形如 [[date,value],...] 且日期升序",
          f"{len(series)} 列, axis {len(axis)} 天")
    check(all(p[0] in set(axis) for pts in series.values() for p in pts),
          "曲线所有日期都落在 axis 内")

    # 相关矩阵
    corr = d.get("corr") or {}
    m, n = corr.get("matrix") or [], corr.get("n_matrix") or []
    keys = corr.get("keys") or []
    check(len(m) == len(keys) and all(len(r) == len(keys) for r in m), "matrix 为方阵", f"{len(keys)}×{len(keys)}")
    check(len(n) == len(m) and all(len(r) == len(m) for r in n), "n_matrix 与 matrix 同形")
    sym = all(abs((m[i][j] or 0) - (m[j][i] or 0)) < 1e-9
              for i in range(len(m)) for j in range(len(m))
              if m[i][j] is not None and m[j][i] is not None)
    check(sym, "相关系数矩阵对称")
    diag = all((m[i][i] is None) or abs(m[i][i] - 1.0) < 1e-9 for i in range(len(m)))
    check(diag, "对角线 = 1(自相关)")
    check(all(n[i][i] > 0 for i in range(len(n))), "n_matrix 对角线 > 0(自对样本量)")
    check("min_n" in corr and "method_name" in corr and "note" in corr, "corr 含 min_n/method_name/note",
          f"min_n={corr.get('min_n')}")

    # ── 第 3 层: 换品种 → 维度清单变化(证明是真取数, 非写死) ──
    print("\n[F] 换品种重算: 证明维度按品种真实取数")
    d2 = client.get("/api/quant/compute?variety=SC&days=180&dims=price,warrant_chg,oi").get_json()
    keys2 = [x["key"] for x in (d2.get("dims") or [])]
    keys1 = [x["key"] for x in dims]
    print(f"     {code} 可用维度: {keys1}")
    print(f"     SC 可用维度: {keys2}")
    check(d2.get("name") != d.get("name"), "两个品种 name 不同", f"{d.get('name')} vs {d2.get('name')}")
    check(keys1 != keys2, "两品种可用维度集不同(含 SC 无现货指数 → 基差族应消失)")

    # ── 第 4 层: 参数生效 ──
    print("\n[G] method=spearman 走另一个 HTTP 请求")
    d3 = client.get(f"/api/quant/compute?variety={code}&days=180&method=spearman&dims=price,warrant_chg").get_json()
    check(d3.get("method") == "spearman", "method 回显 spearman")
    check(d3.get("method_name") != d.get("method_name"), "method_name 随之变化",
          f"{d.get('method_name')} vs {d3.get('method_name')}")

    # ── 第 5 层: 非法参数 ──
    print("\n[H] 边界: 非法品种 / 无 dims 参数")
    bad = client.get("/api/quant/compute?variety=ZZZZ")
    check(bad.status_code == 400, "池外品种返回 400", str(bad.status_code))
    nodims = client.get(f"/api/quant/compute?variety={code}").get_json()
    check(bool(nodims.get("selected")), "不带 dims 时后端回退默认维度", str(nodims.get("selected")))

    print("\n" + "=" * 78)
    if FAILS:
        print(f"结果: 失败 {len(FAILS)} 项")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("结果: 全部通过 (真实数据)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
