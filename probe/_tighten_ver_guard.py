# -*- coding: utf-8 -*-
"""把索引版本断言收紧 + 补一条「僵尸索引项」回归测试。

【为什么】上一轮只断言 `_SEC_IDX_VER >= 16`, 这轮的 ">= 16" 在任何后续版本都**恒真**
  ⇒ 结构性增删时忘了 +1, 护栏抓不住, 用户浏览器里就是"删掉的旧索引项还在"。
  按项目既有的"断言必须能变红"纪律, 改成精确值断言。
"""
P = r"D:\AgentSense\AgentSense\tests\test_page_tools_variety_state.py"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src

# ── 1) 收紧已有的版本断言 ──────────────────────────────────────
OLD = '''def test_sec_index_version_bumped_for_matrix_move():
    """结构变了必须 +1 版本号, 否则旧索引不重建, 用户看到的还是旧布局。"""
    src = _src()
    m = re.search(r"_SEC_IDX_VER:\\s*'(\\d+)'", src)
    assert m, "未找到 _SEC_IDX_VER"
    assert int(m.group(1)) >= 16, (
        "_SEC_IDX_VER=%s 未随覆盖矩阵移出而 +1 —— "
        "已建过索引的页面不会重建, 覆盖矩阵不会出现在小模块索引里" % m.group(1)
    )
'''
NEW = '''def test_sec_index_version_bumped_for_matrix_move():
    """结构变了必须 +1 版本号, 否则旧索引不重建, 用户看到的还是旧布局。

    ⚠️ 这里断言**精确值**而不是 >= N —— >= 是"单调不回头"的写法,
       任何后续版本都恒真, 结构性增删时忘了 +1 就抓不住(实测过的假绿形态)。
       每次改 tab 内卡片结构, 请同步 +1 并更新本断言的期望值。
    """
    src = _src()
    m = re.search(r"_SEC_IDX_VER:\\s*'(\\d+)'", src)
    assert m, "未找到 _SEC_IDX_VER"
    assert m.group(1) == '17', (
        "_SEC_IDX_VER=%s (期望 '17')。若你刚改了 tab 内卡片结构: 把它 +1 并同步本断言; "
        "若不慎回退: 用户浏览器里会残留已删除的旧索引项(僵尸项)" % m.group(1)
    )


def test_no_zombie_index_labels_for_merged_cards():
    """★ 索引里不得出现指向已删除卡片的僵尸项（④走势图/⑤明细数据）。"""
    src = _src()
    for t in ('<h3>④ 走势图', '<h3>⑤ 明细数据'):
        assert t not in src, (
            "%s 仍存在 ⇒ 若索引未重建, 会出现点了没反应的僵尸索引项" % t
        )
    assert GTJA_MERGED_TITLE in src, "合并卡标题缺失"
'''
assert src.count(OLD) == 1, "旧版本断言未唯一命中: %d" % src.count(OLD)
src = src.replace(OLD, NEW, 1)

# ── 2) _matrix_assertions_ok 里的版本判据从 >=16 改成 ==17 ─────
OLD2 = """    m = re.search(r"_SEC_IDX_VER:\\s*'(\\d+)'", src)
    if not m or int(m.group(1)) < 16:
        return False
    i = src.find('<h3>覆盖矩阵')"""
NEW2 = """    m = re.search(r"_SEC_IDX_VER:\\s*'(\\d+)'", src)
    if not m or m.group(1) != '17':
        return False
    i = src.find('<h3>覆盖矩阵')"""
assert src.count(OLD2) == 1, "版本判据未唯一命中: %d" % src.count(OLD2)
src = src.replace(OLD2, NEW2, 1)

out = src.encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
