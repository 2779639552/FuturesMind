# -*- coding: utf-8 -*-
"""把四家交易所的采集结果整理成「仓单分析」板块直接可用的 JSON。

产出（data/warrant/web/）：
  overview.json —— 总览：交易所汇总 + 全部品种的仓单量与当日增减（排名用）
  detail.json   —— 细则：按 "交易所|品种代码" 索引，含四个模块的数据
                   1) warehouses 仓库数量分布
                   2) brands     品牌结构（无品牌数据的交易所为空数组）
                   3) types      仓库/厂库占比
                   4) changes    当日增减结构（按仓库，涨跌分列）
                   另附 regions（地区结构，上期所有）/ subs（分库结构，大商所有）

【口径说明】品种级数量一律取交易所自报合计行；仓库级取各所日报的仓库行。
品牌维度只有郑商所（xlsx「品牌」列）与广期所（trademarkName）官方公布，
大商所、上期所日报不含品牌 —— 对应交易所 brand_support=False，前端如实提示。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict

from .common import OUT_DIR, WEB_DIR, ensure_dir, log
from .unify import BRAND_SUPPORT, EXCHANGE_CN

ORDER = ["CZCE", "DCE", "GFEX", "SHFE"]
UNIT_DEFAULT = {"CZCE": "张", "DCE": "手", "GFEX": "手", "SHFE": "吨"}


# ── 小工具 ────────────────────────────────────────────────────────────────
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _i(v):
    """数量统一成 int（交易所原始多为浮点，但都是整数量级）"""
    f = _num(v)
    if f is None:
        return None
    return int(f) if float(f).is_integer() else round(f, 4)


def _agg(rows, key_fn, qty_key="qty", chg_key="change"):
    """按 key 聚合数量与增减 → {key: [qty, change]}"""
    acc = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        k = key_fn(r)
        if k is None or k == "":
            continue
        q = _num(r.get(qty_key))
        if q is not None:
            acc[k][0] += q
        c = _num(r.get(chg_key))
        if c is not None:
            acc[k][1] += c
    return acc


def _sort_desc(acc, limit=None):
    items = sorted(acc.items(), key=lambda kv: -kv[1][0])
    if limit:
        items = items[:limit]
    return items


def _brand_available(name: str, fallback: str) -> str:
    return (name or "").strip() or fallback


# ── 各交易所细则组装 ──────────────────────────────────────────────────────
def _detail_czce(res: dict) -> dict:
    """郑商所：仓库级用 summary（小计行），品牌级用 detail（逐品牌登记）"""
    whs, brands, regions = [], [], []
    by_prod_wh = defaultdict(list)
    by_prod_brand = defaultdict(list)

    for r in res.get("summary", []):
        by_prod_wh[r.get("product_code")].append({
            "no": r.get("warehouse_no") or "",
            "name": r.get("warehouse_name") or "",
            "type": "厂库" if "厂库" in (r.get("warehouse_name") or "") else "仓库",
            "qty": _i(r.get("qty")), "change": _i(r.get("change")),
        })
    for r in res.get("detail", []):
        # 只取仓单表（kind=warrant）；「机构编号表」是有效预报，不能混入品牌统计
        if (r.get("kind") or "warrant") != "warrant":
            continue
        b = (r.get("brand") or "").strip()
        if not b:
            continue
        by_prod_brand[r.get("product_code")].append({
            "name": b, "wh": r.get("warehouse_name") or "",
            "qty": _num(r.get("qty")), "change": _num(r.get("change")),
        })
    return {"wh": by_prod_wh, "brand": by_prod_brand, "region": {}, "sub": {}}


def _detail_gfex(res: dict) -> dict:
    by_prod_wh, by_prod_brand = defaultdict(list), defaultdict(list)
    for r in res.get("detail", []):
        code = r.get("variety_code")
        by_prod_wh[code].append({
            "no": r.get("warehouse_code") or "",
            "name": r.get("warehouse_name") or "",
            "type": r.get("warehouse_type") or "仓库",
            "qty": _i(r.get("qty")), "change": _i(r.get("change")),
        })
        b = (r.get("trademark") or "").strip()
        if b:
            by_prod_brand[code].append({
                "name": b, "wh": r.get("warehouse_name") or "",
                "qty": _num(r.get("qty")), "change": _num(r.get("change")),
            })
    return {"wh": by_prod_wh, "brand": by_prod_brand, "region": {}, "sub": {}}


def _detail_dce(res: dict) -> dict:
    """大商所：无品牌。分库（whCodeOrder 带下划线）作为结构性替代视图。"""
    by_prod_wh, by_prod_sub = defaultdict(list), defaultdict(list)
    for r in res.get("detail", []):
        by_prod_wh[r.get("variety_code")].append({
            "no": r.get("warehouse_code") or "",
            "name": r.get("warehouse_name") or "",
            "type": r.get("warehouse_type") or "仓库",
            "qty": _i(r.get("qty")), "change": _i(r.get("change")),
        })
    for r in res.get("sub", []) or []:
        by_prod_sub[r.get("variety_code")].append({
            "name": r.get("warehouse_name") or "",
            "type": r.get("warehouse_type") or "仓库",
            "qty": _i(r.get("qty")), "change": _i(r.get("change")),
        })
    return {"wh": by_prod_wh, "brand": {}, "region": {}, "sub": by_prod_sub}


def _detail_shfe(res: dict) -> dict:
    """上期所：无品牌。有地区字段，作为结构性替代视图。"""
    by_prod_wh = defaultdict(list)
    for r in res.get("detail", []):
        by_prod_wh[r.get("product_code")].append({
            "no": r.get("warehouse_no") or "",
            "name": r.get("warehouse_name") or "",
            "type": r.get("warehouse_type") or "仓库",
            "region": (r.get("region") or "").strip(),
            "qty": _i(r.get("qty")), "change": _i(r.get("change")),
        })
    return {"wh": by_prod_wh, "brand": {}, "region": "AUTO", "sub": {}}


_BUILDERS = {"CZCE": _detail_czce, "GFEX": _detail_gfex,
             "DCE": _detail_dce, "SHFE": _detail_shfe}


# ── 主入口 ────────────────────────────────────────────────────────────────
def build(results: dict, date_str: str | None = None) -> dict:
    """results: {交易所代码: collect() 的返回}；返回写出的文件路径"""
    ensure_dir(WEB_DIR)
    date_str = date_str or max(r["date"] for r in results.values())
    # 兼容两种入参：命令行给的 YYYYMMDD 与 collect() 返回的 YYYY-MM-DD
    if "-" not in date_str and len(date_str) == 8:
        date_str = "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:])

    ex_meta, products, detail = [], [], {}
    for ex in ORDER:
        res = results.get(ex)
        if not res:
            continue
        prod_rows = res.get("product", []) or []
        unit = UNIT_DEFAULT.get(ex, "")
        # 单位构成：上期所同时用「吨/千克/桶」，不能把总量直接相加成一个数
        umix = defaultdict(lambda: [0.0, 0])
        for p in prod_rows:
            u = p.get("unit") or unit
            umix[u][0] += _num(p.get("qty")) or 0
            umix[u][1] += 1
        unit_mix = [{"unit": k, "qty": _i(v[0]), "count": v[1]}
                    for k, v in sorted(umix.items(), key=lambda kv: -kv[1][0])]
        # 【2026-10-21】把"这一所是不是沿用了上次快照"透给前端：不标出来，
        #   用户只会看到日期不动的旧数据，误以为是最新的。
        _fetch = res.get("_fetch") or {}
        ex_meta.append({
            "code": ex, "name": EXCHANGE_CN.get(ex, ex), "unit": unit,
            "date": res.get("date"),
            "stale": bool(_fetch.get("stale")),
            "fetch_error": _fetch.get("error"),
            "brand_support": bool(BRAND_SUPPORT.get(ex)),
            "product_count": len(prod_rows),
            "total_qty": _i(sum(_num(p.get("qty")) or 0 for p in prod_rows)),
            "unit_mix": unit_mix,
        })

        built = _BUILDERS[ex](res)
        wh_by_prod, brand_by_prod = built["wh"], built["brand"]
        sub_by_prod = built["sub"]

        for p in prod_rows:
            code = p.get("product_code") or p.get("variety_code") or ""
            name = p.get("product_name") or p.get("variety") or code
            if not code:
                continue
            u = p.get("unit") or unit
            whs = wh_by_prod.get(code, [])

            # 模块1：仓库数量分布（按数量降序）
            whs_sorted = sorted(whs, key=lambda x: -(x["qty"] or 0))
            # 模块3：仓库/厂库占比
            tacc = _agg(whs, lambda r: r.get("type") or "仓库")
            types = [{"name": k, "qty": _i(v[0]), "change": _i(v[1])}
                     for k, v in sorted(tacc.items(), key=lambda kv: -kv[1][0])]
            # 地区结构
            regions = []
            if built["region"]:
                racc = _agg(whs, lambda r: (r.get("region") or "").strip() or None)
                regions = [{"name": k, "qty": _i(v[0]), "change": _i(v[1])}
                           for k, v in sorted(racc.items(), key=lambda kv: -kv[1][0])]
            # 模块2：品牌结构
            brs = brand_by_prod.get(code, [])
            bacc = _agg(brs, lambda r: r.get("name"))
            brands = [{"name": k, "qty": _i(v[0]), "change": _i(v[1])}
                      for k, v in sorted(bacc.items(), key=lambda kv: -kv[1][0])]
            # 模块4：当日增减结构（按仓库；上期所/大商所/广期所同样粒度，保证可比）
            cacc = _agg(whs, lambda r: r.get("name"))
            ups = [{"name": k, "change": _i(v[1]), "qty": _i(v[0])}
                   for k, v in sorted(cacc.items(), key=lambda kv: -kv[1][1])
                   if v[1] > 0][:12]
            downs = [{"name": k, "change": _i(v[1]), "qty": _i(v[0])}
                     for k, v in sorted(cacc.items(), key=lambda kv: kv[1][1])
                     if v[1] < 0][:12]
            # 有品牌时另附品牌口径增减，供前端切换查看
            bud = bd = None
            if brands:
                bud = [{"name": k, "change": _i(v[1]), "qty": _i(v[0])}
                       for k, v in sorted(bacc.items(), key=lambda kv: -kv[1][1])
                       if v[1] > 0][:12]
                bd = [{"name": k, "change": _i(v[1]), "qty": _i(v[0])}
                      for k, v in sorted(bacc.items(), key=lambda kv: kv[1][1])
                      if v[1] < 0][:12]
            subs = sorted(sub_by_prod.get(code, []),
                          key=lambda x: -(x["qty"] or 0))

            detail["%s|%s" % (ex, code)] = {
                "warehouses": whs_sorted,
                "brands": brands,
                "types": types,
                "regions": regions,
                "subs": subs,
                "changes": {"dim": "仓库", "up": ups, "down": downs},
                "changes_brand": ({"up": bud or [], "down": bd or []}
                                  if brands else None),
            }

            # 昨日仓单量: DCE/GFEX 接口自带; CZCE/SHFE 只公布当日量与增减, 用「当日-增减」反推
            last_qty = _i(p.get("last_qty"))
            if last_qty is None and p.get("qty") is not None and p.get("change") is not None:
                last_qty = _i((_num(p.get("qty")) or 0) - (_num(p.get("change")) or 0))

            products.append({
                "exchange": ex, "exchange_cn": EXCHANGE_CN.get(ex, ex),
                "code": code, "name": name, "unit": u,
                "last_qty": last_qty,
                "qty": _i(p.get("qty")), "change": _i(p.get("change")),
                "warehouse_count": len(whs),
                "brand_count": len(brands),
                "has_brand": bool(brands),
            })

    # 本次真正采集失败的交易所清单（有快照兜底也会有数据，但必须外显）
    fetch_errors = {m["code"]: {"error": m.get("fetch_error"), "date": m.get("date")}
                    for m in ex_meta if m.get("stale")}
    overview = {
        "date": date_str,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "exchanges": ex_meta,
        "products": products,
        "fetch_errors": fetch_errors,
    }
    detail_doc = {
        "date": date_str,
        "generated_at": overview["generated_at"],
        "brand_support": BRAND_SUPPORT,
        "detail": detail,
    }

    p1 = os.path.join(WEB_DIR, "overview.json")
    p2 = os.path.join(WEB_DIR, "detail.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(overview, f, ensure_ascii=False, separators=(",", ":"))
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(detail_doc, f, ensure_ascii=False, separators=(",", ":"))

    log.info("站点数据: 品种 %d 个 / 细则 %d 组 -> %s",
             len(products), len(detail), WEB_DIR)
    return {"overview": p1, "detail": p2,
            "products": len(products), "details": len(detail)}
