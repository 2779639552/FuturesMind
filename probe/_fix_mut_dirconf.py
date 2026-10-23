# -*- coding: utf-8 -*-
"""修 3 条被跳过的变异锚点（并补一条更贴切的"假 0"变异）。

跳过原因：
  · M7  锚点「不是行情胜率」在总表/总览各出现一次（命中 2）⇒ 拆成两条、各带唯一上下文；
  · M11 锚点 `if (!raw || raw === '—') return;` 在两个方法里各一份（命中 2）
        ⇒ 带上后一行（`conf-bars`）限定到 _vizConfCell；
  · M12 我按 2 空格缩进写，而补丁插入的方法体是**顶格**的 ⇒ 命中 0。
另补 M11b：把"解析不出百分比"的兜底改成**假 0% 空条** —— 这正是护栏要拦的编造数据。
"""
from pathlib import Path

P = Path("probe/_mutation_dir_conf_viz.py")
s = P.read_text(encoding="utf-8")

OLD = '''    ("M7 丢掉「不是行情胜率」的语义说明",
     "（观点表述越明确越高，不是行情胜率）",
     "（观点表述越明确越高）"),'''
NEW = '''    # ⚠ M7 的锚点「不是行情胜率」在总表/总览**各有一份** ⇒ 必须带唯一上下文,
    #   否则命中 2 被跳过（第一版就是这么被跳过的）。
    ("M7a 总表: 丢掉「不是行情胜率」的语义说明",
     "+ v + '%（观点表述越明确越高，不是行情胜率）'",
     "+ v + '%（观点表述越明确越高）'"),

    ("M7b 总览: 丢掉「不是行情胜率」的语义说明",
     "研报观点识别置信度 ${pct}%（观点表述越明确越高，不是行情胜率）",
     "研报观点识别置信度 ${pct}%（观点表述越明确越高）"),'''
assert s.count(OLD) == 1, ("M7 锚点", s.count(OLD))
s = s.replace(OLD, NEW, 1)

OLD2 = '''    ("M11 缺失置信度被当成 0 画空条（编造数据）",
     "if (!raw || raw === '—') return;",
     "if (!raw) return;"),'''
NEW2 = '''    # ⚠ 该串在两个方法各有一份 ⇒ 带后一行限定到 _vizConfCell
    ("M11 置信度列: 去掉「—」的早退",
     "if (!raw || raw === '—') return;\\nconst parts = raw.split(/\\\\s*\\\\/\\\\s*/).filter(Boolean);\\nif (!parts.length) return;\\ntd.innerHTML = '<span class=\\"conf-bars\\">'",
     "if (!raw) return;\\nconst parts = raw.split(/\\\\s*\\\\/\\\\s*/).filter(Boolean);\\nif (!parts.length) return;\\ntd.innerHTML = '<span class=\\"conf-bars\\">'"),

    ("M11b ★ 解析不出百分比时**假造成 0% 空条**（把「没给」读成「给了 0 分」）",
     "if (!m) return '<span style=\\"color:var(--text-muted)\\">' + this._escHtml(p) + '</span>';",
     "if (!m) m = ['', '0'];"),'''
assert s.count(OLD2) == 1, ("M11 锚点", s.count(OLD2))
s = s.replace(OLD2, NEW2, 1)

OLD3 = '''    ("M12 不拆多家机构（分歧被抹成一个颜色）",
     "const parts = raw.split(/\\\\s*\\\\/\\\\s*/).filter(Boolean);\\n  if (!parts.length) return;\\n  td.innerHTML = '<span class=\\"dir-chips\\">'",
     "const parts = [raw];\\n  if (!parts.length) return;\\n  td.innerHTML = '<span class=\\"dir-chips\\">'"),'''
NEW3 = '''    # ⚠ 补丁插入的方法是**顶格**的 ⇒ 锚点不能带 2 空格缩进（第一版命中 0）
    ("M12 方向列不拆多家机构（分歧被抹成一个颜色）",
     "const parts = raw.split(/\\\\s*\\\\/\\\\s*/).filter(Boolean);\\nif (!parts.length) return;\\ntd.innerHTML = '<span class=\\"dir-chips\\">'",
     "const parts = [raw];\\nif (!parts.length) return;\\ntd.innerHTML = '<span class=\\"dir-chips\\">'"),'''
assert s.count(OLD3) == 1, ("M12 锚点", s.count(OLD3))
s = s.replace(OLD3, NEW3, 1)

P.write_text(s, encoding="utf-8", newline="")
import ast
ast.parse(s)
print("  ✔ 变异脚本已修（M7 拆两条 / M11 限定范围 / M11b 新增 / M12 去缩进）")
print("  条目数:", s.count('    ("M'))
