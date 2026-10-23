# -*- coding: utf-8 -*-
"""L1b：接入 vote_aggregate，把四象拆成两轴，输出并列的 weight
"""
import io

ROOT = "D:/AgentSense/AgentSense/"
P = "quant_verdict.py"
s = io.open(ROOT + P, encoding="utf-8", newline="").read()
NL = "\r\n"
J = NL.join


def once(s, old, new, tag):
    n = s.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次" % (tag, n)
    return s.replace(old, new, 1)


# 1) 导入
s = once(s,
         "import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）",
         J(["import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）",
            "import vote_aggregate as vag  # 【调用模块】按数据轴聚合 → 加权总信号(L1b)"]),
         "import")

# 2) 带符号强度助手
s = once(s,
         J(['    try:',
            '        return max(0.0, min(1.0, float(x)))',
            '    except (TypeError, ValueError):',
            '        return 0.0',
            ]),
         J(['    try:',
            '        return max(0.0, min(1.0, float(x)))',
            '    except (TypeError, ValueError):',
            '        return 0.0',
            '',
            '',
            'def _signed(x: Any, denom: float = 3.0) -> float:',
            '    """带符号的强度：s·m ∈ [-1,1]。供四象按轴拆分时使用。',
            '',
            '    【参数 denom】原始分所在的量纲上限（四象的估值/驱动都映射到 −3~+3）。',
            '    """',
            '    try:',
            '        v = float(x or 0)',
            '    except (TypeError, ValueError):',
            '        return 0.0',
            '    if v == 0:',
            '        return 0.0',
            '    return (1.0 if v > 0 else -1.0) * _m01(abs(v) / denom)',
            ]),
         "signed")

# 3) 四象拆轴分量
s = once(s,
         J(['                          "strength_basis": "min(|估值|,|驱动|)/3 (两轴同向才算单边, 取短板)"})']),
         J(['                          "strength_basis": "min(|估值|,|驱动|)/3 (两轴同向才算单边, 取短板)",',
            '                          # ★ L1b：拆轴分量（带符号强度）。由投票方在源头拆，',
            '                          #   聚合层只消费不解释——否则 aggregate 就得反推四象的内部口径。',
            '                          #   估值轴取负号：估值越低(越负)=越偏多。',
            '                          "axis_parts": {',
            '                              "basis": _signed(-(vd.get("value") or 0)),',
            '                              "inventory": _signed(vd.get("drive") or 0),',
            '                          }})']),
         "vd parts")

# 4) 计算 weighted（紧跟 net 之后，不影响 net）
s = once(s,
         J(['    net, vote_audit = vs.net_of(dir_votes)  # 【变量】方向净票 + 计入审计']),
         J(['    net, vote_audit = vs.net_of(dir_votes)  # 【变量】方向净票 + 计入审计',
            '    # 【L1b】加权总信号：与 net **并列**输出, 不参与 verdict 判定。',
            '    #   等一段时间的实盘对照后, 再决定要不要让 verdict 改看它。',
            '    weighted = vag.aggregate(dir_votes)  # 【变量】按轴加权的总信号']),
         "weighted calc")

# 5) 挂进响应
s = once(s,
         J(['        "vote_audit": vote_audit,  # 【变量】哪些票计入了/哪些没被认出']),
         J(['        "vote_audit": vote_audit,  # 【变量】哪些票计入了/哪些没被认出',
            '        "weighted": weighted,  # 【变量】加权总信号(与 net 并列, 不替代)']),
         "expose")

io.open(ROOT + P, "w", encoding="utf-8", newline="").write(s)
print("L1b 补丁完成；CRLF=%d bareLF=%d" % (s.count("\r\n"), s.count("\n") - s.count("\r\n")))
