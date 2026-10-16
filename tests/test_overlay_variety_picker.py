"""情绪数据页「情绪得分 vs 价格走势」的**独立品种选择** —— 回归测试(2026-09-21)。

═══ 改之前是什么样 ═══

这张图的数据源写死读运行分析页的 #sym:

    const sym = document.getElementById('sym').value;   // 运行分析页的品种

于是"想换这张图上的品种"只能回「运行分析」改分析对象 —— 图表品种与分析对象被
绑死: 换图 = 顺手把分析对象也换了; 想对比两个品种的情绪, 得来回切页反复改。
而同一个页面里, 下方「采集帖子」卡片早就有自己的 #post-filter, 唯独这张图没有。

═══ 改法 ═══

给这张图一个自己的下拉 #overlay-sym(与 #post-filter 同构: 每张卡管自己的口径),
数据源改为读它, 并补三件"独立选择之后才会暴露的事":

① 保留用户选择 —— _loadVarieties() 在数据更新完成后会再跑一次, 若每次都重置成
   默认值, 用户选好的品种会在下一轮采集结束时被悄悄换掉。
② 失败必须清旧图 —— 下拉已切到新品种、屏上却还是上一个品种的曲线, 比明确报错
   更糟(报错至少让人知道眼前是错的)。且**两条失败路径都要清**: 本模板开头把
   window.fetch 包了一层, HTTP>=400 且响应是 HTML 错误页时**直接 throw**, 根本
   回不到 !r.ok 分支 —— 只挂 !r.ok 的话, 服务端 500 时旧图会留在屏上。
③ 空态分两档 —— "只有价格没情绪" 与 "连价格都没有" 不是一回事, 品种可自由选
   之后后者会真的遇到, 不能都报同一句话。

═══ 断言策略 ═══

结构断言(本文件) 挡不住"分支永远不执行"(把条件改成 if False 仍然通过), 所以真正的
护栏是 tests/js/overlay_variety_picker_harness.mjs —— 它把整个内联脚本载入 Node
(桩 DOM/echarts/fetch), 真调 App 的方法、真看发出去的请求 URL。这里只做
"元素/方法在不在"的轻量结构断言, 行为断言交给那个 harness(找不到 node 时跳过)。
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import web_app

ROOT = Path(web_app.__file__).resolve().parent
TEMPLATE = ROOT / "web_template.html"
HARNESS = ROOT / "tests" / "js" / "overlay_variety_picker_harness.mjs"


# ══════════════════════════════════════════════════════════════════
# 工具: 取模板源码 / 剥注释 / 取某个方法体的范围
# ══════════════════════════════════════════════════════════════════

def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """只保留**代码行**, 剥掉整行 // 注释。

    【为什么必须剥】本文件的说明性注释里原样引用了旧写法与旧元素 id(#sym 等),
    若不剥离, "不得出现 X"这类断言会被自己的注释命中 —— 源码文本断言的自指陷阱。
    """
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(ln)
    return "\n".join(out)


def _method_body(src: str, name: str, container: str = "App") -> str:
    """按大括号配平取出 `name(...) { ... }` 的方法体(含签名)。

    【为什么不用正则】方法体里嵌套了对象字面量与箭头函数, 正则非贪婪匹配会在
    第一个 `}` 就截断 —— 那正好会漏掉方法后半段的断言目标。
    """
    code = _code_only(src)
    # 【注意】要容忍 `async ` 前缀 —— 漏了它, async 方法的断言会全部静默找不到方法
    m = re.search(rf"^\s*(?:async\s+)?{re.escape(name)}\s*\([^)]*\)\s*\{{", code, re.M)
    assert m, f"没找到方法 {name} —— 改名了? 方法体断言会全部失效"
    i = code.index("{", m.start())
    depth = 0
    for j in range(i, len(code)):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[m.start() : j + 1]
    raise AssertionError(f"方法 {name} 的大括号不配平")


# ══════════════════════════════════════════════════════════════════
# 1) 下拉本体: 存在、被绑定、就在这张卡的标题行里
# ══════════════════════════════════════════════════════════════════

def test_overlay_select_exists_and_is_bound():
    """模板里必须有独立的 #overlay-sym, 且 onchange 指向专属回调。"""
    src = _src()
    m = re.search(r"<select[^>]*id=\"overlay-sym\"[^>]*>", src)
    assert m, "「情绪得分 vs 价格走势」缺少自己的品种下拉 #overlay-sym"
    assert "App.onOverlaySymChange()" in m.group(0), (
        "下拉没绑定 onOverlaySymChange() —— 选了品种不会重画图"
    )


def test_overlay_select_sits_in_overlay_card_header():
    """下拉必须在叠加图那张卡的 <h3> 内(与 #post-filter 同构), 不能飘到别处。"""
    src = _src()
    h3 = re.search(r"<h3>情绪得分 vs 价格走势.*?</h3>", src, re.S)
    assert h3, "没找到叠加图卡片的 <h3>"
    assert "overlay-sym" in h3.group(0), "品种下拉不在叠加图卡片标题行里"


def test_callback_exists():
    """专属回调方法必须存在, 且只重画这张图(不重载帖子、不碰 #sym)。"""
    body = _method_body(_src(), "onOverlaySymChange")
    assert "loadOverlayChart" in body, "onOverlaySymChange 没有触发起重画"
    assert "loadSentimentPosts" not in body, (
        "onOverlaySymChange 顺带重载了帖子列表 —— 帖子有自己的 #post-filter, 不该被牵连"
    )


# ══════════════════════════════════════════════════════════════════
# 2) 数据源: 图表读自己的下拉, 不再读运行分析品种
# ══════════════════════════════════════════════════════════════════

def test_overlay_chart_reads_its_own_select():
    """loadOverlayChart 的取数来源必须是 #overlay-sym。

    2026-09-30 升级：原断言要求保留 `getElementById('sym')` 兜底，理由是
    「半加载状态会取不到品种」。但那个兜底正是**跨页串味**的来源 —— 情绪页的叠加图
    回退去用「运行分析」页的品种；且它解决的是"取不到"，而取不到是**静默取错品种**
    （_defaultCode() 回退池内第一项）。
    现改为经统一状态层 _curVariety('overlay-sym')：永不为空、且永不借用别的页的品种。
    """
    body = _method_body(_src(), "loadOverlayChart")
    assert "overlay-sym" in body, (
        "loadOverlayChart 不再读 #overlay-sym —— 图表又跟运行分析品种绑回去了"
    )
    assert "_curVariety('overlay-sym')" in body, (
        "取品种没走统一状态层 _curVariety('overlay-sym') —— "
        "控件缺失/半加载时会取不到品种(旧写法还会回退去借 #sym, 属跨页串味)"
    )
    assert "getElementById('sym')" not in body, (
        "loadOverlayChart 仍在读 #sym —— 缺乏 #overlay-sym 时又去借运行分析页的品种"
    )


def test_populate_method_exists_and_preserves_user_choice():
    """填充方法必须存在, 且**保留用户选择**(数据更新后会再次调用它)。"""
    body = _method_body(_src(), "_populateOverlaySymSelect")
    assert "keep" in body, (
        "_populateOverlaySymSelect 不再保留用户选择 —— 每轮采集结束都会把用户选好的品种换掉"
    )
    assert "sentiment_posts" in body, "选项文案丢了帖数, 用户看不出哪个品种真有情绪数据"


def test_load_varieties_refreshes_overlay_select():
    """品种列表刷新时必须一并刷新叠加图的下拉(否则新采集的品种不会出现在里面)。"""
    body = _method_body(_src(), "_loadVarieties")
    assert "_populateOverlaySymSelect" in body, (
        "_loadVarieties 不再刷新 #overlay-sym —— 数据更新后下拉里的帖数会过期"
    )


# ══════════════════════════════════════════════════════════════════
# 3) 失败态: 两条路径都必须清掉旧图
# ══════════════════════════════════════════════════════════════════

def test_overlay_error_helper_exists():
    body = _method_body(_src(), "_overlayChartError")
    assert "_initChart" in body, "失败善后没有重建图表实例 —— 旧曲线会留在屏上"
    assert "overlay-badge" in body, "失败善后没更新徽标, 用户不知道图上是什么"


def test_both_failure_paths_clear_stale_chart():
    """!r.ok 与 catch 两条路径都要走善后。

    【关键】模板开头的 window.fetch 包装层在「HTTP>=400 且响应为 HTML 错误页」时
    会直接 throw, 回不到 !r.ok —— 只挂 !r.ok 的话服务端 500 时旧图留屏。
    """
    body = _method_body(_src(), "loadOverlayChart")
    n = body.count("_overlayChartError")
    assert n >= 2, f"只用 {n} 条路径走了失败善后(应 >=2: !r.ok 与 catch 各一次)"
    assert "catch" in body, "loadOverlayChart 的 catch 被删了"


def test_empty_state_has_two_tiers():
    """空态分两档: 连价格都没有 ≠ 只有价格没情绪。

    【为什么连条件一起断言】只断言两句文案"在不在"是弱护栏 —— 把条件改成
    永假(`false ? [...] : ...`)后, 文案字符串仍在源码里, 断言照样通过。
    必须同时锁住那个分档条件本身。
    """
    body = _method_body(_src(), "loadOverlayChart")
    assert "data.length === 0" in body, (
        "分档条件 `data.length === 0` 不见了 —— 两档又被合并成一档, 空数据会误报「仅展示价格走势」"
    )
    assert "暂无数据" in body, "少了「该时间窗内没有价格序列」这一档提示"
    assert "当前仅展示价格走势" in body, "少了「有价格没情绪」那一档提示"


# ══════════════════════════════════════════════════════════════════
# 4) 真行为验证: 把内联脚本载入 Node 真跑一遍
# ══════════════════════════════════════════════════════════════════

def _find_node():
    """找 node。找不到就返回 None(测试跳过, 不误报失败)。"""
    env = os.environ.get("AGENTSENSE_NODE")
    if env and Path(env).exists():
        return env
    which = shutil.which("node")
    if which:
        return which
    cands = []
    versions = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if versions.is_dir():
        cands += sorted(versions.glob("*/node.exe"), reverse=True)
        cands += sorted(versions.glob("*/bin/node"), reverse=True)
    cands.append(Path(r"C:\Program Files\nodejs\node.exe"))
    for c in cands:
        if c.exists():
            return str(c)
    return None


def test_behavior_harness_all_green():
    """行为断言: 载入整段内联脚本, 真调方法, 真看请求 URL。"""
    node = _find_node()
    if not node:
        pytest.skip("未找到 node —— 跳过行为验证(上面的结构断言仍生效)")
    assert HARNESS.exists(), f"行为验证脚本缺失: {HARNESS}"
    r = subprocess.run(
        [node, str(HARNESS), str(TEMPLATE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    detail = (r.stdout or "")[-3000:] + "\n" + (r.stderr or "")[-1200:]
    assert r.returncode == 0, f"行为验证未全绿(退出码 {r.returncode}):\n{detail}"
    assert "全部通过" in r.stdout
    assert "23 通过" in r.stdout, f"断言条数变了(期望 23 条), 请确认是增删而非被绕过:\n{detail}"
