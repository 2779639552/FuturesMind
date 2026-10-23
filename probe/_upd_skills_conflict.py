# -*- coding: utf-8 -*-
"""更新 agentsense-add-tab skill：fixed 表格列宽是"相对份额"、marked 转义 <br>（2026-10-19）。"""
from pathlib import Path

BLOCK = r"""
## `table-layout:fixed` 的列宽是**相对份额**，不是绝对值（2026-10-19 实测）

【现象】给某列设 `width:120px`、另一列 `width:110px`，但页面上两列实际是 **439px 和 402px**。
【真因】`table-layout:fixed` + `width:100%` 时，若各列定宽之和 < 表宽，浏览器会
**按同一比例摊开**多余空间。实测第二节：

```
定宽  62 / 120 / 110 / 88 / 78   （和 = 458，表宽 1652）
实际 227 / 439 / 402 / 285 / 300   （×3.66，比例恒定）
```

⇒ 用户抱怨"某一列太宽"时，**不要只看那一列的 px**，要看它**在定宽序列里的排位**：
  120px 那列本来是"第二宽"，放大后就成了最宽的 439px。

【修法】**调相对份额**，不是调绝对值：
```css
/* 要压窄的一列：份额降到很低；要让位的一列：份额拉高 */
.report-content table.rv-conflict-tbl td:nth-child(2) { width: 76px;  }  /* 439 → 141 */
.report-content table.rv-conflict-tbl td:nth-child(3) { width: 360px; }  /* 402 → 670 */
```
只把 120 改成 100、而不动另一列 ⇒ 比例几乎没变，**肉眼看不出差别**。
**验收必须量真实 DOM 宽度并比较两者比值**，不能断言"CSS 里写了多少 px"。

【作用域】`nth-child` 是全局规则，必须限定到目标表（动态打 class），否则会压坏别的表。

---

## marked 会把 GFM 表格单元格里的 `<br>` **转义成字面文本**（2026-10-19 实测）

【现象】后端落盘的 md 里 `各家观点与逻辑` 用 `<br>` 分隔 4 家机构（`<br>` 出现 54 次、
后端没有任何转义），但页面上 4 家**全挤在同一段**，而且能直接看到 `<br>` 这几个字符。
【真因】marked 在 **GFM 表格单元格**内把 inline HTML 当纯文本处理（表格语法优先），
DOM 里存的是字面 `&lt;br>`。所以"数据是对的"和"页面是对的"在这里**不是一回事**。

【修法】渲染后做一次「还原 + 拆分」，**只认 `br` 这一种标记**（不要整段反转义，避免注入面）：
```js
const BR = /(?:&lt;|<)br\s*\/?(?:&gt;|>)/gi;       // 转义与未转义都要认
let parts = td.innerHTML.split(BR).map(x => x.trim()).filter(Boolean);
if (parts.length < 2) return;                       // 单家 ⇒ 不动（别把一句话拆碎）
td.innerHTML = parts.map(x => '<div class="cf-row">' + x + '</div>').join('');
```
配套 CSS 给段间加虚线分隔（否则"每家一行"读起来仍是一整块）。

【★ 验收判据】
- 断言 **每个 `.cf-row` 的 `getBoundingClientRect().top` 互不相同** —— 这才是"真的各占一行"；
  只数 `.cf-row` 个数不够（它们可以并排）。
- 断言单元格内**没有残留的字面 `<br>` 文本**（`textContent` 里搜不到 `<br`）。
- **单家行必须不拆**：否则段数≠家数，校验失真、DOM 也多一层无意义包裹。
- ⚠ 别用 `td 高度 / 行高` 判断"折了几行"：同一行里别的列很长会把 td 撑高，
  量出来是整行高度。要判断某格文本是否换行，量**文本自身宽度**（`Range.selectNodeContents`）。
"""

p = Path.home() / ".workbuddy" / "skills" / "agentsense-add-tab" / "SKILL.md"
t = p.read_text(encoding="utf-8")
if "table-layout:fixed` 的列宽是**相对份额**" in t:
    print("  已存在，跳过")
else:
    p.write_text(t.rstrip() + "\n" + BLOCK, encoding="utf-8")
    print("  agentsense-add-tab 已追加（%d 行）" % ((t.rstrip() + "\n" + BLOCK).count("\n") + 1))
