# -*- coding: utf-8 -*-
"""删掉我误加的重复 `_div_span` 定义，改为复用文件原有同名函数。

背景：原有 `_div_span(src, start)` 返回 `(段文本, 结束下标)`；我在末尾又定义了一个
返回 `(start, end)` 的同名函数 ⇒ **覆盖**了原函数，导致 `_tools_segment` 等 14 个
既有测试报 `TypeError: argument of type 'int' is not iterable`。
⚠️ 本文件是 LF；必须二进制读写。
"""
P = r"D:\AgentSense\AgentSense\tests\test_page_tools_variety_state.py"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src

# ── 1) 删掉我加的重复定义 ───────────────────────────────────────
dup = '''def _div_span(src, start):
    """从 start（指向 '<div'）做 div 深度配对，返回 (start, end)。

    ⚠️ 与 `_gtja_tools_span` 同源：不能用"找下一个 </div>"，内外都有大量 </div>。
    """
    assert src.startswith("<div", start), "起点不是 <div"
    depth = 0
    j = start
    while j < len(src):
        nxt_open = src.find("<div", j)
        nxt_close = src.find("</div>", j)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + 4
        else:
            depth -= 1
            j = nxt_close + 6
            if depth == 0:
                return (start, j)
    return None


'''
assert src.count(dup) == 1, "重复定义段未唯一命中: %d" % src.count(dup)
src = src.replace(dup, "", 1)

# ── 2) test_matrix_guard_kills_move_back 改用原有 _div_span ────
old = '''    ms = src.find(MATRIX_CARD_OPEN)
    assert ms != -1, "找不到覆盖矩阵卡起点（结构变了？）"
    mspan = _div_span(src, ms)
    assert mspan, "覆盖矩阵卡 div 未配对成功"
    block = src[mspan[0]:mspan[1]]

    # ① 原地删掉
    rest = src[:mspan[0]] + src[mspan[1]:]
    assert rest != src
    # ② 粘到控制条闭合 div 之前（span 在区间之前，删后面的块不影响它）
    span2 = _gtja_tools_span(rest)
    assert span2 == span, "删掉后面的块不该改变控制条区间"
    close_pos = span2[1] - len("</div>")
    mutated = rest[:close_pos] + block + "\\n  " + rest[close_pos:]
    assert mutated != src, "变异未生效"
'''
new = '''    ms = src.find(MATRIX_CARD_OPEN)
    assert ms != -1, "找不到覆盖矩阵卡起点（结构变了？）"
    block, m_end = _div_span(src, ms)   # 复用文件原有 _div_span: (段文本, 结束下标)
    assert block.startswith("<div") and block.endswith("</div>"), "矩阵卡配对异常"

    # ① 原地删掉
    rest = src[:ms] + src[m_end:]
    assert rest != src
    # ② 粘到控制条闭合 div 之前（控制条区间在矩阵卡之前，删后面的块不影响它）
    span2 = _gtja_tools_span(rest)
    assert span2 == span, "删掉后面的块不该改变控制条区间"
    close_pos = span2[1] - len("</div>")
    mutated = rest[:close_pos] + block + "\\n  " + rest[close_pos:]
    assert mutated != src, "变异未生效"
'''
assert src.count(old) == 1, "move_back 变异主体未唯一命中: %d" % src.count(old)
src = src.replace(old, new, 1)

out = src.encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
