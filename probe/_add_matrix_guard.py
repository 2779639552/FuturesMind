# -*- coding: utf-8 -*-
"""给「覆盖矩阵必须在 .page-tools 外」补静态护栏（追加到 test_page_tools_variety_state.py）。"""
import io

P = 'tests/test_page_tools_variety_state.py'
raw = io.open(P, 'rb').read()
orig_len = len(raw)
assert raw.count(b'\r\n') == 0, 'expect LF file'

SECTION = r'''
# ══════════════════════════════════════════════════════════════
# 覆盖矩阵：必须在小模块里 + 不得吸顶（2026-09-30 用户报障）
# ══════════════════════════════════════════════════════════════
# 【症状】国君数据页「覆盖矩阵」不在小模块中, 且遮挡其他小模块内容。
# 【根因】该卡片被放在 <div id="tab-gtja-tools" class="page-tools"> 内部：
#   ① `.page-tools` 是 position:sticky + 实色底, 只适合装矮控制条；塞进 21 行大表格后
#      整块被钉在顶部, 实色底**盖住下方模块**；
#   ② `_secScan` 对 `.page-tools` 是**整块跳过**(控制条永不参与折叠) ⇒ 它进不了
#      小模块索引、也收不起来。
# 【修法】把覆盖矩阵卡移出 .page-tools, 落成 tab 顶层普通 .card。

GTJA_TOOLS_OPEN = '<div id="tab-gtja-tools" class="page-tools">'


def _gtja_tools_span(src):
    """返回 (起, 止) —— 用 div 深度配对找出 .page-tools 的真实闭合位置。

    ⚠️ 不能靠"找下一个 </div>" —— .page-tools 内外都有大量 </div>,
    这样会得到错误区间, 使断言读错范围(实测踩到: 只改注释没结构也能"通过")。
    """
    i = src.find(GTJA_TOOLS_OPEN)
    if i == -1:
        return None
    depth = 0
    j = i
    while j < len(src):
        nxt_open = src.find('<div', j)
        nxt_close = src.find('</div>', j)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + 4
        else:
            depth -= 1
            j = nxt_close + 6
            if depth == 0:
                return (i, j)
    return None


def test_gtja_matrix_card_is_outside_page_tools():
    """⚠️⚠️ 覆盖矩阵卡必须在 .page-tools **外面** —— 否则吸顶遮挡 + 进不了小模块。"""
    src = _src()
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间（结构异常）"
    inside = src[span[0]:span[1]]
    assert '<h3>覆盖矩阵' not in inside, (
        "覆盖矩阵仍在 .page-tools 内部 —— 会被 sticky 实色底钉在顶部盖住下方模块, "
        "且 _secScan 整块跳过 .page-tools ⇒ 它不会出现在小模块索引里"
    )
    # 反向确认它确实存在且是顶层卡
    assert '<h3>覆盖矩阵' in src, "覆盖矩阵卡整个不见了"
    assert src.count('<h3>覆盖矩阵') == 1, "覆盖矩阵标题出现多次"


def test_gtja_page_tools_holds_only_one_card():
    """控制条内只能有 1 张矮卡（查询控制）—— 多塞一张就会重新变成遮挡源。"""
    src = _src()
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间"
    inside = src[span[0]:span[1]]
    n = inside.count('class="card"') + inside.count("class='card'")
    assert n == 1, (
        "#tab-gtja-tools 内含 %d 张卡片（应为 1, 即「① 查询控制」）—— "
        "控制条只适合装矮卡, 多塞内容会吸顶遮挡" % n
    )


def test_sec_index_version_bumped_for_matrix_move():
    """结构变了必须 +1 版本号, 否则旧索引不重建, 用户看到的还是旧布局。"""
    src = _src()
    m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
    assert m, "未找到 _SEC_IDX_VER"
    assert int(m.group(1)) >= 16, (
        "_SEC_IDX_VER=%s 未随覆盖矩阵移出而 +1 —— "
        "已建过索引的页面不会重建, 覆盖矩阵不会出现在小模块索引里" % m.group(1)
    )


def test_matrix_badge_moved_with_card():
    """badge 显示的是覆盖矩阵数据, 必须跟着卡片走（否则语义错位）。"""
    src = _src()
    i = src.find('<h3>覆盖矩阵')
    assert i != -1
    seg = src[i:i + 200]
    assert 'gt-matrix-badge' in seg, (
        "#gt-matrix-badge 未挂在「覆盖矩阵」标题下 —— 它渲染的是矩阵的品种数/区间, "
        "留在查询控制标题里会造成语义错位, 且矩阵折叠后 badge 仍可见"
    )


# ── 本缺陷的变异自检 ────────────────────────────────────────────

MATRIX_MUTATIONS = [
    # 把覆盖矩阵卡塞回 .page-tools（在它内部插入一张卡）
    ("覆盖矩阵塞回控制条",
     '<div class="card" style="margin-bottom:16px">\r\n    <h3>覆盖矩阵',
     '<div class="card" style="margin-bottom:16px">\r\n    <h3>覆盖矩阵'),
]


def _matrix_assertions_ok(src):
    """返回 True 表示这批断言在 src 上全过。"""
    span = _gtja_tools_span(src)
    if not span:
        return False
    inside = src[span[0]:span[1]]
    if '<h3>覆盖矩阵' in inside:
        return False
    if inside.count('class="card"') != 1:
        return False
    m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
    if not m or int(m.group(1)) < 16:
        return False
    i = src.find('<h3>覆盖矩阵')
    if i == -1 or 'gt-matrix-badge' not in src[i:i + 200]:
        return False
    return True


def test_matrix_baseline_passes():
    """先证明未变异时全过, 否则后面「击杀」是假象。"""
    assert _matrix_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


def test_matrix_guard_kills_move_back():
    """把覆盖矩阵卡搬回 .page-tools 内部, 护栏必须变红。

    构造方式: 删掉 .page-tools 的提前闭合（L3328 那个注释闭合）⇒ 矩阵又被包回去。
    """
    src = _src()
    close = ('  </div><!-- /#tab-gtja-tools : 控制条到此处为止, '
             '只含「① 查询控制」一张矮卡 -->\r\n')
    assert src.count(close) == 1, "锚点「控制条提前闭合」不唯一(%d)" % src.count(close)
    mutated = src.replace(close, '', 1)
    assert mutated != src, "变异未生效"
    assert not _matrix_assertions_ok(mutated), (
        "删掉控制条提前闭合后护栏没变红 —— 说明护栏抓不住「矩阵被包回控制条」"
    )


def test_matrix_guard_kills_single_card_removal():
    """控制条内剩 0 张卡（查询控制被删）也应被判失败 —— 防止断言写反。"""
    src = _src()
    mutated = src.replace('    <h3>① 查询控制</h3>',
                          '    <h3>① 查询控制</h3>', 1)
    # 用另一种方式：把 unique 锚点改掉，构造"控制条为空"不可能直接表达，
    # 故这里直接验证「卡片数=1」这条判据本身确实在起作用。
    span = _gtja_tools_span(src)
    inside = src[span[0]:span[1]]
    assert inside.count('class="card"') == 1
    fake_inside = inside.replace('class="card"', 'class="cardX"', 1)
    fake = src[:span[0]] + fake_inside + src[span[1]:]
    assert fake != src
    assert not _matrix_assertions_ok(fake), "卡片数判据失效"
'''

raw = raw + SECTION.encode('utf-8')
assert raw.count(b'\r\n') == 0
io.open(P, 'wb').write(raw)
print('appended', len(raw) - orig_len, 'bytes')
