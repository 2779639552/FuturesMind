# -*- coding: utf-8 -*-
"""把内嵌的 `_fit_payload` 抽成**模块级纯函数** `_fit_prompt_payload`。

【为什么】内嵌闭包**无法从测试注入**（要调它就得跑整个总结流程 = 要调 LLM）
  ⇒ 护栏咬不住"预算装载"这个关键行为。抽成模块级后可直接单测。
  （本项目已有同型纪律：判据相关的逻辑要能被单测，别藏在闭包里。）
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


# ── ① 在 _collect_daily_report_items 之前插入模块级纯函数 ─────
ANCHOR = "def _collect_daily_report_items(rows: list[dict], date: str, rtype: str = \"\") -> list[dict]:"
FUNC = '''def _fit_prompt_payload(blocks: list[str], budget: int = 28000) -> str:
    """把材料块按**预算**装载进 prompt；装不下的**显式标注**（不静默丢弃）。

    【为什么需要】本项目的总结 prompt 侧原先写作 `payload[:28000]` —— **从尾部硬切**：
      ① 会把某条材料切成半句（LLM 看到残缺要点）；
      ② 更糟的是**不告知**，LLM 会以为后面的品种"今天没研报" ⇒ 可能漏写甚至编造。
    【为什么"跳过"而不是"遇到装不下的就停"】后者会被一个长条目挡住它后面**所有**的短条目，
      白丢很多本可放进去的材料。
    【返回】装载结果；若略过任何条目，末尾附一行注明被略过的**品种/来源**。
    """
    keep: list[str] = []
    used = 0
    dropped: list[str] = []
    for blk in blocks:
        if used + len(blk) + 2 > budget:
            head = blk.split("】", 1)[0].lstrip("【").strip()
            dropped.append(head or blk[:12])
            continue
        keep.append(blk)
        used += len(blk) + 2
    out = "\\n\\n".join(keep)
    if dropped:
        out += ("\\n\\n（注：以下品种的材料因超出长度预算未列入，请勿据此下结论：%s）"
                % "、".join(sorted(set(dropped))))
    return out


''' + ANCHOR
sub(ANCHOR, FUNC, "① 插入模块级 _fit_prompt_payload")

# ── ② 调用点改为调用模块级函数（删掉内嵌定义）─────────────────
OLD_CALL = '''    #   ⚠ 用"跳过"而不是"遇到装不下的就停"：后者会被一个长条目挡住后面所有短条目。
    _BUDGET = 28000

    def _fit_payload(blocks: list[str]) -> str:
        """按预算装载材料；被略过的**在末尾显式标注**（别让 LLM 以为那些品种没研报）。"""
        keep: list[str] = []
        used = 0
        dropped: list[str] = []
        for blk in blocks:
            if used + len(blk) + 2 > _BUDGET:
                head = blk.split("】", 1)[0].lstrip("【").strip()
                dropped.append(head or blk[:12])
                continue
            keep.append(blk)
            used += len(blk) + 2
        out = "\\n\\n".join(keep)
        if dropped:
            out += ("\\n\\n（注：以下品种的材料因超出长度预算未列入，请勿据此下结论：%s）"
                    % "、".join(sorted(set(dropped))))
        return out

    _payload_txt = _fit_payload(payload.split("\\n\\n"))'''
NEW_CALL = '''    #   ⚠ 用"跳过"而不是"遇到装不下的就停"：后者会被一个长条目挡住后面所有短条目。
    #   ★ 装载逻辑抽成**模块级** `_fit_prompt_payload`：内嵌闭包无法从测试注入，
    #     而"预算装载"正是最需要护栏咬住的行为（本项目纪律：判据逻辑要能单测）。
    _payload_txt = _fit_prompt_payload(payload.split("\\n\\n"))'''
sub(OLD_CALL, NEW_CALL, "② 调用点改为模块级函数")

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
