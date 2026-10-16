# -*- coding: utf-8 -*-
"""给 test_research_variety_summary.py 追加「reports 数字 vs report_rows 数组」契约护栏。

背景（2026-09-30 实测）：
- 生成端点 POST /api/research/variety-summary/generate 返回的 `reports` 是**数字**
  (len(items), 研报篇数)；
- 列表端点 GET /api/research/variety-summary/<date> 返回的 `report_rows` 是**数组**。
- 前端曾写 `v.report_rows = d.reports || 0;` ⇒ 把数字塞进数组字段 ⇒
  `_renderVarietySummaries` 里 `reps.reduce` 抛 TypeError ⇒ **整个品种总结网格渲染中断**。
⚠️ 本文件是 LF；必须二进制读写。
"""
P = r"D:\AgentSense\AgentSense\tests\test_research_variety_summary.py"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src

EXTRA = '''

# ---------------------------------------------------------------------------
# 前端契约:reports(数字) 与 report_rows(数组) 是两个字段, 不可互赋
# ---------------------------------------------------------------------------
def _tmpl() -> str:
    """读前端模板（web_template.html）。"""
    import pathlib
    return (pathlib.Path(web_app.__file__).parent / "web_template.html").read_text(
        encoding="utf-8", errors="replace")


def test_generate_route_reports_is_scalar():
    """生成端点的 `reports` 必须是 int —— 它就是篇数, 不是逐份行数组。"""
    import inspect
    src = inspect.getsource(web_app._generate_variety_summary)
    assert '"reports": len(items)' in src, (
        "生成端点不再返回标量 reports: len(items) —— 若改成数组, "
        "本文件的类型契约与前端赋值都要同步复核"
    )


def test_template_never_assigns_scalar_reports_to_report_rows():
    """⚠️⚠️ 前端禁把 `d.reports`(数字) 赋给 `report_rows`(数组)。

    赋错 ⇒ `reps.reduce(...)` 抛 `TypeError: reps.reduce is not a function`,
    整个品种总结网格渲染中断（实测 JS 报错就是这句）。
    """
    t = _tmpl()
    bad = "v.report_rows = d.reports"
    assert bad not in t, (
        "前端又把生成端点的标量 `d.reports` 赋给了数组字段 `report_rows` —— "
        "会令 _renderVarietySummaries 的 reps.reduce 抛 TypeError、整个网格渲染中断"
    )


def test_template_report_rows_read_is_array_safe():
    """`report_rows` 的消费点必须仍按数组用（`|| []`），保证缺字段也不炸。"""
    t = _tmpl()
    assert "const reps = v.report_rows || [];" in t, (
        "`report_rows` 的读取点被改动 —— 它必须兜底成数组([]), "
        "否则接口缺字段时 reduce 直接抛错"
    )


def test_contract_guard_kills_bad_assignment():
    """变异自检:把错误赋值塞回去, 护栏必须变红（证明它真抓得住）。"""
    t = _tmpl()
    mutated = t.replace(
        "v.generated_at = d.generated_at || '';", 
        "v.generated_at = d.generated_at || ''; v.report_rows = d.reports || 0;",
        1,
    )
    assert mutated != t, "变异未生效（锚点没命中）"
    assert "v.report_rows = d.reports" in mutated
    # 复刻断言逻辑, 确认在变异体上确实为 False
    assert not ("v.report_rows = d.reports" not in mutated), (
        "护栏对「reports 赋给 report_rows」这一回退不敏感"
    )
'''

out = (src.rstrip("\n") + "\n" + EXTRA).encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
