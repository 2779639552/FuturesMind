"""htfc_api.py — 华泰期货天玑研报 API 轻量客户端(vendored)

【模块角色】
  从华泰期货官方"天玑"平台(ent.htfc.com)拉研报数据。本源文件由官方技能包
  `.claude/skills/htfc-research-report-skill/scripts/get_data.py` 精简 vendored
  而来:只保留研报列表/详情所需的函数(分类/列表/详情),去掉用户/偏好/下载等
  采集器用不到的部分;HTTP 层走同目录 `htfc_requests.py`(纯 stdlib urllib,
  无第三方依赖,规避 requests 的代理/TLS 指纹问题)。

  【与技能原版的差异】
    - 删掉原版 `os.environ["no_proxy"] = "*"`(会静默绕过项目全部代理设置);
    - 相对导入 `. _compat_requests` 改为平级导入 `htfc_requests`;
    - 只保留 request_api / data_of / 分类 v2 / 列表 specificList / 详情 reportInfo。
  【密钥】环境变量 HTFC_BASE_URL + HTFC_API_KEY;API key 以 `apikey` header 发送。

  复用方只应关心三件事:
    search_reports(item_value, cur_page, page_size) -> {"data": {"resultList": [...]}}
    get_report_info(article_id, item_value)         -> {"data": {title/content/...}}
    get_report_product_types_v2()                    -> {"data": {...}}
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urljoin

import htfc_requests  # 【调用包】vendored stdlib HTTP 兼容层(与原技能 _compat_requests 同源)

API_BASE_URL = os.getenv("HTFC_BASE_URL", "").rstrip("/")  # 【变量】天玑接口根(env,web_app 已 load .env)
TIMEOUT_SECONDS = 60  # 【变量】单次请求超时(秒)
SUCCESS_CODES = {"0", "200"}  # 【变量】业务成功码集合


def _get_base_url(base_url: str | None = None) -> str:
    """取接口根地址;缺配置抛错(采集器在 env 缺失时给出明确提示)。"""
    value = (base_url or API_BASE_URL or os.getenv("HTFB_BASE_URL") or "").strip().rstrip("/")
    if not value:
        raise ValueError("未识别到天玑接口根地址,请配置环境变量 HTFC_BASE_URL。")
    return value


def _headers(token: str | None = None, user_id: str | None = None) -> dict[str, str]:
    """组装请求头:apikey 必填,来自环境变量 HTFC_API_KEY。"""
    api_key_value = (os.getenv("HTFC_API_KEY") or "").strip()
    if not api_key_value:
        raise ValueError("未识别到您的 API KEY,请配置环境变量 HTFC_API_KEY。")
    headers = {"Content-Type": "application/json", "apikey": api_key_value}
    if (token or "").strip():
        headers["token"] = token.strip()
    if (user_id or "").strip():
        headers["userId"] = user_id.strip()
    return headers


def _normalize_response(payload: Any) -> dict[str, Any]:
    """字符串响应转 dict;非 dict 抛错(接口契约要求 data 对象)。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("响应是字符串,但不是合法 JSON。") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"响应格式异常:期望 dict,实际为 {type(payload)}。")
    return payload


def _check_business_code(payload: dict[str, Any]) -> dict[str, Any]:
    """校验业务码;非 0/200 抛错(带错误信息,便于采集器跳过并留 error)。"""
    code = payload.get("errorCode", payload.get("code"))
    if code is not None and str(code) not in SUCCESS_CODES:
        message = (
            payload.get("errorMessage") or payload.get("message")
            or payload.get("msg") or "未知错误"
        )
        raise RuntimeError(f"接口返回错误:[{code}] {message}")
    return payload


def request_api(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    token: str | None = None,
    user_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """发天玑 HTTP 请求并返回业务 dict(错误统一转 RuntimeError)。"""
    url = urljoin(_get_base_url(base_url) + "/", path.lstrip("/"))
    try:
        response = htfc_requests.request(
            method.upper(),
            url,
            headers=_headers(token=token, user_id=user_id),
            params=params or {},
            json=json_body,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return _check_business_code(_normalize_response(response.json()))
    except htfc_requests.Timeout as exc:
        raise RuntimeError(f"请求超时({TIMEOUT_SECONDS}秒)。") from exc
    except htfc_requests.ConnectionError as exc:
        raise RuntimeError(f"连接失败:{exc}") from exc
    except htfc_requests.HTTPError as exc:
        raise RuntimeError(f"HTTP 错误:{exc.response.status_code} - {exc.response.text}") from exc
    except htfc_requests.RequestException as exc:
        raise RuntimeError(f"请求异常:{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("响应不是合法 JSON。") from exc


def data_of(response_data: dict[str, Any]) -> Any:
    """取响应 data 字段;无 data 键时原样返回(部分接口直接返回对象)。"""
    return (
        response_data.get("data")
        if isinstance(response_data, dict) and "data" in response_data
        else response_data
    )


def get_report_product_types_v2(
    type_value: Any | None = None,
    token: str | None = None,
    user_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """GET /bus/report/ptypes_v2 — 新版研报分类(取"日报"栏目 item_value 用)。"""
    params = {"type": type_value} if type_value is not None else {}
    return request_api("GET", "/bus/report/ptypes_v2", params=params, token=token, user_id=user_id, base_url=base_url)


def search_reports(
    item_value: Any,
    cur_page: int = 1,
    page_size: int = 10,
    title: str | None = None,
    broadheading_code: str | None = None,
    subclass_code: str | None = None,
    res_report: Any | None = None,
    token: str | None = None,
    user_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """GET /bus/report/specificList — 新版研报列表(item_value 选栏目:如 10074=日报)。

    【返回】{"data": {"resultList": [...], "totalRows": N}};列表项含 id/title/
    itemValue/reportType/publishDateTime/showDate/subclassCodeName 等。
    """
    if not item_value:
        raise ValueError("查询研报列表必须提供 item_value。")
    params: dict[str, Any] = {"curPage": cur_page, "pageSize": page_size, "item_value": item_value}
    for key, value in {
        "title": title,
        "broadheading_code": broadheading_code,
        "subclass_code": subclass_code,
        "res_report": res_report,
    }.items():
        if value is not None:
            params[key] = value
    return request_api("GET", "/bus/report/specificList", params=params, token=token, user_id=user_id, base_url=base_url)


def get_report_info(
    article_id: Any,
    item_value: Any,
    token: str | None = None,
    user_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """GET /bus/report/reportInfo — 研报详情(正文 content/mainContent/AI 摘要等)。

    【参数】article_id: 列表项 id(如 "RE17421");item_value: 列表项 itemValue(如 "10070")。
    """
    if not article_id:
        raise ValueError("查询研报详情必须提供 articleId。")
    if not item_value:
        raise ValueError("查询研报详情必须提供 itemValue。")
    return request_api(
        "GET",
        "/bus/report/reportInfo",
        params={"articleId": article_id, "itemValue": item_value},
        token=token,
        user_id=user_id,
        base_url=base_url,
    )
