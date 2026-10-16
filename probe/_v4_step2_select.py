# -*- coding: utf-8 -*-
"""v4 补充修复：
⑤ 控件组内的 select 被全局 `width:100%` 撑满 ⇒ 每个 select 占满整行，
   于是"日报/日期/生成/重新生成"竖排 3 行（用户截图）。
   修法：控件组内的 select 改回 auto 宽度。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

ADD = r'''
/* ── ⑤ 补：控件组内的 select 用内容宽度，不撑满 ─────────────────────
   根因：全局 `select { width: 100% }`（表单默认撑满，对纵向表单是对的），
   但放进「卡片标题行右侧的控件组」后，每个 select 各占满一行 ⇒ 竖排。
   这里只在控件组语境下改回 auto，不影响正文里的纵向表单。 */
.card > div:has(> h3) > div:nth-child(2) > select,
.card > div:has(> h3) > div:nth-child(2) > input {
  width: auto;
  min-width: 88px;
  flex: 0 0 auto;
}
/* 卡片标题行右侧的控件组：整体不收缩、内部优先横排 */
.card > div:has(> h3) > div:nth-child(2) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
if "⑤ 补：控件组内的 select" not in src:
    src = src[:i] + ADD + "\n" + src[i:]
    print("OK 已追加 select 宽度修正")
else:
    print("已存在，跳过")

F.write_text(src, encoding="utf-8")
print(f"{n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块:", len(re.findall(r'(?m)^{', src)))
print("position: sticky 出现次数:", len(re.findall(r'position:\s*sticky', src)))
for m in re.finditer(r'position:\s*sticky', src):
    line = src[:m.start()].count("\n") + 1
    seg = src[max(0, m.start()-400):m.start()]
    bs = re.findall(r'([^{};\n][^{}\n]*?)\s*\{', seg)
    print(f"   L{line}: {bs[-1].strip()[:60] if bs else '?'}")
