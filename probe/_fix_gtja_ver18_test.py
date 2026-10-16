"""同步 _SEC_IDX_VER 17->18 的测试断言。

⚠️ tests/test_page_tools_variety_state.py 是 **LF**（与 web_template.html 的 CRLF 不同），
   锚点必须用 \n。上一版用 \r\n 未命中 ⇒ 该文件未被写入（脚本先校验后写，属安全失败）。
"""
import sys

PATH = "tests/test_page_tools_variety_state.py"
raw = open(PATH, "rb").read()
crlf = raw.count(b"\r\n")
print(f"该文件 CRLF 行数 = {crlf} (期望 0 ⇒ LF 文件)")

subs = [
    ("    assert m.group(1) == '17', (\n"
     "        \"_SEC_IDX_VER=%s (期望 '17')。若你刚改了 tab 内卡片结构: 把它 +1 并同步本断言; \"\n",
     "    assert m.group(1) == '18', (\n"
     "        \"_SEC_IDX_VER=%s (期望 '18')。若你刚改了 tab 内卡片结构/初始展开形态: 把它 +1 \"\n"
     "        \"并同步本断言; \"\n"),
    ("    if not m or m.group(1) != '17':\n",
     "    if not m or m.group(1) != '18':\n"),
]

errs = []
for a, _ in subs:
    n = raw.count(a.encode("utf-8"))
    if n != 1:
        errs.append(f"锚点命中 {n} 次 (期望 1): {a[:70]!r}")
if errs:
    print("ABORT（未写入）")
    print("\n".join(errs))
    sys.exit(2)

for a, b in subs:
    raw = raw.replace(a.encode("utf-8"), b.encode("utf-8"), 1)
open(PATH, "wb").write(raw)
print(f"OK {PATH}: {len(subs)} 处; CRLF 仍为 {raw.count(chr(13).encode()+chr(10).encode())}")
