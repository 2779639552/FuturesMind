"""gtja_api.py — 国泰君安期货 VIP cloudApi 数据客户端(基差 / 仓单 / 观点信号)

【模块角色】
  国泰君安期货"机构客户服务平台"行情/研究 API(vip.gtjaqh.com,鉴权头
  accessKeyId + accessKeySecret)。该数据源比免费 AKShare 更准更全,作为
  AgentSense 数据层的"优先源"增强(2026-09-03 接入):
    - fetch_basis_df():     品种基差/现货价(优先),供 get_futures_basis 使用;
    - fetch_inventory_df(): 交易所注册仓单(优先),供 get_futures_inventory 使用;
    - fetch_viewpoint():    周度观点信号(最新一帧),供前端观点卡片。

  【覆盖范围】
    - 基差 basisData.query.do:rows=reportDate/spotPrice/futuresPrice/basisValue/
      basisPremiumRate/contractCode/codeName/sectionName/district/spotIndexName/unitName。
      覆盖子集:只有有现货指数的品种(铜/螺纹…),原油 SC 等返回 0 行 → 调用方须回退 AKShare。
    - 仓单 fut.warehouseStock.query.do:rows=tradingDay/onWarrant(注册仓单)/code/exchangeCode。
    - 观点 commodity.weekly.viewpoint.query.do:列表端点(必填 page/startReportDate/
      endReportDate;code 过滤被服务端忽略须客户端筛),weekly 偏 reason 长文
      (2026-09-03 晨报日度已下线,只保留周度;2026-09-05 自 queryByCode 迁移,因后者
      只回最新一帧而 GTJA 提前建下期帧致多数品种查空)。
  【契约事实】真实调通(2026-09-03):POST `{base}/api/unicorn.cloudApi.{ep}.do`,
  业务码 code=0 成功;偶发瞬时 300004(网关 LB 抖动)与 404 已观察,内部自动重试。
  【可逆开关】未配置 GTJA_ACCESS_KEY_ID/GTJA_ACCESS_KEY_SECRET 时 configured()=False,
  所有 fetch_* 直接返回 None → 调用方走原 AKShare 链路,行为与接入前一致。
  【安全】密钥只从环境变量读取(.env,不 commit),不在本文件硬编码。
"""

from __future__ import annotations

import logging  # 【调用包】日志输出(拉取失败/兜底告警)
import os  # 【调用包】读取环境变量密钥
from datetime import date, timedelta  # 【调用包】仓单窗口推算(tail(60) 需 ≥60 交易日)
from typing import Any  # 【调用包】类型标注

import pandas as pd  # 【调用包】归一化 DataFrame(列对齐 commodity_futures 英文 schema)

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("GTJA_BASE_URL", "https://vip.gtjaqh.com").rstrip("/")  # 【变量】接口根(env,web_app 已 load .env)
TIMEOUT_SECONDS = 45  # 【变量】单次请求超时(秒)
MAX_TRIES = 4  # 【变量】瞬时网关抖动(300004/404)重试次数

# 端点名(拼接成 {API_BASE_URL}/api/unicorn.cloudApi.{ep}.do,ep 带点分节)
EP_BASIS = "basisData.query"  # 【变量】品种基差/现货价
EP_WAREHOUSE = "fut.warehouseStock.query"  # 【变量】交易所注册仓单
EP_VIEW_WEEKLY = "commodity.weekly.viewpoint.query"  # 【变量】周度观点(列表端点,按日期区间)
EP_RESEARCH = "researchReportAttachmentQuery"  # 【变量】研报附件查询(2026-09-04 开通):按发布日期区间拉研报列表+附件直链

# 内部基差 DataFrame 的标准列序(与 commodity_futures 归一化后列序一致,含近月/主力两套)
_BASIS_COLUMNS = [  # 【变量】基差 DataFrame 标准列(与 AKShare 东财路径 keep_cols 相同)
    "date",
    "spot_price",
    "dominant_contract",
    "dominant_contract_price",
    "dom_basis",
    "dom_basis_rate",
    "near_contract",
    "near_contract_price",
    "near_basis",
    "near_basis_rate",
]


class GTJAError(RuntimeError):
    """【异常】GTJA 接口不可用(网络/业务码失败),调用方应回退原数据源。"""


def configured() -> bool:
    """是否配置了访问密钥;未配置时数据层不应尝试走 GTJA(直接回退 AKShare)。"""
    return bool((os.getenv("GTJA_ACCESS_KEY_ID") or "").strip()) and bool(
        (os.getenv("GTJA_ACCESS_KEY_SECRET") or "").strip()
    )


def _url(endpoint: str) -> str:
    """拼请求 URL:cloudApi.{endpoint}.do 的分节端点是文档既定形态(点连接,勿改斜杠)。"""
    return f"{API_BASE_URL}/api/unicorn.cloudApi.{endpoint}.do"


def _headers() -> dict[str, str]:
    """组装鉴权请求头(accessKeyId + accessKeySecret,与 apifox 文档一致)。"""
    key_id = (os.getenv("GTJA_ACCESS_KEY_ID") or "").strip()
    key_secret = (os.getenv("GTJA_ACCESS_KEY_SECRET") or "").strip()
    if not key_id or not key_secret:
        raise GTJAError("未配置 GTJA_ACCESS_KEY_ID / GTJA_ACCESS_KEY_SECRET。")
    return {"Content-Type": "application/json", "accessKeyId": key_id, "accessKeySecret": key_secret}


def _request(endpoint: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """POST 拉取并返回 data 记录列表;业务码非 0 / 网络异常重试后抛 GTJAError。

    【注意】data 可能直接是 list,也可能是 {recordList:[...]}(分页接口);统一解包成 list。
    """
    import requests  # 【调用包】HTTP 客户端(与 commodity_futures 同用 requests)

    url = _url(endpoint)
    last_err: Exception | None = None
    for attempt in range(MAX_TRIES):
        try:
            resp = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT_SECONDS)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一重试后上报
            last_err = exc
        else:
            code = payload.get("code") if isinstance(payload, dict) else None
            if code == 0:
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    return data.get("recordList") or data.get("list") or data.get("rows") or []
                if isinstance(data, list):
                    return data
                return []
            msg = payload.get("msg") if isinstance(payload, dict) else ""
            # 瞬时网关抖动(code 300004/404)才重试;鉴权/参数类错误直接抛。
            transient = code in (300004,) or resp.status_code == 404
            if not transient:
                raise GTJAError(f"接口返回错误:[{code}] {msg}")
            last_err = GTJAError(f"接口返回错误:[{code}] {msg}")
        if attempt < MAX_TRIES - 1:
            import time  # 【调用包】抖动退避(短暂 sleep 后重试)

            time.sleep(0.4 * (attempt + 1))
    raise GTJAError(f"GTJA {endpoint} 请求失败(重试 {MAX_TRIES} 次):{last_err}")


# ---------------------------------------------------------------------------
# 归一化(纯函数,便于单测)
# ---------------------------------------------------------------------------
def normalize_basis_rows(rows: list[dict[str, Any]]) -> pd.DataFrame | None:
    """把 basisData.query.do 的行归一化为 commodity_futures 英文 schema 的 DataFrame。

    【映射】reportDate→date(YYYYMMDD 无横杠);spotPrice→spot_price;国君基差是对单一
      参考合约(近月/主力随换月滚动)报价 → dominant_* 与 near_* 两套同值别名,
      保证 web 端 _basis_points(近月)/_run_input_basis_points(主力)都能出数。
    【去重】同一天可能出现多地区/多现货指数行(SMM/长江、华东/上海):保留 SMM 行,
      否则保留 API 顺序首行,避免现货指数口径漂移。
    【空】rows 为空 → None(调用方回退 AKShare)。
    """
    if not rows:
        return None
    sel: dict[str, dict[str, Any]] = {}  # 【变量】date(YYYYMMDD) → 选中的行
    for r in rows:
        d = (r.get("reportDate") or "").replace("-", "")
        if not d or r.get("spotPrice") is None:
            continue
        cur = sel.get(d)
        is_smm = "SMM" in (r.get("spotIndexName") or "")
        if cur is None:
            sel[d] = r
        else:
            cur_is_smm = "SMM" in (cur.get("spotIndexName") or "")
            if is_smm and not cur_is_smm:
                sel[d] = r  # 【关键】无 SMM 时可能落到长江/其它指数 → 有 SMM 则优先 SMM
    if not sel:
        return None
    records = []
    for d, r in sel.items():
        contract = r.get("contractCode") or ""
        spot = pd.to_numeric(r.get("spotPrice"), errors="coerce")
        fut = pd.to_numeric(r.get("futuresPrice"), errors="coerce")
        basis = pd.to_numeric(r.get("basisValue"), errors="coerce")
        rate = pd.to_numeric(r.get("basisPremiumRate"), errors="coerce")
        row = {
            "date": int(d),
            "spot_price": spot,
            "dominant_contract": contract,
            "dominant_contract_price": fut,
            "dom_basis": basis,
            "dom_basis_rate": rate,
            "near_contract": contract,
            "near_contract_price": fut,
            "near_basis": basis,
            "near_basis_rate": rate,
        }
        records.append(row)
    df = pd.DataFrame(records, columns=_BASIS_COLUMNS).dropna(subset=["spot_price"])
    if df.empty:
        return None
    return df.sort_values("date").reset_index(drop=True)


def normalize_inventory_rows(rows: list[dict[str, Any]]) -> pd.DataFrame | None:
    """把 fut.warehouseStock.query.do 的行归一化为 date/inventory/change DataFrame。

    【映射】tradingDay→date(YYYY-MM-DD);onWarrant(注册仓单,字符串)→inventory;
      change = 相邻交易日差值(与交易所回退源同口径)。date 列下游 pd.to_datetime 统一。
    【空】rows 为空 → None。
    """
    if not rows:
        return None
    records = []
    for r in rows:
        day = r.get("tradingDay") or r.get("reportDate") or ""
        if not day:
            continue
        warrant = pd.to_numeric(r.get("onWarrant"), errors="coerce")
        if pd.isna(warrant):
            continue
        records.append({"date": str(day), "inventory": float(warrant), "change": 0.0})
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    df["change"] = df["inventory"].diff().fillna(0.0)
    return df


# ---------------------------------------------------------------------------
# 对外拉取函数(空/错 → None,绝不让数据层抛异常;密钥未配置同 None)
# ---------------------------------------------------------------------------
def fetch_basis_df(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """拉国君品种基差/现货价并归一化;覆盖子集(SC 等)或失败 → None。

    Args:
        code: 品种代码(大小写不敏感)
        start_date / end_date: YYYY-MM-DD(接口接受 ISO 日期)
    """
    if not configured():
        return None
    try:
        rows = _request(
            EP_BASIS,
            {"code": code, "startReportDate": start_date, "endReportDate": end_date},
        )
    except GTJAError as exc:
        logger.warning("GTJA basis fetch failed for %s: %s", code, exc)
        return None
    return normalize_basis_rows(rows)


def fetch_inventory_df(
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame | None:
    """拉国君交易所注册仓单(tail(60) 需要 ≥60 个交易日 → 默认取近 240 天窗口)。

    Args:
        code: 品种代码(大小写不敏感)
        start_date / end_date: YYYY-MM-DD,可选显式区间;两者都给则用之,否则保持
            近 240 天默认窗口(与 commodity_futures 下游兼容,显式区间只是覆盖)。
    """
    if not configured():
        return None
    if start_date and end_date and start_date > end_date:
        logger.warning("GTJA warehouse: start>end, skip %s [%s ~ %s]", code, start_date, end_date)
        return None
    if start_date and end_date:
        start, end = start_date, end_date
    else:
        end = date.today()
        start = (end - timedelta(days=240)).strftime("%Y-%m-%d")
        end = end.strftime("%Y-%m-%d")
    try:
        rows = _request(
            EP_WAREHOUSE,
            {"code": code, "startReportDate": start, "endReportDate": end},
        )
    except GTJAError as exc:
        logger.warning("GTJA warehouse fetch failed for %s: %s", code, exc)
        return None
    return normalize_inventory_rows(rows)


def fetch_viewpoint(code: str) -> dict[str, Any]:
    """拉国君周度观点信号(该品种最新一帧;2026-09-03 晨报日度已下线)。

    【返回】{weekly: {...}|None, error: str|None};失败只让 weekly 为 None、error 记
      首错供前端提示;无信号与失败在 UI 上可区分。

    【关键逻辑】2026-09-05 实测:原 queryByCode 端点只回最新一帧,而 GTJA 提前建下期
      帧后逐品种补内容,最新帧常只有个别品种有数据 → 其余品种全查空(前端一直"无")。
      改走 .query 列表端点(必填 page/startReportDate/endReportDate;与研报接口一样
      code 过滤被服务端忽略,必须客户端按 code 筛),取近 4 周窗口内该品种最新一帧;
      end 放宽 +7 天以含提前发布的下期帧。
    """
    if not configured():
        return {"weekly": None, "error": "GTJA 未配置(缺密钥)"}
    result: dict[str, Any] = {"weekly": None, "error": None}
    try:
        end = date.today()
        rows = _request(
            EP_VIEW_WEEKLY,
            {
                "page": 1,
                "size": 1000,
                "startReportDate": (end - timedelta(days=28)).strftime("%Y-%m-%d"),
                "endReportDate": (end + timedelta(days=7)).strftime("%Y-%m-%d"),
            },
        )
        mine = [
            r for r in rows
            if isinstance(r, dict) and str(r.get("code") or "").upper() == code.upper()
        ]
        if mine:  # 服务端不保证排序,客户端按 reportDate 降序取最新一帧
            row = dict(max(mine, key=lambda r: str(r.get("reportDate") or "")))
            # 接口 score 偶发字符串("0")→ 统一成 int
            sc = row.get("score")
            if isinstance(sc, str) and sc.strip().lstrip("+-").isdigit():
                row["score"] = int(sc)
            result["weekly"] = row
        else:
            result["error"] = f"近 4 周无 {code} 周度观点帧"
    except GTJAError as exc:
        logger.warning("GTJA weekly viewpoint fetch failed for %s: %s", code, exc)
        result["error"] = str(exc)
    return result


def fetch_research_reports(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """按发布日期区间拉国君研报列表(researchReportAttachmentQuery,2026-09-04 开通)。

    【参数】start_date / end_date: YYYY-MM-DD,均为**闭区间**(服务端含端点日)。
    【返回】原始行列表,每行 {infoId, title, publishTime, author, summary,
            riskLevel, infoTags:[{tagName,tagId}], attachments:[{name,type,address}]};
            失败/未配置 → [](调用方记录告警,不抛)。
    【关键逻辑】必填 publishTimeStart/publishTimeEnd(缺参报 300001);服务端**忽略
              page/size 与一切过滤参数**,一次返回区间内全量 → 客户端自行过滤/限流。
    """
    if not configured():
        return []
    body: dict[str, Any] = {"publishTimeStart": start_date, "publishTimeEnd": end_date}
    try:
        return _request(EP_RESEARCH, body)
    except GTJAError as exc:
        logger.warning("GTJA research report fetch failed [%s ~ %s]: %s", start_date, end_date, exc)
        return []
