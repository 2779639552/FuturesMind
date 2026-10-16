# -*- coding: utf-8 -*-
"""上海期货交易所（SHFE / INE）仓单日报采集

主数据源（官方仓单日报表，含「增减」，无需浏览器、无反爬）：
  https://www.shfe.com.cn/data/tradedata/future/stockdata/dailystock_{YYYYMMDD}/ZH/all.html
  表头：地区 | 仓库 | 期货（仓单量）| 增减        （单位随品种不同：吨 / 千克 / 桶）
  每个品种一个块，块内最后一行 class="isTotal" 为品种总计。

辅数据源（仓单货位级明细，JSON，一条记录=一张仓单）：
  https://www.shfe.com.cn/data/tradedata/future/dailydata/stock_{product}_{YYYYMMDD}.dat
  注意：明细为「仓单登记台账」，与官方日报口径存在少量差异
  （实测 2026-09-11 铜：明细 20875 吨 vs 日报 20854 吨），
  故**以官方日报为准**，明细仅作留档/下钻参考。

可用日期： https://www.shfe.com.cn/data/tradedata/future/dailydata/stock_data_list.dat
"""
from __future__ import annotations

import datetime as dt
import json
import re

from bs4 import BeautifulSoup

from .common import get, log, new_session, save_raw

EXCHANGE = "SHFE"
EXCHANGE_CN = "上海期货交易所"
BASE = "https://www.shfe.com.cn/data"
URL_LIST = BASE + "/tradedata/future/dailydata/stock_data_list.dat"
URL_PRODUCT_CONFIG = BASE + "/config/product_config.dat"
URL_DAILY_HTML = BASE + "/tradedata/future/stockdata/dailystock_{date}/ZH/all.html"
URL_STOCK = BASE + "/tradedata/future/dailydata/stock_{product}_{date}.dat"


def _session():
    return new_session({
        "Referer": "https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/",
        "Accept": "text/html,application/json,text/plain,*/*"})


def available_dates() -> list[dt.date]:
    r = get(_session(), URL_LIST)
    nums = re.findall(r"\d{8}", r.content.decode("utf-8"))
    return sorted({dt.datetime.strptime(n, "%Y%m%d").date() for n in nums})


def latest_date(start: dt.date | None = None) -> dt.date:
    start = start or dt.date.today()
    dates = [d for d in available_dates() if d <= start]
    if not dates:
        raise RuntimeError("SHFE: 无可用交易日")
    return dates[-1]


def products() -> dict[str, str]:
    """{品种中文名: 品种代码}（仅期货类目）"""
    r = get(_session(), URL_PRODUCT_CONFIG)
    cfg = json.loads(r.content.decode("utf-8"))
    out = {}
    for key, v in cfg.items():
        if not key.endswith("_f"):
            continue
        out[v.get("productname", key)] = v.get("productgroupid") or key[:-2]
    return out


def _expand_grid(table):
    """把带 rowspan/colspan 的 HTML 表展开成 (二维列表, 每行class列表)"""
    grid, classes = [], []
    spans: dict[int, list] = {}          # 列号 -> [剩余行数, 值]

    def cell_text(c):
        return re.sub(r"\s+", " ", c.get_text(" ", strip=True))

    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        classes.append(tr.get("class") or [])
        row, col, ci = [], 0, 0
        while ci < len(cells) or any(v[0] > 0 for v in spans.values()):
            if spans.get(col, [0])[0] > 0:
                row.append(spans[col][1])
                spans[col][0] -= 1
                col += 1
                continue
            if ci >= len(cells):
                break
            c = cells[ci]
            ci += 1
            txt = cell_text(c)
            rs = int(c.get("rowspan") or 1)
            cs = int(c.get("colspan") or 1)
            for k in range(cs):
                row.append(txt)
                if rs > 1:
                    spans[col + k] = [rs - 1, txt]
                col += 1
        grid.append(row)
    return grid, classes


# 官方日报里的品种名与交易所产品配置的差异
NAME_ALIAS = {"中质含硫原油": "原油", "低硫燃料油": "低硫燃料油",
              "铜(BC)": "铜(BC)"}
PAREN = re.compile(r"[（(](仓库|厂库|保税)[)）]\s*$")


def resolve_code(name: str, name2code: dict) -> str:
    """把官方日报里的品种名映射到品种代码

    先精确匹配，再去掉「(仓库)/(厂库)」后缀匹配，最后过别名表。
    """
    if name in name2code:
        return name2code[name]
    base = PAREN.sub("", name).strip()
    if base in name2code:
        return name2code[base]
    alias = NAME_ALIAS.get(base) or NAME_ALIAS.get(name)
    if alias and alias in name2code:
        return name2code[alias]
    return ""


def parse_html(html: str, date: dt.date, name2code: dict[str, str]):
    """解析官方仓单日报 HTML → (明细, 品种合计)

    页面细节（实测 2026-09）：
      * 每个 <table> 一个品种块；表头存在多种变体：
          地区|仓库|期货|增减     （标准仓库表）
          厂库|地区|期货|增减     （厂库表）
          地区|厂库|期货|增减     （厂库表）
          地区|交割仓库|本日数量|增减 （原油）
        因此**按表头文字定位列**，不能按固定列号。
      * 行 class：special_row_type（品种/单位）、isTotal（合计 / 完税商品总计 /
        保税商品总计 / 总计）、普通数据行
      * 「合计」为地区小计，「总计」为品种合计（权威值）；个别品种（黄金、燃料油）
        无「总计」行，此时按明细求和兜底
      * 同一品种可能拆成「仓库」「厂库」两个块，需合并为一个品种
    """
    soup = BeautifulSoup(html, "lxml")
    blocks, unmapped = [], set()

    for tb in soup.find_all("table"):
        grid, classes = _expand_grid(tb)
        if not grid:
            continue
        head = grid[0]
        has_wh = any(("仓库" in c or "厂库" in c) for c in head)
        has_chg = any(("增减" in c) for c in head)
        has_qty = any(c in ("期货", "本日数量") for c in head)
        if not (has_wh and has_chg and has_qty):
            continue

        def col(keys, default):
            for i, c in enumerate(head):
                if any(k in c for k in keys):
                    return i
            return default

        i_region = col(("地区",), 0)
        i_wh = col(("交割仓库", "仓库", "厂库"), 1)
        i_qty = col(("期货", "本日数量"), 2)
        i_chg = col(("增减",), 3)

        cur = {"code": "", "name": "", "unit": "", "kind": "warehouse",
               "rows": [], "total": None}
        for i, row in enumerate(grid):
            cls = classes[i] if i < len(classes) else []
            cells = [c for c in row if c]
            if not cells:
                continue

            if "special_row_type" in cls:
                cur["name"] = cells[0]
                for c in cells[1:]:
                    m = re.search(r"单位[:：]\s*(\S+)", c)
                    if m:
                        cur["unit"] = m.group(1)
                cur["code"] = resolve_code(cur["name"], name2code)
                if not cur["code"]:
                    unmapped.add(cur["name"])
                cur["kind"] = "factory" if "厂库" in head[i_wh] else "warehouse"
                continue

            if "isTotal" in cls:
                joined = "".join(cells)
                if cells[0] == "总计" and "商品总计" not in joined:
                    vals = [c for c in cells[1:] if c != "总计"]
                    cur["total"] = (_num(vals[0] if vals else None),
                                    _num(vals[1] if len(vals) > 1 else None))
                continue

            if len(cells) > max(i_region, i_wh, i_qty, i_chg) and \
                    cells[i_qty] != head[i_qty] and not re.match(
                        r"^\d{4}-\d{2}-\d{2}", cells[0]):
                cur["rows"].append({
                    "exchange": EXCHANGE, "date": date.isoformat(),
                    "product_code": cur["code"], "product_name": cur["name"],
                    "unit": cur["unit"], "region": cells[i_region],
                    "warehouse_name": cells[i_wh],
                    "warehouse_type": "厂库" if cur["kind"] == "factory" else "仓库",
                    "qty": _num(cells[i_qty]), "change": _num(cells[i_chg]),
                })
        if cur["name"]:
            blocks.append(cur)

    if unmapped:
        log.warning("SHFE 以下品种名未匹配到代码: %s", sorted(unmapped))

    # 同一品种可能拆成「仓库」「厂库」两块 → 按代码合并
    detail = [r for b in blocks for r in b["rows"]]
    merged: dict[str, dict] = {}
    for b in blocks:
        key = b["code"] or PAREN.sub("", b["name"]).strip()
        # 每个块先取自己的数值：优先「总计」行，缺失则按明细求和
        qty, chg = b["total"] if b["total"] else (None, None)
        if qty is None:
            rs = [r for r in b["rows"] if r["qty"] is not None]
            if rs:
                qty = sum(r["qty"] for r in rs)
                chg = sum(r["change"] or 0 for r in rs) if any(
                    r["change"] is not None for r in rs) else None
                log.info("SHFE %s 无「总计」行，按明细求和 qty=%s", b["name"], qty)
        m = merged.setdefault(key, {"exchange": EXCHANGE, "date": date.isoformat(),
                                    "product_code": b["code"],
                                    "product_name": PAREN.sub("", b["name"]).strip(),
                                    "unit": b["unit"], "qty": 0.0, "change": 0.0,
                                    "has_qty": False, "has_chg": False})
        if qty is not None:
            m["qty"] += qty
            m["has_qty"] = True
        if chg is not None:
            m["change"] += chg
            m["has_chg"] = True

    product = []
    for m in merged.values():
        product.append({"exchange": m["exchange"], "date": m["date"],
                        "product_code": m["product_code"],
                        "product_name": m["product_name"], "unit": m["unit"],
                        "qty": round(m["qty"], 4) if m["has_qty"] else None,
                        "change": round(m["change"], 4) if m["has_chg"] else None})
    return detail, product


def _num(v):
    if v is None:
        return None
    t = str(v).replace(",", "").strip()
    if t in ("", "-", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_product_detail(product: str, date: dt.date):
    r = get(_session(), URL_STOCK.format(product=product, date=date.strftime("%Y%m%d")))
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:                                    # noqa: BLE001
        return None


def collect(date: dt.date | None = None, with_detail: bool = False,
            force: bool = False) -> dict:
    if date is None:
        date = latest_date()
        log.info("SHFE 使用最近交易日 %s", date)

    ds = date.strftime("%Y%m%d")
    r = get(_session(), URL_DAILY_HTML.format(date=ds))
    if r.status_code != 200 or len(r.content) < 2000:
        raise RuntimeError("SHFE %s 仓单日报无数据（HTTP %s）" % (date, r.status_code))
    html = r.content.decode("utf-8", "ignore")
    save_raw(EXCHANGE, "%s_dailystock_all.html" % ds, html)

    detail, product = parse_html(html, date, products())

    aux = []
    if with_detail:
        for code in sorted({d["product_code"] for d in detail if d["product_code"]}):
            js = fetch_product_detail(code, date)
            if js and js.get("StockOutData"):
                save_raw(EXCHANGE, "%s_stock_%s.json" % (ds, code),
                         json.dumps(js, ensure_ascii=False))
                aux.extend(_flatten_positions(js, code, date))

    log.info("SHFE %s 仓库级 %d 行 / 品种合计 %d 个%s",
             date, len(detail), len(product),
             ("，货位明细 %d 行" % len(aux)) if aux else "")
    return {"exchange": EXCHANGE, "date": date.isoformat(),
            "detail": detail, "summary": detail, "product": product,
            "positions": aux}


def _flatten_positions(js: dict, product: str, date: dt.date) -> list[dict]:
    out = []
    for it in js.get("StockOutData", []):
        out.append({
            "exchange": EXCHANGE, "date": date.isoformat(), "product_code": product,
            "warehouse_no": it.get("WAREHOUSEID", ""),
            "warehouse_name": it.get("WAREHOUSENAME", ""),
            "position_no": it.get("STGPOSID", ""),
            "position_name": it.get("STGPOSNAME", ""),
            "warrant_code": it.get("CRGPOSCODE", ""),
            "weight": float(it.get("CRGWEIGHT") or 0),
            "valid_weight": float(it.get("WRTWEIGHT") or 0),
            "expired_weight": float(it.get("EXPIREDWEIGHT") or 0),
            "extend_weight": float(it.get("EXTENDWEIGHT") or 0),
            "settle_date": it.get("SETTDATE", ""),
        })
    return out
