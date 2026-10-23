# -*- coding: utf-8 -*-
"""更新两个 skill：
  ① mutation-guard-verification —— 判据自身的三种出错形态（自指 / AST 形态 / 计数太松）
  ② agentsense-add-tab —— 「数据完整 + 显示收敛」这套改法（含 prompt 预算配套）
"""
from pathlib import Path

# ═══════════════════════════════════ ① mutation-guard-verification
p1 = Path("C:/Users/lenovo/.workbuddy/skills/mutation-guard-verification/SKILL.md")
raw = p1.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
s = raw.decode("utf-8")
A1 = '## 配套：判定"测试是真通过还是假通过"'
assert s.count(A1) == 1, ("skill1 anchor", s.count(A1))

B1 = '''## ★★★ 陷阱：判据**自己**出错——三种形态都能让变异"假存活/假击杀"（2026-10-16 实测）

这一节讲的是：**变异存活了，但你查了半天生产代码，发现代码是对的** —— 那是**判据**的问题。

### ① 自指：判据把我自己的**注释**当成了违规代码

```python
assert 'view[:60] + "…"' not in src          # ❌
```
我在生产代码里写了说明：`# 【2026-10-16】此处原先 `view[:60] + "…"`` ⇒ 这个串**存在于文件**，
断言**恒红**（而且看起来像"没修好"）。
> ★ 修正：**用 AST 判定**，只看真实语法结构。注释/docstring 不是违规代码。
> ```python
> for node in ast.walk(ast.parse(src)):
>     if isinstance(node, ast.Subscript): ...
> ```
> 同类：`assert "旧的错误写法" not in src` 这种"查字符串"的判据，**只要你在代码里解释过它，就会自指**。

### ② ★ AST 认错形态：`x[:N]` 不是 `ast.Constant`

```python
sl = node.slice
if isinstance(sl, ast.Constant) and isinstance(sl.value, int):   # ❌ 漏掉 x[:N]
```
- `x[N]`   → `slice` 是 `ast.Constant(N)`
- `x[:N]`  → `slice` 是 **`ast.Slice(lower=None, upper=Constant(N), step=None)`**

⇒ 只认 `Constant` 会**漏掉 `x[:N]`（最常见的截断写法）**。实测：把 `view[:60]` 加回去，
护栏**全绿**（变异 M1/M2 存活）。**必须两种形态都认**：
```python
if isinstance(sl, ast.Constant) and isinstance(sl.value, int):     n_val = sl.value
elif isinstance(sl, ast.Slice) and isinstance(sl.upper, ast.Constant): n_val = sl.upper.value
```

### ③ 计数式判据太松：`>= N` 会随源码增删而失效

```python
assert src.count('replace("\\n", " ").replace("|", "/")') >= 3   # ❌ 实际有 4 处
```
删掉其中一处仍 `>= 3` ⇒ 放行（变异 M8 存活）。
> ★ 修正：改成**不变量**而不是计数 ——
> **"凡是清 `|` 的地方必须同时清 `\\n`"**（与处数无关，还能抓"以后新增字段又只清一半"）：
> ```python
> offenders = [ln for ln in body.splitlines()
>              if 'replace("|", "/")' in ln and 'replace("\\n", " ")' not in ln]
> assert not offenders
> ```

### ★ 小结：变异存活时，优先怀疑判据，而不是生产代码

排查顺序：
1. **判据是不是自指的**（我自己的注释/说明命中了它）；
2. **判据是不是认错了形态**（AST 节点类型、正则锚点）；
3. **判据是不是太松**（计数阈值、`or` 连接、窗口过长）；
4. 前三都排除后，才是"生产代码真被改坏了却没拦住"。

''' + A1
s = s.replace(A1, B1.replace("\n", eol), 1)
s = s.replace("\r\n", "\n").replace("\n", eol)
p1.write_bytes(s.encode("utf-8"))
b1 = p1.read_bytes()
print("  ① mutation-guard-verification 已更新；EOL: CRLF=%d bareLF=%d"
      % (b1.count(b"\r\n"), b1.count(b"\n") - b1.count(b"\r\n")))

# ═══════════════════════════════════ ② agentsense-add-tab
p2 = Path("C:/Users/lenovo/.workbuddy/skills/agentsense-add-tab/SKILL.md")
raw2 = p2.read_bytes()
eol2 = "\r\n" if raw2.count(b"\r\n") > 0 else "\n"
s2 = raw2.decode("utf-8")
A2 = "## 外文研报「超长正文」的分层归纳（2026-10-13 实测，改 `foreign_report.py`）"
assert s2.count(A2) == 1, ("skill2 anchor", s2.count(A2))

B2 = '''## 用户说「内容被截断了」—— 数据层完整 + 显示层收敛（2026-10-16 实测）

**症状**：用户指着某列说"部分内容被截断"（如某行的"一句话核心观点"断在半个括号里）。

### ★★★ 一、先找全**所有**截断层（它们通常**串联**）

本项目实测 **4 层、7 处**，层层叠加：
① 内容提取 `max_len=150`（把函数默认 360 收紧）
② 同源合并 `text_cap=200`
③ 组装展示：每家 `op[:40]` → 拼接后 `view[:80]`（**两层叠加**，就是它把句子断在括号中间）
④ 同型字段 `trade[:60]` 等

```bash
# 一次列全候选
grep -n 'view\\[:60\\]\\|op\\[:40\\]\\|text_cap=\\|max_len=\\|\\[:80\\]' src.py
```
★ **必须写一个分层观测脚本**（逐层打印长度：items → 原文 → 最终行），**用真实数据**跑。
只用构造数据测会漏上游 —— 构造往往绕过 `_collect_*` 这类函数（本项目第一版就漏了 `max_len` 那层）。

### ★★★ 二、★ 放开前先查：这个上限是不是**为别的目的**定的

本项目 `text_cap=200` 的注释写着"**21 品种 × 4 机构 × 200 字仍在 payload 28000 以内**"
⇒ 它是**按 LLM prompt 预算算出来的**。盲目放开到 360 ⇒ 超预算，
而 `payload[:28000]` 是**静默硬切**（不告知 ⇒ LLM 以为后面的品种没研报 ⇒ 漏写/编造）。
> **纪律**：改任何"上限常量"前，读它的注释与调用处，确认**它是不是另一个约束的解**。
> 是的话 —— **改 A 必须带改 B**。

### ★★★ 三、正解：解耦两种长度需求

```
数据层：text_cap=0                     ⇒ items 存【完整】（供显示）
prompt：组装时 [:320] 精简 + 按预算装载 ⇒ 预算与改前相当
显示层：默认 3 行 + 「展开全文」        ⇒ 不把表格撑爆
```
★ 附带好处：某条目因预算被**略过**时，展示端会**回退到完整文本** ⇒ 略过 ≠ 显示缺失。
★ 已生成的 md 里是**旧代码写的截断文本** ⇒ 必须 `force` 重跑才看得到效果，
  否则用户会以为"没修好"（**要主动告知并重跑**，别只改代码就交差）。

### ★★ 四、"按预算装载"三原则

```python
def _fit_prompt_payload(blocks, budget=28000):
    # 装得下就装；装不下【跳过】（不是停止）；被略过的【显式标注】在末尾
```
- **跳过而非停止**：`break` 会被一个长条目挡住**后面所有**短条目。
- **必须显式标注**：静默截断 ⇒ LLM 以为"那些品种今天没研报"。
- **抽成模块级纯函数**：内嵌闭包无法从测试注入，护栏咬不住。

### ★★★ 五、放开截断时**绝不能连带删掉**的两件事

1. **清洗**：`\\n` → 空格（markdown 单元格里换行会把**表劈成两行**）、
   `|` → `/`（不换则**后面列全错位**）。都是"不报错、只是表坏了"。
   ★ 顺手能查出真 bug：本项目 `opinion` 两样都清、`trade` **只清竖线**（头寸含换行就劈表）。
2. **预算**（见二）。

### ★★ 六、显示收敛的判据用**字符数**，别用几何量

`scrollHeight > clientHeight` 在**折叠容器**内失真（`display:none` 时 clientHeight=0
⇒ "溢出"恒真 ⇒ 每格都长按钮）。本页总结区块可折叠 ⇒ 必须用字符数阈值。
★ **必须给「展开全文」入口**（只 clamp 不给入口 = 用户仍以为被截断），
且**打印时全部展开**（`@media print` 放开 clamp + 隐藏按钮）——截在 PDF 里等于丢数据。

''' + A2
s2 = s2.replace(A2, B2.replace("\n", eol2), 1)
s2 = s2.replace("\r\n", "\n").replace("\n", eol2)
p2.write_bytes(s2.encode("utf-8"))
b2 = p2.read_bytes()
print("  ② agentsense-add-tab 已更新；EOL: CRLF=%d bareLF=%d"
      % (b2.count(b"\r\n"), b2.count(b"\n") - b2.count(b"\r\n")))
