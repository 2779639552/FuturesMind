# -*- coding: utf-8 -*-
"""L1a：给每张方向票补 axis / strength / confidence / strength_basis。

【纪律】只加字段，**不改任何判断**：bias 不变、net 不变、verdict 不变。
  验证方式不是"看起来没变"，而是 ① 单测断言 net 只读 bias；② 改前改后同一 payload 的
  build_verdict 输出逐字段比对（bias/net/verdict 必须完全相同）。

【strength 的统一口径】"这张票对**它自己的 bias** 有多确信"，落在 [0,1]。
  · 非方向票（ADXR / 背离 / 突破）→ 0：它们本来就不指方向，给强度是自欺。
  · 二值方向票（鹿希武）→ 1.0：几何只判趋势成立与否，没有连续尺度，不要硬造。
  · 有连续尺度的 → 各自归一，公式写进 strength_basis，可复核。

【confidence 的口径】"这张票自己的数据靠不靠得住"，与方向无关。
  目前只有两类会打折：① 库存变化 >100%（小基数失真，仓单 1,000→19,590 就是实例）；
  ② 威科夫无事件（没有证据 ≠ 中性，可信度降低）。
"""
import io

ROOT = "D:/AgentSense/AgentSense/"
P = "quant_verdict.py"


def load(p):
    s = io.open(ROOT + p, encoding="utf-8", newline="").read()
    return s, ("\r\n" if "\r\n" in s else "\n")


def once(s, old, new, tag):
    n = s.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次（应为 1）" % (tag, n)
    return s.replace(old, new, 1)


s, NL = load(P)
J = NL.join


# ── 0. 强度夹取工具 ──
s = once(s,
         "import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）",
         J(["import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）",
            "",
            "",
            "def _m01(x: Any) -> float:",
            '    """把强度夹到 [0,1]；非数值 / None → 0。',
            "",
            "    【为什么必须夹】强度要参与下游加权，任何一个异常值都会把总分带飞——",
            "      最现实的例子是库存变化 +1855%（小基数失真），不夹就会让那一票的强度爆表。",
            '    """',
            "    try:",
            "        return max(0.0, min(1.0, float(x)))",
            "    except (TypeError, ValueError):",
            "        return 0.0",
            ]),
         "helper")


# ── 1. 鹿希武（price）──
s = once(s,
         J(['        dir_votes.append({"book": "鹿希武", "bias": bias, "detail": detail,',
            '                          "basis": "几何画线(趋势线+拐点线+分界点A)"})']),
         J(['        dir_votes.append({"book": "鹿希武", "bias": bias, "detail": detail,',
            '                          "basis": "几何画线(趋势线+拐点线+分界点A)",',
            '                          "axis": "price",',
            '                          "strength": 1.0 if bias in ("做多", "做空") else 0.0,',
            '                          "confidence": 1.0,',
            '                          "strength_basis": "二值方向票: 几何只判趋势成立与否, 无连续强度尺度"})']),
         "luxiwu")


# ── 2. 墨菲 ADXR（price，不给方向）──
s = once(s,
         J(['            "basis": "ADXR 趋势性评级(不做方向, 只判该不该用趋势系统)",',
            '        })']),
         J(['            "basis": "ADXR 趋势性评级(不做方向, 只判该不该用趋势系统)",',
            '            "axis": "price",',
            '            "strength": 0.0,',
            '            "confidence": 1.0,',
            '            "strength_basis": f"不进方向(只判趋势系统可用性); ADXR={adxr[\'adxr\']} 可作 L2 的轴内权重参考",',
            '        })']),
         "adxr")


# ── 3. 二维四象（composite：横跨基差+库存两根轴）──
s = once(s,
         J(['        dir_votes.append({"book": "多空逻辑", "bias": vd["advice"],',
            '                          "detail": f"{vd[\'quadrant\']}（估值 {vd[\'value\']} / 驱动 {vd[\'drive\']}）",',
            '                          "basis": "基差率(估值)+ 库存分位(驱动) 二维四象"})']),
         J(['        dir_votes.append({"book": "多空逻辑", "bias": vd["advice"],',
            '                          "detail": f"{vd[\'quadrant\']}（估值 {vd[\'value\']} / 驱动 {vd[\'drive\']}）",',
            '                          "basis": "基差率(估值)+ 库存分位(驱动) 二维四象",',
            '                          # ★ 它横跨两根轴(基差估值 + 库存驱动), 故单列 composite;',
            '                          #   L2 要拆成两票才能按轴加权, 现在不拆(拆了会改 net)。',
            '                          "axis": "composite",',
            '                          # 强度取**较弱的那条轴**/3: 四象只按两轴符号出"单边",',
            '                          #   实际强度应由短板决定(一条轴满格、另一条刚过零, 不该算强)。',
            '                          "strength": _m01(min(abs(vd.get("value") or 0),',
            '                                                abs(vd.get("drive") or 0)) / 3.0),',
            '                          "confidence": 1.0,',
            '                          "strength_basis": "min(|估值|,|驱动|)/3 (两轴同向才算单边, 取短板)"})']),
         "vd")


# ── 4. 期限结构（basis）──
s = once(s,
         J(['            "basis": "跨月价差形态(品种态定位, 非择时信号)",',
            '        })']),
         J(['            "basis": "跨月价差形态(品种态定位, 非择时信号)",',
            '            "axis": "basis",',
            '            "strength": _m01(abs(tsr.get("spread_pct") or 0) / 3.0),',
            '            "confidence": 1.0,',
            '            "strength_basis": "|跨月价差%|/3 (3% 价差视为满强度)",',
            '        })']),
         "term")


# ── 5. 库存周期（price；方向≡价格）──
s = once(s,
         J(['            "basis": "库存方向 × 价格方向 四阶段(品种态, 速度/方向优先于绝对值)",',
            '        })']),
         J(['            "basis": "库存方向 × 价格方向 四阶段(品种态, 速度/方向优先于绝对值)",',
            '            # ★ 归 price 轴：四阶段表里(降,涨)→多、(升,涨)→多、(升,跌)→空、(降,跌)→空，',
            '            #   方向上等价于"价格涨→多 / 价格跌→空"，库存方向只改标签。',
            '            "axis": "price",',
            '            "strength": _m01(abs(icp.get("price_chg_pct") or 0) / 5.0),',
            '            # 库存变化 >100% 多半是小基数失真(仓单 1,000→19,590 实测), 方向仍可参考但可信度降。',
            '            "confidence": 0.4 if abs(float(icp.get("inv_chg_pct") or 0)) > 100 else 1.0,',
            '            "strength_basis": "|价格5日变化%|/5; 库存变化>100% 记为小基数失真 → confidence 0.4",',
            '        })']),
         "cyc")


# ── 6. 利润（profit）──
s = once(s,
         J(['                              "basis": "\'库存+基差+利润\' 交易系统的第三维(慢变量, 作校验用)"})']),
         J(['                              "basis": "\'库存+基差+利润\' 交易系统的第三维(慢变量, 作校验用)",',
            '                              "axis": "profit",',
            '                              "strength": _m01(abs(pct - 0.5) / 0.5),',
            '                              "confidence": 1.0,',
            '                              "strength_basis": "|利润分位-0.5|/0.5 (越贴 0 或 1 越强; 0.3/0.7 只是触发阈值)"})']),
         "profit")


# ── 7. 墨菲背离（price，不给方向）──
s = once(s,
         J(['                          "basis": "RSI 背离(须在极限区)"})']),
         J(['                          "basis": "RSI 背离(须在极限区)",',
            '                          "axis": "price",',
            '                          "strength": 0.0,',
            '                          "confidence": 1.0,',
            '                          "strength_basis": "预警类, 不给方向; L2 用作 price 轴的可信度折扣"})']),
         "div")


# ── 8. 墨菲突破（price，不给方向）──
s = once(s,
         J(['                              "basis": "3% 穿越 + 2 天原则(与鹿希武双线口径冲突, 并列不混用)"})']),
         J(['                              "basis": "3% 穿越 + 2 天原则(与鹿希武双线口径冲突, 并列不混用)",',
            '                              "axis": "price",',
            '                              "strength": 0.0,',
            '                              "confidence": 1.0,',
            '                              "strength_basis": "口径对照, 不给方向"})']),
         "break")


# ── 9. 威科夫（price）──
s = once(s,
         J(['            "basis": "量价行为事件识别(SC/BC/AR/ST/Spring/UT/SOS/SOW)—— 只标事件不判阶段",',
            '        })']),
         J(['            "basis": "量价行为事件识别(SC/BC/AR/ST/Spring/UT/SOS/SOW)—— 只标事件不判阶段",',
            '            "axis": "price",',
            '            "strength": _m01(abs((_wc.get("accumulation") or 0)',
            '                                  - (_wc.get("distribution") or 0)) / 2.0),',
            '            # 没有事件 ≠ 中性: 是"没证据", 可信度必须低于有事件时。',
            '            "confidence": 1.0 if _ev2 else 0.3,',
            '            "strength_basis": "|吸筹-派发|/2; 无事件时 confidence 0.3(没证据≠中性)",',
            '        })']),
         "wyck")


io.open(ROOT + P, "w", encoding="utf-8", newline="").write(s)
print("L1a 补丁完成；CRLF=%d bareLF=%d" % (s.count("\r\n"), s.count("\n") - s.count("\r\n")))
