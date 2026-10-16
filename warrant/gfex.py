# -*- coding: utf-8 -*-
"""广州期货交易所（GFEX）仓单日报采集

数据源：POST http://www.gfex.com.cn/u/interfacesWebTdWbillWeeklyQuotes/loadList
参数：gen_date=YYYYMMDD（交易日），variety=（品种代码，空=全部）
返回：JSON {code, msg, param, data:[{variety, whAbbr, lastWbillQty, wbillQty, diff,...}]}
      其中 data[0] 为「总计」行。
"""
from __future__ import annotations

import datetime as dt
import json

from .common import date_range_back, get, log, new_session, post, save_raw

EXCHANGE = "GFEX"
EXCHANGE_CN = "广州期货交易所"
API = "http://www.gfex.com.cn/u/interfacesWebTdWbillWeeklyQuotes/loadList"
REFERER = "http://www.gfex.com.cn/gfex/cdrb/hqsj_tjsj.shtml"

FIELDS = ("varietyOrder", "groupCodeOrder", "whCodeOrder", "whType", "variety",
          "genDate", "whAbbr", "trademarkName", "lastWbillQty", "wbillQty", "diff")


def has_real_data(js: dict | None) -> bool:
    """判定响应是否含**真实品种数据**(而不是只有「总计」占位行)。

    【关键】2026-09-17 实测踩坑: 广期所日报在盘后发布前, 接口照样返回
        {"code":"0","msg":"接口访问成功！","data":[{"variety":"总计",
         "lastWbillQty":null,"wbillQty":null,"diff":null}]}
    —— HTTP 200 + code=0 + data 非空, 是标准的**假成功空数据**。旧版只判
    `code=="0" and data` ⇒ 把空壳当有效数据落盘, 广期所当日直接变 0 个品种
    (四所变三所), 而日志里没有任何异常。
    ⇒ 判定必须下探到"排除总计行后还有没有带数量的明细行"。
    """
    if not js:
        return False
    if str(js.get("code")) != "0":
        return False
    rows = js.get("data") or []
    real = [r for r in rows
            if (r.get("variety") or "").strip() not in ("", "总计")]
    if not real:
        return False
    return any(r.get("wbillQty") is not None or r.get("lastWbillQty") is not None
               for r in real)


def fetch(date: dt.date) -> dict | None:
    s = new_session({
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })
    # 注意：该接口只接受 POST，GET 会被前置网关重置(520)
    r = post(s, API, data={"gen_date": date.strftime("%Y%m%d"), "variety": ""})
    if r.status_code != 200:
        return None
    try:
        js = r.json()
    except Exception:                                    # noqa: BLE001
        return None
    if not has_real_data(js):        # 假成功空数据(仅「总计」占位)一律视作无数据
        return None
    return js


def latest_date(start: dt.date | None = None, lookback: int = 12):
    start = start or dt.date.today()
    for d in date_range_back(start, lookback):
        js = fetch(d)
        if js:
            return d, js
    raise RuntimeError("GFEX: 最近 %d 天内未找到仓单日报数据" % lookback)


def parse(js: dict, date: dt.date):
    """返回 (明细, 交易所合计, 品种合计)

    注意：接口返回的 data 中混有「总计」行和「XX小计」行（其 whCodeOrder
    固定为 'wh_code_order'），必须剔除，否则会重复计数。
    """
    detail, total, product = [], [], []
    for row in js.get("data", []):
        variety = (row.get("variety") or "").strip()
        wh_code = (row.get("whCodeOrder") or "").strip()
        rec = {
            "exchange": EXCHANGE, "date": date.isoformat(),
            "variety": variety,
            "variety_code": (row.get("varietyOrder") or "").strip(),
            "warehouse_code": wh_code,
            "warehouse_name": (row.get("whAbbr") or "").strip(),
            "warehouse_type": {"1": "仓库", "2": "厂库"}.get(str(row.get("whType")),
                                                            row.get("whType")),
            "trademark": (row.get("trademarkName") or "").strip(),
            "last_qty": row.get("lastWbillQty"),
            "qty": row.get("wbillQty"),
            "change": row.get("diff"),
        }
        if variety == "总计" or wh_code == "" and not rec["variety_code"]:
            total.append(rec)
        elif variety.endswith("小计") or wh_code == "wh_code_order":
            rec["variety"] = variety.replace("小计", "")
            product.append(rec)
        else:
            detail.append(rec)
    return detail, total, product


def collect(date: dt.date | None = None, force: bool = False) -> dict:
    if date is None:
        date, js = latest_date()
        log.info("GFEX 使用最近交易日 %s", date)
    else:
        js = fetch(date)
        if js is None:
            # 【关键】指定交易日无数据(日报尚未发布)是**常态**而不是异常: 定时
            # 任务 16:30 跑时广期所当日数据常常还没出。旧版直接抛异常 ⇒ 整所
            # 丢失(前端四所变三所、且不知情)。改为回退到最近有数据的交易日。
            try:
                fb_date, fb_js = latest_date(start=date - dt.timedelta(days=1),
                                             lookback=10)
            except RuntimeError:
                raise RuntimeError("GFEX %s 及其之前 10 天均无数据" % date)
            log.warning("GFEX %s 当日无数据(日报未发布), 回退到最近有效交易日 %s",
                        date, fb_date)
            date, js = fb_date, fb_js

    save_raw(EXCHANGE, "%s_wbill.json" % date.strftime("%Y%m%d"),
             json.dumps(js, ensure_ascii=False, indent=1))
    detail, total, product = parse(js, date)
    log.info("GFEX %s 明细 %d / 品种合计 %d", date, len(detail), len(product))
    return {"exchange": EXCHANGE, "date": date.isoformat(),
            "detail": detail, "summary": total, "product": product}
