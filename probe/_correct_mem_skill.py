# -*- coding: utf-8 -*-
"""更正 MEMORY.md 补21 与 skill W-7e（我先前基于**接口层错误码**误判成**会话层问题**），
并把本轮**已验证可用**的替代链路（cover + content）写进 skill。
"""
from pathlib import Path

MEM = Path(r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/MEMORY.md")
SK = Path(r"C:/Users/lenovo/.workbuddy/skills/silent-pipeline-failure-triage/SKILL.md")

CORRECTION = """
> ### ⚠️⚠️ 【2026-10-16 更正 · 必读】上述"寿命/频率"结论**已被推翻**
>
> **错在哪**：我拿 `/web/mp/articles` 的错误码（`-2012`→`-2041`）去推断**会话**状态。
> **实测同一份 cookie**：
> | 接口 | 结果 |
> |---|---|
> | `GET /web/shelf/sync` | **HTTP 200 / 411 本书 / 无 errCode** ⇒ **会话有效** |
> | `GET /web/mp/articles?…` | `-2012`（15:41）→ `-2041`（16:15） |
>
> ⇒ 会话**没有问题**；那些码只出现在 **mp 接口**上 ⇒ 属**接口层**（风控 / 该接口已废弃）。
> 且该 cookie 是 **10-14 10:28 存的**，**2 天后**仍能通过 shelf/sync
> ⇒ "每天至少要扫两次" **不成立**。
>
> **★ 铁律**：判"会话有没有事"必须用一个**不受目标接口风控影响**的接口；
> **绝不能用目标接口的错误码去推断会话状态**（这是本轮最大的坑）。
>
> **★ 已验证的替代链路（推荐）**：
> ```
> 每号 1 个请求：GET /web/mp/cover?bookId=MP_WXS_xxx   → title + reviewId（该号最新一篇）
> 每篇 1 个请求：GET /web/mp/content?reviewId=xxx       → 渲染好的文章 HTML（正文可达）
> ```
> 实测样本：`混沌天成研究` → `【宏观早评】海外转涨，但股指仍需谨慎`（正文含 2026年10月16日）。
> **请求量 127 → 6 + 新文数**（原设计对每号翻页整表）。
> ⚠ 失效假设：`reviewId` 的 token **不再**等于原文直链 —— 拼
> `mp.weixin.qq.com/s/{token}` 返 **「参数错误」**（新 id 是不透明的）；
> 正文必须走 `/web/mp/content`。
> ⚠ 局限：`cover` 只给**最新一篇** ⇒ 两次运行之间发多篇会漏 ⇒ 提高频率（reviewId 去重幂等）
> 或评估把这 6 个号接进**免登录的合 集通道**。

"""

# ── ① MEMORY.md：插在补21 标题之后（最显眼处）────────────────────
raw = MEM.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
t = raw.decode("utf-8")
A1_n = "## 四·补21、★★ 别把「借来的会话凭据」挂进无人值守的定时任务（2026-10-16 实测）"
A1 = A1_n + eol
assert t.count(A1) == 1, ("mem anchor", t.count(A1))
assert "上述\"寿命/频率\"结论已被推翻" not in t, "已更正过"
t = t.replace(A1, A1 + CORRECTION.replace("\n", eol), 1)
t = t.replace("\r\n", "\n").replace("\n", eol)
MEM.write_bytes(t.encode("utf-8"))
print("  ① MEMORY.md 已插入更正（补21 标题后）")

# ── ② skill：插在 W-7e 标题之后 ────────────────────────────────
raw2 = SK.read_bytes()
eol2 = "\r\n" if raw2.count(b"\r\n") > 0 else "\n"
s2 = raw2.decode("utf-8")
A2 = "#### ⚠️ W-7e. **借来的会话凭据**撑不过" + chr(34) + "两次运行之间的静默期" + chr(34) + "（2026-10-16 实测）"
assert s2.count(A2) == 1, ("skill anchor", s2.count(A2))
assert "本节\"寿命/频率\"结论已被推翻" not in s2, "已更正过"
CORR2 = (A2 + "\n\n" + CORRECTION.strip() + "\n")
s2 = s2.replace(A2, CORR2, 1)
s2 = s2.replace("\r\n", "\n").replace("\n", eol2)
SK.write_bytes(s2.encode("utf-8"))
b2 = SK.read_bytes()
print("  ② skill 已插入更正；EOL: CRLF=%d bareLF=%d"
      % (b2.count(b"\r\n"), b2.count(b"\n") - b2.count(b"\r\n")))
