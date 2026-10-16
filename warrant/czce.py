# -*- coding: utf-8 -*-
"""郑州商品交易所（CZCE）仓单日报采集

数据源：http://www.czce.com.cn/cn/DFSStaticFiles/Future/{Y}/{YYYYMMDD}/FutureDataWhsheet.xlsx

要点
----
1) 反爬：郑商所站点使用瑞数 WAF，会拦截 .htm/.html 请求（412），
   但静态数据文件 .xlsx 可正常访问 —— 本采集器走的就是 .xlsx 通道。
2) 表结构：单 sheet 内多品种块纵向堆叠，且**同一文件存在 23 种不同表头布局**
   （如「仓库编号/仓库简称/年度/等级/品牌/仓单数量/当日增减/有效预报/升贴水」
     或「仓库编号/仓库简称/品牌/仓单数量(完税)/仓单数量(保税)/...」）。
   因此必须**按表头名解析**，不能按固定列号。
3) 每个品种可能出现两块：机构编号表（有效预报）与仓库编号表（仓单）。
   两者分别归入 forecast / detail+summary。
"""
from __future__ import annotations

import datetime as dt
import io
import re

import pandas as pd

from .common import date_range_back, get, log, new_session, save_raw

EXCHANGE = "CZCE"
EXCHANGE_CN = "郑州商品交易所"
XLSX_URL = ("http://www.czce.com.cn/cn/DFSStaticFiles/Future/"
            "{year}/{date}/FutureDataWhsheet.xlsx")

RE_PRODUCT = re.compile(r"品种\s*[:：]\s*([\u4e00-\u9fa5（）()]*?)\s*([A-Z]{1,3})\s")
RE_UNIT = re.compile(r"单位\s*[:：]\s*(\S+)")
RE_SPEC = re.compile(r"每张(?:仓单|预报|确认书)\s*=\s*([^）)\s]+)")

QTY_KEYS = ("仓单数量", "预报数量", "确认书数量")
NAME_KEYS = ("仓库简称", "机构简称", "厂库简称", "集团简称")
# 表头首列：仓库编号 / 厂库编号 / 机构编号 / 集团编号 ...
RE_NO_HEADER = re.compile(r"^[\u4e00-\u9fa5]{2,6}编号$")


def fetch_xlsx(date: dt.date):
    url = XLSX_URL.format(year=date.year, date=date.strftime("%Y%m%d"))
    s = new_session({"Referer": "https://www.czce.com.cn/"})
    r = get(s, url)
    if r.status_code != 200 or len(r.content) < 1000:
        return None
    return r.content


def latest_date(start: dt.date | None = None, lookback: int = 12):
    start = start or dt.date.today()
    for d in date_range_back(start, lookback):
        content = fetch_xlsx(d)
        if content:
            return d, content
    raise RuntimeError("CZCE: 最近 %d 天内未找到仓单日报数据" % lookback)


def _s(v) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat", "null") else t


def _num(v):
    t = _s(v).replace(",", "")
    if t in ("", "-", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse(raw: bytes, date: dt.date):
    """返回 (明细, 仓库级汇总, 品种级合计, 预报)"""
    df = pd.read_excel(io.BytesIO(raw), header=None, dtype=object)
    rows = df.where(pd.notna(df), None).values.tolist()

    detail, warehouse, product, forecast = [], [], [], []
    board = code = name = unit = spec = None
    cols: list[str] = []
    kind = None
    wh_no = wh_name = None
    warns = []

    for r in rows:
        vals = [_s(x) for x in r]
        c0 = vals[0] if vals else ""
        joined = " ".join(x for x in vals if x)

        m = RE_PRODUCT.search(joined + " ")
        if m:
            name, code = m.group(1).strip(), m.group(2).strip()
            if not name:                     # 如「品种：PTA」无中文名
                name = code
            u = RE_UNIT.search(joined)
            unit = u.group(1) if u else "张"
            sp = RE_SPEC.search(joined)
            spec = sp.group(1).strip() if sp else ""
            cols, kind, wh_no, wh_name = [], None, None, None
            continue

        if RE_NO_HEADER.match(c0):
            cols = vals
            kind = "forecast" if c0 == "机构编号" else "warrant"
            wh_no = wh_name = None
            continue

        if not code or not cols:
            continue

        def cell(*keys):
            for k in keys:
                for i, c in enumerate(cols):
                    if c.startswith(k) and i < len(vals):
                        return vals[i]
            return ""

        def nums(prefix):
            out = []
            for i, c in enumerate(cols):
                if c.startswith(prefix) and i < len(vals):
                    out.append(_num(vals[i]))
            return [x for x in out if x is not None]

        qty_cols = nums("仓单数量") or nums("预报数量") or nums("确认书数量")
        chg = _num(cell("当日增减"))
        fc = _num(cell("有效预报", "有效入库预报"))
        prem = _num(cell("升贴水"))
        q = sum(qty_cols) if qty_cols else None

        if c0 == "总计":
            rec = {"exchange": EXCHANGE, "date": date.isoformat(), "board": board,
                   "product_code": code, "product_name": name, "kind": kind,
                   "qty": q, "change": chg, "forecast": fc, "unit": unit, "spec": spec}
            (product if kind == "warrant" else forecast).append(rec)
            code, cols, kind = None, [], None
            continue

        if c0 == "小计":
            rec = {"exchange": EXCHANGE, "date": date.isoformat(), "board": board,
                   "product_code": code, "product_name": name, "kind": kind,
                   "warehouse_no": wh_no or "", "warehouse_name": wh_name or "",
                   "qty": q, "change": chg, "forecast": fc, "premium": prem,
                   "unit": unit, "spec": spec}
            (warehouse if kind == "warrant" else forecast).append(rec)
            wh_no = wh_name = None
            continue

        if not any(vals):
            continue

        if vals[0] and not re.match(r"^[0-9A-Za-z]", vals[0]) and len(cols) > 2:
            if all(not v for v in vals[1:]) and len(vals[0]) <= 8:
                board = vals[0]
                continue

        if re.match(r"^[0-9]{3,4}$", vals[0]):
            wh_no, wh_name = vals[0], cell(*NAME_KEYS)

        attrs = {c: v for c, v in zip(cols, vals)
                 if c and v and c not in ("当日增减", "有效预报", "有效入库预报",
                                          "升贴水", "仓单编号") and not c.startswith("仓单数量")
                 and not c.startswith("预报数量") and not c.startswith("确认书数量")}
        # 品牌/等级/年度：郑商所仓单以「品牌」逐条登记，是品牌结构的唯一来源。
        # 表头变体多（「品牌」「品牌名称」「产地/品牌」），统一取含「品牌」的列。
        brand = ""
        for c, v in zip(cols, vals):
            if c and "品牌" in c and v:
                brand = v
                break
        detail.append({
            "exchange": EXCHANGE, "date": date.isoformat(), "board": board,
            "product_code": code, "product_name": name, "kind": kind,
            "warehouse_no": wh_no or "", "warehouse_name": wh_name or "",
            "brand": brand,
            "grade": attrs.get("等级", ""), "year": attrs.get("年度", ""),
            "qty": q, "change": chg, "forecast": fc, "premium": prem,
            "unit": unit, "spec": spec, "attrs": attrs,
        })

    # 自检：每个品种块的总计 vs 小计之和
    from collections import defaultdict
    agg = defaultdict(float)
    for r in warehouse:
        if r["qty"] is not None:
            agg[(r["product_code"], r["spec"])] += r["qty"]
    for r in product:
        if r["qty"] is None:
            continue
        k = (r["product_code"], r["spec"])
        if abs(agg.get(k, 0.0) - r["qty"]) > 0.5:
            warns.append("CZCE %s(%s) 总计=%s 与仓库小计之和=%s 不一致"
                         % (r["product_code"], r["spec"], r["qty"], agg.get(k, 0.0)))
    for w in warns:
        log.warning(w)

    return detail, warehouse, product, forecast


def collect(date: dt.date | None = None, force: bool = False) -> dict:
    if date is None:
        date, raw = latest_date()
        log.info("CZCE 使用最近交易日 %s", date)
    else:
        raw = fetch_xlsx(date)
        if raw is None:
            raise RuntimeError("CZCE %s 无数据" % date)

    save_raw(EXCHANGE, "%s_Whsheet.xlsx" % date.strftime("%Y%m%d"), raw)
    detail, warehouse, product, forecast = parse(raw, date)
    log.info("CZCE %s 明细 %d / 仓库小计 %d / 品种合计 %d / 预报 %d",
             date, len(detail), len(warehouse), len(product), len(forecast))
    return {"exchange": EXCHANGE, "date": date.isoformat(),
            "detail": detail, "summary": warehouse,
            "product": product, "forecast": forecast}
