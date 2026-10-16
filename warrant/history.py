# -*- coding: utf-8 -*-
"""仓单历史时序 —— 分品种 / 分仓库的「每日仓单变化」曲线数据源。

【数据来源】全部为本地落盘文件, 不联网:
  data/warrant/out/YYYYMMDD_<EX>_product.json   品种级(四所, warrant_collect 采集产物)
  data/warrant/out/YYYYMMDD_CZCE_summary.json   郑商所仓库级(小计行)
  data/warrant/out/YYYYMMDD_<EX>_detail.json    大商所/广期所/上期所仓库级
  data/warrant/raw/czce/YYYYMMDD_Whsheet.xlsx   郑商所更早日期回填(out 缺失该日时,
                                                用 czce.parse 离线解析本地 xlsx)

【产出】(进程内缓存, 文件集变化才重建 —— 见 _cache_key)
  dates       排序后的全部交易日 ["2026-08-21", ...]
  products    {"CZCE|SR": {exchange, code, name, unit,
                           qty: {date: 数量}, change: {date: 当日增减}}}
  warehouses  {"CZCE|SR": {"藁城永安": {type, qty: {date: v}, change: {date: v}}}}
  ex_totals   {"CZCE": {unit, qty: {date: 合计}, excluded: 非主单位品种数}}
              四所合计走势用 —— 只合计该所主单位品种(郑=张/大=手/广=手/上=吨),
              上期所的千克(白银/黄金)/桶(原油)品种不可与吨相加, 单独计数排除。

【口径】
  · 某品种/仓库在某日期缺数据 → 该日期键缺省(前端画折线留空, connectNulls=false)。
  · 品种缺某日多为「当日该所未采集/空壳被拦」; 广期所 2026-09-16 即为已知缺口。
  · 仓库身份按 warehouse_name 识别(同一物理库跨日同名); CZCE summary 行 kind
    必须为 warrant(机构预报表 kind=forecast 不能混入)。
  · DCE/GFEX detail 里的汇总占位行(warehouse_code=wh_code_order 或空名)剔除。
"""
from __future__ import annotations

import json
import os
import re

from .common import OUT_DIR, RAW_DIR, log

ORDER = ["CZCE", "DCE", "GFEX", "SHFE"]
UNIT_MAIN = {"CZCE": "张", "DCE": "手", "GFEX": "手", "SHFE": "吨"}

_PROD_RE = re.compile(r"^(\d{8})_([A-Z]+)_product\.json$")
_CZCE_XLSX_RE = re.compile(r"^(\d{8})_Whsheet\.xlsx$")

_CACHE = {"key": None, "data": None}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _i(v):
    f = _num(v)
    if f is None:
        return None
    return int(f) if float(f).is_integer() else round(f, 4)


def _date_cn(yyyymmdd: str) -> str:
    return "%s-%s-%s" % (yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:])


# ── 郑商所 xlsx 回填(离线解析本地原始文件) ─────────────────────────────────
def _czce_parse_file(path: str, date_cn: str) -> dict | None:
    """用 czce.parse 离线解析本地 xlsx; 返回 {"product": [...], "summary": [...]}。
    独立成函数便于测试替身(避免单测真造 xlsx)。"""
    try:
        from datetime import datetime as _dt

        from . import czce
        with open(path, "rb") as f:
            raw = f.read()
        d = _dt.strptime(date_cn.replace("-", ""), "%Y%m%d").date()
        detail, warehouse, product, _forecast = czce.parse(raw, d)
        return {"product": product, "summary": warehouse}
    except Exception as e:  # noqa: BLE001 - 单个历史文件解析失败不拖垮整体
        log.warning("CZCE 历史回填失败 %s: %s", path, e)
        return None


def _cache_key() -> tuple:
    """out/ 全部文件 + czce 原始 xlsx 的 (名, mtime, 大小) 元组 —— 任一变化即重建。"""
    items = []
    for d in (OUT_DIR, os.path.join(RAW_DIR, "czce")):
        try:
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                items.append((fn, int(st.st_mtime), st.st_size))
        except OSError:
            pass
    return tuple(sorted(items))


# ── 单日数据合并 ──────────────────────────────────────────────────────────
def _merge_day(date_cn: str, ex: str, res: dict,
               products: dict, warehouses: dict) -> None:
    """把某交易所某日的 product/仓库行并入累计结构。"""
    unit_default = UNIT_MAIN.get(ex, "")

    for p in res.get("product", []) or []:
        # CZCE product 行带 kind(=warrant); 其余所无此字段。机构预报不能混入。
        if ex == "CZCE" and (p.get("kind") or "warrant") != "warrant":
            continue
        code = (p.get("product_code") or p.get("variety_code") or "").strip()
        if not code:
            continue
        key = "%s|%s" % (ex, code)
        acc = products.setdefault(key, {
            "exchange": ex, "code": code,
            "name": (p.get("product_name") or p.get("variety") or code),
            "unit": p.get("unit") or unit_default,
            "qty": {}, "change": {},
        })
        q = _i(p.get("qty"))
        if q is not None:
            acc["qty"][date_cn] = q
        c = _i(p.get("change"))
        if c is not None:
            acc["change"][date_cn] = c

    if ex == "CZCE":
        wh_rows = res.get("summary", []) or []
    else:
        wh_rows = res.get("detail", []) or []

    for r in wh_rows:
        if ex == "CZCE" and (r.get("kind") or "warrant") != "warrant":
            continue
        code = (r.get("product_code") or r.get("variety_code") or "").strip()
        name = (r.get("warehouse_name") or "").strip()
        wcode = (r.get("warehouse_code") or r.get("warehouse_no") or "").strip()
        # 汇总占位行(如 GFEX 的 wh_code_order)与空名行不是真实仓库
        if not code or not name or wcode == "wh_code_order":
            continue
        key = "%s|%s" % (ex, code)
        acc = warehouses.setdefault(key, {})
        w = acc.setdefault(name, {"type": r.get("warehouse_type") or
                                  ("厂库" if "厂库" in name else "仓库"),
                                  "qty": {}, "change": {}})
        q = _i(r.get("qty"))
        if q is not None:
            w["qty"][date_cn] = q
        c = _i(r.get("change"))
        if c is not None:
            w["change"][date_cn] = c


def build() -> dict:
    """扫描 out/(+ CZCE 原始 xlsx 回填)构建全量历史时序。"""
    products: dict = {}
    warehouses: dict = {}
    out_dates: set[str] = set()

    for fn in os.listdir(OUT_DIR):
        m = _PROD_RE.match(fn)
        if not m:
            continue
        date_str, ex = m.group(1), m.group(2)
        if ex not in ORDER:
            continue
        date_cn = _date_cn(date_str)
        out_dates.add(date_cn)
        try:
            with open(os.path.join(OUT_DIR, fn), encoding="utf-8") as f:
                prod_rows = json.load(f)
        except Exception:  # noqa: BLE001 - 坏文件跳过, 不让单点拖垮整页
            log.warning("仓单历史: 读取失败 %s", fn)
            prod_rows = []
        res = {"product": prod_rows}

        wh_name = "%s_%s_summary.json" % (date_str, ex) if ex == "CZCE" \
            else "%s_%s_detail.json" % (date_str, ex)
        wh_path = os.path.join(OUT_DIR, wh_name)
        if os.path.exists(wh_path):
            try:
                with open(wh_path, encoding="utf-8") as f:
                    res["summary" if ex == "CZCE" else "detail"] = json.load(f)
            except Exception:  # noqa: BLE001
                log.warning("仓单历史: 读取失败 %s", wh_name)

        _merge_day(date_cn, ex, res, products, warehouses)

    # ── CZCE 回填: 本地有更早的原始 xlsx 而 out/ 没有解析产物 → 离线补齐 ──
    backfilled = []
    czce_raw_dir = os.path.join(RAW_DIR, "czce")
    if os.path.isdir(czce_raw_dir):
        for fn in sorted(os.listdir(czce_raw_dir)):
            m = _CZCE_XLSX_RE.match(fn)
            if not m:
                continue
            date_cn = _date_cn(m.group(1))
            if date_cn in out_dates:
                continue
            parsed = _czce_parse_file(os.path.join(czce_raw_dir, fn), date_cn)
            if not parsed:
                continue
            _merge_day(date_cn, "CZCE", parsed, products, warehouses)
            out_dates.add(date_cn)
            backfilled.append(date_cn)

    # ── 四所合计(主单位口径) ──
    ex_totals: dict = {}
    for ex in ORDER:
        unit = UNIT_MAIN[ex]
        qty_acc: dict = {}
        excluded = 0
        for key, acc in products.items():
            if acc["exchange"] != ex:
                continue
            if acc["unit"] != unit:
                excluded += 1
                continue
            for d, q in acc["qty"].items():
                qty_acc[d] = qty_acc.get(d, 0) + q
        ex_totals[ex] = {"unit": unit, "qty": qty_acc, "excluded": excluded}

    if backfilled:
        log.info("仓单历史: CZCE 回填 %d 天 (%s)", len(backfilled),
                 ", ".join(sorted(backfilled)))
    return {
        "dates": sorted(out_dates),
        "products": products,
        "warehouses": warehouses,
        "ex_totals": ex_totals,
        "backfilled": backfilled,
    }


def get() -> dict:
    """带缓存的对外入口: 文件集没变就直接返回上次结果。"""
    key = _cache_key()
    if _CACHE.get("key") == key and _CACHE.get("data") is not None:
        return _CACHE["data"]
    data = build()
    _CACHE["key"] = key
    _CACHE["data"] = data
    return data
