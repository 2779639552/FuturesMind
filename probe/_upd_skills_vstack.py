# -*- coding: utf-8 -*-
"""更新两个 skill（2026-10-19）。两者都是 LF。"""
from pathlib import Path

BLOCK_A = r"""
## 多机构「逐行对应」的三列纵向对齐（2026-10-19 实测，研报总表）

【场景】一张表里「发行方 / 方向 / 置信度」各自是一家机构一个值，原来是 `A / B / C` 横向塞在一行，
        用户要求**纵向排列**且第 k 家在三列里必须**同一行**。

【★ 核心事实：三个独立 `<td>` 无法共享 grid 行】它们各自排版，唯一能让第 k 项同行的办法是
**「每项等高 + 同 gap + td 顶部对齐」**，缺任一项就**逐项累积错位**（页面不报错，只是"看着没对齐"）：

```css
.rv-stack   { display:flex; flex-direction:column; gap:5px; align-items:flex-start; }
.src-item   { height:20px; box-sizing:border-box; }   /* 三列都要 */
.dir-chips  { gap:5px; }  .dir-chips > .dir-chip { height:20px; box-sizing:border-box; }
.conf-bars  { gap:5px; }  .conf-bars > .conf-bar { height:20px; box-sizing:border-box; }
td.rv-vstack { vertical-align:top; }                   /* ★ 默认 middle 会让内容不等的格中心对齐 */
```

**必须 `box-sizing:border-box`**：否则 chip 的 `padding` 会把 20px 撑成 22px，多机构行里
第 2、3 项越差越多。**三列 gap 必须相同**（5/4 混用也会错开）。

【家数以「发行方」为准】它是"几家机构"的权威列。先渲染它拿到 `n`，再让方向/置信度按 n 补齐：

```js
const n = this._vizSourceCell(tds[iSrc]);   // 返回家数
this._vizDirCell(tds[iDir], n);
this._vizConfCell(tds[iConf], n);
// 列内：家数不足时补**不可见占位**（不能 display:none —— 高度塌陷会把后续项整体上移）
const total = Math.max(n || 0, chips.length);
while (chips.length < total) chips.push('<span class="dir-chip ghost">·</span>');
// CSS: .src-item.ghost { visibility:hidden; }
```

【列宽重排：`nth-child` 是全局的，必须限定到目标表】
`.report-content table td:nth-child(3){width:110px}` 这种规则会作用于**页面所有表**；
直接收窄会把第二节「各家观点与逻辑」等**列序完全不同**的表一起压坏。
做法：渲染时动态打专属 class（`.rv-views-tbl`），再用**更高特异性**覆盖：

```css
.report-content table.rv-views-tbl td:nth-child(3) { width:100px; }   /* 0,3,1 > 0,2,1 */
```

⚠ 打标必须在 `_clampLongCell` **测量之前** —— 列宽变了，"4 行是否放得下"的结论也会变。

【实测收益】观点列 441→480px（每行 28→30 字，4 行容量 112→120 字）。
【验收判据】用真 DOM 量 `getBoundingClientRect().top`：三列第 k 项必须 `Δ ≤ 1.5px`，
并断言每项高度 `= 20px`（**不是**只断言 CSS 里写了 20px）。
"""

BLOCK_B = r"""
## ★★★ 陷阱：提示词里的**禁止类示例**会被 LLM 当成模板照抄（2026-10-19 实测）

【现象】要 LLM"不要逐家罗列"，于是在 prompt 里写了反例：

```
严禁写成「永安 看多(58%)—出口配额紧；国泰君安 中性(58%)—柴油支撑」这种句式
```

结果 **21/21 行全部**是这个格式 —— 模型对"长什么样"的模仿远强于对"不许"的理解。

【修法】① **删掉反例**，只留**正面示例**；② 用肯定句描述目标形态（"合成一段、结构为驱动→结论"）；
③ 把"不要什么"改写成**可执行的输出前自检**（"若出现任何机构名或百分数即不合格，重写"）；
④ 若素材本身按机构分行，必须显式说明"**素材分行 ≠ 输出分行**"（模型会把输入结构当输出结构）。

【护栏】断言**目标段落内**不出现该格式的示例（`re.search(r"看[多空]\s*[(（]\s*\d+\s*%", seg)`），
而不是断言"模型不会照抄"（那测不了）。注意只查对应段落 —— 别的段落本来就需要这个格式。

---

## ★★★ 陷阱：**结构校验过严**与过松一样致命——它会静默回退到降级路径（2026-10-19 实测）

【现象】给"这一行是不是目标表的列结构"加校验，写 `len(方向格) <= 6`。
但多机构时方向格是 `中性/中性/中性/中性`（**11 字**）⇒ **所有多机构行**被跳过
⇒ 解析结果为空 ⇒ 代码走 `if not views:` 兜底 ⇒ 回退"原文拼接"，观点从 90 字 **暴涨到 800 字**。
放宽成逐项 `len(p) <= 4` 后，又挡掉了 `中性偏震荡`（5 字）⇒ 该品种单独回退。
**两次都不报错**，只表现为"长度异常"。

【判别与修法】
1. **降级路径必须留痕**：`if not parsed:` 分支里 `logger.warning(...)` 带**条数**
   （0 条 vs 21 条 vs 19 条，一眼看出是"结构不匹配"还是"LLM 少写"）。
2. 校验优先用**结构性特征**（含不含句读、是否恰好 N 列、是否有分隔符）而不是**长度阈值**；
   长度只做宽松兜底上限。
3. **必须用真实样本做行为验证**：结构断言（"代码里有 `len(p) > 8`"）拦不住
   "校验过严 ⇒ 整行被跳过"，只有拿真 LLM 输出跑一遍、断言 `views["P"]` 存在才算数。
4. 出现"数字突然翻倍/骤降"时，先看**是否走了兜底分支**，别急着调 prompt。
"""

p = Path.home() / ".workbuddy" / "skills" / "agentsense-add-tab" / "SKILL.md"
t = p.read_text(encoding="utf-8")
if "三列纵向对齐" in t:
    print("  agentsense-add-tab: 已存在，跳过")
else:
    p.write_text(t.rstrip() + "\n" + BLOCK_A, encoding="utf-8")
    print("  agentsense-add-tab: 已追加（%d → %d 行）" % (t.count("\n") + 1, (t.rstrip() + "\n" + BLOCK_A).count("\n") + 1))

q = Path.home() / ".workbuddy" / "skills" / "mutation-guard-verification" / "SKILL.md"
s = q.read_text(encoding="utf-8")
if "禁止类示例" in s:
    print("  mutation-guard-verification: 已存在，跳过")
else:
    q.write_text(s.rstrip() + "\n" + BLOCK_B, encoding="utf-8")
    print("  mutation-guard-verification: 已追加（%d → %d 行）" % (s.count("\n") + 1, (s.rstrip() + "\n" + BLOCK_B).count("\n") + 1))
