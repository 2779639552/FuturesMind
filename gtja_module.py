"""gtja_module.py — 「国君数据」页聚合层: 把 GTJA 四个数据源聚合成页面要的 payload。

【与 gtja_api.py 的分工(刻意分层)】
  gtja_api.py  = **传输层**: HTTP/鉴权/分页/磁盘缓存 → 归一化 DataFrame。
  本模块        = **聚合层**: 按品种把四个数据源算成"页面能直接渲染的指标列表 + 分位"。
  分层的理由: 本模块必须能**不起 Flask 就单测**(见 tests/test_gtja_module.py),
              所以它不复制任何传输逻辑, 只调用 gtja_api。

【★ 三态契约 —— 本模块存在的首要理由】
  国君四个源里有两个(基差/仓单)是**带参数端点**, 两个(库存/利润)是**全量端点**。
  最大的风险不是"取不到数", 而是**把"取数失败"显示成"数值 0"** —— 用户据此下结论
  会得出完全相反的判断。故本模块对每个源统一给出三态, 且 UI 必须分开展示:
    · available=True,  rows 非空 → 有数据
    · available=True,  rows 空   → **确定无数据**(上游正常应答 0 行; 如 SC 无现货指数口径)
    · available=False, error=... → **接口失败 / 未配密钥**(与"无数据"语义完全不同)
  【为什么直接调 gtja_api._request 而不用 fetch_basis_df()】后者把"0 行"和"请求失败"
  都压成 `None` 返回 —— 那正好把两个必须区分的状态合并掉了。_request 会**抛 GTJAError**
  表示真失败, 正常返回 0 行才走 available=True 分支, 三态由此可判。
  (调用同包私有函数是有意为之: 同项目内共享一处传输实现, 优于再写一份 HTTP。)

【并发与超时(遵守项目已知陷阱)】
  GTJA 单次请求超时默认 45s × 重试 4 次 = 最坏 180s/源 ⇒ 四源串行最坏可达 12 分钟。
  故这里: ① 四源线程池并发; ② 显式传短超时(basis/warehouse 12s);
  ③ 全量端点靠磁盘缓存(6h TTL)兜住, 冷启动首次约 6~18s。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# 基差/仓单(带参数端点)的单次请求超时: 远小于 gtja_api 默认 45s, 避免个别品种拖住整页。
FAST_TIMEOUT = 12
# 指标序列在 payload 里最多回传多少点(仅影响图表, 不影响分位统计口径)。
MAX_SERIES_POINTS = 250
# 单次最多输出多少个指标行(基差 4 + 仓单 2 + 库存 N + 利润 N 的合计上限)。
MAX_INDICATORS = 16
# 利润口径最多取几个(按数据点数降序), 避免 40+ 口径把页面撑爆。
MAX_PROFIT_LABELS = 6
# 【★ 上游硬约束, 2026-09-29 实测】基差端点 basisData.query 单次查询跨度**不得超过 180 天**:
#   180 天 ✓(123 行) / 181 天 ✗ `[300001] 单次查询时间范围不超过180天`。
#   仓单端点 fut.warehouseStock.query **无此限制**(实测 500 天可用) ⇒ 只对基差分片。
#   ⚠️ 现有「数据仓库」页的 /api/gtja/dataset/basis 默认正是 `today-180 ~ today` ——
#     正好压在边界上、零余量(实测今天是 180 天刚好通过)。此处取 175 天留 5 天安全垫。
UPSTREAM_BASIS_MAX_DAYS = 180  # 【变量】上游实测硬限制(只读事实, 供测试与文档引用)
MAX_SPAN_DAYS = 175            # 【变量】本模块选用的安全分片跨度(< 上游限制, 留安全垫)
# 分片数上限(175 天 × 6 ≈ 2.9 年)。防前端传入超长区间把上游拖垮。
MAX_CHUNKS = 6
# 默认回看窗口(天): 必须 ≤ MAX_SPAN_DAYS, 否则基差必然报 300001。
DEFAULT_RANGE_DAYS = 175
# 仓单端点的分片跨度: 实测无 180 天限制, 取 365 天控制单次响应体量。
WAREHOUSE_SPAN_DAYS = 365


# ---------------------------------------------------------------------------
# 数值 / 统计工具(纯函数)
# ---------------------------------------------------------------------------
def _num(x: Any) -> float | None:
    """转 float; 非数值/NaN/inf → None(保证 JSON 里绝不出现 NaN, 那是非法 JSON)。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _pct_rank(vals: list[float], cur: float) -> float | None:
    """当前值在给定序列里的百分位(0~100; <=cur 的占比)。

    【为什么用"占比"而不是"区间线性插值"】占比对**非正态/含跳变**的期限结构、
      升贴水序列更稳健(本项目已有先例: 现货基差分位就是按区间占比算的 SN 思路)。
      线性插值在极值处会把"历史最高"显示成 100 附近但不等于 100, 容易被读成"还没到顶"。
    【返回】点数 <2 → None(单点序列谈分位无意义, 宁可显"—"也不编一个 50%)。
    """
    if len(vals) < 2:
        return None
    n_le = sum(1 for v in vals if v <= cur)
    return round(n_le / len(vals) * 100, 1)


def _series_stats(series: list[dict[str, Any]], value_key: str) -> dict[str, Any]:
    """把 [{date, value}] 序列算成指标统计块(当前值/前值/变化/极值/分位)。

    【返回】{"status": "ok"|"insufficient", "as_of", "current", "prev", "chg", "chg_pct",
             "hi", "lo", "pct", "n", "first_date", "last_date"}
      · status="insufficient" ⇒ 点数为 0; 有 1 个点时 current 有值但 chg/pct 为 None。
    【为什么 chg_pct 单独给】仓库量从 0 起(如 PX/LU 仓单为 0)时 (cur-prev)/prev 会除零,
      此时 chg_pct=None 而 chg 仍有效 —— 只给一个字段会让前端被迫猜。
    """
    pts = [(str(p.get("date") or ""), _num(p.get(value_key))) for p in series]
    pts = [(d, v) for d, v in pts if v is not None]
    pts.sort(key=lambda x: x[0])
    out: dict[str, Any] = {
        "status": "insufficient", "as_of": "", "current": None, "prev": None,
        "chg": None, "chg_pct": None, "hi": None, "lo": None, "pct": None,
        "n": len(pts), "first_date": "", "last_date": "",
    }
    if not pts:
        return out
    vals = [v for _, v in pts]
    cur = vals[-1]
    out.update({
        "status": "ok", "as_of": pts[-1][0], "current": cur,
        "hi": max(vals), "lo": min(vals), "n": len(vals),
        "first_date": pts[0][0], "last_date": pts[-1][0],
        "pct": _pct_rank(vals, cur),
    })
    if len(vals) >= 2:
        prev = vals[-2]
        out["prev"] = prev
        out["chg"] = round(cur - prev, 6)
        if prev not in (0, 0.0):
            out["chg_pct"] = round((cur - prev) / abs(prev) * 100, 4)
    return out


def _tail(series: list[dict[str, Any]], cap: int = MAX_SERIES_POINTS) -> list[list[Any]]:
    """序列裁成 [[date, value], ...] 且只留最近 cap 点(图表用, 省带宽)。

    【cap=0 的语义】**不裁**, 返回全历史 —— 季节性图要按年对齐 5 年曲线,
      被裁到 250 点就只剩一年, 同比无从谈起(2026-10-19 加)。
    """
    out: list[list[Any]] = []
    for p in (series if cap <= 0 else series[-cap:]):
        v = _num(p.get("value"))
        if v is None:
            continue
        out.append([str(p.get("date") or ""), v])
    return out


def _mk_indicator(
    iid: str, group: str, label: str, series: list[dict[str, Any]],
    unit: str = "", note: str = "", pct_scope: str = "",
    series_cap: int = MAX_SERIES_POINTS,
) -> dict[str, Any] | None:
    """组装一个指标行; 无有效点 → None(调用方据此跳过, 不产出空壳行)。"""
    st = _series_stats(series, "value")
    if st["status"] != "ok":
        return None
    return {
        "id": iid, "group": group, "label": label, "unit": unit, "note": note,
        "pct_scope": pct_scope or f"全历史({st['n']}点)",
        **st,
        "series": _tail(series, series_cap),
    }


def _clamp_range_days(want: int | None) -> int:
    """把调用方给的回看天数夹到 [5, MAX_SPAN_DAYS]; None/非法 → DEFAULT_RANGE_DAYS。

    【为什么单独抽成函数】这段夹紧是"默认查询必然成功"的唯一保障(见 fetch_overview)。
      内联在 fetch_overview 里时**无法被针对性测试** —— 变异测试实测: 把
      DEFAULT_RANGE_DAYS 改到 400 天, 因为夹紧仍在, 行为毫无变化 ⇒ 用例假绿、
      测不到真正要保护的东西。抽出来后可单独断言, 也让"上限"只有一处定义。
    """
    try:
        n = int(want) if want is not None else DEFAULT_RANGE_DAYS
    except (TypeError, ValueError):
        n = DEFAULT_RANGE_DAYS
    return max(5, min(n, MAX_SPAN_DAYS))


def _datespan_days(start: str, end: str) -> int:
    """起止日期跨度(天)。解析失败 → 0(调用方按"不分片"处理)。"""
    try:
        d0 = date.fromisoformat(start[:10])
        d1 = date.fromisoformat(end[:10])
    except (TypeError, ValueError):
        return 0
    return (d1 - d0).days


def _chunked_request(endpoint: str, code: str, start: str, end: str,
                     max_span: int) -> tuple[list[dict[str, Any]], int, str]:
    """按 max_span 天分片请求带参数端点, 拼接全部行。

    【为什么需要】基差端点单次跨度 >180 天直接报 `[300001] 单次查询时间范围不超过180天`
      (实测 180✓ / 181✗)。若不处理, 用户一选长区间就被显示成"接口失败" ——
      而真实原因是"这个区间要分几次问", 属于**用法问题而非数据问题**, 不该混为一谈。
    【返回】(rows, chunks, warning)
      · rows  = 各分片行拼接(不做去重/排序, 交给调用方按各自口径处理)
      · chunks= 实际分片数
      · warning = 非空表示"部分分片失败", 内容为**哪个区间失败了** ——
        这条必须透传到 UI: 只回部分数据却不说, 用户会拿残缺序列算分位并与全量历史比大小。
    【分片方向】从**最新往最早**切, 并优先保住最新一段: 若中途失败, 已拿到的近端数据
      仍是最有决策价值的那部分(用户看基差主要看当下位置)。
    """
    from datetime import date as _d, timedelta as _td

    span = _datespan_days(start, end)
    if span <= max_span:
        rows = _request_rows(endpoint, code, start, end)
        return rows, 1, ""

    chunks: list[tuple[str, str]] = []
    cur_end = end
    guard = 0
    while cur_end > start and guard < MAX_CHUNKS:
        guard += 1
        try:
            cur_start = (_d.fromisoformat(cur_end[:10]) - _td(days=max_span)).isoformat()
        except ValueError:
            break
        if cur_start < start:
            cur_start = start
        chunks.append((cur_start, cur_end))
        if cur_start <= start:
            break
        # 下一片的结束日 = 本片开始日的前一天(留空档不重不漏, 避免边界日重复计数)
        cur_end = (_d.fromisoformat(cur_start) - _td(days=1)).isoformat()

    rows_all: list[dict[str, Any]] = []
    failed: list[str] = []
    for s, e in chunks:
        try:
            rows_all.extend(_request_rows(endpoint, code, s, e))
        except Exception as exc:  # noqa: BLE001
            # 【必须带原因】只报"哪一段失败了"信息量不够 —— 用户无法判断是限流要重试、
            #   是区间超限要改用法、还是凭证有问题。附上截断后的异常消息供定位。
            why = str(exc).strip().replace("\n", " ")[:60]
            failed.append(f"{s}~{e}({type(exc).__name__}{': ' + why if why else ''})")
    warn = ("部分区间取数失败, 下方序列**不完整**: " + "; ".join(failed)) if failed else ""
    return rows_all, len(chunks), warn


def _request_rows(endpoint: str, code: str, start: str, end: str) -> list[dict[str, Any]]:
    """单次请求带参数端点(基差/仓单共用同一请求体形状); 失败抛 GTJAError 由调用方判态。"""
    from tradingagents.dataflows import gtja_api

    return gtja_api._request(
        endpoint,
        {"code": code, "startReportDate": start, "endReportDate": end},
        timeout=FAST_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 各数据源取数(逐个返回 三态 + 指标列表)
# ---------------------------------------------------------------------------
def _src_basis(code: str, start: str, end: str,
               series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:
    """基差/现货价(带参数端点)。SC / PX / FU / LU / CJ 实测为**确定无数据**。

    【分片】跨度 >MAX_SPAN_DAYS 自动分片(上游限 180 天, 见 MAX_SPAN_DAYS 注释)。
    """
    from tradingagents.dataflows import gtja_api

    res: dict[str, Any] = {
        "available": False, "error": "", "rows": 0, "note": "", "indicators": [],
        "span": f"{start} ~ {end}", "chunks": 0, "warning": "",
    }
    try:
        rows, chunks, warn = _chunked_request(
            gtja_api.EP_BASIS, code, start, end, MAX_SPAN_DAYS)
    except Exception as exc:  # noqa: BLE001 - 真失败(区别于 0 行)
        res["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return res

    res["available"] = True
    res["rows"] = len(rows)
    res["chunks"] = chunks
    res["warning"] = warn
    if not rows:
        res["note"] = ("接口正常应答 0 行 —— 该品种无现货指数口径。"
                       "国君基差只覆盖有现货指数的品种(实测 SC/PX/FU/LU/CJ 为空)。")
        return res

    # 同一天可能多地区/多指数行: 与 gtja_api.normalize_basis_rows 同规则优先 SMM 行,
    # 否则保留首个(口径漂移会让序列忽高忽低, 必须先定唯一)。
    sel: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = str(r.get("reportDate") or "").replace("-", "")
        if not d or _num(r.get("spotPrice")) is None:
            continue
        cur = sel.get(d)
        if cur is None or ("SMM" in str(r.get("spotIndexName") or "")
                           and "SMM" not in str(cur.get("spotIndexName") or "")):
            sel[d] = r
    if not sel:
        res["note"] = "接口有返回行, 但无一行含可解析的现货价。"
        return res

    def _iso(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 and d.isdigit() else d

    def _ser(field: str) -> list[dict[str, Any]]:
        return [{"date": _iso(d), "value": r.get(field)} for d, r in sel.items()]

    unit = ""
    for r in sel.values():
        if r.get("unitName"):
            unit = str(r["unitName"])
            break
    note = "现货价与其对应的参考合约基差(接口原始口径, 未做单位归一)"
    for iid, label, field, u in (
        ("basis.spot_price", "现货价", "spotPrice", unit),
        ("basis.contract_price", "参考合约价", "futuresPrice", unit),
        ("basis.value", "基差(现货−期货)", "basisValue", unit),
        ("basis.rate", "基差率", "basisPremiumRate", "%"),
    ):
        ind = _mk_indicator(iid, "基差", label, _ser(field), unit=u, note=note,
                            pct_scope=f"区间({res['span']})", series_cap=series_cap)
        if ind:
            res["indicators"].append(ind)
    if not res["indicators"]:
        res["note"] = "接口有返回行, 但四个可注入字段均无有效数值。"
    return res


def _src_warehouse(code: str, start: str, end: str,
                   series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:
    """交易所注册仓单(带参数端点)。实测 18/18 品种全覆盖。

    【分片】实测该端点**无 180 天限制**(500 天可用), 但仍走同一分片通道:
      一是留出统一入口便于上游将来加限制, 二是分片后单次响应更小、失败重试代价更低。
    """
    from tradingagents.dataflows import gtja_api

    res: dict[str, Any] = {
        "available": False, "error": "", "rows": 0, "note": "", "indicators": [],
        "span": f"{start} ~ {end}", "chunks": 0, "warning": "",
    }
    try:
        rows, chunks, warn = _chunked_request(
            gtja_api.EP_WAREHOUSE, code, start, end, WAREHOUSE_SPAN_DAYS)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return res

    res["available"] = True
    res["rows"] = len(rows)
    res["chunks"] = chunks
    res["warning"] = warn
    if not rows:
        res["note"] = "接口正常应答 0 行(该品种此区间无注册仓单记录)。"
        return res

    recs = []
    for r in rows:
        day = str(r.get("tradingDay") or r.get("reportDate") or "")
        v = _num(r.get("onWarrant"))
        if day and v is not None:
            recs.append({"date": day[:10], "value": v})
    if not recs:
        res["note"] = "接口有返回行, 但无一行含可解析的仓单量。"
        return res
    recs.sort(key=lambda x: x["date"])

    ind = _mk_indicator("warehouse.on_warrant", "仓单", "注册仓单", recs,
                        note="交易所注册仓单(onWarrant)。⚠️ 各品种计量单位不同(桶/张/吨), "
                             "接口未回传单位 ⇒ 此处不做归一, 仅供同品种纵向比较与验证。",
                        pct_scope=f"区间({res['span']})", series_cap=series_cap)
    if ind:
        res["indicators"].append(ind)
    # 日变化单独成一个指标(与主值同源, 便于在前端直接看"较前日")。
    chg = []
    for i in range(1, len(recs)):
        chg.append({"date": recs[i]["date"],
                    "value": recs[i]["value"] - recs[i - 1]["value"]})
    ind2 = _mk_indicator("warehouse.change", "仓单", "仓单日变化", chg,
                         note="相邻记录差值(与主值同为接口原始单位)。",
                         pct_scope=f"区间({res['span']})", series_cap=series_cap)
    if ind2:
        res["indicators"].append(ind2)
    if not res["indicators"]:
        res["note"] = "接口有返回行, 但仓单量字段均不可解析。"
    return res


def _full_dataset_source(code: str, endpoint: str, cache_dir, value_field: str,
                         group: str, kind_labels: list[str], max_labels: int,
                         note: str, series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:
    """全量端点(库存/利润)通用取数 → 按 dataType 分组各自成指标。

    【三态】fetch_dataset 失败会抛 GTJAError → available=False;
            正常返回但**该品种无任何 dataType** → available=True + note 说明无覆盖。
    【为什么在这里按 dataType 再分组】全量端点服务端忽略一切筛选参数(只认 page/size),
      必须本地筛; 分组逻辑与 gtja_api._split_data_type 同一口径(不自己重写拆分)。
    """
    from tradingagents.dataflows import gtja_api

    res: dict[str, Any] = {
        "available": False, "error": "", "rows": 0, "note": "", "indicators": [],
        "span": "全量数据集", "chunks": 0, "warning": "",
    }
    try:
        rows = gtja_api.fetch_dataset(endpoint, cache_dir)
    except Exception as exc:  # noqa: BLE001
        # ⚠️【2026-09-29 修】这里**必须直接 return**: 早先的写法在异常分支之后仍会落到
        #   末尾的 `res["note"] = ...` 赋值, 把"接口失败"改写成"全量数据集里没有 XX 的
        #   任何记录" —— 等于把**取数失败说成"该品种无数据"**。那正是本模块最想消灭的
        #   一类误导(用户据此会认为"国君确实没这个品种", 而实际只是这次请求失败了)。
        res["error"] = f"{type(exc).__name__}: {exc}"[:200]
        res["note"] = "⚠️ 该源本次取数失败, 下方无数据**不代表该品种没有数据**。"
        return res

    res["available"] = True
    # ⚠️【2026-09-29】`rows` 必须表示**该品种匹配到的记录数**, 不是数据集全量行数。
    #   早先写 `res["rows"] = len(rows)`(全量 18 万行), 前端会显示成"该品种有 188141 条数据"
    #   而 note 同时写着"没有 BU 的任何记录" —— 两个字段自相矛盾, 用户无法判断。
    #   全量行数另存 dataset_rows, 仅供排障。
    res["dataset_rows"] = len(rows)
    res["rows"] = 0
    want = code.strip().lower()
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        c, label = gtja_api._split_data_type(r.get("dataType") or "")
        if c != want:
            continue
        v = _num(r.get(value_field))
        d = str(r.get("dataDate") or "")
        if v is None or not d:
            continue
        groups.setdefault(label, []).append({"date": d[:10], "value": v})

    res["rows"] = sum(len(v) for v in groups.values())  # 该品种匹配到的记录数
    if not groups:
        res["note"] = f"全量数据集里没有 {code} 的任何记录(该源未覆盖此品种)。"
        return res

    # 排序: 优先"干净口径"(标签正好等于某个既定口径, 如 加工利润/期货库存),
    # 再按数据点数降序 —— 避免细分口径(如 X_现货_固定床)把主口径挤出名单。
    def _key(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, int]:
        lb, rows_ = item
        exact = 0 if lb in kind_labels else 1
        return (exact, -len(rows_))

    for label, ser in sorted(groups.items(), key=_key)[:max_labels]:
        ser.sort(key=lambda x: x["date"])
        ind = _mk_indicator(f"{group}.{label}", group, label or "(未命名口径)", ser,
                            note=note, pct_scope=f"全历史({len(ser)}点)", series_cap=series_cap)
        if ind:
            res["indicators"].append(ind)
    if not res["indicators"]:
        res["note"] = "该品种有记录, 但数值字段全部不可解析。"
    else:
        res["note"] = f"该品种共 {len(groups)} 个口径, 此处展示前 {len(res['indicators'])} 个。"
    return res


def fetch_overview(
    code: str, start: str | None = None, end: str | None = None,
    range_days: int | None = None,
) -> dict[str, Any]:
    """按品种聚合四个数据源 → 页面 payload(指标列表 + 各源三态)。

    Args:
        code: 品种代码(大小写不敏感)。
        start / end: YYYY-MM-DD; 缺省取近 range_days 天。
        range_days: 默认窗口天数(基差/仓单用)。⚠️ 会被夹到 ≤ MAX_SPAN_DAYS,
            否则基差端点必报 `[300001] 不超过180天`; 想查更长区间请显式传 start/end,
            由 _chunked_request 自动分片。
    【返回】{"ok":True, "variety","name","source","start","end","range_days",
             "indicators":[...], "sources":{源名:{available,error,rows,note,span,chunks,warning}},
             "counts":{...}, "unit_note"}
    【失败】未配密钥 / 日期倒置 → {"ok":False, "error":"..."}(不抛, 让路由直接回 200 + 错误体)。

    【★ 为什么 range_days 默认写 None 而不是 DEFAULT_RANGE_DAYS】Python 的默认参数在
      **函数定义时**求值一次。若写成 `range_days: int = DEFAULT_RANGE_DAYS`, 之后再修改
      模块常量 `DEFAULT_RANGE_DAYS` **完全不起作用**(签名里已固化成旧值) —— 一处改了没反应、
      排查起来极费时(变异测试实测: 把常量改到 400 天, 行为毫无变化 ⇒ 测试假绿)。
      写成 None + 体内读取, 常量才真正是"单一事实来源"。
    """
    from tradingagents.dataflows import gtja_api

    code = (code or "").strip().upper()
    if not gtja_api.configured():
        return {"ok": False, "error": "未配置国君(GTJA)数据源密钥(GTJA_ACCESS_KEY_ID/SECRET)"}

    today = date.today()
    e = (end or today.strftime("%Y-%m-%d")).strip()
    # 【为什么要夹到 MAX_SPAN_DAYS】无参数调用时若默认超过 175 天, 基差**必然**失败并显示
    #   成"接口失败" —— 用户会以为模块坏了, 而实际只是默认窗口越过上游限制。
    eff_days = _clamp_range_days(range_days)
    s = (start or (today - timedelta(days=eff_days)).strftime("%Y-%m-%d")).strip()
    if s > e:
        return {"ok": False, "error": f"开始日期不能晚于结束日期({s} > {e})"}

    jobs = {
        "basis": lambda: _src_basis(code, s, e),
        "warehouse": lambda: _src_warehouse(code, s, e),
        "inventory": lambda: _full_dataset_source(
            code, gtja_api.EP_GTJA_INVENTORY, gtja_api._GTJA_INV_CACHE_DIR,
            "inventoryValue", "库存", ["期货库存", "社会库存", "工厂库存"], 3,
            "国君自建库存数据集(与交易所仓单**不同源**; 期货库存是交割库口径, "
            "社会库存是产业链口径, 二者背离=库存向交割库转移)。",
        ),
        "profit": lambda: _full_dataset_source(
            code, gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR,
            "profitValue", "利润", ["加工利润", "进口利润"], MAX_PROFIT_LABELS,
            "加工/进口利润(接口原始单位)。慢变量, 宜与库存/基差共振判断, 不宜单独择时。",
        ),
    }
    out: dict[str, Any] = {}
    # 四源并发: 两个全量端点冷启动各需 6~18s, 串行会叠加成分钟级; 并发后 ≈ 最慢那一源。
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {k: pool.submit(fn) for k, fn in jobs.items()}
        for k, fut in futs.items():
            try:
                out[k] = fut.result()
            except Exception as exc:  # noqa: BLE001 - 单源异常不得拖垮整页
                logger.warning("GTJA overview 源 %s 异常(%s): %s", k, code, exc)
                out[k] = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                          "rows": 0, "note": "", "indicators": [], "span": ""}

    indicators: list[dict[str, Any]] = []
    for key in ("basis", "warehouse", "inventory", "profit"):
        indicators.extend(out[key].get("indicators") or [])
    indicators = indicators[:MAX_INDICATORS]

    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

        name = (VARIETY_METADATA.get(code) or {}).get("name", code)
    except Exception:  # noqa: BLE001 - 元数据拿不到不影响出数
        name = code

    return {
        "ok": True, "variety": code, "name": name,
        "source": "国泰君安(GTJA) VIP cloudApi", "start": s, "end": e,
        "range_days": eff_days,
        "indicators": indicators,
        "sources": {k: {kk: vv for kk, vv in v.items() if kk != "indicators"}
                    for k, v in out.items()},
        "counts": {
            "indicators": len(indicators),
            "available_sources": sum(1 for v in out.values() if v.get("available")),
            "failed_sources": sum(1 for v in out.values() if not v.get("available")),
        },
        "unit_note": "各序列单位以接口原始回传为准, 平台**不做单位归一** —— "
                     "不同品种/不同源的量纲不可直接比大小, 只可在同一序列内做纵向比较。",
        "span_note": f"基差端点上游限制单次查询不超过 180 天, 本模块按 {MAX_SPAN_DAYS} 天"
                     "自动分片; 分片有失败时会在对应源的 warning 里点名具体区间。",
    }


def fetch_coverage(codes: list[str], range_days: int = 30) -> dict[str, Any]:
    """全池覆盖矩阵: 每个品种哪些源有数据(供页面顶部一览 + 选品种时避坑)。

    【为什么只查带参数端点】库存/利润是全量端点, 一次拉全量即可覆盖全部品种
      (本地筛), 无需按品种逐个请求 ⇒ 这里对基差/仓单逐品种并发, 库存/利润只拉一次再本地判。
    【返回】{"ok":True, "start","end","sources":{源名:{available,error}},
             "rows":[{code,name,basis,warehouse,inventory,profit}], "summary":{每源命中数}}
      每个 cell 取值: "ok"(有数据) / "empty"(确定无数据) / "fail"(接口失败)。
    """
    from tradingagents.dataflows import gtja_api

    if not gtja_api.configured():
        return {"ok": False, "error": "未配置国君(GTJA)数据源密钥(GTJA_ACCESS_KEY_ID/SECRET)"}

    today = date.today()
    e = today.strftime("%Y-%m-%d")
    s = (today - timedelta(days=range_days)).strftime("%Y-%m-%d")
    codes = [c.strip().upper() for c in codes if c and c.strip()]

    def _q_basis(c: str) -> tuple[str, str]:
        r = _src_basis(c, s, e)
        return ("fail" if not r["available"] else ("ok" if r["rows"] else "empty")), r["error"]

    def _q_wh(c: str) -> tuple[str, str]:
        r = _src_warehouse(c, s, e)
        return ("fail" if not r["available"] else ("ok" if r["rows"] else "empty")), r["error"]

    basis_map: dict[str, tuple[str, str]] = {}
    wh_map: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fb = {c: pool.submit(_q_basis, c) for c in codes}
        fw = {c: pool.submit(_q_wh, c) for c in codes}
        for c in codes:
            try:
                basis_map[c] = fb[c].result()
            except Exception as exc:  # noqa: BLE001
                basis_map[c] = ("fail", f"{type(exc).__name__}: {exc}"[:120])
            try:
                wh_map[c] = fw[c].result()
            except Exception as exc:  # noqa: BLE001
                wh_map[c] = ("fail", f"{type(exc).__name__}: {exc}"[:120])

    # 两个全量源: 各拉一次, 本地判每个品种有无(避免 N 次全量拉取)。
    inv_codes: set[str] = set()
    prf_codes: set[str] = set()
    inv_state = ("ok", "")
    prf_state = ("ok", "")
    try:
        for r in gtja_api.fetch_dataset(gtja_api.EP_GTJA_INVENTORY, gtja_api._GTJA_INV_CACHE_DIR):
            c, _ = gtja_api._split_data_type(r.get("dataType") or "")
            if c:
                inv_codes.add(c)
    except Exception as exc:  # noqa: BLE001
        inv_state = ("fail", f"{type(exc).__name__}: {exc}"[:120])
    try:
        for r in gtja_api.fetch_dataset(gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR):
            c, _ = gtja_api._split_data_type(r.get("dataType") or "")
            if c:
                prf_codes.add(c)
    except Exception as exc:  # noqa: BLE001
        prf_state = ("fail", f"{type(exc).__name__}: {exc}"[:120])

    try:
        from tradingagents.dataflows.commodity_futures import VARIETY_METADATA as _VM

        _name = lambda c: (_VM.get(c) or {}).get("name", c)  # noqa: E731
    except Exception:  # noqa: BLE001
        _name = lambda c: c  # noqa: E731

    def _cell(state: tuple[str, str], has: bool, err: str = "") -> dict[str, str]:
        st, e_ = state
        if st == "fail":
            return {"state": "fail", "error": e_ or err}
        return {"state": "ok" if has else "empty", "error": ""}

    rows = []
    for c in codes:
        b_st, b_err = basis_map.get(c, ("fail", "未查询"))
        w_st, w_err = wh_map.get(c, ("fail", "未查询"))
        rows.append({
            "code": c, "name": _name(c),
            "basis": {"state": b_st, "error": b_err},
            "warehouse": {"state": w_st, "error": w_err},
            "inventory": _cell(inv_state, c.lower() in inv_codes),
            "profit": _cell(prf_state, c.lower() in prf_codes),
        })

    def _hits(k: str) -> int:
        return sum(1 for r in rows if r[k]["state"] == "ok")

    return {
        "ok": True, "start": s, "end": e, "rows": rows,
        "sources": {
            "basis": {"available": True, "error": ""},
            "warehouse": {"available": True, "error": ""},
            "inventory": {"available": inv_state[0] != "fail", "error": inv_state[1]},
            "profit": {"available": prf_state[0] != "fail", "error": prf_state[1]},
        },
        "summary": {k: _hits(k) for k in ("basis", "warehouse", "inventory", "profit")},
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# 季节性(按年同比) —— 数据仓库页走势图改为季节性图(2026-10-19 用户要求)
# ---------------------------------------------------------------------------
# 口径来自桌面「季节性图绘制包」(聚酯周报 PDF 那套): x 轴＝年内天数,
# 每年一条线, 当年粗线, 历史年细线且半透明, 最新点标注「值 (±变化)」, 6 色年份映射。
# 前端绘图, 这里只负责**把多年历史取回来并按年分组**。
SEASONAL_YEARS = 5          # 【变量】最多回看几个年份(含当年)
_SEASONAL_TTL = 6 * 3600    # 【变量】进程内缓存 TTL(秒); 基差/仓单是全量请求, 不能每点一次打一遍上游
_SEASONAL_CACHE: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}


def _doy(d: date) -> int:
    """日期 → 年内天数(0 基), 且**闰年对齐到非闰年轴**。

    【为什么必须对齐】季节性图是"不同年份同一天并排看"。若闰年 3 月 1 日记成 60、
      平年记成 59, 同一根竖线上会差一天, 曲线整体错位一格; 更糟的是 2/29 与 3/1
      在图中会落到同一位置。把闰年 2/29 之后统一 -1 ⇒ 全年 0~365, 与平年逐日对齐。
    """
    doy = (d - date(d.year, 1, 1)).days
    leap = (d.year % 4 == 0 and d.year % 100 != 0) or d.year % 400 == 0
    if leap and doy > 59:
        doy -= 1
    return max(0, min(365, doy))


def to_seasonal_years(series: list[dict[str, Any]],
                      max_years: int = SEASONAL_YEARS) -> dict[str, Any]:
    """[{date,value}] → 季节性结构(纯函数, 可直接单测)。

    【返回】{"years": {"2024": [[doy, val], ...]}, "latest": {doy,val,date,diff},
             "year_count", "point_count", "first", "last"}
      · years 的键按年份升序保留**最近 max_years 个**(缺数据的年份自然缺席)
      · latest.diff = 与"上一个数据点"的差值(不是同比), 供图上标注 "(±x)"
    【边界】空序列 → years={}, latest=None(前端据此出空态, 绝不画 0 值假线)。
    """
    pts: list[tuple[date, float]] = []
    for p in series or []:
        ds = str(p.get("date") or "")[:10]
        v = _num(p.get("value"))
        if len(ds) < 10 or v is None:
            continue
        try:
            dt = date.fromisoformat(ds)
        except ValueError:
            continue
        pts.append((dt, v))
    pts.sort(key=lambda x: x[0])
    if not pts:
        return {"years": {}, "latest": None, "year_count": 0, "point_count": 0,
                "first": "", "last": ""}

    grouped: dict[int, dict[int, float]] = {}
    for dt, v in pts:
        # 同日重复行(接口偶发)后值覆盖前值, 与基差"同一天多地区只留一条"同一思路
        grouped.setdefault(dt.year, {})[_doy(dt)] = v
    years = sorted(grouped)[-max(1, int(max_years)):]
    out_years = {
        str(y): [[d, round(grouped[y][d], 6)] for d in sorted(grouped[y])]
        for y in years
    }

    last_dt, last_v = pts[-1]
    diff = round(last_v - pts[-2][1], 6) if len(pts) > 1 else None
    return {
        "years": out_years,
        "latest": {"doy": _doy(last_dt), "val": round(last_v, 6),
                   "date": last_dt.isoformat(), "diff": diff},
        "year_count": len(years),
        "point_count": sum(len(v) for v in out_years.values()),
        "first": pts[0][0].isoformat(),
        "last": last_dt.isoformat(),
    }


def fetch_indicator_seasonal(code: str, iid: str,
                             years: int = SEASONAL_YEARS) -> dict[str, Any]:
    """按指标 id 回取多年历史并按年分组, 供「季节性图」渲染。

    Args:
        code: 品种代码。
        iid: 指标 id(与 fetch_overview 返回的 indicators[].id 完全一致), 前缀决定数据源:
             `basis.*` 基差端点 / `warehouse.*` 仓单端点 / `库存.*`·`利润.*` 全量数据集。
        years: 回看年份数(含当年), 夹到 [1, SEASONAL_YEARS]。
    【返回】{"ok":True, id,label,group,unit,note,span,chunks,warning, cached,
             years, latest, year_count, point_count, first, last}
    【失败】{"ok":False, "error": "..."}(200 返回, 前端保持"本地单年图"不白屏)

    【为什么单独一个接口而不复用 overview】overview 的序列被 MAX_SERIES_POINTS 裁到 250 点、
      且基差/仓单默认只查 175 天 —— 两者都只够画"近半年", 画不出多年同比。
      本接口对同一指标**显式要全历史**(series_cap=0)并按年分组, 与页面快照互不影响。
    """
    from tradingagents.dataflows import gtja_api

    code = (code or "").strip().upper()
    iid = (iid or "").strip()
    if not gtja_api.configured():
        return {"ok": False, "error": "未配置国君(GTJA)数据源密钥(GTJA_ACCESS_KEY_ID/SECRET)"}
    if not iid:
        return {"ok": False, "error": "缺少指标参数(id)"}
    try:
        n_years = int(years)
    except (TypeError, ValueError):
        n_years = SEASONAL_YEARS
    n_years = max(1, min(n_years, SEASONAL_YEARS))

    key = (code, iid, n_years)
    hit = _SEASONAL_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _SEASONAL_TTL:
        payload = dict(hit[1])
        payload["cached"] = True
        return payload

    today = date.today()
    start = date(today.year - n_years + 1, 1, 1).isoformat()
    end = today.isoformat()

    try:
        if iid.startswith("库存."):
            src = _full_dataset_source(
                code, gtja_api.EP_GTJA_INVENTORY, gtja_api._GTJA_INV_CACHE_DIR,
                "inventoryValue", "库存", ["期货库存", "社会库存", "工厂库存"], 3,
                "国君自建库存数据集", series_cap=0)
        elif iid.startswith("利润."):
            src = _full_dataset_source(
                code, gtja_api.EP_PROFIT, gtja_api._PROFIT_CACHE_DIR,
                "profitValue", "利润", ["加工利润", "进口利润"], MAX_PROFIT_LABELS,
                "加工/进口利润", series_cap=0)
        elif iid.startswith("basis."):
            src = _src_basis(code, start, end, series_cap=0)
        elif iid.startswith("warehouse."):
            src = _src_warehouse(code, start, end, series_cap=0)
        else:
            return {"ok": False, "error": f"未知指标 id: {iid}", "id": iid}
    except Exception as exc:  # noqa: BLE001 - 单个指标取数失败按接口错误上报
        logger.warning("GTJA seasonal 取数异常(%s/%s): %s", code, iid, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200], "id": iid}

    if not src.get("available"):
        return {"ok": False, "error": src.get("error") or "取数失败", "id": iid}

    ind = next((i for i in (src.get("indicators") or []) if i.get("id") == iid), None)
    if not ind:
        return {"ok": False, "id": iid,
                "error": "该指标在当前口径下无序列(上游可能未覆盖此品种)",
                "note": src.get("note") or ""}

    seas = to_seasonal_years(
        [{"date": d, "value": v} for d, v in (ind.get("series") or [])], n_years)
    payload: dict[str, Any] = {
        "ok": True, "variety": code, "id": iid,
        "label": ind.get("label"), "group": ind.get("group"),
        "unit": ind.get("unit") or "", "note": ind.get("note") or "",
        "span": f"{start} ~ {end}", "chunks": src.get("chunks") or 0,
        "warning": src.get("warning") or "", "cached": False,
        **seas,
    }
    _SEASONAL_CACHE[key] = (time.time(), payload)
    return payload
