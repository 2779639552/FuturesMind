# -*- coding: utf-8 -*-
"""重写「⑬ 全品种技术面总览」（v2）—— 两阶段渐进式 + 修正趋势强度口径。

============================== 为什么要 v2 ================================
v1（刚追加的那版）是**同步算完才返回**，实测：
    单品种 7.3s（1 次日线 + 4 次分钟，天勤短连接每次 ~4.8s）
    ⇒ 21 品种 153s ⇒ **首屏等 2.5 分钟**，不可接受。
且天勤建连是串行的（`_connect_lock`），加大并发也没用（实测并发 6 时 0.72 次/秒）。

v2 改两件事：
  ① **两阶段**：先算「日线层」（每品种 1 次取数，21 次 ≈ 30s），行立即可见；
     再后台补「多周期」（60/15/5）。前端按 `ready/total/done` 渐进渲染。
  ② **趋势强度口径**：v1 用 `votes/3*99`，干净趋势恒为 ±99（饱和、没信息量）。
     v2 改为**同时体现票数与 ADX 质量**：`votes/3 * 99 * min(adx,50)/50`
     ⇒ 同样 3 票，ADX 25 得 49、ADX 50+ 得 99，能区分"强趋势"与"弱趋势"。

============================== 周期口径 ================================
日线 / 60分 / 15分 / 5分（4 个）。**去掉 30分**：它与 60分 区分度最低，
而每多一个周期就多 21 次短连接（约 +30s），边际信息不值这个等待。

============================== 纪律 ================================
· 锚点先验命中（本节唯一），失败即中止且不写文件；备份已在 _patch_quant_overview 时做过。
· quant_module.py 是纯 LF；写回自检 `CRLF == 0`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "quant_module.py"

MARKER = """# ===========================================================================
# ⑬ 全品种技术面总览(2026-10-14 新增) —— 「先看总览, 再点进单品种」
# ===========================================================================
"""

NEW_BLOCK = '''# ===========================================================================
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
_TTL_OVERVIEW = 300     # 【变量】总览整体缓存 5 min(与 _TTL_RANK 同档)
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
    if row.get("period_ready", 0) >= len(_OV_PERIODS) and \\
            row["resonance"] >= _OV_RESONANCE_MIN and res_dir != "flat":
        row["verdict"] = "多" if res_dir == "up" else "空"
    else:
        row["verdict"] = "多" if score >= 2 else ("空" if score <= -2 else "中")

    # 置信度: 共振占 60 分 + 日线技术分占 40 分 ⇒ 全周期共振且技术分满 = 100。
    # 周期未取全时按"已知周期"归一, 避免渐进过程中数字忽高忽低。
    denom = row.get("period_ready") or len(_OV_PERIODS)
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


def _ov_worker(key: str, codes: list[str]) -> None:
    """后台线程主体：阶段 A(日线层) → 阶段 B(多周期)，每完成一个品种就发布一次。

    【为什么逐品种发布】用户希望"越早看到东西越好"。阶段 A 每完成一个品种就把
    rows 快照写回 `_OV_STATE`，前端下一次轮询即可看到该行 —— 不必等 21 个全好。
    【失败不致命】单品种异常写入兜底行(带 `error` 字段)，整轮永远会走到 done=True，
    否则前端会无限轮询。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    rows: dict[str, dict] = {}

    def _publish(done: bool = False, err: str = "") -> None:
        with _OV_LOCK:
            st = _OV_STATE.get(key)
            if st is None:      # 被显式 refresh 清掉了 ⇒ 放弃本轮
                return
            st["rows"] = dict(rows)
            st["done"] = done
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
    """确保后台计算已启动（幂等）。返回 state key。"""
    key = _ov_state_key(codes)
    with _OV_LOCK:
        st = _OV_STATE.get(key)
        fresh = bool(st and st.get("done") and time.time() - st.get("ts", 0) < _TTL_OVERVIEW)
        if st is not None and not st.get("done") and not force:
            return key                     # 正在跑, 不重复起线程
        if fresh and not force:
            return key                     # 结果还新鲜
        _OV_STATE[key] = {"rows": {}, "done": False, "ts": time.time(), "err": ""}
    t = threading.Thread(target=_ov_worker, args=(key, list(codes)),
                         name="quant-overview", daemon=True)
    t.start()
    return key


def _ov_snapshot(key: str, codes: list[str]) -> tuple[list[dict], bool, str]:
    """取当前快照（可能是部分结果）。返回 (rows_按 codes 序, done, err)。"""
    with _OV_LOCK:
        st = _OV_STATE.get(key) or {}
        rows_map = st.get("rows") or {}
        done = bool(st.get("done"))
        err = st.get("err") or ""
    return ([rows_map[c] for c in codes if c in rows_map], done, err)


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
    rows, done, err = _ov_snapshot(key, codes)

    # 板块清单按**池内全部品种**动态派生(扩池自动跟随, 不硬编码)。
    # ⚠️ 用 codes 而非 rows: 渐进阶段 rows 还不全, 用 rows 会让下拉里的板块越筛越少。
    pools = sorted({_sector_bucket(_meta(c).get("sector_cn") or "") for c in codes})

    sector = (request.args.get("sector") or "").strip()
    if sector and sector != "全部品种":
        rows = [r for r in rows if r.get("sector") == sector]

    return jsonify(_json_safe({
        "ok": True, "count": len(rows), "ready": len(rows), "total": len(codes),
        "done": done, "cached": done and not err, "err": err,
        "pools": ["全部品种"] + pools,
        "periods": [{"key": p, "cn": _OV_PERIOD_CN[p]} for p in _OV_PERIODS],
        "rows": rows,
    }))
'''

def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf != 0 or lf == 0:
        print(f"✘ quant_module.py 应为纯 LF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8")

    print("=" * 74)
    print("重写「⑬ 全品种技术面总览」v2（两阶段渐进 + 强度口径修正）")
    print("=" * 74)

    n = src.count(MARKER)
    print(f"\n[1] 锚点预检：本节标记命中 {n} 次（期望 1）")
    if n != 1:
        print("✘ 锚点异常 —— 已中止，文件未改动")
        return 3

    head = src[: src.index(MARKER)]
    src = head + NEW_BLOCK
    print(f"    ✔ 本节已整体替换（{len(NEW_BLOCK)} 字符）")

    print("\n[2] 自检")
    checks = [
        ("周期常量为 4 个", '_OV_PERIODS: tuple[str, ...] = ("day", "60", "15", "5")' in src),
        ("分钟周期常量存在", '_OV_INTRADAY: tuple[str, ...] = ("60", "15", "5")' in src),
        ("阶段 A 函数存在", "def _overview_base(" in src),
        ("阶段 B 函数存在", "def _overview_fill_periods(" in src),
        ("派生字段函数存在", "def _ov_finalize(" in src),
        ("后台状态机存在", "_OV_STATE: dict[str, dict] = {}" in src),
        ("后台线程存在", 'name="quant-overview"' in src),
        ("快照函数存在", "def _ov_snapshot(" in src),
        ("兜底行存在", "def _ov_fallback_row(" in src),
        ("路由仍注册", '@quant_bp.route("/api/quant/overview")' in src),
        ("v1 的旧单阶段实现已移除", "def _overview_all(" not in src),
        ("旧的 30分 周期已移除", '"30", "15", "5")' not in src),
        ("强度含 ADX 系数", "quality = min(adx or 0.0, _OV_STRENGTH_ADX_REF)" in src),
        ("_json_safe 仍存在", "def _json_safe(" in src),
    ]
    bad = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad:
        print(f"\n✘ 自检未过：{bad} —— 已中止，文件未改动")
        return 4

    TARGET.write_text(src, encoding="utf-8", newline="")
    chk = TARGET.read_bytes()
    l2 = chk.count(b"\n") - chk.count(b"\r\n")
    print(f"\n[3] 已写入 {len(raw)} → {len(chk)} bytes ({len(chk) - len(raw):+d})")
    print(f"    行尾: CRLF={chk.count(b'\r\n')} bareLF={l2}"
          f" => {'✔ 纯 LF' if chk.count(b'\r\n') == 0 else '✘'}")
    print("\n✔ v2 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
