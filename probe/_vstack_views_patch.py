# -*- coding: utf-8 -*-
"""总表「发行方/方向/置信度」三列纵向逐机构对齐（2026-10-19 用户要求）。

【用户要求】① 发行方列也用纵向排列，与自己的方向/置信度一一对应；
            ② 一句话核心观点口径更丰富、字数适当提升（纵向排列腾出空间）。
【实现】三个独立单元格无法共享 grid 行 ⇒ 靠「每项固定 20px + 同 gap 5px + td 顶部对齐」
        让第 k 项严格同高；家数以**发行方**为准，方向/置信度不足时补不可见占位（防错行）。
【EOL】web_template.html 是 CRLF，必须 newline="" 读写并自检 bareLF==0。
"""
import sys
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
CRLF = raw.count(b"\r\n")
BARE = raw.count(b"\n") - CRLF
assert CRLF > 0 and BARE == 0, "进门前 EOL 不是纯 CRLF: crlf=%d bare=%d" % (CRLF, BARE)
# 【EOL 铁律】锚点用 LF 书写 ⇒ 必须先归一化成 LF 做替换，落盘时再转回 CRLF。
#   不归一化 = 锚点命中 0 次 = **静默不写**（本项目踩过多次）。
assert raw.count(b"\r\n") == raw.count(b"\n"), "存在裸 LF（混合 EOL），先查再改"
t = raw.decode("utf-8").replace("\r\n", "\n")

applied = []


def sub(old, new, label, count=1):
    global t
    n = t.count(old)
    assert n == count, "★ [%s] 锚点命中 %d 次（期望 %d）" % (label, n, count)
    t = t.replace(old, new, count)
    applied.append(label)


# ── A. CSS：三列纵向 stack（固定行高 + 同 gap），并把 clamp 3 行放宽到 4 行 ──────────
OLD_CSS = """.dir-chips { display:inline-flex; flex-wrap:wrap; gap:3px; }
.conf-bars { display:inline-flex; flex-direction:column; gap:2px; }
.conf-bar { display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }"""

NEW_CSS = """/* 【2026-10-19 纵向逐机构对齐】总表「发行方 / 方向 / 置信度」三列改为**一家机构一行**。
   ★ 三列是三个独立单元格，无法共享 grid 行 ⇒ 只能靠
     「每项固定 20px + 同 gap 5px + td 顶部对齐」让第 k 项严格同高。
   ★ 任一处漏设高度（或 td 用默认 middle 对齐）都会**整列错行且不报错** —— 已用断言/护栏钉死。
   ⚠ 行高必须用 box-sizing:border-box，否则 chip 的 padding 会把 20px 撑成 22px ⇒ 逐项累积错位。 */
.rv-stack { display:flex; flex-direction:column; gap:5px; align-items:flex-start; }
.src-item { display:flex; align-items:center; height:20px; box-sizing:border-box;
            white-space:nowrap; font-size:var(--fs-xs); color:var(--text-secondary); }
.dir-chips { display:flex; flex-direction:column; gap:5px; align-items:flex-start; }
.dir-chips > .dir-chip { display:inline-flex; align-items:center; height:20px; box-sizing:border-box; }
.conf-bars { display:flex; flex-direction:column; gap:5px; align-items:flex-start; }
.conf-bars > .conf-bar { display:inline-flex; align-items:center; gap:4px; white-space:nowrap;
                         height:20px; box-sizing:border-box; }
/* 【占位】家数不齐时补的不可见项：占住同一行高，但**不显示任何内容**
   （不能用 display:none —— 那样高度塌陷、后面所有项上移，反而错位）。 */
.src-item.ghost, .dir-chip.ghost, .conf-bar.ghost { visibility:hidden; }
/* 三列的 td 必须顶部对齐：默认 middle 会让内容高度不同的单元格**中心对齐** ⇒ 首项起点错开。 */
.report-content table td.rv-vstack { vertical-align:top; }"""
sub(OLD_CSS, NEW_CSS, "A. CSS 三列纵向 stack")

# clamp 3 行 → 4 行（观点口径放宽后 3 行放不下；两处：.rv-long 与全局 .clamp3）
sub(".rv-long > .clamp3 { display:-webkit-box; -webkit-line-clamp:3;",
    ".rv-long > .clamp3 { display:-webkit-box; -webkit-line-clamp:4;",
    "B. clamp 行数 3→4（观点口径放宽后 3 行放不下）")

# ── C. JS：_decorateViewTable 先拿发行方家数，再据此渲染方向/置信度 ──────────────
OLD_LOOP = """Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const tds = tr.children;
if (tds[iDir]) this._vizDirCell(tds[iDir]);
if (tds[iConf]) this._vizConfCell(tds[iConf]);
iLong.forEach(k => { if (tds[k]) this._clampLongCell(tds[k]); });
});"""
NEW_LOOP = """// 【发行方列 2026-10-19】用户要求：发行方也纵向排列，且与**自己的**方向/置信度逐行对应。
// ★ 家数以**发行方**为准（它是"几家机构"的权威列）：先渲染发行方拿到 n，
//   再让方向/置信度按 n 补齐 —— 否则数据少一项时整列错位、页面不报错。
const iSrc = ths.findIndex(th => (th.textContent || '').trim() === '发行方');
Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const tds = tr.children;
const n = (iSrc >= 0 && tds[iSrc]) ? this._vizSourceCell(tds[iSrc]) : 0;
if (tds[iDir]) this._vizDirCell(tds[iDir], n);
if (tds[iConf]) this._vizConfCell(tds[iConf], n);
iLong.forEach(k => { if (tds[k]) this._clampLongCell(tds[k]); });
});"""
sub(OLD_LOOP, NEW_LOOP, "C. 循环改为先取发行方家数")

# ── D. 新增 _vizSourceCell，并把 _vizDirCell 改为纵向 + 补齐 ──────────────────
OLD_DIR = """_vizDirCell(td) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.innerHTML = '<span class="dir-chips">' + parts.map(p => {
const cls = p.includes('多') ? 'up' : (p.includes('空') ? 'down' : 'flat');
return '<span class="dir-chip ' + cls + '">' + this._escHtml(p) + '</span>';
}).join('') + '</span>';
},"""

NEW_DIR = """_vizSourceCell(td) {
// 【功能】发行方单元格 → 纵向逐机构一项（与方向/置信度第 k 项同高，天然对应）。
// 【返回】家数 n（方向/置信度按它补齐；无研报返回 0）。
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return 0;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return 0;
td.classList.add('rv-vstack');
td.innerHTML = '<span class="rv-stack">' + parts.map(p =>
'<span class="src-item" title="' + this._escHtml(p) + '">' + this._escHtml(p) + '</span>'
).join('') + '</span>';
return parts.length;
},

_vizDirCell(td, n) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.classList.add('rv-vstack');
const chips = parts.map(p => {
const cls = p.includes('多') ? 'up' : (p.includes('空') ? 'down' : 'flat');
return '<span class="dir-chip ' + cls + '">' + this._escHtml(p) + '</span>';
});
// 【补齐 2026-10-19】发行方 3 家而方向只拆出 2 个（脏数据/分隔符异常）时补**不可见占位**。
//   ⚠ 不能用 display:none：高度塌陷会让后续项整体上移 ⇒ 与发行方整列错位。
const total = Math.max(n || 0, chips.length);
while (chips.length < total) chips.push('<span class="dir-chip ghost">·</span>');
td.innerHTML = '<span class="dir-chips">' + chips.join('') + '</span>';
},"""
sub(OLD_DIR, NEW_DIR, "D. 新增 _vizSourceCell / 方向改纵向+补齐")

# ── E. 置信度：纵向 + 补齐 ────────────────────────────────────────────────────
OLD_CONF = """_vizConfCell(td) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.innerHTML = '<span class="conf-bars">' + parts.map(p => {"""
NEW_CONF = """_vizConfCell(td, n) {
const raw = (td.textContent || '').trim();
if (!raw || raw === '—') return;
const parts = raw.split(/\\s*\\/\\s*/).filter(Boolean);
if (!parts.length) return;
td.classList.add('rv-vstack');
const bars = parts.map(p => {"""
sub(OLD_CONF, NEW_CONF, "E1. 置信度入口改纵向+接收家数")

OLD_CONF_END = """+ '<span class="pct" style="color:' + col + '">' + v + '%</span></span>';
}).join('') + '</span>';
},"""
NEW_CONF_END = """+ '<span class="pct" style="color:' + col + '">' + v + '%</span></span>';
});
// 同方向列：按发行方家数补齐不可见占位，保证第 k 项与发行方第 k 家严格同行
const total = Math.max(n || 0, bars.length);
while (bars.length < total) bars.push('<span class="conf-bar ghost">·</span>');
td.innerHTML = '<span class="conf-bars">' + bars.join('') + '</span>';
},"""
sub(OLD_CONF_END, NEW_CONF_END, "E2. 置信度补齐")

# ── F. clamp 判据：字符数阈值 → 几何（是否真被裁）+ 不可见兜底 ────────────────
OLD_CLAMP = """const txt = (td.textContent || '').trim();
if (!txt || txt === '—') return;
if (txt.length <= 120) return;                   // 短内容不动（3 行内放得下，无需按钮）
td.dataset.clamped = '1';
td.classList.add('rv-long');
const box = document.createElement('div');
box.className = 'clamp3';
while (td.firstChild) box.appendChild(td.firstChild);   // 保留原有 HTML（含 <br> 等）
td.appendChild(box);
const btn = document.createElement('button');"""
NEW_CLAMP = """const txt = (td.textContent || '').trim();
if (!txt || txt === '—') return;
td.dataset.clamped = '1';
td.classList.add('rv-long');
const box = document.createElement('div');
box.className = 'clamp3';
while (td.firstChild) box.appendChild(td.firstChild);   // 保留原有 HTML（含 <br> 等）
td.appendChild(box);
// 【判据 2026-10-19：字符数 → 几何】原 `txt.length <= 120 return` 与列宽无关：
//   宽列（观点）明明 4 行放得下也长出按钮，窄列（头寸）放不下反而没有按钮。
//   正确判据是"**是否真被裁**"（scrollHeight > clientHeight）。
//   ⚠ 但折叠容器内 clientHeight=0 ⇒ 测不出来 ⇒ 此时**一律给按钮**：
//     失败方向必须是"多一个按钮"，绝不能是"全文不可达"（那才是用户看到的"又被截断了"）。
const invisible = box.clientHeight === 0;
const clipped = invisible ? true : (box.scrollHeight > box.clientHeight + 2);
if (!clipped) {
// 没被裁 ⇒ 还原 DOM（别让这格莫名其妙多一层 div/变成可点）
while (box.firstChild) td.insertBefore(box.firstChild, box);
box.remove();
td.classList.remove('rv-long');
td.dataset.clamped = '0';
return;
}
const btn = document.createElement('button');"""
sub(OLD_CLAMP, NEW_CLAMP, "F. clamp 改几何判据")

out = t.replace("\n", "\r\n").encode("utf-8")
c2, b2 = out.count(b"\r\n"), out.count(b"\n") - out.count(b"\r\n")
assert c2 > 0 and b2 == 0, "★ 出口 EOL 被破坏: crlf=%d bare=%d" % (c2, b2)
P.write_bytes(out)
print("  已应用 %d 处：" % len(applied))
for x in applied:
    print("   ✔", x)
