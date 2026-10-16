"""把 _SEC_IDX_VER 17 -> 18 并补版本说明。

为什么必须 +1: 已建索引的浏览器里 tab.dataset.secIndex === '17'，_buildSectionIndex 会直接
return ⇒ **本次「默认展开」修复对老用户完全不生效**（他们看到的仍是覆盖矩阵/四源状态折叠）。
这正是铁律 14 的实质：任何影响"索引构建结果/初始形态"的改动都要 bump 版本号。

⚠️ 两个文件都是 CRLF: 二进制读写 + bareLF 校验。
"""
import sys

FILES = [
    ("web_template.html", [
        ("  _SEC_IDX_VER: '17',\r\n",
         "  _SEC_IDX_VER: '18',\r\n"),
        ("  //   v17 (2026-09-30): 国君页 ④ 走势图 / ⑤ 明细数据 并入 ③ 指标总览(5 项 → 3 项)。\r\n",
         "  //   v17 (2026-09-30): 国君页 ④ 走势图 / ⑤ 明细数据 并入 ③ 指标总览(5 项 → 3 项)。\r\n"
         "  //   v18 (2026-09-30): 新增 _SEC_OPEN_BY_TAB 显式默认展开集合(国君页覆盖矩阵 + 四源状态),\r\n"
         "  //                     取代“只展开第一个 ≥200px 的块”的几何裁决 —— 该裁决在数据回填前\r\n"
         "  //                     量到的是空壳高度, 会让有内容的块默认收起(需重建索引才生效)。\r\n"),
    ]),
    ("tests/test_page_tools_variety_state.py", [
        ("    assert m.group(1) == '17', (\r\n"
         "        \"_SEC_IDX_VER=%s (期望 '17')。若你刚改了 tab 内卡片结构: 把它 +1 并同步本断言; \"\r\n",
         "    assert m.group(1) == '18', (\r\n"
         "        \"_SEC_IDX_VER=%s (期望 '18')。若你刚改了 tab 内卡片结构/初始展开形态: 把它 +1 \"\r\n"
         "        \"并同步本断言; \"\r\n"),
        ("    if not m or m.group(1) != '17':\r\n",
         "    if not m or m.group(1) != '18':\r\n"),
    ]),
]

for path, subs in FILES:
    raw = open(path, "rb").read()
    before = raw.count(b"\n") - raw.count(b"\r\n")
    errs = []
    for a, _ in subs:
        ab = a.encode("utf-8")
        n = raw.count(ab)
        if n != 1:
            errs.append(f"  {path}: 锚点命中 {n} 次 (期望 1): {a[:60]!r}")
    if errs:
        print("ABORT")
        print("\n".join(errs))
        sys.exit(2)
    for a, b in subs:
        raw = raw.replace(a.encode("utf-8"), b.encode("utf-8"), 1)
    after = raw.count(b"\n") - raw.count(b"\r\n")
    if after != 0:
        print(f"ABORT: {path} bareLF {before} -> {after}")
        sys.exit(2)
    open(path, "wb").write(raw)
    print(f"OK {path}: {len(subs)} 处, bareLF={after}")
