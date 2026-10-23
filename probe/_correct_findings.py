# -*- coding: utf-8 -*-
"""① 给体检脚本补 -2041/-2042 释义；② 写今日日志（含**更正**）。

★ 本轮必须更正的事实：
  之前我基于 `/web/mp/articles` 的 `-2012` 推出"会话寿命 < 8h ⇒ 必须每天扫码"。
  实测**同一份 cookie** 打 `/web/shelf/sync`：HTTP 200 / 411 本书 / **无 errCode**
  ⇒ **会话是有效的**。那些错误码只出现在 **mp 接口**上
  （先 -2012、后 -2041）⇒ 属**接口层**问题（风控 / 该接口已废弃）。
  并且 cookie 是 **10-14 10:28 存的，到今天 10-16 16:20 已 2 天**，仍能通过 shelf/sync。
"""
from pathlib import Path

report = []

# ── ① 体检脚本补错误码 ─────────────────────────────────────────
P = Path("probe/_weread_session_check.py")
s = P.read_text(encoding="utf-8")
OLD = """    -2010: ("cookie 无效 / 身份不被识别（缺 wr_skey 或写错）",
            "→ 检查 cookies.json 是否含 wr_skey；必要时重跑登录脚本"),
}"""
assert s.count(OLD) == 1, ("codes", s.count(OLD))
NEW = """    -2010: ("cookie 无效 / 身份不被识别（缺 wr_skey 或写错）",
            "→ 检查 cookies.json 是否含 wr_skey；必要时重跑登录脚本"),
    -2041: ("认证 / 风控错误（可能弹验证码；也可能是该接口对新版已废弃）",
            "→ 先用 /web/shelf/sync 判「会话有没有事」（本脚本 ①b）；
               若会话正常，则这是**接口层**问题 ⇒ 改用 /web/mp/cover + /web/mp/content"),
    -2042: ("该文章未收录", "→ 跳过该篇即可"),
}"""
s = s.replace(OLD, NEW, 1)
P.write_text(s, encoding="utf-8", newline="")
report.append("  ✓ ① 体检脚本已补 -2041 / -2042 释义")

# ── ② 今日日志 ─────────────────────────────────────────────────
p = Path(r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/2026-10-16.md")
t = p.read_text(encoding="utf-8")
ADD = """
### 19:10 ★★ 重大更正：不是"会话失效"，是 **mp 接口被废弃**（并找到可用替代）

**起因**：用户跑了一键重登 bat，脚本报「已处于登录态，跳过扫码」、随后验证返 **-2041**（原为 -2012）。

#### ★ 更正我前面的结论（有错必改）

我之前据 `/web/mp/articles` 的 `-2012` 推出「会话寿命 < 8h ⇒ **每天至少要扫两次**」。**这个结论是错的。**

实测**同一份 cookie**：

| 接口 | 结果 |
|---|---|
| `GET /web/shelf/sync` | **HTTP 200 / 411 本书 / 无 errCode** ⇒ **登录态有效** |
| `GET /web/mp/articles?…` | `-2012`（15:41）→ `-2041`（16:15） |

⇒ **会话没问题**；错误码只出现在 **mp 接口**上 ⇒ 属**接口层**问题（风控 / 该接口已废弃）。
而且该 cookie 是 **10-14 10:28 保存的**，到 **10-16 16:20 已 2 天**仍能通过 shelf/sync
⇒ "每天要扫两次"的说法**不成立**。

**★ 教训（已写进 skill）**：判"会话有没有事"必须用一个**不受目标接口风控影响**的接口；
**绝不能拿目标接口的错误码去推断会话状态** —— 那正是本轮踩的坑。

#### ★★ 找到并**全链路验证**了替代接口

| 步骤 | 接口 | 实测 |
|---|---|---|
| 取最新一篇 | `GET /web/mp/cover?bookId=MP_WXS_xxx` | ✅ 返回 `title` + `reviewId` + 头像/封面 |
| 取正文 | `GET /web/mp/content?reviewId=xxx` | ✅ **返回渲染好的文章 HTML**（3.5MB，去标签纯文本 3443 字，含标题/日期/正文） |

实测样本：`混沌天成研究` → 最新一篇 `【宏观早评】海外转涨，但股指仍需谨慎`
（正文里含日期 **2026年10月16日**，即**今天的新文，一直在等我们去取**）。

**⚠ 一个失效的假设**：`reviewId` 的 token 段**不再**等于原文直链 ——
直接拼 `https://mp.weixin.qq.com/s/{token}` 返回 **「参数错误」**
（外部资料一致：`originalId` 已从 22 字符短链 token 变成 75 字符**不透明 id**，
"不再自己拼 URL，改走 `/web/mp/content` 换真实链接"）。

#### 请求量：**127 → 6 + 新文数**

原设计对每号翻页整表（6 号 × ~21 页 ≈ **127 个请求**），风控暴露面大；
新设计 = **每号 1 个 cover 请求**（6 个）+ 每个新文 1 个 content 请求。
⇒ 小一个数量级，这本身也降低了再次触发风控的概率。

#### ⚠ 新方案的已知局限（必须说清）

`cover` **只给最新一篇** ⇒ 若两次运行之间该号发了多篇，**中间那些会被漏掉**。
- 缓解一：**提高运行频率**（reviewId 去重是幂等的，跑密不产生重复）；
- 缓解二：评估把这 6 个号**接进「合集通道」**（免登录、已稳定跑近一个月）——
  需先确认这些号有没有「合集」。若成立，则可彻底摆脱 cookie 与风控。

#### 顺带修掉的真实缺陷（用户实跑踩到）

`probe/_weread_login.mjs` 第 96 行判"已登录"的写法是
`(await ctx.cookies()).some(c => c.name === 'wr_skey')` —— **只看 cookie 名字在不在**，
不看有没有效。旧 cookie 又会被注入浏览器上下文 ⇒
**脚本永远报「已处于登录态，跳过扫码」⇒ 一键重登永远走不到扫码那一步**。

已修：
- 新增 `checkSession()`：拿 cookie 打 `/web/shelf/sync` **真验证**；
- 新增 `--force`：用**干净上下文**（不注入旧 cookie）+ 跳过校验，强制走扫码
  （注入旧 cookie 会让登录入口不出现、二维码弹不出来）；
- 桌面 bat 已改为带 `--force`；
- 提示 `savedAt` **只是"文件被写的时刻"**，脚本会假刷新它（本次就被刷成 16:15），别当"登录时刻"用。

#### 新增/更新的工具

- `probe/_weread_cover_probe.py`（1 请求测 cover）
- `probe/_weread_content_analyze.py`（1 请求测 content 并落盘分析，分析完删样本）
- `probe/_weread_direct_url_probe.py`（验证直链为何不可用）
- `probe/_weread_session_check.py` **已更正**：新增 ①b 用 shelf/sync 判会话，
  结论段分开报"会话"与"接口"，并补 -2041/-2042 释义
- `probe/_fix_weread_login.py`（本次登录脚本补丁）

"""
anchor = "### 18:35 评估方案 C（官方网关）：**结论不成立**（四角度实测）"
assert t.count(anchor) == 1, ("log anchor", t.count(anchor))
i = t.index(anchor)
t = t[:i] + ADD.lstrip("\n") + "\n" + t[i:]
p.write_bytes(t.encode("utf-8"))
report.append("  ✓ ② 今日日志已更新 %d 字符" % len(ADD))

print("\n".join(report))
