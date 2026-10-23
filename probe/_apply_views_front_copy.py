# -*- coding: utf-8 -*-
"""观点总览前端适配「每家最新一期」口径：计数/说明/导出标题/导出行日期。

为什么要改前端：口径从"某一天"变成"各家各自最新一期"后，
  · 顶部写"研报日期 2026-10-21"会骗人（表里还有 10-18 的永安）；
  · 导出 PDF/PNG 的标题同样，"研报日期"要换成区间；
  · 导出件原来"研报"列没有日期 ⇒ 混排后看不出哪条是旧的，必须补上。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

OLD_COUNT = ''' const _dateTxt = rtype === '季报' ? this._quarterLabel(d.date) : d.date;
 if (elCount) elCount.textContent = d.date ? `${rtype ? rtype + ' ' : ''}${d.rows.length} 份 · ${d.dates.length} 个${rtype === '季报' ? '季度' : '日期'}` : '';
 if (elNote) elNote.textContent = d.date
 ? `${d.name}(${d.variety}) · 研报${rtype === '季报' ? '季度' : '日期'} ${_dateTxt}${rtype ? ` · 仅${rtype}结论` : ''}: 各发行方对它的方向与观点要点。同一品种同${rtype === '季报' ? '季' : '日'}可能多份(如覆盖多个子品种), 点标题看完整两段式结论。 —为研报未披露。`
 : '';
'''

NEW_COUNT = ''' const _dateTxt = rtype === '季报' ? this._quarterLabel(d.date) : d.date;
 // 【2026-10-21 口径变更】缺省 = 每家发行方各取最新一期（周报机构不再被日内日报挤掉）。
 //   此时表里是**跨日期**混排，不能再写"研报日期 XXX"，改写成区间 + 说明。
 const _perSrc = !rtype && !!d.latest_per_source;
 const _nSrc = new Set(d.rows.map(x => x.source)).size;
 const _span = d.span || {};
 if (elCount) elCount.textContent = d.date
 ? (_perSrc ? `${_nSrc} 家 · ${d.rows.length} 份` : `${rtype ? rtype + ' ' : ''}${d.rows.length} 份 · ${d.dates.length} 个${rtype === '季报' ? '季度' : '日期'}`)
 : '';
 if (elNote) elNote.textContent = d.date
 ? (_perSrc
 ? `${d.name}(${d.variety}) · 各家最新一期（${_span.oldest || _dateTxt} ~ ${_dateTxt}）：每家取各自最新观点，只发周报的机构（如永安）不会被日内日报挤掉；行上日期为该期研报发布日，点标题看完整两段式结论。 —为研报未披露。`
 : `${d.name}(${d.variety}) · 研报${rtype === '季报' ? '季度' : '日期'} ${_dateTxt}${rtype ? ` · 仅${rtype}结论` : ''}: 各发行方对它的方向与观点要点。同一品种同${rtype === '季报' ? '季' : '日'}可能多份(如覆盖多个子品种), 点标题看完整两段式结论。 —为研报未披露。`)
 : '';
'''

OLD_HEAD = '''<div style="font-size:1.0625rem;font-weight:800;color:${C.text};line-height:1.4">逐品种观点总览 · ${esc(d.name || code)}(${esc(code)}) · 研报日期 ${esc(d.date)}</div>'''
NEW_HEAD = '''<div style="font-size:1.0625rem;font-weight:800;color:${C.text};line-height:1.4">逐品种观点总览 · ${esc(d.name || code)}(${esc(code)}) · ${d.latest_per_source ? `各家最新一期（${esc((d.span || {}).oldest || '')} ~ ${esc(d.date)}）` : `研报日期 ${esc(d.date)}`}</div>'''

OLD_ROW = ''' <td style="padding:6px 8px;color:${C.sub};font-size:0.75rem;border-bottom:1px solid ${C.line}"><span style="color:var(--brand);font-weight:600">${esc(rp.title)}</span>${covers}</td>'''
NEW_ROW = ''' <td style="padding:6px 8px;color:${C.sub};font-size:0.75rem;border-bottom:1px solid ${C.line}"><span style="color:var(--brand);font-weight:600">${esc(rp.title)}</span>${covers}${(rp.publish_date || rp.uploaded_at) ? `<div style="color:${C.muted};font-size:0.7rem;margin-top:2px">${esc(String(rp.publish_date || '').slice(0, 10) || String(rp.uploaded_at || '').slice(0, 10))}</div>` : ''}</td>'''

PAIRS = [(OLD_COUNT, NEW_COUNT), (OLD_HEAD, NEW_HEAD), (OLD_ROW, NEW_ROW)]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    o = old.replace("\n", "\r\n")
    n = new.replace("\n", "\r\n")
    c = out.count(o)
    assert c == 1, "锚点命中 %d 次: %r" % (c, old[:60])
    out = out.replace(o, n, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
