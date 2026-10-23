# -*- coding: utf-8 -*-
"""修：带序号（① / 1.）的多家被切成 4 段 —— 序号被单独切出来（2026-10-19）。

【现象】「① 华泰 看多(72%)—…<br>② 国泰君安 中性(55%)—…」期望 2 段，实际 **4 段**：
        ['①', '华泰 看多…', '②', '国泰君安 中性…']
【真因】切分锚点是「机构名 + 方向」，lookahead 在「华泰」前切一刀 ⇒ 序号「① 」被独立成段。
【修法】切分后做一次**前缀归并**：只由序号/数字/标点/空白组成的段，并入**下一段**开头。
        （不在正则里硬解，是因为前缀形态多：① 、(1)、- 、· …… 归并更稳）
"""
from pathlib import Path

P = Path("web_template.html")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "EOL 非纯 CRLF"
t = raw.decode("utf-8").replace("\r\n", "\n")

OLD = """parts = parts.flatMap(x => x.split(SPLITTER).map(s => s.trim()).filter(Boolean));

// ③ 数**真正的家数**：段首是「机构名 + 方向」"""

NEW = """parts = parts.flatMap(x => x.split(SPLITTER).map(s => s.trim()).filter(Boolean));

// ②b 【前缀归并 2026-10-19】「① 华泰 看多」会在「华泰」前切一刀，把序号单独切出来
//   ⇒ 只由序号/数字/标点/空白组成的段，并入**下一段**开头（不在正则里硬解，前缀形态太多）。
const prefixOnly = /^[\\s\u2460-\u2473 0-9.、\\-·()（）]*$/;
const merged = [];
let carry = '';
for (const seg of parts) {
if (prefixOnly.test(seg)) { carry += seg + ' '; continue; }
merged.push((carry + seg).trim());
carry = '';
}
if (carry.trim()) merged.push(carry.trim());
parts = merged.filter(Boolean);

// ③ 数**真正的家数**：段首是「机构名 + 方向」"""

assert t.count(OLD) == 1, t.count(OLD)
t = t.replace(OLD, NEW, 1)
out = t.replace("\n", "\r\n").encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "出口 EOL 破坏"
P.write_bytes(out)
print("  ✔ 已加前缀归并（序号并入下一段）")
