# -*- coding: utf-8 -*-
"""在 MEMORY.md 的「前端交互：页级控制条」段落追加两条铁律。
⚠️ 该文件是 CRLF（bareLF=0）—— 新增内容也必须用 \\r\\n。
"""
P = r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/MEMORY.md"

raw = open(P, "rb").read()
txt = raw.decode("utf-8")
assert raw.count(b"\n") - raw.count(b"\r\n") == 0, "该文件应为纯 CRLF"

ANCHOR = ("- ⚠️ `_enclosing_body` 类方法定位正则须**允许 `async` 前缀**"
          "（`async runMultiCompare() {`）。\r\n")
assert txt.count(ANCHOR) == 1, "锚点不唯一: %d" % txt.count(ANCHOR)

ADD = (
    "- ⚠️⚠️ **`.page-tools` 只许放「一张矮控制卡」**（09-30 用户报障：矩阵“不在小模块中”+“遮挡其他小模块”）。\r\n"
    "  两个后果同时发生且都静默：①`sticky`+实色底 ⇒ 大表格被钉顶部**盖住下方模块**；\r\n"
    "  ②`_secScan` 对 `.page-tools` **整块跳过** ⇒ 里面内容**进不了小模块索引、收不起来**。\r\n"
    "  判据：每页 `.page-tools` 内 `:scope > .card` **恰好 1 个**；大卡片一律放 tab 顶层。改结构记得 `_SEC_IDX_VER` +1。\r\n"
    "  ⚠️ 结构类改动**不能只对源码文本断言**（本轮首版只改注释、静态断言全绿，是 E2E 抓到 5 个 FAIL）；\r\n"
    "  配对的闭合 div 必须**按 div 深度配平**；验收须含「滚动后卡片 top 明显上移（没被钉住）」。\r\n"
    "- ⚠️ **接口字段类型契约**：`reports` 是**标量**(篇数)、`report_rows` 是**数组**(逐份行)，二者**禁互赋**。\r\n"
    "  曾写 `v.report_rows = d.reports || 0` ⇒ `reps.reduce` 抛 TypeError ⇒ **整个网格渲染中断**(非局部错)。\r\n"
    "  通则：把接口值赋给前端状态字段前，先确认两边**容器类型**一致；消费端一律 `|| []` 兜底。\r\n"
)

out = (txt.replace(ANCHOR, ANCHOR + ADD, 1)).encode("utf-8")
assert out.count(b"\n") - out.count(b"\r\n") == 0, "落盘引入了裸 LF，拒绝写入"
open(P, "wb").write(out)
print("OK %d -> %d bytes" % (len(raw), len(out)))
