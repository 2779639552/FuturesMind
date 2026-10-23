"""研报「每日总结」三/四节横向排列的静态契约护栏(2026-10-21)。

【需求】用户原话: "在研报的每日总结模块下, 将三、可信程度分析 与 四、综合结论与关注要点
两个部分改为横向排列, 左边是三, 右边是四"。

【本文件守什么(行为细节由 tests/js/daily_twocol_harness.mjs 真执行)】
  1. ★ 必须**有调用点**且在 `_decorateConflictTable` 之后 —— 方法写了没人调 = 白改;
     放前面则前面几处装饰搬动表格节点时可能打乱刚建好的两列。
  2. ★ 定位按**标题文本**, 不许写死下标 —— 第一节是后端代码生成的, 将来多/少一节
     会让 `h2[2]` 指到别的节, 现象是"右边一列莫名变成观点对比表"。
  3. ★ 第四节收集的停止条件必须**放行首项**(`n !== h4 && ...`): h4 自己就是 H2,
     不放行 ⇒ 一进循环就 break、右列只剩标题空壳(本次实跑踩到, 为此专门守)。
  4. 纯 DOM 搬移(appendChild/insertBefore), 不许回写 innerHTML —— 会丢已挂的事件、
     也会让 marked 已渲染的表格再解析一遍。
  5. 先插容器再搬节点: 先 appendChild 把 h3 搬走, h3 就不在 host 里了,
     insertBefore(wrap, h3) 抛 NotFoundError ⇒ 被 loadDailySummary 的 catch 吃掉 ⇒ 整卡「加载失败」。
  6. 列宽 auto-fit + min-width:0(长表格不撑破单列), 窄屏自动堆成单列。
  7. 幂等标记 dataset.rvTwoCol。
"""

from __future__ import annotations

import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _s() -> str:
    return io.open(TPL, encoding="utf-8").read()


def _method() -> str:
    """抠出 _layoutDailyTwoCol 方法体(按花括号配平, 模板缩进已压平)。"""
    s = _s()
    m = re.search(r"\n\s*_layoutDailyTwoCol\(root\)\s*\{", s)
    assert m, "找不到 _layoutDailyTwoCol 方法定义"
    i = s.index("{", m.start())
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[m.start():j + 1]
    raise AssertionError("_layoutDailyTwoCol 花括号未闭合")


def test_method_exists_and_is_called_after_decorators():
    s = _s()
    assert "_layoutDailyTwoCol(root) {" in s, "方法未定义"
    call = "this._layoutDailyTwoCol(el);"
    assert s.count(call) == 1, "调用点应恰好 1 处(实际 %d)" % s.count(call)
    dec = s.index("this._decorateConflictTable(el);")
    assert dec < s.index(call), "必须在冲突表装饰之后调用(否则两列结构会被后续 DOM 搬动打乱)"
    # 必须在 loadDailySummary 内、紧跟同一段渲染流程
    fn = s.index("async loadDailySummary() {")
    nxt = s.find("async loadDailySummary() {", fn + 10)
    assert fn < s.index(call) and (nxt < 0 or s.index(call) < nxt), "调用点跑到了别的方法里"


def test_locate_sections_by_heading_text_not_index():
    m = _method()
    assert "querySelectorAll('h2')" in m
    assert "findIndex" in m, "必须按标题文本找节"
    assert "可信程度" in m and "综合结论" in m
    # 禁止写死下标定位：h2[2] / hs[2] / querySelectorAll('h2')[3]
    for bad in [r"hs\[\s*[23]\s*\]", r"querySelectorAll\('h2'\)\[\s*\d+\s*\]"]:
        assert not re.search(bad, m), "写死了节下标(%s) ⇒ 节数一变就错位" % bad


def test_fourth_section_loop_admits_its_own_heading():
    """★ 本次实际踩到的坑：起点 h4 自身是 H2，不放行就收空。"""
    m = _method()
    assert "n !== h4 && n.tagName === 'H2'" in m, (
        "第四节收集的停止条件必须放行首项(h4 自己是 H2)，否则一进循环就 break，右列只剩标题"
    )
    # 循环体里必须 push 起点自身
    seg = m[m.index("n = h4;"):]
    assert "nodes4.push(n)" in seg


def test_pure_dom_moves_no_innerhtml():
    m = _method()
    # 注释里会提到 innerHTML(解释为什么不能用它)，断言只看可执行行
    code = "\n".join(l for l in m.splitlines() if not l.strip().startswith("//"))
    assert "innerHTML" not in code, "不许回写 innerHTML(丢事件 + 二次解析)"
    assert "appendChild" in code and "insertBefore" in code


def test_container_inserted_before_moving_nodes():
    m = _method()
    i_ins = m.index("host.insertBefore(wrap, h3);")
    i_col = m.index("wrap.appendChild(mkCol(nodes3")
    assert i_ins < i_col, (
        "必须先 insertBefore 再搬节点：先 appendChild 搬走 h3 会让 insertBefore 抛 NotFoundError "
        "⇒ 被 loadDailySummary 的 catch 吃掉 ⇒ 整卡显示「加载失败」"
    )


def test_responsive_columns_and_min_width():
    m = _method()
    assert "repeat(auto-fit,minmax(320px,1fr))" in m, "列宽应 auto-fit，窄屏自动堆叠"
    assert "min-width:0" in m, "grid 子项默认 min-width:auto，长表格会撑破单列"


def test_idempotent_marker():
    m = _method()
    assert "rvTwoCol" in m and "dataset" in m, "缺幂等标记，重复调用会二次包装"


def test_degrades_when_a_section_is_missing():
    m = _method()
    assert re.search(r"if \(i3 < 0 \|\| i4 < 0\) return;", m), "两节缺任一必须原样返回"
    assert "host !== h4.parentNode" in m, "两节不在同一父级时不许跨容器搬节点"


def test_template_stays_crlf():
    raw = io.open(TPL, "rb").read()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "模板出现裸 LF(本项目铁律：CRLF)"
