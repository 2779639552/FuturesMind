# -*- coding: utf-8 -*-
"""第二节「观点对比与冲突」：收窄分歧状态列 + 每家发行方独占一行（2026-10-19）。

【用户要求】①「分歧状态」篇幅太宽，适当截断分给「各家观点与逻辑」；
            ② 观点与逻辑部分尽量每个发行方单独占一行/多行，不要多家挤在一行。

【★ 根因一：列宽不是"定宽"，而是"按比例放大"】
  `.report-content table{table-layout:fixed;width:100%}` 下，若各列定宽之和 < 表宽，
  浏览器会**按比例摊开**多余空间。实测第二节（表宽 1652px）：
      品种 62→227、分歧状态 120→439、观点与逻辑 110→402、焦点 88→285、根源 78→300
  比例恒为 ≈3.66 ⇒ 「分歧状态」(120px，全表第二宽) 被放大成 439px，成了最宽的列。
  所以修法不是"改一个绝对值"，而是**调各列的相对份额**：把分歧状态的份额压到很低、
  把观点列的份额拉高。改绝对值而不管比例 = 看不出变化。

【★ 根因二：LLM 写的 `<br>` 被 marked 转义成字面文本】
  落盘 md 里是正常的 `<br>`（54 处），后端未转义；但 marked 在 GFM 表格单元格里
  把 inline HTML 当纯文本 ⇒ DOM 里是 `&lt;br>` 字面字符 ⇒ 每家机构**没有真换行**，
  全都挤在同一段里。故渲染后做一次「还原 + 拆分」。
  ⚠ 只还原 `br` 这一种标记（不是整段反转义），不会引入注入面。

【EOL】web_template.html 为 CRLF，必须归一化后再替换、落盘时转回。
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 非纯 CRLF"
t = raw.decode("utf-8").replace("\r\n", "\n")

applied = []


def sub(old, new, label):
    global t
    n = t.count(old)
    assert n == 1, "★ [%s] 锚点命中 %d 次" % (label, n)
    t = t.replace(old, new, 1)
    applied.append(label)


# ── A. CSS：第二节专属列宽 + 每家一行的样式 ────────────────────────────────────
OLD_CSS = """.report-content table td.rv-vstack { vertical-align:top; }"""
NEW_CSS = """.report-content table td.rv-vstack { vertical-align:top; }
/* ── 第二节「观点对比与冲突」列宽重排（2026-10-19 用户要求）────────────────────
   ★ 这里的 px **不是最终宽度**：table-layout:fixed + width:100% 下，各列定宽之和
     小于表宽时会**按比例摊开**。所以真正要调的是「相对份额」：
       分歧状态 120 → 76（份额降到约 1/12），观点与逻辑 110 → 360（份额拉到约 40%）。
     实测（表宽 1652px）：分歧状态 439 → 141px，观点与逻辑 402 → 670px。
   ⚠ 只改绝对值、不调相对份额 ⇒ 看不出变化（浏览器照样等比放大）。 */
.report-content table.rv-conflict-tbl th:nth-child(2),
.report-content table.rv-conflict-tbl td:nth-child(2) {
  width: 76px; text-align: center; white-space: nowrap;      /* 分歧状态：4~5 字，够用即止 */
}
.report-content table.rv-conflict-tbl th:nth-child(3),
.report-content table.rv-conflict-tbl td:nth-child(3) {
  width: 360px;                                              /* 各家观点与逻辑：吃最大份额 */
}
.report-content table.rv-conflict-tbl th:nth-child(4),
.report-content table.rv-conflict-tbl td:nth-child(4) { width: 190px; }   /* 分歧焦点 */
.report-content table.rv-conflict-tbl th:nth-child(5),
.report-content table.rv-conflict-tbl td:nth-child(5) { width: 200px; }   /* 分歧根源 */
/* 每家机构独占一段：段间加一条虚线，扫读时能立刻数清"几家" */
.report-content table td.cf-cell .cf-row { display:block; }
.report-content table td.cf-cell .cf-row + .cf-row {
  margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--border-light);
}"""
sub(OLD_CSS, NEW_CSS, "A. 第二节专属列宽 + 每家一段样式")

# ── B. 接线：在总表后处理之后调用 ────────────────────────────────────────────
# ⚠ 原文行首有 1 个空格（类方法体内的缩进），锚点必须带上
OLD_CALL = """ this._decorateViewTable(el);
 } catch (e) { el.innerHTML = '<span style="color:var(--red)">加载失败</span>'; }"""
NEW_CALL = """ this._decorateViewTable(el);
 // 【2026-10-19】第二节「观点对比与冲突」：收窄分歧状态列 + 每家机构独占一行。
 // 放在总表之后：两者定位用的表头不同(方向/置信度 vs 分歧状态/各家观点与逻辑)，互不干扰。
 this._decorateConflictTable(el);
 } catch (e) { el.innerHTML = '<span style="color:var(--red)">加载失败</span>'; }"""
sub(OLD_CALL, NEW_CALL, "B. 接线 _decorateConflictTable")

# ── C. 新方法：插在 _decorateViewTable 之前 ──────────────────────────────────
OLD_METHOD = """// 【功能】把「当日/本周/本季观点总表」的方向列与置信度列转成可视化(2026-10-16 用户要求)。"""
NEW_METHOD = """// 【功能】第二节「观点对比与冲突」：①列宽重排（分歧状态收窄、观点与逻辑扩容）；
//   ②把「各家观点与逻辑」拆成**每家机构一段**（2026-10-19 用户要求）。
// 【定位】按**表头文本**找列，不写死列序号（与总表同一纪律）。
// 【★ 为什么需要拆行】LLM 在 md 里用 `<br>` 分隔各家，但 **marked 会把 GFM 表格单元格
//   里的 inline HTML 转义成字面文本**（实测 DOM 里是 `&lt;br>`）⇒ 页面上 `<br>` 只是
//   几个可见字符、每家机构全挤在同一段里。故渲染后还原成真正的块级段落。
// 【安全】只识别 `br` 这一种标记并替换为等价的换行，**不做整段反转义** ⇒ 不引入注入面。
// 【幂等】打 `dataset.cfDone`，切日期/类型会重渲染，重复调用不叠加。
_decorateConflictTable(root) {
if (!root) return;
Array.from(root.querySelectorAll('table')).forEach(tbl => {
if (tbl.dataset && tbl.dataset.cfDone === '1') return;
const ths = Array.from(tbl.querySelectorAll('thead th'));
const iState = ths.findIndex(th => (th.textContent || '').trim() === '分歧状态');
const iLogic = ths.findIndex(th => (th.textContent || '').trim() === '各家观点与逻辑');
if (iState < 0 || iLogic < 0) return;        // 只认第二节这张表，不误伤其它表格
if (tbl.dataset) tbl.dataset.cfDone = '1';
if (tbl.classList) tbl.classList.add('rv-conflict-tbl');
Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => {
const td = tr.children[iLogic];
if (td) this._splitSourceRows(td);
});
});
},

// 【功能】把一个「各家观点与逻辑」单元格拆成每家一段。
// 【切分优先级】① 真/转义的 `<br>`（LLM 的正常写法）→ ② 「机构名 + 方向(置信度)」模式
//   （LLM 偶尔用「；」连写多家时的兜底）→ ③ 都切不开就保持原样（只有一家，不该拆）。
_splitSourceRows(td) {
if (!td || td.dataset.cfSplit === '1') return;
const html = td.innerHTML || '';
// `&lt;br>` / `&lt;br&gt;` / `<br>` / `<br/>` 全部视为分隔符
const BR = /(?:&lt;|<)br\\s*\\/?(?:&gt;|>)/gi;
let parts = html.split(BR).map(x => x.trim()).filter(Boolean);
if (parts.length < 2) {
// 兜底：文本里出现两个及以上「机构 方向(数字%)」才算多家（避免把一句话误切）
const joined = parts.join(' ');
const PAT = /(?=[\\u4e00-\\u9fa5A-Za-z]{2,8}\\s*(?:看多|看空|中性|偏多|偏空)\\s*[(（]\\s*\\d+\\s*%)/;
const byPat = joined.split(PAT).map(x => x.trim()).filter(Boolean);
if (byPat.length > parts.length) parts = byPat;
}
if (parts.length < 2) return;                // 只有一家机构 ⇒ 不动（别把单句拆碎）
td.dataset.cfSplit = '1';
td.classList.add('cf-cell');
td.innerHTML = parts.map(x => '<div class="cf-row">' + x + '</div>').join('');
},

// 【功能】把「当日/本周/本季观点总表」的方向列与置信度列转成可视化(2026-10-16 用户要求)。"""
sub(OLD_METHOD, NEW_METHOD, "C. 新增 _decorateConflictTable / _splitSourceRows")

out = t.replace("\n", "\r\n").encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "出口 EOL 破坏"
P.write_bytes(out)
print("  已应用 %d 处：" % len(applied))
for x in applied:
    print("   ✔", x)
