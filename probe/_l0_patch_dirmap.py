# -*- coding: utf-8 -*-
"""L0 补丁：把 4 个文件里各抄一份的 dirmap 统一到 vote_semantics。

【为什么用脚本而不是手改】四个文件、两种行尾（CRLF/LF）、5 个锚点。
  手改最容易踩的坑就是行尾被写坏（Windows 上 Path.write_text 会转 CRLF），
  那会让后续所有锚点失配、且没有任何报错 —— 所以这里逐文件按原行尾读写。
"""
import io
import sys

ROOT = "D:/AgentSense/AgentSense/"


def load(p):
    s = io.open(ROOT + p, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in s else "\n"
    return s, nl


def save(p, s):
    io.open(ROOT + p, "w", encoding="utf-8", newline="").write(s)


def once(s, old, new, tag):
    n = s.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次（应为 1）" % (tag, n)
    return s.replace(old, new, 1)


report = []

# ══════════════ 1. quant_verdict.py（CRLF） ══════════════
p = "quant_verdict.py"
s, NL = load(p)

s = once(s,
         "import quant_methodology as qm  # 【调用模块】方法论纯函数库",
         "import quant_methodology as qm  # 【调用模块】方法论纯函数库" + NL
         + "import vote_semantics as vs  # 【调用模块】方向票语义唯一权威来源（dirmap 已废弃）",
         "qv import")

s = once(s,
         NL.join([
             '    # 结论: 只取**方向性票**(排除"暂不操作/不可用/预警/提示")',
             '    dirmap = {"做多": 1, "单边做多": 1, "做空": -1, "单边做空": -1}',
             '    net = sum(dirmap.get(v["bias"], 0) for v in dir_votes)  # 【变量】方向净票',
         ]),
         NL.join([
             '    # 结论: 只取**方向性票** —— 映射统一在 vote_semantics, 禁止在此另写一份。',
             '    # 【2026-10-19 修复】原 dirmap 缺「偏多/偏空」⇒ 期限结构票永远静默计 0：',
             '    #   实测 TA 9 张票只有 3 张进净票(4 张哑票)。审计随净票一起返回, 便于发现',
             '    #   "新加了一张票却忘了登记" —— 这正是隐掉 44% 票数而不报错的根因。',
             '    net, vote_audit = vs.net_of(dir_votes)  # 【变量】方向净票 + 计入审计',
         ]),
         "qv net")

s = once(s,
         NL.join(['        "votes": dir_votes,']),
         NL.join(['        "votes": dir_votes,',
                  '        "vote_audit": vote_audit,  # 【变量】哪些票计入了/哪些没被认出']),
         "qv stage")

save(p, s)
report.append(("quant_verdict.py", s.count("\r\n"), s.count("\n") - s.count("\r\n")))

# ══════════════ 2. fund_module.py（LF） ══════════════
p = "fund_module.py"
s, NL = load(p)

s = once(s,
         "from flask import Blueprint, jsonify, request",
         "from flask import Blueprint, jsonify, request" + NL
         + "import vote_semantics as _vs  # 【调用模块】方向票语义唯一权威来源",
         "fm import")

s = once(s,
         NL.join([
             '            dirmap = {"做多": 1, "单边做多": 1, "做空": -1, "单边做空": -1}',
             '            keys = ("term", "cyc", "prof")',
             '            row["skill_net"] = sum(dirmap.get(str(row.get("skill_" + k)), 0) for k in keys)',
         ]),
         NL.join([
             '            keys = ("term", "cyc", "prof")',
             '            # 【2026-10-19】方向映射统一走 vote_semantics：原 dirmap 漏了「偏多/偏空」，',
             '            #   而前端 _FRK_TEXT_RANK 一直把「偏多」当 +1 ⇒ 前后端口径不一致。',
             '            row["skill_net"] = sum(_vs.sign(row.get("skill_" + k)) for k in keys)',
         ]),
         "fm month net")

s = once(s,
         NL.join([
             '    dirmap = {"做多": 1, "单边做多": 1, "做空": -1, "单边做空": -1}',
             '    ev["net"] = sum(dirmap.get(str(ev["votes"].get(k)), 0) for k, _ in _FUND_VOTE_BOOKS)',
         ]),
         NL.join([
             '    # 【2026-10-19】统一走 vote_semantics（期限结构的「偏多/偏空」此前恒计 0）',
             '    ev["net"] = sum(_vs.sign(ev["votes"].get(k)) for k, _ in _FUND_VOTE_BOOKS)',
         ]),
         "fm votes net")

save(p, s)
report.append(("fund_module.py", s.count("\r\n"), s.count("\n") - s.count("\r\n")))

# ══════════════ 3. quant_methodology.py（CRLF） ══════════════
p = "quant_methodology.py"
s, NL = load(p)

s = once(s,
         "from typing import Any, Sequence  # 【调用包】类型标注",
         "from typing import Any, Sequence  # 【调用包】类型标注" + NL
         + "import vote_semantics as _vs  # 【调用模块】方向票语义唯一权威来源",
         "qm import")

s = once(s,
         NL.join([
             "    dirmap = {'做多': 1, '单边做多': 1, '做空': -1, '单边做空': -1}",
         ]),
         "    # 【2026-10-19】方向映射统一走 vote_semantics（原 dirmap 漏「偏多/偏空」）",
         "qm dirmap")

s = once(s,
         NL.join([
             "    longs = [v for v in others if dirmap.get(v.get('bias'), 0) > 0]",
             "    shorts = [v for v in others if dirmap.get(v.get('bias'), 0) < 0]",
         ]),
         NL.join([
             "    longs = [v for v in others if _vs.sign(v.get('bias')) > 0]",
             "    shorts = [v for v in others if _vs.sign(v.get('bias')) < 0]",
         ]),
         "qm longs/shorts")

save(p, s)
report.append(("quant_methodology.py", s.count("\r\n"), s.count("\n") - s.count("\r\n")))

# ══════════════ 4. tests/test_fund_xsection.py（LF） ══════════════
p = "tests/test_fund_xsection.py"
s, NL = load(p)

s = once(s,
         NL.join([
             '    dirmap = {"做多": 1, "单边做多": 1, "做空": -1, "单边做空": -1}',
             '    expect = sum(dirmap.get(str(row.get("skill_" + k)), 0) for k in keys)',
         ]),
         NL.join([
             '    # 【2026-10-19】★ 不许再抄一份映射：此前这条测试复制了生产代码的 dirmap，',
             '    #   生产代码漏「偏多/偏空」时测试跟着一起错 ⇒ 恒绿，属于自指陷阱。',
             '    #   现改为引用 vote_semantics 同一份表（独立于本测试的期望值构造）。',
             '    from vote_semantics import sign as _sign',
             '    expect = sum(_sign(row.get("skill_" + k)) for k in keys)',
         ]),
         "test dirmap")

save(p, s)
report.append(("tests/test_fund_xsection.py", s.count("\r\n"), s.count("\n") - s.count("\r\n")))

print("=== 补丁完成（文件名, CRLF 数, 裸 LF 数）===")
for r in report:
    print("  %-32s CRLF=%-5d bareLF=%d" % r)
print("\n说明: 原文件为 CRLF 的，bareLF 必须仍为 0；原为 LF 的 CRLF 必须仍为 0。")
