#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""隆众资讯 · 现货价格采集器（真实浏览器，不提取 Cookie）

【为什么必须用浏览器】
  隆众登录挂了网易易盾行为验证码，**服务端强校验**（实测不带 validate 提交直接报
  「动态验证码校验失败」）。但易盾在真实浏览器里是**无感静默通过**的。
  ⇒ 用 playwright 持久化 profile：首次人工登录一次（JWT 有效期 30 天），之后复用。

【关键设计 —— 所有取数都在页面上下文里用 fetch 完成】
  同源 + credentials:'include' ⇒ 规避 Cookie 提取 / TLS 指纹 / IP 绑定 / UA 校验等
  全部坑（这几条纯脚本路线已逐一实测失败）。浏览器发请求 = 和真人点击完全一致。

【三步接口链路】（实测确认）
  1. getChannelTree                       （免登录）        → channelId
  2. queryVarietiesByChannelId?channelId=X（需登录）        → varietiesId
     ⚠️ channelId ≠ varietiesId！沥青 channelId=1733、varietiesId=372。
        直接拿 channelId 当 varietiesId 会取到**别的品种**（实测返回"苹果""对苯二甲胺"）。
  3. queryBusinessTypeByVarietiesId?vId=Y （需登录）        → businessType 列表
     3=市场价格 / 2=企业价格 / 4=国际价格；4 常带 childList 子类
     （原油：22期货价/23国际市场/27现货价/28远期现货/26远期现货升贴水）
     ★ twoLevelBusinessType 就是这里的子类取值，无子类时为 0。
  4. queryPricePage (POST，需登录)        → 真实价格
     body: {varietiesId, businessType, twoLevelBusinessType, timeType:0, pageNum, pageSize}

【响应结构有两种形态，必须都兼容】
  A) 按区域分组：{"东北地区": [行, 行, ...], "华东地区": [...]}      ← 多数品种
  B) 数字键单行：{"0": 行, "1": 行, ...}                          ← 原油等国际价格
  行内价格按**价格分项**嵌套：{"2026/10/14": {"price": {"主流价": "6400"},
                                             "dataRiseOrFall": {"主流价": "0"}}}
  分项可能有多个（主流价/高端价/低端价）。

用法:
    python oilchem_price_collect.py --login        # 首次：打开浏览器人工登录（过易盾）
    python oilchem_price_collect.py --check        # 自检：登录态 + 各品种可用性（不写盘）
    python oilchem_price_collect.py                # 采集全部品种
    python oilchem_price_collect.py --only BU,TA   # 只采指定品种
    python oilchem_price_collect.py --headful      # 采集时也显示窗口（headless 被拦时用）

环境变量:
    AGENTSENSE_OILCHEM_BROWSER_DIR  浏览器 profile 目录（默认 ~/.tradingagents/oilchem_browser）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ─────────────────────────── 配置 ───────────────────────────
def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else default


PROFILE_DIR = _env_path("AGENTSENSE_OILCHEM_BROWSER_DIR",
                        Path.home() / ".tradingagents" / "oilchem_browser")
OUT_DIR = ROOT / "data" / "oilchem_price" / "web"
META_PATH = ROOT / "data" / "oilchem_price" / "meta.json"

DC_ORIGIN = "https://dc.oilchem.net"
API = DC_ORIGIN + "/ndc"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 品种池 → 隆众频道 ID（channelId，来自 getChannelTree；运行时再解析成 varietiesId）
# ★ NR（20号胶）隆众**完全未覆盖**，故不在表内
VARIETY_CHANNELS = {
    "SC": 1673,   # 能源/原油专区/原油
    "TA": 1835,   # 化工/合纤原料/PTA
    "PX": 1820,   # 化工/芳烃/对二甲苯
    "EG": 1834,   # 化工/合纤原料/MEG      ← 隆众叫 MEG，不是"乙二醇"
    "FU": 1689,   # 能源/船舶燃料/内贸船用油
    "LU": 1690,   # 能源/船舶燃料/保税船用油（与 FU 同指 varietiesId=360 船用油）
    "PG": 1718,   # 能源/液化气专区/民用气
    "M": 5917,    # 农产品/蛋白饲料/豆粕
    "CF": 3843,   # 化纤/棉花专区/棉花
    "CJ": 5891,   # 农产品/果蔬/红枣
    "LC": 4942,   # 电池/锂/碳酸锂
    "RU": 1937,   # 橡胶/天然橡胶/干胶     ← 隆众叶子叫"干胶"
    "BR": 1943,   # 橡胶/合成橡胶/顺丁橡胶
    "LH": 5924,   # 农产品/猪业/生猪
    "BU": 1733,   # 能源/沥青/重交沥青     ← varietiesId=372
    "PS": 6032,   # 氟硅/多晶硅
    "SI": 7917,   # 氟硅/工业硅
    "BZ": 1816,   # 化工/芳烃/纯苯
    "EB": 1819,   # 化工/芳烃/苯乙烯
    "P": 9054,    # 油脂化工/棕榈油专区/棕榈油
}

# businessType 优先级：市场价格 > 企业价格 > 国际价格
BT_PRIORITY = ("3", "2")
# 国际价格(businessType=4)只取这些子类（跳过"期货价格"，期货价另有来源）
BT4_KEEP = {"23", "27"}          # 国际市场 / 现货价格
MAX_COMBOS_PER_VARIETY = 4       # 每品种最多取几组(businessType, twoLevel)
PAGE_SIZE = 100                  # ★ 上限 100：实测 200 会被拒（"pageSize数值超限"）
MAX_PAGES = 8                    # 单组合最多翻页数（防异常时死循环）
NAV_TIMEOUT_MS = 45000
FETCH_TIMEOUT_S = 60.0
INTER_REQUEST_SLEEP = 0.8        # 请求间节流（隆众连续快打会限流）

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
]


# ─────────────────────────── 浏览器 ───────────────────────────
def _launch(pw, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        user_agent=UA,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1440, "height": 900},
        args=LAUNCH_ARGS,
    )
    ctx.add_init_script(STEALTH_JS)
    return ctx


def _page(pw, headless: bool):
    ctx = _launch(pw, headless)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(DC_ORIGIN + "/page/", timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    return ctx, page


def _api_get(page, path: str):
    """在页面上下文里 GET 一个 /ndc 接口，返回解析后的 JSON（失败返回 None）。"""
    js = """async (u) => {
        try {
            const r = await fetch(u, {credentials: 'include'});
            return await r.text();
        } catch (e) { return 'ERR ' + String(e); }
    }"""
    raw = page.evaluate(js, API + path)
    if not raw or raw.startswith("ERR"):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _api_post(page, path: str, payload: dict):
    js = """async (a) => {
        try {
            const r = await fetch(a.url, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(a.body)
            });
            return await r.text();
        } catch (e) { return 'ERR ' + String(e); }
    }"""
    raw = page.evaluate(js, {"url": API + path, "body": payload})
    if not raw or raw.startswith("ERR"):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def get_user_id(page) -> int:
    """登录态探针：未登录返回 0（这是最干净的判据）。"""
    d = _api_get(page, "/common/getUserId")
    try:
        return int((d or {}).get("response") or 0)
    except Exception:
        return 0


# ─────────────────────────── 数据解析 ───────────────────────────
def _num(v):
    """把 '6400' / '1,234.5' / '' 归一成 float 或 None。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iter_rows(body_map):
    """兼容 priceBodyMap 的三种形态，统一产出 (分组名, 行dict)。

    实测形态：
      A) dict[region] = [行, 行, ...]   ← 沥青等多数品种（按区域分组）
      B) dict[key]     = 行             ← 少见
      C) list          = [行, 行, ...]  ← **原油等国际价格**（无分组）
    ⚠️ 形态 C 极易漏判：用 JS 的 Object.keys() 看数组也会显示 ["0","1",...]，
       看起来像形态 B，实际是 list。必须同时判 list 与 dict。
    """
    if isinstance(body_map, dict):
        for key, val in body_map.items():
            if isinstance(val, list):
                for row in val:
                    if isinstance(row, dict):
                        yield key, row
            elif isinstance(val, dict):
                yield key, val
    elif isinstance(body_map, list):
        for row in body_map:
            if isinstance(row, dict):
                yield "", row


def parse_price_response(payload: dict, code: str) -> dict | None:
    """把一个 queryPricePage 响应解析成结构化结果。"""
    resp = (payload or {}).get("response") or {}
    dates = resp.get("dateList") or []
    heads = [h.get("cnField") for h in (resp.get("priceHeadList") or [])]
    power = resp.get("pricePowerBO") or {}

    rows, n_dropped = [], 0
    for _grp, r in _iter_rows(resp.get("priceBodyMap")):
        # 每行按日期取出各价格分项；分项名（主流价/高端价…）取自行内
        series: dict = {}
        items: set = set()
        changes: dict = {}
        for d in dates:
            cell = r.get(d)
            if not isinstance(cell, dict):
                continue
            pr = cell.get("price") or {}
            rf = cell.get("dataRiseOrFall") or {}
            day = {}
            for item, val in pr.items():
                items.add(item)
                day[item] = _num(val)
            if day:
                series[d] = day
            for item, val in rf.items():
                changes.setdefault(item, {})[d] = _num(val)
        if not series:
            n_dropped += 1                  # 该行无任何价格（全"询价中"）
            continue
        # 所有分项在所有日期都为空 ⇒ 无有效报价。
        # ★ 账号未开通某品种权限时，服务端**仍返回完整的行骨架，但价格全是空串**，
        #   若不在这里剔除，会产出一批"有行无价"的脏数据（实测 CJ/LH/M/PS/SI 均如此）。
        if not any(v is not None for day in series.values() for v in day.values()):
            n_dropped += 1
            continue

        latest_date = max(series.keys())
        latest = series[latest_date]
        prev_dates = sorted(series.keys())
        prev = series[prev_dates[-2]] if len(prev_dates) > 1 else {}

        rows.append({
            "region": r.get("regionName") or "",
            "market": r.get("marketName") or r.get("internalMarketName") or "",
            "spec": r.get("specificationsName") or "",
            "brand": r.get("brandName") or "",
            "priceType": r.get("priceTypeName") or "",
            "unit": r.get("unitValuationName") or "",
            "businessType": r.get("businessTypeName") or "",
            "twoLevelType": r.get("twoLevelBusinessTypeName") or "",
            "businessId": r.get("businessId"),
            "latest": latest,
            "prev": prev,
            "change": _num(r.get("dataRiseOrFall")),
            "rate": r.get("dataRate") or "",
            "rmbPrice": _num(r.get("rmbPrice")),
            "remark": r.get("remark") or "",
            "series": series,
            "changeSeries": changes,
        })

    if not rows:
        return None

    return {
        "code": code,
        "varietiesId": resp.get("varietiesId"),
        "varietiesName": resp.get("varietiesName") or "",
        "dates": dates,
        "priceItems": sorted({it for row in rows for it in (row["latest"] or {})}),
        "headList": heads,
        "total": resp.get("total"),
        "power": {"status": power.get("status"),
                  "see": power.get("see"), "download": power.get("download")},
        "rows": rows,
        "rowsDropped": n_dropped,
    }


# ─────────────────────────── 采集流程 ───────────────────────────
def resolve_varieties_id(page, channel_id: int):
    d = _api_get(page, "/price/list/queryVarietiesByChannelId?channelId=%d" % channel_id)
    arr = (d or {}).get("response") or []
    if not arr:
        return None
    return int(arr[0]["varietiesId"]), arr[0].get("varietiesName") or ""


def resolve_business_types(page, vid: int) -> list:
    """返回 [(businessType, twoLevelBusinessType, 显示名), ...]"""
    d = _api_get(page, "/price/list/queryBusinessTypeByVarietiesId?varietiesId=%d" % vid)
    avail = (d or {}).get("response") or []
    by_code = {str(x.get("businessType")): x for x in avail}

    combos = []
    for bt in BT_PRIORITY:
        if bt in by_code:
            x = by_code[bt]
            combos.append((bt, 0, x.get("businessTypeName") or ""))
            for c in (x.get("childList") or []):
                combos.append((bt, c.get("businessType"), c.get("businessTypeName") or ""))
    if not combos and "4" in by_code:
        x = by_code["4"]
        subs = x.get("childList") or []
        for c in subs:
            if str(c.get("businessType")) in BT4_KEEP:
                combos.append(("4", c.get("businessType"), c.get("businessTypeName") or ""))
        if not subs:
            combos.append(("4", 0, x.get("businessTypeName") or ""))
    return combos[:MAX_COMBOS_PER_VARIETY]


def _fetch_combo(page, code: str, vid: int, bt: str, t2):
    """取一个 (businessType, twoLevelBusinessType) 组合的全部行（自动翻页）。

    返回 (rows, 最后一份解析结果, 错误信息, 实际页数, 权限对象)。

    ★ 必须把非 200 的 message 透出来。实测 pageSize=200 时服务端返回
      {"status":"400","message":"pageSize数值超限"} —— 若吞掉 message，失败会
      静默表现成"0 行"，极难定位（本项目踩过这个坑）。
    ★ 权限对象单独带出：账号未开通时所有行会被有效值过滤清空，
      parse_price_response 返回 None，若只靠它就拿不到权限信息。
    """
    rows, last_parsed, pages, last_power = [], None, 0, {}
    for pn in range(1, MAX_PAGES + 1):
        payload = {
            "varietiesId": str(vid), "businessType": str(bt),
            "twoLevelBusinessType": int(t2) if t2 else 0,
            "timeType": 0, "pageNum": pn, "pageSize": PAGE_SIZE,
        }
        d = _api_post(page, "/price/list/queryPricePage", payload)
        if d is None:
            return rows, last_parsed, "请求失败(无响应)", pages, last_power
        st = str(d.get("status") or "")
        if st != "200":
            return rows, last_parsed, "HTTP%s %s" % (st, d.get("message") or "?"), pages, last_power
        pw = (d.get("response") or {}).get("pricePowerBO") or {}
        if pw and not last_power:
            last_power = pw
        parsed = parse_price_response(d, code)
        if not parsed:
            break
        last_parsed = parsed
        pages += 1
        rows.extend(parsed["rows"])
        try:
            total = int(parsed["total"]) if parsed.get("total") is not None else None
        except (TypeError, ValueError):
            total = None
        if total is None or len(rows) >= total or not parsed["rows"]:
            break
        time.sleep(INTER_REQUEST_SLEEP * 0.5)
    return rows, last_parsed, "", pages, last_power


def collect_variety(page, code: str, channel_id: int, verbose: bool = True,
                    fails: dict | None = None) -> dict | None:
    def _fail(msg: str):
        if fails is not None:
            fails[code] = msg
        return None

    got = resolve_varieties_id(page, channel_id)
    if not got:
        if verbose:
            print("    ✗ %-4s 频道 %s 无法解析品种 ID" % (code, channel_id))
        return _fail("频道 %s 无法解析品种 ID" % channel_id)
    vid, vname = got

    combos = resolve_business_types(page, vid)
    if not combos:
        if verbose:
            print("    ✗ %-4s %s(vid=%s) 无可用价格类型" % (code, vname, vid))
        return _fail("无可用价格类型")

    merged, notes, meta_pick, power = [], [], None, {}
    for bt, t2, btname in combos:
        rows, parsed, err, pages, pw = _fetch_combo(page, code, vid, bt, t2)
        if pw and not power:
            power = pw
        label = "%s(%s%s)" % (btname or bt, bt, "/" + str(t2) if t2 else "")
        if rows:
            for r in rows:
                r["_bt"] = str(bt)
                r["_btName"] = btname or r.get("businessType") or ""
            merged.extend(rows)
            meta_pick = meta_pick or parsed
            notes.append("%s:%d行%s" % (label, len(rows),
                                        ("/%d页" % pages) if pages > 1 else ""))
        else:
            notes.append("%s:%s" % (label, err or "0行"))
        time.sleep(INTER_REQUEST_SLEEP)

    # 去重（翻页时后端可能重复吐出同一行）
    seen, uniq = set(), []
    for r in merged:
        k = (r.get("businessId"), r.get("region"), r.get("market"),
             r.get("spec"), r.get("brand"), r.get("_bt"), r.get("_btName"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    n_dup = len(merged) - len(uniq)
    merged = uniq

    # ★ 权限门禁：账号未开通该品种时，服务端仍返回行骨架但价格全空（已被上面剔干净）。
    #   这里给出**明确原因**，而不是笼统的"0 行"——否则会被误读成数据缺失。
    #   power 由 _fetch_combo 单独带出，即使所有行都被过滤掉也能拿到。
    if not power:
        power = (meta_pick or {}).get("power") or {}
    no_perm = power.get("see") is False or (
        power.get("status") is not None and power.get("status") not in (9,))
    if no_perm:
        if verbose:
            print("    ⊘ %-4s %-8s vid=%-5s **账号未开通**（power.status=%s, see=%s）— 有行骨架但无价格"
                  % (code, vname, vid, power.get("status"), power.get("see")))
        return _fail("账号未开通（power.status=%s）" % power.get("status"))

    if not merged:
        if verbose:
            print("    ⊘ %-4s %s(vid=%s) 有权限但无价格（%s）" % (code, vname, vid, "、".join(notes)))
        return _fail("有权限但无价格（%s）" % "、".join(notes))

    latest_dates = (meta_pick or {}).get("dates") or []
    if verbose:
        print("    + %-4s %-8s vid=%-5s %3d行%s | %s | 最新 %s"
              % (code, vname, vid, len(merged),
                 ("(去重%d)" % n_dup) if n_dup else "",
                 "、".join(notes), latest_dates[-1] if latest_dates else "?"))

    return {
        "code": code,
        "varietiesId": vid,
        "varietiesName": vname,
        "channelId": channel_id,
        "dates": latest_dates,
        "priceItems": (meta_pick or {}).get("priceItems") or [],
        "headList": (meta_pick or {}).get("headList") or [],
        "power": power,
        "businessTypes": notes,
        "rows": merged,
        "fetchedAt": dt.datetime.now().isoformat(timespec="seconds"),
    }


def build_overview(items: list, failed: dict) -> dict:
    """汇总：每品种按**单位**分组取最新一天的价格区间，供前端总览表。"""
    prods = []
    for it in items:
        rows = it.get("rows") or []
        # ★ 必须按单位分组统计。同一品种可能混用不同单位（船用油实测是「美元/吨」，
        #   其余多为「元/吨」），不分单位直接算均值会把不可比的数加在一起。
        buckets: dict = {}
        for r in rows:
            u = r.get("unit") or ""
            for v in (r.get("latest") or {}).values():
                if v is not None:
                    buckets.setdefault(u, []).append(v)
        by_unit = [{"unit": u, "n": len(v), "min": min(v), "max": max(v),
                    "avg": round(sum(v) / len(v), 2)}
                   for u, v in sorted(buckets.items()) if v]
        main = max(by_unit, key=lambda x: x["n"]) if by_unit else {
            "unit": "", "min": None, "max": None, "avg": None, "n": 0}
        prods.append({
            "code": it["code"],
            "name": it["varietiesName"],
            "varietiesId": it["varietiesId"],
            "rows": len(rows),
            "regions": sorted({r.get("region") for r in rows if r.get("region")}),
            "specs": sorted({r.get("spec") for r in rows if r.get("spec")})[:12],
            "unit": main["unit"],
            "min": main["min"],
            "max": main["max"],
            "avg": main["avg"],
            "byUnit": by_unit,
            "dates": it.get("dates") or [],
            "noPermission": False,
        })
    # 无数据的品种也列出（前端需要提示"未开通"，而不是静静消失）
    for code, reason in (failed or {}).items():
        prods.append({
            "code": code, "name": "", "varietiesId": None, "rows": 0,
            "regions": [], "specs": [], "unit": "", "min": None, "max": None,
            "avg": None, "byUnit": [], "dates": [],
            "noPermission": "未开通" in str(reason),
            "note": reason,
        })
    prods.sort(key=lambda x: x["code"])
    ok = [p for p in prods if not p.get("note")]
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "隆众资讯 / dc.oilchem.net",
        "products": prods,
        "failed": failed,
        "productCount": len(ok),
        "rows": sum(p["rows"] for p in prods),
    }


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ─────────────────────────── 三种模式 ───────────────────────────
def mode_login() -> int:
    from playwright.sync_api import sync_playwright
    print("=" * 64)
    print(" 隆众资讯 · 首次登录")
    print(" profile:", PROFILE_DIR)
    print("=" * 64)
    with sync_playwright() as pw:
        ctx = _launch(pw, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://member.oilchem.net/", timeout=NAV_TIMEOUT_MS,
                  wait_until="domcontentloaded")
        print("\n请在打开的窗口里登录（账号 yonganqihuo）。")
        print("登录成功后本脚本会自动检测并退出。最多等 5 分钟…\n")
        ok = False
        for i in range(100):
            time.sleep(3)
            try:
                if get_user_id(page) > 0:
                    ok = True
                    break
            except Exception:
                pass
        if ok:
            print("✅ 登录成功，凭据已存入 profile（有效期约 30 天）。")
        else:
            print("⚠️ 5 分钟内未检测到登录态，profile 未更新。")
        ctx.close()
    return 0 if ok else 1


def mode_check(only: list | None, headless: bool) -> int:
    from playwright.sync_api import sync_playwright
    codes = only or sorted(VARIETY_CHANNELS)
    with sync_playwright() as pw:
        ctx, page = _page(pw, headless)
        uid = get_user_id(page)
        print("登录态 getUserId =", uid, "（0 = 未登录，需先 --login）")
        if uid <= 0:
            ctx.close()
            return 1
        print("\n%-5s %-10s %-7s %-8s %s" % ("码", "隆众名", "vid", "权限", "可用价格类型"))
        print("-" * 70)
        for code in codes:
            ch = VARIETY_CHANNELS.get(code)
            got = resolve_varieties_id(page, ch) if ch else None
            if not got:
                print("%-5s %-10s %-7s %-8s %s" % (code, "-", "-", "-", "无法解析"))
                continue
            vid, vname = got
            combos = resolve_business_types(page, vid)
            # ★ 权限必须实测：只看 businessType 列表会误判——账号未开通时
            #   服务端照样返回价格类型清单，只是价格全空（实测 CJ/LH/M/PS/SI）。
            probe = _api_post(page, "/price/list/queryPricePage", {
                "varietiesId": str(vid),
                "businessType": (combos[0][0] if combos else "3"),
                "twoLevelBusinessType": (combos[0][1] if combos else 0),
                "timeType": 0, "pageNum": 1, "pageSize": 1})
            pw = ((probe or {}).get("response") or {}).get("pricePowerBO") or {}
            perm = "✓" if pw.get("status") == 9 else "✗未开通"
            desc = "、".join("%s(%s%s)" % (n, b, "/" + str(t) if t else "")
                             for b, t, n in combos) or "无"
            print("%-5s %-10s %-7s %-8s %s" % (code, vname, vid, perm, desc))
            time.sleep(0.3)
        ctx.close()
    return 0


def mode_collect(only: list | None, headless: bool, dry_run: bool) -> int:
    from playwright.sync_api import sync_playwright
    codes = only or sorted(VARIETY_CHANNELS)
    items, failed = [], {}

    with sync_playwright() as pw:
        ctx, page = _page(pw, headless)
        uid = get_user_id(page)
        if uid <= 0:
            print("✗ 未登录（getUserId=0）。请先运行：python %s --login"
                  % Path(__file__).name)
            ctx.close()
            return 1
        print("登录态 OK (userId=%d) | 待采 %d 个品种\n" % (uid, len(codes)))

        for code in codes:
            ch = VARIETY_CHANNELS.get(code)
            if not ch:
                failed[code] = "不在品种映射表内"
                continue
            try:
                it = collect_variety(page, code, ch, fails=failed)
            except Exception as ex:                      # noqa: BLE001
                failed[code] = "异常: %s" % str(ex)[:120]
                print("    ✗ %-4s 异常 %s" % (code, str(ex)[:100]))
                it = None
            if it:
                items.append(it)
            else:
                failed.setdefault(code, "无数据或不可用")
            time.sleep(INTER_REQUEST_SLEEP)
        ctx.close()

    overview = build_overview(items, failed)
    print("\n── 结果 ──")
    print("成功 %d / 尝试 %d，失败 %d：%s"
          % (len(items), len(codes), len(failed),
             "、".join("%s(%s)" % (k, v) for k, v in failed.items()) or "无"))

    if dry_run:
        print("（--dry-run 未写盘）")
        return 0

    for it in items:
        _atomic_write(OUT_DIR / ("%s.json" % it["code"]), it)
    _atomic_write(OUT_DIR / "overview.json", overview)
    _atomic_write(META_PATH, {
        "generated_at": overview["generated_at"],
        "products": overview["productCount"],
        "rows": sum(p["rows"] for p in overview["products"]),
        "failed": failed,
    })
    total_rows = sum(p["rows"] for p in overview["products"])
    print("已写入 %s（%d 个品种 / %d 行）" % (OUT_DIR, overview["productCount"], total_rows))
    return 0


# ─────────────────────────── 入口 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="隆众资讯现货价格采集器（真实浏览器）")
    ap.add_argument("--login", action="store_true", help="打开浏览器人工登录（首次必做）")
    ap.add_argument("--check", action="store_true", help="自检登录态与品种可用性，不写盘")
    ap.add_argument("--only", default="", help="只采指定品种，逗号分隔，如 BU,TA")
    ap.add_argument("--headful", action="store_true", help="采集时显示浏览器窗口")
    ap.add_argument("--dry-run", action="store_true", help="采集但不写盘")
    a = ap.parse_args()

    only = [x.strip().upper() for x in a.only.split(",") if x.strip()] or None
    if only:
        unknown = [c for c in only if c not in VARIETY_CHANNELS]
        if unknown:
            print("✗ 未知品种: %s（已支持: %s）"
                  % (",".join(unknown), ",".join(sorted(VARIETY_CHANNELS))))
            return 2

    if a.login:
        return mode_login()
    if a.check:
        return mode_check(only, headless=not a.headful)
    return mode_collect(only, headless=not a.headful, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
