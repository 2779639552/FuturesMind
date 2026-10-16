"""对齐 _SEC_IDX_VER 的并发变更：
  · '18'（我写的）已被另一处工作改为 '19'（量化页新增"数据计算面板" q-cmp-*）。
  · 版本号现由两边共有：我的 E2E 不再写死具体值，只断言"≥18"(即已 bump、老索引会重建)；
    测试断言对齐到当前真值 '19'（该文件刻意用精确值，保持其强度不变）。

⚠️ 两个文件都是 **LF**。
"""
import sys

EDITS = [
    ("probe/_e2e_gtja_default_open.mjs", [
        ("log(s0.ver === '18', `索引版本 = ${s0.ver} (期望 '18' —— 否则老浏览器不重建索引, 修复不生效)`);",
         "// 版本号可能与别处改动共享, 故只断言\"已 bump 到 18+\"(= 老索引会被重建)。\n"
         "log(Number(s0.ver) >= 18, `索引版本 = ${s0.ver} (期望 >=18 —— 否则老浏览器不重建索引, 修复不生效)`);"),
    ]),
    ("tests/test_page_tools_variety_state.py", [
        ("    assert m.group(1) == '18', (\n"
         "        \"_SEC_IDX_VER=%s (期望 '18')。若你刚改了 tab 内卡片结构/初始展开形态: 把它 +1 \"\n",
         "    assert m.group(1) == '19', (\n"
         "        \"_SEC_IDX_VER=%s (期望 '19')。若你刚改了 tab 内卡片结构/初始展开形态: 把它 +1 \"\n"),
        ("    if not m or m.group(1) != '18':\n",
         "    if not m or m.group(1) != '19':\n"),
    ]),
]

for path, subs in EDITS:
    raw = open(path, "rb").read()
    print(f"{path}: CRLF={raw.count(chr(13).encode()+chr(10).encode())}")
    errs = [f"  锚点命中 {raw.count(a.encode())} 次: {a[:50]!r}" for a, _ in subs
            if raw.count(a.encode()) != 1]
    if errs:
        print("ABORT"); print("\n".join(errs)); sys.exit(2)
    for a, b in subs:
        raw = raw.replace(a.encode("utf-8"), b.encode("utf-8"), 1)
    open(path, "wb").write(raw)
    print(f"  OK {len(subs)} 处")
