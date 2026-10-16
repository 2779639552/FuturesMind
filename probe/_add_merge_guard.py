# -*- coding: utf-8 -*-
"""给 test_page_tools_variety_state.py 追加「国君页三卡合并」静态护栏。

背景（2026-09-30 用户要求）：④ 走势图 / ⑤ 明细数据 都由 ③ 指标总览的「查看」按钮驱动，
拆三张卡时要靠 _gtEnsureVisible 展开兄弟块、且三块各占一条索引项 ⇒ 合并进 ③ 一张卡。

护栏要守住的性质（都是"容易被人好心地拆回去"的点）：
  1. 三个容器 id(#gt-indicators / #gt-chart / #gt-table)必须在**同一张 .card** 内；
  2. 合并卡内**只能有 1 个 h3** —— _secTitleOf 取第一个 h3 作索引名，多塞会串名；
  3. ④⑤ 不得再作为独立卡片存在（标题里没有 "④ 走势图" / "⑤ 明细数据" 的 h3）；
  4. showGtjaIndicator 不得再调 _gtEnsureVisible（同卡内结果就地可见，再滚动反而丢掉视线）。

⚠️ 本文件是 LF；二进制读写。
"""
P = r"D:\AgentSense\AgentSense\tests\test_page_tools_variety_state.py"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src

EXTRA = r'''

# ── 国君页三卡合并（2026-09-30 用户要求）─────────────────────────
# 【症状】④ 走势图 / ⑤ 明细数据 都只由 ③ 指标总览的「查看」按钮驱动, 却各自成卡:
#   ① 点「查看」得靠 _gtEnsureVisible 去展开下面两块, 否则屏幕上什么都不动(像按钮坏了);
#   ② 三块各占一条小模块索引项, 「选指标→看走势→看明细」被拆成三段。
# 【修法】并入 ③ 一张卡, 用虚线分隔的 <div> 子标题(不用 h3, 免得串索引名)。
# 【关键约束】三个容器 id 与三个 badge 一律不改 —— tests/js/gtja_module_harness.mjs
#   按 id 断言渲染行为(50 例)。

GTJA_MERGED_TITLE = '<h3>③ 指标总览 / 走势图 / 明细'


def _merged_card_span(src):
    """返回合并卡的 (起, 止)。以标题所在 div 起做 div 深度配平。

    ⚠️ 与 _gtja_tools_span 同源: 不能用"找下一个 </div>" —— 卡内外都有大量 </div>。
    """
    i = src.find(GTJA_MERGED_TITLE)
    if i == -1:
        return None
    start = src.rfind('<div', 0, i)
    if start == -1:
        return None
    depth = 0
    j = start
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
                return (start, j)
    return None


def test_gtja_three_containers_share_one_card():
    """★ 总览表 / 走势图 / 明细 三个容器必须在同一张卡内。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡（标题「③ 指标总览 / 走势图 / 明细」不见了？）"
    inside = src[span[0]:span[1]]
    for i in ('id="gt-indicators"', 'id="gt-chart"', 'id="gt-table"'):
        assert i in inside, (
            "%s 不在合并卡内 —— 三块被拆开了。合并的意义是「点查看→就地出图出表」,"
            "拆开后又要靠 _gtEnsureVisible 去展开兄弟块, 且各占一条索引项" % i
        )
    # 三个 badge 也要在卡内（徽标渲染的是本卡数据, 留在卡外会语义错位）
    for b in ('id="gt-ind-badge"', 'id="gt-chart-badge"', 'id="gt-tbl-badge"'):
        assert b in inside, "%s 不在合并卡内（徽标与它渲染的数据分离了）" % b


def test_gtja_merged_card_has_single_h3():
    """⚠️ 合并卡内只能有 1 个 h3 —— _secTitleOf 取第一个 h3 作索引名。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡"
    inside = src[span[0]:span[1]]
    n = inside.count('<h3>')
    assert n == 1, (
        "合并卡内含 %d 个 h3（应为 1）。_secTitleOf 只取**卡内第一个** h3, "
        "多塞会把「走势图」「明细数据」当索引名, 卡内子标题请用 <div>" % n
    )


def test_gtja_old_standalone_cards_gone():
    """④⑤ 不得再作为独立 h3 卡片存在（合并必须真发生, 不是复制一份）。"""
    src = _src()
    for t in ('<h3>④ 走势图', '<h3>⑤ 明细数据'):
        assert t not in src, "%s 仍作为独立卡片存在 —— 合并没做干净（会重复渲染同一容器）" % t


def test_showgtjaindicator_no_longer_ensures_visible():
    """★ 点「查看」后结果就地可见, 不得再调 _gtEnsureVisible 去滚动/展开。

    ⚠️ _gtEnsureVisible **方法本体保留**（harness ④ 段守护它, 且是通用工具）,
       这里只禁 showGtjaIndicator 继续依赖它。
    """
    src = _src()
    body = _method_body(src, "showGtjaIndicator")
    assert "_gtEnsureVisible(" not in body, (
        "showGtjaIndicator 仍在调 _gtEnsureVisible —— 三卡已合并, 结果就地渲染在同一张卡里, "
        "再展开兄弟块只会把当前视线滚走"
    )
    # 反向确认渲染调用还在（防止"直接删空函数体"式假绿）
    assert "_renderGtjaChart(" in body and "_renderGtjaTable(" in body, (
        "showGtjaIndicator 不再渲染图表/明细 —— 合并后它仍必须驱动这两块"
    )


def _merge_assertions_ok(src):
    """这批断言在 src 上是否全过。"""
    span = _merged_card_span(src)
    if not span:
        return False
    inside = src[span[0]:span[1]]
    if not all(i in inside for i in
               ('id="gt-indicators"', 'id="gt-chart"', 'id="gt-table"')):
        return False
    if inside.count('<h3>') != 1:
        return False
    if '<h3>④ 走势图' in src or '<h3>⑤ 明细数据' in src:
        return False
    try:
        body = _method_body(src, "showGtjaIndicator")
    except Exception:
        return False
    if "_gtEnsureVisible(" in body:
        return False
    return "_renderGtjaChart(" in body and "_renderGtjaTable(" in body


def test_gtja_merge_baseline_passes():
    """先证未变异时全过, 否则「击杀」是假象。"""
    assert _merge_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


def test_gtja_merge_guard_kills_split_back():
    """【变异·真结构】把 #gt-table 那块**真的搬出**合并卡, 护栏必须变红。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡"
    inside = src[span[0]:span[1]]
    tbl = inside.find('<!-- 明细数据')
    assert tbl != -1, "卡内找不到明细数据块"
    dup, end = _div_span(src, span[0] + tbl + inside[tbl:].find('<div'))
    assert dup.startswith("<div"), "明细块配对异常"
    start_abs = span[0] + tbl + inside[tbl:].find('<div')
    mutated = src[:start_abs] + src[end:] + "\n" + dup
    assert mutated != src, "变异未生效"
    assert not _merge_assertions_ok(mutated), (
        "把明细数据搬出合并卡后护栏没变红 —— 抓不住这次拆分"
    )


def test_gtja_merge_guard_kills_extra_h3():
    """【变异】给合并卡塞第二个 h3, 护栏必须变红（防索引串名）。"""
    src = _src()
    anchor = GTJA_MERGED_TITLE
    assert src.count(anchor) == 1
    mutated = src.replace(anchor, anchor + '\n    <h3>走势图</h3>', 1)
    assert mutated != src, "变异未生效"
    assert not _merge_assertions_ok(mutated), (
        "卡内多塞一个 h3 后护栏没变红 —— 抓不住「索引名被串掉」"
    )
'''

out = (src.rstrip("\n") + "\n" + EXTRA).encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
