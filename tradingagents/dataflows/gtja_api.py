"""gtja_api.py — 国泰君安期货 VIP cloudApi 数据客户端(基差 / 仓单 / 观点 / 利润)

【模块角色】
  国泰君安期货"机构客户服务平台"行情/研究 API(vip.gtjaqh.com,鉴权头
  accessKeyId + accessKeySecret)。该数据源比免费 AKShare 更准更全,作为
  AgentSense 数据层的"优先源"增强(2026-09-03 接入):
    - fetch_basis_df():     品种基差/现货价(优先),供 get_futures_basis 使用;
    - fetch_inventory_df(): 交易所注册仓单(优先),供 get_futures_inventory 使用;
    - fetch_viewpoint():    周度观点信号(最新一帧),供前端观点卡片。
    - fetch_profit_df():    品种加工利润/进口利润(2026-09-28 接入),供
      "库存+基差+利润"三因子交易系统(qihuo-duokong-logic 方法论)使用;
    - fetch_gtja_inventory_df(): 国君自建库存数据集(期货/社会/工厂口径,与仓单不同源)。

  【覆盖范围】
    - 基差 basisData.query.do:rows=reportDate/spotPrice/futuresPrice/basisValue/
      basisPremiumRate/contractCode/codeName/sectionName/district/spotIndexName/unitName。
      覆盖子集:只有有现货指数的品种(铜/螺纹…),原油 SC 等返回 0 行 → 调用方须回退 AKShare。
    - 仓单 fut.warehouseStock.query.do:rows=tradingDay/onWarrant(注册仓单)/code/exchangeCode。
    - 观点 commodity.weekly.viewpoint.query.do:列表端点(必填 page/startReportDate/
      endReportDate;code 过滤被服务端忽略须客户端筛),weekly 偏 reason 长文
      (2026-09-03 晨报日度已下线,只保留周度;2026-09-05 自 queryByCode 迁移,因后者
      只回最新一帧而 GTJA 提前建下期帧致多数品种查空)。
    - 利润 processProfitData.query.do:rows=dataType/dataDate/profitValue。dataType 形如
      `{品种小写}_{加工利润|进口利润}[_细分口径]`(如 ta_加工利润、eg_加工利润_石脑油制、
      m_加工利润_现货压榨利润_美豆、sr_进口利润_配额外进口)。2026-09-28 实测 188059 行 /
      88 个 dataType。⚠️ 服务端只认 page/size,**一切筛选参数被忽略**(total 恒为全量)
      → 必须全量分页拉取 + 本地过滤,故带磁盘缓存(见 _PROFIT_CACHE_DIR)。
    - 国君库存 inventoryData.query.do:rows=dataType/dataDate/inventoryValue。dataType 形如
      `{品种小写}_{期货库存|社会库存|工厂库存}`,2026-09-28 实测 45642 行。⚠️ 同为全量端点。

  【端点发现方法(2026-09-28,可复用)】平台前端是 Angular 模块联邦应用,端点名不在文档
  里也搜不到,但**可以扒**:POST `{base}/api/unicorn.cloudApi.{ep}.do` 的返回码可判存在性
  —— `300001`=端点存在(且 msg 会**吐出必填参数名**,如"page参数不能为空"),`300004`=
  "接口不存在"。两者文案截然不同,已用已知端点做对照实验验证可靠。命名规律为
  `{域}.{数据集}Data.query`(如 basisData/inventoryData/processProfitData)。
  【契约事实】真实调通(2026-09-03):POST `{base}/api/unicorn.cloudApi.{ep}.do`,
  业务码 code=0 成功;偶发瞬时 300004(网关 LB 抖动)与 404 已观察,内部自动重试。
  【可逆开关】未配置 GTJA_ACCESS_KEY_ID/GTJA_ACCESS_KEY_SECRET 时 configured()=False,
  所有 fetch_* 直接返回 None → 调用方走原 AKShare 链路,行为与接入前一致。
  【安全】密钥只从环境变量读取(.env,不 commit),不在本文件硬编码。
"""

from __future__ import annotations

import json  # 【调用包】利润/库存数据集磁盘缓存序列化
import logging  # 【调用包】日志输出(拉取失败/兜底告警)
import os  # 【调用包】读取环境变量密钥
import time  # 【调用包】磁盘缓存 TTL 时间戳
import uuid  # 【调用包】写缓存时生成唯一临时文件名(多进程并发写同一缓存不会互撞)
from datetime import date, timedelta  # 【调用包】仓单窗口推算(tail(60) 需 ≥60 交易日)
from pathlib import Path  # 【调用包】磁盘缓存目录
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
EP_PROFIT = "processProfitData.query"  # 【变量】加工利润/进口利润(2026-09-28 扒前端发现;全量端点,须本地筛)
EP_GTJA_INVENTORY = "inventoryData.query"  # 【变量】国君自建库存数据集(期货/社会/工厂口径;全量端点)

# 【为什么需要磁盘缓存】EP_PROFIT / EP_GTJA_INVENTORY 是"全量数据集"端点:服务端忽略
# 一切筛选参数,只认 page/size(实测 front 传 code/dataType 后 total 恒为全量)。因此
# 每次取数都要 19~23 页、约 18s、~16MB。日频数据每日仅更新一次 ⇒ 落盘缓存 + TTL 命中
# 即可把重复调用降到 0 次请求;进程内缓存(_MEM_*)只是第二层,重启后仍靠磁盘。
_PROFIT_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "gtja_profit"  # 【变量】利润数据集磁盘缓存目录
_GTJA_INV_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "gtja_inventory"  # 【变量】国君库存数据集磁盘缓存目录
_DATASET_TTL = 6 * 3600  # 【变量】全量数据集磁盘缓存 TTL(秒,6h;日频数据足够)
_PAGE_SIZE = 10000  # 【变量】全量端点单页条数(实测 10000 可用,20000 报 300001)
_MAX_PAGES = 40  # 【变量】全量端点分页上限(防服务端 total 异常导致死循环;188059/10000≈19 页)

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


def _request(endpoint: str, body: dict[str, Any], timeout: int | None = None) -> list[dict[str, Any]]:
    """POST 拉取并返回 data 记录列表;业务码非 0 / 网络异常重试后抛 GTJAError。

    【注意】data 可能直接是 list,也可能是 {recordList:[...]}(分页接口);统一解包成 list。
    【timeout 参数 2026-09-28】可选,缺省用模块常量 TIMEOUT_SECONDS(45s)。批量/截面类
      调用方(如量化模块的多品种排行)传更小的值可让"个别品种服务端慢"快速失败,
      否则 45s×4 次重试会让批量任务整体卡住(实测 20 品种串行曾达 842s)。
    """
    import requests  # 【调用包】HTTP 客户端(与 commodity_futures 同用 requests)

    url = _url(endpoint)
    eff_timeout = timeout if timeout and timeout > 0 else TIMEOUT_SECONDS  # 【变量】本次生效超时
    last_err: Exception | None = None
    for attempt in range(MAX_TRIES):
        try:
            resp = requests.post(url, headers=_headers(), json=body, timeout=eff_timeout)
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
# 全量数据集拉取(分页 + 磁盘缓存)—— 供 EP_PROFIT / EP_GTJA_INVENTORY 使用
# ---------------------------------------------------------------------------
_MEM_DATASET: dict[str, tuple[float, list[dict[str, Any]]]] = {}  # 【变量】进程内二级缓存(endpoint → (时间, 行))


def _dataset_cache_path(cache_dir: Path, endpoint: str) -> Path:
    """磁盘缓存文件路径(endpoint 名含点 → 换成下划线,避免当扩展名解析)。"""
    safe = endpoint.replace(".", "_")
    return cache_dir / f"{safe}.json"


def _read_dataset_cache(cache_dir: Path, endpoint: str, ttl: int) -> list[dict[str, Any]] | None:
    """读磁盘缓存;缺失/损坏/超 TTL → None(绝不抛,缓存只是加速层)。"""
    path = _dataset_cache_path(cache_dir, endpoint)
    try:
        if not path.exists() or (time.time() - path.stat().st_mtime) > ttl:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("rows") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else None
    except Exception as exc:  # noqa: BLE001 - 缓存读失败绝不能影响主流程
        logger.warning("GTJA dataset cache read failed (%s): %s", endpoint, exc)
        return None


def _write_dataset_cache(cache_dir: Path, endpoint: str, rows: list[dict[str, Any]]) -> None:
    """写磁盘缓存(原子替换:先写唯一 .tmp 再 rename,避免半截文件被读到)。失败只记日志。

    【2026-09-30 修 · 多进程并发写必撞 WinError 32】
    旧实现把临时文件名固定成 `{endpoint}.tmp`,于是**只要有两个进程同时刷新同一份
    缓存**,就会:进程 A 打开 tmp 写、进程 B 也对同一个 tmp 打开写、A 先 os.replace
    成功并关掉句柄,而 B 的 os.replace 落到一个仍被 A(或自己)持有的文件上 →
    `[WinError 32] 另一个程序正在使用此文件,进程无法访问`。
    实测证据:server_watchdog.err.log 里同一端点连续 5 条 WinError 32 告警。

    为什么这件事在本项目里不是理论风险:全量数据集(利润/库存)的刷新点天然并发 ——
    · web 进程的调度器(18:00/22:00 研报链路)会取;
    · 采集器子进程(独立 python 进程)也会取;
    · 本次新增的「国君数据」模块走的是**同一个** gtja_api.fetch_dataset + 同一缓存目录,
      用户点一次刷新就是第三个并发写者。
    ⇒ 修法:tmp 名带 uuid4 后缀(每写者独占一个文件),再用 os.replace 做原子替换。
      os.replace 在 Windows 上对"目标已存在"是覆盖语义,不会因并发而报错。
    残留的 .tmp 不清理也无害:读取只看 `{endpoint}.json`,且下次写会换新名字。
    """
    path = _dataset_cache_path(cache_dir, endpoint)
    # 唯一临时名:uuid 保证并发写者各写各的,不会互抢同一文件句柄。
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"savedAt": int(time.time()), "rows": rows}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GTJA dataset cache write failed (%s): %s", endpoint, exc)
        try:
            tmp.unlink()  # 失败时清掉自己的半截文件,别留垃圾
        except OSError:
            pass


def fetch_dataset(
    endpoint: str,
    cache_dir: Path,
    ttl: int = _DATASET_TTL,
    force: bool = False,
) -> list[dict[str, Any]]:
    """全量分页拉取一个"数据集型"端点,带磁盘 + 进程内双层缓存。

    【为什么要全量拉】实测 processProfitData / inventoryData 这类端点**只认 page/size**:
      传 code / dataType / 日期区间等任何筛选参数,返回的 total 与内容都不变(全量)。
      所以只能全量拉回本地再筛 —— 这正是缓存存在的理由(19 页 / 18s / 16MB)。
    【缓存层级】进程内 _MEM_DATASET → 磁盘 {cache_dir}/{endpoint}.json → 真实请求。
    【失败语义】网络/业务错 → 抛 GTJAError(调用方 fetch_profit_df 等会兜住降级);
      但**磁盘缓存读失败不抛**(只是没命中)。
    """
    now = time.time()
    hit = _MEM_DATASET.get(endpoint)
    if hit and not force and (now - hit[0]) < ttl:
        return hit[1]
    if not force:
        cached = _read_dataset_cache(cache_dir, endpoint, ttl)
        if cached is not None:
            _MEM_DATASET[endpoint] = (now, cached)
            return cached

    rows: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, _MAX_PAGES + 1):
        chunk = _request(endpoint, {"page": str(page), "size": str(_PAGE_SIZE)})
        if not chunk:
            break
        rows.extend(chunk)
        if total is None:
            # _request 只回 recordList,拿不到 total → 用"本页不满"判收尾(见下)
            total = -1
        if len(chunk) < _PAGE_SIZE:  # 不满一页 = 最后一页
            break
    if not rows:
        return []
    _MEM_DATASET[endpoint] = (time.time(), rows)
    _write_dataset_cache(cache_dir, endpoint, rows)
    return rows


def _split_data_type(data_type: str) -> tuple[str, str]:
    """拆 dataType → (品种小写, 口径)。如 'eg_加工利润_石脑油制' → ('eg', '加工利润_石脑油制')。

    【为什么】两类数据集都用 `{品种code}_{中文口径}` 命名(实测 88 + 55 种),调用方按品种
      过滤必须用同一个拆分口径,集中在此避免各处重复实现出偏差。
    """
    t = (data_type or "").strip()
    if "_" not in t:
        return "", t
    code, _, label = t.partition("_")
    return code.strip().lower(), label.strip()


def fetch_profit_df(
    code: str,
    kind: str | None = None,
    force: bool = False,
) -> pd.DataFrame | None:
    """取某品种的加工利润 / 进口利润序列(长表: date / dtype / label / profit)。

    Args:
        code: 品种代码(大小写不敏感),如 "TA" / "eg"。匹配 dataType 的 `{小写code}_` 前缀。
        kind: 口径过滤,可选。'加工' / '进口' 做子串匹配;None = 全部口径都要。
        force: True 时绕过缓存强制重拉(数据源更新后手动刷新用)。

    【返回】列为 date(YYYY-MM-DD) / dtype(原始 dataType) / label(口径细分) / profit。
      该品种无利润数据 → None。多个口径(如 eg 的"石脑油制"+"合成气制")会各成一行序列,
      调用方按 dtype 分组即可。
    """
    if not configured():
        return None
    try:
        rows = fetch_dataset(EP_PROFIT, _PROFIT_CACHE_DIR, force=force)
    except GTJAError as exc:
        logger.warning("GTJA profit dataset fetch failed: %s", exc)
        return None
    want = (code or "").strip().lower()
    records = []
    for r in rows:
        c, label = _split_data_type(r.get("dataType") or "")
        if c != want:
            continue
        if kind and kind not in label:
            continue
        v = pd.to_numeric(r.get("profitValue"), errors="coerce")
        if pd.isna(v):
            continue
        records.append({
            "date": str(r.get("dataDate") or ""),
            "dtype": str(r.get("dataType") or ""),
            "label": label,
            "profit": float(v),
        })
    if not records:
        return None
    df = pd.DataFrame(records)
    return df.sort_values(["dtype", "date"]).reset_index(drop=True)


def fetch_gtja_inventory_df(
    code: str,
    kind: str | None = None,
    force: bool = False,
) -> pd.DataFrame | None:
    """取某品种的国君自建库存序列(长表: date / dtype / label / inventory)。

    【与 fetch_inventory_df 的区别】fetch_inventory_df 取的是**交易所注册仓单**
      (fut.warehouseStock,单一口径);本函数取国君自建的三口径库存数据集
      (`{code}_期货库存` / `_社会库存` / `_工厂库存`),可交叉验证"期货库存 vs 社会库存"
      的背离(期货库存增而社会库存减 = 库存向交割库转移)。

    Args:
        code: 品种代码(大小写不敏感)。
        kind: 口径过滤,'期货' / '社会' / '工厂' 子串匹配;None = 全部。
        force: 绕过缓存强制重拉。
    """
    if not configured():
        return None
    try:
        rows = fetch_dataset(EP_GTJA_INVENTORY, _GTJA_INV_CACHE_DIR, force=force)
    except GTJAError as exc:
        logger.warning("GTJA inventory dataset fetch failed: %s", exc)
        return None
    want = (code or "").strip().lower()
    records = []
    for r in rows:
        c, label = _split_data_type(r.get("dataType") or "")
        if c != want:
            continue
        if kind and kind not in label:
            continue
        v = pd.to_numeric(r.get("inventoryValue"), errors="coerce")
        if pd.isna(v):
            continue
        records.append({
            "date": str(r.get("dataDate") or ""),
            "dtype": str(r.get("dataType") or ""),
            "label": label,
            "inventory": float(v),
        })
    if not records:
        return None
    df = pd.DataFrame(records)
    return df.sort_values(["dtype", "date"]).reset_index(drop=True)


def clear_dataset_cache(endpoint: str | None = None) -> int:
    """清空磁盘+内存数据集缓存(数据源更新后手动刷新用);返回清掉的文件数。

    endpoint=None → 清 EP_PROFIT 与 EP_GTJA_INVENTORY 两个;传具体端点名则只清它。
    """
    eps = [endpoint] if endpoint else [EP_PROFIT, EP_GTJA_INVENTORY]
    dirs = {EP_PROFIT: _PROFIT_CACHE_DIR, EP_GTJA_INVENTORY: _GTJA_INV_CACHE_DIR}
    n = 0
    for ep in eps:
        _MEM_DATASET.pop(ep, None)
        d = dirs.get(ep)
        if d is None:
            continue
        p = _dataset_cache_path(d, ep)
        try:
            if p.exists():
                p.unlink()
                n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("GTJA dataset cache clear failed (%s): %s", ep, exc)
    return n


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
def fetch_basis_df(
    code: str, start_date: str, end_date: str, timeout: int | None = None
) -> pd.DataFrame | None:
    """拉国君品种基差/现货价并归一化;覆盖子集(SC 等)或失败 → None。

    Args:
        code: 品种代码(大小写不敏感)
        start_date / end_date: YYYY-MM-DD(接口接受 ISO 日期)
        timeout: 可选单次请求超时(秒);缺省 45s。批量场景传小值可快速失败。
    """
    if not configured():
        return None
    try:
        rows = _request(
            EP_BASIS,
            {"code": code, "startReportDate": start_date, "endReportDate": end_date},
            timeout=timeout,
        )
    except GTJAError as exc:
        logger.warning("GTJA basis fetch failed for %s: %s", code, exc)
        return None
    return normalize_basis_rows(rows)


def fetch_inventory_df(
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    timeout: int | None = None,
) -> pd.DataFrame | None:
    """拉国君交易所注册仓单(tail(60) 需要 ≥60 个交易日 → 默认取近 240 天窗口)。

    Args:
        code: 品种代码(大小写不敏感)
        start_date / end_date: YYYY-MM-DD,可选显式区间;两者都给则用之,否则保持
            近 240 天默认窗口(与 commodity_futures 下游兼容,显式区间只是覆盖)。
        timeout: 可选单次请求超时(秒);缺省 45s。批量场景传小值可快速失败。
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
            timeout=timeout,
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
