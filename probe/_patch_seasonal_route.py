# -*- coding: utf-8 -*-
"""在 web_app.py 的 /api/gtja/coverage 之前插入 /api/gtja/seasonal 路由。

web_app.py 是 CRLF —— 必须 newline="" 读写并**逐字节保持 CRLF**, 否则行尾被改写。
"""
import io

P = "web_app.py"
src = io.open(P, encoding="utf-8", newline="").read()
assert "\r\n" in src and "\n" not in src.replace("\r\n", ""), "web_app.py 不是纯 CRLF, 中止"

ANCHOR = '@app.route("/api/gtja/coverage")\r\n'
assert src.count(ANCHOR) == 1, "锚点命中 %d 次" % src.count(ANCHOR)

BLOCK = '''@app.route("/api/gtja/seasonal")
def api_gtja_seasonal():
    """单指标季节性图数据: 多年历史 → 按年分组(年内天数 → 值)。

    【参数】variety(必填) / id(必填, 与 overview 的 indicators[].id 一致) / years(可选, 1~5)。
    【返回】gtja_module.fetch_indicator_seasonal 的 payload(200)。
    【为什么单独一个接口】overview 的序列被裁到最近 250 点、基差默认只查 175 天,
      只够画"近半年"; 季节性图要的是**同一指标跨多年的同日对齐**, 必须单独回取全历史。
      该接口有 6h 进程内缓存(基差/仓单要分片请求, 不能每点一次都打上游)。
    【失败】一律 200 + {"ok":False,"error":...}, 前端保持"本地单年图", 不白屏。
    """
    code = (request.args.get("variety") or "").strip().upper()
    iid = (request.args.get("id") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "缺少品种参数(variety)"}), 400
    if not iid:
        return jsonify({"ok": False, "error": "缺少指标参数(id)"}), 400
    try:
        years = int(request.args.get("years") or 5)
    except (TypeError, ValueError):
        years = 5
    years = max(1, min(years, 5))  # 【护栏】上限与 gtja_module.SEASONAL_YEARS 一致
    try:
        import gtja_module  # 【调用包】聚合层(局部导入, 不拖慢主模块加载)

        payload = gtja_module.fetch_indicator_seasonal(code, iid, years)
    except Exception as exc:  # noqa: BLE001 - 聚合层异常按接口错误上报, 不让 500 冒泡
        logger.warning("GTJA seasonal 失败(%s/%s): %s", code, iid, exc, exc_info=True)
        return jsonify({"ok": False, "error": f"取数失败: {type(exc).__name__}: {exc}"[:200]}), 200
    return jsonify(payload)


'''.replace("\n", "\r\n")

src = src.replace(ANCHOR, BLOCK + ANCHOR, 1)
io.open(P, "w", encoding="utf-8", newline="").write(src)
print("OK 插入路由 ->", P)
