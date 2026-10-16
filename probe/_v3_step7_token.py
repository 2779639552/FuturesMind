# -*- coding: utf-8 -*-
"""密度重构 v3 · 第七步：把 11px 显式化为「标签类例外」令牌。

情况说明：实测 4 个页签出现 11px，全部是**标签/徽标**（stat-lbl 指标标签、
表格内的类型徽标），**无一是正文**。
这是密度与可读性的折中：财经终端（Wind / 同花顺）的标注普遍用 11px，
而用户本次的核心诉求正是"在合理密度内尽量浓缩信息"。

与其让 11px 散落成硬编码（下次改起来找不到），不如显式定义成受控例外：
  --fs-tag: 11px  —— **仅限标签/徽标**，禁止用于正文、表格正文、按钮、输入
正文类硬底线仍是 12px（--fs-xs）。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ── ① 新增 --fs-tag 令牌（暗/亮两套都需要，值相同）──────────
old_tok = """ --fs-xs: 0.75rem;"""
new_tok = """ /* 标签类专用字号（11px）—— 受控例外：
    仅用于指标标签(stat-lbl)/徽标(badge)这类**非正文**信息。
    为什么单开一个令牌而不是硬编码 11px：避免下次改密度时满文件找不到；
    也为"正文硬底线 12px"（--fs-xs）与"标签可到 11px"划出明确边界。 */
 --fs-tag: 11px;
 --fs-xs: 0.75rem;"""
if old_tok in src:
    src = src.replace(old_tok, new_tok, 1)
    print("① --fs-tag 令牌已定义")
else:
    print("① WARN 未找到 --fs-xs 定义")

# ── ② 把散落的 11px 换成令牌 ──────────────────────────────
# 只替换"标签/徽标"相关的几条，不动其它位置
TAG_RULES = [
    (".stat-card .stat-lbl { font-size: 11px; margin-top: 0; }",
     ".stat-card .stat-lbl { font-size: var(--fs-tag); margin-top: 0; }"),
    ("#wt-table .badge { font-size: 11px; padding: 1px 6px; }",
     "#wt-table .badge { font-size: var(--fs-tag); padding: 1px 6px; }"),
]
for o, n in TAG_RULES:
    if o in src:
        src = src.replace(o, n, 1)
        print(f"② 令牌化: {o[:48]}...")
    else:
        print(f"② WARN 未命中: {o[:48]}...")

F.write_text(src, encoding="utf-8")

# ── 复核：还有哪些 11px 未令牌化 ─────────────────────────
rest = [(src[:m.start()].count("\n") + 1, src[max(0, m.start()-90):m.start()+50].replace("\n", " "))
        for m in re.finditer(r'font-size:\s*11px', src)]
print(f"\n③ 残留硬编码 11px: {len(rest)} 处")
for line, ctx in rest:
    print(f"   L{line}: ...{ctx.strip()[-80:]}")

print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
