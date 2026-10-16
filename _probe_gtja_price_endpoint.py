"""探测国泰君安 cloudApi 是否存在"价格/行情"类数据集端点。

判据(gtja_api.py docstring 记录的 2026-09-28 方法):
  POST {base}/api/unicorn.cloudApi.{ep}.do
    300001 = 端点存在(且 msg 会吐出必填参数名)
    300004 = 接口不存在
"""
import json
import time
import urllib.request
import urllib.error
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tradingagents", "dataflows"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

BASE = os.getenv("GTJA_BASE_URL", "https://vip.gtjaqh.com").rstrip("/")
KID = os.getenv("GTJA_ACCESS_KEY_ID", "")
KSEC = os.getenv("GTJA_ACCESS_KEY_SECRET", "")
print(f"BASE={BASE}  configured={bool(KID and KSEC)}")

CANDIDATES = [
    "priceData.query", "quoteData.query", "quotesData.query",
    "klineData.query", "kline.query", "dailyKlineData.query",
    "futuresPriceData.query", "futuresQuoteData.query",
    "marketData.query", "dailyData.query", "futuresData.query",
    "contractData.query", "realtimeData.query", "realtimeQuoteData.query",
    "trendData.query", "indexData.query", "mainContractData.query",
    "futuresDailyData.query", "futureQuoteData.query", "quoteHistoryData.query",
]


def probe(ep: str) -> tuple[str, str]:
    url = f"{BASE}/api/unicorn.cloudApi.{ep}.do"
    body = json.dumps({"page": "1", "size": "10"}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "accessKeyId": KID, "accessKeySecret": KSEC,
        "User-Agent": "Mozilla/5.0",
    }, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
        dt = time.time() - t0
        try:
            j = json.loads(raw)
            code = str(j.get("code"))
            msg = str(j.get("msg") or j.get("message") or "")[:80]
        except Exception:
            code, msg = "?", raw[:80]
        return code, f"{msg} ({dt:.1f}s)"
    except urllib.error.HTTPError as e:
        return f"HTTP{e.code}", f"({time.time()-t0:.1f}s)"
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {str(e)[:60]} ({time.time()-t0:.1f}s)"


print("\n=== 端点存在性探测 ===")
for ep in CANDIDATES:
    code, note = probe(ep)
    mark = "★存在" if code == "300001" else ("?" if code not in ("300004", "ERR", "HTTP404") else "")
    print(f"  {ep:32} code={code:>6}  {note}  {mark}")
