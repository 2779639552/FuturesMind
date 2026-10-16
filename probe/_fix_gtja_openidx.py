"""修 E2E 抓到的真缺陷: _resetSecFilter 末尾残留 `i === openIdx` 引用。

现象: 清空筛选时 JS 抛 "openIdx is not defined" ⇒ 该行的标签态同步被跳过,
      且整个 rows.forEach 回调在那一行之前**部分生效**、之后中断(块的开合已改完,
      只有标签 is-open 没同步)。E2E 抓到: ③ 指标总览清空后仍是 419/895px 展开。
教训: 只对源码文本断言会漏掉这类"变量改名漏改一处"的缺陷 —— 必须跑真实交互。

⚠️ web_template.html 是 CRLF。
"""
import sys

P = "web_template.html"
raw = open(P, "rb").read()
before = raw.count(b"\n") - raw.count(b"\r\n")

old = (
    "      if (r.btn) r.btn.classList.toggle('is-open', i === openIdx);\r\n"
    "    });\r\n"
    "    this._applySecFilter(idx, tabId);\r\n"
).encode("utf-8")
new = (
    "      if (r.btn) r.btn.classList.toggle('is-open', openRows.has(i));\r\n"
    "    });\r\n"
    "    this._applySecFilter(idx, tabId);\r\n"
).encode("utf-8")

n = raw.count(old)
print(f"锚点命中 {n} 次 (期望 1)")
if n != 1:
    print("ABORT")
    sys.exit(2)

raw = raw.replace(old, new, 1)
after = raw.count(b"\n") - raw.count(b"\r\n")
print(f"bareLF: {before} -> {after}")
if after != 0:
    print("ABORT: EOL 被破坏")
    sys.exit(2)
open(P, "wb").write(raw)

# 残留 openIdx 检查: 活代码里只应剩 2 处 fallback 分支内的局部变量
print("OK 已修")
