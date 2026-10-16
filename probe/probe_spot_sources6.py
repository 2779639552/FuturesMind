# -*- coding: utf-8 -*-
"""探针 8: 攻 SC(原油)/LU(低硫燃料油)/CJ(红枣) —— 100ppi 站内页 + 东财接口。"""
import re
import warnings

import requests

warnings.filterwarnings("ignore")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.100ppi.com/"}


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


sec("1. 100ppi 站内搜索/价格页 找 原油 / 红枣 / 低硫燃料油")
CANDS = [
    "https://www.100ppi.com/sf/",
    "https://www.100ppi.com/sf/day-2026-09-29.html",
    "https://www.100ppi.com/mprice/",
    "https://www.100ppi.com/price/",
]
for u in CANDS:
    try:
        r = requests.get(u, headers=H, timeout=25)
        txt = r.text
        print(f"\n  {u} -> {r.status_code} len={len(txt)}")
        for kw in ("原油", "红枣", "低硫燃料油", "纯苯", "对二甲苯"):
            print(f"     含「{kw}」: {kw in txt}", end="")
        print()
        if "sf/day" in u and r.status_code == 200:
            # 抓表头符号
            syms = re.findall(r">\s*([A-Z]{2})\s*<", txt)
            print("     页面内候选符号:", sorted(set(syms))[:70])
    except Exception as e:  # noqa: BLE001
        print(f"  {u} -> ERR {type(e).__name__}: {str(e)[:90]}")

sec("2. 东财 现货/基差 私有接口探测")
EM = [
    ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_FUTU_BASIS&columns=ALL&pageSize=200",
     "东财基差表"),
    ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&fs=m:113,m:114,m:115&fields=f12,f13,f14",
     "东财期货现货列表"),
]
for u, tag in EM:
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=25)
        print(f"  [{tag}] {r.status_code} -> {r.text[:220]}")
    except Exception as e:  # noqa: BLE001
        print(f"  [{tag}] ERR {type(e).__name__}: {str(e)[:90]}")

sec("3. 新浪 现货基差相关端点")
SINA = [
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQFuturesSpot",
    "https://hq.sinajs.cn/list=nf_SC0",
]
for u in SINA:
    try:
        r = requests.get(u, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}, timeout=20)
        print(f"  {u[:70]} -> {r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {u[:70]} -> ERR {type(e).__name__}: {str(e)[:80]}")
