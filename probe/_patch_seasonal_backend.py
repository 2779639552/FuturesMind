# -*- coding: utf-8 -*-
"""给 gtja_module.py 增加「季节性(按年同比)」能力。

改动:
 1. _tail / _mk_indicator / _src_basis / _src_warehouse / _full_dataset_source
    增加 series_cap 参数(默认仍为 MAX_SERIES_POINTS, 行为不变) —— 季节性需要**全历史**序列,
    不能被"只留最近 250 点"的图表裁剪掐掉多年数据。
 2. 新增 to_seasonal_years(): 序列 → {years: {年: [[年内天数, 值]]}, latest}
 3. 新增 fetch_indicator_seasonal(): 按指标 id 回取多年历史 + 分组(带进程内 TTL 缓存)
"""
import io, sys

P = "gtja_module.py"
src = io.open(P, encoding="utf-8", newline="").read()
orig = src


def once(old, new, tag):
    global src
    n = src.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次(应为 1)" % (tag, n)
    src = src.replace(old, new, 1)


# ── 1) _tail 增加 cap ────────────────────────────────────────────────
once(
    'def _tail(series: list[dict[str, Any]]) -> list[list[Any]]:\n'
    '    """序列裁成 [[date, value], ...] 且只留最近 MAX_SERIES_POINTS 点(图表用, 省带宽)。"""\n'
    '    out: list[list[Any]] = []\n'
    '    for p in series[-MAX_SERIES_POINTS:]:',
    'def _tail(series: list[dict[str, Any]], cap: int = MAX_SERIES_POINTS) -> list[list[Any]]:\n'
    '    """序列裁成 [[date, value], ...] 且只留最近 cap 点(图表用, 省带宽)。\n'
    '\n'
    '    【cap=0 的语义】**不裁**, 返回全历史 —— 季节性图要按年对齐 5 年曲线,\n'
    '      被裁到 250 点就只剩一年, 同比无从谈起(2026-10-19 加)。\n'
    '    """\n'
    '    out: list[list[Any]] = []\n'
    '    for p in (series if cap <= 0 else series[-cap:]):',
    "_tail",
)

# ── 2) _mk_indicator 增加 series_cap ────────────────────────────────
once(
    'def _mk_indicator(\n'
    '    iid: str, group: str, label: str, series: list[dict[str, Any]],\n'
    '    unit: str = "", note: str = "", pct_scope: str = "",\n'
    ') -> dict[str, Any] | None:',
    'def _mk_indicator(\n'
    '    iid: str, group: str, label: str, series: list[dict[str, Any]],\n'
    '    unit: str = "", note: str = "", pct_scope: str = "",\n'
    '    series_cap: int = MAX_SERIES_POINTS,\n'
    ') -> dict[str, Any] | None:',
    "_mk_indicator sig",
)
once('        "series": _tail(series),', '        "series": _tail(series, series_cap),', "_mk_indicator body")

# ── 3) _src_basis 透传 ─────────────────────────────────────────────
once(
    'def _src_basis(code: str, start: str, end: str) -> dict[str, Any]:',
    'def _src_basis(code: str, start: str, end: str,\n'
    '               series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:',
    "_src_basis sig",
)
once(
    '        ind = _mk_indicator(iid, "基差", label, _ser(field), unit=u, note=note,\n'
    '                            pct_scope=f"区间({res[\'span\']})")',
    '        ind = _mk_indicator(iid, "基差", label, _ser(field), unit=u, note=note,\n'
    '                            pct_scope=f"区间({res[\'span\']})", series_cap=series_cap)',
    "_src_basis call",
)

# ── 4) _src_warehouse 透传 ─────────────────────────────────────────
once(
    'def _src_warehouse(code: str, start: str, end: str) -> dict[str, Any]:',
    'def _src_warehouse(code: str, start: str, end: str,\n'
    '                   series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:',
    "_src_warehouse sig",
)
once(
    '                        pct_scope=f"区间({res[\'span\']})")\n'
    '    if ind:\n'
    '        res["indicators"].append(ind)\n'
    '    # 日变化单独成一个指标',
    '                        pct_scope=f"区间({res[\'span\']})", series_cap=series_cap)\n'
    '    if ind:\n'
    '        res["indicators"].append(ind)\n'
    '    # 日变化单独成一个指标',
    "_src_warehouse call1",
)
once(
    '    ind2 = _mk_indicator("warehouse.change", "仓单", "仓单日变化", chg,\n'
    '                         note="相邻记录差值(与主值同为接口原始单位)。",\n'
    '                         pct_scope=f"区间({res[\'span\']})")',
    '    ind2 = _mk_indicator("warehouse.change", "仓单", "仓单日变化", chg,\n'
    '                         note="相邻记录差值(与主值同为接口原始单位)。",\n'
    '                         pct_scope=f"区间({res[\'span\']})", series_cap=series_cap)',
    "_src_warehouse call2",
)

# ── 5) _full_dataset_source 透传 ───────────────────────────────────
once(
    'def _full_dataset_source(code: str, endpoint: str, cache_dir, value_field: str,\n'
    '                         group: str, kind_labels: list[str], max_labels: int,\n'
    '                         note: str) -> dict[str, Any]:',
    'def _full_dataset_source(code: str, endpoint: str, cache_dir, value_field: str,\n'
    '                         group: str, kind_labels: list[str], max_labels: int,\n'
    '                         note: str, series_cap: int = MAX_SERIES_POINTS) -> dict[str, Any]:',
    "_full sig",
)
once(
    '        ind = _mk_indicator(f"{group}.{label}", group, label or "(未命名口径)", ser,\n'
    '                            note=note, pct_scope=f"全历史({len(ser)}点)")',
    '        ind = _mk_indicator(f"{group}.{label}", group, label or "(未命名口径)", ser,\n'
    '                            note=note, pct_scope=f"全历史({len(ser)}点)", series_cap=series_cap)',
    "_full call",
)

# ── 6) 追加季节性函数 ─────────────────────────────────────────────
APPEND = '''

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
'''

if "\nSEASONAL_YEARS = 5" in src:
    raise SystemExit("季节性函数已存在, 中止(避免重复追加)")

src = src.rstrip("\n") + "\n" + APPEND

# time 用于 TTL 缓存
if "\nimport time\n" not in src:
    once("import logging\n", "import logging\nimport time\n", "import time")

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("OK 写入", P, "| 字节", len(orig), "->", len(src))
