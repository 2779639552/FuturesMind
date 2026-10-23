# -*- coding: utf-8 -*-
"""总表长文本列的**显示收敛**（数据已完整，这里只管"别把表格撑爆"）。

【背景】放开截断后，「一句话核心观点」列最长 1485 字、平均 849 字（周报）
  ⇒ 不收敛的话单个单元格就是 30+ 行高，21 个品种的表会拉成几屏。
【原则】**数据完整 + 显示收敛** —— 不再靠丢数据控制布局：
  · 默认收起为 3 行（100 字左右就能看出大意）；
  · 超出时给一个明确的「展开全文 ▾」按钮（**必须有入口**，否则用户以为还是被截的）；
  · 打印/导出时**全部展开**（截在 PDF 里等于丢了）。

【★ 判据为什么用"字符数"而不是 scrollHeight/clientHeight】
  几何判据在**折叠容器内**会失真：元素 `display:none` 时 clientHeight=0，
  `scrollHeight > clientHeight` 恒真 ⇒ 所有单元格都长出"展开"按钮。
  本页的总结区块是**可折叠的**（`_groupSectorMenus`），故必须用与布局无关的判据。
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① CSS ──────────────────────────────────────────────────
sub(".dir-chip {", '''/* 【长文本列显示收敛 2026-10-16】数据层已放开截断（观点最长 1485 字），
   这里只做**显示**收敛：默认 3 行 + 「展开全文」按钮；打印时全部展开（截在 PDF 里等于丢数据）。 */
.report-content table td.rv-long { cursor: pointer; }
.report-content table td.rv-long > .clamp3 { display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.report-content table td.rv-long.open > .clamp3 { display:block; overflow:visible; }
.report-content table td.rv-long .clamp-toggle { display:inline-block; margin-top:4px; padding:0; border:none; background:none; color:var(--brand); font-size:0.75rem; cursor:pointer; }
@media print {
  .report-content table td.rv-long > .clamp3 { display:block !important; overflow:visible !important; -webkit-line-clamp:unset !important; }
  .report-content table td.rv-long .clamp-toggle { display:none !important; }
}
.dir-chip {''', "① 新增 .rv-long / .clamp3 CSS（含打印展开）")

# ── ② 后处理里加"长文本列"分支 ──────────────────────────────
sub('''Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const tds = tr.children;
if (tds[iDir]) this._vizDirCell(tds[iDir]);
if (tds[iConf]) this._vizConfCell(tds[iConf]);
});''',
    '''// 【长文本列 2026-10-16】按**表头文本**定位（同"方向/置信度"的纪律：不写死列序号）
const iLong = ths.map((th, k) => ({ k, t: (th.textContent || '').trim() }))
.filter(x => x.t === '一句话核心观点' || x.t === '头寸建议').map(x => x.k);
Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const tds = tr.children;
if (tds[iDir]) this._vizDirCell(tds[iDir]);
if (tds[iConf]) this._vizConfCell(tds[iConf]);
iLong.forEach(k => { if (tds[k]) this._clampLongCell(tds[k]); });
});''',
    "② 后处理接线长文本列")

# ── ③ 新增 _clampLongCell 方法 ─────────────────────────────
sub("// 【功能】方向单元格 → 分色 chip：看多=红 / 看空=绿 / 中性=琥珀(中国市场惯例)。",
    '''// 【功能】长文本单元格 → 默认收 3 行 + 「展开全文」（仅**显示**层，不改内容）。
// 【★ 判据用字符数，不用 scrollHeight/clientHeight】几何判据在**折叠容器**内会失真
//   （display:none 时 clientHeight=0 ⇒ "溢出"恒真 ⇒ 每格都长出按钮）。
//   本页总结区块是可折叠的，故用与布局无关的字符数阈值。
// 【为什么必须给按钮】只做 clamp 而不给入口，用户会以为"还是被截断了"
//   —— 本次改动的目的正是让完整内容**可达**。
_clampLongCell(td) {
if (!td || td.dataset.clamped === '1') return;   // 幂等：切日期会重渲染，但同一次内防重复包裹
const txt = (td.textContent || '').trim();
if (!txt || txt === '—') return;
if (txt.length <= 120) return;                   // 短内容不动（3 行内放得下，无需按钮）
td.dataset.clamped = '1';
td.classList.add('rv-long');
const box = document.createElement('div');
box.className = 'clamp3';
while (td.firstChild) box.appendChild(td.firstChild);   // 保留原有 HTML（含 <br> 等）
td.appendChild(box);
const btn = document.createElement('button');
btn.type = 'button';
btn.className = 'clamp-toggle';
btn.textContent = '展开全文 ▾';
btn.addEventListener('click', (e) => {
e.preventDefault();
e.stopPropagation();                             // 别让点击冒泡到行/单元格的其它处理
const on = td.classList.toggle('open');
btn.textContent = on ? '收起 ▴' : '展开全文 ▾';
});
td.appendChild(btn);
},

// 【功能】方向单元格 → 分色 chip：看多=红 / 看空=绿 / 中性=琥珀(中国市场惯例)。''',
    "③ 新增 _clampLongCell 方法")

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
