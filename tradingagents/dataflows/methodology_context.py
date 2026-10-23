"""methodology_context.py — 五书方法论「关键读数」确定性注入块(非 @tool)。

【模块角色】(2026-09-29 新增) 把用户级技能 `futures-quant-workflow`(5 本方法论书的
合流编排)及其 5 个子技能里**可确定性计算**的读数, 在分析师节点构造提示时
**确定性注入 system prompt**, 让分析正文真正用上体系化方法论。

【为什么必须注入而不是做成 @tool】
  工具调用由 LLM 自主决策, 不保证发生 —— 上一版技术面 prompt 里完全没有方法论内容
  (趋势线/拐点线/分界点A/库存周期 命中 0 处), 导致分析正文只能写通用技术分析(MA/MACD/RSI),
  用户看到的就是"比较简洁"。确定性注入保证读数 100% 到位且不占工具轮数。
  下钻(换周期/更长历史/其它指标)仍走各自的 @tool。

【双通道设计】(对齐 skills/analyst-context-injection 的核心主张)
  通道 A(确定性计算): quant_methodology.py 纯函数算出读数 → 本模块渲染成提示块
  通道 B(LLM 分析): 本块拼进 system prompt → 分析师据此写体系化推理
  本模块 = 把通道 A 的结果送进通道 B 的桥。

【与 ⑧卡片的一致性】取数一律复用 quant_module 的 `_load_daily_bars` / `_fetch_basis` /
  `_fetch_inventory` 与 quant_verdict 的 `_rsi_series`, 保证注入块与「多体系方法论研判」
  卡片显示同一套数字(避免两处算法漂移)。

【只注入该用的分析师】(关键纪律, 见 SKILL.md)
  技术面 ← 鹿希武几何 + 墨菲 ADXR/背离/突破过滤
  基本面 ← 多空逻辑估值驱动 + 库存/基差序列 + Carver 成本-速度上限
  宏观   ← 跨书冲突提示 + 成本上限(轻量)
  产业数据不注入技术面/宏观, 否则模型会拿库存数据解释 K 线形态。

【失败纪律】任何异常 → 返回 ""(静默降级, 绝不影响分析主线);
  数据不足 → 渲染成显式缺口而非省略, 让模型知道"这里没有数据", 不许编造。
"""
from __future__ import annotations

import logging  # 【调用包】注入失败降级告警
import time  # 【调用包】进程内 TTL 缓存时间戳
from typing import Any

logger = logging.getLogger(__name__)

# 【变量】进程内 TTL 缓存: 同一品种 5 分钟内不重复取数(分析管线会连调三个分析师)
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 300


def _cached(key: str, builder) -> str:
    """带 TTL 的进程内缓存包装; builder 抛异常时返回 ""(降级为无注入)。"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        val = builder() or ""
    except Exception as e:  # noqa: BLE001 - 注入失败绝不影响分析主线
        logger.warning("方法论注入块构建失败(%s): %s", key, e)
        val = ""
    _CACHE[key] = (now, val)
    return val


def _nz(v: Any, dash: str = "未识别") -> str:
    """None/空 → 人类可读占位(不输出 None 字样)。"""
    if v is None or v == "":
        return dash
    return str(v)


def _num(v: Any, nd: int = 2) -> str:
    """数值格式化; 非数值原样返回。"""
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return _nz(v)


# ══════════════════════════════════════════════════════════════════════════
# 技术面注入块 — 鹿希武几何 + 墨菲指标
# ══════════════════════════════════════════════════════════════════════════
def _build_technical(symbol: str) -> str:
    """技术面分析师用: 鹿希武双线/A点 + 墨菲 ADXR/RSI背离。"""
    import quant_methodology as qm  # 【调用模块】方法论纯函数库
    from quant_module import _daily_bars_cached  # 【调用函数】复用后复权日线(同 ⑧卡片口径)

    # 【2026-10-22 收口】必须走缓存层。本块在技术分析师节点里**排在价格轴视图之前**
    #   执行，原先两处各自 `_load_daily_bars` ⇒ 同一份日线被实拉两遍
    #   （cProfile 实测 `_load_daily_bars` 调用 2 次、累计 6.819s，缓存层 0 次）。
    bars = _daily_bars_cached(symbol, 250)
    if len(bars) < 30:
        return (f"【方法论读数 · 技术面】{symbol}: 日线仅 {len(bars)} 根(需 ≥30), "
                f"方法论读数不可算 —— 请如实说明「结构点不足」, 不要估一个数。\n")

    lines: list[str] = [
        "【方法论读数 · 技术面】(五书技能确定性注入, 零工具成本, 优先阅读)",
        f"数据源: quant_methodology 纯函数实时计算({symbol} 本地后复权日线 {len(bars)} 根)。",
        "权重规则: 本块与公开接口/研报覆盖同一指标同一时段时, 以本块为准(同口径确定性计算)。",
        "",
    ]

    # ── 鹿希武几何三件套 ──
    geo: dict = {}
    try:
        geo = qm.analyze_trend_geometry(bars, 3)
    except Exception as e:  # noqa: BLE001
        logger.warning("趋势几何计算失败(%s): %s", symbol, e)
    if geo.get("ok"):
        d = geo.get("direction")
        lines.append(f"◆ 趋势几何(鹿希武 cap-trendline-drawing / cap-pivot-line / cap-breakout-validity)")
        lines.append(f"  方向判定: {_nz(d)} | 最新收盘: {_num(geo.get('last_close'))}")
        if geo.get("trendline") is not None or geo.get("pivotline") is not None:
            lines.append(f"  趋势线: {_num(geo.get('trendline'))} | 拐点线: {_num(geo.get('pivotline'))} "
                         f"| 双线同破: {'是' if geo.get('double_break') else '否'}")
            lines.append("  判据: 只破一条=趋势「即将」转变; 双线同破=趋势「已经」结束(不用 3% 阈值)")
        else:
            lines.append("  双线未构成(取点不足或不穿越条件不满足) → 双线判定降级, 如实说明")
        pa = geo.get("point_a") or {}
        if pa:
            lines.append(f"  分界点A: {_num(pa.get('price'))} ({_nz(pa.get('date'))}) — "
                         f"⚠️ N3 缺口: ZigZag 近似且**系统性偏早**, 仅作参考不作硬信号")
        sw = geo.get("swings") or []
        if sw:
            lines.append(f"  有效摆动点: {len(sw)} 个 (末点 {_nz(sw[-1].get('date'))} "
                         f"{_nz(sw[-1].get('kind'))}@{_num(sw[-1].get('price'))})")
    else:
        lines.append(f"◆ 趋势几何(鹿希武): 不可算 —— {_nz(geo.get('reason'))}")

    # ── 墨菲 ADXR 趋势性 ──
    try:
        adx = qm.compute_adx_adxr(bars)
        if adx.get("ok"):
            lines.append("")
            lines.append("◆ 趋势性筛选(墨菲 cap-trading-systems)")
            lines.append(f"  ADXR={_num(adx.get('adxr'), 1)} ({_nz(adx.get('level'))}) | "
                         f"ADX={_num(adx.get('adx'), 1)} | +DI={_num(adx.get('plus_di'), 1)} / "
                         f"-DI={_num(adx.get('minus_di'), 1)}")
            if adx.get("tradable") is False:
                lines.append("  ⚠️ ADXR<20 → 无趋势, **不用趋势跟随系统**(墨菲口径)")
    except Exception as e:  # noqa: BLE001
        logger.warning("ADXR 计算失败(%s): %s", symbol, e)

    # ── 墨菲 RSI 背离 ──
    try:
        from quant_verdict import _rsi_series

        rsi_ser = _rsi_series(bars, 14)
        if rsi_ser:
            div = qm.rsi_divergence(bars, rsi_ser, geo.get("swings") or None)
            if div.get("ok"):
                lines.append("")
                lines.append("◆ 摆动背离(墨菲 cap-oscillators-divergence)")
                lines.append(f"  背离去向: {_nz(div.get('divergence'))} | 强度: {_nz(div.get('strength'))}")
                lines.append(f"  {_nz(div.get('desc'))}")
    except Exception as e:  # noqa: BLE001
        logger.warning("RSI 背离计算失败(%s): %s", symbol, e)

    # ── 鹿希武周期绑定(同一时刻不同周期可给出相反方向) ──
    try:
        pb = qm.period_binding(bars, 20)
        if pb.get("ok"):
            lines.append("")
            lines.append("◆ 周期绑定(鹿希武 cap-trend-definition)")
            lines.append(f"  短(5日)={_nz(pb.get('short'))} / 中(10日)={_nz(pb.get('mid'))} / "
                         f"长(20日)={_nz(pb.get('long'))} | 多周期矛盾: "
                         f"{'⚠️ 是' if pb.get('conflict') else '否'}")
            lines.append(f"  {_nz(pb.get('desc'))}")
    except Exception as e:  # noqa: BLE001
        logger.warning("周期绑定计算失败(%s): %s", symbol, e)

    # ── 鹿希武排除法三态裁决(方向唯一性) ──
    try:
        # broke_point_a 传 None: 几何层无法可靠确认是否已破 A(N3 缺口),
        # 未确认 → 排除法返回「暂不操作」, 正是作者要的三态之一。
        de = qm.direction_exclusion(geo.get("direction"), bool(geo.get("double_break")), None)
        if de.get("ok"):
            lines.append("")
            lines.append("◆ 方向排除法裁决(鹿希武 cap-direction-exclusion)")
            lines.append(f"  状态: {_nz(de.get('state'))} → 动作: **{_nz(de.get('action'))}**")
            lines.append(f"  {_nz(de.get('desc'))}")
    except Exception as e:  # noqa: BLE001
        logger.warning("排除法裁决计算失败(%s): %s", symbol, e)

    # ── 墨菲均线双线系统(cap-moving-averages) ──
    try:
        ma = qm.ma_crossover_system(bars)
        if ma.get("ok"):
            lines.append("")
            lines.append("◆ 均线双线系统(墨菲 cap-moving-averages)")
            lines.append(f"  参数 {ma.get('fast')}/{ma.get('slow')} | MA{ma.get('fast')}="
                         f"{_num(ma.get('ma_fast'))} / MA{ma.get('slow')}={_num(ma.get('ma_slow'))} → "
                         f"{_nz(ma.get('state'))}")
            lines.append("  " + ("最近交叉: " + _nz(ma.get("cross")) + f"({ma.get('cross_bars_ago')} 根前)"
                                 if ma.get("cross") else "近 60 根内无交叉"))
            _man = ma.get("notes") or []
            lines.append("  ⚠️ " + _nz((_man + ["", ""])[0]) + " " + _nz((_man + ["", ""])[1]))
    except Exception as e:  # noqa: BLE001
        logger.warning("均线系统计算失败(%s): %s", symbol, e)

    # ── 墨菲量仓验证(cap-volume-open-interest) ──
    try:
        voi = qm.volume_open_interest(bars)
        if voi.get("ok"):
            lines.append("")
            lines.append("◆ 量仓验证(墨菲 cap-volume-open-interest)")
            lines.append(f"  价格 {_num(voi.get('px_chg_pct'))}% / 量 {_num(voi.get('vol_chg_pct'))}% / "
                         f"持仓 {_num(voi.get('oi_chg_pct'))}%(5 日) → {_nz(voi.get('read'))}")
            lines.append("  权重口径: 价格 5 : 量 3 : 持仓 2, 三者均为**验证性**指标(不独立产生信号)。")
            lines.append("  ⚠️ 本读数**未做季节性修正**(需自建 5 年均线基准); 中国持仓**次日公布**。")
            if voi.get("warning"):
                lines.append(f"  {voi['warning']}")
    except Exception as e:  # noqa: BLE001
        logger.warning("量仓验证计算失败(%s): %s", symbol, e)

    # ── 墨菲趋势结构与支撑阻挡(cap-trend-structure) ──
    try:
        tst = qm.trend_structure(bars, 3)
        if tst.get("ok"):
            lines.append("")
            lines.append("◆ 趋势结构与支撑阻挡(墨菲 cap-trend-structure)")
            lines.append(f"  结构: {_nz(tst.get('direction'))} | 支撑(前低) {_num(tst.get('support'))} / "
                         f"阻挡(前高) {_num(tst.get('resistance'))}"
                         + (f" | 上升趋势线外推 {_num(tst.get('uptrend_line'))}"
                            if tst.get("uptrend_line") is not None else ""))
            if tst.get("role_swap"):
                lines.append(f"  {tst['role_swap']}")
            lines.append("  判据: 支撑=前低 / 阻挡=前高; 被**决定性穿越**后角色互换"
                         "(重要水平 10%、短线 3-5%)。")
    except Exception as e:  # noqa: BLE001
        logger.warning("趋势结构计算失败(%s): %s", symbol, e)

    lines.append("")
    lines.append("⚠️ 边界(引用须点明): 鹿希武三处未验证缺口 —— N1 角度标尺不可跨软件移植 / "
                 "N2 盈利目标区间宽度是斐波那契主档 2.8 倍、无对照样本 / N3 分界点A 过早判定系统性偏低。")
    lines.append("⚠️ 突破口径冲突: 本块用鹿希武「双线几何」; 若你另用墨菲「3% 穿越 + 2 天」, "
                 "**两套并列陈述、标明来源, 禁止取平均或混用**。")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════
# 基本面注入块 — 多空逻辑 + Carver 成本上限
# ══════════════════════════════════════════════════════════════════════════
def _build_fundamental(symbol: str) -> str:
    """基本面分析师用: 多空逻辑估值驱动二维四象 + 库存/基差序列 + Carver 成本-速度上限。"""
    import quant_methodology as qm
    from quant_module import (_fetch_basis, _fetch_inventory, _fetch_term_structure,
                              _daily_bars_cached)

    lines: list[str] = [
        "【方法论读数 · 基本面】(五书技能确定性注入, 零工具成本, 优先阅读)",
        f"数据源: quant_methodology 纯函数实时计算({symbol})。",
        "权重规则: 本块与公开接口/研报覆盖同一指标同一时段时, 以本块为准(同口径确定性计算)。",
        "",
    ]

    basis_rates: list[float] = []
    inv_series: list[float] = []
    inv_chg = None
    try:
        b = _fetch_basis(symbol, 180)
        if b.get("ok"):
            basis_rates = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
    except Exception as e:  # noqa: BLE001
        logger.warning("基差取数失败(%s): %s", symbol, e)
    try:
        inv = _fetch_inventory(symbol, 180)
        if inv.get("ok"):
            inv_series = [r["qty"] for r in inv["rows"] if r.get("qty") is not None]
        pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
        if len(pos) >= 4 and pos[-4]:
            inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2)
    except Exception as e:  # noqa: BLE001
        logger.warning("库存取数失败(%s): %s", symbol, e)

    lines.append("◆ 估值+驱动二维四象(多空逻辑 cap-value-drive)")
    if len(basis_rates) >= 10 and len(inv_series) >= 10:
        try:
            vd = qm.value_drive_matrix(basis_rates, inv_series, inv_chg)
            if vd.get("ok"):
                lines.append(f"  估值={_num(vd.get('value'))}(基差率) | 驱动={_num(vd.get('drive'))} | "
                             f"象限={_nz(vd.get('quadrant'))} | 建议={_nz(vd.get('advice'))}")
                lines.append(f"  ⚠️ {_nz(vd.get('note'))}")
            else:
                lines.append(f"  不可算 —— {_nz(vd.get('reason'))}")
        except Exception as e:  # noqa: BLE001
            logger.warning("二维四象计算失败(%s): %s", symbol, e)
            lines.append("  计算异常, 已降级(如实说明)")
    else:
        lines.append(f"  不可算 —— 基差率 {len(basis_rates)} 点 / 库存 {len(inv_series)} 点 "
                     f"(各需 ≥10)。**不得凭印象编造象限**, 如实说明数据不足。")

    # ── 期限结构 regime(cap-term-structure / cap-term-structure-warrant) ──
    lines.append("")
    lines.append("◆ 期限结构(多空逻辑 cap-term-structure)")
    try:
        t = _fetch_term_structure(symbol)
        if t.get("ok"):
            tr = qm.term_structure_regime(t.get("rows") or [])
            if tr.get("ok"):
                lines.append(f"  结构: {_nz(tr.get('shape'))} | 近月 {_nz(tr.get('near'))} → "
                             f"远月 {_nz(tr.get('far'))} (价差 {_num(tr.get('spread'))}, "
                             f"{_num(tr.get('spread_pct'))}%)")
                lines.append(f"  现货含义: {_nz(tr.get('spot_meaning'))} → 方向倾向 {_nz(tr.get('bias'))}")
                lines.append(f"  ⚠️ {_nz((tr.get('notes') or [''])[0])}")
            else:
                lines.append(f"  不可算 —— {_nz(tr.get('reason'))}")
        else:
            lines.append(f"  未取到跨月报价 —— {_nz(t.get('error'))}(如实说明, 不得编造结构)")
    except Exception as e:  # noqa: BLE001
        logger.warning("期限结构取数失败(%s): %s", symbol, e)
        lines.append("  取数异常, 已降级(如实说明)")

    # ── 库存周期四阶段(cap-inventory-cycle) ──
    lines.append("")
    lines.append("◆ 库存周期四阶段(多空逻辑 cap-inventory-cycle)")
    try:
        bars = _daily_bars_cached(symbol, 180)  # 【收口 2026-10-22】走缓存层
        px_series = [float(b["close"]) for b in bars if b.get("close") is not None]
        icp = qm.inventory_cycle_phase(inv_series, px_series, window=5)
        if icp.get("ok"):
            lines.append(f"  阶段: {_nz(icp.get('phase'))} | 库存{_nz(icp.get('inv_dir'))}"
                         f"{_num(icp.get('inv_chg_pct'))}% / 价格{_nz(icp.get('price_dir'))}"
                         f"{_num(icp.get('price_chg_pct'))}%")
            rank = icp.get("inv_pct_rank")
            lines.append(f"  {_nz(icp.get('bias'))}" +
                         (f" 库存处于近序列 {rank}% 分位。" if rank is not None else ""))
            lines.append(f"  ⚠️ {_nz((icp.get('notes') or [''])[0])}")
        else:
            lines.append(f"  不可算 —— {_nz(icp.get('reason'))}(如实说明, 不得猜阶段)")
    except Exception as e:  # noqa: BLE001
        logger.warning("库存周期阶段计算失败(%s): %s", symbol, e)
        lines.append("  计算异常, 已降级(如实说明)")

    lines.append("")
    lines.append("◆ 库存/基差序列概况(多空逻辑 cap-inventory-cycle / cap-basis-calculation)")
    lines.append(f"  基差率序列: {len(basis_rates)} 点" +
                 (f" | 最新 {_num(basis_rates[-1], 3)}" if basis_rates else " (无)"))
    lines.append(f"  库存序列: {len(inv_series)} 点" +
                 (f" | 近期变化 {_num(inv_chg)}%" if inv_chg is not None else " | 变化不可算"))
    lines.append("  ⚠️ 库存看**速度/方向**而非绝对值; 四阶段是品种态定位, 不是择时信号。")

    lines.append("")
    lines.append("◆ 成本-速度上限(Carver cap-cost-speed)")
    try:
        cs = qm.cost_speed_limit(0.35, None)
        if cs.get("ok"):
            lines.append(f"  预期税前 SR=0.35 → 年换手上限 ≈ {_num(cs.get('max_turnover'), 1)} 次 "
                         f"({_nz(cs.get('tier'))})")
            lines.append("  判据: 年换手×每手成本 > 预期税前 SR 的 1/3 → 不配资本; 不信任回测 SR>2")
    except Exception as e:  # noqa: BLE001
        logger.warning("成本上限计算失败(%s): %s", symbol, e)

    # ── 折盘面口径审计(多空逻辑 cap-basis-calculation) ──
    # 【为什么给基本面而不是技术面】这是产业口径问题(现货折盘面系数/品质升贴水),
    #   不是 K 线问题; 注入技术面会让模型拿"现货怎么折算"解释形态。
    lines.append("")
    lines.append("◆ 基差折盘面口径(多空逻辑 cap-basis-calculation)")
    try:
        _brows = [r for r in (b.get("rows") if b.get("ok") else []) or []
                  if r.get("spot_price") and r.get("futures_price") is not None and r.get("basis") is not None]
        if _brows:
            _l = _brows[-1]
            _sp, _fp, _bs = float(_l["spot_price"]), float(_l["futures_price"]), float(_l["basis"])
            _imp = (_fp + _bs) / _sp if _sp else None
            lines.append(f"  最新 {_nz(_l.get('date'))}: 现货 {_num(_sp)} / 期货 {_num(_fp)} / "
                         f"基差 {_num(_bs)} → 反推折盘面系数 ≈ {_num(_imp, 4)}")
            if _imp is not None and abs(_imp - 1.0) <= 0.005:
                lines.append("  ⚠️ 反推系数 ≈ 1.00 → 本平台基差是**未折盘面**的原始相减口径; "
                             "比不同品种的升贴水幅度时**须比基差率而非绝对值**(卡原文 A2-2), "
                             "水平值不可直接采信。")
            else:
                lines.append("  反推系数偏离 1.00 → 已含某种折算/品质升贴水, 具体口径待核。")
        else:
            lines.append("  不可算 —— 该品种无现货指数口径(无原始现货/期货行), 如实说明不编造。")
    except Exception as e:  # noqa: BLE001
        logger.warning("基差口径审计失败(%s): %s", symbol, e)
        lines.append("  计算异常, 已降级(如实说明)")

    # ── 跨期套利四逻辑(多空逻辑 cap-calendar-spread) ──
    lines.append("")
    lines.append("◆ 跨期套利四逻辑(多空逻辑 cap-calendar-spread)")
    try:
        _term_rows = (t.get("rows") if t.get("ok") else []) if isinstance(t, dict) else []
        if _term_rows:
            _inv = inv_series
            _pos = [v for v in _inv if isinstance(v, (int, float)) and v > 0]
            _ipct = (sum(1 for x in _pos if x <= float(_inv[-1])) / len(_pos)) \
                if _pos and isinstance(_inv[-1], (int, float)) else None
            _cb = float(_brows[-1]["basis"]) if (b.get("ok") and b.get("rows")
                                                 and b["rows"][-1].get("basis") is not None) else None
            _cs = qm.calendar_spread(_term_rows, inventory_pct=_ipct, warrant_pct=None,
                                     holding_cost=None, basis=_cb)
            if _cs.get("ok"):
                lines.append(f"  月差 {_num(_cs.get('month_spread'))}({_nz(_cs.get('structure'))}) | "
                             f"共振票 {_cs.get('tally')} → {_nz(_cs.get('conclusion'))}")
                for _v in (_cs.get("votes") or []):
                    lines.append(f"    · {_v.get('logic')}: {_v.get('lean')} —— {_v.get('detail')}")
                lines.append("  ⚠️ **库存/仓单本平台同源(交易所注册仓单)** → ①仓单与③库存只投一票(按分位), "
                             "避免同一序列两票制造**假共振**; 成本逻辑因无持仓成本数据未投。")
                lines.append("  ⚠️ 四条逻辑**共振时胜率更高**(卡原文) —— 勿把四票平均成一个模糊结论。")
            else:
                lines.append(f"  不可算 —— {_nz(_cs.get('reason'))}")
        else:
            lines.append("  不可算 —— 未取到跨月合约报价, 月差不可算(如实说明)")
    except Exception as e:  # noqa: BLE001
        logger.warning("跨期套利计算失败(%s): %s", symbol, e)
        lines.append("  计算异常, 已降级(如实说明)")

    # ── 跨品种套利五逻辑(多空逻辑 cap-cross-commodity-spread) ──
    lines.append("")
    lines.append("◆ 跨品种套利五逻辑(多空逻辑 cap-cross-commodity-spread)")
    try:
        from quant_module import _daily_bars_cached as _ldb  # 【收口 2026-10-22】

        _pairs = qm.cross_pair_of(symbol)
        if _pairs:
            _p0 = _pairs[0]
            _leg_code = _p0["leg"]
            _leg_meta = None
            try:
                from quant_module import _meta as _m

                _leg_meta = _m(_leg_code)
            except Exception:  # noqa: BLE001 - 元数据缺失只影响文案
                pass
            _lbars = _ldb(_leg_code, 30)
            _leg_px = float(_lbars[-1]["close"]) if _lbars else None
            _leg_term = None
            try:
                from quant_module import _fetch_term_structure as _fts

                _lt = _fts(_leg_code)
                if _lt.get("ok"):
                    _nr, _fr = (_lt.get("near") or {}), (_lt.get("far") or {})
                    _leg_term = "back" if float(_fr.get("price") or 0) < float(_nr.get("price") or 0) else "contango"
            except Exception:  # noqa: BLE001
                pass
            lines.append(f"  登记对腿: {_leg_code}"
                         f"({(_leg_meta or {}).get('name', _leg_code)}, 关系={_p0['relation']})")
            lines.append(f"  配对依据: {_p0['note']}")
            lines.append(f"  对腿结构: {_nz(_leg_term)} / 价格 {_num(_leg_px)}")
            lines.append(f"  手数配比按**货值匹配**(卡原文): 需两侧合约乘数; 本块未带乘数 → "
                         f"落地时按 a·m·x = b·n·y 自算, 不得按 1:1。")
            lines.append("  ⚠️ 只机械化第 ⑤ 条(期限结构对冲: 多 back 空 contango, 无需云止损); "
                         "①产业利润 ②伴生 ③替代 ④宏观 依赖产业判断, 逐条自检不自动出结论。")
        else:
            lines.append("  本品种**无登记配对**(函数库未收录) → 不虚构对腿; 如需跨品种请显式指定。")
    except Exception as e:  # noqa: BLE001
        logger.warning("跨品种套利计算失败(%s): %s", symbol, e)
        lines.append("  计算异常, 已降级(如实说明)")

    # ── Carver 收缩阶梯 + 估计可靠性(cap-shrinkage-ladder / cap-estimate-reliability) ──
    # 【边界】这是"元参数纪律"(怎么收缩估计), 不是某个品种的现货读数 → 归基本面/组合侧。
    lines.append("")
    lines.append("◆ 估计收缩纪律(Carver cap-shrinkage-ladder / cap-estimate-reliability)")
    try:
        _dy = round(len(bars) / 244.0, 2) if bars else None
        _sh = qm.shrinkage_ladder(_dy) if _dy else {"ok": False}
        if _sh.get("ok"):
            lines.append(f"  样本 {_sh.get('data_years')} 年({_nz(_sh.get('band'))}) → "
                         f"SR 收缩 **{_sh.get('sr_shrinkage')}** / 相关收缩 **{_sh.get('corr_shrinkage')}**"
                         f"(0=用估计值, 1=完全退向等权)")
            lines.append("  ⚠️ 样本年限按**本地 K 线长度**估算(一年 244 交易日) → 只反映「看得见多久」, "
                         "非真实回测样本; 两个轴**分开设**收缩量, 不可共用一个系数。")
        _rel = qm.estimate_reliability()
        lines.append(f"  可靠性判据: SR 预测误差 ≈ 随机数据的 {_rel.get('sr_ratio')}× / "
                     f"相关性 ≈ {_rel.get('corr_ratio')}× → **多收缩相关、少收缩均值**。")
    except Exception as e:  # noqa: BLE001
        logger.warning("收缩阶梯计算失败(%s): %s", symbol, e)

    lines.append("")
    lines.append("⚠️ 资金管理层级(引用须分清): 组合层限额(墨菲五限额)与单笔层预算(鹿希武 30%/10 次)"
                 "是**层级关系非替代** —— 组合波动率定总敞口 → 单品种分配 → 单笔止损距离反推手数。")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════
# 宏观注入块(轻量)— 跨书冲突提示 + 成本上限
# ══════════════════════════════════════════════════════════════════════════
def _build_macro(symbol: str) -> str:
    """宏观/政策分析师用: 轻量块 —— 主要给跨书冲突处置纪律与估值驱动现状。"""
    import quant_methodology as qm
    from quant_module import _fetch_basis, _fetch_inventory

    lines: list[str] = [
        "【方法论读数 · 宏观口径】(五书技能确定性注入, 零工具成本)",
        f"数据源: quant_methodology 纯函数实时计算({symbol})。",
        "",
    ]
    try:
        b = _fetch_basis(symbol, 180)
        inv = _fetch_inventory(symbol, 180)
        br = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None] if b.get("ok") else []
        ivs = [r["qty"] for r in inv["rows"] if r.get("qty") is not None] if inv.get("ok") else []
        pos = [v for v in ivs if isinstance(v, (int, float)) and v > 0]
        chg = round((pos[-1] / pos[-4] - 1) * 100, 2) if len(pos) >= 4 and pos[-4] else None
        if len(br) >= 10 and len(ivs) >= 10:
            vd = qm.value_drive_matrix(br, ivs, chg)
            if vd.get("ok"):
                lines.append(f"◆ 估值+驱动(多空逻辑 cap-value-drive): {_nz(vd.get('quadrant'))} → "
                             f"{_nz(vd.get('advice'))}")
                lines.append("  ⚠️ 二维四象是品种分类工具, **不是择时工具** —— 入场时机仍需技术面。")
        else:
            lines.append("◆ 估值+驱动: 基差/库存序列不足 10 点, 不可算(如实说明, 不得编造)")
    except Exception as e:  # noqa: BLE001
        logger.warning("宏观块取数失败(%s): %s", symbol, e)
        lines.append("◆ 估值+驱动: 取数异常, 已降级(如实说明)")

    lines.append("")
    lines.append("⚠️ 跨书冲突处置纪律(宏观事件常同时触发多书结论): 同一问题不同书口径不同 → "
                 "**并列陈述两套结论并标明来源, 禁止取平均、禁止混用**。")
    lines.append("  典型: 突破有效性 墨菲「3%+2天」 vs 鹿希武「双线几何」; 止损距离 墨菲(支撑位下方)"
                 " vs 鹿希武(唯一B点) vs Carver(波动率定标) —— 同一笔单可差数倍。")
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════
# 对外接口(与 variety_db_context / research_macro_context 同签名风格)
# ══════════════════════════════════════════════════════════════════════════
def technical_methodology_context(symbol: str) -> str:
    """技术面分析师方法论注入块; 失败/无数据返回 ""(静默降级)。"""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return _cached(f"tech:{s}", lambda: _build_technical(s))


def fundamental_methodology_context(symbol: str) -> str:
    """基本面分析师方法论注入块; 失败/无数据返回 ""(静默降级)。"""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return _cached(f"fund:{s}", lambda: _build_fundamental(s))


def macro_methodology_context(symbol: str) -> str:
    """宏观分析师方法论注入块(轻量); 失败/无数据返回 ""(静默降级)。"""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return _cached(f"macro:{s}", lambda: _build_macro(s))


def clear_cache() -> None:
    """清空进程内缓存(测试用)。"""
    _CACHE.clear()
