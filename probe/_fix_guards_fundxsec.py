"""本轮 4 个新红的归因与修法（2026-10-16）。

【4 个红的性质】**都是我引入的，且都不是产品缺陷**：
  1. test_sec_index_version_bumped        —— 我按铁律把 _SEC_IDX_VER 从 27 升到 28, 旧守卫钉的是 27
  2. test_page_tools_lives_below_tab_open_tag[fund] —— fund 也做了「总览→明细」双视图,
                                            控制条不再字面紧跟页签开标签(该守卫**本来就为 quant
                                            留了同一分支**, 只需把 fund 纳入)
  3. test_tab_restructure_invariants      —— 断言写死 `this.loadFund(); this.loadWarrant();` 一行,
                                            我改成多行分支 ⇒ 改成**格式无关**的接线断言
  4. test_frontend_shows_next_refresh_time —— ★ 该守卫**全局**禁用「之后走 5 分钟缓存」,
                                            而新表占位文案合法地写了 5 分钟(它的缓存确实是 5 min)
                                            ⇒ 必须**限定到技术面总览区域**, 而不是放宽
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def edit(rel: str, old: str, new: str, why: str) -> None:
    p = ROOT / rel
    s = p.read_text(encoding="utf-8")
    assert s.count(old) == 1, "%s: 锚点命中 %d (%s)" % (rel, s.count(old), why)
    p.write_text(s.replace(old, new, 1), encoding="utf-8", newline="")
    print("  OK  %-52s %s" % (why, rel))


# ── 1. 版本号 27 → 28 ──────────────────────────────────────────────────────
_V27 = (
    '    """D：tab 内卡片有增删 ⇒ `_SEC_IDX_VER` 必须 +1。"""\n'
    '    h = _h()\n'
    '    assert "_SEC_IDX_VER: \'27\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
    '    assert "_SEC_IDX_VER: \'26\'," not in h, "旧版本号残留"'
)
_V28 = (
    '    """D：tab 内卡片有增删 ⇒ `_SEC_IDX_VER` 必须 +1。\n\n'
    '    【2026-10-16】新增「全品种基本面截面表」并把基本面页拆成两视图 ⇒ 27 升 28。\n'
    '    """\n'
    '    h = _h()\n'
    '    assert "_SEC_IDX_VER: \'28\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
    '    assert "_SEC_IDX_VER: \'27\'," not in h, "旧版本号残留"'
)
edit("tests/test_multi_logic_relocation.py", _V27, _V28, "版本号 27 → 28")

# ── 2. fund 纳入双视图分支 ─────────────────────────────────────────────────
edit("tests/test_page_tools_variety_state.py",
     """    if tab == "quant":
        # ⚠️ 不能用 `src[i:]`（会切到**文件末尾**，把其它 tab 的 .page-tools 也算进来 →
        #    断言假红）。必须用 **div 深度配平**精确框出该容器。""",
     """    if tab in ("quant", "fund"):
        # 【2026-10-16 追加 fund】基本面页也做了同款「全品种总览 → 单品种明细」双视图
        #   (#f-overview-view / #f-detail-view) ⇒ 控制条不再字面紧跟页签开标签。
        #   **不变量仍成立且照旧被断言**：控制条是该视图内的第一个 .page-tools。
        # ⚠️ 不能用 `src[i:]`（会切到**文件末尾**，把其它 tab 的 .page-tools 也算进来 →
        #    断言假红）。必须用 **div 深度配平**精确框出该容器。""",
     "双视图分支纳入 fund")

edit("tests/test_page_tools_variety_state.py",
     """        detail = _span('id="q-detail-view"')
        ov = _span('id="q-overview-view"')
        assert 'id="tab-quant-tools"' in detail, (
            "#tab-quant 的控制条不在 #q-detail-view 内 —— 详情视图会没有入口控件"
        )
        assert detail.count('class="page-tools"') == 1, (
            "#q-detail-view 内 .page-tools 恰好应有 1 个"
        )
        assert 'class="page-tools"' not in ov, (
            "总览视图里放了 .page-tools —— 双视图会各有一个控制条、切换时互相干扰"
        )
        return""",
     """        detail = _span('id="%s-detail-view"' % tab)
        ov = _span('id="%s-overview-view"' % tab)
        assert ('id="tab-%s-tools"' % tab) in detail, (
            "#tab-%s 的控制条不在 #%s-detail-view 内 —— 详情视图会没有入口控件"
            % (tab, tab)
        )
        assert detail.count('class="page-tools"') == 1, (
            "#%s-detail-view 内 .page-tools 恰好应有 1 个" % tab
        )
        assert 'class="page-tools"' not in ov, (
            "%s 的**总览视图**里放了 .page-tools —— 双视图会各有一个控制条、切换时互相干扰"
            % tab
        )
        return""",
     "双视图断言参数化(quant/fund 通用)")

# ── 3. 接线断言改成格式无关 ────────────────────────────────────────────────
edit("tests/test_page_tools_variety_state.py",
     """    assert "this.loadFund(); this.loadWarrant();" in src, "fund 未接 loadWarrant\"""",
     """    # 【2026-10-16】原断言写死 `this.loadFund(); this.loadWarrant();` 一行；fund 的两视图
    #   改造把它拆成了多行分支 ⇒ 改成**格式无关**的接线断言（守的是"进 fund 会拉仓单"
    #   这条行为，不是那行文本怎么排版）。
    assert re.search(r"if \\(t === 'fund'\\) \\{[\\s\\S]{0,800}?this\\.loadWarrant\\(\\)", src), (
        "fund 未接 loadWarrant（进基本面页不会加载仓单子模块）"
    )""",
     "loadWarrant 接线断言改为格式无关")

# ── 4. 「5 分钟缓存」文案守卫限定到技术面总览区域 ───────────────────────────
edit("tests/test_quant_overview_refresh.py",
     '''def test_frontend_shows_next_refresh_time():
    """A3：进度条要把"下次什么时候刷新"显示出来（否则用户不知道是否还在自动更新）。"""
    s = _tpl()
    assert "每 15 分钟自动刷新（下次 " in s
    assert "之后走 15 分钟缓存" in s, "首屏占位文案没同步成 15 分钟"
    assert "之后走 5 分钟缓存" not in s, "还留着旧的「5 分钟缓存」文案"''',
     '''def test_frontend_shows_next_refresh_time():
    """A3：进度条要把"下次什么时候刷新"显示出来（否则用户不知道是否还在自动更新）。

    【★ 2026-10-16 限定范围】原断言在**全文**范围里禁止出现「之后走 5 分钟缓存」。
    新增的「全品种基本面截面表」占位文案合法地写了「之后走 5 分钟缓存」（那个表的后端
    缓存**确实是 5 min**，与技术面总览的 15 min 无关）⇒ 全局禁用会误报。
    **正解是把判据限定到技术面总览视图**，而不是把断言删掉 —— 它要守的是
    "技术面总览不得退回 5 分钟口径"这条产品要求。
    """
    s = _tpl()
    assert "每 15 分钟自动刷新（下次 " in s
    assert "之后走 15 分钟缓存" in s, "首屏占位文案没同步成 15 分钟"
    # 取 #q-overview-view 容器的范围（div 深度配平），只在其中查旧文案
    start = s.rindex("<div", 0, s.index('id="q-overview-view"'))
    depth, i = 0, start
    pat = re.compile(r"<div\\b|</div>")
    ov = ""
    while i < len(s):
        m = pat.search(s, i)
        assert m, "q-overview-view 未配平"
        depth += 1 if m.group(0) == "<div" else -1
        if depth == 0:
            ov = s[start:m.end()]
            break
        i = m.end()
    assert ov, "未取到 #q-overview-view 范围"
    assert "之后走 15 分钟缓存" in ov, "技术面总览的占位文案没同步成 15 分钟"
    assert "之后走 5 分钟缓存" not in ov, "技术面总览里还留着旧的「5 分钟缓存」文案"''',
     "「5 分钟缓存」判据限定到 #q-overview-view")

# 该文件需要 import re（若没有则补）
p = ROOT / "tests" / "test_quant_overview_refresh.py"
src = p.read_text(encoding="utf-8")
if not re.search(r"^import re$", src, re.M):
    src = src.replace("from pathlib import Path", "import re\nfrom pathlib import Path", 1)
    p.write_text(src, encoding="utf-8", newline="")
    print("  OK  补 import re")
print()
print("四处守卫修改完成")
