"""quant_methodology.py — 多体系方法论的可计算落地层(纯函数库, 无 Flask 依赖)

【模块角色】(2026-09-28 新增) 把用户级技能 `futures-quant-workflow`(5 本方法论书的合流编排)
及其 5 个子技能里**可确定性计算**的能力卡, 落成纯函数, 供 quant_module 的
`/api/quant/verdict` 组装成「四段式研判」。
(注: 上游技能内含 5 本方法论来源, 是本模块的**输入出处**;
 本模块与对应界面卡片的对外名称统一为「多体系方法论研判」。)

  方法论来源 → 子技能 → 本模块消费的卡
  ─────────────────────────────────────────────────────────────
  ① Carver    carver-systematic-futures  cap-cost-speed(成本-速度上限)
  ② 多空逻辑   qihuo-duokong-logic        cap-value-drive(估值+驱动四象) / 基差率
  ③ 墨菲       murphy-technical-analysis  cap-trading-systems(ADXR 趋势性)
                                          cap-oscillators-divergence(RSI 背离)
                                          cap-trend-filters(3% 穿越 + 2 天)
                                          cap-money-management(组合层五限额)
  ④ Harris     harris-execution           cap-execution-costs(显性 + 隐性成本)
  ⑤ 鹿希武     luxiwu-trend-trading        cap-trendline-drawing / cap-pivot-line(双线)
                                          cap-point-a(分界点 A)
                                          cap-entry-point(回调 30~95%)
                                          cap-profit-target(1.618 四公式)
                                          cap-stop-loss(唯一止损 B 点 + 三条上移许可)
                                          cap-money-management(四步资金管理链)

【核心纪律 — 全部来自 futures-quant-workflow/SKILL.md 的「串联规则」】
  1. 固定读序: 方向 → 入场 → 风控 → 成本 → 校验(顺序不可颠倒, 后一步依赖前一步)。
  2. 三条连读契约(缺一条结论就不完整):
       - 止损 = 价位 + 金额   (luxiwu.stop-loss + luxiwu.money-management)
       - 盈亏比 = 分子 + 分母 (luxiwu.profit-target + luxiwu.stop-loss)
       - 净期望 = 毛期望 − 成本(任一信号卡 + harris.execution-costs)
  3. 冲突处置: 并列陈述, **禁止混用**(不得取平均、不得混用两套口径)。
  4. 旁路校验: 主观形态类规则未经 Carver 随机数据定标**不得当 alpha 依据**。
  5. 输出四段式: 方向 / 入场 / 风控 / 依据与边界 —— 缺段视为未完成。

【边界(必须原样透传给用户)】
  - 鹿希武体系 3 处未验证缺口: N1 角度标尺不可移植(实测 7.1°→76.0°) /
    N2 盈利目标覆盖区间无对照样本 / N3 分界点 A 过早判定系统性偏低。
  - 多空逻辑全书零回测验证(作者自述 37% 胜率 3:1 赔率)。
  - 墨菲主观图表方法未经现代统计验证, 只能当注意力工具。
  - Carver 语料全为 pre-cost 口径, Harris 是其执行侧补丁。
  - 本模块各体系均不覆盖中国品种最新费率/交易制度细则。

【设计约束】
  - 纯函数, 不读写文件、不碰数据库、不发网络请求(数据由调用方注入)。
  - 缺输入 → 该步返回 {ok: False, reason}, 调用方原样陈列(不算失败)。
  - 绝不静默: 每一项降级都要有可读原因。
"""

from __future__ import annotations

import math  # 【调用包】isfinite 判断
from typing import Any, Sequence  # 【调用包】类型标注

# ══════════════════════════════════════════════════════════════════════════
# 常量: 方法论出处与边界声明(输出里原样带出, 便于用户溯源)
# ══════════════════════════════════════════════════════════════════════════

BOOKS: dict[str, str] = {  # 【变量】书名简称 → 全称(输出溯源用)
    'carver': 'Carver《系统化期货交易》',
    'duokong': 'JerryMa《期货多空逻辑》',
    'murphy': '约翰·墨菲《期货市场技术分析》',
    'harris': 'Larry Harris《Trading and Electronic Markets》',
    'luxiwu': '鹿希武《趋势交易法》',
}

# 鹿希武体系三处未验证缺口(引用其结论时必须点明, 见 SKILL.md 边界)
LUXIWU_GAPS: list[str] = [  # 【变量】强制披露的缺口清单
    'N1 角度标尺不可移植: 依赖图表纵横比, 实测同一趋势可 7.1°→76.0°, 本模块**不使用**角度判据。',
    'N2 盈利目标覆盖区间无对照样本: 30%~95% 回调与 1.618 目标区间宽度是斐波那契主档的 2.8 倍, 属经验值而非统计结论。',
    'N3 分界点 A 过早判定系统性偏低: 蒙特卡洛容差 0.5→2.0 无改善(误判率 20.0%→20.5%), 本模块 A 点仅为参考, 不作硬信号。',
]

# 通用边界(每次输出都带)
GENERAL_DISCLAIMERS: list[str] = [  # 【变量】跨书通用边界
    '多空逻辑(库存/基差/仓单)全书零回测验证, 作者自述 37% 胜率 3:1 赔率。',
    '墨菲主观图表方法未经现代统计验证, 只能当注意力工具, 不得当 alpha 依据。',
    'Carver 语料为 pre-cost 口径; 净期望必须在下结论前扣成本(Harris)。',
    '本模块引用的各方法论体系均不覆盖中国品种最新费率/涨跌停/保证金细则, 费率须按实盘核对。',
    '摆动点/趋势线/分界点 A 属人工识别环节, 本模块用 ZigZag 近似, 与人工画线可能不同。',
]


def _is_num(x: Any) -> bool:
    """数值且非 NaN/Inf。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _round(x: Any, n: int = 4) -> float | None:
    """安全舍入; 不可数 → None。"""
    return round(float(x), n) if _is_num(x) else None


def _num(v: Any, nd: int = 2, dash: str = '—') -> str:
    """渲染数值给人类看; None/不可数 → 占位符(绝不把字面量 'None' 渲染进正文)。

    【为什么本模块也要有】methodology_context 有一份同名的, 但那属于"提示块渲染层";
      本纯函数库的 desc 字段也会被直接展示(以及进 HTTP 响应的 stages.*.desc),
      同样不能泄漏 None。
    """
    if v is None or v == '':
        return dash
    try:
        return f'{float(v):.{nd}f}'
    except (TypeError, ValueError):
        return str(v)


# ══════════════════════════════════════════════════════════════════════════
# ① 墨菲: ADXR 趋势性评级(cap-trading-systems)
# ══════════════════════════════════════════════════════════════════════════
def compute_adx_adxr(bars: Sequence[dict], period: int = 14) -> dict:
    """计算 Wilder ADX / ADXR(回答「这个品种该不该用趋势系统」)。

    【公式】+DM/-DM 平滑 → TR 平滑 → +DI/-DI → DX → ADX(Wilder 平滑) →
            ADXR = (ADX_今日 + ADX_前 period 日) / 2
    【判据】墨菲 cap-trading-systems: ADXR < 20 表示**无趋势**, 不适用趋势顺应系统。
            王尔德估计趋势阶段仅占市场约 30% 时间。
    【返回】{ok, adx, adxr, plus_di, minus_di, level, tradable, desc}
            level ∈ 强趋势(≥25) / 弱趋势(20~25) / 无趋势(<20); 数据不足 → ok:False。
    【用途】截面排行里作**品种池趋势性筛选列**(SKILL.md 关键交叉验证第 3 条)。
    """
    if len(bars) < period * 2 + 5:
        return {'ok': False, 'reason': f'K 线不足({len(bars)} 根, 需 ≥{period * 2 + 5})'}
    highs = [float(b['high']) for b in bars]
    lows = [float(b['low']) for b in bars]
    closes = [float(b['close']) for b in bars]
    n = len(bars)

    plus_dm: list[float] = [0.0] * n  # 【变量】方向性运动(+DM 序列)
    minus_dm: list[float] = [0.0] * n  # 【变量】方向性运动(-DM 序列)
    tr: list[float] = [0.0] * n  # 【变量】真实波幅序列
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0  # 【变量】+DM 须大于 -DM 且为正
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0  # 【变量】-DM 同理
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    def _wilder(seq: list[float]) -> list[float]:
        """Wilder 平滑(等价 alpha=1/period 的 EWM, 前 period 项用累加做种子)。"""
        out = [0.0] * n
        out[period] = sum(seq[1: period + 1])
        for i in range(period + 1, n):
            out[i] = out[i - 1] - out[i - 1] / period + seq[i]
        return out

    tr_s = _wilder(tr)  # 【变量】TR 的 Wilder 平滑
    pdm_s = _wilder(plus_dm)  # 【变量】+DM 的 Wilder 平滑
    mdm_s = _wilder(minus_dm)  # 【变量】-DM 的 Wilder 平滑
    dx: list[float] = [0.0] * n  # 【变量】DX 序列
    plus_di = minus_di = 0.0
    for i in range(period, n):
        if tr_s[i] <= 0:
            continue
        pdi = 100 * pdm_s[i] / tr_s[i]  # 【变量】+DI
        mdi = 100 * mdm_s[i] / tr_s[i]  # 【变量】-DI
        if i == n - 1:
            plus_di, minus_di = pdi, mdi
        s = pdi + mdi
        dx[i] = 100 * abs(pdi - mdi) / s if s > 0 else 0.0
    # ADX = DX 的 Wilder 平滑(从第一个有效 DX 起累加种子)
    adx = [0.0] * n
    seed_end = 2 * period  # 【变量】ADX 种子窗口末端(不含)
    if seed_end <= n:
        adx[seed_end - 1] = sum(dx[period: seed_end]) / period
    for i in range(seed_end, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    adx_now = adx[n - 1]
    back_idx = n - 1 - period  # 【变量】period 日前的 ADX 下标
    back = adx[back_idx] if back_idx >= 0 else adx_now
    adxr = (adx_now + back) / 2  # 【变量】ADXR = (ADX + period 日前 ADX)/2

    level = '强趋势' if adxr >= 25 else ('弱趋势' if adxr >= 20 else '无趋势')
    tradable = adxr >= 20  # 【变量】是否适合趋势顺应系统
    desc = f'ADXR={adxr:.1f} → {level}。' + (
        '趋势系统可用。' if tradable else
        '按墨菲 cap-trading-systems, ADXR<20 不适用趋势顺应系统(拉锯市里趋势策略胜率显著下降)。')
    return {'ok': True, 'adx': _round(adx_now, 1), 'adxr': _round(adxr, 1),
            'plus_di': _round(plus_di, 1), 'minus_di': _round(minus_di, 1),
            'level': level, 'tradable': tradable, 'desc': desc,
            'card': 'murphy.cap-trading-systems'}


# ══════════════════════════════════════════════════════════════════════════
# ② 鹿希武: 摆动点识别(ZigZag 近似) — 双线 / A 点 / 止损 B 点的公共底座
# ══════════════════════════════════════════════════════════════════════════
def find_swings(bars: Sequence[dict], k: int = 3) -> list[dict]:
    """识别摆动高低点(局部极值): 前后各 k 根内最高/最低。

    【为什么需要】鹿希武的 趋势线 / 拐点线 / 分界点 A / 唯一止损 B 点 全部以
    「摆动点」为锚。本函数是这些卡的**公共底座**(近似人工画线的取点)。
    【返回】[{idx, date, price, kind('H'|'L')}], 按时间升序; 不足 → []。
    【局限】局部极值法对 k 敏感; 与人工画线可能不同 → 结论须标注主观性。
    """
    if len(bars) < 2 * k + 1:
        return []
    out: list[dict] = []
    for i in range(k, len(bars) - k):
        window = bars[i - k: i + k + 1]
        hi = max(float(b['high']) for b in window)
        lo = min(float(b['low']) for b in window)
        if abs(float(bars[i]['high']) - hi) < 1e-9:
            out.append({'idx': i, 'date': str(bars[i]['date'])[:10],
                        'price': _round(bars[i]['high']), 'kind': 'H'})
        elif abs(float(bars[i]['low']) - lo) < 1e-9:
            out.append({'idx': i, 'date': str(bars[i]['date'])[:10],
                        'price': _round(bars[i]['low']), 'kind': 'L'})
    return out


def analyze_trend_geometry(bars: Sequence[dict], k: int = 3) -> dict:
    """鹿希武几何三件套: 趋势方向 / 双线是否被破 / 分界点 A(参考)。

    【方法】
      1. 取最近若干摆动点(H/L 交替), 判断末段方向: 高点抬升+低点抬升→上升;
         高点下降+低点下降→下降; 混合→横盘(方向未定)。
      2. **趋势线**(cap-trendline-drawing): 上升线连「最低点与最高点**之前**的
         任意低点」(关键在「之前」, 连之后是典型错误画法); 下降线对称。
      3. **拐点线**(cap-pivot-line): 通道外延线平行平移 —— 口诀
         「要找下先画上, 要找上先画下」。
      4. **双线同破**(cap-breakout-validity): 同时破趋势线+拐点线 = 趋势结束;
         只破一条 = 即将转变。**不用 3% 阈值**(那是墨菲口径, 见冲突并列)。
      5. **分界点 A**(cap-point-a): 破双线前**最后一个调整浪**的极值点。本模块
         用最近一个反向摆动点近似, 并**强制标注 N3 缺口**。
    【返回】{ok, direction, trendline, pivotline, double_break, point_a, swings, notes}
    """
    swings = find_swings(bars, k)
    if len(swings) < 4:
        return {'ok': False, 'reason': f'摆动点不足({len(swings)} 个, 需 ≥4)', 'swings': swings}

    def _alternate(pool: list[dict]) -> list[dict]:
        """把原始摆动点折叠成 H/L 严格交替的序列(连续同类型只留更极端者)。"""
        alt: list[dict] = []
        for p in pool:
            if alt and alt[-1]['kind'] == p['kind']:
                keep = (max if p['kind'] == 'H' else min)(alt[-1]['price'], p['price'])
                alt[-1] = p if keep == p['price'] else alt[-1]
            else:
                alt.append(p)
        return alt

    # 【关键】交替折叠会「吃掉」同类型点 → 至少需要 2 高 + 2 低才能判方向;
    # 从最近 6 个起逐步向前扩窗, 直到满足(最多吃满全部摆动点)。
    alt: list[dict] = []
    for take in range(6, len(swings) + 1):
        alt = _alternate(swings[-take:])
        n_h = sum(1 for p in alt if p['kind'] == 'H')
        n_l = sum(1 for p in alt if p['kind'] == 'L')
        if n_h >= 2 and n_l >= 2:
            break
    if len(alt) < 3:
        return {'ok': False, 'reason': '摆动点未形成有效交替(可能区间过窄)', 'swings': swings}
    n_alt_h = sum(1 for p in alt if p['kind'] == 'H')
    n_alt_l = sum(1 for p in alt if p['kind'] == 'L')
    if n_alt_h < 2 or n_alt_l < 2:
        return {'ok': True, 'direction': '横盘(未定)', 'trendline': None, 'pivotline': None,
                'double_break': False, 'point_a': None, 'last_close': _round(float(bars[-1]['close'])),
                'swings': alt,
                'notes': [f'有效摆动点不足(交替后仅 {n_alt_h} 高 + {n_alt_l} 低, 判方向需各 ≥2) '
                          f'→ 按 cap-direction-exclusion 输出「暂不操作」。']}

    highs = [p for p in alt if p['kind'] == 'H']  # 【变量】交替高点
    lows = [p for p in alt if p['kind'] == 'L']  # 【变量】交替低点
    hi_rising = len(highs) >= 2 and highs[-1]['price'] > highs[-2]['price']
    lo_rising = len(lows) >= 2 and lows[-1]['price'] > lows[-2]['price']
    if hi_rising and lo_rising:
        direction = '上升'
    elif (not hi_rising) and (not lo_rising) and len(highs) >= 2 and len(lows) >= 2:
        direction = '下降'
    else:
        direction = '横盘(未定)'

    last_close = float(bars[-1]['close'])
    trendline = pivotline = None
    slope_t: float | None = None  # 【变量】趋势线斜率(拐点线须与之平行)
    double_break = False  # 【变量】双线是否同时被破
    notes: list[str] = []

    if direction == '上升':
        p_low = lows[0]  # 【变量】上升段起点低点
        p_hi = highs[-1]  # 【变量】上升段当前最高点
        prior_lows = [p for p in lows if p_low['idx'] < p['idx'] < p_hi['idx']]
        if prior_lows:
            p2 = prior_lows[-1]  # 【变量】「最高点之前」的最近一个低点(满足不穿越)
            if p2['idx'] != p_low['idx'] and p2['price'] != p_low['price']:
                slope_t = (p2['price'] - p_low['price']) / (p2['idx'] - p_low['idx'])
                trendline = _round(p_low['price'] + slope_t * (len(bars) - 1 - p_low['idx']))
            notes.append(f"上升趋势线取自低点 {p_low['date']}({p_low['price']}) 与最高点之前的"
                         f"低点 {p2['date']}({p2['price']}) —— 符合 cap-trendline-drawing 的"
                         f"「之前/之后」对错分界。")
        prior_his = [p for p in highs if p['idx'] < p_hi['idx']]
        if prior_his and slope_t is not None:
            p_h = prior_his[-1]  # 【变量】通道外延线锚点(最高点之前的高点)
            # 拐点线 = **与趋势线同斜率的平行平移**, 平移量由高点决定(口诀: 要找下先画上)
            pivotline = _round(p_h['price'] + slope_t * (len(bars) - 1 - p_h['idx']))
        if trendline is not None and pivotline is not None:
            double_break = last_close < trendline and last_close < pivotline
    elif direction == '下降':
        p_hi = highs[0]
        p_lo = lows[-1]
        prior_his = [p for p in highs if p_hi['idx'] < p['idx'] < p_lo['idx']]
        if prior_his:
            p2 = prior_his[-1]
            if p2['idx'] != p_hi['idx'] and p2['price'] != p_hi['price']:
                slope_t = (p2['price'] - p_hi['price']) / (p2['idx'] - p_hi['idx'])
                trendline = _round(p_hi['price'] + slope_t * (len(bars) - 1 - p_hi['idx']))
            notes.append(f"下降趋势线取自高点 {p_hi['date']}({p_hi['price']}) 与最低点之前的"
                         f"高点 {p2['date']}({p2['price']})。")
        prior_los = [p for p in lows if p['idx'] < p_lo['idx']]
        if prior_los and slope_t is not None:
            p_l = prior_los[-1]  # 【变量】通道外延线锚点(最低点之前的低点)
            # 拐点线 = **与趋势线同斜率的平行平移**, 平移量由低点决定(口诀: 要找上先画下)
            pivotline = _round(p_l['price'] + slope_t * (len(bars) - 1 - p_l['idx']))
        if trendline is not None and pivotline is not None:
            double_break = last_close > trendline and last_close > pivotline

    if direction != '横盘(未定)' and trendline is None:
        notes.append('趋势线未构成(取点不足或不穿越条件不满足), 双线判定降级。')

    point_a = None  # 【变量】分界点 A(参考值)
    if direction == '上升' and lows:
        point_a = {'price': lows[-1]['price'], 'date': lows[-1]['date'],
                   'meaning': '上升途中最近一个调整浪低点；跌破 → 按 cap-point-a 视为新趋势(下降)成立'}
    elif direction == '下降' and highs:
        point_a = {'price': highs[-1]['price'], 'date': highs[-1]['date'],
                   'meaning': '下降途中最近一个调整浪高点；升破 → 新趋势(上升)成立'}
    notes.append('分界点 A 为 ZigZag 近似值, 且 N3 缺口指出其**系统性偏早**, 仅作参考不作硬信号。')

    return {'ok': True, 'direction': direction, 'trendline': trendline, 'pivotline': pivotline,
            'double_break': double_break, 'point_a': point_a, 'last_close': _round(last_close),
            'swings': alt, 'notes': notes,
            'card': 'luxiwu.cap-trendline-drawing+cap-pivot-line+cap-point-a'}


# ══════════════════════════════════════════════════════════════════════════
# ③ 鹿希武: 盈利目标(1.618 四公式, cap-profit-target)
# ══════════════════════════════════════════════════════════════════════════
def profit_target(direction: str, B: float, C: float, D: float) -> dict:
    """鹿希武盈利目标四公式。

    【公式】上升: 最小 = 1.618×(C−B)+D; 最大 = 1.618×(C−B)+C
            下降: 最小 = D−1.618×(B−C); 最大 = C−1.618×(B−C)
    【取点】B=上一段推动浪起点, C=该推动浪终点, D=回调终点。三点须唯一可识别。
    【返回】{ok, min, max, span, process, note}; 三点不可用 → ok:False。
    【边界】属波浪比率外推, **不是统计期望值**; 作者未给命中率统计。
    """
    if not all(_is_num(v) for v in (B, C, D)):
        return {'ok': False, 'reason': 'B/C/D 三点缺值, 目标不可算(不得估一个数)'}
    if direction == '上升':
        if not (C > B):
            return {'ok': False, 'reason': f'上升趋势要求 C>B, 实际 B={B} C={C}'}
        leg = C - B  # 【变量】上一段推动浪幅度
        t_min = 1.618 * leg + D
        t_max = 1.618 * leg + C
        process = f'1.618×({C}−{B})+{D} = {t_min:.4f};  1.618×({C}−{B})+{C} = {t_max:.4f}'
    else:
        if not (B > C):
            return {'ok': False, 'reason': f'下降趋势要求 B>C, 实际 B={B} C={C}'}
        leg = B - C
        t_min = D - 1.618 * leg
        t_max = C - 1.618 * leg
        process = f'{D}−1.618×({B}−{C}) = {t_min:.4f};  {C}−1.618×({B}−{C}) = {t_max:.4f}'
    return {'ok': True, 'min': _round(t_min), 'max': _round(t_max),
            'span': _round(abs(t_max - t_min)), 'leg': _round(leg),
            'process': process, 'B': _round(B), 'C': _round(C), 'D': _round(D),
            'note': '两个目标仅差一个推动浪幅度——本法的信息是「目标大约在这一带」, 不是精确点位。',
            'card': 'luxiwu.cap-profit-target'}


# ══════════════════════════════════════════════════════════════════════════
# ④ 鹿希武: 唯一止损(cap-stop-loss)
# ══════════════════════════════════════════════════════════════════════════
_HABIT_DIGITS = (0, 20, 50, 80)  # 【变量】场内习惯挂单尾数(00/20/50/80)


def stop_loss(direction: str, B: float, tick: float | None = None,
              C: float | None = None, E: float | None = None,
              rebound_ratio: float | None = None, break_c: bool = False) -> dict:
    """鹿希武止损: 初始止损**唯一** = 双线被破时的 B 点; 三条上移许可。

    【初始止损】B 点(其他任何位置都不合法: 在 E 点设初始止损是明确错误)。
    【避让习惯数字】多头止损应**低于**习惯数字 6~7 个最小变动单位, 空头**高于**。
    【三条上移许可(离散, 非连续跟随)】
      许可1 突破 C 点(break_c=True) → 止损 B 上移至 E;
      许可2 自 E 回升 > (B−C) 的 0.5 倍(虽未破 C) → 可**临时**上移至 E;
      许可3 破双线后快速回升 > 最近上升浪 (E−F) 的 30% → 上移至 G(=F)。
    【未命中任何许可】止损维持原位; **跌破趋势线但未触 E → 继续持有**。
    """
    if not _is_num(B):
        return {'ok': False, 'reason': 'B 点未知 → 止损不可定(不得给感觉上的价位)'}
    is_long = direction == '做多'  # 【变量】方向(做多/做空)
    initial = float(B)
    notes: list[str] = ['初始止损**唯一**为双线被破时的 B 点; 在 E 点设初始止损属明确错误。']

    adjusted = initial  # 【变量】避让习惯数字后的最终止损价
    if _is_num(tick) and tick > 0:
        off = 6.5 * float(tick)  # 【变量】作者建议避让 6~7 个最小变动单位
        cand = initial - off if is_long else initial + off
        tail = round(cand) % 100  # 【变量】习惯数字只看最后两位
        if any(abs(tail - h) <= 2 for h in _HABIT_DIGITS):
            cand = cand - off if is_long else cand + off
            notes.append(f'原止损尾数靠近习惯挂单区(00/20/50/80), 已按 6.5 个最小变动单位'
                         f'避让 → {cand:.4f}。')
        adjusted = cand
    else:
        notes.append('未提供最小变动单位 → **未做**习惯数字避让, 请按交易所合约细则人工核对'
                     '(多头略低于、空头略高于整数位)。')

    permit = None  # 【变量】命中的上移许可
    if break_c and _is_num(C) and _is_num(E):
        permit = '许可1(突破 C 点 → B 上移至 E)'
    if permit is None and _is_num(rebound_ratio) and float(rebound_ratio) > 0.5:
        permit = '许可2(自 E 回升 > (B−C) 的 0.5 倍 → 临时上移至 E)'
    if permit is None and _is_num(rebound_ratio) and float(rebound_ratio) > 0.30:
        permit = '许可3(破双线后回升 > 最近上升浪 (E−F) 的 30% → 上移至 G)'
    if permit:
        notes.append(f'命中{permit}。注意这是**离散三次许可**, 不得实现为「持续跟随价格」。')
    else:
        notes.append('未命中任何上移许可 → 止损维持原位。特别地: 跌破趋势线但**未触 E** 时'
                     '应继续持有(只有破 E 趋势才真正被排除)。')

    return {'ok': True, 'initial': _round(initial), 'adjusted': _round(adjusted),
            'move_allowed': permit is not None, 'permit': permit,
            'tick_used': _round(tick) if _is_num(tick) else None, 'notes': notes,
            'card': 'luxiwu.cap-stop-loss'}


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 鹿希武: 资金管理四步链(cap-money-management) — 单笔风险预算层
# ══════════════════════════════════════════════════════════════════════════
def money_management(account: float, entry: float, stop: float, target: float | None,
                     point_value: float, risk_pct: float = 0.30, times: int = 10,
                     max_lots_cap: int = 500) -> dict:
    """鹿希武资金管理四步链(单品种/单笔层, **不覆盖组合层**)。

    【四步】
      1. 总风险额 = 总资金 × 风险比率(默认 30%; 新入市者 10%)。
      2. 单笔止损额度 = 总风险额 ÷ 交易次数(熟练 10 / 不熟练 20)——**硬上限**, 非建议值。
      3. 盈亏比率 = |目标−入场| ÷ |入场−止损|; **≤1 则否决交易计划**(硬闸门)。
      4. 可入市区间 = 止损价 ± 单笔额度÷每点价值; 入场价须落入该区间。
    【手数反解】手数 = 单笔止损额度 ÷ (|入场−止损| × 每点价值), 向下取整。
    【边界】**总风险 30% ≠ 用 30% 资金满杠杆入场**(作者专门警告);
            30% 是作者约定, 未经破产概率/凯利推导(缺口 N4)。
    """
    if not all(_is_num(v) for v in (account, entry, stop)) or not _is_num(point_value):
        return {'ok': False, 'reason': '账户资金/入场价/止损价/每点价值 缺任一 → 无法计算'}
    if float(account) <= 0 or float(point_value) <= 0:
        return {'ok': False, 'reason': '账户资金与每点价值须为正'}
    risk_amt = float(account) * float(risk_pct)  # 【变量】第 1 步: 总风险额
    per_trade = risk_amt / max(1, int(times))  # 【变量】第 2 步: 单笔止损额度(硬上限)
    dist = abs(float(entry) - float(stop))  # 【变量】止损距离(价格单位)
    if dist <= 0:
        return {'ok': False, 'reason': '入场价与止损价相同 → 止损距离为 0, 盈亏比无从谈起'}
    loss_per_lot = dist * float(point_value)  # 【变量】单手亏损金额

    rrr = None  # 【变量】盈亏比率
    if _is_num(target):
        rrr = abs(float(target) - float(entry)) / dist
    lots = int(min(per_trade / loss_per_lot, max_lots_cap)) if loss_per_lot > 0 else 0  # 【变量】建议手数

    pass_gate = None if rrr is None else (rrr > 1)
    allow_pts = per_trade / float(point_value)  # 【变量】允许的价格偏差(点数)
    is_long = float(entry) >= float(stop)
    zone = (float(stop), float(stop) + allow_pts) if is_long else (float(stop) - allow_pts, float(stop))
    in_zone = zone[0] <= float(entry) <= zone[1]
    # 判定顺序很重要: 缺目标价 → 闸门根本无法闭合, 此时不该报"超出区间"(那是另一个维度)
    if pass_gate is None:
        verdict = '待定: 缺目标价, 盈亏比闸门无法闭合(须先补 cap-profit-target)'
    elif pass_gate is False:
        verdict = '**否决**: 盈亏比 ≤1(硬闸门, 不允许酌情执行)'
    elif not in_zone:
        verdict = '**否决**: 入场价超出资金约束允许的可入市区间'
    else:
        verdict = '可执行'
    return {'ok': True,
            'steps': {'1_总风险额': _round(risk_amt, 2), '2_单笔止损额度': _round(per_trade, 2),
                      '2_参数': f'风险档 {risk_pct:.0%} / 交易次数 {times}',
                      '3_盈亏比率': _round(rrr, 3), '4_允许价格偏差': _round(allow_pts),
                      '4_可入市区间': [_round(zone[0]), _round(zone[1])]},
            'stop_distance': _round(dist), 'loss_per_lot': _round(loss_per_lot, 2),
            'lots': lots, 'rrr': _round(rrr, 3), 'pass_gate': pass_gate, 'in_zone': in_zone,
            'verdict': verdict,
            'notes': ['单笔层: 本卡只管**单品种/单笔**风险预算; 组合层限额(单市场≤10-15%/'
                      '同群类≤20-25%/总投资≤50%)属墨菲 cap-money-management, 二者是**层级关系非替代**。',
                      '**总风险 30% ≠ 用 30% 资金满杠杆入场**(作者专门警告的误解)。',
                      '缺口 N4: 30% 是作者约定, 未经破产概率/凯利推导 → 应与 Carver cap-leverage-kelly 交叉验证。',
                      '中国品种保证金/涨跌停/合约乘数不同, 每点价值须按实际合约换算。'],
            'card': 'luxiwu.cap-money-management'}


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 多空逻辑: 估值+驱动 二维四象(cap-value-drive)
# ══════════════════════════════════════════════════════════════════════════
def _score_by_rank(value: float, series: Sequence[float], lo: float = -3.0, hi: float = 3.0) -> float:
    """把值在历史序列里的分位映射到 [lo, hi](插值打分)。

    【口径】多空逻辑 cap-value-drive: 把历史利润率/基差率分布的「最差/最好」映射到
    −3/+3, 中间插值。这里用**窗口内分位**近似(作者原法未指定分布假设)。
    """
    vals = [float(v) for v in series if _is_num(v)]
    if len(vals) < 5:
        return 0.0
    rank = sum(1 for v in vals if v <= value) / len(vals)  # 【变量】分位(0~1)
    return lo + (hi - lo) * rank


def value_drive_matrix(basis_rate_series: Sequence[float], inv_series: Sequence[float],
                       inv_chg_pct: float | None = None) -> dict:
    """估值(基差率)+驱动(库存)二维四象评级。

    【基差符号口径】基差 = 现货 − 期货（作者原文「现货折盘面 − 期货价」）。
    【估值轴】基差率(相对估值, 跨品种可比)分位映射 −3~+3:
              **基差率越高 ⇒ 期货相对现货越便宜 ⇒ 估值越低 ⇒ 越偏多**。
              依据: 作者顺基差逻辑"贴水做多"(贴水 = 期货低于现货 ⇒ 基差率 > 0);
              cap-value-drive 案例「螺纹贴水幅度远大于沪镍, 做多螺纹更具**安全边际**」
              —— 贴水越深(基差率越高)安全边际越大 = 估值越低。
              ⚠️ 2026-09-29 修正: 原实现为 `+_score_by_rank(基差率)`, 方向相反,
                 会把**深贴水**判成"高估值"从而给出"单边做空", 与顺基差方向冲突。
    【驱动轴】库存分位**反向**映射: 库存越低(去库) → 驱动向上 → 偏多。
    【四象限】低估值+驱动向上 → 单边做多(第二象限); 高估值+驱动向上 → 套利/对冲(正套);
             低估值+驱动向下 → 套利/对冲(反套); 高估值+驱动向下 → 单边做空(第四象限)。
    【返回】{ok, value, drive, quadrant, advice, note}
    """
    if len([v for v in basis_rate_series if _is_num(v)]) < 10 or \
       len([v for v in inv_series if _is_num(v)]) < 10:
        return {'ok': False, 'reason': '基差率或库存序列不足 10 个点, 无法做二维评级'}
    # 【估值轴】基差率越高 ⇒ 期货越便宜 ⇒ 估值越低 ⇒ 取负号(见 docstring 口径说明)。
    value = -_score_by_rank(float(basis_rate_series[-1]), basis_rate_series)  # 【变量】估值分
    inv_now = float(inv_series[-1])
    inv_score = -_score_by_rank(inv_now, inv_series)  # 【变量】驱动分(库存越低越向上)
    if _is_num(inv_chg_pct) and float(inv_chg_pct) < -3:
        inv_score = min(3.0, inv_score + 0.5)
    elif _is_num(inv_chg_pct) and float(inv_chg_pct) > 3:
        inv_score = max(-3.0, inv_score - 0.5)

    if value < 0 and inv_score > 0:
        quadrant, advice = '第二象限(低估值+驱动向上)', '单边做多'
    elif value > 0 and inv_score > 0:
        quadrant, advice = '第一象限(高估值+驱动向上)', '套利/对冲(适合正套)'
    elif value < 0 and inv_score < 0:
        quadrant, advice = '第三象限(低估值+驱动向下)', '套利/对冲(适合反套, 结合仓单有效期)'
    elif value > 0 and inv_score < 0:
        quadrant, advice = '第四象限(高估值+驱动向下)', '单边做空'
    else:
        quadrant, advice = '原点附近(估值/驱动均中性)', '无明确信号, 观望'
    return {'ok': True, 'value': round(value, 2), 'drive': round(inv_score, 2),
            'quadrant': quadrant, 'advice': advice,
            'note': '二维四象是**品种分类工具, 不是择时工具**——入场时机仍需技术面。'
                    '估值用利润率(绝对)+基差率(相对)等权是作者设定, 本模块仅用基差率。',
            'card': 'duokong.cap-value-drive'}


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 墨菲: RSI 背离 + 3% 穿越/2 天原则
# ══════════════════════════════════════════════════════════════════════════
def rsi_divergence(bars: Sequence[dict], rsi_series: Sequence[float | None],
                   swings: Sequence[dict] | None = None, lookback_k: int = 3) -> dict:
    """RSI 背离(墨菲 cap-oscillators-divergence): **必须发生在极限区**才有效。

    【判据】顶背离 = 价格创更高高点, 但 RSI 高点更低, 且 RSI 曾入超买区(>70);
            底背离 = 价格创更低低点, 但 RSI 低点更高, 且 RSI 曾入超卖区(<30)。
    """
    if len(bars) < 30 or len(rsi_series) < 30:
        return {'ok': False, 'reason': '数据不足 30 根, 无法判背离'}
    sw = list(swings) if swings else find_swings(bars, lookback_k)
    his = [p for p in sw if p['kind'] == 'H'][-2:]  # 【变量】最后两个摆动高点
    los = [p for p in sw if p['kind'] == 'L'][-2:]  # 【变量】最后两个摆动低点

    def _rsi_at(idx: int) -> float | None:
        """取摆动点当日附近的 RSI(容忍 ±2 根错位)。"""
        for off in (0, -1, 1, -2, 2):
            j = idx + off
            if 0 <= j < len(rsi_series) and _is_num(rsi_series[j]):
                return float(rsi_series[j])  # type: ignore[arg-type]
        return None

    ob = max((float(v) for v in rsi_series if _is_num(v)), default=50.0)  # 【变量】窗口最高 RSI
    os_ = min((float(v) for v in rsi_series if _is_num(v)), default=50.0)  # 【变量】窗口最低 RSI

    if len(his) == 2:
        p1, p2 = his[0], his[1]
        r1, r2 = _rsi_at(p1['idx']), _rsi_at(p2['idx'])
        if _is_num(p2['price']) and _is_num(p1['price']) and _is_num(r1) and _is_num(r2):
            if float(p2['price']) > float(p1['price']) and float(r2) < float(r1):
                strength = ('有效(极限区)' if ob > 70 else
                            '弱(未入超买区, 按墨菲口径不构成有效背离)')
                return {'ok': True, 'divergence': '顶背离', 'strength': strength,
                        'desc': f"价格 {p1['price']}→{p2['price']} 创新高, RSI {r1:.1f}→{r2:.1f} 未跟上"
                                f'(窗口最高 RSI {ob:.1f})。',
                        'card': 'murphy.cap-oscillators-divergence'}
    if len(los) == 2:
        p1, p2 = los[0], los[1]
        r1, r2 = _rsi_at(p1['idx']), _rsi_at(p2['idx'])
        if _is_num(p2['price']) and _is_num(p1['price']) and _is_num(r1) and _is_num(r2):
            if float(p2['price']) < float(p1['price']) and float(r2) > float(r1):
                strength = ('有效(极限区)' if os_ < 30 else
                            '弱(未入超卖区, 按墨菲口径不构成有效背离)')
                return {'ok': True, 'divergence': '底背离', 'strength': strength,
                        'desc': f"价格 {p1['price']}→{p2['price']} 创新低, RSI {r1:.1f}→{r2:.1f} 未跟随"
                                f'(窗口最低 RSI {os_:.1f})。',
                        'card': 'murphy.cap-oscillators-divergence'}
    return {'ok': True, 'divergence': '无', 'strength': '-',
            'desc': '最近两个同向摆动点未构成背离。', 'card': 'murphy.cap-oscillators-divergence'}


def murphy_breakout_filter(bars: Sequence[dict], level: float, direction: str = '上升') -> dict:
    """墨菲 3% 穿越 + 2 天原则(cap-trend-filters)。

    【规则】有效突破 = 收盘穿越关键位 **3% 以上** 且 **连续 2 天** 站住; 否则视为拉锯伪信号。
    【与鹿希武冲突】鹿希武用「趋势线+拐点线是否**同时**破」, **不用 3% 阈值** →
            两者结论不同时**必须并列陈述, 禁止混用**。
    """
    if not _is_num(level) or len(bars) < 3:
        return {'ok': False, 'reason': '关键位或 K 线不足'}
    lv = float(level)
    closes = [float(b['close']) for b in bars]
    last = closes[-1]
    pct = (last - lv) / lv * 100 if lv else 0.0  # 【变量】穿越幅度(%)
    want_up = direction == '上升'
    ok_pct = pct >= 3 if want_up else pct <= -3  # 【变量】3% 阈值
    days = 0  # 【变量】连续站住天数
    for c in reversed(closes):
        cond = c > lv if want_up else c < lv
        if cond and abs((c - lv) / lv * 100) >= 3:
            days += 1
        else:
            break
    passed = bool(ok_pct and days >= 2)
    return {'ok': True, 'passed': passed, 'pct': round(pct, 2), 'days': days,
            'desc': f'收盘距关键位 {pct:+.2f}%(阈值 ±3%), 连续站住 {days} 天(阈值 2) → '
                    + ('**通过**墨菲过滤。' if passed else '**未通过**墨菲过滤(可能为拉锯伪信号)。'),
            'conflict_note': '鹿希武体系改用「趋势线+拐点线是否同时破」, 不用 3% 阈值 → 两套结论并列, 不混用。',
            'card': 'murphy.cap-trend-filters'}


# ══════════════════════════════════════════════════════════════════════════
# ⑧ Carver 速度上限 + Harris 成本测量
# ══════════════════════════════════════════════════════════════════════════
def cost_speed_limit(expected_sr: float = 0.35, cost_per_trade_sr: float | None = None,
                     rolls_per_year: int = 4) -> dict:
    """Carver 速度上限(cap-cost-speed): 判断一个交易速度值不值得配风险资本。

    【规则】速度上限 = 预期税前 SR 的 1/3; 若「年换手 × 每手成本 > 上限」则不配资本。
            最大允许换手 = (上限 ÷ 每手成本) − 年展期次数。
    【默认】单品种中位 SR 约 0.30~0.40 → 上限约 0.10~0.13 SR/年;
            全球成本天花板约 0.007 SR/笔。
    【边界】成本数据必须自备(海外实证); **不信任回测 SR>2**(常见根因: 隐含 forward fill)。
    """
    if not _is_num(expected_sr) or float(expected_sr) <= 0:
        return {'ok': False, 'reason': '预期税前 SR 须为正数'}
    sr = float(expected_sr)
    limit = sr / 3  # 【变量】速度上限 = 预期税前 SR 的 1/3
    cpt = float(cost_per_trade_sr) if _is_num(cost_per_trade_sr) else 0.007  # 【变量】每笔成本(SR 单位)
    assumed = not _is_num(cost_per_trade_sr)
    max_turnover = (limit / cpt) - rolls_per_year if cpt > 0 else None  # 【变量】最大允许年换手
    tier = '可配(成本<1/3 SR)' if cpt <= limit else '**不建议配置**(成本已超速度上限)'
    return {'ok': True, 'speed_limit': _round(limit, 4), 'cost_per_trade_sr': _round(cpt, 4),
            'cost_assumed': assumed, 'max_turnover': _round(max_turnover, 1),
            'rolls_per_year': rolls_per_year, 'tier': tier,
            'desc': f'预期税前 SR={sr:.2f} → 速度上限={limit:.4f}; 每笔成本 {cpt:.4f} SR → '
                    f'年换手上限 ≈ {max_turnover:.1f} 次(已扣 {rolls_per_year} 次展期)。{tier}。',
            'notes': ['每笔成本未由调用方给出时, 采用全球天花板 0.007 SR/笔**假设值**'
                      '(标 cost_assumed=True), 落地须按实盘费率与冲击成本换算。',
                      '时间尺度地图: >2 年=均值回归; 3 月~2 年=趋势但效率递减; 3 周~3 月=趋势最优区;'
                      ' 数天~3 周=趋势仍可用但成本开始咬人。成本越高越要放慢。',
                      '**不信任回测 SR>2**(常见根因: 隐含 forward fill)。'],
            'card': 'carver.cap-cost-speed'}


def harris_cost_estimate(round_trip_fee: float, slip_ticks: float = 1.0,
                         point_value: float = 1.0, order_value: float = 1.0) -> dict:
    """Harris 成本测量(cap-execution-costs): 显性成本 + 隐性成本(冲击)估计。

    【公式】显性 = 往返手续费; 隐性 ≈ 滑点(tick) × 每点价值;
            bps = (显性 + 隐性) ÷ 合约价值 × 10000。
    【三基准】小单看 effective spread; 大单/组合看 implementation shortfall(含机会成本);
            仅被动跟踪才看 VWAP(VWAP 有系统性缺陷, 易被经纪商粉饰)。
    """
    if not _is_num(round_trip_fee):
        return {'ok': False, 'reason': '缺往返手续费(按实盘费率填写)'}
    explicit = float(round_trip_fee)  # 【变量】显性成本(手续费/费用/税)
    implicit = float(slip_ticks or 0) * float(point_value or 0)  # 【变量】隐性成本(滑点/冲击)
    total = explicit + implicit
    bps = total / float(order_value) * 10000 if _is_num(order_value) and float(order_value) > 0 else None
    return {'ok': True, 'explicit': _round(explicit, 2), 'implicit': _round(implicit, 2),
            'total': _round(total, 2), 'bps': _round(bps, 2),
            'benchmark_hint': '小单→effective spread; 大单/组合→IS(含机会成本); 被动跟踪→VWAP。',
            'notes': ['成本分显性(佣金/费用/税)与隐性(市场冲击)。机构级最优激进度 = '
                      '边际交易成本 = 边际机会成本。',
                      '对散户, 机构级 OMS/EMS 不可得, 用经纪商对账单近似。',
                      '中国期货保证金/涨跌停制度与美股订单簿不同, 公式须换算。'],
            'card': 'harris.cap-execution-costs'}


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 墨菲: 组合层资金限额 — 与鹿希武单笔层构成层级
# ══════════════════════════════════════════════════════════════════════════
def murphy_portfolio_limits(account: float, open_positions: int = 1,
                            max_one_market_pct: float = 0.125) -> dict:
    """墨菲组合层资金五限额: **组合层**, 与鹿希武单笔层嵌套(非替代)。

    【五限额】总投资 ≤50% 资金; 单一市场 ≤10-15%; 单一群类 ≤20-25%;
            单笔风险 ≤5%; 总风险按比例分配。
    【层级关系(SKILL.md 第 7 条)】组合波动率定总敞口 → 单品种分配 →
            单笔止损距离反推手数。三者**不是替代关系而是嵌套关系**。
    """
    if not _is_num(account) or float(account) <= 0:
        return {'ok': False, 'reason': '账户资金须为正数'}
    acc = float(account)
    return {'ok': True,
            'cap_total': _round(acc * 0.50, 2), 'cap_one_market': _round(acc * float(max_one_market_pct), 2),
            'cap_group': _round(acc * 0.225, 2), 'cap_single_trade_risk': _round(acc * 0.05, 2),
            'per_market_budget': _round(acc * 0.50 / max(1, int(open_positions)), 2),
            'desc': f'墨菲组合层五限额: 总投资≤{acc * 0.5:,.0f} / 单一市场≤'
                    f'{acc * float(max_one_market_pct):,.0f} / 同一群类≤{acc * 0.225:,.0f} / '
                    f'单笔风险≤{acc * 0.05:,.0f}(单位同账户币种)。',
            'notes': ['组合层(墨菲)与单笔层(鹿希武)是**层级关系非替代**: '
                      '组合波动率定总敞口 → 单品种分配 → 单笔止损距离反推手数。',
                      '盈亏比闸门: 墨菲 ≥3:1 vs 鹿希武 >1 → **冲突时并列陈述, 不取平均、不混用**。'],
            'card': 'murphy.cap-money-management'}


# ══════════════════════════════════════════════════════════════════════════
# ⑨b 扩卡(2026-09-29): 期限结构 / 库存周期阶段 / 周期绑定 / 交易计划 / 排除法
#     补齐前期缺口 —— 均为"有确定输入即可算出唯一结论"的卡, 不含主观形态判断。
# ══════════════════════════════════════════════════════════════════════════
def term_structure_regime(term_rows: Sequence[dict]) -> dict:
    """期限结构 regime(多空逻辑 cap-term-structure / cap-term-structure-warrant)。

    【方法】按合约月份排序, 比较近月与远月价格:
      近月 < 远月 → contango(期货升水; 现货熊市特征: 高库存/低利润 → 偏空)
      近月 > 远月 → backwardation(现货升水; 现货牛市特征: 低库存/紧供给 → 偏多)
      近似持平 → 结构平坦(方向中性)
    【口径纪律】作者原文以现货视角命名(contango=现货熊市), 本函数输出**结构名 +
      现货含义 + 方向倾向**三者并列, 避免"contango 到底看多还是看空"的误读。
    【参数】term_rows: [{contract, month, price, oi, volume}]。
    """
    rows = [r for r in (term_rows or []) if _is_num(r.get('price')) and str(r.get('month') or '')]
    if len(rows) < 2:
        return {'ok': False, 'reason': f'有效合约不足({len(rows)} 个, 需 ≥2)'}
    rows = sorted(rows, key=lambda r: str(r['month']))
    near, far = rows[0], rows[-1]
    np_, fp = float(near['price']), float(far['price'])
    spread = _round(fp - np_, 2)
    pct = _round((fp / np_ - 1) * 100, 3) if np_ else None
    if spread is None or abs(spread) < 1e-9:
        shape, spot_meaning, bias = '平坦', '近远月无价差', '中性'
    elif fp > np_:
        shape, spot_meaning, bias = 'contango(期货升水)', '现货熊市特征(高库存/低利润)', '偏空'
    else:
        shape, spot_meaning, bias = 'backwardation(现货升水)', '现货牛市特征(低库存/紧供给)', '偏多'
    return {'ok': True, 'shape': shape, 'near': f"{near['contract']}@{np_}",
            'far': f"{far['contract']}@{fp}", 'spread': spread, 'spread_pct': pct,
            'spot_meaning': spot_meaning, 'bias': bias,
            'desc': f'期限结构 {shape}: 近月 {near["contract"]} {np_} → 远月 {far["contract"]} {fp} '
                    f'(价差 {spread}, {pct}%)。{spot_meaning} → 方向倾向 {bias}。',
            'notes': ['无现货数据时, 本卡可作逼仓/交仓的替代框架: back + 低仓单 → 正套。',
                      '结构是**品种态**(持续数周~数月), 不是择时信号; 入场仍需技术面。'],
            'card': 'duokong.cap-term-structure + cap-term-structure-warrant'}


def inventory_cycle_phase(inv_series: Sequence[float], price_series: Sequence[float],
                          window: int = 5) -> dict:
    """库存周期四阶段(多空逻辑 cap-inventory-cycle)。

    【四阶段判据】以"库存方向 × 价格方向"二维划分(作者原表):
      库存降 + 价格涨 → 被动去库(需求旺; 偏多)
      库存升 + 价格涨 → 主动建库(补库周期; 偏多但需警惕见顶)
      库存升 + 价格跌 → 被动建库(需求弱; 偏空)
      库存降 + 价格跌 → 主动去库(减产中; 偏空但接近底部)
    【参数】inv_series 库存/仓单序列; price_series 收盘价序列; window 变化窗口(默认 5)。
    【纪律】0 值视为占位跳过(不补零); 序列不足 → 返回不可算, 不猜。
    """
    pos = [float(v) for v in (inv_series or []) if _is_num(v) and float(v) > 0]
    px = [float(v) for v in (price_series or []) if _is_num(v)]
    if len(pos) < window + 1 or len(px) < window + 1:
        return {'ok': False, 'reason': f'库存 {len(pos)} 点 / 价格 {len(px)} 点 不足(各需 ≥{window + 1})'}
    inv_chg = round((pos[-1] / pos[-1 - window] - 1) * 100, 2) if pos[-1 - window] else None
    px_chg = round((px[-1] / px[-1 - window] - 1) * 100, 2) if px[-1 - window] else None
    if inv_chg is None or px_chg is None:
        return {'ok': False, 'reason': '变化率不可算(分母为 0)'}
    inv_dir = '升' if inv_chg > 0.5 else ('降' if inv_chg < -0.5 else '平')
    px_dir = '涨' if px_chg > 0.5 else ('跌' if px_chg < -0.5 else '平')
    table = {('降', '涨'): ('被动去库', '需求旺、供给紧 → 偏多'),
             ('升', '涨'): ('主动建库', '补库周期 → 偏多但需警惕见顶'),
             ('升', '跌'): ('被动建库', '需求弱、累库 → 偏空'),
             ('降', '跌'): ('主动去库', '减产/去库中 → 偏空但接近底部')}
    phase, bias = table.get((inv_dir, px_dir), ('阶段不明', '库存与价格变化均不明显 → 中性'))
    # 分位(供判断"高位累库/低位去库"的强度)
    rank = None
    if len(pos) >= 20:
        rank = _round(sum(1 for v in pos if v <= pos[-1]) / len(pos) * 100, 1)
    return {'ok': True, 'phase': phase, 'bias': bias, 'inv_dir': inv_dir, 'price_dir': px_dir,
            'inv_chg_pct': inv_chg, 'price_chg_pct': px_chg, 'inv_pct_rank': rank,
            'desc': f'库存周期阶段: {phase}(库存{inv_dir} {inv_chg}% / 价格{px_dir} {px_chg}%)。{bias}。'
                    + (f' 库存处于近序列 {rank}% 分位。' if rank is not None else ''),
            'notes': ['看**速度/方向**而非绝对值: 累库放缓本身即边际改善。',
                      '四阶段是**品种态定位**, 需与技术面入场时机配合, 不可单独择时。'],
            'card': 'duokong.cap-inventory-cycle'}


def period_binding(bars: Sequence[dict], back: int = 20) -> dict:
    """趋势的周期绑定(cap-trend-definition)—— 同一时刻不同周期可给出相反方向。

    【方法】对同一序列在不同回溯窗(短/中/长)上分别判方向, 显式列出**是否矛盾**。
      作者的判据: 趋势 = "某一特定周期内的价格波动方向"; 日线上升 + 周线下降**两者都对**,
      矛盾时不得强行统一, 应明示"交易周期未指定 → 方向不唯一"。
    【参数】bars 日线; back 长周期回溯根数(默认 20, 即约一个月)。
    """
    def _dir(seg: Sequence[dict]) -> str:
        if len(seg) < 5:
            return '不足'
        first, last = float(seg[0]['close']), float(seg[-1]['close'])
        chg = (last / first - 1) * 100 if first else 0.0
        if chg > 2:
            return '上升'
        if chg < -2:
            return '下降'
        return '横盘'

    if len(bars) < 15:
        return {'ok': False, 'reason': f'日线不足({len(bars)} 根, 需 ≥15)'}
    short = _dir(bars[-5:])
    mid = _dir(bars[-10:])
    long = _dir(bars[-max(back, 15):])
    dirs = [d for d in (short, mid, long) if d not in ('不足',)]
    conflict = len(set(dirs)) > 1
    return {'ok': True, 'short': short, 'mid': mid, 'long': long, 'conflict': conflict,
            'desc': f'周期绑定: 短(5日)={short} / 中(10日)={mid} / 长({max(back, 15)}日)={long}。'
                    + ('⚠️ **多周期方向矛盾** —— 按 cap-trend-definition, 交易周期未指定时'
                       '方向不唯一, 应并列陈述各周期结论, 不得强行统一。' if conflict
                       else '多周期方向一致 → 方向唯一性较高。'),
            'notes': ['趋势由小到大演变(短期→中期→长期), 交易须"从小到大"看。',
                      '作者警告: 因多周期矛盾而**放弃决策**是把判断权交给运气(应输出三态之一)。'],
            'card': 'luxiwu.cap-trend-definition'}


def trading_plan(entry: float | None, stop: float | None, target: float | None,
                 direction: str = '买入', account: float | None = None,
                 point_value: float | None = None, risk_pct: float = 0.30,
                 times: int = 10) -> dict:
    """交易计划三要素(cap-trading-plan)—— **顺序固定: 止损 → 入市 → 目标**。

    【为什么顺序不可颠倒】先定止损才能反推仓位; 先定目标人就会为"够到目标"而放宽止损。
    【硬闸门】无法确定止损价 → **不做这笔交易**(作者原文: "如果你无法确定止损价位, 就不要做这笔交易")。
    【参数】entry/stop/target 三要素价位(任一缺失则如实标注该要素缺口);
            account + point_value 提供时, 附带反推手数(单笔层风控)。
    """
    missing = []
    if stop is None:
        missing.append('止损价位')
    if entry is None:
        missing.append('入市价位')
    if target is None:
        missing.append('盈利目标')
    ok = not missing
    rr = None
    if entry is not None and stop is not None and target is not None and abs(entry - stop) > 1e-9:
        rr = _round(abs(target - entry) / abs(entry - stop), 2)
    lots = None
    if ok and account and point_value and account > 0 and point_value > 0:
        try:
            mm = money_management(float(account), float(entry), float(stop), float(target),
                                  float(point_value), risk_pct=risk_pct, times=times)
            lots = mm.get('lots')
        except Exception:  # noqa: BLE001 - 手数反推失败不阻断计划输出
            lots = None
    steps = []
    steps.append(f'① 止损价位: {stop if stop is not None else "**未确定 → 按本书铁律, 不做这笔交易**"}')
    steps.append(f'② 入市价位: {entry if entry is not None else "未定(需等回调位 + K 线反转确认)"}')
    steps.append(f'③ 盈利目标: {target if target is not None else "未定(可用 1.618 四公式测算)"}')
    return {'ok': ok, 'direction': direction, 'entry': entry, 'stop': stop, 'target': target,
            'risk_reward': rr, 'lots': lots, 'missing': missing, 'steps': steps,
            'desc': f'交易计划({direction})三要素固定顺序 —— ' + ' / '.join(
                s for s in steps) + (f'。盈亏比 {rr}。' if rr is not None else '。'),
            'notes': ['三要素**顺序不可颠倒**(止损→入市→目标); 且**必须书面化** —— 不写下来不算计划。',
                      '无止损价的交易不成立(作者用学员"入市后才问止损放哪"作反例)。'],
            'card': 'luxiwu.cap-trading-plan'}


def direction_exclusion(direction_geometry: str | None, double_break: bool = False,
                        broke_point_a: bool | None = None) -> dict:
    """方向排除法三态输出(cap-direction-exclusion)。

    【集合逻辑】买入 ∩ (买入+卖出) = 买入 —— 只要排除掉一个方向, 买卖指令即唯一。
    【三态】① 双线未同时破 → 横向未被排除 → **暂不操作**(方向不唯一);
            ② 双线同破且破分界点 A → 新趋势方向确立 → 给出唯一方向;
            ③ 双线同破但未破 A → 横向仍在 → **暂不操作**。
    【纪律】禁止锁单(同时买又卖)、禁止"三方向都说"; **空仓也是一种决定**。
    【参数】direction_geometry 几何给出的原方向; double_break 是否双线同破;
            broke_point_a 是否已破分界点 A(None=未确认)。
    """
    if not direction_geometry or direction_geometry == '横盘(未定)':
        state, out = '方向未定', '暂不操作'
        reason = '几何未能确定方向 → 上升或下降均未被排除 → 方向不唯一, 输出「暂不操作」。'
    elif not double_break:
        state, out = '趋势延续(未破双线)', direction_geometry
        reason = f'双线未同时被破 → 横向未被排除但也未确认转势 → 沿用当前{direction_geometry}(只破一条=即将转变, 不构成新方向)。'
    elif broke_point_a is True:
        newdir = '下降' if direction_geometry == '上升' else '上升'
        state, out = f'新趋势({newdir})', newdir
        reason = f'双线同破 + 突破分界点 A → 原{direction_geometry}趋势结束, 新趋势({newdir})确立 → 方向唯一。'
    else:
        state, out = '横向整理(待裁决)', '暂不操作'
        reason = ('双线同破但**未破分界点 A** → 横向整理未被排除 → 方向不唯一, 输出「暂不操作」; '
                  '未确认前不按新趋势建仓(鹿希武 N3 缺口: A 点过早判定系统性偏低)。')
    return {'ok': True, 'state': state, 'action': out, 'geometry_direction': direction_geometry,
            'double_break': double_break, 'broke_point_a': broke_point_a,
            'desc': f'排除法裁决: {state} → **{out}**。{reason}',
            'notes': ['输出必须为此三态之一(买/卖/暂不操作); "暂不操作"是**合法且有价值的结论**。',
                      '禁止锁单(同时持有反向仓)与"三方向都说"的评论员话术。'],
            'card': 'luxiwu.cap-direction-exclusion'}


# ══════════════════════════════════════════════════════════════════════════
# ⑨c 扩卡(2026-09-30 第二批): 墨菲均线/量仓/趋势结构 + 多空逻辑价差 + Carver 收缩
#     全部为"有确定输入即可算出唯一结论"的卡; 主观形态判断(头肩/三角/艾略特)不在内。
# ══════════════════════════════════════════════════════════════════════════
def sma(series: Sequence[float], n: int) -> list[float | None]:
    """简单移动平均(SMA)。返回与 series 等长的序列, 前 n-1 位为 None。"""
    vals = [float(v) if _is_num(v) else None for v in (series or [])]
    out: list[float | None] = [None] * len(vals)
    if n <= 0:
        return out
    run = 0.0
    cnt = 0
    for i, v in enumerate(vals):
        if v is None:
            run, cnt = 0.0, 0
            continue
        run += v
        cnt += 1
        if cnt > n:  # 【逻辑】滑出窗口(用 i-n 位置的值回退, 遇 None 时窗口重置)
            old = vals[i - n]
            if old is None:
                run, cnt = 0.0, 0
            else:
                run -= old
                cnt -= 1
        if cnt == n:
            out[i] = _round(run / n, 4)
    return out


def ma_crossover_system(bars: Sequence[dict], fast: int = 5, slow: int = 20) -> dict:
    """墨菲均线双线系统(cap-moving-averages)。

    【判据(卡原文)】美林霍克海默五结论之⑤: **双简单移动平均线组合是最佳选择**;
      长期(>40 天)优于短期, 40 天为分水岭; 简单平均优于加权/指数平均。
      双线法: 快线上穿慢线=买、下穿=卖。
    【纪律】① 均线是**滞后**工具, 无趋势市场表现拙劣 → 必须与 ADXR 配合(卡本身写明);
            ② 本函数给的是 5/20 默认档, **参数不可跨品种照搬**(卡边界③: 每市场有独有最优均线,
               中国品种需本国校准) → 输出里标明所用参数。
    【参数】bars 日线; fast/slow 快慢线周期。
    """
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if len(closes) < slow + 2:
        return {'ok': False, 'reason': f'日线不足({len(closes)} 根, 需 ≥{slow + 2})'}
    f = sma(closes, fast)
    s = sma(closes, slow)
    # 找最近一次交叉(从末尾往前扫, 最多回看 60 根)
    cross, cross_i = None, None
    for i in range(len(closes) - 1, max(slow, len(closes) - 60), -1):
        if None in (f[i], s[i], f[i - 1], s[i - 1]):
            continue
        prev_d, cur_d = f[i - 1] - s[i - 1], f[i] - s[i]
        if prev_d <= 0 < cur_d:
            cross, cross_i = '金叉(快线上穿慢线) → 买', i
            break
        if prev_d >= 0 > cur_d:
            cross, cross_i = '死叉(快线下穿慢线) → 卖', i
            break
    state = None
    if f[-1] is not None and s[-1] is not None:
        state = '快线在上(多头排列)' if f[-1] > s[-1] else '快线在下(空头排列)'
    prev_d = (f[-1] - s[-1]) if (f[-1] is not None and s[-1] is not None) else None
    prev_d2 = (f[-2] - s[-2]) if (f[-2] is not None and s[-2] is not None) else None
    expanding = (prev_d is not None and prev_d2 is not None and abs(prev_d) >= abs(prev_d2))
    return {'ok': True, 'fast': fast, 'slow': slow,
            'last_close': _round(closes[-1]), 'ma_fast': f[-1], 'ma_slow': s[-1],
            'state': state, 'spread': _round(prev_d, 4) if prev_d is not None else None,
            'expanding': expanding, 'cross': cross,
            'cross_bars_ago': (len(closes) - 1 - cross_i) if cross_i is not None else None,
            'desc': f'均线系统({fast}/{slow}): 收盘 {_round(closes[-1])} | '
                    f'MA{fast}={f[-1]} / MA{slow}={s[-1]} → {state}'
                    + ('(价差走扩=趋势在强化)' if expanding else '(价差收敛=动能减弱)')
                    + (f'; 最近信号: {cross}({len(closes) - 1 - cross_i} 根前)' if cross else '; 近 60 根内无交叉'),
            'notes': ['**长期(>40 天)优于短期**, 40 天为分水岭(美林霍克海默结论③)。',
                      '**参数不可跨品种照搬** —— 每市场有独有最优均线, 中国品种需本国校准; '
                      '本输出固定为 5/20 档, 仅供结构判断, 不构成"最优参数"声明。',
                      '均线在无趋势市场表现拙劣 → 必须与 ADXR 趋势性配合使用(见墨菲 cap-trading-systems)。'],
            'card': 'murphy.cap-moving-averages'}


def volume_open_interest(bars: Sequence[dict], lookback: int = 5) -> dict:
    """墨菲量仓验证(cap-volume-open-interest)。

    【判据(卡原文)】价格/量/持仓权重约 **5:3:2**(均为验证性指标); 量须沿趋势方向扩张;
      持仓兴趣四规则: 升势+持仓增=看涨 / 升势+持仓减=看跌 / 降势+持仓增=看跌 / 降势+持仓减=看涨。
    【边界(卡原文)】① 中国持仓数据**次日公布**, 期货量分析不如股市有用(总额掩盖个别合约);
      ② 涨跌停锁死日成交量失真; ③ 季节性修正需自建 5 年均线基准(本函数**未做**季节性修正,
         在输出里显式标注, 不假装已修正)。
    【参数】bars 需含 close/volume/open_interest; lookback 变化窗口(默认 5)。
    """
    rows = [b for b in bars if _is_num(b.get("close"))]
    if len(rows) < lookback + 1:
        return {'ok': False, 'reason': f'日线不足({len(rows)} 根, 需 ≥{lookback + 1})'}
    px = [float(b["close"]) for b in rows]
    vols = [b.get("volume") for b in rows]
    ois = [b.get("open_interest") for b in rows]
    chg = lambda seq, k: (  # noqa: E731 - 局部小工具, 保持紧凑
        round((float(seq[-1]) / float(seq[-1 - k]) - 1) * 100, 2)
        if _is_num(seq[-1]) and _is_num(seq[-1 - k]) and float(seq[-1 - k]) else None)
    px_chg = chg(px, lookback)
    vol_chg = chg(vols, lookback) if _is_num(vols[-1]) else None
    oi_chg = chg(ois, lookback) if _is_num(ois[-1]) else None
    px_up = (px_chg or 0) > 0.5
    px_dn = (px_chg or 0) < -0.5
    oi_up = (oi_chg or 0) > 0.5
    oi_dn = (oi_chg or 0) < -0.5
    # 持仓兴趣四规则
    read, bias = '数据不足或变化不显著 → 不构成量仓信号', '中性'
    if px_up and oi_up:
        read, bias = '升势 + 持仓增 = 新资金流入(看涨)', '偏多'
    elif px_up and oi_dn:
        read, bias = '升势 + 持仓减 = 空头斩仓推动(看跌, 反弹不可持续)', '偏空'
    elif px_dn and oi_up:
        read, bias = '降势 + 持仓增 = 新空入场(看跌)', '偏空'
    elif px_dn and oi_dn:
        read, bias = '降势 + 持仓减 = 多头斩仓(接近尾声, 看涨)', '偏多'
    # 量能方向: 涨时量增 / 跌时量减 为健康
    vol_ok = None
    if vol_chg is not None:
        vol_ok = (px_up and vol_chg > 0) or (px_dn and vol_chg < 0)
    # 胀爆/抛售高潮: 价格急动 + 量大 + 持仓骤降 = 大规模平仓预警
    warning = None
    if abs(px_chg or 0) > 3 and (vol_chg or 0) > 20 and (oi_chg or 0) < -3:
        warning = ('⚠️ 价急动 + 量大增 + 持仓骤降 → 疑似'
                   + ('胀爆(顶部大规模平仓)' if px_up else '抛售高潮(底部大规模平仓)'))
    return {'ok': True, 'px_chg_pct': px_chg, 'vol_chg_pct': vol_chg, 'oi_chg_pct': oi_chg,
            'read': read, 'bias': bias, 'vol_confirm': vol_ok, 'warning': warning,
            'oi_latest': ois[-1] if _is_num(ois[-1]) else None,
            'volume_latest': vols[-1] if _is_num(vols[-1]) else None,
            'desc': f'量仓验证: 价格{_num(px_chg)}% / 量{_num(vol_chg)}% / 持仓{_num(oi_chg)}% '
                    f'({lookback} 日) → {read}'
                    + (f'; 量能{"确认" if vol_ok else "背离(量未随价扩张)"}趋势' if vol_ok is not None else '')
                    + (f'。{warning}' if warning else ''),
            'notes': ['权重口径: 价格 5 : 量 3 : 持仓 2, 三者均为**验证性**指标(不独立产生信号)。',
                      '⚠️ 本函数**未做季节性修正**(卡边界②: 需自建 5 年均线对比基准) —— '
                      '持仓水平的高低判断请勿直接采信, 只看其**变化方向**。',
                      '中国持仓数据**次日公布**; 涨跌停锁死日成交量失真(卡边界①)。'],
            'card': 'murphy.cap-volume-open-interest'}


def trend_structure(bars: Sequence[dict], k: int = 3) -> dict:
    """墨菲趋势结构与支撑阻挡(cap-trend-structure)。

    【判据(卡原文)】趋势=一系列依次上升/下降的峰谷; 支撑=前低、阻挡=前高;
      被决定性穿越后**角色互换**。趋势线**两点定线、第三点验证**。
    【边界(卡原文)】角色互换需要"足够大"的穿越(重要水平 10%, 短线 3-5%);
      趋势线"细小穿越"无统一规则 → 本函数把穿越幅度**如实报出**并标注阈值, 不下主观定性。
    【参数】bars 日线; k 摆动点识别强度(与 find_swings 同口径)。
    """
    swings = find_swings(bars, k)
    if len(swings) < 3:
        return {'ok': False, 'reason': f'有效摆动点不足({len(swings)} 个, 需 ≥3)'}
    highs = [s for s in swings if s.get('kind') in ('H', '高', 'high')]
    lows = [s for s in swings if s.get('kind') in ('L', '低', 'low')]
    last_close = float(bars[-1]["close"]) if bars and _is_num(bars[-1].get("close")) else None
    # 依次升高的低点 / 降低的高点
    higher_lows = len(lows) >= 2 and float(lows[-1]['price']) > float(lows[-2]['price'])
    lower_highs = len(highs) >= 2 and float(highs[-1]['price']) < float(highs[-2]['price'])
    if higher_lows and not lower_highs:
        tdir = '上升(依次升高的低点)'
    elif lower_highs and not higher_lows:
        tdir = '下降(依次降低的高点)'
    elif higher_lows and lower_highs:
        tdir = '收敛(低点抬高但高点降低 → 三角形整理)'
    else:
        tdir = '横向/未定'
    support = float(lows[-1]['price']) if lows else None
    resistance = float(highs[-1]['price']) if highs else None
    role = None
    if last_close is not None and support is not None and resistance is not None:
        if last_close < support:
            role = f'收盘已跌破前低支撑 {support} → **支撑转阻挡**(需穿越幅度确认)'
        elif last_close > resistance:
            role = f'收盘已升破前高阻挡 {resistance} → **阻挡转支撑**(需穿越幅度确认)'
    # 趋势线: 用最后两个同类摆动点连线, 外推到最新一根
    tline = None
    if len(lows) >= 2:
        p1, p2 = lows[-2], lows[-1]   # 【变量】两个低点摆动点 dict
        i1 = next((i for i, b in enumerate(bars) if str(b.get('date')) == str(p1.get('date'))), None)
        i2 = next((i for i, b in enumerate(bars) if str(b.get('date')) == str(p2.get('date'))), None)
        if i1 is not None and i2 is not None and i2 != i1:
            slope = (float(p2['price']) - float(p1['price'])) / (i2 - i1)
            tline = _round(float(p1['price']) + slope * (len(bars) - 1 - i1), 4)
    role_txt = role or None
    return {'ok': True, 'direction': tdir, 'support': _round(support), 'resistance': _round(resistance),
            'higher_lows': higher_lows, 'lower_highs': lower_highs, 'role_swap': role_txt,
            'uptrend_line': tline, 'last_close': _round(last_close),
            'swing_count': len(swings),
            'desc': f'趋势结构: {tdir} | 支撑(前低) {_num(support)} / 阻挡(前高) {_num(resistance)}'
                    + (f' | 上升趋势线外推 {tline}' if tline is not None else '')
                    + (f'。{role}' if role else ''),
            'notes': ['支撑阻挡的**重要性**由三因素定: 逗留时间 / 交易量 / 距今远近 —— '
                      '本函数只取价格位置, 未含前两个因素, 引用时须补充。',
                      '习惯数字(10/20/50/100 整数位)常成心理支撑阻挡(卡原文), 可与上述结构位并列参考。',
                      '角色互换需"足够大"的穿越: 重要水平 10%、短线 3-5%; '
                      '本函数报的是**穿越事实**, 是否"决定性"由使用者按上述阈值判定。',
                      '趋势线是"两点定线、第三点验证" → 本函数仅外推最后两个低点, 未做第三点验证。'],
            'card': 'murphy.cap-trend-structure'}


# ══════════════════════════════════════════════════════════════════════════
# 威科夫事件识别(C 方案: 只标事件, 不判阶段)
# ══════════════════════════════════════════════════════════════════════════

# 【为什么只做"事件级识别"而不做"阶段判定"(2026-10-13 设计决策)】
#   威科夫原方法是**描述性框架, 不是机械信号**。四份来源都强调: 阶段 B 可能持续很久、
#   Spring 与 UTAD **可能缺失**、各阶段可能重叠。若程序输出"当前处于吸筹阶段 C",
#   那是**看起来很确定、实际不可靠**的结论 —— 正是本项目一直在防的那类问题。
#   故本函数只做两件事:
#     ① 在历史 K 线上标出**满足量化特征**的威科夫事件(SC/AR/ST/Spring/SOS/LPS 及派发侧镜像);
#     ② 由事件序列给出一个**弱方向倾向**(供独立投票) + 对现有体系的**确认/背离**判断(供确认层)。
#   每个事件都带"触发依据"(量比/影线/回落幅度), 便于人工复核, 不做黑箱判断。
_WYCKOFF_VOL_LOOKBACK = 20  # 【变量】量比基准窗口(当日量 / 前 N 日均量)
_WYCKOFF_ZONE_TOL = 0.03  # 【变量】区间边界的容差(3%, 判"回测到同一区域")
# 【变量】自适应门槛的绝对下限(防止极低波动期把"正常波动"当高潮)
#   ★【为什么必须自适应】2026-10-13 实测: 各品种波动性差异极大 ——
#     量比: RB 最大仅 1.99、TA P90 1.60、SC P90 1.87;
#     |日涨跌幅| 中位: RB 0.38% vs SC 2.43%(差 6 倍);
#     20日累计涨跌中位: RB -0.5% vs SC +13.8%、LC -7.7%(最小 -26.2%)。
#   原先用固定门槛(量比 1.6 + 涨跌 1.8%)⇒ **RB/TA/M 全部零事件**(RB 只有 1 天达标),
#   而 SC 这种高波动品种事件泛滥。统一绝对门槛在跨品种场景下必然失效。
#   改为"按该品种自身近 lookback 日分布取分位", 并加绝对下限兜底。
_WYCKOFF_VOL_FLOOR = 1.12  # 【变量】放量门槛下限(倍)
_WYCKOFF_VOL_STRONG_FLOOR = 1.22  # 【变量】强放量门槛下限(倍)
_WYCKOFF_CHG_FLOOR = 1.0  # 【变量】大幅波动门槛下限(%)
_WYCKOFF_EDGE_MIN = 22.0  # 【变量】长影线占全幅最小%(固定值: 影线比例本身与品种无关)
_WYCKOFF_EVENT_GAP = 6  # 【变量】同类事件最小间隔(交易日) —— 抑制连续重复标注


def _wyck_vol_ratio(bars: Sequence[dict], i: int, lookback: int = _WYCKOFF_VOL_LOOKBACK) -> float | None:
    """当日成交量 / 前 lookback 日均量(不含当日, 避免自身抬高基准)。"""
    if i < lookback:
        return None
    seg = [b.get('volume') for b in bars[i - lookback:i]]
    base = [float(v) for v in seg if _is_num(v) and float(v) > 0]
    cur = bars[i].get('volume')
    if not base or not _is_num(cur):
        return None
    avg = sum(base) / len(base)
    return round(float(cur) / avg, 2) if avg > 0 else None


def _wyck_prev_close(bar: dict, fallback: float) -> float:
    """取该 bar 的"前收盘"近似值: 优先 bar['_prev_close'](由 wyckoff_events 注入)。

    【为什么需要】涨跌幅必须相对**前收盘**, 不能用当日开盘 —— 否则跳空日的跌幅会被
    严重低估(SC/BC 常伴随跳空, 用开盘价算会把"跳空后的窄幅整理"误判成小波动)。
    """
    pc = bar.get('_prev_close')
    return float(pc) if _is_num(pc) and float(pc) > 0 else float(fallback)


def _wyck_bar_shape(bar: dict) -> dict:
    """K 线形态: 实体/上下影线相对长度(用于判"承接有力"/"上方抛压")。

    【为什么用影线】威科夫的高潮日特征是**长影线**: 恐慌抛售留长下影(低位承接),
      买入高潮留长上影(高位抛压)。这是区分"高潮"与"普通大阴/大阳线"的关键。
    【为什么影线比例可以用固定门槛】影线占全幅的**比例**是无量纲的, 与品种波动大小无关
      (实测各品种下影≥25% 的天数都在 23~34 天), 故此处不需自适应。
    """
    o, h, l, c = (bar.get('open'), bar.get('high'), bar.get('low'), bar.get('close'))
    if not all(_is_num(x) for x in (o, h, l, c)):
        return {'ok': False}
    o, h, l, c = float(o), float(h), float(l), float(c)
    rng = h - l
    if rng <= 0:
        return {'ok': False}
    body = abs(c - o)
    return {
        'ok': True, 'range': rng, 'body': body,
        'body_pct': round(body / rng * 100, 1),
        'upper_pct': round((h - max(o, c)) / rng * 100, 1),  # 【变量】上影线占全幅%
        'lower_pct': round((min(o, c) - l) / rng * 100, 1),  # 【变量】下影线占全幅%
        'chg_pct': round((c / _wyck_prev_close(bar, o) - 1) * 100, 2),
    }


def _wyck_pct(sorted_vals: list[float], q: float) -> float | None:
    """取已排序序列的分位值(线性插值); 空序列 → None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _wyck_thresholds(rows: Sequence[dict], lo_i: int, n: int,
                     trend_win: int = 20) -> dict:
    """按**该品种自身**近段分布算出各事件的自适应门槛。

    【返回】{vol, vol_strong, chg, trend_dn, trend_up, samples}
      vol       放量门槛(量比分位, 不低于 _WYCKOFF_VOL_FLOOR)
      vol_strong 强放量门槛(用于 SOS/SOW)
      chg       大幅波动门槛%(|涨跌幅| 分位)
      trend_dn  前置"一段下跌"门槛%(负值, 用于 SC/Spring 的上下文)
      trend_up  前置"一段上涨"门槛%(正值, 用于 BC/UT 的上下文)
    【为什么前置趋势是必需的】第一版只看"当日放量急跌+长下影"就标 SC,
      结果出现 SC(07-21) 紧跟 BC(07-24) 这种**语义矛盾的连续事件** ——
      因为逐日独立判定、完全不看前面是涨是跌。威科夫的事件定义**内嵌趋势背景**:
      SC 必须发生在一段下跌之后, BC 必须发生在一段上涨之后。加上前置约束后,
      同一段行情里两个方向的事件链自然互斥。
    """
    vrs, chgs, trends = [], [], []
    for i in range(lo_i, n):
        vr = _wyck_vol_ratio(rows, i)
        if vr is not None:
            vrs.append(vr)
        chg = _wyck_bar_shape(rows[i]).get('chg_pct')
        if chg is not None:
            chgs.append(abs(chg))
        if i >= trend_win and _is_num(rows[i - trend_win].get('close')) and rows[i - trend_win]['close']:
            trends.append((float(rows[i]['close']) / float(rows[i - trend_win]['close']) - 1) * 100)
    vrs.sort(); chgs.sort(); trends.sort()
    return {
        'vol': max(_WYCKOFF_VOL_FLOOR, _wyck_pct(vrs, 0.85) or _WYCKOFF_VOL_FLOOR),
        'vol_strong': max(_WYCKOFF_VOL_STRONG_FLOOR, _wyck_pct(vrs, 0.90) or _WYCKOFF_VOL_STRONG_FLOOR),
        'chg': max(_WYCKOFF_CHG_FLOOR, _wyck_pct(chgs, 0.85) or _WYCKOFF_CHG_FLOOR),
        # 量萎缩门槛(ST 二次测试用): 取量比的低分位, 并保证至少萎缩 10%(≤0.90)
        'vol_dry': min(0.90, _wyck_pct(vrs, 0.25) or 0.90),
        # 前置趋势: 分位与绝对下限取"更严"的一侧(min 对负值更严 / max 对正值更严)
        'trend_dn': min(-3.0, _wyck_pct(trends, 0.15) or -3.0),
        'trend_up': max(3.0, _wyck_pct(trends, 0.85) or 3.0),
        'samples': {'vol_ratio': len(vrs), 'chg': len(chgs), 'trend': len(trends)},
    }


def _wyck_trend_pct(rows: Sequence[dict], i: int, win: int = 20) -> float | None:
    """第 i 根之前 win 日的累计涨跌幅%(前置趋势背景)。"""
    if i < win or not _is_num(rows[i - win].get('close')) or not rows[i - win]['close']:
        return None
    return (float(rows[i]['close']) / float(rows[i - win]['close']) - 1) * 100


def wyckoff_events(bars: Sequence[dict], lookback: int = 60) -> dict:
    """威科夫事件识别(C 方案: 事件级, 不判阶段)。

    【方法】在近 lookback 根 K 线上, 按**量价特征**标注威科夫关键事件。
      吸筹侧(下跌后→筑底): PS → SC → AR → ST → Spring → SOS → LPS
      派发侧(上涨后→做顶): PSY → BC → AR → ST → UT/UTAD → SOW → LPSY
    【返回】{
      ok, events:[{kind, side, idx, date, price, vol_ratio, basis(触发依据), desc}],
      zone:{low, high, low_date, high_date, width_pct} | None,
      bias,           # 弱方向倾向: 偏多/偏空/中性 —— 供**独立投票**用
      bias_reason,    # 倾向的可读理由
      confirm,        # 供**确认层**用: {stance, strength, align_with, text}
      subjectivity,   # 主观性披露(强制)
      notes, desc, card
    }
    【硬约束(必须遵守)】※ 不做阶段判定; ※ 事件必须带量化依据; ※ 事件不足时**如实说不足**,
      绝不用"可能是阶段 B"这类模糊话术填充。
    """
    rows: list[dict] = []
    for b in bars:
        if not _is_num(b.get('close')):
            continue
        b2 = dict(b)  # 【变量】不修改调用方对象: 注入 _prev_close 需复制
        if rows and _is_num(rows[-1].get('close')):
            b2['_prev_close'] = float(rows[-1]['close'])
        rows.append(b2)
    if len(rows) < 40:
        return {'ok': False, 'reason': f'日线不足({len(rows)} 根, 需 ≥40)'}
    n = len(rows)
    lo_i = max(_WYCKOFF_VOL_LOOKBACK + 1, n - lookback)  # 【变量】扫描起点(保证量比可算)
    # 【门限样本窗口】用比扫描窗口更宽的一段来估分布(lookback 内样本太少, 分位不稳)
    th_lo = max(_WYCKOFF_VOL_LOOKBACK + 1, n - max(lookback * 2, 120))
    th = _wyck_thresholds(rows, th_lo, n)
    closes = [float(b['close']) for b in rows]
    events: list[dict] = []

    def _add(kind: str, side: str, i: int, price: float, basis: str, desc: str,
             vol_ratio: float | None = None) -> None:
        events.append({
            'kind': kind, 'side': side, 'idx': i,
            'date': str(rows[i].get('date'))[:10], 'price': _round(price, 4),
            'vol_ratio': vol_ratio, 'basis': basis, 'desc': desc,
        })

    # ── 高潮日(SC 卖高潮 / BC 买高潮): 前置趋势 + 放量 + 大幅波动 ──
    #   【硬条件三个】前置趋势(语义核心) + 放量(量比自适应门槛) + 大幅波动(自适应门槛)。
    #   ★【影线为何只作"加分项"而不作硬门槛】第一版把"长影线"当硬条件, 结果全品种几乎
    #     零事件 —— 漏斗诊断显示: 满足"放量+大幅波动"有 7~11 天, 再要求"长影线"只剩
    #     0~5 天。原因是**大跌日通常是大实体阴线(下影短)、大涨日是大实体阳线(上影短)**,
    #     "大幅波动 + 长影线"本身就是低概率组合。而威科夫原意里, 高潮的核心特征是
    #     **放量**(努力), 影线只是"承接/抛压"的辅助证据 ⇒ 有则在依据里标明并加重语气,
    #     无则照标但如实说明(不假装有承接)。
    for i in range(lo_i, n):
        vr = _wyck_vol_ratio(rows, i)
        if vr is None or vr < th['vol']:
            continue
        sh = _wyck_bar_shape(rows[i])
        if not sh.get('ok'):
            continue
        chg = sh.get('chg_pct')
        if chg is None or abs(chg) < th['chg']:
            continue
        tr = _wyck_trend_pct(rows, i)
        tr_txt = f', 前 20 日 {tr:+.1f}%' if tr is not None else ''
        # SC: 前置下跌 + 放量急跌(下影长则注明"低位承接" —— 承接迹象, 非必要条件)
        if chg <= -th['chg'] and tr is not None and tr <= th['trend_dn']:
            edge = sh['lower_pct']
            hold = (f', 下影占 {edge}%（低位承接迹象）' if edge >= _WYCKOFF_EDGE_MIN
                    else f', 下影仅 {edge}%（无承接迹象, 属实体阴线）')
            _add('SC', 'accumulation', i, float(rows[i]['low']),
                 f'跌 {chg}% / 量比 {vr}{hold}{tr_txt}',
                 '恐慌抛售(SC): 下跌途中放量急跌 ⇒ 抛压集中释放。'
                 + ('下影长说明低位有承接；' if edge >= _WYCKOFF_EDGE_MIN else '')
                 + '是威科夫吸筹链的起点事件。', vr)
        # BC: 前置上涨 + 放量急涨(上影长则注明"高位抛压")
        elif chg >= th['chg'] and tr is not None and tr >= th['trend_up']:
            edge = sh['upper_pct']
            sup = (f', 上影占 {edge}%（高位抛压迹象）' if edge >= _WYCKOFF_EDGE_MIN
                   else f', 上影仅 {edge}%（无抛压迹象, 属实体阳线）')
            _add('BC', 'distribution', i, float(rows[i]['high']),
                 f'涨 {chg}% / 量比 {vr}{sup}{tr_txt}',
                 '买入高潮(BC): 上涨途中放量急涨 ⇒ 买盘集中释放。'
                 + ('上影长说明高位有抛压；' if edge >= _WYCKOFF_EDGE_MIN else '')
                 + '是威科夫派发链的起点事件。', vr)

    # ── AR 自动反弹/回落: 高潮日之后第一个显著反向摆动 ──
    sc_list = [e for e in events if e['kind'] in ('SC', 'BC')]
    for e0 in sc_list:
        i0 = e0['idx']
        seg = range(i0 + 1, min(i0 + 12, n))
        if e0['kind'] == 'SC':
            cand = [i for i in seg if _is_num(rows[i].get('high'))]
            if not cand:
                continue
            j = max(cand, key=lambda i: float(rows[i]['high']))
            hi = float(rows[j]['high'])
            up = (hi / e0['price'] - 1) * 100 if e0['price'] else 0
            if up >= th['chg']:
                _add('AR', 'accumulation', j, hi, f'自 SC 低点反弹 {up:.1f}%',
                     '自动反弹(AR): 抛售耗尽后的自然回升, 其高点构成**交易区间上沿**。')
        else:
            cand = [i for i in seg if _is_num(rows[i].get('low'))]
            if not cand:
                continue
            j = min(cand, key=lambda i: float(rows[i]['low']))
            lo = float(rows[j]['low'])
            dn = (1 - lo / e0['price']) * 100 if e0['price'] else 0
            if dn >= th['chg']:
                _add('AR', 'distribution', j, lo, f'自 BC 高点回落 {dn:.1f}%',
                     '自动回落(AR): 买盘耗尽后的自然回落, 其低点构成**交易区间下沿**。')

    # ── ST 二次测试: 回到高潮区附近, 但量**明显萎缩**(供需枯竭的证据) ──
    for e0 in sc_list:
        i0 = e0['idx']
        for i in range(i0 + 3, min(i0 + 30, n)):
            vr = _wyck_vol_ratio(rows, i)
            if vr is None or vr > th['vol_dry']:
                continue
            if e0['kind'] == 'SC':
                d = abs(float(rows[i]['low']) / e0['price'] - 1) if e0['price'] else 9
                if d <= _WYCKOFF_ZONE_TOL:
                    _add('ST', 'accumulation', i, float(rows[i]['low']),
                         f'回到 SC 低位区(偏离 {d*100:.1f}%) 且量比仅 {vr}',
                         '二次测试(ST): 回测卖高潮区而量大幅萎缩 ⇒ **卖压枯竭**。', vr)
                    break
            else:
                d = abs(float(rows[i]['high']) / e0['price'] - 1) if e0['price'] else 9
                if d <= _WYCKOFF_ZONE_TOL:
                    _add('ST', 'distribution', i, float(rows[i]['high']),
                         f'回到 BC 高位区(偏离 {d*100:.1f}%) 且量比仅 {vr}',
                         '二次测试(ST): 回测买高潮区而量大幅萎缩 ⇒ **买盘枯竭**。', vr)
                    break

    # ── Spring / UT: 假破区间边界后当日收回(洗盘), 量不必大 ──
    ar_rows = {}
    for e in events:
        if e['kind'] == 'AR':
            ar_rows.setdefault(e['side'], []).append(e)
    for e0 in sc_list:
        i0 = e0['idx']
        ars = ar_rows.get(e0['side']) or []
        ref = None
        if e0['kind'] == 'SC':
            # Spring 参考位 = SC 低点; 跌破后当日收回
            for i in range(i0 + 3, min(i0 + 40, n)):
                if not _is_num(rows[i].get('low')) or not _is_num(rows[i]['close']):
                    continue
                if float(rows[i]['low']) < e0['price'] * (1 - 0.005) and float(rows[i]['close']) > e0['price']:
                    vr = _wyck_vol_ratio(rows, i)
                    _add('Spring', 'accumulation', i, float(rows[i]['low']),
                         f'盘中跌破 SC 低点后收回(收于 {float(rows[i]["close"])})'
                         + (f' / 量比 {vr}' if vr else ''),
                         '弹簧(Spring): 假破支撑洗出弱手后迅速收回 ⇒ 上涨障碍已清除的**可疑信号**。', vr)
                    break
        else:
            for i in range(i0 + 3, min(i0 + 40, n)):
                if not _is_num(rows[i].get('high')) or not _is_num(rows[i]['close']):
                    continue
                if float(rows[i]['high']) > e0['price'] * (1 + 0.005) and float(rows[i]['close']) < e0['price']:
                    vr = _wyck_vol_ratio(rows, i)
                    _add('UT', 'distribution', i, float(rows[i]['high']),
                         f'盘中突破 BC 高点后收回(收于 {float(rows[i]["close"])})'
                         + (f' / 量比 {vr}' if vr else ''),
                         '上冲(UT): 假破阻力诱多后收回 ⇒ 上方买盘耗尽的**可疑信号**。', vr)
                    break

    # ── SOS / SOW: 放量突破区间上沿(强势) 或 跌破下沿(弱势) ──
    zone_hi = zone_lo = None
    for e in events:
        if e['kind'] == 'AR':
            if e['side'] == 'accumulation':
                zone_hi = e['price'] if zone_hi is None else max(zone_hi, e['price'])
            else:
                zone_lo = e['price'] if zone_lo is None else min(zone_lo, e['price'])
    for i in range(lo_i, n):
        vr = _wyck_vol_ratio(rows, i)
        if vr is None or vr < th['vol_strong']:
            continue
        chg = closes[i] / closes[i - 1] - 1 if i else 0
        if zone_hi and chg * 100 >= th['chg'] and closes[i] > zone_hi:
            _add('SOS', 'accumulation', i, closes[i],
                 f'放量涨 {chg*100:.1f}%(量比 {vr}) 突破区间上沿 {_num(zone_hi)}',
                 '强势信号(SOS): 放量突破区间阻力 ⇒ 需求接管, 原阻力应转为支撑。', vr)
        elif zone_lo and chg * 100 <= -th['chg'] and closes[i] < zone_lo:
            _add('SOW', 'distribution', i, closes[i],
                 f'放量跌 {chg*100:.1f}%(量比 {vr}) 跌破区间下沿 {_num(zone_lo)}',
                 '弱势信号(SOW): 放量跌破区间支撑 ⇒ 供给接管, 原支撑应转为阻力。', vr)

    events.sort(key=lambda e: e['idx'])
    # 【去重】同一日内同类事件只留一条(多轮循环可能重复标注)
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for e in events:
        k = (e['idx'], e['kind'], e['side'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    # 【事件抑制】同类事件在 _WYCKOFF_EVENT_GAP 日内重复出现 ⇒ 只留**最显著**的一条(量比最高)。
    #   【为什么需要】实测踩过: BC(09-07) 与 BC(09-08) 连着标两天、SOW 同样连标 ——
    #   读者看到"连续两天都是买入高潮"只会觉得噪声大。威科夫的"高潮"是一个**事件**,
    #   不是一段持续状态 ⇒ 邻近同类事件应视为同一次高潮的不同落点, 合并成一条。
    #   注意: 只抑制**同类**事件; SC 与 ST、BC 与 AR 语义不同, 不得互相抑制。
    def _sig(x: dict) -> float:
        v = x.get('vol_ratio')
        return float(v) if _is_num(v) else 0.0

    kept: list[dict] = []
    for e in sorted(uniq, key=_sig, reverse=True):
        if any(k['kind'] == e['kind'] and abs(int(k['idx']) - int(e['idx'])) < _WYCKOFF_EVENT_GAP
               for k in kept):
            continue
        kept.append(e)
    events = sorted(kept, key=lambda x: x['idx'])

    # ── 交易区间(用 AR 边界粗框, 仅供参照, **不作为"阶段"依据**) ──
    acc_ar = [e for e in events if e['kind'] == 'AR' and e['side'] == 'accumulation']
    dis_ar = [e for e in events if e['kind'] == 'AR' and e['side'] == 'distribution']
    zone = None
    if acc_ar:
        highs = [e['price'] for e in acc_ar if _is_num(e['price'])]
        lows = [e['price'] for e in sc_list if e['kind'] == 'SC' and e['side'] == 'accumulation']
        if highs and lows:
            zone = {'side': 'accumulation', 'low': _round(min(lows), 4), 'high': _round(max(highs), 4)}
    elif dis_ar:
        lows = [e['price'] for e in dis_ar if _is_num(e['price'])]
        highs = [e['price'] for e in sc_list if e['kind'] == 'BC' and e['side'] == 'distribution']
        if lows and highs:
            zone = {'side': 'distribution', 'low': _round(min(lows), 4), 'high': _round(max(highs), 4)}
    if zone and zone['low'] and zone['high'] and zone['low'] > 0:
        zone['width_pct'] = round((zone['high'] / zone['low'] - 1) * 100, 2)
        # 区间内累计成交量(威科夫"因果律"用它衡量"因"的大小)
        i_lo = min([e['idx'] for e in events if e['kind'] in ('SC', 'AR')] or [0])
        i_hi = max([e['idx'] for e in events if e['kind'] in ('SC', 'AR')] or [n - 1])
        i_hi = max(i_hi, min(i_lo + 1, n - 1))
        seg = [float(b['volume']) for b in rows[i_lo:i_hi + 1] if _is_num(b.get('volume'))]
        if seg:
            zone['bars'] = len(seg)
            zone['cum_volume'] = round(sum(seg), 2)

    # ── 弱方向倾向(供**独立投票**): 只看事件侧别与先后, 不做阶段推断 ──
    n_acc = len([e for e in events if e['side'] == 'accumulation'])
    n_dis = len([e for e in events if e['side'] == 'distribution'])
    last = events[-1] if events else None
    bias, bias_reason = '中性', '未识别到足够量价特征事件'
    if events:
        if last and last['side'] == 'accumulation':
            if n_acc > n_dis:
                bias = '偏多'
                bias_reason = (f'最近事件为吸筹侧「{last["kind"]}」({last["date"]}), '
                               f'且吸筹事件 {n_acc} 个 > 派发 {n_dis} 个')
            else:
                bias_reason = (f'最近事件为吸筹侧「{last["kind"]}」, 但派发事件 {n_dis} 个 ≥ '
                               f'吸筹 {n_acc} 个 → 方向不明')
        elif last and last['side'] == 'distribution':
            if n_dis > n_acc:
                bias = '偏空'
                bias_reason = (f'最近事件为派发侧「{last["kind"]}」({last["date"]}), '
                               f'且派发事件 {n_dis} 个 > 吸筹 {n_acc} 个')
            else:
                bias_reason = (f'最近事件为派发侧「{last["kind"]}」, 但吸筹事件 {n_acc} 个 ≥ '
                               f'派发 {n_dis} 个 → 方向不明')

    desc = (f'识别到 {len(events)} 个事件: '
            + ('、'.join(f'{e["kind"]}({e["date"]})' for e in events[-6:]) if events else '无')
            + (f'；参照区间 {zone["low"]}~{zone["high"]}(宽 {zone["width_pct"]}%)' if zone else ''))
    return {
        'ok': True, 'events': events, 'zone': zone,
        'counts': {'accumulation': n_acc, 'distribution': n_dis},
        'bias': bias, 'bias_reason': bias_reason,
        'desc': desc,
        'subjectivity': [
            '威科夫原方法是**描述性框架而非机械信号**: 阶段 B 可能持续很久, '
            'Spring / UTAD **可能缺失**, 各阶段可能重叠 ⇒ 本函数**不做阶段判定**。',
            '本函数只标注**满足量化特征**的事件(量比/影线/回落幅度), 每条的触发依据都列出, '
            '便于人工复核 —— 但"像 SC"不等于"就是 SC"。',
            '威科夫方法**缺乏量化回测验证**(与 Carver/Murphy 的统计证据等级不同), '
            '只能当注意力工具, 不得单独作为建仓依据。',
            '威科夫本人**反对与其他理论混用**(认为指标有滞后性); 本系统按"并列不混用"处置 —— '
            '既作为独立一票参与投票, 又单独出一段"确认层"供读者自行取舍。',
        ],
        'notes': ['事件识别依赖成交量真实性: 涨跌停锁死日、主力合约换月日的量会失真(项目既有坑)。',
                  '交易区间边界由 SC/AR 粗框, 与人工画线可能不同, 仅供参照。'],
        'card': 'wyckoff.events',
    }


def wyckoff_confirm(wyck: dict, votes: Sequence[dict],
                    exclude_books: Sequence[str] = ('威科夫',)) -> dict:
    """威科夫**确认层**: 用威科夫事件判断现有各体系方向票是"共振"还是"背离"。

    【为什么单独成层(用户 2026-10-13 要求)】本系统按"多书并列、不混用"处置;
      威科夫若只当作一票, 就丢掉了它**最有价值的用法** —— 用"谁在吸筹/派发"去
      判断其他体系的信号**有没有资金面支撑**。故独立成层, 不参与投票净额计算。
    【★ 必须排除自己】威科夫的独立票也在 votes 里, 若不剔除就会拿威科夫去确认威科夫
      (实测踩过: 输出"与 威科夫 一致"), 属于**循环论证**、毫无信息量。故默认排除自身,
      并且排除后若没有可对照的票, 如实返回"无对照"而非硬说共振。
    【判据】把其余体系的方向票分成多/空两堆, 与威科夫的弱倾向比对:
      共振  = 威科夫偏多且其余票偏多(或反向同理) → 信号获资金面支持
      背离  = 方向相反 → **重点警示**(技术信号与资金流向矛盾)
      不足  = 威科夫无明确倾向 / 其余票打平 / 排除自己后无票
    【返回】{stance, strength, align_with, oppose, text, caveat, net_txt}
    """
    acc = (wyck.get('counts') or {}).get('accumulation', 0)
    dis = (wyck.get('counts') or {}).get('distribution', 0)
    bias = wyck.get('bias') or '中性'
    dirmap = {'做多': 1, '单边做多': 1, '做空': -1, '单边做空': -1}
    ex = {str(b) for b in (exclude_books or ())}
    # 【调用点】剔除自身票, 避免循环论证
    others = [v for v in votes if str(v.get('book') or '') not in ex]
    longs = [v for v in others if dirmap.get(v.get('bias'), 0) > 0]
    shorts = [v for v in others if dirmap.get(v.get('bias'), 0) < 0]
    net_txt = '、'.join(f'{v.get("book")}({v.get("bias")})' for v in (longs + shorts)) or '无方向票'

    if not wyck.get('ok'):
        return {'stance': '不可用', 'strength': 'none', 'align_with': [], 'oppose': [],
                'text': f'威科夫事件不可用（{wyck.get("reason")}）', 'net_txt': net_txt}
    if bias == '中性':
        return {'stance': '不足', 'strength': 'none', 'align_with': [], 'oppose': [],
                'text': '威科夫**未识别到足够的量价特征事件** ⇒ 无法对其他体系做资金面确认；'
                        '这不等于"中性"，而是"没有证据"（不得当成看多/看空）。',
                'net_txt': net_txt}
    if not longs and not shorts:
        return {'stance': '无对照', 'strength': 'none', 'align_with': [], 'oppose': [],
                'text': f'威科夫为**{bias}**（吸筹 {acc} / 派发 {dis}），'
                        '但其余体系当前**没有方向性票**可对照 ⇒ 无法做资金面确认。',
                'net_txt': net_txt}

    wk_side = '多' if bias == '偏多' else '空'
    aligned, opposed = [], []
    for v in longs:
        (aligned if wk_side == '多' else opposed).append(v.get('book'))
    for v in shorts:
        (aligned if wk_side == '空' else opposed).append(v.get('book'))
    if aligned and not opposed:
        stance, strength = '共振', 'strong'
    elif opposed and not aligned:
        stance, strength = '背离', 'strong'
    elif aligned and opposed:
        stance, strength = '部分共振', 'mixed'
    else:
        stance, strength = '无对照', 'none'

    if stance == '共振':
        text = (f'威科夫事件侧别为**{bias}**（吸筹 {acc} / 派发 {dis}），'
                f'与其余体系 {"、".join(aligned)} 的方向一致 ⇒ **信号获得资金面支持**。')
    elif stance == '背离':
        text = (f'⚠ 威科夫事件侧别为**{bias}**（吸筹 {acc} / 派发 {dis}），'
                f'而 {"、".join(opposed)} 方向相反 ⇒ **技术信号与资金流向矛盾**，'
                '须先查清分歧来源（是事件识别误判，还是价格信号已领先于资金）。')
    elif stance == '部分共振':
        text = (f'威科夫为**{bias}**：与 {"、".join(aligned)} 一致；'
                f'与 {"、".join(opposed)} 相反 ⇒ 体系间本就分歧，威科夫不足以裁决。')
    else:
        text = f'威科夫为**{bias}**，但当前无方向性票可对照。'

    return {
        'stance': stance, 'strength': strength,
        'align_with': aligned, 'oppose': opposed,
        'counts': {'accumulation': acc, 'distribution': dis},
        'text': text, 'net_txt': net_txt,
        'caveat': '确认层**不参与方向票净额计算**（避免与独立投票重复计权），'
                  '且已**剔除威科夫自身的票**（否则等于拿威科夫确认威科夫）；'
                  '威科夫缺乏量化回测验证，此处仅作交叉参照。',
    }


def basis_calculation(spot_raw: float | None, futures_price: float | None,
                      adjust: float = 1.0, offset: float = 0.0,
                      unit: str = '元/吨') -> dict:
    """基差「现货折盘面」计算(cap-basis-calculation)。

    【核心(卡原文)】基差 ≠ 现货 − 期货。必须先把现货**折成盘面价**再相减:
      先处理品质价差、区域价差、数量差(磅差/干湿吨), 否则基差全盘皆错。
      折后盘面价 − 期货价 = 基差。
    【口径】折盘面 = 现货价 × adjust + offset(adjust 为折算系数, offset 为品质/区域升贴水)。
      书内示例: 华东螺纹 ÷0.97; 金布巴粉 ÷0.93 + 20.5; PB 粉 ÷0.92 + 9.0。
    【纪律】折盘面系数属**品种专属口径**, 本函数不内置任何品种的系数 —— 必须由调用方传入,
      并在输出里回显所用口径, 保证口径可追溯、可比(卡 A2 第 4 条: 固定口径才保证连续性)。
    """
    if not _is_num(spot_raw) or not _is_num(futures_price):
        return {'ok': False, 'reason': '现货价或期货价缺值 → 基差不可算(不得直接相减了事)'}
    if not _is_num(adjust) or float(adjust) == 0:
        return {'ok': False, 'reason': '折盘面系数非法(为 0 或非数值)'}
    spot_panel = float(spot_raw) * float(adjust) + float(offset)
    basis = spot_panel - float(futures_price)
    rate = (basis / float(futures_price) * 100) if float(futures_price) else None
    state = '现货升水(back)' if basis > 0 else ('现货贴水(contango)' if basis < 0 else '平水')
    return {'ok': True, 'spot_raw': _round(spot_raw), 'spot_panel': _round(spot_panel),
            'futures': _round(futures_price), 'basis': _round(basis),
            'basis_rate_pct': _round(rate, 3), 'state': state,
            'adjust': adjust, 'offset': offset, 'unit': unit,
            'desc': f'基差(折盘面后): 现货 {_round(spot_raw)} × {_round(adjust, 6)} + {_round(offset)} = '
                    f'{_round(spot_panel)}(盘面口径) − 期货 {_round(futures_price)} = '
                    f'**{_round(basis)}** ({_round(rate, 3)}%), {state}',
            'notes': ['**折盘面做错则基差全盘皆错** —— 这是所有基差类判断(顺基差/库存+基差+利润/'
                      '跨期套利)的地基(卡原文)。',
                      '折算系数须**逐品种固定并长期沿用**(卡 A2-4): 换来换去会让基差序列失去可比性。',
                      '比不同品种的升贴水幅度时应比**基差率**而非绝对值(卡 A2-2)。'],
            'card': 'duokong.cap-basis-calculation'}


def calendar_spread(term_rows: Sequence[dict], inventory_pct: float | None = None,
                    warrant_pct: float | None = None, holding_cost: float | None = None,
                    basis: float | None = None) -> dict:
    """跨期套利四逻辑(cap-calendar-spread)。

    【判据(卡原文)】正套=多近空远, 反套=空近多远。四条安全边际:
      ① 仓单: 巨量仓单做反套 / 少量仓单做正套;
      ② 基差: 贴水做正套 / 升水做反套;
      ③ 库存: 低库存做正套 / 高库存做反套;
      ④ 成本: 月差 < 持有成本做反套 / 月差 > 持有成本做正套。
    【核心纪律(卡原文)】四条逻辑**常同时指向同一方向 → 多逻辑共振时胜率更高**;
      本函数逐条给出倾向并统计共振票数, 不把四票平均成一个模糊结论。
    【盈亏比(卡原文 f28)】back 下正套 RR=基差/月差、反套=月差/基差; contango 下相反。
      选 RR ≥ 1 的机会。
    【⚠️ 为什么入参是分位而不是绝对量】卡的①②是"巨量/少量"、"高/低"这种**相对**判据。
      仓单绝对量动辄上万手/万吨, 拿一个固定阈值(如 0.5)去比会永远判同一方向 —— 那是
      把相对判据伪装成绝对阈值。故本函数只接受 **0~1 的历史分位**(1=自身历史最高),
      由调用方按序列算; 未提供分位则该条**不投票**(不猜高低)。
    """
    rows = [r for r in (term_rows or []) if _is_num(r.get('price')) and str(r.get('month') or '')]
    if len(rows) < 2:
        return {'ok': False, 'reason': f'有效合约不足({len(rows)} 个, 需 ≥2, 无法算月差)'}
    rows = sorted(rows, key=lambda r: str(r['month']))
    near_p, far_p = float(rows[0]['price']), float(rows[-1]['price'])
    month_spread = round(far_p - near_p, 2)  # 【变量】月差 = 远月 − 近月
    is_back = month_spread < 0
    votes: list[dict] = []

    def _pct_lean(p, hi_lean, lo_lean, hi_txt, lo_txt):
        """按分位给票: 高分位(≥0.67)取 hi_lean, 低分位(≤0.33)取 lo_lean, 中间为中性。"""
        if p >= 0.67:
            return hi_lean, f'分位 {p:.0%} → {hi_txt}'
        if p <= 0.33:
            return lo_lean, f'分位 {p:.0%} → {lo_txt}'
        return '中性', f'分位 {p:.0%} → 处于中间区, 不构成边际'

    # ① 仓单逻辑: 巨量(高分位)做反套 / 少量(低分位)做正套
    if _is_num(warrant_pct):
        v1, d1 = _pct_lean(float(warrant_pct), '反套', '正套', '仓单巨量 → 反套', '仓单少量 → 正套')
        votes.append({'logic': '仓单', 'lean': v1, 'detail': f'仓单{d1}'})
    # ② 基差逻辑
    if _is_num(basis):
        b = float(basis)
        v2 = '正套' if b < 0 else ('反套' if b > 0 else None)
        votes.append({'logic': '基差', 'lean': v2 or '中性',
                      'detail': f'基差 {b:.2f}（{"贴水" if b < 0 else "升水" if b > 0 else "平水"}）→ '
                                + (v2 or '不构成边际')})
    # ③ 库存逻辑: 高库存(高分位)做反套 / 低库存(低分位)做正套
    if _is_num(inventory_pct):
        v3, d3 = _pct_lean(float(inventory_pct), '反套', '正套', '高库存 → 反套', '低库存 → 正套')
        votes.append({'logic': '库存', 'lean': v3, 'detail': f'库存{d3}'})
    # ④ 成本逻辑
    if _is_num(holding_cost):
        hc = float(holding_cost)
        if abs(month_spread) < hc:
            votes.append({'logic': '成本', 'lean': '反套',
                          'detail': f'|月差| {abs(month_spread)} < 持有成本 {hc} → 反套'})
        else:
            votes.append({'logic': '成本', 'lean': '正套',
                          'detail': f'|月差| {abs(month_spread)} > 持有成本 {hc} → 正套(接近无风险)'})
    # 盈亏比(卡 f28): 正套 RR=基差/月差, 反套 RR=月差/基差 —— **同一公式**;
    #   结构(back/contango)只决定"该优先看哪一侧", 不改变公式本身(原实现把两侧按键互换, 易误读)。
    rr = None
    favored = '正套' if is_back else '反套'
    if _is_num(basis) and abs(month_spread) > 1e-9:
        bs = abs(float(basis))
        rr = {'正套': _round(bs / abs(month_spread), 2),
              '反套': _round(abs(month_spread) / bs, 2) if bs else None,
              '当前结构优选': favored}
    valid = [v for v in votes if v['lean'] in ('正套', '反套')]
    tally = {'正套': sum(1 for v in valid if v['lean'] == '正套'),
             '反套': sum(1 for v in valid if v['lean'] == '反套')}
    resonance = (tally['正套'] or tally['反套'])
    concl = ('共振指向正套(多近空远)' if tally['正套'] > tally['反套'] else
             '共振指向反套(空近多远)' if tally['反套'] > tally['正套'] else
             '票数相当或不足 → 不构成共振, 按卡纪律**不硬做**' if valid else '缺输入, 四逻辑均未触发')
    return {'ok': True, 'month_spread': month_spread, 'structure': 'back' if is_back else 'contango',
            'near': f"{rows[0]['contract']}@{near_p}", 'far': f"{rows[-1]['contract']}@{far_p}",
            'votes': votes, 'tally': tally, 'resonance_count': resonance,
            'risk_reward': rr, 'conclusion': concl,
            'desc': f'跨期套利四逻辑: 月差 {month_spread}({("back" if is_back else "contango")}) '
                    f'| 共振 {resonance} 票 → {concl}'
                    + (f' | 盈亏比 {rr}' if rr else ''),
            'notes': ['**四条逻辑共振时胜率更高**(卡原文) —— 本函数同时给出四票而非平均成一个数。',
                      '成本逻辑是"最原始、作者不常用"的一条(因持仓成本难估), 缺 holding_cost 时自动跳过。',
                      '盈亏比口径(卡 f28): back 下正套 RR=基差/月差; contango 下反套 RR=基差/月差。'
                      '**选 RR ≥ 1 的机会**。',
                      '合约生命周期(卡 f26): 远期(>2月)看成本/库存趋势、近期(1-2月)看基差收敛、'
                      '即期(<1月)看仓单交割 —— 按阶段选核心指标。'],
            'card': 'duokong.cap-calendar-spread'}


def cross_commodity_spread(leg_a: dict | None, leg_b: dict | None,
                           relation: str = '期限结构对冲', ratio: float | None = None) -> dict:
    """跨品种套利五逻辑(cap-cross-commodity-spread)。

    【判据(卡原文)五条】① 产业利润回归(高利润做空、亏损做多); ② 伴生关系(此消彼长);
      ③ 替代关系(价差超范围回归); ④ 宏观对冲; ⑤ **期限结构对冲(作者最爱)**:
      做多 back 者、做空 contango 者 —— 双重安全边际, **无需云止损**。
    【手数配比(卡原文)】跨品种按**货值匹配**(a·m·x = b·n·y)。
    【参数】leg_a/leg_b: {code, structure('back'/'contango'), price, point_value}。
      本函数只做**可机械判定的第 ⑤ 条**(期限结构对冲) + 手数配比计算;
      其余四条依赖产业逻辑判断, 输出为**检查清单**而非自动结论。
    """
    lines: list[str] = []
    pick = None
    if leg_a and leg_b and leg_a.get('structure') and leg_b.get('structure'):
        sa, sb = leg_a['structure'], leg_b['structure']
        if sa != sb:
            long_leg = leg_a if sa == 'back' else leg_b
            short_leg = leg_b if sa == 'back' else leg_a
            pick = {'long': long_leg.get('code'), 'short': short_leg.get('code')}
            lines.append(f'⑤ 期限结构对冲: 做多 back 的 {long_leg.get("code")}'
                         f' + 做空 contango 的 {short_leg.get("code")}(双重安全边际, 作者最爱)')
        else:
            lines.append(f'⑤ 期限结构对冲: 两腿同为 {sa} → **不构成该逻辑**(需一 back 一 contango)')
    # 货值匹配手数(卡原文: a·m·x = b·n·y)
    lots = None
    if (leg_a and leg_b and _is_num(leg_a.get('price')) and _is_num(leg_b.get('price'))
            and _is_num(leg_a.get('point_value')) and _is_num(leg_b.get('point_value'))):
        a, m = float(leg_b['price']), float(leg_b['point_value'])   # 空腿
        b, nn = float(leg_a['price']), float(leg_a['point_value'])  # 多腿
        if m and b:
            lots = {'多腿手数': 1, '空腿手数': _round((b * nn) / (a * m), 3),
                    'formula': f'货值匹配: {leg_a.get("code")}×1 ≈ {leg_b.get("code")}×'
                               f'{_round((b * nn) / (a * m), 3)}'}
    checklist = ['① 产业利润回归: 高利润做空产业利润(多原料空成品)/ 严重亏损做多',
                 '② 伴生关系: 同一原料的多产品此消彼长(如粕强油弱→做空油粕比)',
                 '③ 替代关系: 价差超范围 → 低价者替代(豆菜粕用单位蛋白价)',
                 '④ 宏观对冲: 经济下行多农空工 / 多黑色空有色 / 煤油比']
    return {'ok': True, 'pick': pick, 'lots': lots, 'checklist': checklist,
            'mechanized_lines': lines,
            'desc': '跨品种套利: ' + ('; '.join(lines) if lines else '未提供两腿结构, 仅输出检查清单')
                    + (f' | {lots["formula"]}' if lots else ''),
            'notes': ['本函数只机械化第 ⑤ 条(期限结构对冲); ①~④ 依赖产业逻辑, '
                      '**列为检查清单由使用者逐条判断**, 不自动出结论(避免把主观判断伪装成计算结果)。',
                      '跨品种必须先"过滤共同因素、留下差异因素"(卡原文), 否则只是放大同向风险。',
                      '手数按**货值匹配**而非手数 1:1(卡原文); 跨期套利才是 1:1 数量匹配。'],
            'card': 'duokong.cap-cross-commodity-spread'}


def shrinkage_ladder(data_years: float) -> dict:
    """Carver 收缩系数阶梯(cap-shrinkage-ladder)。

    【口径(卡原文, 注意方向)】**0 = 纯用估计值, 1 = 完全收缩到先验(等权)**。
      数据越多越信估计(少收缩), 越少越退向等权(多收缩)。
    【阶梯表】≥5年: SR 0.5 / 相关 0.75; 4-5年: 0.6 / 0.75; 3-4年: 0.7 / 0.80;
      2-3年: 0.8 / 0.85; 1-2年: 0.9 / 0.90; ≤1年: 1.0 / 1.0(等权)。
    【核心纪律(卡原文)】**SR 与相关矩阵两个轴要分开设**收缩量, 不可共用一个系数。
    """
    if not _is_num(data_years) or float(data_years) <= 0:
        return {'ok': False, 'reason': '可用样本年限缺失或非正 → 无法查表'}
    y = float(data_years)
    table = [(5.0, 0.5, 0.75, '五年以上'), (4.0, 0.6, 0.75, '四到五年'),
             (3.0, 0.7, 0.80, '三到四年'), (2.0, 0.8, 0.85, '两到三年'),
             (1.0, 0.9, 0.90, '一到两年'), (0.0, 1.0, 1.0, '一年以内')]
    for thr, sr_s, cor_s, label in table:
        if y >= thr:
            break
    return {'ok': True, 'data_years': y, 'band': label,
            'sr_shrinkage': sr_s, 'corr_shrinkage': cor_s,
            'desc': f'收缩阶梯({label}, 样本 {y:g} 年): SR 收缩 **{sr_s}** / 相关收缩 **{cor_s}**'
                    f'(0=用估计值, 1=完全退向等权)',
            'notes': ['两个轴**分开设**: SR 一个收缩量、相关矩阵另一个(卡原文核心纪律)。',
                      '与作者早期文章的 λ 方向相反, 引用"0.5/0.75"时须确认用的是哪套口径。',
                      '最优面**很平坦**: "Use a vaguely okay value and you\'ll do vaguely ok"'
                      '(卡原文) → 不必追求精确最优。'],
            'card': 'carver.cap-shrinkage-ladder'}


def estimate_reliability() -> dict:
    """Carver 估计可靠性判据(cap-estimate-reliability)。

    【结论(卡原文)】以归一化 RMSE 度量(真实数据 vs 同长度随机数据的中位数比值):
      **SR ≈ 1.06**(几乎与随机数据一样好估), **相关性 ≈ 5.6**(差得多)。
    【推论】真实数据里**相关性比均值难估得多** → 应**多收缩相关性、少收缩均值**。
      与"均值难估所以重罚均值"的直觉相反。
    """
    return {'ok': True, 'sr_ratio': 1.06, 'corr_ratio': 5.6,
            'desc': '参数可靠性: SR 的预测误差约为随机数据的 **1.06×**(几乎一样), '
                    '相关性约为 **5.6×**(差得多) → **多收缩相关、少收缩均值**',
            'notes': ['推论直接支撑收缩阶梯里"相关收缩量 > SR 收缩量"'
                      '(如 ≥5 年: SR 0.5 / 相关 0.75)这一看似反直觉的配置。',
                      '该比值在 5 年、10 年前瞻下几乎不变(卡原文)。',
                      '本卡是**判据/清单类**(给出纪律而非某个品种的读数), 无输入、恒可算, '
                      '故不依赖行情数据。'],
            'card': 'carver.cap-estimate-reliability'}


def virtual_real_ratio(oi_main: float | None, warrant_latest: float | None,
                       oi_prev: float | None = None, warrant_prev: float | None = None,
                       contract_size: float | None = None,
                       basis: float | None = None) -> dict:
    """虚实盘比与逼仓识别(cap-warrant-basis)。

    【判据(卡原文)】虚盘 = 主力持仓量 ÷ 2; 实盘 = 仓单数量**折成盘面手数**;
      虚实比 = 虚盘/实盘。比值大 = 钱多货少 → 易涨(多逼空); 比值小 = 货多钱少 → 易跌(空逼多)。
      作者明言**无固定阈值**, 需结合距交割远近 → 本函数不设阈值、只报水平与方向。
    【多逼空三特征】期货贴水 + 仓单少(虚实比大) + 现货坚挺 → 单边做多;
      【空逼多三特征】期货升水 + 巨量仓单(虚实比小) + 现货疲软 → 单边做空。
    【两个安全边际(卡原文)】近月临近交割: ①期货升水则**不做多**、期货贴水则**不做空**;
      ②虚实比大(可能多逼空)**不做空**、虚实比小(可能空逼多)**不做多**。
    【⚠️ 单位口径】实盘要"折成盘面手数" = 仓单量 ÷ 合约乘数。平台的仓单序列单位未随数据
      一同给出 → 未传 contract_size 时**只输出相对变化**(比值水平不输出, 不猜单位)。
      比值的**相对变化**与单位无关(分子分母的常数在百分比里约掉), 故方向判断始终可靠。
    """
    if not _is_num(oi_main) or not _is_num(warrant_latest) or float(warrant_latest) <= 0:
        return {'ok': False, 'reason': '主力持仓或仓单缺值/非正 → 虚实比不可算'}
    virtual = float(oi_main) / 2.0
    level = None
    if _is_num(contract_size) and float(contract_size) > 0:
        real = float(warrant_latest) / float(contract_size)  # 实盘手数
        if real > 0:
            level = _round(virtual / real, 3)
    # 相对变化(单位无关): 虚实比变化 ≈ 虚盘变化 − 实盘变化
    chg = None
    if _is_num(oi_prev) and float(oi_prev) > 0 and _is_num(warrant_prev) and float(warrant_prev) > 0:
        chg = _round(((float(oi_main) / float(oi_prev)) / (float(warrant_latest) / float(warrant_prev)) - 1) * 100, 2)
    # 逼仓倾向(用"变化方向 + 基差"定, 不用水平阈值)
    lean, feat = '中性', []
    # 【命名】basis = 现货 − 期货, 故 basis>0 表示**现货升水/期货贴水**(backwardation)。
    in_backwardation = None if not _is_num(basis) else (float(basis) > 0)
    if in_backwardation is True:
        feat.append('期货贴水')
    elif in_backwardation is False:
        feat.append('期货升水')
    if chg is not None:
        feat.append(f'虚实比{"走扩" if chg > 0 else "收敛"}({chg:+.1f}%)')
    if in_backwardation is True and (chg or 0) > 0:
        lean = '偏多逼空(贴水 + 虚实比走扩 → 钱多货少)'
    elif in_backwardation is False and (chg or 0) < 0:
        lean = '偏空逼多(升水 + 虚实比收敛 → 货多钱少)'
    # 安全边际
    margins: list[str] = []
    if in_backwardation is True:
        margins.append('期货贴水 → **不做空**(卡原文安全边际①)')
    elif in_backwardation is False:
        margins.append('期货升水 → **不做多**(卡原文安全边际①)')
    if chg is not None and chg > 0:
        margins.append('虚实比走扩(可能多逼空) → **不做空**(安全边际②)')
    elif chg is not None and chg < 0:
        margins.append('虚实比收敛(可能空逼多) → **不做多**(安全边际②)')
    return {'ok': True, 'virtual': _round(virtual), 'ratio': level, 'ratio_chg_pct': chg,
            'basis_features': feat, 'lean': lean, 'safety_margins': margins,
            'desc': f'虚实盘比: 虚盘(持仓/2) {_round(virtual)}'
                    + (f' / 实盘 → 比值 {level}' if level is not None
                       else ' | 未提供合约乘数 → **只报相对变化不报比值水平**(不猜仓单单位)')
                    + (f' | 比值变化 {chg:+.1f}%' if chg is not None else ' | 变化不可算')
                    + f' → {lean}',
            'notes': ['作者明言虚实比**无固定阈值**, 须结合距交割远近判断 → 本函数不设阈值。',
                      '移仓早多为凹型(不太可能逼仓)、移仓晚多为凸型(极可能逼仓); contango 凹、back 凸。',
                      '两个安全边际是"防收割铁律": 升水不做多 / 贴水不做空; 比大不做空 / 比小不做多。',
                      '仓单折手数需**合约乘数**; 未传时只给相对变化(单位无关), 避免用错单位得出错误水平值。'],
            'card': 'duokong.cap-warrant-basis'}


def reversal_signals(bars: Sequence[dict], basis_rate_series: Sequence[float] | None = None,
                     oi_series: Sequence[float] | None = None, lookback: int = 5) -> dict:
    """行情反转三信号(cap-reversal-signals)。

    【判据(卡原文)】三信号用于**离场预警**, 不是摸顶抄底(作者明确推荐右侧交易):
      ① 价格处于高位/低位 + **基差修复后** → 需谨慎(高位基差修复=转折而非延续);
      ② **日内**频繁基差升贴水转换 → 预期一致性转变、行情末端;
      ③ **持仓量大幅下降** → 多空一方认输离场, 行情基本结束。
    【⚠️ 本函数能算几条】(如实分列, 不把不可算的混算成"没问题")
      ① 可算: 价格历史分位 + 基差从极值回落的幅度;
      ② **不可算**: 需**日内**逐笔/分钟级基差序列, 平台只有日频 → 显式标注不可得;
      ③ 可算: 持仓量近 N 日降幅。
      净多单率(f39)需前 20 会员持仓数据 → 平台未接入, 一并标注不可得。
    """
    rows = [b for b in bars if _is_num(b.get("close"))]
    if len(rows) < 30:
        return {'ok': False, 'reason': f'日线不足({len(rows)} 根, 需 ≥30)'}
    closes = [float(b["close"]) for b in rows]
    cur = closes[-1]
    rank = sum(1 for c in closes if c <= cur) / len(closes)  # 价格分位
    high_pos = rank >= 0.85
    low_pos = rank <= 0.15
    sig: list[dict] = []
    # ① 高低位 + 基差修复
    br = [float(v) for v in (basis_rate_series or []) if _is_num(v)]
    s1 = {'no': 1, 'name': '价格高低位 + 基差修复后', 'computed': False}
    if len(br) >= 10:
        recent_peak = max(abs(v) for v in br[-20:])
        now_abs = abs(br[-1])
        repaired = recent_peak > 0 and now_abs <= recent_peak * 0.5  # 基差收敛过半
        s1.update({'computed': True, 'price_rank': _round(rank, 3),
                   'basis_now': _round(br[-1], 3), 'basis_recent_peak_abs': _round(recent_peak, 3),
                   'repaired': repaired,
                   'triggered': bool((high_pos or low_pos) and repaired)})
    else:
        s1['reason'] = f'基差率序列仅 {len(br)} 点(需 ≥10)'
    sig.append(s1)
    # ② 日内频繁基差转换 —— 结构性不可得
    sig.append({'no': 2, 'name': '日内频繁基差升贴水转换', 'computed': False,
                'reason': '需**日内**基差序列; 平台仅有日频基差 → 结构性不可算(如实披露不假算)'})
    # ③ 持仓量大幅下降
    ois = [float(v) for v in (oi_series or []) if _is_num(v)]
    s3 = {'no': 3, 'name': '持仓量大幅下降', 'computed': False}
    if len(ois) >= lookback + 1 and ois[-1 - lookback]:
        d = (ois[-1] / ois[-1 - lookback] - 1) * 100
        s3.update({'computed': True, 'oi_chg_pct': _round(d, 2),
                   'triggered': bool(d <= -10)})  # 大幅下降口径: ≥10%
    else:
        s3['reason'] = '日线缺持仓量(open_interest)或点数不足'
    sig.append(s3)
    trig = [s['no'] for s in sig if s.get('triggered')]
    return {'ok': True, 'signals': sig, 'price_rank': _round(rank, 3),
            'triggered_count': len(trig), 'triggered': trig,
            'verdict': (f'反转预警信号命中 {len(trig)} 条(第 {trig} 条)' if trig
                        else '三条中可算的两条均未触发'),
            'desc': f'反转三信号: 命中 {len(trig)} 条' + (f'（第 {trig} 条）' if trig else '')
                    + f' | 价格分位 {_round(rank, 3)}'
                    + ('（高位）' if high_pos else '（低位）' if low_pos else ''),
            'notes': ['三信号是**离场预警**而非反手信号(作者推荐右侧交易, 不主张摸顶抄底)。',
                      '②需日内基差、净多单率需前 20 会员持仓 → 平台均未接入, **如实披露不可算**, '
                      '不得因"没报警"就当作"没有反转风险"。',
                      '③"大幅下降"本函数口径取近 %d 日 ≥10%%; 卡原文未给统一阈值, 属操作化设定。' % lookback,
                      '高位大幅贴水=假贴水(现货提前见顶)、低位大幅升水=假升水 → 不可再按顺基差方向操作。'],
            'card': 'duokong.cap-reversal-signals'}


# ══════════════════════════════════════════════════════════════════════════
# ⑨d 跨品种候选配对表(cap-cross-commodity-spread 的第二腿知识)
# 【为什么必须显式建表】跨品种套利需要"另一条腿", 而单品种接口里只有当前品种。
#   若不显式声明配对关系, 代码只能瞎配一个品种 —— 那是把随机选择伪装成方法结论。
#   本表把**有明确产业逻辑**的配对固定下来(依据卡原文五逻辑分类), 运行时按当前品种
#   查表取对腿, 并**只机械化第 ⑤ 条(期限结构对冲)**, 其余四条照旧走检查清单。
# 【字段】relation 取卡原文五逻辑之一; note 写该配对凭什么成立(供披露, 不许省略)。
# 【边界】这是**候选**配对清单, 不是"最优配对"结论; 逻辑④宏观对冲类仅在极端行情用。
# ══════════════════════════════════════════════════════════════════════════
CROSS_PAIRS: dict[str, list[dict]] = {
    # ── 能化: 芳烃/聚酯链 ──
    'PX': [{'leg': 'TA', 'relation': '①产业利润回归', 'note': 'PX 是 PTA 的直接原料 → 多 PX 空 TA = 做空 PTA 加工利润'},
           {'leg': 'EB', 'relation': '②伴生关系', 'note': '同属芳烃族(PX 与苯), 调油/化工需求此消彼长'},
           {'leg': 'SC', 'relation': '①产业利润回归', 'note': '原油 → 石脑油 → PX, 上下游利润分配'}],
    'TA': [{'leg': 'PX', 'relation': '①产业利润回归', 'note': 'PTA 加工利润的另一面(与 PX 互为对手腿)'},
           {'leg': 'EG', 'relation': '②伴生关系', 'note': 'PTA 与乙二醇同为聚酯两大原料, 竞争/替代'}],
    # ── 能化: 烯烃/苯链 ──
    'EB': [{'leg': 'PS', 'relation': '①产业利润回归', 'note': '苯乙烯 → 聚苯乙烯, 多 EB 空 PS = 做空 PS 加工利润'},
           {'leg': 'PX', 'relation': '②伴生关系', 'note': '芳烃族内部(苯 ↔ 对二甲苯)'}],
    'PS': [{'leg': 'EB', 'relation': '①产业利润回归', 'note': 'PS 成本端就是 EB, 与 EB 互为对手腿'}],
    # ── 能化: 油品 ──
    'SC': [{'leg': 'FU', 'relation': '①产业利润回归', 'note': '原油→燃料油, 做空炼油利润(空 FU 多 SC 的镜像)'},
           {'leg': 'LU', 'relation': '①产业利润回归', 'note': '原油→低硫燃料油'},
           {'leg': 'PX', 'relation': '①产业利润回归', 'note': '原油→石脑油→PX'}],
    'FU': [{'leg': 'LU', 'relation': '③替代关系', 'note': '高硫/低硫燃料油规格替代, 价差超范围回归'},
           {'leg': 'SC', 'relation': '①产业利润回归', 'note': '燃料油裂解价差(对原油)'}],
    'LU': [{'leg': 'FU', 'relation': '③替代关系', 'note': '与高硫燃料油互为替代(脱硫成本定价差)'},
           {'leg': 'SC', 'relation': '①产业利润回归', 'note': '低硫燃料油裂解价差(对原油)'}],
    'PG': [{'leg': 'SC', 'relation': '②伴生关系', 'note': 'LPG 与原油同源(油气伴生), 但季节性需求可分化'}],
    # ── 橡胶 ──
    'RU': [{'leg': 'NR', 'relation': '②伴生关系', 'note': '国产全乳胶与进口标胶, 同受产区气候驱动'},
           {'leg': 'BR', 'relation': '③替代关系', 'note': '天然橡胶 vs 顺丁橡胶: 轮胎原料替代, 价差有替代上下限'}],
    'NR': [{'leg': 'RU', 'relation': '②伴生关系', 'note': '20 号标胶与全乳胶互为对照(升贴水定结构)'}],
    'BR': [{'leg': 'RU', 'relation': '③替代关系', 'note': '合成胶与天然胶的替代价差(丁二烯成本 vs 胶价)'}],
    # ── 新能源材料 ──
    'SI': [{'leg': 'PS', 'relation': '①产业利润回归', 'note': '工业硅 → 多晶硅, 上游利润分配'}],
    # ── 农产品 ──
    'M': [{'leg': 'P', 'relation': '②伴生关系', 'note': '大豆压榨同产豆粕与油脂: 粕强油弱 → 做空油粕比'},
          {'leg': 'LH', 'relation': '①产业利润回归', 'note': '豆粕是饲料主料 → 养殖利润的另一端(弱逻辑, 传导慢)'}],
    'P': [{'leg': 'M', 'relation': '②伴生关系', 'note': '与豆粕互为压榨产物(油粕比)'}],
}


def cross_pair_of(code: str) -> list[dict]:
    """查当前品种的候选对腿(未登记返回 [] → 调用方如实披露"无登记配对")。"""
    return list(CROSS_PAIRS.get((code or '').strip().upper(), []))


# ══════════════════════════════════════════════════════════════════════════
# ⑩ 输出契约组装: 冲突并列 + 缺口披露(不能省)
# ══════════════════════════════════════════════════════════════════════════
def build_conflicts(direction_luxiwu: str | None, murphy_break: dict | None) -> list[dict]:
    """按 SKILL.md 第 3 条组装**必须并列陈述**的冲突项(禁止混用)。

    【典型冲突】
      1. 突破有效性: 墨菲「3% 穿越 + 2 天」vs 鹿希武「双线是否同时破」。
      2. 止损距离: 墨菲(支撑位下方) vs 鹿希武(唯一 B 点) vs Carver(波动率定标) ——
         同一笔单可差数倍; **落地必须选一套并全程一致**。
      3. 盈亏比闸门: 墨菲 ≥3:1 vs 鹿希武 >1。
    """
    out: list[dict] = []
    if murphy_break and murphy_break.get('ok'):
        out.append({
            'topic': '突破有效性',
            'murphy': f"3% 穿越 + 2 天原则 → {murphy_break['desc']}",
            'luxiwu': f'趋势线+拐点线**同时**破才算趋势结束(当前几何判定: {direction_luxiwu})',
            'resolution': '并列陈述; 未指定体系时两套结论都要列出, 不得取平均。',
        })
    out.append({
        'topic': '止损距离',
        'murphy': '支撑位下方(结构位)',
        'luxiwu': '唯一 B 点(双线被破处) + 三条离散上移许可',
        'carver': '按波动率定标(risk target 反推)',
        'resolution': '三者对同一笔单给出的止损距离可能相差数倍 → **落地必须选一套并全程一致**'
                      '(本模块默认按鹿希武 B 点口径), 混用会让回测的盈亏比失真。',
    })
    out.append({
        'topic': '盈亏比闸门',
        'murphy': '盈亏比 ≥ 3:1 才执行',
        'luxiwu': '盈亏比率 > 1 即可执行(本书常态 1~3)',
        'resolution': '两套阈值差异显著 → 并列陈述, 由用户选定体系后全程一致。',
    })
    return out


def build_evidence(cards: Sequence[str], extra_gaps: Sequence[str] | None = None) -> dict:
    """组装「依据与边界」段: 用了哪几张卡 + 已知缺口(含强制披露的鹿希武 N1/N2/N3)。

    【为什么强制】SKILL.md 第 5 条: 四段式**缺段视为未完成**; 且「该结论的已知缺口
    (鹿希武线须点明 N1/N2/N3)」是强制项。
    """
    used = [c for c in cards if c]
    by_book: dict[str, list[str]] = {}  # 【变量】按书归组
    for c in used:
        book = c.split('.')[0] if '.' in c else 'other'
        by_book.setdefault(book, []).append(c)
    return {
        'cards_used': used,
        'cards_by_book': {BOOKS.get(k, k): v for k, v in by_book.items()},
        'luxiwu_gaps': list(LUXIWU_GAPS),
        'extra_gaps': list(extra_gaps or []),
        'disclaimers': list(GENERAL_DISCLAIMERS),
    }


READ_ORDER: list[str] = [  # 【变量】固定读序(SKILL.md: 顺序不可颠倒)
    '① 方向(排除法三态)', '② 入场(价位+触发)', '③ 风控(止损价+金额+手数)',
    '④ 成本(净期望 = 毛期望 − 成本)', '⑤ 校验(结论可不可信)',
]
