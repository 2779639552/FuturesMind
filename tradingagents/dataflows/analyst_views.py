"""analyst_views.py — 把量化结论**按数据轴**分给不同分析师（2026-10-19）。

【它解决什么】
  技术面量化页与基本面量化页各自已经算出结论（技术快照 / 五书方向票 / 三指标策略 /
  二维四象 / 虚实盘比），但运行分析的四个分析师读不到。此前的做法是把所有票在
  `vote_aggregate` 里**合成一个总分** —— 那等于在分析师之前就替他们做了裁决，
  而且总分一出来，谁依据什么就说不清了。

  本模块改成**不融合、只分发**：
    · 价格轴                 → 技术分析师
    · 基差 / 库存 / 利润 三轴 → 基本面分析师
    · 两边的结构化结论        → 研究经理（在辩论之后统一裁决）

【为什么按轴分是"可辩护的切法"】
  `quant_verdict` 的每张票本来就带 `axis`（L1a 引入），`weighted` 也是按轴组织的。
  轴的划分对应**数据来源**，不对应"书"：鹿希武、墨菲、威科夫读的都是 K 线，属同一轴；
  四象的估值腿读基差、驱动腿读库存 —— 所以它在源头被拆成 `axis_parts`。
  ⇒ 分发单位 = 数据轴，不是流派。同轴多票会被轴内归一，不会因"票多"而压过别的轴。

【输出纪律】
  1. **只给自己那部分，并明确写出"不含什么"**。技术分析师拿库存解释 K 线是本项目
     明确要避免的；反过来基本面分析师也不该替技术面定入场时点。
  2. **三态不混**：取数失败写"失败"，无覆盖写"无覆盖"，都不写成 0。
  3. **静默降级**：任何一节失败只丢该节，整块仍可用；全空返回 ""。
  4. **裁决不写硬规则**：27,000+ 条样本的回测里，18 组"谁赢"组合无一通过多重检验，
     所以 `reconcile_view` 只给**处理流程**（同向加档 / 反向给条件句），不给"某方优先"。

【性能】
  冷启动：日线 6.2s + 基差 0.8s + 仓单 1.6s + 期限 0.9s + 利润 0.2s ≈ 9.6s（TA 实测）；
  命中 TTL 后全部 0.00s（下层 quant_module 自带 15min / 6h 缓存）。
  ⇒ 与 `analysis_bundle` 同策略：进程内缓存 10min + 单飞 + 首调最多等 `WAIT_SECONDS`，
     超时先返回 ""，后台线程算完灌缓存，下一次分析命中。
     分析启动时由 `warm()` 预热，正常不会走到超时分支。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

WAIT_SECONDS = 20.0       # 首次调用最多阻塞多久
# 【2026-10-22 体检修正】原值 8.0 的依据是"冷启动实测 9.6s，故略低于它"，已过期：
#   实测 `_compute` 冷启动 16.17s（TA，新进程；日线一项 8.74s，天勤 SSL 失败后回退 AKShare），
#   而技术分析师是图里**第一个**节点（建图 1.54s 后即到达）⇒ 等满 8s 必然拿不到价格轴视图。
#   竞态复现：8.00s→0 字、16.00s 再取仍 0、16.33s（基本面节点）才命中。
#   取 20s = 实测冷启动 + 余量。只在冷缓存时付出，10 分钟内命中则无感。
#   ⚠ 不要改回 8s：`tests/test_injection_timeout_and_order.py` 会红。
CACHE_TTL = 600           # 与 analysis_bundle 同档

_SNAP: dict[str, tuple[float, dict[str, Any]]] = {}
_EVENTS: dict[str, threading.Event] = {}
_LOCK = threading.Lock()

# 轴 → 归属（这是本模块唯一的"分配表"，两侧视图都按它切）
TECH_AXES = ("price",)
FUND_AXES = ("basis", "inventory", "profit")
_AXIS_CN = {"price": "价格", "basis": "基差", "inventory": "库存", "profit": "利润"}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _n(v: Any, nd: int = 2) -> str:
    """数值 → 短文本；非数值返回 "—"（不写 0，0 与"没数据"必须能区分）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    if abs(f) >= 10000:
        return f"{f:,.0f}"
    if abs(f) >= 1:
        return f"{f:,.{nd}f}".rstrip("0").rstrip(".")
    return f"{f:.3f}".rstrip("0").rstrip(".")


def _sc(v: Any) -> str:
    """轴得分 → 带符号两位小数（`_n` 会吃掉 "+1.00" 的符号与零，读起来像整数）。"""
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return "—"


def _sign_cn(x: float, eps: float = 0.05) -> str:
    if x > eps:
        return "偏多"
    if x < -eps:
        return "偏空"
    return "中性"


# 【为什么要在技术视图里改写这一句】「库存周期」这张票的 axis 是 price（方向由价格 5 日
# 涨跌决定），但它的 strength_basis 原文里写着"库存变化>100% 记为小基数失真"。
# 技术分析师看到"库存"两个字就会开始拿库存解释 K 线 —— 正是本项目要避免的越界。
# 这里只改**说明文案**，不改票本身（票的数值仍来自同一份审计）。
_TECH_BASIS_OVERRIDE = {
    "库存周期": "方向由价格 5 日涨跌决定（另一维只决定阶段标签，不改变符号）",
}
# 【票名也要中性化】这张票的 book 叫「多空逻辑(库存周期)」，名字里就带"库存" ——
# 只改说明文案不够，技术分析师照样会被这四个字引到产业数据上。
# 完整票名仍保留在 /api/quant/verdict 的审计里（本模块只改**渲染给技术分析师的标题**）。
_TECH_BOOK_OVERRIDE = {"库存周期": "多空逻辑(周期阶段)"}


def _fmt_vote(v: dict[str, Any], tech: bool = False) -> str:
    """一张票 → 一行。**带上强度与可信度**，否则读的人不知道它有多硬。"""
    book = str(v.get("book") or "?")
    bias = str(v.get("bias") or "?")
    s = v.get("strength")
    c = v.get("confidence")
    tail = ""
    if s is not None and c is not None:
        tail = f"（强度 {_n(s, 2)} / 可信 {_n(c, 2)}）"
    basis = str(v.get("strength_basis") or "").strip()
    if tech:
        for key, alt in _TECH_BASIS_OVERRIDE.items():
            if key in book:
                basis = alt
                book = _TECH_BOOK_OVERRIDE.get(key, book)
                break
    if basis:
        return f"    · {book}：{bias}{tail} —— {basis}"
    return f"    · {book}：{bias}{tail}"


def _axis_line(axes: dict[str, Any], key: str) -> str | None:
    a = (axes or {}).get(key) or {}
    if not a:
        return None
    d = a.get("dir")
    c = a.get("conv")
    sc = a.get("score")
    if d is None:
        return None
    return (f"◆ {_AXIS_CN.get(key, key)}轴：方向 {d:+.2f} × 证据量 {_n(c, 2)} "
            f"= {sc:+.2f} → {_sign_cn(float(sc or 0))}（{int(a.get('n') or 0)} 张票）")


def _orphan_line(votes: list[dict[str, Any]]) -> str | None:
    """归不到任何轴的票 —— 必须外显，不许静默消失。

    【为什么两个视图都要挂这一行】视图按 `axis` 过滤，若某张票的 axis 既不在
      TECH_AXES 也不在 FUND_AXES（例如四象退回 `composite`、或以后新增了一根轴忘了
      登记），它会**两个视图都收不到**，而总分只表现为"少了点分量"，谁都不会报错。
      这与 10-19 那批哑票是同一种失败。
    """
    import vote_semantics as vs

    known = set(TECH_AXES) | set(FUND_AXES)
    # ⚠ 只算**方向票**。非方向票本来就合法地"不归属任何轴"——典型例子是四象在
    #   估值与驱动相反时给出的「套利/对冲(适合反套)」：它明确说"别做单边"，
    #   其两个分量已分别计入基差轴与库存轴，票本身不该再有方向轴。
    #   第一版没有这个过滤，RB 实测把这张票误报成"数据缺口"。
    bad = [str(v.get("book")) for v in votes or ()
           if v.get("axis") not in known and vs.sign(v.get("bias")) != 0]
    if not bad:
        return None
    return ("◆ ⚠ 有 %d 张票归不到任何轴（已在两个视图中隐去，请当作**数据缺口**处理）：%s"
            % (len(bad), "、".join(bad)))


def _head(title: str, scope: str) -> list[str]:
    return [f"【{title}（确定性注入｜{scope}）】"]


# ---------------------------------------------------------------------------
# 计算（一次算全，三个视图共用）
# ---------------------------------------------------------------------------
def _build_payload(sym: str) -> tuple[dict[str, Any], dict[str, str]]:
    """按 `/api/quant/verdict` 的同一批取数装配 payload。

    【为什么不用 fund_module._fund_vote_inputs】它走 `batch=True` 通道，缓存键与页面
      （`/api/quant/verdict` 走默认通道）不同 ⇒ 同一品种会被拉两次，且可能出现
      "页面显示的数和报告里的数不一致"。这里刻意与页面走**同一条取数路径**。
    """
    import quant_module as q

    errs: dict[str, str] = {}
    meta = q._meta(sym)

    bars: list[dict[str, Any]] = []
    try:
        # 【2026-10-22】必须走 `_daily_bars_cached`（内存 5min + 磁盘定稿缓存），
        #   不能直接 `_load_daily_bars` —— 后者每次冷启动实拉 5.4~8.7s（天勤短连接），
        #   而本函数是技术分析师那个"图里第一个节点"的必经路径。实测绕过缓存时
        #   `_compute` 冷启动 16.17s，接上磁盘缓存后 9.89s（且其中 6.8s 仍是实拉，
        #   说明当时确实绕过了；改完应只剩 ~3s）。
        #   ⚠ 该函数返回的是**共享 list**，下游只读不写（`pd.DataFrame(bars)` 是拷贝）。
        bars = q._daily_bars_cached(sym, 250)
    except Exception as exc:  # noqa: BLE001
        errs["bars"] = f"{type(exc).__name__}: {exc}"[:80]

    basis_rows: list[dict[str, Any]] = []
    try:
        b = q._fetch_basis(sym, 180)
        if b.get("ok"):
            basis_rows = b.get("rows") or []
        else:
            errs["basis"] = str(b.get("error") or "取数失败")[:80]
    except Exception as exc:  # noqa: BLE001
        errs["basis"] = f"{type(exc).__name__}: {exc}"[:80]
    basis_rates = [r["basis_rate"] for r in basis_rows if r.get("basis_rate") is not None]

    inv_rows: list[dict[str, Any]] = []
    try:
        iv = q._fetch_inventory(sym, 180)
        if iv.get("ok"):
            inv_rows = iv.get("rows") or []
        else:
            errs["inventory"] = str(iv.get("error") or "取数失败")[:80]
    except Exception as exc:  # noqa: BLE001
        errs["inventory"] = f"{type(exc).__name__}: {exc}"[:80]
    inv_series = [r["qty"] for r in inv_rows if r.get("qty") is not None]
    pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
    inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2) if len(pos) >= 4 and pos[-4] else None

    term: dict[str, Any] = {}
    try:
        t = q._fetch_term_structure(sym)
        if t.get("ok"):
            term = t
        else:
            errs["term"] = str(t.get("error") or "取数失败")[:80]
    except Exception as exc:  # noqa: BLE001
        errs["term"] = f"{type(exc).__name__}: {exc}"[:80]

    profit_series: list[float] = []
    profit_label = ""
    profit_cur = profit_low = profit_high = None
    try:
        pf = q._fetch_profit(sym)
        if pf.get("ok") and pf.get("rows"):
            rows = pf["rows"]
            # 多口径时取"样本最长"的那一条做代表（与 verdict 路由同一口径）
            by_label: dict[str, list[float]] = {}
            for r in rows:
                lab = str(r.get("label") or "")
                if r.get("profit") is not None:
                    by_label.setdefault(lab, []).append(float(r["profit"]))
            if by_label:
                profit_label = max(by_label, key=lambda k: len(by_label[k]))
                profit_series = by_label[profit_label]
                profit_cur = profit_series[-1]
                profit_low, profit_high = min(profit_series), max(profit_series)
        else:
            errs["profit"] = str(pf.get("error") or "无覆盖")[:80]
    except Exception as exc:  # noqa: BLE001
        errs["profit"] = f"{type(exc).__name__}: {exc}"[:80]

    inv_pct = None
    if pos:
        cur = inv_series[-1] if isinstance(inv_series[-1], (int, float)) else None
        if cur is not None:
            inv_pct = round(sum(1 for x in pos if x <= cur) / len(pos), 4)

    payload = {
        "variety": sym, "name": meta.get("name", sym), "unit": meta.get("unit", ""),
        "bars": bars, "basis_rate_series": basis_rates, "inv_series": inv_series,
        "inv_chg_pct": inv_chg, "term": term, "basis_rows": basis_rows, "inv_pct": inv_pct,
        "data_years": round(len(bars) / 244.0, 2),
        "profit_series": profit_series, "profit_label": profit_label,
        "profit_cur": profit_cur, "profit_low": profit_low, "profit_high": profit_high,
    }
    return payload, errs


def _strategy_block(sym: str) -> dict[str, Any] | None:
    """三指标策略（库存 × 利润 × 基差 → 四类策略）。

    判定全部复用 `fund_module` 的既有实现（双口径库存 `_inv_dual`、利润汇总
    `_profit_summary`、映射函数 `_strategy_verdict`）——**不在这里另写一套阈值**，
    否则页面与报告会给出两个不同的"策略名"。
    """
    import fund_module as f
    import quant_module as q

    inv = q._fetch_inventory(sym, 180)
    ck_rows = (inv.get("rows") or []) if inv.get("ok") else []
    dual = q._inv_dual(sym, ck_rows, 180, inv.get("source", "") if inv.get("ok") else "")
    prof = f._profit_summary(sym)

    basis_side = "不明"
    b = q._fetch_basis(sym, 60)
    if b.get("ok") and b.get("rows"):
        vals = [r["basis"] for r in b["rows"] if r.get("basis") is not None]
        if vals:
            basis_side = "贴水" if vals[-1] > 0 else ("升水" if vals[-1] < 0 else "平水")

    st = f._strategy_verdict(dual.get("grade", "不明"), prof.get("grade", "不明"), basis_side)
    st.update({"inv_grade": dual.get("grade", "不明"), "inv_mode": dual.get("mode", ""),
               "prof_grade": prof.get("grade", "不明"), "basis_side": basis_side})
    return st


def _compute(sym: str) -> dict[str, Any]:
    """算一次快照。异常一律内部吞掉（缺哪节标哪节），不向外抛。"""
    import quant_verdict as qv

    payload, errs = _build_payload(sym)
    snap: dict[str, Any] = {"code": sym, "errs": errs, "payload_ok": bool(payload.get("bars"))}

    # 技术快照（页面「技术面量化」用的同一函数）
    try:
        import quant_module as q

        ts = q._technical_snapshot(sym, 250)
        snap["tech_snapshot"] = ts if ts.get("ok") else {}
        if not ts.get("ok"):
            errs["tech_snapshot"] = str(ts.get("error") or "")[:80]
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:80]
        errs["tech_snapshot"] = error
        logger.warning("分析视图-技术快照失败(%s): %s", sym, error)

    # 五书研判（方向票 + 按轴加权；**这里不融合，只拆开供两边各取所需**）
    try:
        out = qv.build_verdict(payload)
        sd = ((out.get("stages") or {}).get("direction")) or {}
        snap["votes"] = sd.get("votes") or []
        snap["weighted"] = sd.get("weighted") or {}
        snap["verdict"] = sd.get("verdict")
        snap["verdict_desc"] = sd.get("desc")
        snap["geometry"] = sd.get("geometry") or {}
        snap["adxr"] = sd.get("adxr") or {}
        snap["extended"] = (out.get("stages") or {}).get("extended") or {}
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:80]
        errs["verdict"] = error
        logger.warning("分析视图-研判失败(%s): %s", sym, error)

    # 三指标策略（基本面页的主结论）
    try:
        snap["strategy"] = _strategy_block(sym)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:80]
        errs["strategy"] = error
        logger.warning("分析视图-三指标策略失败(%s): %s", sym, error)

    # 虚实盘比（仓单折手 vs 单边持仓）
    try:
        import fund_module as f

        snap["vr"] = f._vr_ratio(sym)
    except Exception as exc:  # noqa: BLE001
        errs["vr"] = f"{type(exc).__name__}: {exc}"[:80]

    return snap


def _snapshot(symbol: str, *, blocking: bool = False) -> dict[str, Any]:
    """带缓存 + 单飞 + 超时降级的快照读取。超时返回 {}（调用方据此输出 ""）。

    【blocking】仅预热用。`warm()` 跑在后台线程里，若它也走 8s 超时，预热会在
      实测算完之前就返回 —— 等于没预热（实测 warm 8.0s 返回时缓存为空）。
      ⇒ 预热线程无限等，正常分析路径仍然只等 WAIT_SECONDS。
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}

    now = time.time()
    hit = _SNAP.get(sym)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    with _LOCK:
        ev = _EVENTS.get(sym)
        owner = ev is None
        if owner:
            ev = threading.Event()
            _EVENTS[sym] = ev

    if not owner:
        ev.wait(WAIT_SECONDS)
        hit = _SNAP.get(sym)
        return hit[1] if hit else {}

    def _work() -> None:
        try:
            _SNAP[sym] = (time.time(), _compute(sym))
        except Exception as exc:  # noqa: BLE001 - 兜底: 绝不让后台线程把异常留给 Event
            logger.warning("分析视图-快照构建失败(%s): %s", sym, exc)
            _SNAP[sym] = (time.time(), {"code": sym, "errs": {"fatal": str(exc)[:80]}})
        finally:
            with _LOCK:
                _EVENTS.pop(sym, None)
            ev.set()

    threading.Thread(target=_work, name=f"views-{sym}", daemon=True).start()
    if not blocking:
        if not ev.wait(WAIT_SECONDS):
            # 【可观测性】超时以前是完全静默的 —— 线上注入块缺席，日志里查不到任何痕迹。
            logger.warning(
                "分析视图-首调超时(%s): 等待 %.1fs 未算完，本次不注入（后台续算，下次分析命中）",
                sym, WAIT_SECONDS,
            )
    elif not ev.wait(CACHE_TTL):
        logger.warning("分析视图-预热超时(%s) 超过 %ss", sym, CACHE_TTL)
    hit = _SNAP.get(sym)
    return hit[1] if hit else {}


def _pending(symbol: str) -> bool:
    """该品种是否仍有在途计算。

    【为什么需要】`_snapshot` 返回 `{}` 有两种完全不同的含义：
      · 还在算（超时）  → 本次是「取数失败」，必须说出来；
      · 算完但没有数据  → 是「确定无覆盖」，返回 "" 不注入即可。
    三态纪律不允许把这两者混成一个空串。
    """
    with _LOCK:
        return (symbol or "").strip().upper() in _EVENTS


def _timeout_block(title: str, symbol: str, scope: str) -> str:
    """超时降级块：显式告诉分析师「这一节缺席」，而不是让它以为「本来就没有」。"""
    return "\n".join([
        f"【{title} · {symbol}（确定性注入｜仅{scope}）】",
        f"◆ ⚠ 本次取数超时（等待 {WAIT_SECONDS:.0f}s 未算完），**{scope}结论未纳入**。",
        "  这不是「无信号」，也不是「中性」——是本该有的这一节缺席。",
        "  请在报告里如实写「本次无量化结论」，不要用中性顶替；需要时调工具自取原始数据。",
        "  （后台仍在计算，同品种下次分析会命中缓存。）",
    ])


# ---------------------------------------------------------------------------
# 视图 ①：技术分析师
# ---------------------------------------------------------------------------
def technical_view(symbol: str) -> str:
    """价格轴视图 → 技术分析师。失败/超时 → ""。"""
    snap = _snapshot(symbol)
    votes = [v for v in (snap.get("votes") or []) if v.get("axis") in TECH_AXES]
    ts = snap.get("tech_snapshot") or {}
    if not votes and not ts:
        # 超时 ≠ 无数据：超时要写明「缺席」，无数据才静默不注入（三态纪律）
        return _timeout_block("技术面量化", snap.get("code") or symbol, "价格轴") if _pending(symbol) else ""

    head = _head(f"技术面量化 · {snap.get('code', symbol)}", "仅价格轴")
    lines: list[str] = []

    if ts:
        ind = ts.get("indicators") or {}
        date_s = str(ts.get("date") or "")[:10]
        nb = len((ts.get("series") or {}).get("date") or [])
        lines.append(f"◆ 数据：日线 {nb or '—'} 根（截至 {date_s or '—'}）"
                     f" · 最新价 {_n(ts.get('latest'))}")
        lines.append(f"◆ 趋势：{ts.get('ma_state') or '—'} · MA5 {_n(ind.get('ma5'))} / "
                     f"MA10 {_n(ind.get('ma10'))} / MA20 {_n(ind.get('ma20'))} / "
                     f"MA60 {_n(ind.get('ma60'))}")
        lines.append(f"◆ 动量：RSI14 {_n(ind.get('rsi14'))} · MACD柱 {_n(ind.get('macd_bar'))} · "
                     f"20日动量 {_n(ind.get('mom20_pct'))}%")
        lines.append(f"◆ 波动：ATR14 {_n(ind.get('atr14'))} · 年化波动 {_n(ind.get('ann_vol_pct'))}% · "
                     f"BOLL 上/中/下 {_n(ind.get('boll_up'))} / {_n(ind.get('boll_mid'))} / "
                     f"{_n(ind.get('boll_low'))}")
        lines.append(f"◆ 资金：持仓量 {_n(ind.get('oi_latest'))}（5日 {_n(ind.get('oi_chg5_pct'))}%）")
        lines.append(f"◆ 量化计分：{ts.get('score')}（-5~+5，quant 面板口径；**不是**本报告结论）")

    if votes:
        import vote_semantics as vs

        n_dir = sum(1 for v in votes if vs.sign(v.get("bias")) != 0)
        lines.append(f"◆ 价格轴票（{len(votes)} 张，其中方向票 {n_dir} 张；"
                     f"其余为中性/非方向票，不参与计票）：")
        lines.extend(_fmt_vote(v, tech=True) for v in votes)
        ax = _axis_line(snap.get("weighted") or {}, "price")
        if ax:
            lines.append(ax)

    geo = snap.get("geometry") or {}
    if geo.get("double_break"):
        lines.append("◆ ⚠ 硬闸门：双线同破 → **不做逆势方向**（该否决权在最终研判里已生效）")

    errs = snap.get("errs") or {}
    bad = [k for k in ("bars", "tech_snapshot") if errs.get(k)]
    if bad:
        lines.append("◆ ⚠ 取数异常：" + "、".join(f"{k}({errs[k]})" for k in bad))

    orphan = _orphan_line(snap.get("votes") or [])
    if orphan:
        lines.append(orphan)

    lines.append("⚠ 本块**只含价格轴**。基差/库存/利润属于基本面分析师——请勿用本块推翻产业逻辑，"
                 "也勿据库存解释 K 线。若你认为价格与产业背离，请明确指出「分歧」，不要替对方下结论。")
    return "\n".join(head + lines)


# ---------------------------------------------------------------------------
# 视图 ②：基本面分析师
# ---------------------------------------------------------------------------
def fundamental_view(symbol: str) -> str:
    """基差/库存/利润三轴视图 → 基本面分析师。失败/超时 → ""。"""
    snap = _snapshot(symbol)
    votes = [v for v in (snap.get("votes") or []) if v.get("axis") in FUND_AXES]
    st = snap.get("strategy") or {}
    if not votes and not st:
        return (_timeout_block("基本面量化", snap.get("code") or symbol, "基差/库存/利润三轴")
                if _pending(symbol) else "")

    head = _head(f"基本面量化 · {snap.get('code', symbol)}", "基差/库存/利润三轴")
    lines: list[str] = []

    if st:
        act = st.get("action") or "—"
        reason = str(st.get("reason") or "").strip()
        lines.append(f"◆ 三指标策略：库存 {st.get('inv_grade', '—')} + 利润 {st.get('prof_grade', '—')} "
                     f"+ 基差 {st.get('basis_side', '—')} → **{act}**")
        if reason:
            lines.append(f"    {reason}")
        if st.get("inv_mode"):
            lines.append(f"    库存口径：{st['inv_mode']}")

    if votes:
        import vote_semantics as vs

        n_dir = sum(1 for v in votes if vs.sign(v.get("bias")) != 0)
        lines.append(f"◆ 产业轴票（{len(votes)} 张，其中方向票 {n_dir} 张）：")
        lines.extend(_fmt_vote(v) for v in votes)
        for key in FUND_AXES:
            ax = _axis_line(snap.get("weighted") or {}, key)
            if ax:
                lines.append(ax)

    # 【结构性结论】四象在估值/驱动相反时给「套利/对冲(适合反套)」——不是方向票，
    # 但它是**基本面最有价值的一类结论**（明确说"别做单边"）。不写出来，分析师就只
    # 看到两根轴方向打架，不知道为什么。
    struct = [v for v in (snap.get("votes") or [])
              if v.get("axis") not in FUND_AXES and v.get("axis_parts")]
    if struct:
        lines.append("◆ 结构性结论（不对应单边方向）：")
        for v in struct:
            lines.append(f"    · {v.get('book')}：{v.get('bias')}"
                         "（其估值/驱动分量已分别计入基差轴与库存轴）")

    vr = snap.get("vr") or {}
    if vr.get("ok"):
        lines.append(f"◆ 虚实盘比：{_n(vr.get('ratio'), 2)}（虚盘/实盘）"
                     f" · 实盘 {_n(vr.get('lots'))} 手 · 单边持仓 {_n(vr.get('one_side_oi'))} 手"
                     f" · 仓单分位 {_n((vr.get('ck_pct') or 0) * 100, 0)}%")

    errs = snap.get("errs") or {}
    bad = [k for k in ("basis", "inventory", "profit", "term", "strategy") if errs.get(k)]
    if bad:
        lines.append("◆ ⚠ 取数异常（该维度本次不参与判断）："
                     + "、".join(f"{k}" for k in bad))

    orphan = _orphan_line(snap.get("votes") or [])
    if orphan:
        lines.append(orphan)

    lines.append("⚠ 本块**只含产业与估值轴**。价格趋势属于技术分析师——请勿据此推断入场时点；"
                 "若产业看多而盘面走弱，请如实写「产业未获盘面确认」，不要替技术面下结论。"
                 "库存为周频（发布有滞后），不要用它支撑 ≤3 个交易日的判断。")
    return "\n".join(head + lines)


# ---------------------------------------------------------------------------
# 视图 ③：研究经理（统一裁决）
# ---------------------------------------------------------------------------
def reconcile_view(symbol: str) -> str:
    """两方结构化结论对照 + 裁决纪律 → 研究经理（辩论之后、下单之前）。

    【为什么放在这里】用户的要求是"先分发、后面再统一总结"。研究经理本来就是
      "把多空辩论评成一个投资计划"的节点，是事实上的裁决位；在这里给它**受控的
      结构化对照**（不是两边原文，也不是一个总分），比在聚合层提前融合更可追溯。
    """
    snap = _snapshot(symbol)
    votes = snap.get("votes") or []
    if not votes:
        return ""

    w = snap.get("weighted") or {}
    axes = w.get("axes") or {}

    def _side(keys: tuple[str, ...]) -> tuple[float, int]:
        sc = 0.0
        n = 0
        for k in keys:
            a = axes.get(k) or {}
            sc += float(a.get("score") or 0.0)
            n += int(a.get("n") or 0)
        return sc, n

    def _total(keys: tuple[str, ...]) -> int:
        return sum(1 for v in votes if v.get("axis") in keys)

    tech_sc, tech_n = _side(TECH_AXES)
    fund_sc, fund_n = _side(FUND_AXES)
    # 两方各自"每轴平均"，否则票多的一方天然占优（这正是净票的老毛病）
    tech_avg = tech_sc / max(1, len(TECH_AXES))
    fund_avg = fund_sc / max(1, len(FUND_AXES))

    agree = (tech_avg > 0.05 and fund_avg > 0.05) or (tech_avg < -0.05 and fund_avg < -0.05)
    both_flat = abs(tech_avg) <= 0.05 and abs(fund_avg) <= 0.05
    conflict = (not agree) and (not both_flat)

    head = _head(f"两方结论对照 · {snap.get('code', symbol)}", "供最终裁决")
    lines = [
        f"◆ 技术面（价格轴）：{_sc(tech_avg)} → {_sign_cn(tech_avg)}"
        f"（{_total(TECH_AXES)} 张票 / {tech_n} 个轴条目）",
        f"◆ 基本面（基差/库存/利润）："
        f"基差 {_sc(axes.get('basis', {}).get('score'))} / "
        f"库存 {_sc(axes.get('inventory', {}).get('score'))} / "
        f"利润 {_sc(axes.get('profit', {}).get('score'))}"
        f" ⇒ 三轴均值 {_sc(fund_avg)} → {_sign_cn(fund_avg)}"
        f"（{_total(FUND_AXES)} 张票 / {fund_n} 个轴条目）",
        f"◆ 一致性：{'同向' if agree else ('双方均无方向' if both_flat else '**方向相反**')}",
    ]
    if w.get("gated"):
        lines.append(f"◆ 硬闸门已触发：{','.join(w.get('gates') or [])} → 不得给逆势方向")

    if conflict:
        lines += [
            "◆ 裁决纪律（对 21 品种 / 2020-2026 / 26,000+ 条样本的回测结论，必须遵守）：",
            "    1) **不得直接选边**。18 组「谁赢」组合经日期聚簇 + 非重叠抽样 + Bonferroni 检验"
            "**无一通过**，禁止写「基本面优先」「技术面优先」这类硬规则。",
            "    2) 输出「区间 + 触发条件 + 失效条件 + 时间止损」，让读者能验证，而不是只拿到一个方向。",
            "    3) 若一定要给倾向，只能写「条件倾向」：明确在什么条件下偏向哪一方、什么条件下失效。",
        ]
    elif agree:
        lines += [
            "◆ 裁决纪律：两边同向 → 可给单边方向，置信度上调一档；"
            "但必须写明**失效条件**（哪一条数据反转会使结论作废）。",
        ]
    else:
        lines += [
            "◆ 裁决纪律：双方均无方向 → 不要硬凑方向；输出「观望 + 需要看到什么才动手」。",
        ]

    lines += [
        "⚠ 缺失处理：任何一方若取数失败或无覆盖，务必写「该维度无数据」；"
        "**不得用「中性」顶替缺失**——那会把「不知道」伪装成「没有倾向」。",
        "⚠ 频率纪律：库存为周频（发布有滞后），不能支撑 ≤3 个交易日的判断；"
        "技术面只在 ≤20 日窗口作为确认器，不作为产业方向的否决者。",
    ]
    return "\n".join(head + lines)


# ---------------------------------------------------------------------------
# 预热 / 清理
# ---------------------------------------------------------------------------
def warm(symbol: str) -> None:
    """预热三个视图（后台线程调用；阻塞直到算完，结果进缓存）。

    【为什么必须预热】冷启动实测 9.6s，而视图是在**构造提示**时同步读取的；
      不预热则首次分析会走超时分支、拿不到任何量化结论（正是 P0 国君四源踩过的坑）。
    """
    _snapshot(symbol, blocking=True)


def clear_cache() -> None:
    """清空进程内缓存（测试/排查用）。"""
    with _LOCK:
        _SNAP.clear()
        _EVENTS.clear()
