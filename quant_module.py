"""quant_module.py — 量化/技术分析模块后端(Flask Blueprint)

【模块角色】(2026-09-28 新增) 把此前散落在「研报问答」各数据块里的量化算法
(技术指标 / 库存周期四象限 / 库存+基差信号 / 反转预警)与「有后端无前端」的
20+ 策略回测接口,统一收拢成一个面向用户的独立分析模块,提供 7 个子模块:

  ① K 线图         /api/quant/kline      日K后复权/分钟K + MA/BOLL 叠加 + 成交量
  ② 技术指标面板    /api/quant/technical  MA/RSI/MACD/BOLL/ATR/波动率/夏普/多空计分
  ③ 基差与期限结构  /api/quant/basis      基差走势与分位(国君) + 跨月合约曲线(akshare)
  ④ 仓单库存       /api/quant/inventory  仓单序列 + 库存周期四象限
  ⑤ 量化信号       /api/quant/signals    库存+基差信号 / 反转三信号 / 综合研判
  ⑥ 策略回测       /api/quant/backtest   12+ 策略选择 + 参数 + 指标 + 净值曲线
  ⑦ 截面排行       /api/quant/ranking    多品种量化截面(基差/库存/技术/综合)
  ⑫ 数据计算面板    /api/quant/compute    维度多选 → 多序列曲线 + 相关系数矩阵(2026-09-30)

【数据源(2026-09-30 实测覆盖, 品种池 = ACTIVE_VARIETIES 21 个)】
  - 价格/成交量/持仓量: akshare 主连日线(后复权) —— 21/21
  - 分钟 K:            akshare 新浪分钟(主连 code0) —— 21/21
  - 基差/现货价:        国君 GTJA basisData.query → 生意社 100ppi 兜底 —— 18/21
                        (两源皆无: SC/LU/CJ; 旧记的 EG/PX/FU/BZ 已由 100ppi 兜底源补齐)
  - 交易所注册仓单:     国君 GTJA fut.warehouseStock.query —— 21/21(BZ 偶发限流, 重试即通)
  - 周度观点信号:       国君 GTJA commodity.weekly.viewpoint.query —— 21/21
  - 跨月合约报价:       akshare futures_zh_realtime(全合约) —— 21/21
                        ⚠ 2026-10-16 实测复核: 原"21/21"是**错的**, 实际 17/21 ——
                        FU/P/PG/PX 因新浪命名表用别名(燃油/棕榈/液化石油气/二甲苯)
                        而 KeyError。已加 _SINA_NAME_ALIASES 逐候选名重试修复。
  - 回测价格序列:       signal_analyzer._load_price(思路2 trends JSON) —— 21/21
  ⚠️ 上表数字为 2026-09-30 实测留档; /api/quant/meta 的 coverage 已改为**按池动态派生**,
     不再硬编码, 扩池后自动跟随(见 _spot_source_sets)。

【设计约束】
  - 纯读接口,不写库、不写任何文件、不影响 RAG/研报/采集链路。
  - 单块失败只降级该块(返回 ok:False + error),绝不 500,前端逐卡显示空态。
  - 所有指标纯 pandas/numpy 手写(项目未装 ta-lib),与 web_app 既有 QA 数据块同口径。
  - 品种池严格取 ACTIVE_VARIETIES(21 个),未知品种返回 400。

【与 web_app 的关系】通过 Flask Blueprint 挂载(web_app.register_blueprint),
  避免继续膨胀 625KB 的 web_app.py;注册只需 1 行。Blueprint 内自行延迟导入
  web_app 需要的符号,避免与 web_app 循环导入。
"""

from __future__ import annotations

import io  # 【调用包】CSV 文本解析(价格接口返回 CSV)
import logging  # 【调用包】降级告警
import threading  # 【调用包】TTL 缓存加锁
import time  # 【调用包】TTL 缓存时间戳
from datetime import datetime, timedelta  # 【调用包】日期窗推算
from typing import Any  # 【调用包】类型标注

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

quant_bp = Blueprint("quant", __name__)  # 【变量】量化模块蓝图(web_app 一行注册)

# ── 进程内 TTL 缓存(避免切品种/切子模块时狂打外部接口) ─────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}  # 【变量】key → (写入时间, 值)
_CACHE_LOCK = threading.Lock()  # 【变量】缓存读写锁
_TTL_BASIS = 900  # 【变量】基差缓存 15 min(日频数据, 盘中不变)
_TTL_INVENTORY = 900  # 【变量】仓单缓存 15 min(日频, 每日 16:30 后更新)
_TTL_TERM = 60  # 【变量】期限结构缓存 60s(盘中实时报价, 但不必每请求都打)
_TTL_VIEW = 1800  # 【变量】周度观点缓存 30 min(周频)
_TTL_RANK = 300  # 【变量】截面排行整体缓存 5 min(21 品种 × 多源, 避免重复全量拉)

# 【为什么需要短超时】GTJA 对个别品种会 ReadTimeout, 默认 45s×4 次重试 = 180s/品种;
# 截面排行要遍历 21 品种, 实测曾整体耗时 842s。批量场景改用短超时快速失败,
# 让"个别品种服务端慢"只影响该品种(留空), 不拖垮整表。
_GTJA_BATCH_TIMEOUT = 8  # 【变量】批量场景 GTJA 单次请求超时(秒)
_RANK_PARALLEL = 6  # 【变量】截面排行品种并发数(与 collect_pool 有界并发同思路)


def _cache_get(key: str, ttl: int) -> Any | None:
    """读 TTL 缓存;未命中/过期返回 None。"""
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_set(key: str, val: Any) -> None:
    """写 TTL 缓存(线程安全)。"""
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)


def _pool() -> list[str]:
    """当前活跃品种池(排序后,供下拉/校验/排行用)。"""
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES

    return sorted(ACTIVE_VARIETIES)


def _meta(code: str) -> dict:
    """品种元数据(name/exchange_cn/sector_cn/unit/main_contract 等)。"""
    from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

    return VARIETY_METADATA.get(code, {}) or {}


def _norm_code(raw: str | None) -> str:
    """归一化并校验品种代码;不在池中返回空串(调用方据此回 400)。"""
    code = (raw or "").strip().upper()
    return code if code in _pool() else ""


def _f(x) -> float | None:
    """安全转 float:None/NaN/不可转 → None(JSON 里不留 NaN,否则序列化报错)。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _json_safe(obj):
    """递归清洗 NaN/Inf → None(Flask jsonify 不接受 NaN)。"""
    import math

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


# ===========================================================================
# ① K 线图
# ===========================================================================
def _load_daily_bars(code: str, days: int) -> list[dict]:
    """取日 K(akshare 主连 + 后复权),返回 [{date, open, high, low, close, volume, open_interest}]。

    【复用】直接调 web_app._kline_points(与看板 K 线同口径:真实换月日历优先的后复权),
    避免两处复权算法漂移。价格 CSV 来自 get_futures_price。
    """
    from tradingagents.dataflows.commodity_futures import get_futures_price
    from web_app import _kline_points  # 【调用函数】复用既有后复权 K 线解析(同口径)

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = get_futures_price(code, start, end)
    bars = _kline_points(result, code)
    # 补 open_interest(持仓量, 技术分析/资金面要用; _kline_points 只保留 OHLCV)
    oi_by_date: dict[str, float] = {}
    for line in str(result or "").strip().split("\n"):
        parts = line.split(",")
        if len(parts) >= 7 and parts[0].strip()[:4].isdigit():
            v = _f(parts[6])
            if v is not None:
                oi_by_date[parts[0].strip()] = v
    for b in bars:
        b["open_interest"] = oi_by_date.get(b["date"])
    return bars


def _load_minute_bars(code: str, period: str, bars: int) -> list[dict]:
    """取分钟 K(akshare 新浪,主连 code0,不复权)。委托 web_app._kline_minute 保持同口径。"""
    from web_app import _kline_minute  # 【调用函数】复用既有分钟 K 实现

    data = _kline_minute(code, period)
    if bars and len(data) > bars:
        data = data[-bars:]
    return data


def _ma(values: list[float], n: int) -> list[float | None]:
    """简单移动平均(前 n-1 个为 None,与图表对齐)。"""
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        out.append(round(s / n, 4) if i >= n - 1 else None)
    return out


def _boll(values: list[float], n: int = 20, k: float = 2.0):
    """布林带 → (上轨, 中轨, 下轨) 三个与输入等长的列表(不足窗口为 None)。"""
    import numpy as np

    arr = np.asarray(values, dtype=float)
    up: list[float | None] = []
    mid: list[float | None] = []
    low: list[float | None] = []
    for i in range(len(arr)):
        if i < n - 1:
            up.append(None); mid.append(None); low.append(None)
            continue
        w = arr[i - n + 1: i + 1]
        m = float(w.mean())
        sd = float(w.std(ddof=1)) if n > 1 else 0.0
        mid.append(round(m, 4)); up.append(round(m + k * sd, 4)); low.append(round(m - k * sd, 4))
    return up, mid, low


@quant_bp.route("/api/quant/kline")
def api_quant_kline():
    """① K 线数据(量化模块主图)。

    【参数】variety(必填, 21 品种池); period=day/1/5/15/30/60(默认 day);
            days=日K回看天数(默认 250, 钳 60~730); bars=分钟K根数(默认 500, 钳 50~2000);
            overlap=ma5,ma10,ma20,boll,none(默认 ma5,ma10,ma20) 均线叠加方式。
    【返回】{ok, variety, name, unit, period, adjusted, count, start, end,
            klines:[{date,open,high,low,close,volume,open_interest}],
            overlays:{ma5:[],ma10:[],ma20:[],ma60:[],boll_up:[],boll_mid:[],boll_low:[]},
            volume:[...]}。
    【关键】日 K 走真实换月日历后复权(与看板同口径);分钟 K 不复权(主连连续跳空小)。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)需在 21 品种池内"}), 400
    period = (request.args.get("period") or "day").strip().lower()
    days = max(60, min(request.args.get("days", 250, type=int) or 250, 730))
    bars = max(50, min(request.args.get("bars", 500, type=int) or 500, 2000))
    overlap = (request.args.get("overlap") or "ma5,ma10,ma20").strip().lower()

    if period in ("1", "5", "15", "30", "60"):
        klines = _load_minute_bars(code, period, bars)
        adjusted, pname = False, f"{period}min"
    else:
        period = "day"
        klines = _load_daily_bars(code, days)
        adjusted, pname = True, "day"

    if not klines:
        return jsonify({"ok": False, "variety": code, "error": "未取到 K 线数据(数据源暂不可用)"}), 200

    closes = [b["close"] for b in klines]
    overlays: dict[str, list[float | None]] = {}
    if "none" not in overlap:
        for tag in ("ma5", "ma10", "ma20", "ma60"):
            if tag in overlap or overlap in ("", "ma5,ma10,ma20"):
                n = int(tag[2:])
                if len(closes) >= n:
                    overlays[tag] = _ma(closes, n)
        if "boll" in overlap:
            bu, bm, bl = _boll(closes, 20, 2.0)
            overlays.update({"boll_up": bu, "boll_mid": bm, "boll_low": bl})

    meta = _meta(code)
    # 【2026-10-12】如实标注本次 K 线实际来自哪个数据源('tq' 天勤 / 'akshare' 新浪),
    #   供前端在图上显示来源,避免"以为接了天勤其实还在走免费源"的静默偏差。
    source = "unknown"
    try:
        from tradingagents.dataflows import tq_source
        source = tq_source.last_source()
    except Exception:
        pass
    return jsonify(_json_safe({
        "ok": True,
        "variety": code,
        "name": meta.get("name", code),
        "unit": meta.get("unit", ""),
        "period": pname,
        "adjusted": adjusted,
        "source": source,
        "count": len(klines),
        "start": klines[0]["date"],
        "end": klines[-1]["date"],
        "klines": klines,
        "overlays": overlays,
    }))


# ===========================================================================
# ② 技术指标面板
# ===========================================================================
def _daily_bars_cached(code: str, days: int) -> list[dict]:
    """带 TTL 缓存的日线读取。

    【为什么要它】总览对同一品种要算「日线周期方向」与「技术快照」两件事，两者都
    需要日线；`_technical_snapshot` 内部会自己调 `_load_daily_bars` ⇒ 不缓存就是
    一个品种拉两遍日线（实测冷启动单次 5.4s，21 品种白白多花一倍时间）。
    日线一天只变一次，缓存 5 分钟零风险；`_cross_section_one` 也一并受益。
    【为什么可以不担心污染】下游只读不写（`pd.DataFrame(bars)` 是拷贝）；但**缓存
    返回的是同一个 list 对象**，所以任何调用点都**不得原地修改**返回的 bars。
    """
    key = "dbars:%s:%d" % (code, days)
    hit = _cache_get(key, _TTL_OVERVIEW)
    if hit is not None:
        return hit
    bars = _load_daily_bars(code, days)
    _cache_set(key, bars)
    return bars


def _technical_snapshot(code: str, days: int = 250) -> dict:
    """计算技术指标快照(纯 pandas 手写,与 web_app QA 技术块同口径)。

    【指标】趋势 MA5/10/20/60 + 排列; 动量 RSI14(Wilder)/MACD(12,26,9)/20日动量;
    波动 BOLL(20,2)/ATR14/年化波动率; 资金 持仓量5日变化; 量化 夏普(近60日);
    多空计分(5 项 ±1, -5~+5)。
    【返回】{ok, latest, date, indicators:{...}, score, verdict, ma_state, series:{...}};
    数据不足 30 根 → {ok:False, error}。
    """
    import pandas as pd

    bars = _daily_bars_cached(code, days)  # 【调用函数】带缓存, 见其 docstring
    if len(bars) < 30:
        return {"ok": False, "error": f"日线数据不足({len(bars)} 根, 需 ≥30)"}
    df = pd.DataFrame(bars)
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    latest = float(close.iloc[-1])
    last_date = str(df["date"].iloc[-1])[:10]

    ma = {f"ma{n}": _f(close.rolling(n).mean().iloc[-1]) for n in (5, 10, 20, 60)}
    # RSI14 (Wilder 平滑)
    diff = close.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = up / dn.replace(0, float("nan"))
    rsi = _f((100 - 100 / (1 + rs)).iloc[-1]) or 50.0
    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = _f((dif - dea).iloc[-1]) or 0.0
    # BOLL(20,2)
    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    boll_up, boll_mid = _f((mid + 2 * sd).iloc[-1]), _f(mid.iloc[-1])
    boll_low = _f((mid - 2 * sd).iloc[-1])
    # ATR14
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = _f(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    # 量化
    ret = close.pct_change().dropna()
    mom20 = _f(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else None
    mom20 = round(mom20 * 100, 2) if mom20 is not None else None
    tail = ret.tail(60)
    ann_vol = _f(tail.std() * (252 ** 0.5) * 100)
    sharpe = _f(tail.mean() / tail.std() * (252 ** 0.5)) if _f(tail.std()) else 0.0
    # 持仓量(资金面)
    oi = pd.to_numeric(df.get("open_interest"), errors="coerce").dropna()
    oi_chg = _f(oi.iloc[-1] / oi.iloc[-6] - 1) if len(oi) > 6 and _f(oi.iloc[-6]) else None
    oi_chg = round(oi_chg * 100, 2) if oi_chg is not None else None
    oi_latest = _f(oi.iloc[-1]) if len(oi) else None

    if ma["ma5"] and ma["ma10"] and ma["ma20"]:
        ma_state = "多头排列" if ma["ma5"] > ma["ma10"] > ma["ma20"] else (
            "空头排列" if ma["ma5"] < ma["ma10"] < ma["ma20"] else "均线纠缠")
    else:
        ma_state = "数据不足"
    score = 0
    if ma["ma5"] and ma["ma10"] and ma["ma20"]:
        score += 1 if ma["ma5"] > ma["ma10"] > ma["ma20"] else (-1 if ma["ma5"] < ma["ma10"] < ma["ma20"] else 0)
    score += 1 if rsi > 50 else -1
    score += 1 if macd_bar > 0 else -1
    if ma["ma20"]:
        score += 1 if latest > ma["ma20"] else -1
    if boll_mid:
        score += 1 if latest > boll_mid else -1
    verdict = "偏多" if score >= 2 else ("偏空" if score <= -2 else "中性")

    # ADX/ADXR(墨菲 cap-trading-systems: ADXR<20 表示无趋势, 不适用趋势顺应系统)
    # 【为什么放这里】本函数已加载 bars, 复用可避免截面排行再打一次 akshare。
    adxr_info: dict = {}
    try:
        import quant_methodology as _qm

        adxr_info = _qm.compute_adx_adxr(bars) or {}
    except Exception as e:  # noqa: BLE001 - ADXR 失败不影响其余指标
        logger.warning("ADXR 计算失败(%s): %s", code, e)

    # 序列(供前端画迷你图; 与 bars 等长, 便于与 K 线共轴)
    last_n = min(len(bars), 180)
    series = {
        "date": [b["date"] for b in bars[-last_n:]],
        "close": [b["close"] for b in bars[-last_n:]],
        "ma20": [None if v is None else round(float(v), 4) for v in close.rolling(20).mean().tail(last_n)],
        "rsi": [None if v != v else round(float(v), 2) for v in
                (100 - 100 / (1 + rs)).tail(last_n)],
        "macd_dif": [None if v != v else round(float(v), 4) for v in dif.tail(last_n)],
        "macd_dea": [None if v != v else round(float(v), 4) for v in dea.tail(last_n)],
        "macd_bar": [None if v != v else round(float(v), 4) for v in (dif - dea).tail(last_n)],
    }
    return {"ok": True, "date": last_date, "latest": latest,
            "indicators": {**ma, "rsi14": round(rsi, 2), "macd_bar": round(macd_bar, 4),
                           "boll_up": boll_up, "boll_mid": boll_mid, "boll_low": boll_low,
                           "atr14": atr, "mom20_pct": mom20, "ann_vol_pct": ann_vol,
                           "sharpe60": _f(round(sharpe, 3)) if sharpe is not None else None,
                           "oi_latest": oi_latest, "oi_chg5_pct": oi_chg,
                           "adx": adxr_info.get("adx"), "adxr": adxr_info.get("adxr"),
                           "adxr_level": adxr_info.get("level"),
                           "adxr_tradable": adxr_info.get("tradable"),
                           # 【2026-10-13 补】±DI 随 ADXR 一起出: 前端「趋势性」行要用它表明
                           #   方向性(墨菲: +DI 上穿 -DI=买, 下穿=卖)。compute_adx_adxr 早就
                           #   算了这两个值, 只是此前没往外传, 前端就无从展示。
                           "plus_di": adxr_info.get("plus_di"),
                           "minus_di": adxr_info.get("minus_di")},
            "score": score, "verdict": verdict, "ma_state": ma_state, "series": series}


@quant_bp.route("/api/quant/technical")
def api_quant_technical():
    """② 技术指标面板(单品种快照 + 序列)。

    【参数】variety(必填); days=回看天数(默认 250, 钳 60~730)。
    【返回】{ok, variety, name, unit, ..._technical_snapshot 的全部字段}。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    days = max(60, min(request.args.get("days", 250, type=int) or 250, 730))
    snap = _technical_snapshot(code, days)
    meta = _meta(code)
    return jsonify(_json_safe({"variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""), **snap}))


# ===========================================================================
# ③ 基差与期限结构
# ===========================================================================
def _fetch_basis(code: str, days: int, batch: bool = False) -> dict:
    """取基差序列(国君优先, 生意社 100ppi 兜底);无覆盖品种返回 ok:False + 原因。

    【batch】True 时用短超时(见 _GTJA_BATCH_TIMEOUT),供截面排行等多品种场景;
    单品种详情页用默认长超时, 保证"慢但能出数"。

    【★ 两源回退链(2026-09-30 新增)】
      源 1 国君 GTJA basisData.query —— 覆盖 15/21。
      源 2 生意社 100ppi          —— 补 FU/PX/BZ(见 ppi_basis._PPI_NAME), 仅当源 1 空时启用。
      ★ 两源合计覆盖 18/21(两源皆无: SC/LU/CJ)。
      ★ 两源 dom_basis 均为 **现货 − 期货**(已互校, 见 fund_module 文件头符号铁律),
        故 row 结构完全同构, 调用方(前端/fund_module)无需分支。
      ★ 仍无现货指数: SC 原油 / LU 低硫燃料油 / CJ 红枣(两源皆无), 由前端如实标注。
      ★ 两源皆失败时 error 会写明"已试 国君 → 生意社", 并带 tried_sources/gtja_error/
        ppi_error 三个字段; 若生意社是**不可用**(而非无覆盖), 另标 ppi_broken=True,
        避免把"源故障"写成"数据源边界"。
    """
    key = f"basis:{code}:{days}"
    hit = _cache_get(key, _TTL_BASIS)
    if hit is not None:
        return hit

    res = _fetch_basis_gtja(code, days, batch)
    if not res.get("ok"):
        gtja_err = res.get("error", "")
        alt = _fetch_basis_ppi(code, days)
        if alt.get("ok"):
            alt["fallback_from"] = "gtja"
            res = alt
        else:
            # ★ 两源皆失败: 必须如实保留"已试过哪几源", 不能只留国君文案
            #   —— 否则前端/策略页会把"两源皆无"(数据源边界)误读成"国君故障"。
            #   生意社模块 import 失败时 ppi_error 会是"生意社兜底失败: ...",
            #   该字段一并透出, 故"源不可用"与"源无覆盖"仍可区分。
            ppi_err = alt.get("error", "")
            ppi_broken = ppi_err.startswith("生意社兜底失败")
            res = {
                "ok": False,
                "error": ("该品种无现货指数口径(国君未覆盖; 生意社兜底源当前不可用)"
                          if ppi_broken else
                          "该品种无现货指数口径(已试 国君 GTJA → 生意社 100ppi; 两源均未覆盖)"),
                "rows": [],
                "tried_sources": ["gtja", "100ppi"],
                "gtja_error": gtja_err,
                "ppi_error": ppi_err,
                "ppi_broken": ppi_broken,
            }
    _cache_set(key, res)
    return res


def _fetch_basis_gtja(code: str, days: int, batch: bool = False) -> dict:
    """源 1: 国君 GTJA 基差(原实现, 未改口径)。"""
    from tradingagents.dataflows import gtja_api

    if not gtja_api.configured():
        return {"ok": False, "error": "未配置国君(GTJA)数据源密钥", "rows": []}
    end = datetime.now().date()
    start = (end - timedelta(days=days)).strftime("%Y-%m-%d")
    df = gtja_api.fetch_basis_df(code, start, end.strftime("%Y-%m-%d"),
                                 timeout=_GTJA_BATCH_TIMEOUT if batch else None)
    if df is None or df.empty:
        return {"ok": False, "error": "该品种无现货指数口径(国君基差数据源未覆盖)", "rows": []}
    rows = []
    for rec in df.to_dict("records"):
        d = rec.get("date")
        if isinstance(d, int):
            s = str(d)
            d = f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 else s
        rows.append({"date": str(d), "spot_price": _f(rec.get("spot_price")),
                     "futures_price": _f(rec.get("dominant_contract_price")),
                     "basis": _f(rec.get("dom_basis")), "basis_rate": _f(rec.get("dom_basis_rate")),
                     "contract": str(rec.get("dominant_contract") or "")})
    return {"ok": True, "rows": rows, "count": len(rows), "source": "gtja"}


def _fetch_basis_ppi(code: str, days: int) -> dict:
    """源 2: 生意社 100ppi 基差(兜底; 无该品种覆盖时返回 ok:False 不抛错)。"""
    try:
        import ppi_basis

        if not ppi_basis.supports(code):
            return {"ok": False, "error": "该品种无现货指数口径(国君与生意社均未覆盖)", "rows": []}
        rows = ppi_basis.basis_rows(code, days)
        if not rows:
            return {"ok": False, "error": "生意社暂无该品种现货序列", "rows": []}
        return {"ok": True, "rows": rows, "count": len(rows), "source": "100ppi"}
    except Exception as e:  # noqa: BLE001 - 兜底源失败不能影响主流程
        logger.warning("生意社基差兜底失败(%s): %s", code, e)
        return {"ok": False, "error": f"生意社兜底失败: {type(e).__name__}", "rows": []}


# ── 新浪命名别名表(2026-10-16) ────────────────────────────────────────────
# 【为什么需要】`ak.futures_zh_realtime(symbol=...)` 内部查的是**新浪的品种命名表**
#   (`ak.futures_symbol_mark()`), 该表的品种名与项目内 `VARIETY_METADATA.name`
#   **并不同源**: 表里用的是俗称/别名。实测 2026-10-16:
#       项目名            新浪表名
#       对二甲苯(PX)  →   二甲苯
#       燃料油(FU)    →   燃油
#       棕榈油(P)     →   棕榈
#       LPG(PG)       →   液化石油气
#   直接传项目名 ⇒ KeyError ⇒ 这些品种的**期限结构 / 虚实盘比 / 月差全为空**,
#   而在界面上表现为"无跨月合约报价"—— 看着像**数据源无覆盖**, 实为**取名失败**。
#   (这正是本项目"三态不可混"的原型: 确定无数据 vs 取数失败必须区分。)
# 【变量】顺序 = 候选优先级: 先项目名(多数品种命中), 再逐个试别名。
_SINA_NAME_ALIASES: dict[str, list[str]] = {
    "PX": ["对二甲苯", "二甲苯"],
    "FU": ["燃料油", "燃油"],
    "P": ["棕榈油", "棕榈"],
    "PG": ["LPG", "液化石油气"],
    "LU": ["低硫燃料油"],
}


def _term_realtime_df(ak, code: str, name: str):
    """逐个候选名试 `ak.futures_zh_realtime`, 返回 (df, 命中的名字, 试过的名字列表)。

    【★ 为什么要逐名重试而不是加一张"覆盖表"】别名表是**外部命名表的知识**, 会随
      数据源改版而失效; 用"逐个试直到成功"能把**新增**的命名差异自动吸收, 表只负责
      提高命中优先级。全部失败时把"试过哪些"如实带回 error(区分"无覆盖"与"取名失败")。
    """
    cands = list(_SINA_NAME_ALIASES.get(code, [])) or [name]
    if name not in cands:
        cands = [name] + cands
    tried: list[str] = []
    for cand in cands:
        if not cand:
            continue
        tried.append(cand)
        try:
            df = ak.futures_zh_realtime(symbol=cand)
        except Exception as e:  # noqa: BLE001 - 换下一个候选名继续试
            logger.debug("term 候选名 %s 失败(%s): %s", cand, code, e)
            continue
        if df is not None and not df.empty:
            return df, cand, tried
    return None, None, tried


def _fetch_term_structure(code: str) -> dict:
    """取跨月合约报价 → 期限结构曲线(akshare 全合约实时)。

    【返回】{ok, rows:[{contract, month, price, oi, volume}], shape(contango/backward/none),
            spread(近月-远月), error}。
    【口径】按合约月份升序;剔除"连续"(主连合成码,非真实合约);主力=持仓量最大。
    """
    key = f"term:{code}"
    hit = _cache_get(key, _TTL_TERM)
    if hit is not None:
        return hit
    import re

    import akshare as ak

    meta = _meta(code)
    name = meta.get("name") or code
    # 【2026-10-16 修】新浪命名表与项目名不同源 ⇒ 逐候选名重试(见 _SINA_NAME_ALIASES)。
    df, name_used, tried = _term_realtime_df(ak, code, name)
    if df is None:
        res = {"ok": False, "rows": [],
               "error": f"合约报价取名失败: 试过 {'/'.join(tried)} 均不可用"
                        f"(新浪命名表与项目名称不同源)"}
        _cache_set(key, res)
        return res
    if df is None or df.empty:
        res = {"ok": False, "error": "未取到合约报价", "rows": []}
        _cache_set(key, res)
        return res
    rows = []
    for rec in df.to_dict("records"):
        sym = str(rec.get("symbol") or "")
        m = re.match(r"^([A-Za-z]+)(\d{3,4})$", sym)
        if not m:  # 跳过 TA0/TA2610 之外的合成码
            continue
        rows.append({"contract": sym, "month": m.group(2),
                     "price": _f(rec.get("trade")), "oi": _f(rec.get("position")),
                     "volume": _f(rec.get("volume"))})
    rows = [r for r in rows if r["price"] is not None and r["oi"]]
    if len(rows) < 2:
        res = {"ok": False, "error": "可交易月份不足(不足 2 个)", "rows": rows}
        _cache_set(key, res)
        return res
    rows.sort(key=lambda r: r["month"])
    near, far = rows[0], rows[-1]
    shape = "contango(远月升水)" if far["price"] > near["price"] else (
        "backwardation(远月贴水)" if far["price"] < near["price"] else "flat(平水)")
    main = max(rows, key=lambda r: r["oi"] or 0)
    res = {"ok": True, "rows": rows, "shape": shape,
           "spread": round(far["price"] - near["price"], 2),
           "near": near, "far": far, "main": main}
    _cache_set(key, res)
    return res


@quant_bp.route("/api/quant/basis")
def api_quant_basis():
    """③ 基差与期限结构(基差走势 + 当前升贴水分位 + 跨月曲线)。

    【参数】variety(必填); days=基差回看天数(默认 120, 钳 20~400)。
    【返回】{ok, variety, name, unit, basis:{ok,rows,count,latest,rate,trend,pct_rank,state},
            term:{ok,rows,shape,spread,main}}。
    【关键】基差口径 = **现货 − 期货**（国君 basisValue 原样，实测四品种钉死）：
            >0 = 现货升水 / **期货贴水**（现货强于期货）；<0 = 现货贴水 / **期货升水**。
            分位按窗口内基差序列。⚠️ 表述一律带主体词（"期货贴水"），
            避免与"现货贴水"混淆——两者方向相反，是历史事故点（见 _quant_signals 注释）。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    days = max(20, min(request.args.get("days", 120, type=int) or 120, 400))
    meta = _meta(code)

    b = _fetch_basis(code, days)
    basis_summary: dict[str, Any] = {"ok": False, "rows": [], "count": 0}
    if b.get("ok") and b.get("rows"):
        rows = b["rows"]
        vals = [r["basis"] for r in rows if r["basis"] is not None]
        rates = [r["basis_rate"] for r in rows if r["basis_rate"] is not None]
        latest = vals[-1] if vals else None
        prev5 = vals[-6] if len(vals) >= 6 else (vals[0] if vals else None)
        trend = None
        if latest is not None and prev5 is not None:
            trend = "走强" if latest > prev5 else ("走弱" if latest < prev5 else "持平")
        rank = None
        if vals and latest is not None:
            rank = round(sum(1 for v in vals if v <= latest) / len(vals) * 100, 1)
        state = None
        if latest is not None:
            # 【方向口径】basis = 现货 − 期货 ⇒ >0 = 现货升水(期货贴水); <0 = 现货贴水(期货升水)。
            #   历史上此处曾按"升水/贴水"裸词输出，用户无法判断主体 ⇒ 现显式带主体词。
            state = "期货贴水(现货升水)" if latest > 0 else ("期货升水(现货贴水)" if latest < 0 else "平水")
        basis_summary = {"ok": True, "rows": rows, "count": len(rows), "latest": latest,
                         "rate": (rates[-1] if rates else None), "trend": trend,
                         "pct_rank": rank, "state": state,
                         "max": max(vals) if vals else None, "min": min(vals) if vals else None}
    else:
        basis_summary["error"] = b.get("error")

    term = _fetch_term_structure(code)
    return jsonify(_json_safe({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "unit": meta.get("unit", ""), "days": days,
        "basis": basis_summary, "term": term,
    }))


# ===========================================================================
# ④ 仓单库存 + 库存周期四象限
# ===========================================================================
def _fetch_inventory(code: str, days: int = 180, batch: bool = False) -> dict:
    """取交易所注册仓单序列(国君优先,四所官方作兜底)。

    【batch】True 时用短超时(见 _GTJA_BATCH_TIMEOUT),供截面排行等多品种场景。
    """
    from tradingagents.dataflows import gtja_api

    key = f"inv:{code}:{days}"
    hit = _cache_get(key, _TTL_INVENTORY)
    if hit is not None:
        return hit
    rows: list[dict] = []
    source = ""
    if gtja_api.configured():
        end = datetime.now().date()
        start = (end - timedelta(days=days)).strftime("%Y-%m-%d")
        df = gtja_api.fetch_inventory_df(code, start, end.strftime("%Y-%m-%d"),
                                        timeout=_GTJA_BATCH_TIMEOUT if batch else None)
        if df is not None and not df.empty:
            for rec in df.to_dict("records"):
                rows.append({"date": str(rec.get("date"))[:10], "qty": _f(rec.get("inventory")),
                             "change": _f(rec.get("change"))})
            source = "国泰君安(GTJA)"
    if not rows:  # 兜底: 项目内四所官方仓单模块
        try:
            from web_app import _WARRANT_KEY_MAP
            from warrant import history

            wh = _WARRANT_KEY_MAP.get(code)
            p = (history.get().get("products") or {}).get(wh) if wh else None
            qd = (p or {}).get("qty") or {}
            for d in sorted(qd.keys()):
                if qd[d] is not None:
                    rows.append({"date": str(d)[:10], "qty": _f(qd[d]), "change": None})
            if rows:
                prev = None
                for r in rows:  # 兜底源无 change 列, 自行补算
                    r["change"] = None if prev is None else round(r["qty"] - prev, 2)
                    prev = r["qty"]
                source = "四大交易所官方仓单"
        except Exception as e:  # noqa: BLE001
            logger.warning("仓单兜底源失败(%s): %s", code, e)
    res = ({"ok": True, "rows": rows, "count": len(rows), "source": source}
           if rows else {"ok": False, "error": "该品种无仓单数据(国君与四所官方源均未覆盖)", "rows": []})
    _cache_set(key, res)
    return res


_TTL_PROFIT = 21600  # 【变量】利润缓存 6h(日频数据; 数据层另有磁盘缓存, 这里是第二层)


def _fetch_profit(code: str) -> dict:
    """取品种加工利润/进口利润序列(国君 processProfitData, 2026-09-28 接入)。

    【返回】{ok, rows:[{date, label, profit}], labels:[...], latest:{label,date,value},
            pct:{label: 历史分位}, count, source, error?}
    【口径】`{品种小写}_{加工利润|进口利润}[_细分]`。能化品种常有多装置口径
      (如 EG 的"石脑油制"/"合成气制"), 全部返回, 由前端分色展示、不合并。
    【性能】数据层全量拉取一次(19 页/18s)后落盘缓存, 本函数再叠 6h TTL → 命中即毫秒级。
    """
    from tradingagents.dataflows import gtja_api

    key = f"profit:{code}"
    hit = _cache_get(key, _TTL_PROFIT)
    if hit is not None:
        return hit
    if not gtja_api.configured():
        res = {"ok": False, "error": "未配置国君(GTJA)数据源密钥", "rows": []}
        _cache_set(key, res)
        return res
    try:
        df = gtja_api.fetch_profit_df(code)
    except Exception as e:  # noqa: BLE001
        logger.warning("利润取数失败(%s): %s", code, e)
        df = None
    if df is None or df.empty:
        res = {"ok": False, "error": "该品种无加工/进口利润数据(平台未覆盖)", "rows": []}
        _cache_set(key, res)
        return res
    rows, labels, latest, pct = [], [], {}, {}
    for lb, sub in df.groupby("label"):
        s = sub.sort_values("date")
        labels.append(lb)
        for rec in s.to_dict("records"):
            rows.append({"date": str(rec.get("date"))[:10], "label": lb,
                         "profit": _f(rec.get("profit"))})
        vals = [v for v in s["profit"].tolist() if v is not None]
        if vals:
            cur = float(vals[-1])
            lo, hi = float(min(vals)), float(max(vals))
            latest[lb] = {"date": str(s.iloc[-1].get("date"))[:10], "value": round(cur, 2)}
            pct[lb] = round((cur - lo) / (hi - lo), 4) if hi > lo else None
    res = {"ok": True, "rows": rows, "labels": sorted(labels), "latest": latest,
           "pct": pct, "count": len(rows), "source": "国泰君安(GTJA) processProfitData"}
    _cache_set(key, res)
    return res


@quant_bp.route("/api/quant/profit")
def api_quant_profit():
    """⑩ 加工/进口利润(库存+基差+利润 三因子系统的第三维)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, unit, labels, latest, pct, rows, count, source}
      - labels: 口径名列表(能化多装置会有多个, 如 EG 的石脑油制/合成气制)
      - latest: {口径: {date, value}} 各口径最新值
      - pct:    {口径: 历史分位 0~1}(低利润→偏多; 高利润→偏空)
      - rows:   长表 [{date, label, profit}](前端按 label 分组画多条线)
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    meta = _meta(code)
    d = _fetch_profit(code)
    return jsonify(_json_safe({
        "variety": code, "name": meta.get("name", code), "unit": meta.get("unit", ""), **d,
    }))


# ===========================================================================
# 库存双口径(社会库存 / 交易所仓单)  —— 2026-10-16
# ===========================================================================
# 【为什么需要】"库存"不能只用交易所注册仓单表示: 仓单只是**交割库口径**(可交割量),
#   而**社会库存**是产业链口径。二者背离 = 库存向交割库转移(该数据集官方说明见
#   gtja_module._full_dataset_source 的注释)。
# 【★ 铁律一: 两口径量纲不可比 ⇒ 只在「分位/档位」层面综合, 绝不比较数值】
#   实测同品种可差数量级: EG 社会库存 13 vs 仓单 6226; TA 1385241 vs 1002。
#   平台不做单位归一(见 gtja_module 的 unit_note) ⇒ 一律各算各的**自身历史分位**。
# 【★ 铁律二: 两口径的分位窗口**故意不同**】各按自身可用历史:
#   · 仓单      : 近 `days` 天(默认 180, 日频 ≈119 点) —— 与 _fetch_inventory 既有口径一致
#   · 社会/工厂 : 该品种**全部历史**(周频为主, 实测 81~1428 点) —— 与"利润"维度同规矩
#   窗口不同不影响可比性: 分位本身就是"当前处自身历史何处"的归一化读数,
#   双口径比较的正是这个归一化位置。
_GJA_INV_PREF = ("社会库存", "工厂库存", "期货库存")


def _inv_pct(series: list, cur):
    """cur 在 series 中的百分位(0~1); 序列不足 5 点或 cur 缺失 → None。

    【与 fund_module._pct_rank 同口径】两份必须一致 —— 由
      tests/test_inventory_dual.py::test_pct_helpers_agree 逐值比对钉死。
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 5 or cur is None:
        return None
    return round(sum(1 for v in vals if v <= cur) / len(vals), 4)


def _inv_grade(rank, lo: float = 0.35, hi: float = 0.65) -> str:
    """0~1 分位 → 低/中/高 三档(分位缺失 → '不明')。

    【为什么要在这里再写一份】fund_module._grade 是同一口径, 但 fund 属**上层**
      (它 import quant), 反向 import 会成环。两份一致性同上由测试钉死。
    """
    if rank is None:
        return "不明"
    return "低" if rank <= lo else ("高" if rank >= hi else "中")


def _inv_dir(chg) -> str:
    """变化率 → 升/降/平(±0.5% 死区, 与 _inventory_cycle 的仓单口径一致)。"""
    if chg is None:
        return "不明"
    return "升" if chg > 0.5 else ("降" if chg < -0.5 else "平")


def _inv_chg(qty: list, back: int = 4):
    """近 `back` 期变化率(%); 0 值视为"无注册/已注销"占位**跳过**。

    【为什么要跳 0】与 _inventory_cycle._last_positive 同一理由(该项目已实测
      PTA 13435 → 0 → 1002 的集中注销)。此处只用于**方向**判断, 不参与分位。
    """
    pos = [v for v in qty if v is not None and v > 0]
    if len(pos) < back + 1 or not pos[-(back + 1)]:
        return None
    return round((pos[-1] / pos[-(back + 1)] - 1) * 100, 2)


def _fetch_gtja_inventory(code: str) -> dict:
    """取国君**自建库存数据集**里该品种的序列(社会库存 / 工厂库存 / 期货库存)。

    【★ 与 _fetch_inventory 的区别 —— 别混】
      `_fetch_inventory` = 交易所**注册仓单**(fut.warehouseStock, 单一口径)
      本函数             = 国君自建库存数据集(inventoryData.query, 三口径)
      两者**不同源、量纲不同**, 只可用于分位层面的交叉验证。
    【成本】底层 fetch_dataset 是**全量端点 + 进程内/磁盘双层缓存**(实测热命中
      0.02s / 冷 ≈18s) ⇒ 多品种循环调用几乎免费, 但**首次冷启动要等十几秒**。
    【返回】{ok, label, qty, dates, count, latest, latest_date, source, span,
            chg_pct, dir, error}
    """
    out = {"ok": False, "qty": [], "dates": [], "count": 0}
    try:
        from tradingagents.dataflows import gtja_api

        rows = gtja_api.fetch_dataset(gtja_api.EP_GTJA_INVENTORY,
                                      gtja_api._GTJA_INV_CACHE_DIR)
    except Exception as e:  # noqa: BLE001 - 库存缺失只降级该口径, 不拖垮整卡
        logger.warning("国君自建库存取数失败(%s): %s", code, e)
        return {**out, "error": "%s: %s" % (type(e).__name__, e)}
    want = (code or "").strip().lower()
    buckets = {}
    for r in rows or []:
        c, label = gtja_api._split_data_type(r.get("dataType") or "")
        if c != want:
            continue
        try:
            v = float(r.get("inventoryValue"))
        except (TypeError, ValueError):
            continue
        buckets.setdefault(label, []).append((str(r.get("dataDate") or "")[:10], v))
    if not buckets:
        return {**out, "error": "该品种无国君自建库存数据(社会/工厂/期货口径均无)"}
    # 口径优先级: 社会 > 工厂 > 期货(实测每品种只一种, 仍按优先级取以防上游补口径)
    label = next((k for k in _GJA_INV_PREF if k in buckets), sorted(buckets)[0])
    ser = sorted(buckets[label])
    qty = [v for _, v in ser]
    dates = [d for d, _ in ser]
    chg = _inv_chg(qty, 4)
    return {"ok": True, "label": label, "qty": qty, "dates": dates, "count": len(qty),
            "latest": qty[-1], "latest_date": dates[-1],
            "source": "国君自建库存数据集(%s)" % label,
            "span": "%s~%s" % (dates[0], dates[-1]), "labels": sorted(buckets),
            "chg_pct": chg, "dir": _inv_dir(chg)}


def _inv_social_block(code: str) -> dict:
    """国君社会(工厂)库存 → {分位/档位/方向} 块(① 卡与 ④ 卡**共用**, 口径唯一)。"""
    s = _fetch_gtja_inventory(code)
    if not s.get("ok"):
        return {"ok": False, "error": s.get("error") or "无国君库存数据"}
    rank = _inv_pct(s["qty"], s["latest"])
    return {"ok": True, "label": s["label"], "latest": s["latest"],
            "latest_date": s["latest_date"], "pct_rank": rank, "grade": _inv_grade(rank),
            "count": s["count"], "source": s["source"], "span": s["span"],
            "chg_pct": s.get("chg_pct"), "dir": s.get("dir")}


def _inv_ck_zero_warn(rows: list) -> dict:
    """仓单末位为 0 时的**如实标注**(用户 2026-10-14 拍板: 保留原值 + 加警示标签)。

    【★ 为什么保留 0 而不剔除】实测 0 是部分品种的**常态**, 不是数据源故障:
      BZ 历史 61.3% / EB 50.4% / PX 46.2% / LU 43.7% 的读数为 0(品种特性: 可交割
      货源少, 长期"无注册仓单"); 也可能是一次**集中注销**(_inventory_cycle 注释
      记有 PTA 13435→0→1002 的实测) ⇒ 一律判"源失效"会错杀真实读数。
    【但必须标注】0 直接进分位会把"无注册仓单"读成"库存极低" —— 实测 CJ 的 0 →
      分位 0.042 → 判"低" ⇒ 三指标系统输出**偏多**(凭空的看多信号)。故保留原值的
      同时给出: 连续 0 天数 / 历史 0 占比 / **上次非零值与日期**, 供交叉判断。
    【返回】{warn, zero_streak, zero_pct, last_nonzero, last_nonzero_date}
    """
    res = {"warn": None, "zero_streak": 0, "zero_pct": 0.0,
           "last_nonzero": None, "last_nonzero_date": None}
    vals = [r.get("qty") for r in rows if r.get("qty") is not None]
    if not vals or vals[-1] != 0:
        return res
    streak = 0
    for v in reversed(vals):
        if v == 0:
            streak += 1
        else:
            break
    res["zero_streak"] = streak
    res["zero_pct"] = round(sum(1 for v in vals if v == 0) / len(vals) * 100, 1)
    nz = next(((r.get("qty"), r.get("date")) for r in reversed(rows) if r.get("qty")), None)
    if nz:
        res["last_nonzero"], res["last_nonzero_date"] = nz[0], nz[1]
    seg = ("⚠ 仓单读数为 0(已连续 %d 个交易日; 该品种历史 %s%% 时间为 0)。"
           "0 可能表示无注册仓单或集中注销, 不必然等于库存极低 —— 请结合社会库存判断"
           % (streak, res["zero_pct"]))
    if res["last_nonzero"] is not None:
        seg += "; 上次非零 %s @ %s" % (format(res["last_nonzero"], ",.0f"),
                                       res["last_nonzero_date"])
    res["warn"] = seg
    return res


def _inv_ck_block(rows: list, days: int = 180, source: str = "") -> dict:
    """交易所注册仓单 → 分位/档位 块 + 0 值标注(口径与 ④ 卡/截面排行同源)。"""
    qty = [r.get("qty") for r in rows if r.get("qty") is not None]
    if not qty:
        return {"ok": False, "error": "无仓单数据", "source": source,
                "days": days, **_inv_ck_zero_warn([])}
    cur = qty[-1]
    rank = _inv_pct(qty, cur)
    chg = _inv_chg(qty, 1)
    return {"ok": True, "latest": cur, "latest_date": (rows[-1].get("date") if rows else None),
            "pct_rank": rank, "grade": _inv_grade(rank), "count": len(qty),
            "days": days, "source": source, "chg_pct": chg, "dir": _inv_dir(chg),
            **_inv_ck_zero_warn(rows)}


def _inv_dual(code: str, ck_rows: list, days: int = 180, ck_source: str = "") -> dict:
    """库存**双口径综合** → 单一档位 + 口径关系(用户 2026-10-14 拍板)。

    【规则】
      · 两侧同档       → 采用该档,     mode='双口径共振'
      · 一低一高(反向) → grade='矛盾', mode='口径背离(库存转移迹象)'
                         ★ 矛盾**不进单边判定**: fund_module._strategy_verdict 只有
                         low_i/high_i 两票才触发单边 ⇒ 矛盾两票都不算, 自然落空。
      · 一侧为"中"     → grade='中',   mode='口径分歧'(一强一中性, 取中不下注)
      · 只有一侧有数据 → 用该侧档位,   mode='仅社会库存口径' / '仅仓单口径'
      · 两侧都无       → grade='不明', mode='无口径'
    【返回】{grade, mode, note, ck:{...}, social:{...}, warn, available}
    """
    ck = _inv_ck_block(ck_rows, days, ck_source)
    soc = _inv_social_block(code)
    ck_g = ck.get("grade", "不明") if ck.get("ok") else "不明"
    so_g = soc.get("grade", "不明") if soc.get("ok") else "不明"
    out = {"ck": ck, "social": soc, "warn": ck.get("warn"),
           "available": [n for n, g in (("社会库存", so_g), ("仓单", ck_g)) if g != "不明"]}
    if ck_g == "不明" and so_g == "不明":
        return dict(out, grade="不明", mode="无口径",
                    note="社会库存与仓单两个口径均无可用数据, 库存维度不参与判定")
    if ck_g == "不明":
        return dict(out, grade=so_g, mode="仅社会库存口径",
                    note="该品种无可用交易所注册仓单, 库存档位只由社会库存(%s)给出" % so_g)
    if so_g == "不明":
        return dict(out, grade=ck_g, mode="仅仓单口径",
                    note="该品种无国君社会库存数据, 库存档位只由仓单(%s)给出" % ck_g)
    if ck_g == so_g:
        return dict(out, grade=ck_g, mode="双口径共振",
                    note="社会库存与仓单同处 %s 位, 双口径互证" % ck_g)
    if set([ck_g, so_g]) == set(["低", "高"]):
        return dict(out, grade="矛盾", mode="口径背离(库存转移迹象)",
                    note="社会库存%s而仓单%s → 库存可能在向交割库转移, 单一口径不足以定义方向, 不做单边"
                         % (so_g, ck_g))
    return dict(out, grade="中", mode="口径分歧",
                note="社会库存%s / 仓单%s, 一方居中性 → 综合取中, 无明确方向" % (so_g, ck_g))


def _inv_cross(ck_dir: str, social: dict) -> dict:
    """仓单方向 × 社会库存方向 的交叉验证(供「库存周期四象限」卡)。

    【为什么不合并成一个方向】两者**频率不同**(仓单日频 / 社会库存周频), 合并会
      掩盖"库存向交割库转移"这一关键背离信号 ⇒ 只做一致性判定, 不改四象限本身
      (四象限仍以仓单方向 × 价格方向定相位, 该口径未变)。
    【返回】{mode, note}
    """
    s_dir = social.get("dir") if social.get("ok") else None
    if not s_dir or s_dir == "不明":
        return {"mode": "仅仓单口径", "note": "无社会库存方向可比(该品种无国君库存或期数不足)"}
    if not ck_dir or ck_dir == "不明":
        return {"mode": "仅社会库存口径", "note": "仓单方向不可算"}
    if ck_dir == s_dir:
        return {"mode": "双口径共振", "note": "仓单与社会库存同向(%s), 库存方向可信度更高" % ck_dir}
    if "平" in (ck_dir, s_dir):
        return {"mode": "口径分歧", "note": "仓单%s / 社会库存%s(一方持平), 方向待确认" % (ck_dir, s_dir)}
    return {"mode": "口径背离(库存转移迹象)",
            "note": "仓单%s而社会库存%s → 库存可能在向交割库转移, 勿单看仓单" % (ck_dir, s_dir)}


def _inventory_cycle(code: str, inv_rows: list[dict], price_bars: list[dict]) -> dict:
    """库存周期四象限(qihuo-duokong-logic 技能方法论)。
    【方法】以"库存方向(仓单近 5 日变化)"×"价格方向(20 日涨跌)"划四象限:
      涨+降 → 被动去库(偏多起点) / 涨+升 → 主动建库(偏多主升)
      跌+升 → 被动建库(偏空起点) / 跌+降 → 主动去库(偏空主跌)
    【返回】{ok, inv_chg5_pct, inv_dir, price_dir, phase, note}。
    """
    qty = [r["qty"] for r in inv_rows if r.get("qty") is not None]
    if len(qty) < 3 or len(price_bars) < 20:
        # 仓单不足时仍把社会库存抛出去: 该品种的社会库存可能是唯一可用口径
        return {"ok": False, "error": "仓单或价格序列不足",
                "social": _inv_social_block(code)}

    def _last_positive(seq: list, skip: int = 0) -> float | None:
        """从末位往前取第 skip+1 个 >0 的值(0 视为"已注销"占位, 跳过)。

        【为什么不能用 qty[-1]/qty[-4]】仓单可在近期集中注销归零后重新注册
        (实测 PTA 2026-09 走完 13435 → 0 → 1002); 旧写法 `if prev else 0.0`
        在 prev==0 时把"从 0 暴增"误判成"持平", 四象限整格失效。
        """
        n = 0
        for i in range(len(seq) - 1, -1, -1):
            if seq[i] and seq[i] > 0:
                if n == skip:
                    return float(seq[i])
                n += 1
        return None

    now = _last_positive(qty)
    if now is None:  # 窗口内仓单全为 0(已全部注销)
        # 同上: 仓单全 0 时社会库存往往是唯一能说明"库存到底低不低"的口径
        return {"ok": False, "error": "该品种仓单近期已全部注销(序列为 0), 无法判定库存周期",
                "social": _inv_social_block(code)}
    prev = _last_positive(qty, 1)
    if prev:
        chg = (now / prev - 1) * 100
        inv_dir = "升" if chg > 0.5 else ("降" if chg < -0.5 else "平")
    else:  # 前值不存在或为 0: 百分比无定义(分母为 0), 但"从无到有"方向明确
        chg, inv_dir = None, "升"
    p_now = float(price_bars[-1]["close"])
    p_20 = float(price_bars[-21]["close"])
    price_dir = "涨" if p_now > p_20 else ("跌" if p_now < p_20 else "平")
    phase = {
        ("涨", "降"): ("被动去库", "需求回暖而供给未跟上, 偏多起点"),
        ("涨", "升"): ("主动建库", "价格上行伴随补库, 偏多主升段"),
        ("跌", "升"): ("被动建库", "需求走弱而库存被动累积, 偏空起点"),
        ("跌", "降"): ("主动去库", "价格下行伴随主动减产去库, 偏空主跌段"),
        ("涨", "平"): ("库存持平/价格上行", "观察能否转主动建库"),
        ("跌", "平"): ("库存持平/价格下行", "观察能否转主动去库"),
        ("平", "升"): ("价格持平/库存累积", "方向待确认"),
        ("平", "降"): ("价格持平/库存去化", "方向待确认"),
    }.get((price_dir, inv_dir), ("数据不足", ""))
    sc = _inv_social_block(code)
    return {"ok": True,
            # chg 可为 None(前值为 0, 百分比无定义) → 前端必须按"无定义"展示而非当 0
            "inv_chg5_pct": (round(chg, 2) if chg is not None else None),
            "inv_dir": inv_dir,
            "inv_latest": now, "price_dir": price_dir, "phase": phase[0], "note": phase[1],
            "inv_pct_rank": round(sum(1 for v in qty if v <= now) / len(qty) * 100, 1),
            # ── 2026-10-16 双口径: 社会库存方向 + 与仓单方向的交叉验证 ──
            # 【为什么只做交叉验证、不动四象限】两者**频率不同**(仓单日频 / 社会库存
            #   周频), 合并成一个方向会掩盖"库存向交割库转移"这一关键背离信号。
            #   四象限仍以"仓单方向 × 价格方向"定相位 —— 该口径未变。
            "social": sc, "cross": _inv_cross(inv_dir, sc)}


@quant_bp.route("/api/quant/inventory")
def api_quant_inventory():
    """④ 仓单库存(序列 + 库存周期四象限)。

    【参数】variety(必填); days=回看天数(默认 180, 钳 30~400)。
    【返回】{ok, variety, name, unit, inventory:{ok,rows,count,source,latest,chg,chg_pct,
            pct_rank}, cycle:{ok,phase,...}}。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    days = max(30, min(request.args.get("days", 180, type=int) or 180, 400))
    meta = _meta(code)
    inv = _fetch_inventory(code, days)
    summary: dict[str, Any] = {"ok": False, "rows": [], "count": 0}
    cycle: dict[str, Any] = {"ok": False, "error": "无仓单数据"}
    if inv.get("ok") and inv.get("rows"):
        rows = inv["rows"]
        qty = [r["qty"] for r in rows if r.get("qty") is not None]
        latest = qty[-1] if qty else None
        chg = rows[-1].get("change")
        summary = {"ok": True, "rows": rows, "count": len(rows), "source": inv.get("source", ""),
                   "latest": latest, "chg": chg,
                   "chg_pct": (round(chg / (latest - chg) * 100, 2)
                               if (chg is not None and latest is not None and latest != chg) else None),
                   "pct_rank": (round(sum(1 for v in qty if v <= latest) / len(qty) * 100, 1)
                                if qty and latest is not None else None),
                   "max": max(qty) if qty else None, "min": min(qty) if qty else None}
        cycle = _inventory_cycle(code, rows, _load_daily_bars(code, 80))
    else:
        summary["error"] = inv.get("error")
    # ── 2026-10-16 双口径(社会库存 + 交易所仓单) ──
    # 【★ 原字段 inventory / cycle **一个不改**】既有护栏与前端按原字段断言;
    #   双口径信息走**增量字段** social / composite, 老消费端零影响。
    _ck_rows = (inv.get("rows") or []) if inv.get("ok") else []
    _dual = _inv_dual(code, _ck_rows, days, inv.get("source", "") if inv.get("ok") else "")
    return jsonify(_json_safe({"ok": True, "variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""), "days": days,
                               "inventory": summary, "cycle": cycle,
                               "social": _dual.get("social") or {},
                               "composite": {_k: _dual.get(_k) for _k in
                                             ("grade", "mode", "note", "warn", "available")}}))


# ===========================================================================
# ⑤ 量化信号(库存+基差 / 反转三信号 / 综合研判)
# ===========================================================================
def _quant_signals(code: str) -> dict:
    """综合量化信号(方法论: qihuo-duokong-logic 技能)。

    【三条计分信号】
      1) 库存+基差组合: 低库存+深贴水→偏多; 高库存+高升水→偏空; 指标矛盾→勿单边追(考虑跨期)
      2) 反转三信号:   价格 120 日高低位 + 基差修复 + 持仓量大幅下降
      3) 期限结构:     backwardation(近强远弱)一般反映现货偏紧, contango 反之
    【一条校验维度(不计分)】—— 2026-09-29 补齐
      4) 利润:         "库存+基差+利润"三指标系统的第三维: 低利润→亏损不可持续→偏多,
                       高利润→刺激供给增量→偏空。**慢变量, 只作交叉校验, 不计入 score/bias**
                       (理由见下方 ④ 段注释; 平台覆盖 15/21 品种, 无数据时降级不静默)。
    【返回】{ok, signals:[{name, level, desc, weight}], score, bias, components:{...},
            profit:{ok, label, cur, low, high, pct, count, labels, scored}}。
            ⚠️ signals 内 weight==0 的条目为校验维度, 不参与 score 累计。
    """
    bars = _load_daily_bars(code, 250)
    inv = _fetch_inventory(code, 180)
    basis = _fetch_basis(code, 60)
    term = _fetch_term_structure(code)
    signals: list[dict] = []
    score = 0

    inv_qty = [r["qty"] for r in (inv.get("rows") or []) if r.get("qty") is not None]
    basis_vals = [r["basis"] for r in (basis.get("rows") or []) if r.get("basis") is not None]
    cur_b = basis_vals[-1] if basis_vals else None

    # ① 库存 + 基差
    # 【⚠️ 符号口径 2026-09-29 修正】`dom_basis` = **现货 − 期货**(国君 basisValue 原样)。
    #   实测四品种钉死: RU spot=18550/fut=19010 → basis −460(升水);
    #   CF spot=17252/fut=15780 → basis +1472(贴水); TA spot=7350/fut=6350 → +1000(贴水);
    #   M spot=3350/fut=3359 → −9(升水)。四例均满足 basis == spot − futures。
    #   ⇒ **basis > 0 = 期货贴水**(现货强于期货) / **basis < 0 = 期货升水**。
    #   修正前此处比较符写反(把 cur_b<0 当贴水), 导致"低库存+贴水→偏多"实际触发在
    #   "低库存+升水"上, 方向全反; 现按实测口径纠正, 四个分支同时改, 勿只改其一。
    if inv_qty and len(inv_qty) >= 10 and cur_b is not None:
        q_lo, q_hi = sorted(inv_qty)[int(len(inv_qty) * 0.3)], sorted(inv_qty)[int(len(inv_qty) * 0.7)]
        now = inv_qty[-1]
        low_inv, high_inv = now <= q_lo, now >= q_hi
        if low_inv and cur_b > 0:
            signals.append({"name": "库存+基差", "level": "偏多",
                            "desc": "低库存 + 期货贴水 → 现货偏紧, 期货有向上修复空间"})
            score += 2
        elif high_inv and cur_b < 0:
            signals.append({"name": "库存+基差", "level": "偏空",
                            "desc": "高库存 + 期货升水 → 现货宽松, 期货有向下修复压力"})
            score -= 2
        elif low_inv and cur_b < 0:
            signals.append({"name": "库存+基差", "level": "矛盾",
                            "desc": "低库存但期货升水(指标背离) → 不建议单边追, 可考虑跨期/月差"})
        elif high_inv and cur_b > 0:
            signals.append({"name": "库存+基差", "level": "矛盾",
                            "desc": "高库存但期货贴水(指标背离) → 不建议单边追"})
        else:
            signals.append({"name": "库存+基差", "level": "中性",
                            "desc": "库存与基差均处中位, 无明确方向性组合信号"})
        comp = {"inv_latest": now, "inv_pct_rank": round(sum(1 for v in inv_qty if v <= now) / len(inv_qty) * 100, 1),
                "basis_latest": cur_b, "basis_sign": "现货-期货(>0 贴水 / <0 升水)",
                "inv_q30": q_lo, "inv_q70": q_hi}
    else:
        signals.append({"name": "库存+基差", "level": "降级",
                        "desc": "库存或基差数据缺失(该品种无现货指数或无仓单), 该项不参与研判"})
        comp = {}

    # ② 反转三信号
    if len(bars) >= 30:
        closes = [b["close"] for b in bars]
        lo, hi = min(closes), max(closes)
        pos = (closes[-1] - lo) / (hi - lo + 1e-9)
        warns = []
        if pos > 0.8:
            warns.append(f"价格处 {len(bars)} 日高位({pos*100:.0f}% 分位)")
        elif pos < 0.2:
            warns.append(f"价格处 {len(bars)} 日低位({pos*100:.0f}% 分位)")
        ois = [b.get("open_interest") for b in bars if b.get("open_interest")]
        if len(ois) >= 6 and ois[-6]:
            chg = (ois[-1] / ois[-6] - 1) * 100
            if chg < -10:
                warns.append(f"持仓量近 5 日大降 {chg:.0f}%(资金离场)")
            elif chg > 10:
                warns.append(f"持仓量近 5 日大增 {chg:+.0f}%(资金进场)")
        if warns:
            level = "预警" if pos > 0.8 or pos < 0.2 else "提示"
            desc = "; ".join(warns)
            if level == "预警":
                desc += " → 高低位叠加资金异动, 谨慎追单"
            signals.append({"name": "反转/资金信号", "level": level, "desc": desc})
            if level == "预警":
                score -= 1 if pos > 0.8 else 0
                score += 0 if pos > 0.8 else 1
        else:
            signals.append({"name": "反转/资金信号", "level": "正常",
                            "desc": "价格处区间中部且持仓量平稳, 无反转预警"})

    # ③ 期限结构
    if term.get("ok"):
        shape = term.get("shape") or ""
        if "backwardation" in shape:
            signals.append({"name": "期限结构", "level": "偏多",
                            "desc": f"近强远弱(back), 近月 {term['near']['price']} vs 远月 {term['far']['price']}, "
                                    f"价差 {term['spread']:+.0f} → 现货偏紧格局"})
            score += 1
        elif "contango" in shape:
            signals.append({"name": "期限结构", "level": "偏空",
                            "desc": f"近弱远强(contango), 近月 {term['near']['price']} vs 远月 {term['far']['price']}, "
                                    f"价差 {term['spread']:+.0f} → 现货宽松格局"})
            score -= 1
        else:
            signals.append({"name": "期限结构", "level": "中性", "desc": "跨月价差接近平水"})
    else:
        signals.append({"name": "期限结构", "level": "降级", "desc": term.get("error") or "无跨月报价"})

    # ①~③ 为**计分维度**(weight=1); ④ 利润为**校验维度**(weight=0, 不计入 score)。
    # 前端据此把两类信号分色/分区, 避免用户误以为顶部"信号计分"已含利润。
    for _s in signals:
        _s.setdefault("weight", 1)

    # ── ④ 利润维度(2026-09-29 补齐; 数据 2026-09-28 接入) ──
    # 【为什么补】qihuo-duokong-logic 的核心交易系统是"库存+基差+**利润**"三指标, 但本卡
    #   ①~③ 只落了"库存+基差"二维 + 反转/期限结构两个独立维度, 利润这第三维此前从未进过
    #   本函数(利润是 09-28 才接入平台的, 当时只补给了 quant_verdict 与 QA 两条链, 漏了这里)。
    # 【为什么不计分】利润是**慢变量**(方法论文档明确"只作方向票的补充/校验, 不单独定方向"):
    #   · 与 ① 的作用域重叠 —— 二者都是"基本面方向票", 都计分会让基本面一侧在总分的量纲上
    #     压倒技术面(① 的 ±2 已是分数档上限, 利润再叠加范围会到 ±4);
    #   · 本卡顶部"信号计分"是一个对外承诺的口径, 静默改它会连带改掉 bias 与排行;
    #   ⇒ 因此以 weight=0 作为**校验维度**呈现(与 quant_verdict.py:164-195 的"补充/校验"定位一致),
    #     真实计分口径不变, 用户能当场看到利润与三信号是否同向。
    # 【口径】与 quant_verdict / QA 量化块对齐: 历史分位 ≤0.3→偏多, ≥0.7→偏空, 中间→中性;
    #   多口径(能化多装置)取**样本最长**的一条为代表, 同时披露口径数, 不静默合并。
    profit_out: dict = {"ok": False, "scored": False}
    try:
        prof = _fetch_profit(code)
        if prof.get("ok"):
            by_label: dict[str, list[float]] = {}
            for r in (prof.get("rows") or []):
                v = r.get("profit")
                if v is not None:
                    by_label.setdefault(r["label"], []).append(float(v))
            by_label = {k: v for k, v in by_label.items() if len(v) >= 20}
            if by_label:
                main_label = max(by_label, key=lambda k: len(by_label[k]))
                vals = by_label[main_label]
                cur = float(prof.get("latest", {}).get(main_label, {}).get("value", vals[-1]))
                lo, hi = float(min(vals)), float(max(vals))
                span = hi - lo
                pct = ((cur - lo) / span) if span > 0 else None
                trend = ""
                if len(vals) >= 6:
                    prev = vals[-6]
                    trend = ", 近5期↑" if cur > prev * 1.02 else (", 近5期↓" if cur < prev * 0.98 else ", 近5期→")
                n_lbl = len(by_label)
                more = f"(另有 {n_lbl - 1} 个口径未列示)" if n_lbl > 1 else ""
                if pct is not None and pct <= 0.3:
                    p_level = "偏多(校验)"
                    p_desc = (f"{main_label} 现值 {cur:.0f} / 区间 [{lo:.0f}, {hi:.0f}]{trend}, "
                              f"处历史低位(分位 {pct*100:.0f}%) → 亏损不可持续, 供给端存收缩预期{more}")
                elif pct is not None and pct >= 0.7:
                    p_level = "偏空(校验)"
                    p_desc = (f"{main_label} 现值 {cur:.0f} / 区间 [{lo:.0f}, {hi:.0f}]{trend}, "
                              f"处历史高位(分位 {pct*100:.0f}%) → 高利润刺激供给增量{more}")
                else:
                    p_level = "中性"
                    _pct_txt = f"分位 {pct*100:.0f}%" if pct is not None else "区间无波动"
                    p_desc = (f"{main_label} 现值 {cur:.0f} / 区间 [{lo:.0f}, {hi:.0f}]{trend}, "
                              f"处历史中位({_pct_txt}) → 无方向指引{more}")
                signals.append({"name": "利润", "level": p_level, "desc": p_desc, "weight": 0})
                profit_out = {"ok": True, "scored": False, "label": main_label,
                              "cur": round(cur, 2), "low": round(lo, 2), "high": round(hi, 2),
                              "pct": (round(pct, 4) if pct is not None else None),
                              "count": len(vals), "labels": sorted(by_label.keys())}
            else:
                signals.append({"name": "利润", "level": "降级", "weight": 0,
                                "desc": "利润序列长度不足 20 期, 无法做历史分位(该项不参与研判)"})
                profit_out = {"ok": False, "scored": False, "error": "序列不足 20 期"}
        else:
            signals.append({"name": "利润", "level": "降级", "weight": 0,
                            "desc": f"{prof.get('error') or '该品种无加工/进口利润数据'}(该项不参与研判)"})
            profit_out = {"ok": False, "scored": False, "error": prof.get("error")}
    except Exception as e:  # noqa: BLE001 - 利润缺失只降级该维度, 不阻断整卡
        logger.warning("量化信号-利润维度失败(%s): %s", code, e)
        signals.append({"name": "利润", "level": "降级", "weight": 0,
                        "desc": f"利润取数异常({type(e).__name__}), 该项不参与研判"})
        profit_out = {"ok": False, "scored": False, "error": f"{type(e).__name__}: {e}"}

    bias = "偏多" if score >= 2 else ("偏空" if score <= -2 else "中性")
    return {"ok": True, "signals": signals, "score": score, "bias": bias,
            "components": comp, "inv_count": len(inv_qty), "basis_count": len(basis_vals),
            "bars": len(bars), "profit": profit_out}


@quant_bp.route("/api/quant/signals")
def api_quant_signals():
    """⑤ 量化信号(库存+基差组合 / 反转三信号 / 期限结构 → 综合研判)。

    【参数】variety(必填)。
    【返回】{ok, variety, name, signals:[{name,level,desc}], score, bias, components}。
    【口径】方法论来自用户级技能 qihuo-duokong-logic(仓颉蒸馏《期货多空逻辑》)。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    meta = _meta(code)
    sig = _quant_signals(code)
    return jsonify(_json_safe({"variety": code, "name": meta.get("name", code),
                               "unit": meta.get("unit", ""), **sig}))


# ===========================================================================
# ⑥ 策略回测
# ===========================================================================
# 策略登记表: 前端下拉与参数面板由此驱动(key → {label, fn, params})
_STRATEGIES: dict[str, dict[str, Any]] = {
    "ma_cross": {"label": "双均线交叉", "params": {"fast": 10, "slow": 30},
                 "hint": "快线上穿慢线做多 / 下穿做空"},
    "macd": {"label": "MACD", "params": {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9},
             "hint": "DIF-DEA 柱状上穿 0 做多 / 下穿做空"},
    "rsi": {"label": "RSI 超买超卖", "params": {"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30},
            "hint": "RSI 上穿超卖线做多 / 下穿超买线做空"},
    "bollinger": {"label": "布林带突破", "params": {"bb_period": 20, "num_std": 2.0},
                  "hint": "突破上轨做多 / 下轨做空, 回归中轨离场"},
    "donchian": {"label": "唐奇安通道", "params": {"period": 20},
                 "hint": "突破 N 日高做多 / 破 N 日低做空(海龟经典)"},
    "turtle": {"label": "海龟交易(含ATR止损)", "params": {"turtle_entry": 20, "turtle_exit": 10,
                                                          "atr_period": 14, "atr_mult": 2.0},
               "hint": "唐奇安突破 + ATR 倍数止损"},
    "atr": {"label": "ATR 通道(肯特纳)", "params": {"keltner_period": 20, "keltner_mult": 2.0},
            "hint": "收盘突破 mid±k·ATR 做多/做空"},
    "momentum": {"label": "动量策略", "params": {"lookback": 5, "hold": 3},
                 "hint": "过去 lookback 日上涨做多, 持有 hold 日"},
    "trailing": {"label": "移动止损", "params": {}, "hint": "趋势跟随 + 移动止损离场"},
    "sent_fixed": {"label": "情绪固定阈值", "params": {"horizon": 3, "threshold": 0.2},
                   "hint": "情绪得分突破阈值开仓, 持有 horizon 日"},
    "contrarian": {"label": "逆情绪", "params": {"horizon": 3, "trend_window": 5},
                   "hint": "情绪过热反向做空 / 过冷反向做多"},
    "adaptive_sent": {"label": "情绪自适应", "params": {"horizon": 3},
                      "hint": "按趋势强弱自适应决定顺势/逆向"},
}


def _run_strategy(code: str, strategy: str, params: dict, start: str, end: str) -> dict:
    """执行单个策略回测,返回统一结构(失败 → {ok:False,error})。"""
    import signal_analyzer as sa

    fns = {
        "ma_cross": (sa.run_ma_cross_strategy, ("fast", "slow")),
        "macd": (sa.run_macd_strategy, ("macd_fast", "macd_slow", "macd_signal")),
        "rsi": (sa.run_rsi_strategy, ("rsi_period", "rsi_overbought", "rsi_oversold")),
        "bollinger": (sa.run_bollinger_strategy, ("bb_period", "num_std")),
        "donchian": (sa.run_donchian_strategy, ("period",)),
        "turtle": (sa.run_turtle_strategy, ("turtle_entry", "turtle_exit", "atr_period", "atr_mult")),
        "atr": (sa.run_atr_strategy, ("keltner_period", "keltner_mult")),
        "momentum": (sa.run_momentum_strategy, ("lookback", "hold")),
        "trailing": (sa.run_trailing_strategy, ()),
        "sent_fixed": (sa.run_simulated_trading, ("horizon", "signal_threshold")),
        "contrarian": (sa.run_contrarian_sentiment, ("horizon", "trend_window")),
        "adaptive_sent": (sa.run_adaptive_sentiment, ("horizon",)),
    }
    if strategy not in fns:
        return {"ok": False, "error": f"未知策略: {strategy}"}
    fn, keys = fns[strategy]
    kw: dict[str, Any] = {"variety": code, "start_date": start, "end_date": end}
    for k in keys:
        if k in params and params[k] not in (None, ""):
            try:
                kw[k] = int(params[k]) if float(params[k]) == int(float(params[k])) else float(params[k])
            except (TypeError, ValueError):
                pass
    # sent_fixed 的参数名与函数签名不同(threshold → signal_threshold)
    if strategy == "sent_fixed":
        kw.pop("threshold", None)
        if "threshold" in params and params["threshold"] not in (None, ""):
            kw["signal_threshold"] = params["threshold"]
    try:
        res = fn(**kw) or {}
    except Exception as e:  # noqa: BLE001 - 单策略失败不影响其他策略
        logger.warning("回测失败(%s/%s): %s", code, strategy, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not res or res.get("total_trades") in (None, 0):
        return {"ok": True, "trades": 0, "note": "该参数/区间内无成交(尝试放宽区间或改参数)",
                "stats": res or {}}
    adv = res.get("advanced_metrics") or {}
    # 净值曲线: 由 recent_trades 逐笔累计盈亏重构(引擎未直接给 equity 序列)
    equity, cum = [], 0.0
    for t in res.get("recent_trades") or []:
        cum += float(t.get("pnl") or 0)
        equity.append({"date": str(t.get("exit"))[:10], "equity": round(cum, 4),
                       "pnl": _f(t.get("pnl")), "direction": t.get("direction"),
                       "entry": str(t.get("entry"))[:10], "exit": str(t.get("exit"))[:10],
                       "outcome": t.get("outcome")})
    return {"ok": True, "trades": res.get("total_trades"),
            "stats": {"win_rate": _f(res.get("win_rate")), "total_pnl": _f(res.get("total_pnl")),
                      "avg_pnl_pct": _f(res.get("avg_pnl_pct")), "sharpe": _f(res.get("sharpe_like")),
                      "max_drawdown_pct": _f(res.get("max_drawdown_pct")),
                      "long_trades": res.get("long_trades"), "short_trades": res.get("short_trades"),
                      "win_count": res.get("win_count"), "loss_count": res.get("loss_count"),
                      "advanced": {k: _f(v) for k, v in adv.items() if isinstance(v, (int, float))},
                      "params": {k: v for k, v in res.items() if k in keys}},
            "equity": equity, "recent_trades": res.get("recent_trades") or []}


@quant_bp.route("/api/quant/backtest/strategies")
def api_quant_backtest_strategies():
    """⑥-1 策略清单(前端下拉/参数面板驱动)。

    【返回】{ok, strategies:[{key, label, hint, params}]}。
    """
    return jsonify({"ok": True, "strategies": [
        {"key": k, "label": v["label"], "hint": v["hint"], "params": v["params"]}
        for k, v in _STRATEGIES.items()]})


@quant_bp.route("/api/quant/backtest", methods=["GET", "POST"])
def api_quant_backtest():
    """⑥-2 策略回测(单策略或多策略对比)。

    【参数】body/query: variety(必填); strategy=策略key(默认 ma_cross) 或
            strategies=逗号分隔多策略(与 strategy 二选一, 给 strategies 走对比);
            params={...}(单策略参数) 或 params_map={策略:{...}};
            start=开始日(默认 2025-01-01); end=结束日(默认今日)。
    【返回】{ok, variety, name, start, end, results:{策略key:{trades,stats,equity,...}}}。
    【关键】回测价格来自 signal_analyzer._load_price(思路2 trends, 21/21 覆盖);
    数据缺失品种返回 ok:False + 说明, 前端展示空态而非报错。
    """
    data = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    code = _norm_code(data.get("variety") or request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400
    start = (data.get("start") or request.args.get("start") or "2025-01-01").strip()
    end = (data.get("end") or request.args.get("end") or datetime.now().strftime("%Y-%m-%d")).strip()
    raw_multi = (data.get("strategies") or request.args.get("strategies") or "").strip()
    if raw_multi:
        keys = [k.strip() for k in raw_multi.split(",") if k.strip() in _STRATEGIES]
    else:
        one = (data.get("strategy") or request.args.get("strategy") or "ma_cross").strip()
        keys = [one] if one in _STRATEGIES else ["ma_cross"]

    # 参数合并: 单策略用 params, 多策略用 params_map(缺省用登记默认值)
    pmap_raw = data.get("params_map") or {}
    p_single = data.get("params") or {}
    results: dict[str, Any] = {}
    for k in keys:
        p = dict(_STRATEGIES[k]["params"])
        if len(keys) == 1:
            for kk, vv in (p_single or {}).items():
                if kk in p:
                    p[kk] = vv
        else:
            for kk, vv in ((pmap_raw.get(k) or {})).items():
                if kk in p:
                    p[kk] = vv
        results[k] = {"label": _STRATEGIES[k]["label"], **_run_strategy(code, k, p, start, end)}

    meta = _meta(code)
    # 【2026-10-12】透出回测价格来源: 实时主连(akshare 后复权) vs 思路2 离线 JSON。
    # 让前端一眼看出"数据是新鲜的还是旧管道残留的"。
    try:
        import signal_analyzer as _sa

        _price_src = (_sa._load_price(code) or {}).get("source", "思路2 trends(离线)")
    except Exception:  # noqa: BLE001
        _price_src = "未知"
    return jsonify(_json_safe({"ok": True, "variety": code, "name": meta.get("name", code),
                               "start": start, "end": end, "results": results,
                               "price_source": _price_src,
                               "note": "回测基于历史价格, 不含手续费/滑点, 仅供研究参考"}))


# ===========================================================================
# ⑦ 多品种量化截面排行
# ===========================================================================
def _cross_section_one(code: str) -> dict:
    """单品种截面指标(供排行;无数据的维度留 None,不参与对应排序)。

    【batch=True】内部各源走短超时(8s),单品种最坏 ~20s 而非 ~700s;
    配合 _rank_all 的有界并发, 全池 21 品种可在十几秒内完成。
    """
    row: dict[str, Any] = {"variety": code, "name": _meta(code).get("name", code)}
    try:
        snap = _technical_snapshot(code, 120)
        if snap.get("ok"):
            ind = snap["indicators"]
            row.update({"close": snap["latest"], "ma_state": snap.get("ma_state"),
                        "rsi14": ind.get("rsi14"), "macd_bar": ind.get("macd_bar"),
                        "score": snap.get("score"), "verdict": snap.get("verdict"),
                        "ann_vol_pct": ind.get("ann_vol_pct"), "mom20_pct": ind.get("mom20_pct"),
                        # ADXR 趋势性列(墨菲 cap-trading-systems): 供"品种池趋势性筛选"
                        "adxr": ind.get("adxr"), "adxr_level": ind.get("adxr_level"),
                        "adxr_tradable": ind.get("adxr_tradable"),
                        "atr14": ind.get("atr14"), "latest": snap.get("latest")})
    except Exception as e:  # noqa: BLE001
        logger.warning("截面-技术失败(%s): %s", code, e)
    try:
        b = _fetch_basis(code, 60, batch=True)
        if b.get("ok") and b.get("rows"):
            vals = [r["basis"] for r in b["rows"] if r["basis"] is not None]
            if vals:
                row["basis"] = vals[-1]
                row["basis_pct_rank"] = round(sum(1 for v in vals if v <= vals[-1]) / len(vals) * 100, 1)
    except Exception as e:  # noqa: BLE001
        logger.warning("截面-基差失败(%s): %s", code, e)
    try:
        inv = _fetch_inventory(code, 120, batch=True)
        if inv.get("ok") and inv.get("rows"):
            qty = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
            if qty:
                row["inventory"] = qty[-1]
                row["inventory_chg_pct"] = (round((qty[-1] / qty[-4] - 1) * 100, 2)
                                            if len(qty) >= 4 and qty[-4] else None)
                row["inventory_pct_rank"] = round(sum(1 for v in qty if v <= qty[-1]) / len(qty) * 100, 1)
    except Exception as e:  # noqa: BLE001
        logger.warning("截面-仓单失败(%s): %s", code, e)
    try:
        t = _fetch_term_structure(code)
        if t.get("ok"):
            row["term_shape"] = t.get("shape")
            row["term_spread"] = t.get("spread")
    except Exception as e:  # noqa: BLE001
        logger.warning("截面-期限失败(%s): %s", code, e)
    return row


def _rank_all(codes: list[str]) -> list[dict]:
    """有界并发拉取全池截面(21 品种 × 4 源), 整体结果缓存 5 min。

    【为什么并行】逐品种串行时, 单品种若命中 GTJA 超时会串成分钟级; 并行后
    墙钟 ≈ 最慢单品种而非累加。并发上限 _RANK_PARALLEL 避免打爆外部接口。
    【缓存】结果整体缓存 —— 排行是"全池同一张表", 切换排序维度(score/basis/…)
    无需重新拉数, 直接对缓存结果重排即可秒回。
    """
    key = "rank_all:" + ",".join(codes)
    hit = _cache_get(key, _TTL_RANK)
    if hit is not None:
        return hit
    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_RANK_PARALLEL) as ex:
        futs = {ex.submit(_cross_section_one, c): c for c in codes}
        for fut, c in futs.items():
            try:
                out[c] = fut.result()
            except Exception as e:  # noqa: BLE001 - 单品种失败留空行
                logger.warning("截面失败(%s): %s", c, e)
                out[c] = {"variety": c, "name": _meta(c).get("name", c)}
    rows = [out[c] for c in codes if c in out]
    _cache_set(key, rows)
    return rows


@quant_bp.route("/api/quant/ranking")
def api_quant_ranking():
    """⑦ 多品种量化截面排行。

    【参数】varieties=逗号分隔品种(默认全池 21 个); metric=排序维度
            (score/basis/basis_pct_rank/inventory_chg_pct/inventory_pct_rank/
             ann_vol_pct/mom20_pct/term_spread, 默认 score);
            order=desc/asc(默认 desc); refresh=1 强制跳过缓存重拉。
    【返回】{ok, metric, order, count, cached, rows:[{variety,name,...}]}。
    【性能】全池并行拉取(并发 6、GTJA 短超时 8s)+ 结果缓存 5 min;
    首次约十几秒, 之后切换排序维度秒回(直接对缓存重排)。
    """
    raw = (request.args.get("varieties") or "").strip()
    pool = _pool()
    codes = [c for c in (x.strip().upper() for x in raw.split(",")) if c] if raw else pool
    codes = [c for c in codes if c in pool] or pool
    metric = (request.args.get("metric") or "score").strip()
    order = (request.args.get("order") or "desc").strip().lower()

    cached = True
    if (request.args.get("refresh") or "").strip() in ("1", "true", "yes"):
        with _CACHE_LOCK:  # 【变量】显式失效, 下次调用真正重拉
            _CACHE.pop("rank_all:" + ",".join(codes), None)
        cached = False
    rows = _rank_all(codes)

    def sort_key(r):
        v = r.get(metric)
        ok = isinstance(v, (int, float))
        return (not ok, -(v if ok else 0) if order == "desc" else (v if ok else 0))

    rows = sorted(rows, key=sort_key)
    return jsonify(_json_safe({"ok": True, "metric": metric, "order": order,
                               "count": len(rows), "cached": cached, "rows": rows}))


# ===========================================================================
# ⑧ 多体系方法论研判中枢(方案 B) —— 固定读序 + 四段式输出 + 冲突并列
# ===========================================================================
@quant_bp.route("/api/quant/verdict")
def api_quant_verdict():
    """⑧ 多体系方法论研判(方向 → 入场 → 风控 → 成本 → 校验, 固定读序)。

    【方法论来源】用户级技能 `futures-quant-workflow`(5 本方法论书的合流编排)及其 5 个子技能;
    可计算的能力卡落成 `quant_methodology.py` 的纯函数, 本路由只做数据装配。

    【参数】variety(必填); account=演示账户资金(默认 1000000);
            risk_pct=风险档(默认 0.30, 新入市者可传 0.10); times=交易次数(默认 10);
            point_value=每点价值(默认 1.0; 真实价值须按合约乘数填, 否则仅作示意);
            round_trip_fee=往返手续费(不传则成本段降级为提示);
            tick=最小变动单位(项目元数据无此字段, 不传则跳过习惯数字避让);
            expected_sr=预期税前 SR(默认 0.35, 用于 Carver 速度上限)。
    【返回】{ok, verdict_text(四段式), stages:{direction,entry,risk,cost,verify},
            conflicts(冲突并列), evidence(卡片+缺口), data_gaps}。
    【纪律】本路由**不做**方向加权平均: 各体系方向票并列陈列, 由用户选定体系;
            双线同破时强制「暂不操作」; 鹿希武 N1/N2/N3 缺口强制披露。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)"}), 400

    args = request.args
    meta = _meta(code)
    bars = _load_daily_bars(code, 250)

    # 基差率 + 库存序列(供二维四象; 任一缺失 → 该卡降级, 不阻断整条链)
    basis_rates: list[float] = []
    b = _fetch_basis(code, 180)
    if b.get("ok"):
        basis_rates = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
    inv_series: list[float] = []
    inv = _fetch_inventory(code, 180)
    if inv.get("ok"):
        inv_series = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
    inv_chg = None
    pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]  # 【变量】跳过 0 占位
    if len(pos) >= 4 and pos[-4]:
        inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2)

    # 利润序列(2026-09-28 接入国君 processProfitData): 供"库存+基差+利润"三因子研判。
    # 多口径时取"样本最长"的那一条做代表(能化多装置口径多, 长的通常是主工艺)。
    profit_series: list[float] = []
    profit_label = ""
    profit_cur = profit_low = profit_high = None
    try:
        from tradingagents.dataflows import gtja_api

        pdf = gtja_api.fetch_profit_df(code)
        if pdf is not None and not pdf.empty:
            best = max(pdf.groupby("label"), key=lambda kv: len(kv[1]))
            profit_label = best[0]
            ser = best[1].sort_values("date")["profit"].tolist()
            profit_series = [float(v) for v in ser if v is not None]
            if profit_series:
                profit_cur = profit_series[-1]
                profit_low, profit_high = min(profit_series), max(profit_series)
    except Exception as e:  # noqa: BLE001 - 利润缺失只降级该维度
        logger.warning("方法论研判-利润取数失败(%s): %s", code, e)

    # 回测统计(供旁路校验: SR>2 警戒); 取双均线做代表
    strat_stats = {}

    # 期限结构(2026-09-30 修): 此前硬编码 "term": None, 导致 cap-term-structure
    #   这一票在**线上永远不触发**(进程内探针自造 payload 时看不出来)。
    #   取数失败不阻断——judgment 侧 term_structure_regime 会如实返回不可算。
    term = None
    try:
        _t = _fetch_term_structure(code)
        if _t.get("ok"):
            term = _t
    except Exception as e:  # noqa: BLE001 - 期限结构缺失只降级该票
        logger.warning("方法论研判-期限结构取数失败(%s): %s", code, e)

    # ── 第二批能力卡的入参(2026-09-30) ──
    # 【基差原始行】供 cap-basis-calculation 做「折盘面口径审计」: 拿平台自己的
    #   现货价/期货价/基差反推它隐含的折盘面系数, 而不是把"没做折盘面"这件事静默吞掉。
    basis_rows = (b.get("rows") or []) if b.get("ok") else []
    # 【分位】cap-calendar-spread 的①②③是"巨量/少量""高/低"这种**相对**判据,
    #   必须按自身历史分位, 不能用绝对阈值。平台的库存序列取自交易所**注册仓单**
    #   (见 _fetch_inventory), 故卡的①仓单与③库存在此塌缩为同一条 → 只传一个分位,
    #   避免同一序列投两票制造假共振(在扩展段显式说明)。
    inv_pct = None
    _pos = [float(x) for x in inv_series if isinstance(x, (int, float)) and float(x) > 0]
    if _pos:
        _cur = float(inv_series[-1]) if isinstance(inv_series[-1], (int, float)) else None
        if _cur is not None:
            inv_pct = round(sum(1 for x in _pos if x <= _cur) / len(_pos), 4)
    # 【样本年限】cap-shrinkage-ladder 需"可用样本年限"。平台默认只载入 250 根日线,
    #   故默认按本地 K 线长度估算(一年按 244 个交易日), 并在扩展段**显式标注来源**;
    #   调用方可传 data_years 覆盖(真实回测样本年限)。
    try:
        data_years = float(args.get("data_years")) if args.get("data_years") else round(len(bars) / 244.0, 2)
    except (TypeError, ValueError):
        data_years = round(len(bars) / 244.0, 2)
    # 【跨品种对腿】cap-cross-commodity-spread 需要第二条腿。对腿知识来自函数库的
    #   显式配对表(CROSS_PAIRS, 依据卡原文五逻辑分类), **不猜**。
    #   【2026-09-30 修】原先只试**第一个**对腿, 一旦它取数失败就对外宣称"无登记配对" ——
    #   那是把**取数失败**伪装成**知识缺失**, 属误导。现改为: ① 依次试全部登记对腿,
    #     取第一个成功的; ② 把每次尝试结果一并透出, 供研判层区分"没登记"与"登记了但取不到"。
    cross_leg = None
    pair_attempts: list[dict] = []
    _qm_cross_pair_of = None
    try:
        import quant_methodology as _qm  # 【调用模块】取跨品种配对表(显式登记, 不猜)

        _qm_cross_pair_of = _qm.cross_pair_of
        _pairs = _qm_cross_pair_of(code)
        for _p in _pairs:
            _leg_code = _p["leg"]
            try:
                _leg_term = _fetch_term_structure(_leg_code)
            except Exception as _e:  # noqa: BLE001 - 单个对腿失败继续试下一个
                pair_attempts.append({"leg": _leg_code, "ok": False, "error": f"{type(_e).__name__}: {_e}"})
                continue
            if _leg_term.get("ok"):
                _near, _far = _leg_term.get("near") or {}, _leg_term.get("far") or {}
                _nf, _ff = float(_near.get("price") or 0), float(_far.get("price") or 0)
                pair_attempts.append({"leg": _leg_code, "ok": True})
                if cross_leg is None:  # 取第一个成功的作为展示对腿(后续仅记录可用性)
                    cross_leg = {
                        "code": _leg_code, "relation": _p["relation"], "note": _p["note"],
                        "structure": "back" if _ff < _nf else "contango",
                        "price": _near.get("price"),
                    }
            else:
                pair_attempts.append({"leg": _leg_code, "ok": False,
                                      "error": _leg_term.get("error") or "取数失败"})
    except Exception as e:  # noqa: BLE001 - 跨品种腿缺失只降级该卡
        logger.warning("方法论研判-跨品种对腿取数失败(%s): %s", code, e)

    payload = {
        "variety": code, "name": meta.get("name", code), "unit": meta.get("unit", ""),
        "bars": bars, "basis_rate_series": basis_rates, "inv_series": inv_series,
        "inv_chg_pct": inv_chg, "term": term,
        "basis_rows": basis_rows, "inv_pct": inv_pct,
        "data_years": data_years, "cross_leg": cross_leg,
        "pairs": (_qm_cross_pair_of(code) if _qm_cross_pair_of else []),
        "pair_attempts": pair_attempts,
        "profit_series": profit_series, "profit_label": profit_label,
        "profit_cur": profit_cur, "profit_low": profit_low, "profit_high": profit_high,
        "account": args.get("account", type=float), "risk_pct": args.get("risk_pct", type=float),
        "times": args.get("times", type=int), "point_value": args.get("point_value", type=float),
        "round_trip_fee": args.get("round_trip_fee", type=float),
        "slip_ticks": args.get("slip_ticks", type=float), "tick": args.get("tick", type=float),
        "expected_sr": args.get("expected_sr", type=float),
        "strategy_stats": strat_stats,
    }
    try:
        from quant_verdict import build_verdict

        out = build_verdict(payload)
    except Exception as e:  # noqa: BLE001 - 研判失败只降级该卡, 不 500
        logger.exception("方法论研判失败(%s)", code)
        return jsonify(_json_safe({"ok": False, "error": f"研判组装异常: {type(e).__name__}: {e}"}))
    return jsonify(_json_safe({"variety": code, "name": meta.get("name", code), **out}))


@quant_bp.route("/api/quant/meta")
def api_quant_meta():
    """模块元信息(品种池 + 数据源覆盖说明, 供前端初始化与「数据边界」提示)。

    【返回】{ok, varieties:[{code,name,exchange,unit,sector}], sources:[{name,coverage,note}]}。
    【覆盖度口径】全部按当前池**动态派生**(2026-09-30 改): 池扩容后自动跟随,
      不再硬编码 "x/20" 这类会随扩池失真的字符串(同 fund_module 的既有约定)。
      基差一行的分母/分子取「两源合计覆盖」= 池 − 两源皆无集(ppi_basis.NO_SPOT_VARIETIES)。
    """
    items = []
    for c in _pool():
        m = _meta(c)
        items.append({"code": c, "name": m.get("name", c), "exchange": m.get("exchange_cn", ""),
                      "unit": m.get("unit", ""), "sector": m.get("sector_cn", "")})
    pool = _pool()
    n = len(pool)
    # 两源皆无现货指数的品种(国君返回 0 行 ∧ 生意社 100ppi 清单亦不含)
    try:
        import ppi_basis

        no_spot = [c for c in pool if c in set(getattr(ppi_basis, "NO_SPOT_VARIETIES", []) or [])]
        ppi_support = [c for c in pool if c in set(getattr(ppi_basis, "FALLBACK_ACTIVE", []) or [])]
    except Exception:  # noqa: BLE001 - 兜底源不可导入时退化为"仅国君"口径
        logger.warning("ppi_basis 不可用, 基差覆盖按仅国君口径标注", exc_info=True)
        no_spot, ppi_support = [], []
    n_spot = n - len(no_spot)
    return jsonify({
        "ok": True,
        "varieties": items,
        "strategies": len(_STRATEGIES),
        "sources": [
            {"name": "品种日线/分钟K", "coverage": f"{n}/{n}", "note": "akshare 主连 + 后复权(日K)"},
            {"name": "基差/现货价", "coverage": f"{n_spot}/{n}",
             "note": "国君 GTJA 为主" + (f", 生意社 100ppi 兜底(补 {'/'.join(ppi_support)})" if ppi_support else "")
                     + (f";{'/'.join(no_spot)} 两源皆无现货指数" if no_spot else "")},
            {"name": "交易所注册仓单", "coverage": f"{n}/{n}", "note": "国君 GTJA 优先, 四所官方兜底(BZ 偶发限流, 重试即通)"},
            {"name": "周度观点信号", "coverage": f"{n}/{n}", "note": "国君 GTJA 周度"},
            {"name": "跨月期限结构", "coverage": f"{n}/{n}", "note": "akshare 全合约实时报价"},
            {"name": "策略回测价格", "coverage": f"{n}/{n}", "note": "思路2 trends 价格序列(180 交易日)"},
        ],
    })


# ===========================================================================
# ⑫ 数据计算面板 —— 多维度曲线 + 相关系数矩阵(2026-09-30 新增)
# ===========================================================================
# 【需求】列出已有数据维度 → 多选 → 画曲线 / 算所选指标之间的相关系数
#   (用户原话举例: 价格 与 仓单变化 的相关性、画图)。模块挂在「量化分析」下。
# 【为什么单开一个接口而不是前端拼】相关系数需要**严格的日期对齐**; 前端各卡
#   各拉一条序列、日期集各不相同, 在浏览器里拼极易拼出"看起来对的"伪相关。
#   这里统一在后端对齐, 口径与 ③④ 同源, 前端只负责画。
# 【口径铁律(与 ③④ 完全一致, 不另起炉灶)】
#   · 基差 = **现货 − 期货**(国君 basisValue 原样; 生意社源已在 ppi_basis 取负统一)。
#   · 仓单 = 交易所注册仓单**原样单位**(郑商所=张 / 大商所·广期所=手 /
#     上期所·能源中心=吨或桶), **不折算手数** —— 折算系数依赖郑商所规格,
#     缺失时整条序列会消失, 对"算相关"是净损失; 且相关无量纲, 单位不统一不影响它。
#   · 对齐 = **日期内连接**(两列同日都有值才参与), 不做前向填充 —— 伪相关多来自填充。
_CMP_METHODS = ("pearson", "spearman")  # 【变量】支持的相关口径
_CMP_METHOD_NAMES = {"pearson": "Pearson 线性相关", "spearman": "Spearman 秩相关"}
_CMP_MIN_N = 3  # 【变量】低于 3 个共同交易日不出相关系数(2 点必然 ±1, 无信息量)

# 【变量】维度注册表 (key, 中文名, 单位, 家族, 原始/派生)
#   family 用于前端分组与"数据边界"提示; kind=派生 说明它由同家族原始维度算出来,
#   原始维度没数据时它必然也没有(前端如实提示, 不静默留白)。
_DIM_SPECS: list[tuple[str, str, str, str, str]] = [
    ("price",            "收盘价(后复权)", "元/吨",     "价格", "原始"),
    ("volume",           "成交量",         "手",        "价格", "原始"),
    ("oi",               "持仓量",         "手",        "价格", "原始"),
    ("spot",             "现货价(指数)",   "元/吨",     "基差", "原始"),
    ("futures",          "期货价(主连)",   "元/吨",     "基差", "原始"),
    ("basis",            "基差(现货−期货)", "元/吨",    "基差", "原始"),
    ("basis_rate",       "基差率",         "%",         "基差", "原始"),
    ("warrant",          "仓单数量",       "张/手/吨",  "仓单", "原始"),
    ("warrant_chg",      "仓单日变化",     "同上单位",  "仓单", "派生"),
    ("warrant_chg_pct",  "仓单日变化率",   "%",         "仓单", "派生"),
    ("basis_chg",        "基差日变化",     "元/吨",     "基差", "派生"),
    ("price_chg_pct",    "价格日收益率",   "%",         "价格", "派生"),
]
_DIM_KEYS = tuple(s[0] for s in _DIM_SPECS)  # 【变量】合法维度 key(顺序即展示顺序)
_DIM_DEFAULT = ("price", "warrant_chg")  # 【变量】默认选中(用户原话举例的那一对)
_TTL_COMPUTE = 900  # 【变量】维度序列缓存 15 min(与 ③④ 的日频 TTL 对齐)


def _cmp_series(code: str, days: int) -> dict[str, dict[str, float]]:
    """取全部维度 → {key: {date: value}}(只保留非 None 的点)。

    【复用】价格/成交量/持仓量走 `_load_daily_bars`(① 同源, 后复权 OHLCV + OI);
      基差族走 `_fetch_basis`(③ 同源, 国君优先 + 生意社兜底, 已统一"现货−期货");
      仓单族走 `_fetch_inventory`(④ 同源, 交易所口径原样)。
    【降级】每个数据源各自 try/except: 任一源失败只让它那几个维度为空, 其余照常,
      接口绝不 500 —— 与 ③④ 的"单块降级"设计一致。
    """
    out: dict[str, dict[str, float]] = {k: {} for k in _DIM_KEYS}

    # ── 价格族(一次取数派生 price / volume / oi) ──
    try:
        for b in _load_daily_bars(code, days):
            d = str(b.get("date") or "")[:10]
            if not d:
                continue
            for key, src in (("price", "close"), ("volume", "volume"), ("oi", "open_interest")):
                v = _f(b.get(src))
                if v is not None:
                    out[key][d] = v
    except Exception as e:  # noqa: BLE001 - 单源失败不拖垮整个面板
        logger.warning("compute 价格族失败(%s): %s", code, e)

    # ── 基差族(一次取数派生 spot / futures / basis / basis_rate) ──
    try:
        for r in (_fetch_basis(code, days).get("rows") or []):
            d = str(r.get("date") or "")[:10]
            if not d:
                continue
            for key, src in (("spot", "spot_price"), ("futures", "futures_price"),
                             ("basis", "basis"), ("basis_rate", "basis_rate")):
                v = _f(r.get(src))
                if v is not None:
                    out[key][d] = v
    except Exception as e:  # noqa: BLE001
        logger.warning("compute 基差族失败(%s): %s", code, e)

    # ── 仓单族(一次取数派生 warrant / warrant_chg / warrant_chg_pct) ──
    try:
        rows = sorted((_fetch_inventory(code, days).get("rows") or []),
                      key=lambda r: str(r.get("date") or ""))
        prev: float | None = None
        for r in rows:
            d = str(r.get("date") or "")[:10]
            q = _f(r.get("qty"))
            if not d or q is None:
                continue
            out["warrant"][d] = q
            chg = _f(r.get("change"))
            if chg is None and prev is not None:  # 兜底源(四所官方)无 change 列 → 自算
                chg = round(q - prev, 6)
            if chg is not None:
                out["warrant_chg"][d] = chg
                if prev:  # prev=0 时变化率无定义(与 ④ 四象限同口径: 归零不报百分比)
                    out["warrant_chg_pct"][d] = round(chg / abs(prev) * 100, 6)
            prev = q
    except Exception as e:  # noqa: BLE001
        logger.warning("compute 仓单族失败(%s): %s", code, e)

    # ── 二阶派生: 一阶差分量(先排日期, 再按前一有效点算) ──
    for src_key, dst_key in (("basis", "basis_chg"), ("price", "price_chg_pct")):
        src = out[src_key]
        dates = sorted(src)
        dst = out[dst_key]
        for i in range(1, len(dates)):
            prev_v, cur_v = src[dates[i - 1]], src[dates[i]]
            if dst_key == "basis_chg":
                dst[dates[i]] = round(cur_v - prev_v, 6)
            elif prev_v:  # 价格收益率的基准不得为 0
                dst[dates[i]] = round((cur_v - prev_v) / prev_v * 100, 6)
    return out


def _cmp_rank(seq: list[float]) -> list[float]:
    """秩变换(并列取平均秩), 供 Spearman 用。"""
    order = sorted(range(len(seq)), key=lambda i: seq[i])
    ranks = [0.0] * len(seq)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and seq[order[j + 1]] == seq[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 名次从 1 起, 并列取平均
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _cmp_corr(xs: list[float], ys: list[float], method: str = "pearson") -> float | None:
    """两**等长**序列的相关系数; 长度不足/方差为 0 → None。

    【为什么长度必须等长】本函数不做对齐 —— 对齐是调用方的责任(按日期内连接取
      共同日期后再取值), 这样"取了哪些日期"是单一事实来源, 不会在算相关时被改。
    """
    n = len(xs)
    if n < _CMP_MIN_N or len(ys) != n:
        return None
    if method == "spearman":
        xs, ys = _cmp_rank(xs), _cmp_rank(ys)
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx == 0 or vy == 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    return cov / (vx * vy) ** 0.5


def _cmp_vs_ratio(a: list[float], b: list[float], shift_b: bool = False) -> list[float | None]:
    """虚拟标准化比值 R = A'/B'(仅供"看得见谁在动", 不是相关系数的替代品)。

    【为什么平移】价格是数百~数万, 仓单日变化可为负 —— 直接相除会得到一个被
      "近零分母"放大的锯齿, 量级也毫无意义。Pearson 对线性变换不变, 故
      A' = A − min(A) + 0.1×range(A) 既不改变它与任何 Y 的相关性, 又让分母严格为正。
      shift_b=True 时对 B 也做同样的平移(两列一起看涨落方向时用)。
    """
    if not a or not b:
        return []
    am, aM = min(a), max(a)
    off_a = am - (0.1 * (aM - am) if aM > am else (abs(am) * 0.1 or 1.0))
    a2 = [v - off_a for v in a]
    if shift_b:
        bm, bM = min(b), max(b)
        off_b = bm - (0.1 * (bM - bm) if bM > bm else (abs(bm) * 0.1 or 1.0))
        b2 = [v - off_b for v in b]
    else:
        b2 = list(b)
    return [round(x / y, 6) if y else None for x, y in zip(a2, b2)]


@quant_bp.route("/api/quant/compute")
def api_quant_compute():
    """⑫ 数据计算面板: 列维度 → 多选 → 曲线 + 相关矩阵。

    【参数】variety(必填, 21 品种池内); days=回看天数(默认 180, 钳 30~365);
            dims=逗号分隔维度 key(默认 price,warrant_chg; 未知 key 静默忽略);
            method=pearson|spearman(默认 pearson);
            vs=虚拟比值对(形如 "price~warrant_chg", 可选)。
    【返回】{ok, variety, name, unit, days, method, method_name, dims:[...],
            selected:[key], series:{key:[[date,value],...]},
            corr:{keys, matrix, n_matrix, min_n, method, method_name, note},
            vs:{a,b,a_name,b_name,points,shifted,note}|None}
    【两个不同的 n 必须分清, 否则会误读】
      · 曲线: 每个维度画**它自己的完整序列**(并集日期轴), 不因别的维度缺数据而变短;
      · 相关: 每对维度取**共同有效交易日**(内连接), 故 n_matrix 逐对可能不同 ——
        界面必须显式给出每对的 n, 不能让用户以为所有格子都是同一个样本量。
    """
    code = _norm_code(request.args.get("variety"))
    if not code:
        return jsonify({"ok": False, "error": "缺少或非法品种参数(variety)需在 21 品种池内"}), 400
    days = max(30, min(request.args.get("days", 180, type=int) or 180, 365))
    method = (request.args.get("method") or "pearson").strip().lower()
    if method not in _CMP_METHODS:
        method = "pearson"

    cache_key = f"compute:{code}:{days}"
    series_map = _cache_get(cache_key, _TTL_COMPUTE)
    if series_map is None:
        series_map = _cmp_series(code, days)
        _cache_set(cache_key, series_map)

    # ── 维度清单(只列本品种**真有数据**的维度; 空维度不摆出来当陷阱) ──
    dims_payload = []
    for key, name, unit, family, kind in _DIM_SPECS:
        pts = series_map.get(key) or {}
        if not pts:
            continue
        dates = sorted(pts)
        dims_payload.append({
            "key": key, "name": name, "unit": unit, "family": family, "kind": kind,
            "count": len(dates), "first": dates[0], "last": dates[-1],
            "latest": pts[dates[-1]],
        })
    available = [d["key"] for d in dims_payload]

    # ── 选中维度(默认对按可用性回退, 保证一进面板就有东西可看) ──
    raw_sel = [k.strip() for k in (request.args.get("dims") or "").split(",") if k.strip()]
    sel = [k for k in raw_sel if k in available]
    if not sel:
        sel = [k for k in _DIM_DEFAULT if k in available] or available[:2]

    # ── 曲线序列: 并集日期轴, 每列各画各的(缺当日值 → null, 不填充) ──
    axis = sorted({d for k in sel for d in series_map.get(k, {})})
    series_out = {
        k: [[d, series_map[k].get(d)] for d in axis if d in series_map[k]]
        for k in sel
    }

    # ── 相关矩阵: 逐对**内连接**(共同有效交易日), 故 n 逐对给出 ──
    corr_keys = [k for k in sel if len(series_map.get(k) or {}) >= _CMP_MIN_N]
    matrix: list[list[float | None]] = []
    n_matrix: list[list[int]] = []
    for a in corr_keys:
        row_v: list[float | None] = []
        row_n: list[int] = []
        for b in corr_keys:
            common = sorted(set(series_map[a]) & set(series_map[b]))
            xs = [series_map[a][d] for d in common]
            ys = [series_map[b][d] for d in common]
            row_v.append(None if a == b else _cmp_corr(xs, ys, method))
            row_n.append(len(common))
        matrix.append(row_v)
        n_matrix.append(row_n)
    for i, row in enumerate(matrix):  # 对角线自相关恒为 1(样本量不足时才留 None)
        if n_matrix[i][i] >= _CMP_MIN_N and row[i] is None:
            row[i] = 1.0

    min_n = min((n_matrix[i][j] for i in range(len(n_matrix)) for j in range(len(n_matrix))
                 if i != j), default=0)

    # ── 可选: 虚拟比值对 "A~B" ──
    vs_out = None
    vs_arg = (request.args.get("vs") or "").strip()
    if "~" in vs_arg:
        va, _, vb = vs_arg.partition("~")
        va, vb = va.strip(), vb.strip()
        if va in available and vb in available:
            common = sorted(set(series_map[va]) & set(series_map[vb]))
            if common:
                ratios = _cmp_vs_ratio([series_map[va][d] for d in common],
                                       [series_map[vb][d] for d in common], shift_b=True)
                name_of = {k: n for k, n, *_ in _DIM_SPECS}
                vs_out = {
                    "a": va, "b": vb,
                    "a_name": name_of.get(va, va), "b_name": name_of.get(vb, vb),
                    "points": [[d, v] for d, v in zip(common, ratios)],
                    "shifted": True,
                    "note": "R = A'/B', A'/B' 已各自平移为严格正(A'=A−minA+0.1×极差); "
                            "Pearson 对线性变换不变, 故 R 与 B 的相关性等于原始 A 与 B 的相关性。",
                }

    meta = _meta(code)
    return jsonify(_json_safe({
        "ok": True, "variety": code, "name": meta.get("name", code),
        "unit": meta.get("unit", ""), "days": days,
        "method": method, "method_name": _CMP_METHOD_NAMES.get(method, method),
        "dims": dims_payload, "selected": sel, "axis": axis, "series": series_out,
        "corr": {"keys": corr_keys, "matrix": matrix, "n_matrix": n_matrix,
                 "min_n": min_n, "method": method,
                 "method_name": _CMP_METHOD_NAMES.get(method, method),
                 "note": "矩阵每格为该对维度**共同有效交易日**(内连接, 不做前向填充)上的相关系数; "
                         "对角为自相关 1。n 逐对不同, 已在每个格子里标注。"},
        "vs": vs_out,
    }))


# ===========================================================================
# ⑬ 全品种技术面总览(2026-10-14 新增) —— 「先看总览, 再点进单品种」
# ===========================================================================
# 【这个接口解决什么】原 /api/quant/ranking 是"按某一维度排序的截面表"; 用户要的是
#   **一眼看全部品种的技术面**: 每行同时给出 多周期方向 + 共振数 + 结论 + 置信度 +
#   流动性 + 关键信号, 并能点进单品种看细节。口径/形状都不同, 故独立成端点。
# 【取数铁律】全部复用既有函数, 不另起一套口径:
#   日线技术面 → _technical_snapshot(score/verdict/ma_state/ADX/持仓量)
#   K 线        → _daily_bars_cached / _load_minute_bars
#
# 【★ 为什么要"两阶段渐进式"】实测瓶颈在天勤**短连接**:
#   单品种 = 1 次日线 + 3 次分钟, 每次约 4.8s ⇒ 单品种 7.3s, 21 品种 **153s**;
#   而建连被 `_connect_lock` 串行化, 加大并发也没用(实测并发 6 时 0.72 次/秒)。
#   同步算完再返回 = 首屏等两个半小时 —— 不可接受。
#   ⇒ 拆成两阶段, 后台线程跑, 前端按 `ready/total/done` 渐进渲染:
#       阶段 A「日线层」: 每品种 1 次取数(21 次 ≈ 30s) ⇒ 整张表先有价/涨跌/日线方向/
#                        技术分/置信度/流动性/关键信号, **行立即可见**;
#       阶段 B「多周期」: 再补 60/15/5 三个周期的方向与共振数。
#   结果整体缓存 5 min(与 _TTL_RANK 同档), 期间重复访问秒回。

# 【周期口径】日线 / 60分 / 15分 / 5分(4 个)。**去掉 30分**: 它与 60分 区分度最低,
#   而每多一个周期就多 21 次短连接(约 +30s), 边际信息不值这个等待。
#   不放 3分/1分: 项目信号体系最短到 5 分钟, 更短的周期噪声大, 反而稀释"共振数"
#   的可解释性(共振应是"多个可交易周期是否一致", 而不是把秒级噪声也算一票)。
_OV_PERIODS: tuple[str, ...] = ("day", "60", "15", "5")
_OV_INTRADAY: tuple[str, ...] = ("60", "15", "5")   # 阶段 B 补的周期
_OV_PERIOD_CN: dict[str, str] = {"day": "日线", "60": "60分", "30": "30分",
                                 "15": "15分", "5": "5分", "3": "3分", "1": "1分"}
_OV_BARS = 120          # 【变量】每周期取多少根(够 EMA30 + ADX 稳定, 又不至于太慢)
_TTL_OVERVIEW = 900     # 【变量】总览整体缓存 15 min
# 【2026-10-14 用户要求】「每隔 15 分钟刷新一次并缓存」⇒ 与前端自动刷新周期对齐。
#   前端到点重取 ⇒ 此时缓存刚过期 ⇒ 后台重算(约 105s) ⇒ done=False ⇒ 前端回到 3s 渐进轮询。
_OV_ADX_FLAT = 20       # 【变量】ADX 低于此值判「无趋势/震荡」
_OV_RESONANCE_MIN = 3   # 【变量】≥N 个周期一致才给出方向性结论(4 周期 ⇒ 3/4 起)
_OV_STRENGTH_ADX_REF = 50  # 【变量】趋势强度的 ADX 参考值(见 _period_direction)

# 【粗板块归并】_meta 给的是细分子板块(「能化(化工)」), 总览要的是可筛选的粗板块。
#   映射按**精确表 + 前缀兜底**两级判定, 不用逐个子板块硬编码 —— 扩池新增
#   「能化(芳烃)」这类子板块时会自动落到「能化」, 不会漏。
_SECTOR_EXACT: dict[str, str] = {
    "能化(燃料)": "能源", "能化(能源)": "能源",
    "有色(新能源)": "新能源",
    "农产品(油脂)": "谷物油脂",
}


def _sector_bucket(sector_cn: str) -> str:
    """细分子板块 → 总览用的粗板块(全部品种 / 能源 / 新能源 / 农产品 …)。

    【判定顺序】先查精确表(处理"括号里的限定改变了归属"的情形, 如 能化(燃料)→能源),
    再按前缀兜底(能化(化工)→能化)。前缀兜底是为了扩池时不漏新子板块。
    """
    s = (sector_cn or "").strip()
    if not s:
        return "其他"
    if s in _SECTOR_EXACT:
        return _SECTOR_EXACT[s]
    for pre in ("能化", "有色", "黑色", "农产品", "贵金属", "金融"):
        if s.startswith(pre):
            return pre
    return s


def _period_direction(bars: list[dict]) -> dict:
    """单个周期的方向读数(**纯计算, 无任何 IO**)。

    【判据】三票制 + ADX 闸门 —— 与项目既有口径同源, 不引入新魔法:
      · 三票(各 ±1): ①收盘在 EMA30 上方 ②EMA10 在 EMA30 上方 ③EMA30 近 5 根斜率向上
      · **ADX < 20 直接判「震荡」** —— 墨菲 cap-trading-systems 的 ADXR<20
        「无趋势, 不适用趋势顺应系统」, 项目 `quant_methodology.compute_adx_adxr`
        已在用同一阈值; 这里保持一致, 避免"日线没有趋势却给出做多方向"。
    【趋势强度口径】`votes/3 * 99 * min(adx, 50)/50`:
      **同时体现"票数一致度"与"趋势质量"**。只用 votes/3*99 会让所有干净趋势恒为
      ±99(饱和、无信息量); 乘上 ADX 系数后, 同为 3 票时 ADX 25 → 49、ADX 50+ → 99,
      能区分强趋势与弱趋势。震荡一律 0, 便于前端只按方向渲染。
    """
    if not bars or len(bars) < 35:
        return {"dir": "flat", "label": "—", "strength": 0, "adx": None,
                "bars": len(bars or [])}
    closes = [_f(b.get("close")) for b in bars]
    closes = [c for c in closes if c is not None]
    if len(closes) < 35:
        return {"dir": "flat", "label": "—", "strength": 0, "adx": None,
                "bars": len(closes)}

    def _ema(vals: list[float], span: int) -> list[float]:
        k = 2.0 / (span + 1.0)
        out = [vals[0]]
        for v in vals[1:]:
            out.append(out[-1] + k * (v - out[-1]))
        return out

    e10, e30 = _ema(closes, 10), _ema(closes, 30)
    last = closes[-1]
    votes = 0
    votes += 1 if last > e30[-1] else -1
    votes += 1 if e10[-1] > e30[-1] else -1
    votes += 1 if e30[-1] > e30[-6] else -1

    adx = None
    try:
        import quant_methodology as _qm

        adx = _f((_qm.compute_adx_adxr(bars) or {}).get("adx"))
    except Exception as e:  # noqa: BLE001 - ADX 失败不影响方向票
        logger.warning("总览 ADX 计算失败: %s", e)

    nbars = len(closes)
    if adx is not None and adx < _OV_ADX_FLAT:
        return {"dir": "flat", "label": "震", "strength": 0, "adx": adx, "bars": nbars}
    if votes == 0:
        return {"dir": "flat", "label": "震", "strength": 0, "adx": adx, "bars": nbars}
    quality = min(adx or 0.0, _OV_STRENGTH_ADX_REF) / _OV_STRENGTH_ADX_REF
    st = int(round(votes / 3 * 99 * quality))
    return {"dir": "up" if votes > 0 else "down",
            "label": "多" if votes > 0 else "空",
            "strength": st, "adx": adx, "bars": nbars}


def _ov_key_signal(row: dict) -> str:
    """拼一句「关键信号」—— 用户在原设计里最看重的一列(一眼知道该干什么)。

    【为什么不是把指标名堆上去】原设计写的是可执行的话
    (如「趋势强度-12, 始终做空; 反弹至 EMA 优先做空」)。所以这里按
    **趋势强度 → 动作建议 → 均线形态 → 超买超卖** 的次序拼, 而不是罗列数值。
    """
    per = row.get("periods") or {}
    day = per.get("day") or {}
    d, adx = day.get("dir"), day.get("adx")
    st = day.get("strength") or 0
    seg: list[str] = []
    if d == "up":
        seg.append("日线趋势强度 +%d" % abs(st))
        seg.append("回调至 EMA20 优先做多" if (adx or 0) >= 25 else "偏多但 ADX 待走强")
    elif d == "down":
        seg.append("日线趋势强度 -%d" % abs(st))
        seg.append("反弹至 EMA20 优先做空" if (adx or 0) >= 25 else "偏空但 ADX 待走强")
    else:
        seg.append("日线无趋势(ADX %s)" % ("<20" if adx is not None else "缺"))
        seg.append("区间对待, 不追单边")
    ma = row.get("ma_state") or ""
    if ma in ("多头排列", "空头排列"):
        seg.append(ma)
    rsi = row.get("rsi14")
    if isinstance(rsi, (int, float)):
        if rsi >= 70:
            seg.append("RSI %.0f 超买" % rsi)
        elif rsi <= 30:
            seg.append("RSI %.0f 超卖" % rsi)
    return "; ".join(seg)


def _overview_base(code: str) -> dict:
    """**阶段 A：日线层**（每品种只 1 次取数）—— 行骨架 + 价/涨跌/技术分/日线方向。

    【为什么独立成函数】两阶段渐进的第一个阶段必须是"便宜的"：它决定整张表能不能
    在 ~30s 内先显示出来。分钟周期留到 `_overview_fill_periods`。
    """
    meta = _meta(code)
    sector_raw = meta.get("sector_cn") or ""
    row: dict[str, Any] = {
        "variety": code, "name": meta.get("name", code),
        "exchange": meta.get("exchange", ""),
        "sector": _sector_bucket(sector_raw), "sector_raw": sector_raw,
        "unit": meta.get("unit", ""), "main_contract": meta.get("main_contract", ""),
        # 【默认值必须在阶段 A 就给】流动性分档要等阶段 A 全跑完才能按全池算
        # (单品种看不到全池)。缺这个默认值时前端该列是 `undefined` ⇒ 显示空白。
        "liquidity": "—",
        "periods": {}, "periods_done": False,
    }
    try:
        snap = _technical_snapshot(code, _OV_BARS)
        if snap.get("ok"):
            ind = snap.get("indicators") or {}
            row.update({
                "latest": snap.get("latest"), "date": snap.get("date"),
                "score": snap.get("score"), "ma_state": snap.get("ma_state"),
                "rsi14": ind.get("rsi14"), "macd_bar": ind.get("macd_bar"),
                "ann_vol_pct": ind.get("ann_vol_pct"), "mom20_pct": ind.get("mom20_pct"),
                "adx": ind.get("adx"), "adxr": ind.get("adxr"),
                "adxr_level": ind.get("adxr_level"), "adxr_tradable": ind.get("adxr_tradable"),
                "atr14": ind.get("atr14"), "oi_latest": ind.get("oi_latest"),
                "oi_chg5_pct": ind.get("oi_chg5_pct"), "sharpe60": ind.get("sharpe60"),
            })
    except Exception as e:  # noqa: BLE001 - 单品种失败留空行, 绝不拖垮整表
        logger.warning("总览-技术失败(%s): %s", code, e)

    # 日线周期方向 + 涨跌幅(快照只给 latest, 不给前收 ⇒ 用日线最后两根算)
    try:
        bars = _daily_bars_cached(code, _OV_BARS)
        row["periods"] = {"day": _period_direction(bars)}
        if len(bars) >= 2:
            c1, c0 = _f(bars[-1].get("close")), _f(bars[-2].get("close"))
            if c1 and c0:
                row["change_pct"] = round((c1 / c0 - 1) * 100, 2)
            if row.get("latest") is None and c1:
                row["latest"] = c1
            row["volume"] = _f(bars[-1].get("volume"))
    except Exception as e:  # noqa: BLE001
        logger.warning("总览-日线失败(%s): %s", code, e)

    _ov_finalize(row, total=len(_OV_PERIODS))
    return row


def _ov_finalize(row: dict, total: int) -> None:
    """由当前已拿到的周期算 共振/状态/结论/置信度/关键信号 —— **纯计算, 可反复调**。

    【为什么要能反复调】两阶段渐进下, 阶段 A 只有日线、阶段 B 才有全部周期;
    每补一批周期都要重算这四个字段, 所以必须抽成幂等的纯函数(不能写成一次性赋值)。
    """
    per = row.get("periods") or {}
    have = [p for p in _OV_PERIODS if (per.get(p) or {}).get("bars")]
    ups = sum(1 for p in have if per[p]["dir"] == "up")
    downs = sum(1 for p in have if per[p]["dir"] == "down")
    row["period_total"] = total
    row["period_ready"] = len(have)
    row["period_up"] = ups
    row["period_down"] = downs
    row["resonance"] = max(ups, downs)
    res_dir = "up" if ups > downs else ("down" if downs > ups else "flat")
    row["resonance_dir"] = res_dir

    day_dir = (per.get("day") or {}).get("dir")
    row["state"] = {"up": "偏多", "down": "偏空"}.get(day_dir, "震荡")

    score = row.get("score")
    score = score if isinstance(score, (int, float)) else 0
    # 结论优先看共振; 周期还没取全时(resonance 天然偏低)**不要**据此判"中",
    # 否则阶段 A 会给出一片"中"、阶段 B 又集体变脸 —— 用"日线+技术分"先给结论。
    if row.get("period_ready", 0) >= len(_OV_PERIODS) and \
            row["resonance"] >= _OV_RESONANCE_MIN and res_dir != "flat":
        row["verdict"] = "多" if res_dir == "up" else "空"
    else:
        row["verdict"] = "多" if score >= 2 else ("空" if score <= -2 else "中")

    # 置信度: 共振占 60 分 + 日线技术分占 40 分 ⇒ 全周期共振且技术分满 = 100。
    # 【★ 分母必须是**目标周期总数**, 不能用"已就绪周期数"】实测踩过: 阶段 A 只有日线时
    #   resonance=1/period_ready=1 ⇒ 1/1*60+40 = **100 分**, 而真相是"4 个周期里只有
    #   1 个有方向" —— 会误导用户把 1/4 共振当强信号。用总数做分母后阶段 A 最多 15+40=55,
    #   阶段 B 补齐后自然升上去; 数字会跳是**可接受**的代价(诚实 > 平稳)。
    denom = len(_OV_PERIODS)
    row["confidence"] = int(round(row["resonance"] / denom * 60 + abs(score) / 5 * 40))
    row["key_signal"] = _ov_key_signal(row)


def _overview_fill_periods(code: str, row: dict) -> None:
    """**阶段 B**：补 60/15/5 三个周期（就地更新 row，并重算派生字段）。"""
    per = row.setdefault("periods", {})
    for p in _OV_INTRADAY:
        if (per.get(p) or {}).get("bars"):
            continue
        try:
            per[p] = _period_direction(_load_minute_bars(code, p, _OV_BARS))
        except Exception as e:  # noqa: BLE001 - 单周期失败留空, 不拖垮该行
            logger.warning("总览-周期%s失败(%s): %s", p, code, e)
            per[p] = {"dir": "flat", "label": "—", "strength": 0, "adx": None, "bars": 0}
    row["periods_done"] = all((per.get(p) or {}).get("bars") for p in _OV_PERIODS)
    _ov_finalize(row, total=len(_OV_PERIODS))


def _overview_one(code: str) -> dict:
    """单品种**完整**总览行(阶段 A + 阶段 B)。保留给测试/单品种直算用。"""
    row = _overview_base(code)
    _overview_fill_periods(code, row)
    return row


def _liquidity_grade(oi_vals: dict[str, float]) -> dict[str, str]:
    """按**全池持仓量**把品种分成 高/中/低 三档。

    【为什么按池内分位而不是绝对阈值】持仓量的绝对量级跨品种差异极大
    (原油与红枣不是一个数量级), 用固定阈值会把半数品种判成"低"。
    分档只用于**同一张表内的相对比较**, 不构成交易信号。
    """
    items = sorted(((c, v) for c, v in (oi_vals or {}).items() if v), key=lambda kv: kv[1])
    n, out = len(items), {}
    if not n:
        return out
    for i, (c, _v) in enumerate(items):
        q = i / max(1, n - 1)
        out[c] = "低" if q < 1 / 3 else ("高" if q > 2 / 3 else "中")
    return out


# ── 后台渐进计算的状态机 ────────────────────────────────────────────────
# 【形状】与项目其它任务型功能一致: 模块级状态 + 锁 + 后台线程 + 前端轮询。
#   前端拿 `ready/total/done` 决定"还要不要继续轮询、网格是否显示进度"。
_OV_STATE: dict[str, dict] = {}   # 【变量】key → {rows:{code:row}, done:bool, ts:float, err:str}
_OV_LOCK = threading.Lock()       # 【变量】状态读写锁


def _ov_state_key(codes: list[str]) -> str:
    return "overview:" + ",".join(codes) + ":v2"


def _ov_worker(key: str, codes: list[str], carry: dict | None = None) -> None:
    """后台线程主体：阶段 A(日线层) → 阶段 B(多周期)，每完成一个品种就发布一次。

    【为什么逐品种发布】用户希望"越早看到东西越好"。阶段 A 每完成一个品种就把
    rows 快照写回 `_OV_STATE`，前端下一次轮询即可看到该行 —— 不必等 21 个全好。
    【失败不致命】单品种异常写入兜底行(带 `error` 字段)，整轮永远会走到 done=True，
    否则前端会无限轮询。
    【★ carry = 上一轮结果（stale-while-revalidate, 2026-10-16）】TTL 到期重算时把旧
      rows 带进来 ⇒ ① 前端**秒开旧表**(而不是回到"计算中 0/21"干等一整轮 ~105s);
      ② 逐品种覆盖 ⇒ 表里每一行算好一个就换成新的, 用户看到的是"就地刷新"。
      ⚠ 有旧结果时 `done` 必须**全程保持 True** —— 否则前端会把已显示的表格换成进度条,
      又把"秒开"毁掉(这是本次修复的核心)。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: dict[str, dict] = dict(carry or {})   # 【变量】带上旧结果, 逐品种覆盖
    had_old = bool(carry)                       # 【变量】有旧结果 ⇒ 全程 done=True
    prog = {"n": 0}                             # 【变量】本轮已刷新的品种数(供前端显示进度)

    def _publish(done: bool = False, err: str = "") -> None:
        with _OV_LOCK:
            st = _OV_STATE.get(key)
            if st is None:      # 被显式 refresh 清掉了 ⇒ 放弃本轮
                return
            st["rows"] = dict(rows)
            st["done"] = bool(done or had_old)
            st["refreshing"] = not done
            st["stale"] = bool(had_old and not done)
            st["refreshed"] = prog["n"]
            st["ts"] = time.time()
            if err:
                st["err"] = err

    try:
        # ── 阶段 A ──────────────────────────────────────────────
        with ThreadPoolExecutor(max_workers=_RANK_PARALLEL) as ex:
            futs = {ex.submit(_overview_base, c): c for c in codes}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    rows[c] = fut.result()
                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段A失败(%s): %s", c, e)
                    rows[c] = _ov_fallback_row(c, str(e))
                prog["n"] += 1
                _publish()

        # 阶段 A 一结束就按全池算流动性(单品种看不到全池)
        grades = _liquidity_grade({c: r.get("oi_latest") for c, r in rows.items()})
        for c, r in rows.items():
            r["liquidity"] = grades.get(c, "—")
        _publish()

        # ── 阶段 B ──────────────────────────────────────────────
        with ThreadPoolExecutor(max_workers=_RANK_PARALLEL) as ex:
            futs = {ex.submit(_overview_fill_periods, c, rows[c]): c
                    for c in codes if c in rows}
            for fut in as_completed(futs):
                c = futs[fut]
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    logger.warning("总览-阶段B失败(%s): %s", c, e)
                prog["n"] += 1
                _publish()
    except Exception as e:  # noqa: BLE001 - 兜底: 整轮异常也必须收敛到 done
        logger.warning("总览后台计算异常: %s", e)
        _publish(done=True, err=str(e)[:200])
        return
    _publish(done=True)


def _ov_fallback_row(code: str, err: str = "") -> dict:
    """品种级兜底行(取数全失败时)——保证表格行数稳定, 不出现"少了一行"。"""
    meta = _meta(code)
    return {"variety": code, "name": meta.get("name", code),
            "sector": _sector_bucket(meta.get("sector_cn") or ""),
            "exchange": meta.get("exchange", ""), "periods": {},
            "periods_done": False, "state": "—", "verdict": "中", "resonance": 0,
            "period_total": len(_OV_PERIODS), "period_ready": 0,
            "confidence": 0, "key_signal": "数据获取失败", "error": err[:120]}


def _ov_start(codes: list[str], force: bool = False) -> str:
    """确保后台计算已启动（幂等）。返回 state key。

    【★ stale-while-revalidate（2026-10-16 修）】此前 TTL 一到就把 rows **清空**再重算
      ⇒ 用户每次回到页面(距上次 >15min) 都得对着进度条等一整轮(~105s) ——
      这正是"为什么每次进入该页面还是要重新加载计算"那个投诉的直接来源。
    现改为：
      · 已有旧结果 ⇒ **保留**它并在后台重算：全程 `done=True` + `refreshing=True`
        ⇒ 前端**秒开旧表**，算好的品种逐个替换；从无结果(首次)才走空表 + 渐进轮询。
      · 真正决定"要不要重算"的仍然是 `_TTL_OVERVIEW`，只是重算期间**不再剥夺**
        用户已经能看到的数据。
    【状态字段】rows / done(有可用全量结果) / refreshing(后台正在算) /
      stale(当前 rows 是上一轮的) / refreshed(本轮已刷新品种数) / ts / err。
    """
    key = _ov_state_key(codes)
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        refreshing = bool(st.get("refreshing"))
        done = bool(st.get("done"))
        # 新鲜 = 算完过、当前不在刷新、不是旧数据、且未过期
        fresh = (done and not refreshing and not st.get("stale")
                 and time.time() - st.get("ts", 0) < _TTL_OVERVIEW)
        if refreshing:
            # 已有后台刷新在跑（显式 force 也不再起一个线程 —— 那一轮马上就会给出新数据）
            return key
        if fresh and not force:
            return key                     # 结果还新鲜, 命中
        has_old = bool(st.get("rows"))
        carry = dict(st["rows"]) if has_old else None
        _OV_STATE[key] = {"rows": dict(carry or {}), "done": has_old, "refreshing": True,
                          "stale": has_old, "refreshed": 0, "err": "",
                          "ts": st.get("ts", 0)}
    t = threading.Thread(target=_ov_worker, args=(key, list(codes), carry),
                         name="quant-overview", daemon=True)
    t.start()
    return key


def _ov_snapshot(key: str, codes: list[str]) -> tuple[list[dict], bool, str, dict]:
    """取当前快照（可能是部分结果）。返回 (rows_按 codes 序, done, err, meta)。

    【meta（2026-10-16 新增）】{refreshing, stale, refreshed, ts}
      —— 供前端区分三态，而不是只有"算完/没算完"两种：
        · 首次计算中(done=False)          → 显示进度条
        · 有旧数据+后台刷新中(refreshing)  → **旧表继续显示** + 短轮询等新数据落地
        · 完成且不再刷新                   → 排 15 min 定时器
    """
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        rows_map = st.get("rows") or {}
        done = bool(st.get("done"))
        err = st.get("err") or ""
        meta = {"refreshing": bool(st.get("refreshing")),
                "stale": bool(st.get("stale")),
                "refreshed": int(st.get("refreshed") or 0),
                "ts": float(st.get("ts") or 0)}
    return ([rows_map[c] for c in codes if c in rows_map], done, err, meta)


@quant_bp.route("/api/quant/overview")
def api_quant_overview():
    """⑬ 全品种技术面总览 —— 「先看总览, 再点进单品种」。

    【参数】varieties=逗号分隔品种(默认全池 21 个); sector=粗板块过滤(可选);
            refresh=1 强制重算。
    【返回】{ok, count, ready, total, done, cached, err, pools, periods, rows}
      · `ready/total/done` 供前端做**渐进渲染**: 每完成一个品种 ready +1;
        `done=False` 时前端应继续轮询(建议 3s)。
      · 每个 row: variety/name/sector/latest/change_pct/periods{day,60,15,5}
                 /period_ready/period_total/state/resonance/resonance_dir/verdict
                 /confidence/liquidity/key_signal/ma_state/rsi14/adx/oi_latest …
    【为什么不在请求里同步算】实测 21 品种 × 4 周期 = 153s(天勤短连接瓶颈),
      同步算等于让首屏等两个半小时。详见本节顶部注释。
    【降级】单品种/单周期失败只留空值并保留该行, 绝不 500。
    """
    raw = (request.args.get("varieties") or "").strip()
    pool = _pool()
    codes = [c for c in (x.strip().upper() for x in raw.split(",")) if c] if raw else pool
    codes = [c for c in codes if c in pool] or pool

    force = (request.args.get("refresh") or "").strip() in ("1", "true", "yes")
    key = _ov_start(codes, force=force)
    rows, done, err, meta = _ov_snapshot(key, codes)

    # 板块清单按**池内全部品种**动态派生(扩池自动跟随, 不硬编码)。
    # ⚠️ 用 codes 而非 rows: 渐进阶段 rows 还不全, 用 rows 会让下拉里的板块越筛越少。
    pools = sorted({_sector_bucket(_meta(c).get("sector_cn") or "") for c in codes})

    sector = (request.args.get("sector") or "").strip()
    if sector and sector != "全部品种":
        rows = [r for r in rows if r.get("sector") == sector]

    return jsonify(_json_safe({
        "ok": True, "count": len(rows), "ready": len(rows), "total": len(codes),
        "done": done,
        # 【★ cached 语义修正 2026-10-16】原为 `done and not err` —— 只要算完就报"缓存",
        #   哪怕就是**刚刚现算出来**的(实测第一轮就 `cached=true`)。现改为如实反映
        #   "本次拿到的是一份**没在刷新**的新鲜结果"。
        "cached": bool(done and not meta["refreshing"] and not meta["stale"] and not err),
        "refreshing": meta["refreshing"], "stale": meta["stale"],
        "refreshed": meta["refreshed"], "ts": meta["ts"], "err": err,
        "pools": ["全部品种"] + pools,
        "periods": [{"key": p, "cn": _OV_PERIOD_CN[p]} for p in _OV_PERIODS],
        "rows": rows,
    }))
