# -*- coding: utf-8 -*-
"""把四家交易所的异构结构归一化为统一 schema

统一（仓库级）字段：
  exchange, exchange_cn, date, product_code, product_name,
  warehouse_no, warehouse_name, warehouse_type,
  last_qty, qty, change, unit, region, extra

统一（品种级）字段：
  exchange, exchange_cn, date, product_code, product_name,
  last_qty, qty, change, unit

品种级数据一律**优先采用交易所自己给出的合计行**（即各模块返回的 product 字段），
而不是自行汇总仓库行，避免各行口径差异造成偏差；同时用仓库行求和做交叉校验。
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict

from .common import ensure_dir, log, to_float

EXCHANGE_CN = {"CZCE": "郑州商品交易所", "DCE": "大连商品交易所",
               "GFEX": "广州期货交易所", "SHFE": "上海期货交易所"}

UNIFIED_WAREHOUSE_COLS = [
    "exchange", "exchange_cn", "date", "product_code", "product_name",
    "warehouse_no", "warehouse_name", "warehouse_type", "region",
    "last_qty", "qty", "change", "unit", "extra",
]
UNIFIED_PRODUCT_COLS = [
    "exchange", "exchange_cn", "date", "product_code", "product_name",
    "last_qty", "qty", "change", "unit",
]
# 品牌级明细：只有郑商所(品牌列)与广期所(trademarkName)官方公布品牌，
# 大商所/上期所日报不含品牌维度 → 该表对这两家为空。
UNIFIED_BRAND_COLS = [
    "exchange", "exchange_cn", "date", "product_code", "product_name",
    "warehouse_name", "brand", "qty", "change", "unit",
]

# 各交易所官方是否公布「品牌」维度（供前端如实提示，不臆造数据）
BRAND_SUPPORT = {"CZCE": True, "GFEX": True, "DCE": False, "SHFE": False}


def _blank(exchange: str, date: str) -> dict:
    d = {c: None for c in UNIFIED_WAREHOUSE_COLS}
    d.update({"exchange": exchange, "exchange_cn": EXCHANGE_CN.get(exchange, exchange),
              "date": date})
    return d


def _fill_last(d: dict) -> dict:
    """交易所未直接给出「昨日仓单」时，用 当日 - 增减 反推。

    DCE / GFEX 接口自带 last_qty；CZCE / SHFE 只公布当日量与增减，
    这里统一补齐，保证四所字段语义一致。反推出的值同样受用。
    """
    if d.get("last_qty") is None and d.get("qty") is not None and d.get("change") is not None:
        try:
            v = float(d["qty"]) - float(d["change"])
        except (TypeError, ValueError):
            return d
        d["last_qty"] = int(v) if float(v).is_integer() else round(v, 4)
    return d


def normalize_warehouse(exchange: str, res: dict) -> list[dict]:
    date = res["date"]
    out = []
    if exchange == "CZCE":
        for r in res.get("summary", []):
            d = _blank(exchange, date)
            d.update({
                "product_code": r.get("product_code"), "product_name": r.get("product_name"),
                "warehouse_no": r.get("warehouse_no"), "warehouse_name": r.get("warehouse_name"),
                "warehouse_type": "厂库" if "厂库" in (r.get("warehouse_name") or "") else "仓库",
                "qty": r.get("qty"), "change": r.get("change"), "unit": r.get("unit"),
                "extra": r.get("spec") or None})
            out.append(d)
    elif exchange == "DCE":
        for r in res.get("detail", []):
            d = _blank(exchange, date)
            d.update({
                "product_code": r.get("variety_code"), "product_name": r.get("variety"),
                "warehouse_no": r.get("warehouse_code"), "warehouse_name": r.get("warehouse_name"),
                "warehouse_type": r.get("warehouse_type"), "region": r.get("delivery_place") or None,
                "last_qty": r.get("last_qty"), "qty": r.get("qty"),
                "change": r.get("change"), "unit": "手"})
            out.append(d)
    elif exchange == "GFEX":
        for r in res.get("detail", []):
            d = _blank(exchange, date)
            d.update({
                "product_code": r.get("variety_code"), "product_name": r.get("variety"),
                "warehouse_no": r.get("warehouse_code"), "warehouse_name": r.get("warehouse_name"),
                "warehouse_type": r.get("warehouse_type"),
                "last_qty": r.get("last_qty"), "qty": r.get("qty"),
                "change": r.get("change"), "unit": "手",
                "extra": r.get("trademark") or None})
            out.append(d)
    elif exchange == "SHFE":
        for r in res.get("detail", []):
            d = _blank(exchange, date)
            d.update({
                "product_code": r.get("product_code"), "product_name": r.get("product_name"),
                "warehouse_no": "", "warehouse_name": r.get("warehouse_name"),
                "warehouse_type": "仓库", "region": r.get("region"),
                "qty": r.get("qty"), "change": r.get("change"), "unit": r.get("unit")})
            out.append(d)
    return [_fill_last(d) for d in out]


def normalize_product(exchange: str, res: dict) -> list[dict]:
    """交易所自报的品种合计"""
    date = res["date"]
    out = []
    for r in res.get("product", []):
        code = (r.get("product_code") or r.get("variety_code") or "").strip()
        name = (r.get("product_name") or r.get("variety") or "").strip()
        if not code and not name:
            continue
        out.append(_fill_last({
            "exchange": exchange, "exchange_cn": EXCHANGE_CN.get(exchange, exchange),
            "date": date, "product_code": code, "product_name": name,
            "last_qty": r.get("last_qty"), "qty": r.get("qty"),
            "change": r.get("change"),
            "unit": r.get("unit") or ("吨" if exchange == "SHFE" else "手"),
        }))
    return out


def normalize_brand(exchange: str, res: dict) -> list[dict]:
    """品牌级明细（仅 CZCE / GFEX 官方有品牌数据）"""
    if not BRAND_SUPPORT.get(exchange):
        return []
    date = res["date"]
    out = []
    for r in res.get("detail", []):
        brand = (r.get("brand") or r.get("trademark") or "").strip()
        if not brand:
            continue
        out.append(_fill_last({
            "exchange": exchange, "exchange_cn": EXCHANGE_CN.get(exchange, exchange),
            "date": date,
            "product_code": r.get("product_code") or r.get("variety_code") or "",
            "product_name": r.get("product_name") or r.get("variety") or "",
            "warehouse_name": r.get("warehouse_name") or "",
            "brand": brand,
            "qty": r.get("qty"), "change": r.get("change"),
            "unit": r.get("unit") or ("张" if exchange == "CZCE" else "手"),
        }))
    return out


def cross_check(warehouse_rows: list[dict], product_rows: list[dict]) -> list[str]:
    """用仓库行求和校验品种合计"""
    agg = defaultdict(float)
    for r in warehouse_rows:
        agg[(r["exchange"], r["product_code"])] += to_float(r.get("qty"), 0) or 0
    warns = []
    for p in product_rows:
        k = (p["exchange"], p["product_code"])
        if p.get("qty") is None or k not in agg:
            continue
        if abs(agg[k] - float(p["qty"])) > 1.0:
            warns.append("%s %s(%s): 合计=%s vs 仓库求和=%s"
                         % (p["exchange"], p["product_name"], p["product_code"],
                            p["qty"], agg[k]))
    return warns


def write_csv(path: str, rows: list[dict], cols: list[str]) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path
