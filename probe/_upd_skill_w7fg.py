# -*- coding: utf-8 -*-
"""给 skill 补两条可复用判据：
   W-7f 「前天还可用、今天不行」→ 如何给"平台有没有更新"取证
   W-7g 用户内容型接口的两个细节（token 的 ~→_ / 解析 reviewId 别用 split('_')[-1]）
"""
from pathlib import Path

P = Path(r"C:/Users/lenovo/.workbuddy/skills/silent-pipeline-failure-triage/SKILL.md")
raw = P.read_bytes()
eol = "\r\n" if raw.count(b"\r\n") > 0 else "\n"
s = raw.decode("utf-8")

ANCHOR = "#### ⚠️ W-7e. **借来的会话凭据**撑不过"
assert s.count(ANCHOR) == 1, ("anchor", s.count(ANCHOR))
assert "W-7f." not in s, "已补过"

BLOCK = """#### ★★★ W-7f. 「前天还可用、今天不行」→ 先给**平台变更**取证（2026-10-16 实测）

用户问"是不是对方更新了"，**别靠感觉回答**。三条独立取证，成本都很低：

| 取证 | 怎么做 | 能证明什么 |
|---|---|---|
| **前端有没有发版** | 拉首页 → 抽**全部**静态资源 → 逐个 `HEAD` 读 `Last-Modified`/`ETag`，**看最大值落在哪天** | 发版必换 hash ⇒ 最近部署日**早于**故障日 ⇒ **前端没改**（本项目：最新 10-09、故障 10-14 ⇒ 排除前端） |
| **官方通道有没有变** | 有官方 API/技能时：带当前版本号打一次看有无 `upgrade_info`；再查官方仓库提交 | 官方**是否要求升级** / 最近发版日 |
| **★ 同类第三方项目何时被迫适配** | GitHub API 查**使用同一接口**的仓库提交历史（可按 `path`/`since` 过滤），并 `search/issues` 搜错误码 | ★ 最能定位"**服务端变更发生的大致时刻**"—— 别人踩到并提交修复的日期通常紧贴变更日 |

**本项目实例**：官方无升级要求、前端最后部署 10-09，
但第三方在 **10-15** 提交「fix: 微信读书换了 originalId 格式」⇒ **变更确在 10-14 前后**，
与故障时刻吻合 ⇒ 定性为"**平台改了，我们的实现是按改前写的**"，而不是"莫名坏了"。

> ★ 顺带一条：**"某接口已废弃"不能只看一篇文章**。本项目某文档 8 月就写"该接口已废弃（恒返 -2041）"，
> 而我们的通道 **10-14 还能用** ⇒ 同一接口在不同时间/账号/频率下表现不同，
> 结论必须写清"**在什么条件下**观测到的"。

#### ★★★ W-7g. 用户内容型接口的两个"细节决定成败"（本轮实测）

**① URL token 里的 `~` 要还原成 `_`**：
`reviewId` 形如 `MP_WXS_<数字>_<token>`，而 token 里的 `~` 在**原文链接**里对应 `_`：

```
mp.weixin.qq.com/s/…HNw~-2A   → 「参数错误」
mp.weixin.qq.com/s/…HNw_-2A   → HTTP 200 ✔ 正文可达
```

⇒ 拿不到正文时**先试这个字符替换**（社区同类项目踩过并修过）。

**② 解析 `reviewId` 别用 `split("_")[-1]`**：
`bookId` 自己就含下划线（`MP_WXS_3869147758`）⇒ 必须
`rid.split(bookId + "_", 1)[1]`，否则截出的 token 是错的（**我第一次就截错，白测两轮**）。

**③ 这个发现顺手改了成本结构**：正文既然**免登录**可取（公开页面），
那只有"列清单/拿最新"这一步要 cookie ⇒ 需 cookie 的请求从"整表翻页"降到"**每号 1 次**"。

#### ⚠️ W-7e. **借来的会话凭据**撑不过"""

s = s.replace(ANCHOR, BLOCK, 1)
s = s.replace("\r\n", "\n").replace("\n", eol)
P.write_bytes(s.encode("utf-8"))
b = P.read_bytes()
print("  ✓ skill 已加 W-7f / W-7g；EOL: CRLF=%d bareLF=%d；总行数 %d"
      % (b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n"), s.count(eol) + 1))
