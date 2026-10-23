# -*- coding: utf-8 -*-
"""写记忆：① 平台变更取证结论；② 免登录取正文的突破。"""
from pathlib import Path

MEM = Path(r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory")

# ── ① 今日日志 ──────────────────────────────────────────────────
p = MEM / "2026-10-16.md"
s = p.read_text(encoding="utf-8")
ADD = """
### 20:05 追问"为什么前天可用、今天中断？微信读书这两天有更新吗" → 查到平台变更证据

**用户原话**：为什么前天还可用，今天链路就中断了？查一下微信读书在这两天有更新吗

#### 三条独立取证（回答"有没有更新"）

| 取证 | 方法 | 结论 |
|---|---|---|
| **官方 Agent 技能/API** | 带 `skill_version=1.0.5` 打 `/_list`，看有无 `upgrade_info`；另查官方仓库提交 | **无升级要求**。官方最新就是 **v1.0.5（2026-10-10 发布）**，早于我们建通道（10-14）⇒ 官方这条链路这两天没变 |
| **网页前端** | 拉首页 33 个静态资源，逐个读 `Last-Modified` | **最新部署 = 2026-10-09**（23/33 个在 10-09）⇒ **10-14~10-16 没有前端发版** |
| **★ mp（公众号）链** | 查用同一接口的第三方仓库提交历史 | **平台确实动了**：`steptian/weread-mp` 在 **2026-10-15** 提交 `fix: 微信读书换了 originalId 格式，read 改用 /web/mp/content 拿正文`；其 README 写「📌 2026-10 重要变更：originalId 从 22 字符短链 token 变成 **75 字符不透明 id**」 |

⇒ **结论**：官方 API 与前端都没变，但**公众号这条链在 10-14 前后被平台改过** ——
与我们的失败时间（10-14 傍晚起）**吻合**。所以不是"莫名其妙坏了"，而是**我们的实现是按"改动前"的样子写的**。

#### 又问：是"接口废弃"还是"会话/风控"？（并排除了会话）

- 同一份 cookie：`/web/shelf/sync` **正常**（411 本书 / 无 errCode）
- **今天 16:15 用户跑 bat，服务端真的重新下发了 `wr_skey`**（到期 **11-13 → 11-15**、值也换了）
- 用**刚刷新的**会话，mp 接口**照样失败** ⇒ 进一步排除"会话过期"，指向**接口层**

**★ 决定性判别**（一步即可）：重新扫码后再看 mp/articles 是否恢复
  · 恢复 ⇒ 是账号态/风控（社区 issue #62 的解法是"手机 App → 设置 → 登录设备 → 全部清理后重扫"）
  · 不恢复 ⇒ 接口确实已废弃

#### ★★★ 突破：正文**免登录**可达（照抄社区 09-23 的修复思路）

社区 `we-mp-rss` 有条 09-23 的修复：**「公众号链接 token 的 `~` 还原为 `_`」**。拿我们的 reviewId 一试：

| 拼法 | 结果 |
|---|---|
| `https://mp.weixin.qq.com/s/6sbTBEdZjNgzAAhHNw**~**-2A` | **「参数错误」** |
| `https://mp.weixin.qq.com/s/6sbTBEdZjNgzAAhHNw**_**-2A`（`~`→`_`） | **HTTP 200 / 3,543,277 字节 / 正文可达** |

⇒ **正文完全不需要 cookie**（与合集通道同一取法）！
于是新链路只需**每号 1 个 cover 请求**要 cookie，正文走公开页面。

**★ 注意 reviewId 的解析细节**（我第一次就截错了）：
`reviewId = "MP_WXS_3869147758_6sbTBEdZjNgzAAhHNw~-2A"`
而 `bookId = "MP_WXS_3869147758"` **本身就含下划线** ⇒ 取 token 要用
`rid.split(bookId + "_", 1)[1]`，**不能**用 `rid.split("_")[-1]`。

#### 新增探针

- `probe/_weread_update_forensics.py`（网关期望版本 + 前端资源部署时间）
- `probe/_weread_timeline.py`（第三方仓库提交时间线）
- 免登录正文验证：见本轮 `_weread_direct_url_probe.py` 的演进结论

"""
anchor = "### 19:10 ★★ 重大更正：不是\"会话失效\"，是 **mp 接口被废弃**（并找到可用替代）"
assert s.count(anchor) == 1, ("log anchor", s.count(anchor))
i = s.index(anchor)
s = s[:i] + ADD.lstrip("\n") + "\n" + s[i:]
p.write_bytes(s.encode("utf-8"))
print("  ✓ 日志已更新 %d 字符" % len(ADD))
