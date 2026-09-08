"""dongzheng_api.py — 东证期货「繁微 Fiona」MCP 客户端(viewpoint 等端点)

【模块角色】
  对接繁微 Fiona 的 streamable-http MCP 端点(track.finoview.com.cn/fiona/mcp/),
  为 research_collector_dongzheng.py 提供东证观点数据拉取,与 gtja_api.py /
  research_data.py(华泰天玑)同层。

  实测(2026-09-08):viewpoint / futures_market_data / futures_ranking /
  **report 四端点匿名可调用**(tools/list + tools/call 全通);
  rating_prediction(市场预期,本 skill 主 MCP)与 news/price_structure/dzlabel
  必须 Bearer token(401)。token 由 env FIONA_MCP_TOKEN 提供(Claude Code MCP
  注册同样引用该变量,一处配置两端生效)。

  【report 端点】东证研报库(2026-09-08 实测 14357 份):report_search(
  keyword/author/product_name/industry_name/start_date/end_date 过滤)→
  report_get_detail(摘要全文)→ report_get_url(带 token 的 PDF 直链,
  requests 可直接下载)。**这是东证采集器的主内容源**(真研报 + PDF,走与
  国君/华泰完全相同的文本提取/版面/视觉重述/RAG 管线)。

  【能力边界】viewpoint 只有观点库/周度观点/动态快评,**没有研报 PDF 下载**;
  动态快评是纯文本(可带图片链接),入库走 .md 文本文件而非 PDF。

【MCP 协议】streamable-http:POST JSON-RPC,Accept 须带 text/event-stream,
  响应可能是 SSE(data: {...})或裸 JSON,两种都要能解析;无会话保持需求
  (服务端未返回 Mcp-Session-Id 也照常工作,实测每次调用独立)。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

MCP_BASE = os.environ.get(
    "FIONA_MCP_BASE", "https://track.finoview.com.cn/fiona/mcp/"
).rstrip("/")  # 【变量】繁微 MCP 端点前缀(env 可覆盖,测试用)
REQUEST_TIMEOUT = 60  # 【变量】单次 MCP 调用超时(秒)
VIEWPOINT_ENDPOINT = "viewpoint"  # 【变量】观点库端点名(匿名可用)
REPORT_ENDPOINT = "report"  # 【变量】东证研报库端点名(匿名可用,采集器主源)


def configured() -> bool:
    """是否可用:匿名即可调用 viewpoint,恒 True(保留函数与 gtja_api 同构)。"""
    return True


def _rpc(endpoint: str, method: str, params: dict, req_id: int = 1, timeout: int = REQUEST_TIMEOUT) -> dict:
    """一次 JSON-RPC 调用(无状态);返回 result,错误抛 RuntimeError。

    【关键逻辑】Accept 必须同时带 application/json 与 text/event-stream
              (streamable-http 规范要求,缺 event-stream 服务端 4xx);
              响应按 SSE(data: 行)或裸 JSON 自适应解析。
    """
    url = f"{MCP_BASE}/{endpoint}"
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    token = os.environ.get("FIONA_MCP_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - 网络层错误统一转 RuntimeError(调用方按"接口失败"处理)
        raise RuntimeError(f"Fiona MCP {endpoint} 请求失败: {e}") from e
    # SSE 响应取最后一个 data: 行;裸 JSON 直接 parse
    payload = None
    if raw.lstrip().startswith("{"):
        payload = raw
    else:
        for m in re.finditer(r"^data:\s*(.+)$", raw, re.M):
            payload = m.group(1)
    if not payload:
        raise RuntimeError(f"Fiona MCP {endpoint} 响应无 data 载荷: {raw[:200]}")
    try:
        out = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Fiona MCP {endpoint} 响应非 JSON: {payload[:200]}") from e
    if out.get("error"):
        raise RuntimeError(f"Fiona MCP {endpoint} RPC 错误: {out['error']}")
    return out.get("result") or {}


def _tools_call(endpoint: str, name: str, arguments: dict | None = None) -> dict:
    """调用端点上的一个 MCP 工具,返回结构化结果(dict)。

    【结果提取】优先 structuredContent;退化取首个 text content 并尝试 JSON
              解析;都不满足则抛错(调用方按接口失败处理)。
    """
    result = _rpc(endpoint, "tools/call", {"name": name, "arguments": arguments or {}})
    if result.get("isError"):
        raise RuntimeError(f"Fiona MCP 工具 {name} 返回 isError")
    sc = result.get("structuredContent")
    if isinstance(sc, dict):
        return sc
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            try:
                parsed = json.loads(c.get("text") or "")
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                break
    raise RuntimeError(f"Fiona MCP 工具 {name} 无结构化结果")


def fetch_dynamics(start_date: str | None = None, end_date: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
    """动态快评列表(发布日期区间过滤,含标题/正文/作者/catalogue/图片链接)。

    【参数】start_date/end_date: YYYY-MM-DD(闭区间,None=服务端默认全部);
            limit/offset: 分页(实测 limit 大值可行,服务端有 total 回显)。
    【返回】items 列表,每条含 source_id/title/text/catalogue/publish_time/
            authors/pictures/link;接口空或失败抛异常由调用方兜底。
    """
    args: dict = {"limit": int(limit), "offset": int(offset)}
    if start_date:
        args["start_date"] = start_date
    if end_date:
        args["end_date"] = end_date
    out = _tools_call(VIEWPOINT_ENDPOINT, "viewpoint_search_dynamics", args)
    items = out.get("items")
    return items if isinstance(items, list) else []


def fetch_dynamic_detail(source_id: int) -> dict:
    """单条动态全文(含图片,列表 text 若被截断用本接口补全)。"""
    return _tools_call(VIEWPOINT_ENDPOINT, "viewpoint_get_dynamic_detail", {"source_id": int(source_id)})


def fetch_views(freq: str = "weekly", limit: int = 100, offset: int = 0) -> list[dict]:
    """周期/年度观点列表(view_id/product_name/product_code/view_grade/
    forecast/current/risk 文本/研究员/prediction 窗口)。freq: weekly|monthly|yearly。"""
    out = _tools_call(VIEWPOINT_ENDPOINT, "viewpoint_search_views",
                      {"freq": freq, "limit": int(limit), "offset": int(offset)})
    items = out.get("items")
    return items if isinstance(items, list) else []


def fetch_enums() -> dict:
    """枚举目录 {categories, researchers, catalogues}(筛选辅助)。"""
    return _tools_call(VIEWPOINT_ENDPOINT, "viewpoint_get_enums", {})


# ── report 端点(东证研报库,采集器主源) ─────────────────────────────────

def fetch_reports(start_date: str | None = None, end_date: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    """研报列表(按撰写日期区间过滤,含标题/作者/类型/板块/评级/摘要/关联品种)。

    【参数】start_date/end_date: YYYY-MM-DD;limit/offset: 分页。
    【返回】items 列表,每条含 report_id/title/author/write_date/type_name/
            industry_name/rating_value/forcast_value/summary/product_names;
            另有 total(调用方如需分页可自查)。
    """
    args: dict = {"limit": int(limit), "offset": int(offset)}
    if start_date:
        args["start_date"] = start_date
    if end_date:
        args["end_date"] = end_date
    out = _tools_call(REPORT_ENDPOINT, "report_search", args)
    items = out.get("items")
    return items if isinstance(items, list) else []


def fetch_report_detail(report_id: int) -> dict:
    """单篇研报完整详情(摘要不截断)。"""
    return _tools_call(REPORT_ENDPOINT, "report_get_detail", {"report_id": int(report_id)})


def fetch_report_url(report_id: int) -> dict:
    """研报 PDF 下载地址 {report_id, title, pdf_url, write_date}。

    【注意】pdf_url 自带时效 token,下载须尽快;实测 urllib 会 IncompleteRead,
            用 requests(带重试)稳定。
    """
    return _tools_call(REPORT_ENDPOINT, "report_get_url", {"report_id": int(report_id)})


def fetch_report_enums() -> dict:
    """研报枚举 {types, industries, ratings, forcasts, futures, researchers}。"""
    return _tools_call(REPORT_ENDPOINT, "report_get_enums", {})
