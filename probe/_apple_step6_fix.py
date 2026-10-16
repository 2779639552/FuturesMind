# -*- coding: utf-8 -*-
"""Apple 化 · 第六步：emoji 清理的副作用补救 + 版式细节。
1) 原 🗑 图标按钮（2 处）现为空壳 -> 补 1.4px 描边线性 SVG
2) 旧紫系装饰色（JS/ECharts 用）-> 品牌蓝
   ⚠️ 刻意**不动** #ef4444/#22c55e/#fbbf24 —— 它们是「红涨绿跌」涨跌语义色与非涨跌警示色，
      属业务语义，改了会让图表与文字语义错位。只换纯装饰的旧紫系。
3) 页级控制条内单张卡占满宽度（原来只用左半屏，右侧大片空白）
4) 折叠条标题/提示/箭头 三段式布局修正（删 emoji 后 space-between 把提示甩到正中）
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

TRASH = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
         'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
         '<path d="M2.8 4.6h10.4M6.4 4.6V3.3a.9.9 0 0 1 .9-.9h1.4a.9.9 0 0 1 .9.9v1.3'
         'M4.3 4.6l.7 8.2a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.2" '
         'stroke-linecap="round" stroke-linejoin="round"/></svg>')

# ── 1) 补图标按钮 ────────────────────────────────────────
fixed_btn = 0
for pat in (
    r'(<button\b[^>]*id="res-qa-del"[^>]*>)\s*(</button>)',
    r'(<button\b[^>]*onclick="App\.deleteQaTerm\(\$\{t\.id\}\)"[^>]*>)\s*(</button>)',
):
    src, n = re.subn(pat, lambda m: m.group(1) + TRASH + m.group(2), src)
    fixed_btn += n
    if n == 0:
        print("WARN: 未命中", pat[:60])
print(f"图标按钮补全: {fixed_btn} 处")

# ── 2) 旧紫系装饰色 -> 品牌蓝（仅装饰位）──────────────────
DECOR = {
    "#c7c7ff": "#2997ff", "#b9b9ff": "#2997ff", "#e4dcff": "#7cc4ff",
    "#818cf8": "#2997ff", "#a78bfa": "#bf5af2", "#6366f1": "#2997ff",
}
dcount = 0
for old, new in DECOR.items():
    # 大小写不敏感替换
    pat = re.compile(re.escape(old), re.I)
    cnt = len(pat.findall(src))
    if cnt:
        src = pat.sub(new, src)
        dcount += cnt
        print(f"  装饰色 {old} -> {new} : {cnt} 处")
print(f"旧紫系装饰色收敛: {dcount} 处（涨跌语义色刻意保留）")

# ── 3+4) 版式细节，追加覆盖 CSS ────────────────────────────
FIX2 = r'''
/* ── 版式细节修正（Apple 层 · 第二批） ─────────────────────────── */

/* 页级控制条内若只有一张卡，让它占满整行 —— 否则右侧留出半屏空白（实测「分析配置」） */
.page-tools .grid > .card:only-child { grid-column: 1 / -1; }

/* 折叠条三段式：标题左、提示与箭头靠右贴合。
   原 justify-content:space-between 在删掉 emoji 后会把中间提示甩到正中，很怪。 */
.expander-header { justify-content: flex-start; }
.expander-header .arrow { margin-left: 0; }
.expander-header > span:not(.arrow) { margin-left: auto; padding: 0 10px 0 0; }

/* 图标按钮：正方形、无内边距，避免 SVG 被药丸内边距挤偏 */
.btn-icon-only {
  padding: 8px 10px;
  min-width: 36px;
  justify-content: center;
}
'''
# 插在 Apple 层内部（≤900px 断点之前）
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX2 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"\nOK 第六步完成：{n0} -> {len(src.encode('utf-8'))} 字节")
print("残留空 button:", len(re.findall(r'<button\b[^>]*>\s*</button>', src)))
