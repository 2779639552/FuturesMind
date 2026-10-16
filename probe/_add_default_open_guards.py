"""向 tests/test_page_tools_variety_state.py 追加「国君页默认展开」护栏段落。

⚠️ 该文件是 **LF**（read_text 亦会把 CRLF 归一成 LF）⇒ 锚点用 \n。
"""
import sys

P = "tests/test_page_tools_variety_state.py"
raw = open(P, "rb").read()
print("CRLF 行数 =", raw.count(b"\r\n"))
if raw.count(b"\r\n"):
    print("ABORT: 该文件本应是 LF")
    sys.exit(2)

BLOCK = '''
# ── 国君页默认展开（2026-09-30 v18, 用户要求「覆盖矩阵和四源状态不要折叠」）──
#
# 【根因】原默认展开是「只展开第一个高度 ≥200px 的块」。问题在于**高度是渲染期
#   不可信的临时量**：索引在 loadGtja() 之后(0ms)构建时各卡还是「查询后显示。」空壳
#   (实测覆盖矩阵 110px、四源状态 45px)，真高(429px / 895px)要等接口回填才有。
#   于是「四源取数状态有内容却被默认收起」—— 这就是用户报的「仍然折叠」。
# 【修法】新增 _SEC_OPEN_BY_TAB 显式配置默认展开的下标集合，不再让几何量替用户决定
#   「哪些模块该被看见」。

GTJA_DEFAULT_OPEN_CFG = "'gtja': [0, 1],"

# _resetSecFilter 末尾那行的**多行上下文**锚点（单行 `openRows.has(i)` 在
# _renderSectionIndex 里也有一份，不唯一定位就会改错目标）。
RESET_CHIP_SYNC_ANCHOR = (
    "      if (r.btn) r.btn.classList.toggle('is-open', openRows.has(i));\\n"
    "    });\\n"
    "    this._applySecFilter(idx, tabId);"
)


def _default_open_assertions_ok(src: str) -> bool:
    """这批断言在 src 上是否全过（供基线与变异自检共用）。"""
    # ① 显式配置存在，且国君页配了前两块
    if src.count(GTJA_DEFAULT_OPEN_CFG) != 1:
        return False
    if "_SEC_OPEN_BY_TAB: {" not in src:
        return False
    # ② 派发函数：未配置返回 null（=走旧逻辑），配置了返回 Set
    helper = _method_body(src, "_secDefaultOpen")
    if "return null" not in helper or "new Set(o)" not in helper:
        return False
    # ③ 建索引时用配置集合裁决默认展开
    render = _method_body(src, "_renderSectionIndex")
    if "this._secDefaultOpen(tabId)" not in render:
        return False
    if "openRows.has(i)" not in render:
        return False
    # ④ ★ 清空筛选后必须用**同一套**配置还原
    #    （漏了它，点一次「共 N 项」就会把刚看到的四源状态又收回去）
    reset = _method_body(src, "_resetSecFilter")
    if "this._secDefaultOpen(tabId)" not in reset:
        return False
    if "openRows.has(i)" not in reset:
        return False
    # ⑤ 两个方法体内不得残留悬空 openIdx 引用
    #    （改名漏改一处 ⇒ 运行时 ReferenceError：块的开合已改完、标签态同步被跳过，
    #      E2E 实测症状就是「清空后 ③ 仍撑着不收起」）
    for body in (render, reset):
        if "i === openIdx" in body:
            return False
    return True


def test_gtja_default_open_config_exists():
    """国君页必须显式配置「覆盖矩阵 + 四源状态」默认展开。"""
    assert _default_open_assertions_ok(_src()), (
        "默认展开配置缺失或被回退 —— 用户一进国君页又会看到覆盖矩阵/四源状态折叠"
    )


def test_gtja_default_open_indices_match_card_order():
    """配置写的是下标 [0, 1]，得确认它们**确实是**覆盖矩阵与四源状态。

    否则（例如将来把某张卡前移）配置会静默指向别的模块 —— 页面照常渲染，
    只是展开的不是用户想看的那两块，肉眼很难发现。
    """
    src = _src()
    i_matrix = src.find("<h3>覆盖矩阵")
    i_sources = src.find("<h3>② 四源取数状态")
    i_merged = src.find(GTJA_MERGED_TITLE)
    assert -1 not in (i_matrix, i_sources, i_merged), "国君页三张卡标题不全"
    assert i_matrix < i_sources < i_merged, (
        "卡序位置 %d/%d/%d ⇒ 下标 0/1 不再对应 覆盖矩阵/四源状态，"
        "请同步修改 _SEC_OPEN_BY_TAB['gtja']" % (i_matrix, i_sources, i_merged)
    )


def test_default_open_baseline_passes():
    """先证未变异时全过，否则后面「击杀」是假象。"""
    assert _default_open_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


def test_default_open_mutation_anchors_are_unique():
    """变异锚点必须唯一，否则会打到别处（本文件开头经验 3）。"""
    src = _src()
    for a in (
        GTJA_DEFAULT_OPEN_CFG,
        RESET_CHIP_SYNC_ANCHOR,
        "    return new Set(o);",
        "    const o = this._SEC_OPEN_BY_TAB[tabId];",
    ):
        assert src.count(a) == 1, "锚点不唯一（%d 处）: %r" % (src.count(a), a[:70])


def test_default_open_guard_kills_config_removed():
    """【变异·真结构】删掉国君页的默认展开配置，护栏必须变红。

    防的是「悄悄把配置去掉、退回成只展开首块」这种回退。
    """
    src = _src()
    mutated = src.replace(GTJA_DEFAULT_OPEN_CFG + "\\n", "", 1)
    assert mutated != src, "变异未生效（没删掉配置行）"
    assert not _default_open_assertions_ok(mutated), (
        "去掉配置后护栏没变红 —— 抓不住「退回只展开首块」的回退"
    )


def test_default_open_guard_kills_reset_filter_regression():
    """【变异·真回归】把 _resetSecFilter 的 openRows.has(i) 改回悬空 openIdx。

    这正是本轮 E2E 抓到的真实缺陷（静态文本断言当时全绿）：
    清空筛选时抛 "openIdx is not defined"，块的开合已改完、标签态同步被跳过，
    表现为「刚展开的覆盖矩阵/四源状态又被收回去」。
    """
    src = _src()
    regressed = RESET_CHIP_SYNC_ANCHOR.replace("openRows.has(i)", "i === openIdx")
    assert regressed != RESET_CHIP_SYNC_ANCHOR
    mutated = src.replace(RESET_CHIP_SYNC_ANCHOR, regressed, 1)
    assert mutated != src, "变异未生效"
    assert not _default_open_assertions_ok(mutated), (
        "把 _resetSecFilter 退回 openIdx 后护栏没变红 —— 抓不住这类改名漏改"
    )


def test_default_open_guard_kills_helper_neutralized():
    """【变异】让 _secDefaultOpen 恒返回 null（配置形同虚设），护栏必须变红。"""
    src = _src()
    mutated = src.replace("    return new Set(o);", "    return null;", 1)
    assert mutated != src, "变异未生效"
    assert not _default_open_assertions_ok(mutated), (
        "配置失效后护栏没变红 —— _secDefaultOpen 被架空也测不出来"
    )
'''

tail = raw.rstrip(b"\n") + b"\n" + BLOCK.encode("utf-8")
open(P, "wb").write(tail)
print("OK 追加 %d 字节" % len(BLOCK.encode("utf-8")))
print("CRLF 仍为", tail.count(b"\r\n"))
