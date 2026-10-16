# -*- coding: utf-8 -*-
"""大连商品交易所（DCE）仓单日报采集

背景：大商所站点使用「瑞数信息」动态防护：
      * 直接 GET/POST 受保护路径 → 412
      * 需要真实浏览器内核执行下发的挑战脚本，换取动态 Cookie 后才可放行
      * 数据接口的 query 上还带一次性动态令牌（如 ?VoGRv6Ir=...），复用会 400
      因此 DCE 必须用「真实浏览器内核」采集：打开官方数据页，拦获 XHR 响应。

数据页（SPA）：
  http://www.dce.com.cn/frontend/dcereport/#/zh/wbillWeeklyQuotes?variety=all&tradeType=1
数据接口（页面内部调用）：
  POST /dcereport/publicweb/dailystat/wbillWeeklyQuotes
  body: {"varietyId":"all","tradeDate":"YYYYMMDD"}
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

from .common import ROOT, log, save_raw

EXCHANGE = "DCE"
EXCHANGE_CN = "大连商品交易所"
SPA_URL = ("http://www.dce.com.cn/frontend/dcereport/"
           "#/zh/wbillWeeklyQuotes?variety=all&tradeType=1")
API_KEY = "wbillWeeklyQuotes"

EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
PROFILE = os.path.join(ROOT, ".browser_profile")


def find_browser() -> str | None:
    for p in (EDGE, CHROME):
        if os.path.exists(p):
            return p
    return None


def _open_page(pw, headless: bool, timeout_ms: int):
    exe = find_browser()
    os.makedirs(PROFILE, exist_ok=True)
    kw = dict(user_data_dir=PROFILE, headless=headless, locale="zh-CN",
              viewport={"width": 1600, "height": 1000},
              args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    if exe:
        kw["executable_path"] = exe
    ctx = pw.chromium.launch_persistent_context(**kw)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def collect(date: dt.date | None = None, headless: bool = False,
            timeout_s: int = 60, force: bool = False) -> dict:
    """采集 DCE 仓单日报。

    date 为 None 时取页面默认（最近交易日）。
    注：SPA 通过 UI 选日期，本采集器默认取最新交易日；如需历史日期，
    可扩展 _pick_date() 与日期控件交互。
    """
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []

    def on_response(resp):
        try:
            if API_KEY in resp.url and resp.request.resource_type in ("xhr", "fetch"):
                if resp.status == 200:
                    captured.append({
                        "url": resp.url,
                        "method": resp.request.method,
                        "post": resp.request.post_data,
                        "json": resp.json(),
                    })
        except Exception:                                # noqa: BLE001
            pass

    with sync_playwright() as pw:
        ctx, page = _open_page(pw, headless, timeout_s * 1000)
        page.on("response", on_response)
        try:
            page.goto(SPA_URL, timeout=timeout_s * 1000, wait_until="commit")
            for _ in range(timeout_s):
                page.wait_for_timeout(1000)
                if captured:
                    break
            if not captured:
                raise RuntimeError("DCE: 未捕获到 wbillWeeklyQuotes 响应（可能被防护拦截）")
            payload = captured[-1]
        finally:
            try:
                ctx.close()
            except Exception:                            # noqa: BLE001
                pass

    js = payload["json"]
    d = (date or _detect_date(js)).strftime("%Y%m%d")
    save_raw(EXCHANGE, "%s_wbill.json" % d,
             json.dumps(js, ensure_ascii=False, indent=1))

    detail, total, product, sub = parse(js, date)
    log.info("DCE %s 仓库级 %d / 品种合计 %d / 分库 %d 行",
             date or "(页面默认)", len(detail), len(product), len(sub))
    return {"exchange": EXCHANGE, "date": (date or _detect_date(js)).isoformat(),
            "detail": detail, "summary": total,
            "product": product, "sub": sub,
            "api": payload["url"].split("?")[0]}


def _detect_date(js: dict) -> dt.date:
    for row in (js.get("data") or {}).get("entityList", []):
        if row.get("genDate"):
            return dt.datetime.strptime(str(row["genDate"]), "%Y%m%d").date()
    return dt.date.today()


def parse(js: dict, date: dt.date | None):
    """返回 (仓库级明细, 交易所合计, 品种合计, 分库明细)

    entityList 混有四类行，必须区分，否则重复计数：
      * 交易所合计：variety == "总计"
      * 品种小计：variety 以「小计」结尾（其"今日仓单量"即品种口径，权威）
      * 仓库级：variety 有值，且不是某个仓库组的分库
          - 仓库组行：whCodeOrder 为 3 位数字（如 "002"），其值 = 各分库之和
          - 独立仓库行：whCodeOrder 形如 "_160"（无组前缀）
      * 分库级：whCodeOrder 形如 "002_896"，variety 为 null
    实测（2026-09-11 豆一）「仓库组 + 独立仓库」之和 = 品种小计，口径自洽。
    """
    d = (date or _detect_date(js)).isoformat()
    entity = (js.get("data") or {}).get("entityList", [])

    # 先收集每个品种下的仓库组编号
    groups: dict[str, set] = {}
    for row in entity:
        vk = (row.get("varietyOrder") or "").strip()
        wc = (row.get("whCodeOrder") or "").strip()
        if vk and re.fullmatch(r"\d{3,4}", wc):
            groups.setdefault(vk, set()).add(wc)

    detail, total, product, sub = [], [], [], []
    for row in entity:
        variety = (row.get("variety") or "").strip()
        wh_code = (row.get("whCodeOrder") or "").strip()
        vk = (row.get("varietyOrder") or "").strip()
        rec = {
            "exchange": EXCHANGE, "date": d,
            "variety": variety,
            "variety_code": vk,
            "warehouse_code": wh_code,
            "warehouse_name": (row.get("whAbbr") or "").strip(),
            "warehouse_type": {"1": "仓库", "2": "厂库", "3": "集团"}.get(
                str(row.get("whType")), row.get("whType")),
            "delivery_place": row.get("deliveryAbbr") or "",
            "last_qty": row.get("lastWbillQty"),
            "reg_qty": row.get("regWbillQty"),
            "logout_qty": row.get("logoutWbillQty"),
            "qty": row.get("wbillQty"),
            "change": row.get("diff"),
        }
        is_child = "_" in wh_code and wh_code.split("_")[0] in groups.get(vk, set())
        if variety == "总计":
            total.append(rec)
        elif variety.endswith("小计"):
            rec["variety"] = variety[:-2]
            rec["level"] = "product"
            product.append(rec)
        elif is_child or not variety:
            rec["level"] = "sub"
            sub.append(rec)
        else:
            rec["level"] = "warehouse"
            detail.append(rec)
    return detail, total, product, sub
