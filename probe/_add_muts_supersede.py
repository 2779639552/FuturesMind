# -*- coding: utf-8 -*-
"""给变异自检追加两条（陈旧告警推翻判定）：去掉时间对齐 / 断开接线。"""
from pathlib import Path

P = Path("probe/_mutation_wechat_status.py")
s = P.read_text(encoding="utf-8")

# ① 追加两个测试常量
OLD_CONST = 'VER = "tests/test_fund_xsection.py::test_frontend_sec_index_version_bumped"\n'
NEW_CONST = OLD_CONST + (
    'SST = T + "::test_stale_alert_superseded_time_alignment"\n'
    'SSC = T + "::test_both_channels_apply_supersede_check"\n')
assert s.count(OLD_CONST) == 1, ("常量锚点", s.count(OLD_CONST))
s = s.replace(OLD_CONST, NEW_CONST, 1)

# ② 在 MUTS 的 `]` 前追加两条变异
OLD_END = "\n]\n"
NEW_END = '''
    # ── 2026-10-16 追加：陈旧失败告警的推翻判定 ──────────────────────
    ("M11 去掉 UTC→本地 +8 对齐（判定恒假 ⇒ 修复静默失效）", APP,
     '        a = _d.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + _d.timedelta(hours=8)\\n',
     '        a = _d.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")\\n', SST),

    ("M12 断开读书通道的推翻判定（历史告警永久标红）", APP,
     '    if wr_bad and _wechat_alert_superseded(wr_fail, mp_rep.get("ts")):\\n',
     '    if False:\\n', SSC),

    ("M13 合集通道不接推翻判定（只修一半）", APP,
     '    if alb_bad and _wechat_alert_superseded(\\n',
     '    if False and _wechat_alert_superseded(\\n', SSC),
]
'''
i = s.rindex(OLD_END)
assert i > 0, "找不到 MUTS 结尾"
s = s[:i] + NEW_END + s[i + len(OLD_END):]
P.write_text(s, encoding="utf-8", newline="")
print("  ✔ 已追加 M11/M12/M13")
import ast
ast.parse(s)
print("  ✔ 语法 OK")
print("  SST/SSC 已定义:", "SST = T +" in s and "SSC = T +" in s)
