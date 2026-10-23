# -*- coding: utf-8 -*-
"""补两个漏网之鱼 + 修护栏自指。

① 【真漏改】`_prev_variety_view` 的 `_extract_key_opinion(seg, max_len=150)`
   —— 它是「观点延续」文案的来源（`_build_daily_views_table` 的 45/60 字截断已删，
   但源头仍被砍到 150 ⇒ 延续文案依旧短）。与 `_collect_daily_report_items` 是**同一处收紧**。
② 【护栏自指】`assert pat not in s`（整文件匹配）会被**说明注释**命中：
   生产代码与测试文件里都会引用"曾经错误的写法"（如注释写"此处原先 `view[:60] + …`"）
   ⇒ 断言恒红。改为只查**非注释行**。

⚠ 本补丁所有替换串都经 `.replace("\\n", "\\r\\n")` 归一 —— 上一版直接用含 LF 的字符串
   插入 CRLF 文件，被 EOL 断言拦住（好在断言在写盘之前，未造成损伤）。
"""
from pathlib import Path

P = Path("web_app.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") > 0 and raw.count(b"\n") == raw.count(b"\r\n"), "应为纯 CRLF"
t = raw.decode("utf-8")
report: list[str] = []


def sub_text(old: str, new: str, tag: str, n: int = 1):
    global t
    o = old.replace("\n", "\r\n")
    assert t.count(o) == n, ("★锚点未命中", tag, t.count(o))
    t = t.replace(o, new.replace("\n", "\r\n"), n)
    report.append("  ✔ %s" % tag)


sub_text(
    "    opinion = _extract_key_opinion(seg, max_len=150)",
    '''    # 【2026-10-16 用户报障「观点被截断」】此处原为 max_len=150 —— 与
    #   `_collect_daily_report_items` 是**同一处收紧**（当时两处都写 150），
    #   而本函数是「观点延续」文案的来源：源头被砍到 150，下游就算放开也只能拿到短文本。
    #   ⇒ 一并回到函数默认 360。「取第一句」的语义切分保留（那不是字数截断）。
    opinion = _extract_key_opinion(seg, max_len=360)''',
    "① _prev_variety_view（观点延续来源）150 → 360")

out = t.encode("utf-8")
assert out.count(b"\n") == out.count(b"\r\n"), "写回破坏了 CRLF"
P.write_bytes(out)

# ── ② 护栏：只查非注释行 ─────────────────────────────────────
Q = Path("tests/test_views_no_truncation.py")
s = Q.read_text(encoding="utf-8")
OLD1 = """    s = _app()
    bad = [
        'presentation_view = None',  # 占位（保持列表结构）
    ]
    del bad
    for pat, why in ("""
NEW1 = """    s = _app()
    # ⚠ 必须**排除注释行**：生产代码与测试里都会引用"曾经错误的写法"
    #   （如注释写 `此处原先 view[:60] + "…"`）⇒ 整文件 `in s` 匹配会**恒红**（自指陷阱，
    #   本轮踩了一次：判据把"我自己的说明文字"当成了违规代码）。只看**可执行代码行**。
    code_s = "\\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("#"))
    for pat, why in ("""
assert s.count(OLD1) == 1, ("护栏锚点 1", s.count(OLD1))
s = s.replace(OLD1, NEW1, 1)

OLD2 = '        assert pat not in s, "★ %s（应已放开）" % why'
NEW2 = '        assert pat not in code_s, "★ %s（应已放开）" % why'
assert s.count(OLD2) == 1, ("护栏锚点 2", s.count(OLD2))
s = s.replace(OLD2, NEW2, 1)
Q.write_text(s, encoding="utf-8", newline="")

b = P.read_bytes()
print("=" * 74)
print("  落盘完成")
print("=" * 74)
print("\n".join(report))
print("\n  web_app.py: %d → %d 字节；CRLF=%d bareLF=%d"
      % (len(raw), len(b), b.count(b"\r\n"), b.count(b"\n") - b.count(b"\r\n")))
print("  ✔ 护栏改为只查非注释行")
