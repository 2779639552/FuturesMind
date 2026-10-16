# -*- coding: utf-8 -*-
"""同步测试到新结构（tab 重组：国君数据→数据仓库 / 仓单分析→基本面量化）

═══ 为什么要改测试（而不是"测试坏了就算了"）═══
本文件里的 gtja/warrant 用例**核心意图仍然有效**（控制条只装 1 张矮卡、覆盖矩阵不得回加、
div 配平防"卡片被挤出容器"、默认展开配置与卡序一致），**只是锚点从"独立 tab"变成了
"并入后的位置"**。所以做法是**换锚点、保留意图**，并把覆盖面顺带扩大（如配平检查
从 gtja 一个 tab 扩到整个 warehouse 页）。

═══ 改动清单 ═══
① `TABS_WITH_TOOLS` 删 warrant/gtja —— 它们已不是 tab，参数化的 4 个用例自动从 8 项降到 6 项
② `TOOLS_PRIMARY_CONTROL` 删两项
③ `GTJA_TOOLS_OPEN`：控制条已降为页内卡 ⇒ 常量改成无 class 的形态
④ `GTJA_DEFAULT_OPEN_CFG`：`'gtja'` 键随页面迁到 `'warehouse'`
⑤ `test_gtja_tab_div_balance`（+变异版）：锚点改到 **warehouse 容器**（覆盖面更大）
⑥ 新增 `test_tab_restructure_invariants`：把本次结构决策钉死（防后人手滑改回）

⚠️ tests/ 下为 **LF**：读写用 `newline=''` 保持一致。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "tests" / "test_page_tools_variety_state.py"


def main() -> int:
    raw = T.read_bytes()
    assert raw.count(b"\r\n") == 0, "tests/ 下应为 LF"
    s = raw.decode("utf-8")
    h0 = hashlib.md5(raw).hexdigest()

    if "TABS_WITH_TOOLS = [\"run\", \"sentiment\", \"quant\", \"fund\", \"research\", \"variety\"]" in s:
        print("⚠️ 已同步过，跳过")
        return 0

    edits: list[tuple[str, str, str, int]] = [
        # ① 参数化 tab 名单：warrant/gtja 已不是 tab
        ("TABS_WITH_TOOLS",
         'TABS_WITH_TOOLS = ["run", "sentiment", "quant", "fund", "warrant", "research", "gtja", "variety"]',
         '# 【2026-10-14 结构重组】warrant 已并入 fund、gtja 已并入 warehouse(改名「卖方数据」)\n'
         '# ⇒ 不再是独立 tab, 从本名单移除(它们的控制条也已降为**页内卡**, 不再受"tab 控制条"约束)。\n'
         'TABS_WITH_TOOLS = ["run", "sentiment", "quant", "fund", "research", "variety"]', 1),

        # ② 控制条主控件表：删两项
        ("TOOLS_PRIMARY_CONTROL 删 warrant",
         '    "warrant": "wt-product",\n', '', 1),
        ("TOOLS_PRIMARY_CONTROL 删 gtja",
         '    "gtja": "gt-variety",\n', '', 1),

        # ③ 控制条已降为页内卡 ⇒ 常量换形态
        ("GTJA_TOOLS_OPEN 换形态",
         "GTJA_TOOLS_OPEN = '<div id=\"tab-gtja-tools\" class=\"page-tools\">'",
         "# 【2026-10-14】该容器随「国君数据」并入 warehouse 时, 按用户口径**去掉了\n"
         "#   `class=\"page-tools\"`(降为页内卡) —— 一个 tab 只许一个 .page-tools,\n"
         "#   而 warehouse 的顶部控制条不存在(各卡自带上控件)。id 保留(harness 依赖)。\n"
         "GTJA_TOOLS_OPEN = '<div id=\"tab-gtja-tools\">'", 1),

        # ④ 默认展开配置的 tab key
        ("GTJA_DEFAULT_OPEN_CFG 改 warehouse",
         'GTJA_DEFAULT_OPEN_CFG = "\'gtja\': [0, 1],"',
         "# 【2026-10-14】页面迁入 warehouse ⇒ 配置键同步改(否则配置永远命中不到,\n"
         "#   变成静默死配置)。下标语义不变: warehouse 现在**第一块**就是卖方数据。\n"
         "GTJA_DEFAULT_OPEN_CFG = \"'warehouse': [0, 1],\"", 1),
    ]

    out = s
    for tag, old, new, want in edits:
        got = out.count(old)
        assert got == want, "[%s] 锚点命中 %d（期望 %d）" % (tag, got, want)
        out = out.replace(old, new, 1)
        print("    ✔ %-28s (命中 %d)" % (tag, want))

    # ⑤ div 配平用例：锚点从 #tab-gtja 换成 warehouse 容器（覆盖面变大）
    OLD_BAL = """    import re as _re
    src = _src()
    i = src.find('<div id="tab-gtja" class="tab">')
    j = src.find('</main>', i)
    assert i != -1 and j > i, "找不到 #tab-gtja 区间"
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)"""
    NEW_BAL = """    import re as _re
    src = _src()
    # 【2026-10-14 结构重组】锚点由 #tab-gtja 改为 **#tab-warehouse** —— 卖方数据块已并入该页,
    #   且"卡片被挤出容器"的失效模式在并入后同样成立(而且影响面更大: 会挤到 warehouse 的
    #   上传研报/自传数据/研报库三块之后)。覆盖面顺带扩大为整页。
    i = src.find('<div id="tab-warehouse" class="tab">')
    j = src.find('</main>', i)
    assert i != -1 and j > i, "找不到 #tab-warehouse 区间"
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)"""
    assert out.count(OLD_BAL) == 1, "div 配平用例锚点命中 %d" % out.count(OLD_BAL)
    out = out.replace(OLD_BAL, NEW_BAL, 1)
    print("    ✔ %-28s" % "div 配平锚点→warehouse")

    OLD_BAL2 = '''    for t in ('<h3>② 四源取数状态', '<h3>③ 指标总览 / 走势图 / 明细'):
        assert t in src[i:j], "%s 不在 #tab-gtja 区间内 —— 卡片被挤出 tab 容器了" % t'''
    NEW_BAL2 = '''    # 卖方数据块的两张卡必须仍在 warehouse 容器内（未被挤到 </main> 或其它 tab 下）
    for t in ('<h3>② 四源取数状态', '<h3>③ 指标总览 / 走势图 / 明细'):
        assert t in src[i:j], "%s 不在 #tab-warehouse 区间内 —— 卡片被挤出容器了" % t'''
    assert out.count(OLD_BAL2) == 1
    out = out.replace(OLD_BAL2, NEW_BAL2, 1)

    # 变异用例同样换锚点
    OLD_MUT = """    src = _src()
    i = src.find('<div id="tab-gtja" class="tab">')
    j = src.find('</main>', i)
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)"""
    NEW_MUT = """    src = _src()
    # 【2026-10-14】锚点同步改为 warehouse（见上条用例说明）
    i = src.find('<div id="tab-warehouse" class="tab">')
    j = src.find('</main>', i)
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)"""
    assert out.count(OLD_MUT) == 1, out.count(OLD_MUT)
    out = out.replace(OLD_MUT, NEW_MUT, 1)
    OLD_MUT2 = """    i2 = mutated.find('<div id="tab-gtja" class="tab">')
    j2 = mutated.find('</main>', i2)"""
    NEW_MUT2 = """    i2 = mutated.find('<div id="tab-warehouse" class="tab">')
    j2 = mutated.find('</main>', i2)"""
    assert out.count(OLD_MUT2) == 1
    out = out.replace(OLD_MUT2, NEW_MUT2, 1)
    print("    ✔ %-28s" % "变异用例锚点→warehouse")

    # ⑥ 新增结构不变量护栏
    out = out.rstrip("\n") + "\n\n\n" + '''# ══════════════════════════════════════════════════════════════
# 2026-10-14 结构重组：把决策钉死（防后人手滑改回）
# ══════════════════════════════════════════════════════════════
def _strip_c(s: str) -> str:
    """剥掉 HTML 注释后的活代码。

    ★ 本文件多处断言用裸字符串，而**迁移说明注释里会照抄被删掉的原写法**
      （如 `<div id="tab-gtja" class="tab">`）⇒ 不剥注释会把正确结构误报成"没删干净"。
    """
    import re as _re
    return _re.sub(r"<!--.*?-->", "", s, flags=_re.S)


def test_tab_restructure_invariants():
    """结构重组的三条产品决策必须同时成立。

    用户 2026-10-14 拍板：
      ① 数据仓库下并入 卖方数据(原国君数据改名) + 研报库 + 自传数据
      ② 基本面量化下并入 仓单分析
      ③ 情绪数据移到「基本面量化」后、「外文研报」前
    """
    src = _src()
    live = _strip_c(src)

    # ① nav 里不再有 gtja / warrant（并入 ⇒ 独立入口删除）
    assert 'data-tab="gtja"' not in live, "nav 仍有「国君数据」入口 —— 它已并入数据仓库"
    assert 'data-tab="warrant"' not in live, "nav 仍有「仓单分析」入口 —— 它已并入基本面量化"
    assert '<div id="tab-gtja" class="tab">' not in live, "#tab-gtja 容器未删"
    assert '<div id="tab-warrant" class="tab">' not in live, "#tab-warrant 容器未删"

    # ② nav 顺序：情绪数据 在 基本面量化 后、外文研报 前
    import re as _re
    nav = src[src.index('<nav id="nav">', 90000):]
    nav = nav[: nav.index("</nav>")]
    order = _re.findall(r'data-tab="([a-z]+)"', nav)
    assert order.index("fund") < order.index("sentiment") < order.index("foreign"), (
        "导航顺序不符：情绪数据必须位于「基本面量化」后、「外文研报」前（当前 %s）" % order
    )

    # ③ 控制条铁律：一个 tab 恰好 ≤1 个 .page-tools
    #    卖方数据块在 warehouse（该页无 page-tools）；仓单块在 fund（并入方控制条已降级）
    wh = live[live.index('<div id="tab-warehouse" class="tab">'):]
    wh = wh[: wh.index('<div id="tab-variety" class="tab">')] if '<div id="tab-variety"' in wh else wh
    assert "<div id=\"tab-gtja-tools\">" in wh, "卖方数据块不在 warehouse 内"
    assert 'class="page-tools"' not in wh[:2000], (
        "并入 warehouse 的控制条未降为页内卡 —— 会让 warehouse 出现 page-tools"
    )

    # ④ 目标页接线：进 warehouse 要拉卖方数据、进 fund 要拉仓单
    assert "this.loadWarehouse(); this.loadGtja();" in src, "warehouse 未接 loadGtja"
    assert "this.loadFund(); this.loadWarrant();" in src, "fund 未接 loadWarrant"

    # ⑤ Ctrl+N 与 nav 对齐（7 项）且上界同步
    m = _re.search(r"const tabs = \\[([^\\]]+)\\]", src)
    arr = _re.findall(r"'([a-z]+)'", m.group(1))
    assert arr == [t for t in order if t != "variety"], (
        "Ctrl+N 数组与导航不一致：%s vs %s" % (arr, order)
    )
    assert "e.key <= '7'" in src, "Ctrl+N 上界未随 7 页调整"
'''
    T.write_text(out, encoding="utf-8", newline="")
    b = T.read_bytes()
    assert b.count(b"\r\n") == 0, "EOL 被破坏（tests 应为 LF）"

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    bak = ROOT / "backup" / ("test_page_tools_pre_restructure_%s.py" % ts)
    shutil.copy2(T, bak)
    print("\n[备份] backup/%s" % bak.name)
    print("md5 %s -> %s" % (h0[:8], hashlib.md5(T.read_bytes()).hexdigest()[:8]))
    print("✔ 测试已同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())
