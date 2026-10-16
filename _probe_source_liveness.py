"""数据源在线存活探测：对每个生产源发一次真实请求，记录状态码与延迟。
判读：200/401/403 = 站点活着（401/403 多为需鉴权，属正常）；
      超时/连接失败 = 不可达。
"""
import ssl
import time
import json
import urllib.request
import urllib.error

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

TARGETS = [
    ("华泰期货·天玑(研报)", "https://ent.htfc.com/"),
    ("国泰君安·云API(研报/基差)", "https://vip.gtjaqh.com/"),
    ("东证·繁微Fiona MCP(研报/观点)", "https://track.finoview.com.cn/fiona/mcp/"),
    ("生意社100ppi(现货)", "https://www.100ppi.com/"),
    ("上期所SHFE(仓单)", "https://www.shfe.com.cn/"),
    ("郑商所CZCE(仓单)", "https://www.czce.com.cn/"),
    ("大商所DCE(仓单)", "https://www.dce.com.cn/"),
    ("广期所GFEX(仓单)", "https://www.gfex.com.cn/"),
    ("微信公众号(合集通道)", "https://mp.weixin.qq.com/"),
    ("DeepSeek官方(LLM)", "https://api.deepseek.com/"),
    ("Google News RSS(彭博新闻,未接入)", "https://news.google.com/rss"),
]

print(f"{'数据源':34} {'HTTP':>6} {'耗时':>8}  判读")
print("-" * 78)
for name, url in TARGETS:
    t0 = time.time()
    code, note = "-", ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            code = r.status
        note = "可达"
    except urllib.error.HTTPError as e:
        code = e.code
        note = {401: "可达(需鉴权)", 403: "可达(被拒/需鉴权)",
                404: "可达(路径不存在)", 429: "可达(限流)"}.get(e.code, "可达(非200)")
    except Exception as e:
        note = f"不可达: {type(e).__name__}"
    dt = time.time() - t0
    print(f"{name:34} {str(code):>6} {dt:>7.2f}s  {note}")

print()
print("=== 繁微 Fiona MCP 具体端点探测（viewpoint 匿名可用）===")
for ep in ["viewpoint/search_views", "viewpoint/search_dynamics", "report/search"]:
    u = f"https://track.finoview.com.cn/fiona/mcp/{ep}"
    t0 = time.time()
    try:
        req = urllib.request.Request(u, data=json.dumps({}).encode(), headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Mozilla/5.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            body = r.read(160).decode("utf-8", "replace")
            print(f"  {ep:26} HTTP {r.status}  {time.time()-t0:.2f}s  {body[:110]!r}")
    except urllib.error.HTTPError as e:
        print(f"  {ep:26} HTTP {e.code}  {time.time()-t0:.2f}s  (鉴权/参数问题)")
    except Exception as e:
        print(f"  {ep:26} 失败 {type(e).__name__}  {time.time()-t0:.2f}s")
