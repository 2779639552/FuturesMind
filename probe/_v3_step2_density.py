# -*- coding: utf-8 -*-
"""密度重构 v3 · 第二步：从"营销页留白"改为"资讯站密度"。

用户原话：
  ·「这种页面信息密度太低了。这种大号的数字去显示毫无意义」
  ·「不要滥用这种大号的圆角卡片去显示大号字体」
  ·「这个网站是自用的资讯网站，要在合理密度设计内尽量浓缩信息，
      在一个页面内尽可能多的去塞下信息」
  ·「这种设计真的是浪费空间」
  ·「你的这个按钮颜色太浅，和苹果一样搞成黑色的」
  ·「你这个表格都没优化，太丑了」

方向修正：上一版照搬了 apple.com.cn 的**大留白**，但那是营销页；
自用资讯站的密度本身就是价值。本步按"数据终端"的密度重做，
同时保留苹果的**克制美学**（纯色、细线、黑白灰、无特效）。

密度基准（对齐财经终端，非营销页）：
  · 卡片内边距  24/32px → 14/16px
  · 卡片间距    20px    → 12px
  · 卡片圆角    18px    → 10px
  · 卡片标题    21px    → 15px
  · KPI 数值    28px    → 17px（并改为紧凑指标带）
  · 表格行内距  12px    → 6/8px，字号 15 → 13px
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ══ ① 令牌：圆角收紧 + 新增黑色主按钮令牌 ══════════════════
tok = [
    # 圆角：大圆角是"浪费空间"观感的一部分，苹果控件实际多为 8~12px
    ("  --radius: 12px;\n  --radius-lg: 18px;\n  --radius-sm: 8px;",
     "  --radius: 8px;\n  --radius-lg: 10px;\n  --radius-sm: 6px;"),
    # 主按钮改黑（亮色主题：苹果黑 #1d1d1f + 白字）
    ("  --btn-primary-bg: #0071e3;\n  --btn-primary-fg: #ffffff;",
     "  --btn-primary-bg: #1d1d1f;\n  --btn-primary-fg: #ffffff;\n  --btn-primary-bg-h: #000000;"),
]
for o, n in tok:
    if o in src:
        src = src.replace(o, n, 1)
        print(f"① 令牌已改: {o.splitlines()[0].strip()[:38]} ...")
    else:
        print(f"① WARN 未命中: {o.splitlines()[0].strip()[:38]}")

# 亮色主题里的圆角与按钮（令牌在 [data-theme="light"] 里也各有一份时需同步）
print("  圆角令牌出现次数:", len(re.findall(r'--radius-lg:\s*\d+px', src)))

# 暗色主题下按钮改为「浅底深字」——黑底上黑按钮不可见，必须反过来
DARK_BTN = r'''
/* 暗色主题：主按钮用浅底深字（黑底上黑按钮不可见）。
   这是"黑色主按钮"在暗色模式下的正确等价物，而非简单套用 #1d1d1f。 */
[data-theme="dark"] {
  --btn-primary-bg: #f5f5f7;
  --btn-primary-fg: #1d1d1f;
  --btn-primary-bg-h: #ffffff;
}
'''
src = src.replace(":root {", DARK_BTN.strip() + "\n:root {", 1) if ':root {' in src else src

# ══ ② 密度重构主块（追加到设计层末，源序最后以覆盖同特异性规则）══
DENSITY = r'''
/* ══════════════════════════════════════════════════════════════════════
   密度重构层  (2026-10-14 v3)
   ----------------------------------------------------------------------
   方向修正：上一版照搬 apple.com.cn 的**大留白**，但那是营销页。
   本应用是**自用资讯站** —— 信息密度本身就是价值，"一屏塞下更多"优先于"呼吸感"。
   本层以财经数据终端为密度基准重做，同时保留苹果的克制美学：
   纯色、细线、黑白灰、零特效、零装饰色。
   ──────────────────────────────────────────────────────────────────────
   密度对照（改前 → 改后）：
     卡片内边距 24/32px → 14/16px   卡片间距 20px → 12px
     卡片圆角   18px    → 10px      卡片标题 21px → 15px
     KPI 数值   28px    → 17px      表格内距 12px → 6/8px
   ══════════════════════════════════════════════════════════════════════ */

/* ── ① 卡片：压缩内边距与间距 ───────────────────────────────── */
.card {
  padding: 14px 16px;
  border-radius: var(--radius-lg);
}
/* 卡片间距统一 12px（由"前有兄弟"规则供给，见上） */
.tab > * + *,
.page-tools > * + *,
.analysis-sub > * + *,
#f-research > * + * { margin-top: 12px !important; }
* + .card { margin-top: 12px; }
/* grid 内卡片间距由 gap 接管，同步收紧 */
.grid { gap: 12px; }
.stat-row { gap: 0; }

/* ── ② 卡片标题：21px → 15px（大标题在这种密度下纯属浪费垂直空间）── */
.card h3 {
  font-size: var(--fs-base);
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 10px;
  gap: 8px;
}
/* 区块内的一/二/三级标题（研报正文除外）同样收紧 */
h1 { font-size: var(--fs-lg); }
h2 { font-size: var(--fs-md); }
h3 { font-size: var(--fs-base); }
h4 { font-size: var(--fs-base); }

/* ── ③ KPI：从"5 张大圆角卡"改为"一条紧凑指标带" ──────────────
   为什么这么改：用户明确指出「滥用大号圆角卡片显示大号字体」毫无意义。
   改成整条一个底色 + 竖线分隔的多列布局，单格高度从 ~86px 降到 ~48px，
   一屏可多放近一倍指标。 */
.stat-row {
  display: flex;
  flex-wrap: wrap;
  background: var(--bg-tile);
  border-radius: var(--radius);
  overflow: hidden;
  padding: 0;
  margin-bottom: 0;
}
.stat-card {
  flex: 1 1 0;
  min-width: 110px;
  background: transparent;
  border: none;
  border-left: 1px solid var(--border-light);
  border-radius: 0;
  padding: 9px 14px;
  text-align: left;
}
.stat-card:first-child { border-left: none; }
.stat-card .stat-val {
  font-size: var(--fs-md);        /* 17px，原 28px */
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.02em;
  color: var(--text);
}
.stat-card .stat-lbl {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-top: 1px;
  line-height: 1.35;
}
/* 暗色主题下指标带用深色底 */
[data-theme="dark"] .stat-row { background: var(--bg-tile); }

/* ── ④ 表格：全宽 + 紧凑行高（用户指为"最严重的问题"）───────────
   原实现：table 未设 width ⇒ 浏览器按内容宽度渲染（实测 307px vs 卡片 1328px），
   左侧整片空白。同时行内距 12px + 15px 字号在高密度场景过于松散。 */
table { width: 100%; }
table th {
  font-size: var(--fs-xs);
  font-weight: 500;
  color: var(--text-muted);
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  text-align: left;
}
table td {
  font-size: var(--fs-sm);        /* 13px，原 15px */
  padding: 6px 10px;
  border-bottom: 1px solid var(--border-light);
  line-height: 1.45;
}
/* 表格首列贴左、末列贴右（数字列右对齐是数据表的基本要求） */
table th:first-child, table td:first-child { padding-left: 0; }
table th:last-child, table td:last-child { padding-right: 0; }
/* 行悬停：细底色而非整行高亮 */
table tbody tr:hover { background: var(--bg-hover); }
/* 数字列统一右对齐 + 等宽数字 */
table td.num, table th.num { text-align: right; font-variant-numeric: tabular-nums; }
/* 明细表容器：限高内滚，避免长表把一屏撑到几屏 */
#wt-table { max-height: 520px; overflow: auto; }

/* 研报正文表格同步收紧 */
.report-content th { padding: 6px 10px; font-size: var(--fs-xs); }
.report-content td { padding: 6px 10px; font-size: var(--fs-sm); }
.delta-tbl th, .delta-tbl td { padding: 5px 8px; font-size: var(--fs-xs); }

/* ── ⑤ 表单控件：高 40px → 34px（一屏多塞一行控件）────────────── */
input, select, textarea { min-height: 34px; font-size: var(--fs-sm); padding: 0 10px; }
label { font-size: var(--fs-xs); margin-bottom: 4px; }
/* 表单行内标签与控件更紧 */
.card > div[style*="display:flex"] { gap: 12px !important; }

/* ── ⑥ 按钮：高 40 → 34px，主按钮黑色 ───────────────────────── */
.btn {
  min-height: 34px;
  padding: 0 16px;
  font-size: var(--fs-sm);
  border-radius: var(--radius);
}
.btn-sm { min-height: 28px; padding: 0 12px; font-size: var(--fs-xs); }
/* 主按钮：亮色=苹果黑，暗色=浅底深字（见令牌覆盖） */
.btn-primary { background: var(--btn-primary-bg); color: var(--btn-primary-fg); }
.btn-primary:hover { background: var(--btn-primary-bg-h); }
.btn-outline { border-color: var(--border); color: var(--text); }
.btn-outline:hover { background: var(--bg-hover); color: var(--text); border-color: var(--text-muted); }
/* 方形图标按钮随之调整 */
#res-qa-del { width: 34px; min-width: 34px; padding: 0; }

/* ── ⑦ 顶部导航：苹果官网样式 ─────────────────────────────────
   苹果顶栏的特征：纯文字、无药丸底、无下划线、激活态**只加深字色**。
   上一版用了淡蓝药丸底 + 字重变化，不是苹果的做法。 */
nav { gap: 0; }
nav button {
  background: transparent;
  border: none;
  border-radius: 0;
  color: var(--text-secondary);
  padding: 0 11px;
  min-height: 48px;              /* 撑满顶栏 —— 苹果的点击区是整条高度 */
  display: inline-flex;
  align-items: center;
  font-size: var(--fs-xs);       /* 12px 精字 */
  font-weight: 400;
  letter-spacing: 0;
  transition: color var(--transition);
}
nav button:hover { color: var(--text); background: transparent; }
nav button.active {
  color: var(--text);            /* 苹果：激活只加深字色 */
  font-weight: 500;
  background: transparent;
  border: none;
  box-shadow: none;
  text-shadow: none;
}

/* ── ⑧ 折叠条与徽标：同步收紧 ─────────────────────────────── */
.expander-header { padding: 11px 0; font-size: var(--fs-sm); }
.expander-body { padding: 0 0 12px; font-size: var(--fs-sm); line-height: 1.55; }
.badge, .badge-info, .badge-bull, .badge-bear, .badge-neutral {
  font-size: 11px;
  padding: 1px 7px;
}
.pipeline-stage { padding: 4px 10px; font-size: 11px; }
.sec-index { padding: 0 0 10px; margin-bottom: 0; }

/* ── ⑨ 移动端：密度同步收紧（但仍保证 44px 可点区）─────────── */
@media (max-width: 768px) {
  .card { padding: 12px 12px; }
  .stat-card { flex: 1 1 50%; padding: 8px 10px; }
  .stat-card:nth-child(odd) { border-left: none; }
  .stat-card .stat-val { font-size: var(--fs-base); }
  table th, table td { padding: 6px 8px; }
  nav button { min-height: 46px; padding: 0 8px; }
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + DENSITY + "\n" + src[i:]
print("② 密度重构层已追加")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
for p in ["--btn-primary-bg: #1d1d1f", "--radius-lg: 10px", "[data-theme=\"dark\"] {\n  --btn-primary-bg: #f5f5f7"]:
    print(f"  校验 {p[:34]!r}: {'OK' if p in src else 'MISSING'}")
