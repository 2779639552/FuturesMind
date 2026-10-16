# -*- coding: utf-8 -*-
"""探针 6: 直连 99 期货网(99qh.com) 现货走势接口, 验证对缺口品种的覆盖。

接口: https://centerapi.fx168api.com/app/qh/api/spot/trend?productId=<id>
品种表: https://www.99qh.com/data/spotTrend 的 __NEXT_DATA__.varietyListData[].productList

返回 ['日期','期货收盘价','现货价格'], 可自算 basis / basis_rate。
"""
import json
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TARGETS = {"SC": "原油", "LU": "低硫燃料油", "FU": "燃料油", "CJ": "红枣",
           "PX": "对二甲苯", "BZ": "纯苯", "NR": "20号胶", "CF": "棉花", "EG": "乙二醇"}


def get_token():
    r = requests.get("https://centerapi.fx168api.com/app/common/v.js",
                     headers={"Origin": "https://www.99qh.com", "Referer": "https://www.99qh.com",
                              "User-Agent": UA}, timeout=30)
    return r.headers.get("_pcc")


def get_items():
    r = requests.get("https://www.99qh.com/data/spotTrend", headers={"User-Agent": UA}, timeout=30)
    soup = BeautifulSoup(r.text, features="lxml")
    tag = soup.find(name="script", attrs={"id": "__NEXT_DATA__"})
    if tag is None:
        print("  [ERR] 未找到 __NEXT_DATA__")
        return []
    data = json.loads(tag.text)
    rows = []
    for it in data["props"]["pageProps"]["data"]["varietyListData"]:
        for p in it.get("productList", []):
            rows.append({"exchange": p.get("qhExchangeName"), "name": p.get("name"),
                         "productId": p.get("productId")})
    return rows


def main():
    print("=" * 74)
    print("1. 取品种-ID 表")
    print("=" * 74)
    items = get_items()
    print(f"  OK: 共 {len(items)} 个品种")
    name2id = {i["name"]: i["productId"] for i in items}
    for code, cn in TARGETS.items():
        print(f"    {code:<5}{cn:<10} -> {name2id.get(cn, '未收录')}")

    token = get_token()
    print(f"\n  token: {'已获取' if token else '获取失败'}")

    print("\n" + "=" * 74)
    print("2. 逐个取现货序列")
    print("=" * 74)
    ok_codes = []
    for code, cn in TARGETS.items():
        pid = name2id.get(cn)
        if pid is None:
            print(f"  [缺] {code}({cn}): 表中无此品种")
            continue
        try:
            r = requests.get("https://centerapi.fx168api.com/app/qh/api/spot/trend",
                             params={"productId": pid, "pageNo": "1", "pageSize": "50000",
                                     "startDate": "", "endDate": "2050-01-01", "appCategory": "web"},
                             headers={"User-Agent": UA, "_pcc": token or "",
                                      "Origin": "https://www.99qh.com", "Referer": "https://www.99qh.com"},
                             timeout=40)
            j = r.json()
            lst = (j.get("data") or {}).get("list") or []
            n = len(lst)
            if n == 0:
                print(f"  [空] {code}({cn}) pid={pid}: 0 行  resp={str(j)[:150]}")
                continue
            lst_sorted = sorted(lst, key=lambda x: str(x.get("date")))
            last = lst_sorted[-1]
            sp, fp = last.get("sp"), last.get("fp")
            rate = (sp - fp) / sp if sp else None
            print(f"  [OK] {code}({cn}) pid={pid}: n={n} 末行 {last.get('date')} "
                  f"sp={sp} fp={fp} basis={None if sp is None else round(sp - fp, 2)} "
                  f"rate={None if rate is None else round(rate, 6)}")
            ok_codes.append(code)
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {code}({cn}): {type(e).__name__}: {str(e)[:120]}")

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    print(f"  可取到序列的: {ok_codes}")
    miss = [c for c in ["SC", "LU", "CJ", "NR"] if c not in ok_codes]
    print(f"  缺口品种仍缺的: {miss or '无 —— 全部可补!'}")


if __name__ == "__main__":
    main()
