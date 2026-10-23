# -*- coding: utf-8 -*-
"""MEMORY.md：给补21 再补一节「官方接口存在 ≠ 有你要的能力」（方案C 排除的可复用判据）。"""
from pathlib import Path
import collections
import re

m = Path(r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/MEMORY.md")
raw = m.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
t = raw.decode("utf-8")

ANCHOR = "### ★ 五、落地时别忘的两件小事"
assert t.count(ANCHOR) == 1
assert "官方接口存在" not in t, "已补过"

SUB = """### ★★★ 七、"官方接口存在" ≠ "官方接口有你要的能力"（2026-10-16 实测）

评估"换官方网关/API Key 来做无人值守"时，最容易犯的错是**只验到"能连上、能列条目"**就下结论。
必须验到"**能拿到目标数据**"这一层，而且要**两个反误判动作**：

| 动作 | 为什么必须做 | 本项目实例 |
|---|---|---|
| **sanity check**（拿同类正常对象问同一接口） | `[]` / 空结果既可能是"没有数据"也可能是"接口坏了" | `/book/chapterinfo` 对公众号返 `chapters: []`；对普通电子书返 **43 章** ⇒ 接口没坏，是"公众号没有章节" |
| **系统性扫参数空间** | 只试一个参数值会得出相反的错误结论 | 扫 10 个 `scope`：0/6/10/12/13/14/16 正常，**只有 2（公众号）/ 4（公众号文章）返 `-2041`**；只看 scope=0 会以为"搜索可用"，只看 scope=4 会以为"搜索整体不可用" |

**本项目结论**：官方网关（微信读书 Agent Gateway）`/shelf/sync` 能列出 6 个公众号**条目**，
但 `/book/chapterinfo` 列不出文章、`/store/search` 的公众号两个 tab 恒被拒
⇒ **无法列出/追踪某号的新文章 ⇒ 不能替代网页会话通道**。

★ 顺带一条判据：**别假设"其他渠道会兜底"**。本项目查过 —— 那 6 个号在库里 10 行的
`filename` 全是 `wxmp_`（读书通道写的）⇒ 放弃该通道 = **彻底失去这 6 个号**。

### ★ 五、落地时别忘的两件小事"""
t = t.replace(ANCHOR, SUB, 1)
t = t.replace("\r\n", "\n").replace("\n", eol)
m.write_bytes(t.encode("utf-8"))
b = m.read_bytes()
hs = re.findall(r"^## .+$", t, re.M)
dup = {k: v for k, v in collections.Counter(hs).items() if v > 1}
print("  MEMORY.md 已更新；EOL: CRLF=%d bareLF=%d；重复标题: %s；总字符 %d"
      % (b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n"), dup if dup else "无 ✓", len(t)))
