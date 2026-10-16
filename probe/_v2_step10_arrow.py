# -*- coding: utf-8 -*-
"""重做视觉层 · 第十步：收尾 —— 替换三角形符号 + 修掉遗留英文文案。

处理三件事：
  ① `.arrow` 的 ▶（16 处，含 JS 模板注入的）→ 纯 CSS 画的 chevron
     为什么用 CSS 而不是逐个换 SVG：箭头同时由**静态 HTML** 和 **JS 模板字符串**
     两处产出，逐个替换会漏；而 `font-size:0` + `::before` 画 chevron 能一次性
     覆盖两种来源，且展开态的旋转只靠一个 transform。
  ② 按钮里的 ▶/⏸ → 1.4px 描边 SVG（与其它按钮图标风格统一）
  ③ 顺手修掉 JS 里遗留的英文文案 `'▶ Start'`（应为「开始分析」）—— 这是旧代码
     残留，用户界面出现英文与整站中文不一致。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

def svg(paths, size=14):
    return (f'<svg viewBox="0 0 16 16" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.4" aria-hidden="true" '
            f'style="vertical-align:-2px">{paths}</svg>')

PLAY = svg('<path d="M4.8 3.2 12.4 8l-7.6 4.8V3.2Z" fill="currentColor" stroke="none"/>')
PAUSE = svg('<rect x="4.2" y="3.2" width="2.7" height="9.6" rx="1" fill="currentColor" '
            'stroke="none"/><rect x="9.1" y="3.2" width="2.7" height="9.6" rx="1" '
            'fill="currentColor" stroke="none"/>')

# ── ① 开始分析按钮 ──────────────────────────────────────
c1 = 0
p = re.compile(r'(<button[^>]*id="btn-run"[^>]*>)\s*[^<]*?开始分析\s*(</button>)')
src, n = p.subn(lambda m: m.group(1) + PLAY + "开始分析" + m.group(2), src)
c1 += n
print(f"① 开始分析按钮 -> SVG: {c1} 处")

# ── ② JS 里的按钮文案（同时修掉英文残留）──────────────────
c2 = 0
for old, new in [
    ("btnRun.textContent = '▶ Start';", f"btnRun.innerHTML = '{PLAY}开始分析';"),
    ("btnRun.textContent = '开始分析';", f"btnRun.innerHTML = '{PLAY}开始分析';"),
]:
    if old in src:
        cnt = src.count(old)
        src = src.replace(old, new)
        c2 += cnt
        print(f"   {old[:44]!r} x{cnt}")

# 「继续生成 / 暂停生成」切换按钮
p = re.compile(r"btn\.textContent = this\._vsPaused \? '[^']*' : '[^']*';")
def _vs(m):
    global c2
    c2 += 1
    return f"btn.innerHTML = this._vsPaused ? '{PLAY}继续生成' : '{PAUSE}暂停生成';"
src, n = p.subn(_vs, src)
c2 += n
print(f"② JS 按钮文案 -> SVG 并修正英文: {c2} 处")

# ── ③ 剩余的 ▶ / ⏸ / ⏹（非 .arrow 的）一并清掉 ──────────
c3 = 0
# 先把 .arrow 里的保留（交给 CSS chevron 处理，字符会被 font-size:0 隐藏）
for ch in ("\u25B6", "\u23F8", "\u23F9", "\u23F5", "\u25B8"):
    c = src.count(ch)
    if c:
        c3 += c
        # 保留 .arrow 内的；其余删除
        src = re.sub(r'(?<!class="arrow">)' + re.escape(ch), '', src)
        src = src.replace(f'>{ch}<', '><')
print(f"③ 清理残余三角形/播放符: {c3} 处（.arrow 内的保留由 CSS 隐藏）")

# ── ④ CSS chevron（一次性覆盖静态 + JS 注入两种来源）──────
CHEV = r'''
/* ── 折叠箭头：纯 CSS chevron（苹果式 disclosure indicator）──────────
   为什么不用 SVG 逐个替换：箭头由**静态 HTML** 与 **JS 模板字符串**两处产出
   （共 16 处），逐个替换必漏；`font-size:0` + `::before` 能一次覆盖两种来源。
   几何：border-right + border-bottom 形成一个「L」，旋转 -45° 后指向右；
   展开态旋转 135° 指向下。 */
.expander-header .arrow {
  font-size: 0;              /* 隐藏原 ▶ 字符，但保留其占位与旋转钩子 */
  position: relative;
  width: 10px;
  height: 10px;
  flex: none;
  margin-left: var(--sp-2);
  transition: transform 0.25s cubic-bezier(.22,1,.36,1);
}
.expander-header .arrow::before {
  content: '';
  position: absolute;
  left: 1px;
  top: 1px;
  width: 8px;
  height: 8px;
  border-right: 1.5px solid var(--text-muted);
  border-bottom: 1.5px solid var(--text-muted);
  transform: rotate(-45deg);
  transition: border-color 0.2s;
}
.expander.open .expander-header .arrow { transform: rotate(90deg); }
.expander-header:hover .arrow::before { border-color: var(--brand); }

/* 折叠条三段式：标题左 / 提示与箭头贴右（删 emoji 后 space-between 会把提示甩到正中） */
.expander-header { justify-content: flex-start; }
.expander-header > span:not(.arrow) { margin-left: auto; padding: 0 var(--sp-3) 0 0; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + CHEV + "\n" + src[i:]
print("④ 已写入 CSS chevron")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("残留 ▶:", src.count("\u25B6"), " (应为 16 左右，全在 .arrow 内)")

# 复查：不是 .arrow 内容的 ▶
bad = [m.start() for m in re.finditer('\u25B6', src)
       if 'class="arrow"' not in src[max(0, m.start()-40):m.start()+10]]
print("非 .arrow 的 ▶:", len(bad))
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
