# -*- coding: utf-8 -*-
"""修正 [9] 段里一条自伤的断言。

原写法: `!/走势图|明细数据/.test(labels.slice(2).join(''))`
  ⇒ 合并卡的标签「③ 指标总览 / 走势图 / 明细」**本身就含**这两个词, 必然误报 FAIL。
正确判据: 不得存在**独立**的走势图/明细项 —— 即标签里没有以 ④/⑤ 开头的那两条,
  且索引项数恰为 3。
"""
P = r"D:\AgentSense\AgentSense\probe\_e2e_page_tools.mjs"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src

OLD = (
    "  log(!/走势图|明细数据/.test(st.labels.slice(2).join('')),\n"
    "      `索引不再出现「走势图」「明细数据」独立项: [${st.labels.join(' | ')}]`);\n"
)
NEW = (
    "  // ⚠️ 判据必须看「独立项」: 合并卡的标签「③ 指标总览 / 走势图 / 明细」本身含这两个词,\n"
    "  //   用 /走势图|明细/ 去测整串会命中自己(自伤断言, 实测假红一次)。\n"
    "  //   正确判据: ① 没有以 ④/⑤ 开头的独立项; ② 索引项数恰为 3。\n"
    "  const noStandalone = !st.labels.some(s => /^[④⑤]\\s*(走势图|明细数据)/.test(s));\n"
    "  log(noStandalone && st.labels.length === 3,\n"
    "      `无「④走势图」「⑤明细数据」独立项且共 3 项: [${st.labels.join(' | ')}]`);\n"
)
assert src.count(OLD) == 1, "待修断言未唯一命中: %d" % src.count(OLD)
src = src.replace(OLD, NEW, 1)

out = src.encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
