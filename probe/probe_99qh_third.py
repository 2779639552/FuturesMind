# -*- coding: utf-8 -*-
"""探针: 99期货网(fx168api) 现货走势接口 —— 能否覆盖 SC/LU/CJ 三个缺口品种
只读取证，不写任何生产文件。
"""
import json
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"Origin": "https://www.99qh.com", "Referer": "https://www.99qh.com",
     "User-Agent": UA}

print("=" * 72)
print("STEP 1  取 _pcc token:  https://centerapi.fx168api.com/app/common/v.js")
print("=" * 72)
try:
    r = requests.get("https://centerapi.fx168api.com/app/common/v.js",
                     headers=H, timeout=20)
    print("  status =", r.status_code)
    print("  len    =", len(r.content))
    print("  all headers:")
    for k, v in r.headers.items():
        print("     %-22s %s" % (k, v[:90]))
    tok = r.headers.get("_pcc")
    print("  _pcc   =", (tok[:40] + "...") if tok else "【无】")
    print("  body[:200] =", r.text[:200].replace("\n", " "))
except Exception as e:
    tok = None
    print("  ERR:", type(e).__name__, str(e)[:200])

print()
print("=" * 72)
print("STEP 2  取品种 name -> productId 映射")
print("=" * 72)
pmap = {}
import akshare as ak
try:
    df = ak.spot_price_table_qh()
    print("  品种表 shape:", df.shape)
    # 直接走 akshare 内部函数拿 productId
    import inspect
    import pathlib
    src = pathlib.Path(inspect.getsourcefile(ak.spot_price_qh)).read_text(encoding="utf-8")
    # 找 __get_item_of_spot_price_qh 的真实实现文件
    for m in ["_qh", "qh"]:
        pass
    # 尝试调用私有函数
    fn = getattr(ak.spot_price_qh, "__globals__", {})
    inner = fn.get("__get_item_of_spot_price_qh") or fn.get("_spot_price_qh__get_item_of_spot_price_qh")
    if inner is None:
        # 名字改写为 _模块名__函数名
        for k, v in fn.items():
            if "get_item_of_spot_price_qh" in k:
                inner = v
                print("  找到内部函数:", k)
                break
    if inner is not None:
        idf = inner()
        print("  内部表 cols:", list(idf.columns))
        if "name" in idf.columns and "productId" in idf.columns:
            pmap = dict(zip(idf["name"].astype(str), idf["productId"]))
            print("  productId 映射数:", len(pmap))
            for nm in ["原油", "低硫燃料油", "红枣", "燃料油", "石油沥青"]:
                print("     %-8s -> %s" % (nm, pmap.get(nm, "【不在表中】")))
        else:
            print("  内部表无 name/productId 列 -> 上游结构已变")
    else:
        print("  未能定位内部函数")
except Exception as e:
    print("  ERR:", type(e).__name__, str(e)[:200])
    idf = None

print()
print("=" * 72)
print("STEP 3  直连现货走势接口 (这是 akshare 报 TypeError 的那一步)")
print("=" * 72)
URL = "https://centerapi.fx168api.com/app/qh/api/spot/trend"
for nm in ["原油", "低硫燃料油", "红枣"]:
    pid = pmap.get(nm)
    if pid is None:
        print("  %-8s 跳过(无 productId)" % nm)
        continue
    hh = dict(H)
    if tok:
        hh["_pcc"] = tok
    params = {"productId": pid, "pageNo": "1", "pageSize": "10",
              "startDate": "", "endDate": "2050-01-01", "appCategory": "web"}
    try:
        rr = requests.get(URL, params=params, headers=hh, timeout=25)
        ct = rr.headers.get("Content-Type", "")
        print("  ---- %s (productId=%s) ----" % (nm, pid))
        print("     status=%s  ctype=%s  len=%s" % (rr.status_code, ct, len(rr.content)))
        try:
            j = rr.json()
            print("     JSON 顶层键:", list(j.keys()) if isinstance(j, dict) else type(j))
            if isinstance(j, dict):
                print("     code=%s msg=%s" % (j.get("code"), j.get("msg") or j.get("message")))
                d = j.get("data")
                if isinstance(d, dict):
                    lst = d.get("list")
                    print("     data.list 长度:", len(lst) if isinstance(lst, list) else lst)
                    if isinstance(lst, list) and lst:
                        print("     首行:", json.dumps(lst[0], ensure_ascii=False)[:200])
                else:
                    print("     data 类型:", type(d).__name__, str(d)[:200])
        except Exception as je:
            print("     非 JSON! 前 200 字节:", rr.text[:200].replace("\n", " "))
            print("     json err:", je)
    except Exception as e:
        print("  %-8s ERR: %s %s" % (nm, type(e).__name__, str(e)[:160]))

print()
print("完成")
