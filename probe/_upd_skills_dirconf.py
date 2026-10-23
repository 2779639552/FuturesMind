# -*- coding: utf-8 -*-
"""更新两个 skill：
  ① agentsense-add-tab —— 给"后端生成的 markdown 表格"做可视化（渲染后处理）
  ② mutation-guard-verification —— ★ 护栏取方法体的"窗口跨方法"陷阱
"""
from pathlib import Path

# ═══════════════════════════════════════════════════════════ ① agentsense-add-tab
p1 = Path("C:/Users/lenovo/.workbuddy/skills/agentsense-add-tab/SKILL.md")
raw = p1.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
s = raw.decode("utf-8")
A1 = "## 外文研报「超长正文」的分层归纳（2026-10-13 实测，改 `foreign_report.py`）"
assert s.count(A1) == 1, ("skill1 anchor", s.count(A1))

BLOCK1 = '''## 给「后端生成的 markdown 表格」加可视化：用**渲染后处理**（2026-10-16 实测）

**需求形态**：用户说"这张表列 X/列 Y 的可视性优化一下"（如方向用颜色分档、置信度用进度条），
而那张表是**后端代码生成 markdown** 再 `marked.parse` 出来的。

### ★★★ 一、先分清表格是谁生成的 —— 决定实现方式

| 类型 | 做法 |
|---|---|
| **后端生成 markdown**（且会落盘 .md） | ★ **渲染后处理 DOM**，**不改后端** |
| 前端 JS 直接生成 HTML | 直接改模板串 |

**为什么后端表不改后端**：本项目「当日观点总表」由后端拼 markdown、**落盘**到
`~/.tradingagents/research_daily/{date}.md`。改后端要 ①**重跑 LLM 总结**才生效
（不能改历史已生成的）②往落盘 md 里塞 HTML（污染落盘件、影响导出/对比/后续解析）。
⇒ **后处理**对已生成的内容立即生效、不动数据、随时可回退。**改后端 = 0 收益 + 一堆副作用。**

### ★★★ 二、后处理的四个必备件

```js
_decorateViewTable(root) {
  Array.from(root.querySelectorAll('table')).forEach(tbl => {
    if (tbl.dataset.vizDone === '1') return;                       // ③ 幂等
    const ths = Array.from(tbl.querySelectorAll('thead th'));
    const iA = ths.findIndex(th => th.textContent.trim() === '方向');   // ① 按表头文本定位
    const iB = ths.findIndex(th => th.textContent.trim() === '置信度');
    if (iA < 0 || iB < 0) return;                                  // ② 多列同时命中才处理
    tbl.dataset.vizDone = '1';
    Array.from(tbl.querySelectorAll('tbody tr')).forEach(tr => { /* 逐格改造 */ });
  });
}
```

1. **按表头文本定位列**，别写死列序号：列由后端拼，**加一列就整体平移**
   ⇒ 写死 index 会把样式画到**别的列**上，**不报错、只是错位**（本项目 2026-09-11 加过「头寸建议」列）。
2. **要求多列同时命中**：只要求"含列 A"或"含列 B"任一命中，会改到不相干的表。
3. **幂等标记**：切日期/换口径会**重渲染同一容器** ⇒ 没标记就"chip 里套 chip"，每切一次厚一层。
4. **接线要在"渲染完"之后**：定义了方法漏调用 = **静默失效**（页面照常、只是没效果）。

### ★★★ 三、三个"静默出错"的判据（务必进护栏）

- **配色语义反转不报错**：本项目原有代码把"看多=绿、看空=红"（与中国惯例**相反**），
  而且**两处不一致** ⇒ 任何人"照抄旧代码"就把语义改回去。⇒ 断言钉死三态色值。
  ★ **只改方向专用的类**：本项目 `badge-bull/bear` 被「厂库/仓库」等**非方向语义**复用
  ⇒ 改 CSS 会误伤，先查"这个类还有谁在用"。
- **★ 缺失值绝不能画成 0**：研报常不给置信度（项目约定显示「—」，**不拿 0.5 冒充**）
  ⇒ 把「—」渲染成 0% 空进度条，会被读成"分析给了个 0 分" ⇒ **编造数据**。
  做法：对缺失**早退**；解析失败时**原样文本保留**，不要落成 0。
- **一行多个值必须逐个渲染**：汇总表常把多家来源合一行、用 ` / ` 分隔（如「看空 / 看多」）
  ⇒ 整格只渲染一个会把**分歧抹平**，而分歧正是这类表最该看的。⇒ 按分隔符拆开逐项上色。

### ★★ 四、视觉验证的两个坑

- **E2E 判色要按 RGB 组合分类**，别用"R 通道大就是红"：**琥珀(橙)的 R 也大**
  ⇒ 中性被误判成看多（假 FAIL）。看**绿通道**最稳（红 G≈69 / 琥珀 G≈159 / 绿 G≈209）。
- **取样要遍历**：第一个样本可能只有"中性" ⇒ 那样"看多=红/看空=绿"其实**没验到**。
  遍历到取到目标样本为止；取不到就**如实报"未取到样本"**，别假装通过。
- **进度条长度**：38px 粗看难分辨 63% 与 72% ⇒ 46px 起比较舒服（宽度=数值，颜色=强度档）。

''' + A1
s = s.replace(A1, BLOCK1.replace("\n", eol), 1)
s = s.replace("\r\n", "\n").replace("\n", eol)
p1.write_bytes(s.encode("utf-8"))
b1 = p1.read_bytes()
print("  ① agentsense-add-tab 已更新；EOL: CRLF=%d bareLF=%d"
      % (b1.count(b"\r\n"), b1.count(b"\n") - b1.count(b"\r\n")))

# ═══════════════════════════════════════════════════════════ ② mutation-guard-verification
p2 = Path("C:/Users/lenovo/.workbuddy/skills/mutation-guard-verification/SKILL.md")
raw2 = p2.read_bytes()
eol2 = "\r\n" if raw2.count(b"\r\n") > 0 else "\n"
s2 = raw2.decode("utf-8")
A2 = "## 配套：判定\"测试是真通过还是假通过\""
assert s2.count(A2) == 1, ("skill2 anchor", s2.count(A2))

BLOCK2 = r'''## ★★★ 陷阱：护栏取"方法体"时用**固定长度窗口** ⇒ 窗口跨进下一个方法（2026-10-16 实测）

**症状**：变异**存活**，但看代码怎么看都对 —— 因为**生产代码完全正确**，是**护栏的取文范围**错了。

**实测经过**：给两个相邻的前端方法写护栏，用固定窗口取方法体：

```python
i = h.index('_vizDirCell(td) {')
body = h[i:i + 1500]          # ❌ 窗口跨进了下一个方法
```

而这两个方法只隔 **617 字符** ⇒ 把 `_vizDirCell` 的关键实现改成"整格只渲染一个值"
（会**抹平多家机构的分歧**，是真实缺陷），断言**照样通过** —— 因为它查的那句
（`split(/\s*\/\s*/)`）其实是**下一个方法**里的。

**修法**：按**下一个标记**精确切片，不要用长度：

```python
def _method_body(src: str, name: str, until: str) -> str:
    """取 [name, until) 之间的方法体。★ 别用固定长度窗口 —— 相邻方法可能只隔几百字符，
    窗口会跨进下一个方法 ⇒ 断言在"A 里查 B 才有的语句"时照样通过（变异存活却看不出）。"""
    i = src.index(name)
    j = src.index(until, i)
    return src[i:j]
```

> **判据**：`窗口长度 > 目标方法与下一个方法之间的距离` 时，必须改成边界切片。
> 实测检查法：`print(h.index(next_method) - h.index(this_method))`，与窗口长度比一下。

**为什么这类洞特别危险**：
- 生产代码**完全正确**、护栏**红绿也对得上**，只有把生产代码改坏时才暴露；
- 若这个变异项恰好没设计（只测了"完全删掉这个方法"这种粗变异），**永远发现不了**。
⇒ **变异项要设计"把关键实现换成看似合理的简化版"**，而不只是"删掉整段"。

### ★ 同一轮的第二类陷阱：变异锚点被"同形语句"命中多次 ⇒ 静默跳过

实测：`if (!raw || raw === '—') return;` 在两个方法里**各有一份** ⇒ `count == 2` ⇒
脚本按"锚点不唯一"跳过该变异（**看着像"已验证"，其实什么都没验**）。

**修法**：锚点带**唯一上下文**（后一行 / 独有的前缀字符串），并**让脚本在"跳过"时醒目告警**
（本项目脚本打印 `⚠ 跳过（锚点命中 N）` 并在汇总里统计，避免"跳过"被当成"通过"）。

★ 相关：**补丁插入的代码缩进常与手写代码不同**（补丁块多是**顶格**）
⇒ 锚点里带 2 空格缩进会命中 0。写锚点前先 `print(repr(那一行))`。

''' + A2
s2 = s2.replace(A2, BLOCK2.replace("\n", eol2), 1)
s2 = s2.replace("\r\n", "\n").replace("\n", eol2)
p2.write_bytes(s2.encode("utf-8"))
b2 = p2.read_bytes()
print("  ② mutation-guard-verification 已更新；EOL: CRLF=%d bareLF=%d"
      % (b2.count(b"\r\n"), b2.count(b"\n") - b2.count(b"\r\n")))
