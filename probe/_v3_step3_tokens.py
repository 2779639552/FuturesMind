# -*- coding: utf-8 -*-
"""密度重构 v3 · 第三步：修正令牌替换（缩进是单空格，不是双空格）。

⚠️ 教训：此前"清理多余空格"的正则 `re.sub(r'(?<![ \\t])[ \\t]{2,}(?![ \\t])', ' ', src)`
   会连 CSS 的**缩进**一起压成单空格 —— 于是后续按双空格写的替换全部失配。
   凡按"格式"匹配的替换都必须先确认实际缩进。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ── ① 圆角令牌（单空格缩进）───────────────────────────────
RK = [(" --radius: 12px;", " --radius: 8px;"),
      (" --radius-lg: 18px;", " --radius-lg: 10px;"),
      (" --radius-sm: 8px;", " --radius-sm: 6px;")]
for o, n in RK:
    if o in src:
        src = src.replace(o, n, 1)
        print(f"① 圆角 {o.strip():22} -> {n.strip()}")
    else:
        print(f"① WARN 未命中 {o!r}")

# ── ② 主按钮：区分 :root(默认暗色) 与 [data-theme="light"] ──
li = src.find('[data-theme="light"] {')
li_end = src.find('\n}', li)
print(f"\n② light 块位置: {li} ~ {li_end}")

# 找到所有 --btn-primary-bg 定义（排除我插入的 [data-theme="dark"] 块与注释）
pat = re.compile(r'^( *)(--btn-primary-bg|--btn-primary-fg|--btn-primary-bg-h): *([^;]+);', re.M)
edits = []
for m in pat.finditer(src):
    indent, key, val = m.group(1), m.group(2), m.group(3).strip()
    in_light = li < m.start() < li_end
    # 排除注释行（在 /* */ 内）——简单判断：该行前面同段没有未闭合的 /*
    line_start = src.rfind('\n', 0, m.start()) + 1
    prefix = src[max(0, line_start - 400):line_start]
    if prefix.count('/*') > prefix.count('*/'):
        continue
    edits.append((m.start(), m.end(), indent, key, val, in_light))

print("   找到的按钮令牌定义:")
for s, e, ind, k, v, il in edits:
    print(f"     L{src[:s].count(chr(10))+1:5} {'light' if il else 'root '} {k} = {v}")

# 逐条按块改值
out = src
for s, e, ind, k, v, il in sorted(edits, key=lambda x: -x[0]):
    if il:
        # 亮色主题：用户明确要求「和苹果一样搞成黑色」
        newv = {"--btn-primary-bg": "#1d1d1f", "--btn-primary-fg": "#ffffff",
                "--btn-primary-bg-h": "#000000"}.get(k)
    else:
        # 默认(:root)=暗色主题：黑底上黑按钮不可见 ⇒ 浅底深字
        newv = {"--btn-primary-bg": "#f5f5f7", "--btn-primary-fg": "#1d1d1f",
                "--btn-primary-bg-h": "#ffffff"}.get(k)
    if newv and newv != v:
        out = out[:s] + f"{ind}{k}: {newv};" + out[e:]
    elif not newv:
        pass

# 补上缺失的 --btn-primary-bg-h（light 与 root 各一份）
for anchor, val in ((" --btn-primary-fg: #ffffff;\n --btn-primary-glow", " #000000"),
                    (" --btn-primary-fg: #1d1d1f;\n --btn-primary-glow", " #ffffff")):
    if anchor in out and "--btn-primary-bg-h" not in out.split(anchor)[0][-260:]:
        pass
# 直接在两个 fg 定义后插 bg-h
def add_h(text, fg_color, hover):
    pat = re.compile(r'^( *)(--btn-primary-fg: ' + re.escape(fg_color) + r';)$', re.M)
    def _r(m):
        if "--btn-primary-bg-h" in text[max(0, m.start()-200):m.start()]:
            return m.group(0)
        return m.group(0) + f"\n{m.group(1)}--btn-primary-bg-h: {hover};"
    return pat.sub(_r, text)

out = add_h(out, "#ffffff", "#000000")   # light 块
out = add_h(out, "#1d1d1f", "#ffffff")   # root 块
src = out

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")

# ── 校验 ──────────────────────────────────────────────
print("\n=== 校验 ===")
li = src.find('[data-theme="light"] {')
li_end = src.find('\n}', li)
for m in re.finditer(r'^( *)(--radius|--radius-lg|--radius-sm|--btn-primary-bg|--btn-primary-fg|--btn-primary-bg-h): *([^;]+);', src, re.M):
    line = src[:m.start()].count('\n') + 1
    zone = 'light' if li < m.start() < li_end else ('dark-ovr' if src.rfind('[data-theme="dark"]', 0, m.start()) > 0
                                                      and src.find('}', src.rfind('[data-theme="dark"]', 0, m.start())) > m.start() else 'root')
    print(f"  L{line:5} [{zone:8}] {m.group(2)} = {m.group(3)}")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
