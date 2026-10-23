"""quant_verdict.py — 多体系方法论研判中枢(把 quant_methodology 的纯函数组装成四段式输出)

【模块角色】(2026-09-28 新增) 方案 B「研判中枢」的组装层:
从 quant_module 取数据(日线/基差/仓单/期限结构) → 调 quant_methodology 的纯函数
→ 按 `futures-quant-workflow/SKILL.md` 的**固定读序**输出四段式研判。

【固定读序(顺序不可颠倒, 后一步依赖前一步)】
  ① 方向 → ② 入场 → ③ 风控 → ④ 成本 → ⑤ 校验

【三条连读契约(缺一条结论就不完整)】
  - 止损 = 价位 + 金额      (luxiwu.cap-stop-loss + luxiwu.cap-money-management)
  - 盈亏比 = 分子 + 分母    (luxiwu.cap-profit-target + luxiwu.cap-stop-loss)
  - 净期望 = 毛期望 − 成本  (任一信号卡 + harris.cap-execution-costs)

【冲突处置】同一问题不同书口径不同 → **并列陈述, 禁止混用**(不取平均、不混用)。

【四段式输出契约(缺段视为未完成)】
  1. direction: 唯一方向, 或明确「暂不操作」(禁止「可能上涨」这类模糊表述)
  2. entry:     价位 + 触发条件
  3. risk:      止损价位 + 单笔最大亏损金额 + 建议手数(三件套齐)
  4. evidence:  用了哪几张卡 + 已知缺口(鹿希武线须点明 N1/N2/N3)

【为什么单独成文件】quant_module.py 已 1000+ 行; 研判逻辑与数据获取职责不同,
分开便于单独测纯逻辑(只需注入 dict, 不打网络)。
"""

from __future__ import annotations

import logging  # 【调用包】降级告警
from typing import Any, Sequence  # 【调用包】类型标注

import quant_methodology as qm  # 【调用模块】方法论纯函数库
import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）
import vote_aggregate as vag  # 【调用模块】按数据轴聚合 → 加权总信号(L1b)


def _m01(x: Any) -> float:
    """把强度夹到 [0,1]；非数值 / None → 0。

    【为什么必须夹】强度要参与下游加权，任何一个异常值都会把总分带飞——
      最现实的例子是库存变化 +1855%（小基数失真），不夹就会让那一票的强度爆表。
    """
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _signed(x: Any, denom: float = 3.0) -> float:
    """带符号的强度：s·m ∈ [-1,1]。供四象按轴拆分时使用。

    【参数 denom】原始分所在的量纲上限（四象的估值/驱动都映射到 −3~+3）。
    """
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    if v == 0:
        return 0.0
    return (1.0 if v > 0 else -1.0) * _m01(abs(v) / denom)

logger = logging.getLogger(__name__)


def _latest_positive(seq: Sequence[float]) -> float | None:
    """取序列最后一个正值(仓单序列含 0 值占位: 注销归零后重新注册)。

    【为什么需要】算库存变化必须跳过 0, 否则 `x/0` 保护会写成 0.0,
    把「暴增」误判成「持平」(项目既有坑, 见 quant_module._last_positive)。
    """
    for v in reversed(list(seq)):
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _infer_points(direction: str, geo: dict) -> dict:
    """从几何结果推 B/C/D 三点(鹿希武三件套的取点)。

    【取点口径(cap-profit-target)】
      B = 上一段推动浪起点; C = 该推动浪终点; D = 回调终点。
    【本模块近似】用 ZigZag 摆动点序列: 对上升方向取「低→高→低」为 B/C/D;
    对下降方向取「高→低→高」。取不到 → 返回空(目标不可算, **不得估一个数**)。
    """
    sw = geo.get("swings") or []
    if len(sw) < 3:
        return {}
    # 从末尾向前找合法三元组(方向匹配)
    for i in range(len(sw) - 3, -1, -1):
        a, b, c = sw[i], sw[i + 1], sw[i + 2]
        if direction == "上升" and a["kind"] == "L" and b["kind"] == "H" and c["kind"] == "L":
            return {"B": a, "C": b, "D": c}
        if direction == "下降" and a["kind"] == "H" and b["kind"] == "L" and c["kind"] == "H":
            return {"B": a, "C": b, "D": c}
    return {}


def build_verdict(payload: dict) -> dict:
    """组装多体系方法论四段式研判。

    【入参 payload(全部由调用方注入, 本函数不发网络请求)】
      - variety/name/unit:            品种基础信息
      - bars:                         [{date,open,high,low,close,volume,open_interest}]
      - basis_rate_series:            基差率序列(可空 → 二维四象降级)
      - inv_series:                   仓单/库存数量序列(可空 → 二维四象降级)
      - inv_chg_pct:                  库存近期变化 %
      - term:                         _fetch_term_structure 的结果(可空)
      - basis_latest / basis_ok:      基差现状(可空)
      - profit_series / profit_label: 利润序列与口径名(可空 → 记缺口, 胜率判断降级)
      - profit_cur / profit_low / profit_high: 利润现值与历史区间(可空则从序列自算)
      - float_pnl:                    品种浮盈浮亏(可空, 用于排除法第 3 层)
      - strategy_stats:               回测统计(可空, 用于旁路校验提示)
      - account / risk_pct / times:   资金管理参数(默认 100 万 / 30% / 10 次)
      - round_trip_fee / point_value: Harris 成本参数(可空 → 成本段降级为提示)
    【返回】{ok, variety, read_order, stages:{direction,entry,risk,cost,verify},
           verdict_text, conflicts, evidence, data_gaps}
    """
    bars = payload.get("bars") or []
    if len(bars) < 30:
        return {"ok": False, "error": f"日线数据不足({len(bars)} 根, 需 ≥30), 无法出研判"}

    variety = payload.get("variety") or ""
    name = payload.get("name") or variety
    unit = payload.get("unit") or ""
    cards: list[str] = []  # 【变量】实际用到的能力卡(供 evidence 段)
    gaps: list[str] = []  # 【变量】数据缺口(如实陈列, 绝不静默)

    # ══════════════════════════════════════════════════════════════════
    # ① 方向(鹿希武排除法三态 + 墨菲 ADXR 趋势性 + 多空逻辑估值驱动)
    # ══════════════════════════════════════════════════════════════════
    geo = qm.analyze_trend_geometry(bars, 3)  # 【变量】几何三件套(双线/A点)
    adxr = qm.compute_adx_adxr(bars)  # 【变量】墨菲 ADXR 趋势性
    swings = geo.get("swings") or []
    rsi_ser = _rsi_series(bars, 14) if len(bars) >= 30 else []
    div = qm.rsi_divergence(bars, rsi_ser, swings) if rsi_ser else {"ok": False, "reason": "RSI 未算"}
    vd = qm.value_drive_matrix(payload.get("basis_rate_series") or [],
                               payload.get("inv_series") or [],
                               payload.get("inv_chg_pct"))

    dir_votes: list[dict] = []  # 【变量】方向票(多书并列, 不取平均)

    def _nz(v, dash="未识别") -> str:
        """把 None/空值渲染成人类可读文案。

        【为什么必须做】Python 的 None 直接进 f-string 会写成字面量 "None" 渲染给用户
        (实测泄漏在「趋势线 None / 双线同破 False」)。研判输出面向决策, 缺值必须显式
        说明"未识别", 而不是抛一个无意义字面量。
        """
        return dash if v is None else str(v)

    def _yn(v) -> str:
        """布尔三态渲染: True→是 / False→否 / None→未判定(不写英文 True/False)。"""
        if v is None:
            return "未判定"
        return "是" if v is True else "否"

    if geo.get("ok"):
        cards.append(geo.get("card", ""))
        g_dir = geo.get("direction")
        bias = ("做多" if g_dir == "上升" else "做空" if g_dir == "下降" else "暂不操作")
        detail = (f"趋势线 {_nz(geo.get('trendline'))} / 拐点线 {_nz(geo.get('pivotline'))} / "
                  f"双线同破 {_yn(geo.get('double_break'))}")
        if g_dir == "上升" and geo.get("double_break"):
            bias = "暂不操作"
            detail += " —— **双线同时被破 ⇒ 按 cap-breakout-validity 上升趋势已结束**"
        elif g_dir == "下降" and geo.get("double_break"):
            bias = "暂不操作"
            detail += " —— **双线同时被破 ⇒ 下降趋势已结束**"
        dir_votes.append({"book": "鹿希武", "bias": bias, "detail": detail,
                          "basis": "几何画线(趋势线+拐点线+分界点A)",
                          "axis": "price",
                          "strength": 1.0 if bias in ("做多", "做空") else 0.0,
                          "confidence": 1.0,
                          "strength_basis": "二值方向票: 几何只判趋势成立与否, 无连续强度尺度"})
    else:
        gaps.append(f"鹿希武几何: {geo.get('reason')}")

    if adxr.get("ok"):
        cards.append(adxr.get("card", ""))
        dir_votes.append({
            "book": "墨菲", "bias": "趋势系统可用" if adxr["tradable"] else "趋势系统不可用(无趋势)",
            "detail": f"ADXR={adxr['adxr']}({adxr['level']}), +DI={adxr['plus_di']} / -DI={adxr['minus_di']}",
            "basis": "ADXR 趋势性评级(不做方向, 只判该不该用趋势系统)",
            "axis": "price",
            "strength": 0.0,
            "confidence": 1.0,
            "strength_basis": f"不进方向(只判趋势系统可用性); ADXR={adxr['adxr']} 可作 L2 的轴内权重参考",
        })
    else:
        gaps.append(f"墨菲 ADXR: {adxr.get('reason')}")

    if vd.get("ok"):
        cards.append(vd.get("card", ""))
        dir_votes.append({"book": "多空逻辑", "bias": vd["advice"],
                          "detail": f"{vd['quadrant']}（估值 {vd['value']} / 驱动 {vd['drive']}）",
                          "basis": "基差率(估值)+ 库存分位(驱动) 二维四象",
                          # ★ 它横跨两根轴(基差估值 + 库存驱动), 故单列 composite;
                          #   L2 要拆成两票才能按轴加权, 现在不拆(拆了会改 net)。
                          "axis": "composite",
                          # 强度取**较弱的那条轴**/3: 四象只按两轴符号出"单边",
                          #   实际强度应由短板决定(一条轴满格、另一条刚过零, 不该算强)。
                          "strength": _m01(min(abs(vd.get("value") or 0),
                                                abs(vd.get("drive") or 0)) / 3.0),
                          "confidence": 1.0,
                          "strength_basis": "min(|估值|,|驱动|)/3 (两轴同向才算单边, 取短板)",
                          # ★ L1b：拆轴分量（带符号强度）。由投票方在源头拆，
                          #   聚合层只消费不解释——否则 aggregate 就得反推四象的内部口径。
                          #   估值轴取负号：估值越低(越负)=越偏多。
                          "axis_parts": {
                              "basis": _signed(-(vd.get("value") or 0)),
                              "inventory": _signed(vd.get("drive") or 0),
                          }})
    else:
        gaps.append(f"多空逻辑二维四象: {vd.get('reason')}")


    # ── 期限结构 regime(多空逻辑 cap-term-structure) ──
    # 【为什么单列一票】二维四象看的是"单点基差率 + 库存分位"; 期限结构看的是**跨月曲线形态**:
    #   contango(期货升水)=现货熊市特征(高库存/低利润) → 偏空;
    #   backwardation(现货升水)=现货牛市特征(低库存/紧供给) → 偏多。
    #   二者同属多空逻辑体系但观察维度不同, 可互相印证; 背离时**并列陈述不取平均**。
    term_rows = (payload.get("term") or {}).get("rows") if payload.get("term") else None
    tsr = qm.term_structure_regime(term_rows or [])
    if tsr.get("ok"):
        cards.append(tsr.get("card", ""))
        dir_votes.append({
            "book": "多空逻辑(期限结构)", "bias": tsr["bias"],
            "detail": f"{tsr['shape']}: {tsr['near']} → {tsr['far']}"
                      f"（价差 {tsr['spread']}, {tsr['spread_pct']}%）",
            "basis": "跨月价差形态(品种态定位, 非择时信号)",
            "axis": "basis",
            "strength": _m01(abs(tsr.get("spread_pct") or 0) / 3.0),
            "confidence": 1.0,
            "strength_basis": "|跨月价差%|/3 (3% 价差视为满强度)",
        })
    else:
        gaps.append(f"多空逻辑期限结构: {tsr.get('reason')}")

    # ── 库存周期四阶段(多空逻辑 cap-inventory-cycle) ──
    # 【为什么不与二维四象合并】二维四象把库存压成一个"驱动分位", 会丢掉**库存与价格的联动方向**;
    #   四阶段(库存方向 × 价格方向)正是作者原表的判据: 被动去库/主动建库 → 偏多,
    #   被动建库/主动去库 → 偏空。"阶段不明"(两者均无明显变化)时不投票, 记为中性。
    px_series = [float(b["close"]) for b in bars if b.get("close") is not None]
    icp = qm.inventory_cycle_phase(payload.get("inv_series") or [], px_series, 5)
    _phase_bias = {"被动去库": "做多", "主动建库": "做多", "被动建库": "做空", "主动去库": "做空"}
    if icp.get("ok") and icp.get("phase") in _phase_bias:
        cards.append(icp.get("card", ""))
        rank = icp.get("inv_pct_rank")
        dir_votes.append({
            "book": "多空逻辑(库存周期)", "bias": _phase_bias[icp["phase"]],
            "detail": f"{icp['phase']}（库存{icp['inv_dir']}{icp['inv_chg_pct']}% / "
                      f"价格{icp['price_dir']}{icp['price_chg_pct']}%）"
                      + (f", 库存分位 {rank}%" if rank is not None else ""),
            "basis": "库存方向 × 价格方向 四阶段(品种态, 速度/方向优先于绝对值)",
            # ★ 归 price 轴：四阶段表里(降,涨)→多、(升,涨)→多、(升,跌)→空、(降,跌)→空，
            #   方向上等价于"价格涨→多 / 价格跌→空"，库存方向只改标签。
            "axis": "price",
            "strength": _m01(abs(icp.get("price_chg_pct") or 0) / 5.0),
            # 库存变化 >100% 多半是小基数失真(仓单 1,000→19,590 实测), 方向仍可参考但可信度降。
            "confidence": 0.4 if abs(float(icp.get("inv_chg_pct") or 0)) > 100 else 1.0,
            "strength_basis": "|价格5日变化%|/5; 库存变化>100% 记为小基数失真 → confidence 0.4",
        })
    elif icp.get("ok"):
        # 已算出但四阶段未命中(库存与价格变化均 <0.5%) → **显式披露**, 不静默略过,
        # 否则用户会以为该卡根本没跑。此时不投票(方向偏中性)。
        gaps.append(f"多空逻辑库存周期: 已计算但阶段不明(库存{icp['inv_dir']}{icp['inv_chg_pct']}% / "
                    f"价格{icp['price_dir']}{icp['price_chg_pct']}%) → 不参与方向投票")
    else:
        gaps.append(f"多空逻辑库存周期: {icp.get('reason')}")

    # ── 利润维度(2026-09-28 接入国君 processProfitData) ──
    # 【为什么单列一票】qihuo-duokong-logic 的核心是"库存+基差+**利润**"三指标; 二维四象
    #   只用基差(估值)+库存(驱动)两维, 利润是缺失的第三维。方法论文档明确:
    #   低利润 → 亏损不可持续 → 供给端收缩预期 → 偏多; 高利润 → 刺激供给增量 → 偏空。
    # 【边界】利润是**慢变量**, 只作方向票的补充/校验, 不单独定方向; 无数据时如实记缺口。
    prof_ser = payload.get("profit_series") or []
    prof_label = payload.get("profit_label") or "利润"
    if prof_ser and len(prof_ser) >= 20:
        vals = [float(v) for v in prof_ser if v is not None]
        cur = float(payload.get("profit_cur") if payload.get("profit_cur") is not None else vals[-1])
        lo = float(payload.get("profit_low") if payload.get("profit_low") is not None else min(vals))
        hi = float(payload.get("profit_high") if payload.get("profit_high") is not None else max(vals))
        span = hi - lo
        if span > 0:
            pct = (cur - lo) / span  # 【变量】利润在历史区间中的分位(0=最低, 1=最高)
            if pct <= 0.3:
                p_bias, p_txt = "做多", f"处历史低位(分位 {pct*100:.0f}%) → 亏损不可持续, 供给收缩预期"
            elif pct >= 0.7:
                p_bias, p_txt = "做空", f"处历史高位(分位 {pct*100:.0f}%) → 高利润刺激供给增量"
            else:
                p_bias, p_txt = "暂不操作", f"处历史中位(分位 {pct*100:.0f}%) → 无方向指引"
            # 环比趋势(近 5 期)作交叉说明
            trend = ""
            if len(vals) >= 6:
                prev = vals[-6]
                trend = ", 近5期↑" if cur > prev * 1.02 else (", 近5期↓" if cur < prev * 0.98 else ", 近5期→")
            cards.append("qihuo-duokong-logic.cap-inventory-basis-profit")
            dir_votes.append({"book": "多空逻辑(利润)", "bias": p_bias,
                              "detail": f"{prof_label} 现值 {cur:.0f} / 区间 [{lo:.0f}, {hi:.0f}]{trend}; {p_txt}",
                              "basis": "'库存+基差+利润' 交易系统的第三维(慢变量, 作校验用)",
                              "axis": "profit",
                              "strength": _m01(abs(pct - 0.5) / 0.5),
                              "confidence": 1.0,
                              "strength_basis": "|利润分位-0.5|/0.5 (越贴 0 或 1 越强; 0.3/0.7 只是触发阈值)"})
    elif payload.get("variety"):
        gaps.append("利润维度: 该品种无加工/进口利润数据(平台未覆盖, 胜率判断降级)")

    if div.get("ok") and div.get("divergence") != "无":
        cards.append(div.get("card", ""))
        dir_votes.append({"book": "墨菲", "bias": "反转预警" if "有效" in str(div.get("strength")) else "提示",
                          "detail": f"{div['divergence']}（{div['strength']}）{div['desc']}",
                          "basis": "RSI 背离(须在极限区)",
                          "axis": "price",
                          "strength": 0.0,
                          "confidence": 1.0,
                          "strength_basis": "预警类, 不给方向; L2 用作 price 轴的可信度折扣"})

    # 墨菲突破过滤(3% 穿越 + 2 天): **用鹿希武趋势线作关键位** —— 这正是两书口径
    # 对同一问题的正面交锋点(cap-trend-filters vs cap-breakout-validity), 必须并列。
    murphy_break = None
    level = None
    if geo.get("ok") and geo.get("trendline") is not None:
        level = geo["trendline"]
    elif swings:
        level = float(swings[-1]["price"])
    if level is not None:
        mbk_dir = "上升" if (geo.get("direction") == "上升") else "下降"
        murphy_break = qm.murphy_breakout_filter(bars, level, mbk_dir)
        if murphy_break.get("ok"):
            cards.append(murphy_break.get("card", ""))
            dir_votes.append({"book": "墨菲", "bias": "提示(突破口径对照)",
                              "detail": f"以鹿希武趋势线 {level} 为关键位: {murphy_break['desc']}",
                              "basis": "3% 穿越 + 2 天原则(与鹿希武双线口径冲突, 并列不混用)",
                              "axis": "price",
                              "strength": 0.0,
                              "confidence": 1.0,
                              "strength_basis": "口径对照, 不给方向"})

    # ── 威科夫事件(第六体系, 2026-10-13 新增) —— 【用途一: 独立并列投票】 ──
    # 【为什么用"做多/做空"而非威科夫自己的"偏多/偏空"】本项目的净票累加只看
    #   dirmap = {做多/单边做多/做空/单边做空} —— 若填"偏多", 这一票**根本不计入**,
    #   就违背了用户"每种体系分别独立并列投票"的要求。故这里做一次显式换算,
    #   而把威科夫的原始倾向(偏多/偏空/中性)如实写进 detail, 读者能看出它的强度弱于
    #   鹿希武那种明确方向票。
    wyck = qm.wyckoff_events(bars)
    if wyck.get("ok"):
        cards.append(wyck.get("card", ""))
        _wmap = {"偏多": "做多", "偏空": "做空"}
        _wbias = _wmap.get(wyck.get("bias") or "", "暂不操作")
        _wc = wyck.get("counts") or {}
        _ev2 = wyck.get("events") or []
        dir_votes.append({
            "book": "威科夫",
            "bias": _wbias,
            "detail": f"{wyck.get('bias')}（{wyck.get('bias_reason')}）"
                      + (f"；最近事件 " + "、".join(f"{x['kind']}({x['date']})" for x in _ev2[-3:])
                         if _ev2 else "")
                      + f"；吸筹侧 {_wc.get('accumulation', 0)} / 派发侧 {_wc.get('distribution', 0)} 个事件",
            "basis": "量价行为事件识别(SC/BC/AR/ST/Spring/UT/SOS/SOW)—— 只标事件不判阶段",
            "axis": "price",
            "strength": _m01(abs((_wc.get("accumulation") or 0)
                                  - (_wc.get("distribution") or 0)) / 2.0),
            # 没有事件 ≠ 中性: 是"没证据", 可信度必须低于有事件时。
            "confidence": 1.0 if _ev2 else 0.3,
            "strength_basis": "|吸筹-派发|/2; 无事件时 confidence 0.3(没证据≠中性)",
        })
    else:
        gaps.append(f"威科夫事件: {wyck.get('reason')}")

    # 结论: 只取**方向性票** —— 映射统一在 vote_semantics, 禁止在此另写一份。
    # 【2026-10-19 修复】原 dirmap 缺「偏多/偏空」⇒ 期限结构票永远静默计 0：
    #   实测 TA 9 张票只有 3 张进净票(4 张哑票)。审计随净票一起返回, 便于发现
    #   "新加了一张票却忘了登记" —— 这正是隐掉 44% 票数而不报错的根因。
    net, vote_audit = vs.net_of(dir_votes)  # 【变量】方向净票 + 计入审计
    # 【L1b】加权总信号：与 net **并列**输出, 不参与 verdict 判定。
    #   等一段时间的实盘对照后, 再决定要不要让 verdict 改看它。
    # 硬闸门: 双线同破时不做逆势方向。
    # 【为什么提到 aggregate 之前】weighted 必须与 verdict 吃**同一套**闸门——
    #   否则闸门触发时两套信号会给出相反结论(实测 TA: 净票被拦成暂不操作, 加权却还说做多)。
    #   闸门是否决权, 所以只压 direction, 不改 score。
    _hard_gates = ["double_break"] if (geo.get("ok") and geo.get("double_break")) else []
    weighted = vag.aggregate(dir_votes, gates=_hard_gates)  # 【变量】按轴加权的总信号
    if net > 0:
        final_dir, dir_desc = "做多", f"多书方向票净 {net:+d}"
    elif net < 0:
        final_dir, dir_desc = "做空", f"多书方向票净 {net:+d}"
    else:
        final_dir, dir_desc = "暂不操作", "方向票打平/全为中性"

    if _hard_gates:   # 与 weighted 共用同一份闸门结果, 不重复判定
        final_dir = "暂不操作"
        dir_desc += "；且双线同破(原趋势已结束) → 强制「暂不操作」"

    stages_dir = {
        "status": "ok",
        "verdict": final_dir,
        "desc": dir_desc,
        "votes": dir_votes,
        "vote_audit": vote_audit,  # 【变量】哪些票计入了/哪些没被认出
        "weighted": weighted,  # 【变量】加权总信号(与 net 并列, 不替代)
        "geometry": {k: geo.get(k) for k in ("direction", "trendline", "pivotline",
                                             "double_break", "point_a", "notes")} if geo.get("ok") else geo,
        "adxr": adxr,
        "conflict_note": "未指定体系时, 各体系方向票**并列陈列**, 不取平均、不混用。",
    }

    # ── 威科夫【用途二: 确认层】(2026-10-13 新增, 用户要求独立成段) ──
    # 【为什么与"独立投票"分开成两段】同一套威科夫事件有两种读法, 混在一起会语义打架:
    #   ① 独立投票: 威科夫**自己**的方向倾向(已作为一票进入上面的 votes 与净票);
    #   ② 确认层: 威科夫**裁决别人** —— 用"谁在吸筹/派发"判断其他体系的信号有没有
    #      资金面支撑(共振)还是相矛盾(背离)。
    #   两段并列展示、各自独立结论, 且确认层**不参与净票**(避免同一份证据被计两次权)。
    wyck_block: dict[str, Any] = {"ok": False, "reason": "未计算"}
    if wyck.get("ok"):
        wyck_block = {
            "ok": True,
            "vote": {
                "book": "威科夫",
                "bias_raw": wyck.get("bias"),
                "bias_as_vote": _wmap.get(wyck.get("bias") or "", "暂不操作"),
                "reason": wyck.get("bias_reason"),
                "note": "威科夫的原始倾向是「偏多/偏空/中性」(弱倾向); 为参与本项目净票累加, "
                        "上面按 偏多→做多 / 偏空→做空 做了显式换算。",
            },
            "confirm": qm.wyckoff_confirm(wyck, dir_votes),
            "events": wyck.get("events") or [],
            "counts": wyck.get("counts") or {},
            "zone": wyck.get("zone"),
            "desc": wyck.get("desc"),
            "subjectivity": wyck.get("subjectivity") or [],
            "notes": wyck.get("notes") or [],
        }
    else:
        wyck_block = {"ok": False, "reason": wyck.get("reason") or "威科夫事件不可用"}

    # ══════════════════════════════════════════════════════════════════
    # ② 入场(鹿希武: 回调 30%~95% + 关键位 K 线扳机 + 1.618 特例排除)
    # ══════════════════════════════════════════════════════════════════
    points = _infer_points(geo.get("direction") or "", geo) if geo.get("ok") else {}
    entry_price = float(bars[-1]["close"])  # 【变量】入场参考价(现价, 入场段与风控段共用)
    entry_block: dict[str, Any] = {"status": "ok" if points else "degraded"}
    if points and final_dir == "暂不操作":
        # 【纪律】cap-entry-point 第 1 步: 方向必须唯一; 若为「暂不操作」→ 停止。
        # 但仍给出**结构参考**(明确标注为条件性), 便于用户理解"若做需在哪进"。
        entry_block.update({
            "status": "blocked",
            "reason": "方向为「暂不操作」→ 按 cap-entry-point 第 1 步停止"
                      "(方向未唯一之前谈入场是本末倒置)。",
            "conditional_note": "以下为**条件性结构参考**, 非入场建议; 须先由用户选定体系把方向定唯一。",
            "points": {"B": points["B"], "C": points["C"], "D": points["D"]},
        })
        cards.append("luxiwu.cap-entry-point")
    elif points:
        B, C, D = points["B"], points["C"], points["D"]
        leg = abs(float(C["price"]) - float(B["price"]))  # 【变量】推动浪幅度
        entry_block.update({"points": {"B": B, "C": C, "D": D},
                            "leg": round(leg, 4),
                            "pullback_zone": [round(float(C["price"]) - 0.95 * leg, 4),
                                              round(float(C["price"]) - 0.30 * leg, 4)]
                            if geo["direction"] == "上升" else
                            [round(float(C["price"]) + 0.30 * leg, 4),
                             round(float(C["price"]) + 0.95 * leg, 4)],
                            "trigger": "回调落 30%~95% 区间 **且** 关键位出现 K 线反转形态"
                                       "(买入侧: 锤子线/启明星/十字星/阳包阴/刺透; 卖出侧对称)才扣扳机。",
                            "last_close": geo.get("last_close")})
        # 特例排除(cap-entry-point 第 5 步, 强制): 破双线前最后一浪 = 前第一条浪的 1.618 倍 → 不执行
        sw = geo.get("swings") or []
        first_leg = None
        if len(sw) >= 4:
            first_leg = abs(float(sw[1]["price"]) - float(sw[0]["price"]))  # 【变量】前第一条浪幅度
        ratio = (leg / first_leg) if first_leg else None
        entry_block["special_case_ratio"] = round(ratio, 3) if ratio else None
        if ratio and abs(ratio - 1.618) <= 0.15:
            entry_block["special_case_block"] = True
            entry_block["trigger"] += ("  **⚠ 特例排除命中**: 破双线前最后一浪 ≈ 前第一条浪的 "
                                       "1.618 倍 → 止损过大且错误率高, 按 cap-entry-point **不执行**。")
        else:
            entry_block["special_case_block"] = False
        entry_block["notes"] = [
            "回调区间 30%~95% 是**经验值**(缺口 N2), 原文自陈应推导但未给出分布统计。",
            "K 线形态**只在关键位有效**, 机械套用 K 线信号是作者明确列为错误的做法。",
        ]
        # 当前价 vs 回调区: 明确"是否已到入场位置"(避免用户误以为可立即入场)
        z = entry_block["pullback_zone"]
        lo_z, hi_z = min(z), max(z)
        if entry_price > hi_z:
            entry_block["zone_position"] = "当前价高于回调区"
            entry_block["notes"].append(
                f"当前价 {round(entry_price, 4)} **高于**回调区 {[lo_z, hi_z]} → "
                f"按 cap-entry-point 属「回调未到」, 应等价格回落至区间内再等扳机(追高风险)。")
        elif entry_price < lo_z:
            entry_block["zone_position"] = "当前价低于回调区"
            entry_block["notes"].append(
                f"当前价 {round(entry_price, 4)} **低于**回调区 {[lo_z, hi_z]} → "
                f"回调已超 95%(趋势可能已破), 按 cap-entry-point 不作为入场区。")
        else:
            entry_block["zone_position"] = "当前价位于回调区内"
            entry_block["notes"].append("当前价位于回调区内 → 可进入「等关键位 K 线扳机」环节。")
        cards.append("luxiwu.cap-entry-point")
    else:
        entry_block["reason"] = "有效摆动三元组(B/C/D)不可唯一识别 → 入场区间不可算, 不得估一个数"
        gaps.append("鹿希武入场: 摆动点不足以构成 B/C/D 三元组")

    # ══════════════════════════════════════════════════════════════════
    # ③ 风控(止损价位 + 单笔最大亏损金额 + 建议手数 —— 三件套齐)
    # ══════════════════════════════════════════════════════════════════
    tick = payload.get("tick")  # 【变量】最小变动单位(项目元数据无此字段 → 常为 None)
    if tick is None:
        gaps.append("最小变动单位(tick)不在品种元数据中 → 止损**未做习惯数字避让**, 请人工核对")
    # 止损位: 鹿希武口径 = 双线被破时的 B 点。
    # 【方向未定时的处置】不强行给单向止损(那是混用), 而是**双向并列**两个结构位,
    # 明确标注「须先定方向才能收敛为唯一止损」。
    stop_long = stop_short = None
    if points:
        stop_long = float(points["B"]["price"])   # 做多: 止损在推动浪起点下方(B 点)
        stop_short = float(points["C"]["price"])  # 做空: 止损在推动浪终点上方(C 点)
    elif geo.get("ok") and geo.get("point_a"):
        stop_long = stop_short = float(geo["point_a"]["price"])
        gaps.append("止损位退化为分界点 A 近似(N3 缺口: A 点判定偏早), 非鹿希武严格 B 点口径")

    stop_price = (stop_long if final_dir == "做多" else stop_short if final_dir == "做空" else None)
    # 两种"止损不可定"要分清: 方向未唯一 vs 方向已定但结构点不可识别 —— 后者是数据问题不是纪律问题
    if final_dir == "暂不操作":
        _sl_reason = ("方向尚未唯一 → 按 cap-trading-plan「定不出止损就别做」, "
                      "本模块**不强行给单向止损**; 以下是双向结构位供选定体系后收敛。")
        _sl_card = "luxiwu.cap-stop-loss"
    else:
        _sl_reason = (f"方向已定为「{final_dir}」, 但 B/C/D 结构点不可唯一识别 → "
                      f"按 cap-profit-target/cap-stop-loss 的判停点「**不得给一个感觉上的价位**」; "
                      f"需更长的 K 线或人工标注摆动点后重算。")
        _sl_card = "luxiwu.cap-stop-loss"
    sl = qm.stop_loss("做多" if final_dir == "做多" else "做空",
                      stop_price if stop_price is not None else float("nan"), tick) \
        if stop_price is not None else {
            "ok": False, "reason": _sl_reason,
            "both_sides": {"若做多_止损": stop_long, "若做空_止损": stop_short},
            "card": _sl_card,
        }
    if sl.get("ok"):
        cards.append(sl.get("card", ""))

    # 目标位(鹿希武 1.618 四公式): 同样按选定方向算; 方向未定时并列两个方向的目标。
    target = None  # 【变量】目标位(收敛后的单值)
    pt = {"ok": False, "reason": "缺 B/C/D 三点"}
    if points:
        pt = qm.profit_target(geo["direction"],
                              float(points["B"]["price"]), float(points["C"]["price"]),
                              float(points["D"]["price"]))
        if pt.get("ok"):
            target = pt["min"] if geo["direction"] == "上升" else pt["max"]
            cards.append(pt.get("card", ""))

    account = float(payload.get("account") or 1_000_000)  # 【变量】演示账户(默认 100 万)
    risk_pct = float(payload.get("risk_pct") or 0.30)  # 【变量】风险档
    times = int(payload.get("times") or 10)  # 【变量】交易次数
    point_value = float(payload.get("point_value") or 1.0)  # 【变量】每点价值
    mm = qm.money_management(account, entry_price, stop_price or entry_price,
                             target, point_value, risk_pct, times) if stop_price is not None \
        else {
            "ok": False,
            "reason": ("方向未定 → 止损未收敛为唯一值, 资金管理链按 cap-money-management "
                       "第 2 步**无法启动**(单笔额度必须除以一个确定的止损距离)。"
                       if final_dir == "暂不操作" else
                       "止损不可定 → 资金管理链无法启动(分母缺失, 盈亏比与手数都算不出)。"),
            "card": "luxiwu.cap-money-management",
        }
    if mm.get("ok"):
        cards.append(mm.get("card", ""))
    portfolio = qm.murphy_portfolio_limits(account, int(payload.get("open_positions") or 1))
    if portfolio.get("ok"):
        cards.append(portfolio.get("card", ""))

    # 交易计划三要素(鹿希武 cap-trading-plan): **顺序固定 止损 → 入市 → 目标**, 必须书面化。
    # 与上方三件套同源(止损价/入场价/目标价), 但此行给出"可否成立"的裁决与缺口清单。
    plan = qm.trading_plan(
        entry_price,
        stop_price,
        target,
        direction="买入" if final_dir == "做多" else ("卖出" if final_dir == "做空" else "待定"),
        account=account, point_value=point_value, risk_pct=risk_pct, times=times,
    )
    if plan.get("ok"):
        cards.append(plan.get("card", ""))
    elif final_dir != "暂不操作":
        # 方向未唯一时不重复报缺口(上方止损段已说明原因), 避免噪声叠加。
        gaps.append("鹿希武交易计划: 三要素中缺 " + "/".join(plan.get("missing") or []) +
                    " → 按本书铁律, 缺止损则该笔交易不成立")

    stages_risk = {
        "status": "ok" if (sl.get("ok") and mm.get("ok")) else "degraded",
        "stop_loss": sl,
        "money_management": mm,
        "portfolio_limits": portfolio,
        "target": pt,
        "trading_plan": plan,
        "contract": {"entry": round(entry_price, 4), "stop": stop_price,
                     "target": target, "point_value": point_value, "unit": unit},
        "notes": [
            "**止损 = 价位 + 金额** 是连读契约之一: 单读止损卡只有价位、单读资金卡只有金额。",
            "**盈亏比 = 分子 + 分母**: 分母来自止损距离, 没有止损就算不出盈亏比。",
            "三层资金管理是**嵌套非替代**: 组合波动率定总敞口 → 单品种分配 → 单笔止损距离反推手数。",
            "鹿希武止损上移是**离散三次许可**, 不是连续跟随; 跌破趋势线但未触 E 应继续持有。",
            "cap-trading-plan 三要素**顺序固定**: 止损 → 入市 → 目标; 定不出止损就别做。",
        ],
    }

    # ══════════════════════════════════════════════════════════════════
    # ④ 成本(净期望 = 毛期望 − 成本; Harris 补 Carver pre-cost 缺口)
    # ══════════════════════════════════════════════════════════════════
    fee = payload.get("round_trip_fee")  # 【变量】往返手续费(未提供则降级)
    slip = payload.get("slip_ticks")
    if fee is not None:
        cost = qm.harris_cost_estimate(float(fee), float(slip or 1.0), point_value,
                                       order_value=entry_price * point_value)
        cards.append(cost.get("card", ""))
    else:
        cost = {"ok": False,
                "reason": "未提供往返手续费 → 成本段降级为提示(费率须按实盘核对, 各方法论体系均不覆盖最新细则)"}
        gaps.append("Harris 成本: 未提供实盘费率, 净期望无法闭合")
    cs = qm.cost_speed_limit(float(payload.get("expected_sr") or 0.35),
                             payload.get("cost_per_trade_sr"))
    if cs.get("ok"):
        cards.append(cs.get("card", ""))
    stages_cost = {
        "status": "ok" if cost.get("ok") else "degraded",
        "execution_cost": cost,
        "cost_speed": cs,
        "net_expectancy": (f"毛期望需先减成本 {cost.get('total')}({cost.get('bps')} bps) 才是净期望"
                           if cost.get("ok") else "成本未知, 净期望未闭合"),
        "notes": ["**净期望 = 毛期望 − 成本**: 成本是减法项, 必须在**下结论之前**扣, 不能事后解释。",
                  "Carver 语料为 pre-cost 口径, Harris 是其执行侧补丁; 本模块把两者连读。",
                  "**不信任回测 SR>2**(常见根因是隐含 forward fill)。"],
    }

    # ══════════════════════════════════════════════════════════════════
    # ③b 扩展卡段(2026-09-30 第二批): 墨菲均线/量仓/趋势结构 + 多空逻辑价差 + Carver 收缩
    # 【为什么不塞进方向票】方向票的净票只累加"做多/做空"; 墨菲已有 ADXR/背离/突破三票,
    #   再把均线(本质与 ADXR 同源: 都是趋势跟随)塞进去 = 同一本书重复计票 → 会**改变结论**,
    #   那是"为了覆盖率好看而造假接入"。故本段独立呈现, 明确标注"不参与方向净票"。
    # 【为什么填充卡片清单】cards.append 是本模块"实际用到"的唯一证据口径(A 层 + HTTP 扫描
    #   读 evidence.cards_used), 不 append 就等于没接入。
    # ══════════════════════════════════════════════════════════════════
    ext: dict[str, Any] = {}

    # ── 墨菲均线双线系统(cap-moving-averages) ──
    ma = qm.ma_crossover_system(bars)
    if ma.get("ok"):
        cards.append(ma.get("card", ""))
        ext["ma_crossover"] = {
            "state": ma.get("state"), "spread": ma.get("spread"), "expanding": ma.get("expanding"),
            "cross": ma.get("cross"), "cross_bars_ago": ma.get("cross_bars_ago"),
            "ma_fast": ma.get("ma_fast"), "ma_slow": ma.get("ma_slow"),
            "params": f"{ma.get('fast')}/{ma.get('slow')}",
            "desc": ma.get("desc"), "notes": ma.get("notes"),
        }
    else:
        gaps.append(f"墨菲均线系统: {ma.get('reason')}")

    # ── 墨菲量仓验证(cap-volume-open-interest) ──
    voi = qm.volume_open_interest(bars)
    if voi.get("ok"):
        cards.append(voi.get("card", ""))
        ext["volume_open_interest"] = {
            "read": voi.get("read"), "bias": voi.get("bias"),
            "px_chg_pct": voi.get("px_chg_pct"), "vol_chg_pct": voi.get("vol_chg_pct"),
            "oi_chg_pct": voi.get("oi_chg_pct"), "vol_confirm": voi.get("vol_confirm"),
            "warning": voi.get("warning"), "desc": voi.get("desc"), "notes": voi.get("notes"),
        }
    else:
        gaps.append(f"墨菲量仓验证: {voi.get('reason')}")

    # ── 墨菲趋势结构与支撑阻挡(cap-trend-structure) ──
    tst = qm.trend_structure(bars, 3)
    if tst.get("ok"):
        cards.append(tst.get("card", ""))
        ext["trend_structure"] = {
            "direction": tst.get("direction"), "support": tst.get("support"),
            "resistance": tst.get("resistance"), "role_swap": tst.get("role_swap"),
            "uptrend_line": tst.get("uptrend_line"), "swing_count": tst.get("swing_count"),
            "desc": tst.get("desc"), "notes": tst.get("notes"),
        }
    else:
        gaps.append(f"墨菲趋势结构: {tst.get('reason')}")

    # ── 基差折盘面口径审计(cap-basis-calculation) ──
    # 【口径审计的思路】卡原文铁律是"基差 ≠ 现货 − 期货, 必须先折盘面"。平台自己的
    #   dom_basis 是不是原始相减? 用平台给的 spot/futures/basis 反推它隐含的折算系数:
    #   implied = (futures + basis) / spot。若 ≈1.00 且未标系数 → 说明平台用的是**未折盘面**
    #   口径 → 排序可比但**水平值不可直接采信**, 这正是该卡要防的错。这是可确定的算术事实,
    #   不是主观判断(故只在有原始行时给, 无原始行则如实记缺口)。
    brs = payload.get("basis_rows") or []
    latest = next((r for r in reversed(brs)
                   if qm._is_num(r.get("spot_price")) and qm._is_num(r.get("futures_price"))
                   and qm._is_num(r.get("basis"))), None)
    if latest:
        _sp, _fp, _bs = float(latest["spot_price"]), float(latest["futures_price"]), float(latest["basis"])
        bc = qm.basis_calculation(_sp, _fp, 1.0, 0.0)  # 按"未折盘面"口径复算
        implied = ((_fp + _bs) / _sp) if _sp else None
        std_ok = abs(bc["basis"] - _bs) <= max(0.01, abs(_bs) * 0.01)  # 复算是否复现平台值
        cards.append(bc.get("card", ""))
        ext["basis_calculation"] = {
            "spot": _sp, "futures": _fp, "platform_basis": _bs,
            "implied_adjust": qm._round(implied, 4) if implied is not None else None,
            "reproduced": std_ok, "date": latest.get("date"),
            "desc": (f"基差口径审计({_nz(latest.get('date'))}): 平台 现货 {qm._round(_sp)} / "
                     f"期货 {qm._round(_fp)} / 基差 {qm._round(_bs)} → "
                     f"反推折盘面系数 ≈ {qm._round(implied, 4)}"),
            "verdict": ("反推系数 ≈ 1.00 → 平台基差为**未折盘面**的原始相减口径; "
                        "跨品种比升贴水幅度时须比**基差率**而非绝对值, 水平值不可直接采信(卡原文)"
                        if (implied is not None and abs(implied - 1.0) <= 0.005)
                        else "反推系数偏离 1.00 → 平台已含某种折算/品质升贴水, 具体口径待核"),
            "notes": bc.get("notes"),
        }
    else:
        gaps.append("基差折盘面口径审计: 无原始现货/期货行(该品种无现货指数口径)")

    # ── 跨期套利四逻辑(cap-calendar-spread) ──
    _cal_basis = None
    if latest:
        _cal_basis = float(latest["basis"])
    cal = qm.calendar_spread(term_rows or [], inventory_pct=payload.get("inv_pct"),
                             warrant_pct=None, holding_cost=None, basis=_cal_basis)
    if cal.get("ok"):
        cards.append(cal.get("card", ""))
        ext["calendar_spread"] = {
            "month_spread": cal.get("month_spread"), "structure": cal.get("structure"),
            "votes": cal.get("votes"), "tally": cal.get("tally"),
            "risk_reward": cal.get("risk_reward"), "conclusion": cal.get("conclusion"),
            "desc": cal.get("desc"), "notes": cal.get("notes"),
            # 【必须披露的塌缩】平台的仓单序列(_fetch_inventory)就是交易所注册仓单 →
            #   卡的①仓单与③库存是**同一条序列**, 不能投两票, 故只投③(以分位形式)。
            "collapsed_note": "库存/仓单在平台同源(交易所注册仓单) → ①仓单与③库存只投一票(按分位), "
                              "避免同一序列两票制造**假共振**; 成本逻辑因无持仓成本数据未投。",
        }
    else:
        gaps.append(f"跨期套利四逻辑: {cal.get('reason')}")

    # ── 跨品种套利五逻辑(cap-cross-commodity-spread) ──
    _leg = payload.get("cross_leg")
    _this_struct = (cal.get("structure") if cal.get("ok") else None)
    _leg_a = (_leg if _leg else None)
    _leg_self = None
    if _this_struct:
        _leg_self = {"code": variety, "structure": _this_struct,
                     "price": entry_price, "point_value": point_value}
    cc = qm.cross_commodity_spread(_leg_self, _leg_a)
    cards.append(cc.get("card", ""))
    # 【措辞纪律】"没登记配对"与"登记了但对腿取不到数"是**两件事**, 不能都说成前者
    #   —— 那是把取数失败伪装成知识缺失(实测 PX/P 的中文名被 akshare 报价接口拒绝)。
    _registered = payload.get("pairs") or []
    _attempts = payload.get("pair_attempts") or []
    if _leg:
        _pairing = (f"登记对腿: {_leg.get('code')}（{_leg.get('relation')}）—— {_leg.get('note')}")
    elif _registered:
        _fails = "；".join(f"{a.get('leg')}({a.get('error') or '不可用'})" for a in _attempts) or "全部取数失败"
        _pairing = (f"**已登记 {len(_registered)} 个候选对腿**"
                    f"（{'、'.join(p['leg'] for p in _registered)}），但对腿期限结构**未取到** → "
                    f"{_fails}。故本次无法机械判定第⑤条; 不虚构对腿。"
                    f"（这是*取数*问题, 不是*没有*配对关系）")
    else:
        _pairing = ("本品种**无登记配对**(函数库 CROSS_PAIRS 未收录) → 仅输出四逻辑检查清单, "
                    "不虚构对腿; 如需跨品种请显式指定对腿品种。")
    ext["cross_commodity_spread"] = {
        "pick": cc.get("pick"), "lots": cc.get("lots"),
        "checklist": cc.get("checklist"), "pair_note": (_leg or {}).get("note"),
        "desc": cc.get("desc"), "notes": cc.get("notes"),
        "pairing": _pairing,
        "pair_attempts": _attempts,
    }

    # ── Carver 收缩阶梯 + 估计可靠性(cap-shrinkage-ladder / cap-estimate-reliability) ──
    _dy = payload.get("data_years")
    shl = qm.shrinkage_ladder(_dy) if _dy else {"ok": False, "reason": "未提供可用样本年限"}
    if shl.get("ok"):
        cards.append(shl.get("card", ""))
        ext["shrinkage_ladder"] = {
            "band": shl.get("band"), "sr_shrinkage": shl.get("sr_shrinkage"),
            "corr_shrinkage": shl.get("corr_shrinkage"), "data_years": shl.get("data_years"),
            "desc": shl.get("desc"), "notes": shl.get("notes"),
            "source": ("样本年限由**本地 K 线长度**估算(一年按 244 交易日); 传 ?data_years= 可覆盖。"
                       "若要按真实回测样本年限, 必须显式传入 —— 否则该阶梯只反映「看得见多久数据」。"),
        }
    else:
        gaps.append(f"Carver 收缩阶梯: {shl.get('reason')}")
    rel = qm.estimate_reliability()
    cards.append(rel.get("card", ""))
    ext["estimate_reliability"] = {
        "sr_ratio": rel.get("sr_ratio"), "corr_ratio": rel.get("corr_ratio"),
        "desc": rel.get("desc"), "notes": rel.get("notes"),
    }

    # ── 虚实盘比与逼仓识别(多空逻辑 cap-warrant-basis) ──
    _inv_s = payload.get("inv_series") or []
    _ois = [b.get("open_interest") for b in bars]
    _oi_now = _ois[-1] if _ois else None
    _oi_prev_n = next((v for v in reversed(_ois[:-1]) if qm._is_num(v)), None) if len(_ois) > 1 else None
    _w_now = _inv_s[-1] if _inv_s else None
    _w_prev = _inv_s[-2] if len(_inv_s) > 1 else None
    vrr = qm.virtual_real_ratio(_oi_now, _w_now, _oi_prev_n, _w_prev,
                                contract_size=payload.get("contract_size"), basis=_cal_basis)
    if vrr.get("ok"):
        cards.append(vrr.get("card", ""))
        ext["warrant_basis"] = {
            "virtual": vrr.get("virtual"), "ratio": vrr.get("ratio"),
            "ratio_chg_pct": vrr.get("ratio_chg_pct"), "lean": vrr.get("lean"),
            "safety_margins": vrr.get("safety_margins"), "feat": vrr.get("basis_features"),
            "desc": vrr.get("desc"), "notes": vrr.get("notes"),
        }
    else:
        gaps.append(f"虚实盘比与逼仓识别: {vrr.get('reason')}")

    # ── 行情反转三信号(多空逻辑 cap-reversal-signals) ──
    rvs = qm.reversal_signals(bars, payload.get("basis_rate_series"),
                              [v for v in _ois if qm._is_num(v)])
    if rvs.get("ok"):
        cards.append(rvs.get("card", ""))
        ext["reversal_signals"] = {
            "signals": rvs.get("signals"), "triggered": rvs.get("triggered"),
            "triggered_count": rvs.get("triggered_count"), "price_rank": rvs.get("price_rank"),
            "verdict": rvs.get("verdict"), "desc": rvs.get("desc"), "notes": rvs.get("notes"),
        }
    else:
        gaps.append(f"行情反转三信号: {rvs.get('reason')}")

    # ══════════════════════════════════════════════════════════════════
    # ⑤ 校验(旁路: 结论可不可信 —— 主观形态未经 Carver 定标不得当 alpha)
    # ══════════════════════════════════════════════════════════════════
    strat = payload.get("strategy_stats") or {}
    verify_items: list[dict] = []
    verify_items.append({
        "check": "主观规则统计定标",
        "result": "**未通过(结构性)**",
        "desc": "墨菲形态/鹿希武画线属人工识别, 未经 Carver random-data-calibration + "
                "meta-eval-protocol 校验 → **只能当注意力工具, 不得当 alpha 依据**。",
    })
    if strat.get("sharpe") is not None:
        over = float(strat["sharpe"]) > 2
        verify_items.append({
            "check": "回测 SR>2 政策",
            "result": "**可疑(须排查前视)**" if over else "通过",
            "desc": f"回测 sharpe={strat['sharpe']} → "
                    + ("SR>2 按 Carver 个人政策**不可信**, 常见根因是隐含 forward fill。"
                       if over else "未触发 SR>2 警戒线。"),
        })
    else:
        gaps.append("旁路校验: 未提供回测统计, 无法对策略 SR 做可信度核查")
    verify_items.append({
        "check": "数据缺口披露",
        "result": f"{len(gaps)} 项降级",
        "desc": "; ".join(gaps) if gaps else "无缺口, 全部卡片均以真实数据计算。",
    })
    stages_verify = {"status": "ok", "checks": verify_items,
                     "discipline": "任何「这套规则有效」的断言, 落地前必须用 Carver 的"
                                   "随机数据定标 + 元优化评估协议校验。"}

    # ══════════════════════════════════════════════════════════════════
    # 汇总: 四段式 + 冲突并列 + 依据与边界
    # ══════════════════════════════════════════════════════════════════
    entry_price_txt = round(entry_price, 4)
    stop_txt = sl.get("adjusted") if sl.get("ok") else None
    loss_txt = mm.get("steps", {}).get("2_单笔止损额度") if mm.get("ok") else None
    lots_txt = mm.get("lots") if mm.get("ok") else None

    # ② 入场段文案: 区分"可执行/被闸门拦截/不可算"三态
    if entry_block.get("status") == "ok":
        entry_txt = (f"参考价 {entry_price_txt}；回调区间 {entry_block.get('pullback_zone')} "
                     f"[{entry_block.get('zone_position')}], 需关键位 K 线扳机确认")
    elif entry_block.get("status") == "blocked":
        entry_txt = (f"*{entry_block.get('reason')}*  "
                     f"（条件性结构参考: B={entry_block['points']['B']['price']} / "
                     f"C={entry_block['points']['C']['price']} / D={entry_block['points']['D']['price']}）")
    else:
        entry_txt = f"*{entry_block.get('reason')}*"

    # ③ 风控段文案: 方向未定时呈现**双向结构位**(并列不混用)
    if stop_txt is not None:
        risk_txt = (f"止损 {stop_txt} / 单笔最大亏损 {loss_txt if loss_txt is not None else '*不可算*'}"
                    f" / 建议手数 {lots_txt if lots_txt is not None else '*不可算*'}"
                    + (f"（{mm.get('verdict')}）" if mm.get("ok") else ""))
    else:
        both = (sl.get("both_sides") or {})
        # 【2026-09-28 修复】方向未定且结构点不可识别时 both 两值均为 None →
        # 直接插值会渲染成字面量 "None"; 此处按有无值分两态陈述。
        _lv, _sv = both.get("若做多_止损"), both.get("若做空_止损")
        if _lv is None and _sv is None:
            _both_txt = "双向结构位亦不可识别（摆动点不足）→ 需人工标注摆动点或加长 K 线后重算"
        else:
            _both_txt = f"双向结构位: 若做多 {_nz(_lv)} / 若做空 {_nz(_sv)}"
        risk_txt = f"*{sl.get('reason', '止损不可定')}*  （{_both_txt}）"

    verdict_lines = [
        f"**① 方向**: {final_dir}（{dir_desc}）",
        f"**② 入场**: {entry_txt}",
        f"**③ 风控**: {risk_txt}",
        "**④ 成本**: " + (f"{cost.get('total')} 元({cost.get('bps')} bps)" if cost.get("ok")
                          else "*未提供费率, 净期望未闭合*"),
        f"**⑤ 校验**: {len([v for v in verify_items if '未通过' in v['result'] or '可疑' in v['result']])} 项需注意",
    ]
    return {
        "ok": True, "variety": variety, "name": name, "unit": unit,
        "read_order": qm.READ_ORDER,
        "stages": {"direction": stages_dir, "entry": entry_block, "risk": stages_risk,
                   "cost": stages_cost, "extended": ext, "verify": stages_verify,
                   "wyckoff": wyck_block},
        "verdict": {"direction": final_dir, "desc": dir_desc},
        "verdict_text": verdict_lines,
        "conflicts": qm.build_conflicts(geo.get("direction"), murphy_break),
        "evidence": qm.build_evidence(cards, gaps),
        "data_gaps": gaps,
        "disclaimer": "本研判由多体系方法论卡片机械计算, **不构成投资建议**; "
                      "主观形态类规则未经统计定标, 须经 Carver 校验后方可作为依据。",
    }


def _rsi_series(bars: Sequence[dict], period: int = 14) -> list[float | None]:
    """计算 RSI 序列(Wilder 平滑), 与 quant_module._technical_snapshot 同口径。

    【为什么单独实现】研判层需**整条序列**判背离, 而 snapshot 只回最新值。
    """
    closes = [float(b["close"]) for b in bars]
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    ag = sum(gains[1: period + 1]) / period  # 【变量】平均涨幅种子
    al = sum(losses[1: period + 1]) / period  # 【变量】平均跌幅种子
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out
