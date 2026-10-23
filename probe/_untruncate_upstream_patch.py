# -*- coding: utf-8 -*-
"""放开观点/头寸的**上游**软上限（2026-10-16 用户报障「观点被截断」）。

【实测数据（真实链路，date=2026-10-11 周报 / LU）】
  原文该品种段 398~418 字；`_extract_key_opinion` 产出：
      max_len=150    → 124 / 145 / **80** 字     ← 调用处当前传的值（用户在看的）
      max_len=360    → **306 / 312 / 277** 字     ← 函数默认值
      max_len=100000 → 306 / 312 / 277 字         ← 与 360 相同 ⇒ **360 即"不裁"**
  ⇒ **调用处把函数默认的 360 收紧成了 150**，砍掉一半多。这就是"部分观点被截断"的根因之一。

【★ 还有一处口径不一致】「逐品种观点总览」走 `_research_view_row` → `_extract_key_opinion()`
  用的是**默认 360**；只有总表这一条链路传了 150 ⇒ 同一份研报在两个页签里长度不同。

【本补丁改三处】
  ① `_collect_daily_report_items`: max_len 150 → 360（回到函数默认 = 不额外收紧）
  ② 同处 `_merge_same_source_items(text_cap=200, trade_cap=120)`
     ⇒ 放宽到 360 / 240：否则 max_len 刚放宽、合并层又把 306 字的 text 截回 200。
     （cap 是"软上限、按句读边界截断"，0=不限；这里给足，显示长度交给前端收敛。）
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "web_app.py 应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① 观点提取：150 → 360 ───────────────────────────────────
sub('''    opinion = _extract_key_opinion(seg, max_len=150)  # 单条短摘:一天全品种条目多,预算留给覆盖面''',
    '''    # 【2026-10-16 用户报障「观点被截断」】此处原为 max_len=150（把函数默认的 360 收紧了一半多）。
    # 实测（周报 LU，原文段落 398~418 字）：
    #     max_len=150 → 产出 124/145/80 字；max_len=360 → 306/312/277 字；
    #     max_len=100000 → 同样 306/312/277 ⇒ **360 就已经是"不裁"**（再无内容可裁）。
    # ⇒ 150 是纯粹的人为收紧（注释写的"预算留给覆盖面"），代价是观点被砍掉一半。
    # ★ 顺带消除一处**口径不一致**：另有一条链路(`_research_view_row` → 逐品种观点总览)
    #   用的是默认 360 ⇒ 同一份研报在两个页签里长度不同，用户会以为哪边显示错了。
    # 显示长度改由前端收敛承担（见 web_template.html 的 _decorateViewTable：默认 3 行 + 展开全文）。
    opinion = _extract_key_opinion(seg, max_len=360)''',
    "① 观点提取 max_len 150 → 360")

# ── ② 合并层 cap 同步放宽 ──────────────────────────────────
sub('''    for rec in _merge_same_source_items(recs, flatten=True, text_cap=200, trade_cap=120):''',
    '''    # 【2026-10-16】cap 是"合并后文本的软上限(按句读边界截)" —— 它与 ① 是**串联**的：
    #   ① 放宽到 360 后，若这里还是 200，单篇 306 字的 text 会被立刻截回 200（白改）。
    #   ⇒ 同步放宽到 360 / 240；"表格会不会太长"交给前端收敛，不再靠丢数据来控制。
    for rec in _merge_same_source_items(recs, flatten=True, text_cap=360, trade_cap=240):''',
    "② 合并层 text_cap 200→360 / trade_cap 120→240")

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)
b = P.read_bytes()
print("=" * 74)
print("  web_app.py 落盘完成")
print("=" * 74)
print("\n".join(report))
print()
print("  %d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
