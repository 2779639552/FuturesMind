# -*- coding: utf-8 -*-
"""为「空值语义豁免」补回归护栏(tests/test_page_tools_variety_state.py, LF)。

① 修正被补丁打偏的既有变异锚点(旧锚点 `if (!sel.value) sel.value = ...` 已不存在)
② 新增: 6 个空值有语义的下拉 HTML 首项必须是 value=""; _populateVarietySelects
   必须有豁免; 并做变异验证(删豁免 / 删补选 / 把某个下拉改成非空首项 三种都要红)。
"""
import io

P = r"D:\AgentSense\AgentSense\tests\test_page_tools_variety_state.py"
raw = open(P, "rb").read()
bare = raw.count(b"\n") - raw.count(b"\r\n")
assert bare == raw.count(b"\n"), "该文件应为 LF(bareLF=%d)" % bare
txt = raw.decode("utf-8")

OLD_CALL = "if (!sel.value) sel.value = this._curVariety(id);"
NEW_CALL = (
    "const _allowsEmpty = !!sel.querySelector('option[value=\"\"]');\n"
    "      if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);"
)
OLD_GUARD = OLD_CALL + "\n"

# ── ① 修正 _watch_populate ─────────────────────────────────────────
old_watch = (
    "def _watch_populate(s):\n"
    "    return \"sel.value = this._curVariety(id)\" in _method_body(s, \"_populateVarietySelects\")\n"
)
new_watch = (
    "def _watch_populate(s):\n"
    "    return _populate_assertions_ok(s)\n"
)
assert txt.count(old_watch) == 1, "未找到 _watch_populate"
txt = txt.replace(old_watch, new_watch)

# ── ② 修正 test_mutation_anchors_are_safe 里的锚点 ──────────────────
old_anchor = '        "if (!sel.value) sel.value = this._curVariety(id);",\n'
new_anchor = (
    '        "const _allowsEmpty = !!sel.querySelector(\'option[value=\\"\\"]\');",\n'
    '        "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);",\n'
)
assert txt.count(old_anchor) == 1, "未找到 anchors 里的 populate 锚点"
txt = txt.replace(old_anchor, new_anchor)

# ── ③ 修正 MUTATIONS 里那条变异 ────────────────────────────────────
old_mut = (
    '    (\n'
    '        "_populateVarietySelects 只填不选",\n'
    '        lambda s: s.replace("if (!sel.value) sel.value = this._curVariety(id);", "", 1),\n'
    '    ),\n'
)
new_mut = (
    '    (\n'
    '        "_populateVarietySelects 只填不选",\n'
    '        lambda s: s.replace(\n'
    '            "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);", "", 1\n'
    '        ),\n'
    '    ),\n'
    '    (\n'
    '        "丢掉空值语义豁免(全部品种被补成池首)",\n'
    '        lambda s: s.replace(\n'
    '            "!sel.value && !_allowsEmpty) sel.value", "!sel.value) sel.value", 1\n'
    '        ),\n'
    '    ),\n'
)
assert txt.count(old_mut) == 1, "未找到 MUTATIONS 里的 populate 条目"
txt = txt.replace(old_mut, new_mut)

# ── ④ 插入新护栏区块 ──────────────────────────────────────────────
NEW_BLOCK = '''
# ══════════════════════════════════════════════════════════════════
# 3b) 空值语义豁免 —— 「全部品种」下拉不得被补成池首
# （2026-09-30 用户报障：问 PTA 只召回橡胶板块）
# ══════════════════════════════════════════════════════════════════
# 【症状】研报问答问「PTA近日情况」答"现有片段不足以判断"，当日国泰君安
#   「PX PTA MEG 基本面数据」(rid=1463) 与华泰「聚酯日报」(rid=1440) 一条都搜不到。
# 【根因】_populateVarietySelects 填充后无条件补默认选中，而 _curVariety 的兜底是
#   _defaultCode() = 池内第一项(BR)。#res-ask-variety 的首项本是
#   <option value="">全部品种</option>，空串是**合法语义**，被静默改成 BR
#   ⇒ 前端把 variety="BR" 发给后端 ⇒ 检索被品种过滤到橡胶板块。
#   实证①(qa_messages)：09-30 那次问句 filters.variety="BR"，而 09-29/09-28 同一问句为 "TA"。
#   实证②(检索层 A/B)：variety="BR" 召回 6 篇全是 RU/NR/BR；
#          variety="TA" 召回含 rid=1463/1440。
# 【修法】该 select 内存在 option[value=""] ⇒ 空串有语义 ⇒ 跳过补选。
EMPTY_SEMANTIC_SELECTS = (
    "res-ask-variety",       # 研报问答：空串=全部品种(问句带品种名时由后端 _infer_variety_from_question 识别)
    "res-filter-variety",    # 研报列表筛选：空串=全部品种
    "event-variety",         # 情绪事件：空串=全部品种
    "res-upload-variety",    # 本地研报上传：空串=自动识别(可含多品种)
    "ud-upload-variety",     # 自传数据上传：空串=自动识别
    "dwh-ds-variety",        # 数据仓库数据集：空串=选择品种(占位)
)

POPULATE_ALLOWS_EMPTY = (
    "const _allowsEmpty = !!sel.querySelector('option[value=\\"\\"]');\\n"
    "      if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);"
)


def _select_has_empty_option(src: str, sid: str) -> bool:
    """该 <select id=sid> 内是否存在 <option value="">。"""
    m = re.search(r"<select[^>]*\\bid=\\"" + re.escape(sid) + r"\\"[^>]*>", src)
    if not m:
        return False
    end = src.find("</select>", m.end())
    if end < 0:
        return False
    return 'value=""' in src[m.end() : end]


def _populate_assertions_ok(src: str) -> bool:
    """_populateVarietySelects 必须既补默认选中、又豁免空值有语义的下拉。"""
    body = _method_body(src, "_populateVarietySelects")
    if "sel.value = this._curVariety(id)" not in body:
        return False
    if POPULATE_ALLOWS_EMPTY not in body:
        return False
    return True


def test_populate_exempts_empty_semantic_selects():
    """_populateVarietySelects 必须豁免「空串有语义」的下拉，不得一律补成池首。"""
    assert _populate_assertions_ok(_src()), (
        "_populateVarietySelects 缺少空值语义豁免 —— 会把「全部品种」静默补成池内首项"
        "(实测: 研报问答品种被设成 BR, 问 PTA 时检索被过滤到橡胶板块, 当日 PTA 研报全部搜不到)"
    )
    live = _live_occurrences(_src(), 'option[value=""]')
    assert len(live) == 1, (
        "空值豁免的判据必须**恰好 1 处**在活代码里(实测 %d 处) —— 判据落在注释里等于没生效"
        % len(live)
    )


@pytest.mark.parametrize("sid", EMPTY_SEMANTIC_SELECTS)
def test_empty_semantic_select_html_still_has_empty_option(sid):
    """这些下拉的 HTML 首项必须保留 value=""（空串是它们的正常语义）。"""
    assert _select_has_empty_option(_src(), sid), (
        "#%s 的 <option value=\\"\\"> 被删了 —— 空串不再可选, 该模块将被迫落到某个具体品种" % sid
    )


def test_pool_first_is_not_all_varieties():
    """判据自检: 池内第一项确实不是「全部」——证明补成池首会改变语义。"""
    src = _src()
    i = src.find("_defaultCode() {")
    assert i > 0
    assert "_varieties[0]" in src[i : i + 200]
    assert _src().count("全部品种") >= 3, "「全部品种」文案变少 —— 请核对空值语义下拉是否被改动"


MUT_POPULATE = [
    (
        "删掉空值豁免(回到无条件补池首)",
        lambda s: s.replace(
            "!sel.value && !_allowsEmpty) sel.value", "!sel.value) sel.value", 1
        ),
    ),
    (
        "删掉补默认选中本身",
        lambda s: s.replace(
            "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);", "", 1
        ),
    ),
    (
        "res-ask-variety 首项改成非空(空串不再有语义)",
        lambda s: s.replace(
            '<select id="res-ask-variety"><option value="">全部品种</option></select>',
            '<select id="res-ask-variety"></select>',
            1,
        ),
    ),
]


@pytest.mark.parametrize("desc,mutate", MUT_POPULATE, ids=[d for d, _ in MUT_POPULATE])
def test_populate_guard_kills_mutation(desc, mutate):
    """逐个破坏「空值语义豁免」，确认护栏会红。"""
    orig = _src()
    mutated = mutate(orig)
    assert mutated != orig, "变异未生效: " + desc
    killed = not _populate_assertions_ok(mutated) or not all(
        _select_has_empty_option(mutated, sid) for sid in EMPTY_SEMANTIC_SELECTS
    )
    assert killed, "护栏被变异骗过(未击杀): " + desc


def test_populate_mutation_anchors_are_unique():
    """新增变异的锚点必须唯一（否则会打到别处造成假击杀）。"""
    orig = _src()
    for a in [
        "!sel.value && !_allowsEmpty) sel.value",
        "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);",
        '<select id="res-ask-variety"><option value="">全部品种</option></select>',
    ]:
        assert orig.count(a) == 1, "锚点不唯一(%d 处): %r" % (orig.count(a), a[:70])

'''

marker = "\n# ══════════════════════════════════════════════════════════════════\n# 4) 清理与样式\n"
assert txt.count(marker) == 1, "未找到第 4 节标题"
txt = txt.replace(marker, NEW_BLOCK + marker, 1)

open(P, "wb").write(txt.encode("utf-8"))
raw2 = open(P, "rb").read()
print("written size", len(raw2))
print("CRLF count", raw2.count(b"\r\n"), "-> should be 0")
print("new anchors:", txt.count("_populate_assertions_ok"), txt.count("EMPTY_SEMANTIC_SELECTS"))
