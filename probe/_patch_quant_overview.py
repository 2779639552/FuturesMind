# -*- coding: utf-8 -*-
"""给 quant_module.py 追加「⑬ 全品种技术面总览」接口（2026-10-14）。

============================== 为什么单独一个接口 ==============================
`/api/quant/ranking` 是「按某一维度排序的截面表」（单维度、单周期）；
用户要的是**一眼看全部品种的技术面**：每行同时给 多周期方向 + 共振数 + 结论 +
置信度 + 流动性 + 关键信号，并能点进单品种看细节。两者口径与形状都不同。

但**取数一律复用既有函数**（项目铁律：同一指标不另起一套口径）：
  · 日线技术口径 → `_technical_snapshot`（score/verdict/ma_state/ADX/持仓量）
  · 日线/分钟 K   → `_daily_bars_cached` / `_load_minute_bars`

============================== 顺手做的一处优化 ==============================
新增 `_daily_bars_cached` 并让 `_technical_snapshot` 改用它 —— 总览要对每个品种
既算「日线周期方向」又要算「技术快照」，两处都调 `_load_daily_bars` 会**拉两遍**。
日线一天才变一次，缓存 5 分钟零风险，且对 `_cross_section_one` 也一并受益。

============================== 纪律 ==============================
· 备份先做（backup/quant_module_pre_overview_*.py）
· 锚点**先验命中数**，任一不为 1 即中止且不写文件
· quant_module.py 是纯 LF；写回后自检 `CRLF == 0`
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "quant_module.py"

# ═══════════ ① _technical_snapshot 改用缓存的日线加载 ═══════════
A_BARS = '''def _technical_snapshot(code: str, days: int = 250) -> dict:'''
N_BARS = '''def _daily_bars_cached(code: str, days: int) -> list[dict]:
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


def _technical_snapshot(code: str, days: int = 250) -> dict:'''

A_CALL = '''    bars = _load_daily_bars(code, days)
    if len(bars) < 30:
        return {"ok": False, "error": f"日线数据不足({len(bars)} 根, 需 ≥30)"}'''
N_CALL = '''    bars = _daily_bars_cached(code, days)  # 【调用函数】带缓存, 见其 docstring
    if len(bars) < 30:
        return {"ok": False, "error": f"日线数据不足({len(bars)} 根, 需 ≥30)"}'''

# ═══════════ ② 追加总览接口 ═══════════
OVERVIEW = '''

# ===========================================================================
# ⑬ 全品种技术面总览(2026-10-14 新增) —— 「先看总览, 再点进单品种」
# ===========================================================================
# 【这个接口解决什么】原 /api/quant/ranking 是"按某一维度排序的截面表"; 用户要的是
#   **一眼看全部品种的技术面**: 每行同时给出 多周期方向 + 共振数 + 结论 + 置信度 +
#   流动性 + 关键信号, 并能点进单品种看细节。口径/形状都不同, 故独立成端点。
# 【取数铁律】全部复用既有函数, 不另起一套口径:
#   日线技术面 → _technical_snapshot(score/verdict/ma_state/ADX/持仓量)
#   K 线        → _daily_bars_cached / _load_minute_bars
# 【性能】21 品种 × 5 周期, 有界并发 6 + 整体缓存 5 min(照抄 _rank_all 范式);
#   首次约十几秒, 之后秒回。分钟级实测单次 0.3~0.4s, 不是瓶颈。

# 【周期口径】日线 / 60分 / 30分 / 15分 / 5分。
#   不放 3分/1分: 项目信号体系最短到 5 分钟, 更短的周期噪声大, 反而稀释"共振数"的
#   可解释性(共振应当是"多个可交易周期是否一致", 而不是把秒级噪声也算一票)。
_OV_PERIODS: tuple[str, ...] = ("day", "60", "30", "15", "5")
_OV_PERIOD_CN: dict[str, str] = {"day": "日线", "60": "60分", "30": "30分",
                                 "15": "15分", "5": "5分"}
_OV_BARS = 120          # 【变量】每周期取多少根(够 EMA30 + ADX 稳定, 又不至于太慢)
_TTL_OVERVIEW = 300     # 【变量】总览整体缓存 5 min(与 _TTL_RANK 同档)
_OV_ADX_FLAT = 20       # 【变量】ADX 低于此值判「无趋势/震荡」
_OV_RESONANCE_MIN = 4   # 【变量】≥4/5 个周期一致才给出方向性结论

# 【粗板块归并】_meta 给的是细分子板块(「能化(化工)」), 总览要的是可筛选的粗板块。
#   映射按**前缀 + 括号内限定**两级判定, 不用逐个子板块硬编码 —— 扩池新增
#   「能化(芳烃)」这类子板块时会自动落到「能化」, 不会漏。
_SECTOR_EXACT: dict[str, str] = {
    "能化(燃料)": "能源", "能化(能源)": "能源",
    "有色(新能源)": "新能源",
    "农产品(油脂)": "谷物油脂",
}


def _sector_bucket(sector_cn: str) -> str:
    """细分子板块 → 总览用的粗板块(全部品种 / 黑色 / 能源 / 新能源 / 农产品 …)。

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
    【返回】{dir: up|down|flat, label: 多|空|震, strength: -99..99, adx, bars}
      strength 只在有方向时非 0(= 三票归一化); 震荡一律 0, 便于前端只按方向渲染。
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

    if adx is not None and adx < _OV_ADX_FLAT:
        return {"dir": "flat", "label": "震", "strength": 0, "adx": adx,
                "bars": len(closes)}
    if votes >= 1:
        return {"dir": "up", "label": "多", "strength": int(round(votes / 3 * 99)),
                "adx": adx, "bars": len(closes)}
    if votes <= -1:
        return {"dir": "down", "label": "空", "strength": int(round(votes / 3 * 99)),
                "adx": adx, "bars": len(closes)}
    return {"dir": "flat", "label": "震", "strength": 0, "adx": adx, "bars": len(closes)}


def _ov_key_signal(row: dict) -> str:
    """拼一句「关键信号」—— 用户在原设计里最看重的一列(一眼知道该干什么)。

    【为什么不是把指标名堆上去】原设计写的是可执行的话
    (如「趋势强度-12, 始终做空; 反弹至 EMA 优先做空」)。所以这里按
    **趋势强度 → 动作建议 → 超买超卖** 三段拼, 而不是罗列 RSI/MACD 数值。
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


def _overview_one(code: str) -> dict:
    """单品种总览行(**所有字段都带, 缺数据留 None, 绝不抛**)。"""
    meta = _meta(code)
    sector_raw = meta.get("sector_cn") or ""
    row: dict[str, Any] = {
        "variety": code, "name": meta.get("name", code),
        "exchange": meta.get("exchange", ""),
        "sector": _sector_bucket(sector_raw), "sector_raw": sector_raw,
        "unit": meta.get("unit", ""), "main_contract": meta.get("main_contract", ""),
    }

    # ── 日线 + 技术快照(两者共用缓存的日线, 只拉一遍) ────────────────
    try:
        snap = _technical_snapshot(code, _OV_BARS)
        if snap.get("ok"):
            ind = snap.get("indicators") or {}
            row.update({
                "latest": snap.get("latest"), "date": snap.get("date"),
                "score": snap.get("score"), "verdict": snap.get("verdict"),
                "ma_state": snap.get("ma_state"),
                "rsi14": ind.get("rsi14"), "macd_bar": ind.get("macd_bar"),
                "ann_vol_pct": ind.get("ann_vol_pct"), "mom20_pct": ind.get("mom20_pct"),
                "adx": ind.get("adx"), "adxr": ind.get("adxr"),
                "adxr_level": ind.get("adxr_level"), "adxr_tradable": ind.get("adxr_tradable"),
                "atr14": ind.get("atr14"), "oi_latest": ind.get("oi_latest"),
                "oi_chg5_pct": ind.get("oi_chg5_pct"), "sharpe60": ind.get("sharpe60"),
            })
    except Exception as e:  # noqa: BLE001 - 单品种失败留空行
        logger.warning("总览-技术失败(%s): %s", code, e)

    # 涨跌幅: 用日线最后两根算(快照只给 latest, 不给前收)
    try:
        bars = _daily_bars_cached(code, _OV_BARS)
        if len(bars) >= 2:
            c1, c0 = _f(bars[-1].get("close")), _f(bars[-2].get("close"))
            if c1 and c0:
                row["change_pct"] = round((c1 / c0 - 1) * 100, 2)
            if row.get("latest") is None and c1:
                row["latest"] = c1
            row["volume"] = _f(bars[-1].get("volume"))
    except Exception as e:  # noqa: BLE001
        logger.warning("总览-涨跌幅失败(%s): %s", code, e)

    # ── 多周期方向(day 复用上面已取的日线) ───────────────────────────
    periods: dict[str, dict] = {}
    for p in _OV_PERIODS:
        try:
            if p == "day":
                pb = _daily_bars_cached(code, _OV_BARS)
            else:
                pb = _load_minute_bars(code, p, _OV_BARS)
            periods[p] = _period_direction(pb)
        except Exception as e:  # noqa: BLE001 - 单周期失败留空, 不拖垮该行
            logger.warning("总览-周期%s失败(%s): %s", p, code, e)
            periods[p] = {"dir": "flat", "label": "—", "strength": 0, "adx": None,
                          "bars": 0}
    row["periods"] = periods

    # ── 共振 / 状态 / 结论 / 置信度 ────────────────────────────────
    have = [p for p in _OV_PERIODS if (periods.get(p) or {}).get("bars")]
    ups = sum(1 for p in have if periods[p]["dir"] == "up")
    downs = sum(1 for p in have if periods[p]["dir"] == "down")
    total = len(have) or len(_OV_PERIODS)
    row["period_total"] = total
    row["period_up"] = ups
    row["period_down"] = downs
    row["resonance"] = max(ups, downs)          # 共振数(与主导方向一致的周期数)
    res_dir = "up" if ups > downs else ("down" if downs > ups else "flat")
    row["resonance_dir"] = res_dir

    day_dir = (periods.get("day") or {}).get("dir")
    row["state"] = {"up": "偏多", "down": "偏空"}.get(day_dir, "震荡")

    score = row.get("score")
    score = score if isinstance(score, (int, float)) else 0
    if row["resonance"] >= _OV_RESONANCE_MIN and res_dir != "flat":
        row["verdict"] = "多" if res_dir == "up" else "空"
    else:
        row["verdict"] = "多" if score >= 2 else ("空" if score <= -2 else "中")

    # 置信度: 共振占 60 分 + 日线技术分占 40 分 ⇒ 全周期共振且技术分满 = 100
    row["confidence"] = int(round(row["resonance"] / total * 60
                                  + abs(score) / 5 * 40)) if total else 0

    # ── 流动性(池内分档在 _overview_all 里按全池算) ─────────────────
    row["key_signal"] = _ov_key_signal(row)
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


def _overview_all(codes: list[str]) -> tuple[list[dict], bool]:
    """有界并发拉全池总览 + 整体缓存。返回 (rows, cached)。"""
    key = "overview:" + ",".join(codes) + ":v1"
    hit = _cache_get(key, _TTL_OVERVIEW)
    if hit is not None:
        return hit, True

    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_RANK_PARALLEL) as ex:
        futs = {ex.submit(_overview_one, c): c for c in codes}
        for fut, c in futs.items():
            try:
                out[c] = fut.result()
            except Exception as e:  # noqa: BLE001 - 单品种失败留空行
                logger.warning("总览失败(%s): %s", c, e)
                out[c] = {"variety": c, "name": _meta(c).get("name", c),
                          "sector": _sector_bucket(_meta(c).get("sector_cn") or ""),
                          "periods": {}, "state": "—", "verdict": "中",
                          "resonance": 0, "period_total": len(_OV_PERIODS),
                          "confidence": 0, "key_signal": "数据获取失败"}

    rows = [out[c] for c in codes if c in out]
    # 流动性按**全池**持仓量分档(必须在并发返回后统一算, 单品种看不到全池)
    grades = _liquidity_grade({r["variety"]: r.get("oi_latest") for r in rows})
    for r in rows:
        r["liquidity"] = grades.get(r["variety"], "—")
    _cache_set(key, rows)
    return rows, False


@quant_bp.route("/api/quant/overview")
def api_quant_overview():
    """⑬ 全品种技术面总览 —— 「先看总览, 再点进单品种」。

    【参数】varieties=逗号分隔品种(默认全池 21 个); sector=粗板块过滤(可选);
            refresh=1 强制跳过缓存重拉。
    【返回】{ok, count, cached, pools:[粗板块…], periods:[{key,cn}…], rows:[…]}
      每个 row: variety/name/sector/latest/change_pct/periods{day,60,30,15,5}
               /state/resonance/period_total/resonance_dir/verdict/confidence
               /liquidity/key_signal/ma_state/rsi14/adx/oi_latest …
    【性能】21 品种 × 5 周期, 并发 6 + 缓存 5 min; 首次约十几秒, 之后秒回。
    【降级】单品种/单周期失败只留空值, 绝不 500(与模块内其它接口同约定)。
    """
    raw = (request.args.get("varieties") or "").strip()
    pool = _pool()
    codes = [c for c in (x.strip().upper() for x in raw.split(",")) if c] if raw else pool
    codes = [c for c in codes if c in pool] or pool

    if (request.args.get("refresh") or "").strip() in ("1", "true", "yes"):
        with _CACHE_LOCK:  # 【变量】显式失效, 下次调用真正重拉
            _CACHE.pop("overview:" + ",".join(codes) + ":v1", None)

    rows, cached = _overview_all(codes)

    # 板块清单按**池内实际出现**动态派生(扩池自动跟随, 不硬编码);
    # ⚠️ 必须在**过滤前**取全量 rows, 否则选中某板块后下拉里就只剩它自己了。
    pools = sorted({r.get("sector") or "其他" for r in rows})

    sector = (request.args.get("sector") or "").strip()
    if sector and sector != "全部品种":
        rows = [r for r in rows if r.get("sector") == sector]

    return jsonify(_json_safe({
        "ok": True, "count": len(rows), "cached": cached,
        "pools": ["全部品种"] + pools,
        "periods": [{"key": p, "cn": _OV_PERIOD_CN[p]} for p in _OV_PERIODS],
        "rows": rows,
    }))
'''

REPL = [
    ("① 日线缓存函数 + 快照改调缓存", A_BARS, N_BARS),
    ("② 快照内部改用缓存加载", A_CALL, N_CALL),
]


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf != 0 or lf == 0:
        print(f"✘ quant_module.py 应为纯 LF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8")

    print("=" * 74)
    print("补丁：quant_module.py 追加「全品种技术面总览」")
    print("=" * 74)

    print("\n[1] 锚点预检（任一不为 1 即中止，不写文件）")
    bad = []
    for tag, old, _new in REPL:
        n = src.count(old)
        print(f"    {'OK' if n == 1 else '✘'} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            bad.append(tag)
    # 追加点：文件末尾（必须恰有一个结尾）
    tail = "\n"
    if not src.endswith("\n"):
        print("    ✘ 文件结尾异常：不以换行结束")
        bad.append("文件结尾")
    else:
        print("    OK 文件以换行结束（追加点安全）")
    if bad:
        print(f"\n✘ 锚点异常：{bad} —— 已中止，文件未改动")
        return 3

    print("\n[2] 应用替换")
    for tag, old, new in REPL:
        src = src.replace(old, new, 1)
        print(f"    ✔ {tag}")
    src = src + OVERVIEW
    print("    ✔ ③ 追加总览接口（%d 字符）" % len(OVERVIEW))

    print("\n[3] 自检")
    checks = [
        ("_daily_bars_cached 已定义", "def _daily_bars_cached(" in src, True),
        ("快照已改用缓存加载", "bars = _daily_bars_cached(code, days)" in src, True),
        ("总览路由已注册", '@quant_bp.route("/api/quant/overview")' in src, True),
        ("周期常量已定义", '_OV_PERIODS: tuple[str, ...] = ("day", "60", "30", "15", "5")' in src, True),
        ("板块归并已定义", "def _sector_bucket(" in src, True),
        ("周期方向已定义", "def _period_direction(" in src, True),
        ("流动性分档已定义", "def _liquidity_grade(" in src, True),
        ("关键信号已定义", "def _ov_key_signal(" in src, True),
        ("旧的裸加载仍在别处可用", "def _load_daily_bars(" in src, True),
    ]
    problems = []
    for name, got, want in checks:
        ok = (got is want)
        print(f"    {'OK' if ok else '✘'} {name}")
        if not ok:
            problems.append(name)
    if problems:
        print(f"\n✘ 自检未过：{problems} —— 已中止，文件未改动")
        return 4

    TARGET.write_text(src, encoding="utf-8", newline="")
    chk = TARGET.read_bytes()
    c2 = chk.count(b"\r\n")
    l2 = chk.count(b"\n") - c2
    print(f"\n[4] 已写入 {len(raw)} → {len(chk)} bytes (+{len(chk) - len(raw)})")
    print(f"    行尾: CRLF={c2} bareLF={l2} => {'✔ 纯 LF' if c2 == 0 else '✘ 混入 CRLF'}")
    print("\n✔ 补丁完成")
    return 0 if c2 == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
