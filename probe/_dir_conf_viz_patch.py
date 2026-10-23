# -*- coding: utf-8 -*-
"""研报方向/置信度可视化（2026-10-16 用户要求）。

用户原话：
  "研报总结部分：观点总表中方向/置信度部分可视性优化（颜色）如置信度部分使用类似进度条的方法
   提升可视化程度，方向可以分看多（红），与看空（绿）"
用户确认的两点：
  ① 范围 = **两处一起统一**（研报总结「当日观点总表」+「逐品种观点总览」）；
  ② 置信度进度条颜色 = **强度色（高=红）**。

改动：
  A. CSS：新增 .dir-chip / .conf-bar 等（看多=红、看空=绿、中性=琥珀）
  B. 「逐品种观点总览」：chipBg/chipColor 反色（原来 看多绿/看空红）；confCell 改强度色
  C. 「当日观点总表」：新增渲染后处理（方向 → 分色 chip；置信度 → 迷你进度条）
  D. 接线：日报/周报/季报总结渲染处调用后处理
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "web_template.html 应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ══════════════════════════════════════════════════════ A. CSS
CSS_OLD = ".rv-chip {"
CSS_NEW = '''/* 【方向/置信度可视化 2026-10-16 用户要求】
   方向配色约定(中国市场惯例): 看多=红、看空=绿、中性=琥珀;
   置信度进度条用**强度色**: 高=红 / 中=琥珀 / 低=绿 —— 长度表示数值高低,
   颜色只标强度档位(刻意与"看空=绿"的语义区分, tooltip 里写明它指的是"观点表述明确程度,不是行情胜率")。 */
.dir-chip { display:inline-block; padding:1px 7px; border-radius:999px; font-size:0.75rem; font-weight:600; white-space:nowrap; }
.dir-chip.up { background:var(--red-bg); color:var(--red); }
.dir-chip.down { background:var(--green-bg); color:var(--green); }
.dir-chip.flat { background:var(--amber-bg); color:var(--amber); }
.dir-chips { display:inline-flex; flex-wrap:wrap; gap:3px; }
.conf-bars { display:inline-flex; flex-direction:column; gap:2px; }
.conf-bar { display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
.conf-bar .track { width:38px; height:5px; border-radius:3px; background:var(--bg-input); display:inline-block; overflow:hidden; }
.conf-bar .fill { display:block; height:100%; border-radius:3px; }
.conf-bar .pct { font-size:0.75rem; font-variant-numeric:tabular-nums; }
.rv-chip {'''
sub(CSS_OLD, CSS_NEW, "A. 新增 .dir-chip / .conf-bar 等 CSS")

# ══════════════════════════════════════════════════════ B. 逐品种观点总览配色
sub(''' const chipBg = x => x === '看多' ? 'var(--green-bg)' : x === '看空' ? 'var(--red-bg)' : 'var(--amber-bg)';
 const chipColor = x => x === '看多' ? 'var(--green)' : x === '看空' ? 'var(--red)' : 'var(--amber)';''',
    '''// 【配色统一 2026-10-16 用户定】中国市场惯例: 看多=红、看空=绿、中性=琥珀。
// ⚠ 原来写反了(看多绿/看空红) ⇒ 与研报总结的「当日观点总表」不一致(同一份研报两处不同色),
//   用户报障后统一到本约定;改这里同时影响"发行方"列前的小圆点(同一语义,应当同色)。
const chipBg = x => x === '看多' ? 'var(--red-bg)' : x === '看空' ? 'var(--green-bg)' : 'var(--amber-bg)';
const chipColor = x => x === '看多' ? 'var(--red)' : x === '看空' ? 'var(--green)' : 'var(--amber)';''',
    "B1. chipBg/chipColor 反色 → 看多红/看空绿")

sub(" const col = c >= 0.7 ? 'var(--green)' : c >= 0.4 ? 'var(--amber)' : 'var(--red)';",
    '''// 【强度色 2026-10-16 用户定】高=红 / 中=琥珀 / 低=绿(与中国「红=强」的观感一致)。
// ⚠ 长度表示数值高低,颜色只标强度档位 —— tooltip 里写明它**不是**行情胜率,免得与方向色混淆。
const col = c >= 0.7 ? 'var(--red)' : c >= 0.4 ? 'var(--amber)' : 'var(--green)';''',
    "B2. confCell 改强度色（高=红）")

sub('<span style="color:var(--text-secondary);font-size:0.75rem;font-variant-numeric:tabular-nums">${pct}%</span>',
    '<span style="color:${col};font-size:0.75rem;font-variant-numeric:tabular-nums" '
    'title="研报观点识别置信度 ${pct}%（观点表述越明确越高，不是行情胜率）">${pct}%</span>',
    "B3. confCell 数字随强度着色 + 补 tooltip")

# ══════════════════════════════════════════════════════ C. 当日观点总表后处理
NEW_METHODS = '''// 【功能】把「当日/本周/本季观点总表」的方向列与置信度列转成可视化(2026-10-16 用户要求)。
// 【★ 为什么用"渲染后处理"而不改后端 markdown】该表是**后端代码生成的 markdown** 并落盘(.md);
//   改后端要**重跑 LLM 总结**才生效,而且会往原文 md 里塞 HTML(污染落盘件、影响后续导出/对比)。
//   后处理对**已生成**的总结立即生效、不动数据、随时可回退。
// 【定位方式】按**表头文本**找列,不写死列序号(列顺序变了也不会错位);
//   且要求**同时**存在「方向」与「置信度」两列 ⇒ 只认观点总表,不误伤二~四节的其它表格。
// 【幂等】整表打 `dataset.vizDone` 标记 —— 切换日期/类型会重渲染,重复调用不会叠加样式。
_decorateViewTable(root) {
if (!root) return;
Array.from(root.querySelectorAll('table')).forEach(tbl => {
if (tbl.dataset && tbl.dataset.vizDone === '1') return;
const ths = Array.from(tbl.querySelectorAll('thead th'));
const iDir = ths.findIndex(th => (th.textContent || '').trim() === '方向');
const iConf = ths.findIndex(th => (th.textContent || '').trim() === '置信度');
if (iDir < 0 || iConf < 0) return;
if (tbl.dataset) tbl.dataset.vizDone = '1';
Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const tds = tr.children;
if (tds[iDir]) this._vizDirCell(tds[iDir]);
if (tds[iConf]) this._vizConfCell(tds[iConf]);
});
});
},

// 【功能】方向单元格 → 分色 chip：看多=红 / 看空=绿 / 中性=琥珀(中国市场惯例)。
// 【输入】形如「看空 / 看空」「中性 / 看多」「看多」，无研报时是「—」。
// 【★ 为什么按 " / " 拆开】总表把**多家机构**合在一行,与「发行方」列用同一分隔符一一对应
//   ⇒ 拆开逐个上色,才能一眼看出"华泰看多、国泰君安看空"这种分歧(合成一个色反而掩盖分歧)。
_vizDirCell(td) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.innerHTML = '<span class="dir-chips">' + parts.map(p => {
const cls = p.includes('多') ? 'up' : (p.includes('空') ? 'down' : 'flat');
return '<span class="dir-chip ' + cls + '">' + this._escHtml(p) + '</span>';
}).join('') + '</span>';
},

// 【功能】置信度单元格 → 迷你进度条 + 百分比。长度=数值高低,颜色=强度档位(高红/中琥珀/低绿)。
// 【输入】形如「72% / 63%」(后端已把 0~1 小数转成百分数字符串)，研报未给时为「—」。
// 【★ 不拿 0 冒充缺失】「—」原样保留(灰色),**不画空进度条** ——
//   否则"研报没给置信度"会被读成"给了个 0 分",那是编造。
_vizConfCell(td) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.innerHTML = '<span class="conf-bars">' + parts.map(p => {
const m = String(p).match(/(\\d+(?:\\.\\d+)?)\\s*%/);
if (!m) return '<span style="color:var(--text-muted)">' + this._escHtml(p) + '</span>';
const v = Math.max(0, Math.min(100, Math.round(parseFloat(m[1]))));
const col = v >= 70 ? 'var(--red)' : (v >= 40 ? 'var(--amber)' : 'var(--green)');
return '<span class="conf-bar" title="研报观点识别置信度 ' + v + '%（观点表述越明确越高，不是行情胜率）">'
+ '<span class="track"><span class="fill" style="width:' + v + '%;background:' + col + '"></span></span>'
+ '<span class="pct" style="color:' + col + '">' + v + '%</span></span>';
}).join('') + '</span>';
},

// 【功能】把容器内"板块 h3 + 后续表格"包成 expander 折叠菜单(默认展开)。'''

sub(" // 【功能】把容器内\"板块 h3 + 后续表格\"包成 expander 折叠菜单(默认展开)。",
    NEW_METHODS.replace("\n// 【功能】把容器内", "\n // 【功能】把容器内"),
    "C. 新增 _decorateViewTable / _vizDirCell / _vizConfCell")

# ══════════════════════════════════════════════════════ D. 接线
sub(" this._groupSectorMenus(el);",
    ''' this._groupSectorMenus(el);
 // 【2026-10-16】总表方向/置信度可视化：必须在"包折叠菜单"之后做(那时表格已在最终 DOM 里);
 // ⚠ 顺序反了也能工作(菜单只包外层),但放后面语义更清楚、且不会被后续移动 DOM 影响节点引用。
 this._decorateViewTable(el);''',
    "D. 接线到日报/周报/季报总结渲染")

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)
b = P.read_bytes()
print("=" * 74)
print("  web_template.html 落盘完成")
print("=" * 74)
print("\n".join(report))
print()
print("  %d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
