# -*- coding: utf-8 -*-
"""Apple 化 · 第三步：追加「Apple 设计层」覆盖块 + 移动端补偿。
插在主 CSS 块 </style> 之前（源序最后 => 可覆盖前面同特异性规则）。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

LAYER = r'''
/* ══════════════════════════════════════════════════════════════════════
   APPLE 设计层  (2026-10-14)
   ----------------------------------------------------------------------
   做法：不删旧规则，在源序最后追加一层覆盖 —— 同特异性下后者胜，因此这一层
   可以安全地重定义任何前面写过的视觉规则，且回退只需删掉本段。
   覆盖范围：并列卡片成块、表格去渐变表头、滚动条、标题字距、移动端补偿。
   ══════════════════════════════════════════════════════════════════════ */

/* ── 并列卡片：用底色成块（苹果 product tile），区别于单列堆叠的细线分区 ──
   为什么必须区分：单列堆叠的卡片靠“顶细线 + 大留白”分层（编辑式）；
   而 grid 里的卡片是并列的，没有细线就会糊成一片，故改用底色块。 */
.grid > .card ,
.page-tools > .card {
  background: var(--bg-tile);
  border: none;
  border-radius: var(--radius-lg);
  padding: 22px 24px;
}
.grid > .card:hover { background: var(--bg-tile); border: none; }

/* ── 标题字距（苹果的紧凑字距是“精致感”的主要来源） ── */
h1, h2, h3, h4 { letter-spacing: -0.022em; }

/* ── 表格：苹果式表头 —— 去掉糖果紫渐变底，只留 1px 细线 ── */
table { border-collapse: collapse; }
table th {
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--text-muted);
  letter-spacing: 0;
  text-align: left;
}
table td { font-size: 0.9375rem; }
.report-content th {
  background: transparent;
  color: var(--text-muted);
  font-weight: 400;
  border-bottom: 1px solid var(--border);
  padding: 10px 12px;
}
.report-content td { border-bottom: 1px solid var(--border-light); padding: 12px; }
.delta-tbl thead th { background: transparent; color: var(--text-muted); font-weight: 400; }
.delta-tbl th, .delta-tbl td { border-bottom: 1px solid var(--border-light); }
.delta-tbl tbody tr:hover { background: var(--bg-hover); }

/* ── 代码 / 引用 ── */
.report-content code,
.report-content pre { background: var(--bg-tile); border-radius: var(--radius-sm); }
.report-content blockquote { border-left: 3px solid var(--brand); background: var(--bg-tile); }

/* ── 滚动条：苹果式细滚动条（默认粗灰条是廉价感来源之一） ── */
* { scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
*::-webkit-scrollbar { width: 11px; height: 11px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 980px;
  border: 3px solid transparent;
  background-clip: content-box;
}
*::-webkit-scrollbar-thumb:hover { background: var(--text-muted); background-clip: content-box; }

/* ── 选中文本 ── */
::selection { background: var(--selection); color: var(--text); }

/* ── 折叠块：细线分区不参与折叠裁剪 ── */
.sec-block { overflow: hidden; }

/* ── 小屏补偿：.stat-row 改 grid 后原 flex 断点失效，这里重给 ── */
@media (max-width: 768px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px 0; }
  .stat-card { padding: 2px 16px; }
  .stat-card:nth-child(odd) { border-left: none; padding-left: 0; }
  .stat-card .stat-val { font-size: 1.75rem; }
  main { padding: 18px 14px calc(88px + env(safe-area-inset-bottom)); }
  .card { padding: 22px 0 26px; }
  .grid > .card, .page-tools > .card { padding: 18px 16px; border-radius: var(--radius); }
  .elder-nav-fix, nav#nav {
    background: var(--bg-glass);
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    border-top: 1px solid var(--border-light);
    box-shadow: none;
  }
  nav button.active {
    color: var(--brand);
    background: var(--brand-dim);
    border: none;
    border-radius: 980px;
    text-shadow: none;
  }
  .expander-header { padding: 16px 0; font-size: 1rem; }
  .expander-body { padding: 0 0 20px; }
}
@media (max-width: 420px) {
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-card .stat-val { font-size: 1.5rem; }
  .card { padding: 20px 0 24px; }
}

/* ── ≤900px 横屏/矮屏 ── */
@media (max-width: 900px) {
  main { padding: 22px 18px 72px; }
}
'''

anchor = "</style>"
i = src.find(anchor)
if i < 0:
    raise SystemExit("FAIL: 未找到 </style>")
src = src[:i] + LAYER + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print("OK Apple 设计层已追加")
print("行数:", src.count("\n")+1, "字节:", len(src.encode('utf-8')))
for p in [".grid > .card", "APPLE 设计层", "*::-webkit-scrollbar-thumb"]:
    print(f"  校验 {p!r}: {'OK' if p in src else 'MISSING'}")
