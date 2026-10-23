# -*- coding: utf-8 -*-
"""删「价格-库存关联分析」小模块（用户 2026-10-16 要求）。

【背景】该小节在「基本面量化」页 ① 三指标策略系统卡（#f-strategy-card）→
        「库存维度明细」区块内，形如：
            标题 div（含 #f-analysis-badge）+ 正文 div（#f-analysis-body）

★★ 三条纪律（本项目血泪）：
  1. **删卡必须核对 div 闭合**：本模块 4 行自成一对，其后那个 ` </div>` 是外层
     「库存维度明细」区块的闭合 —— 一起删会让 ① 卡提前闭合、后面内容被挤出容器
     （本项目 2026-10-08 在国君页踩过：用户看到「模块平铺到了所有大模块下面」）。
  2. **结构改动不能只做文本断言** ⇒ 同步加 div 深度配平护栏。
  3. `_SEC_IDX_VER` **必须 +1**（结构变了要重建索引，否则旧浏览器残留僵尸项）。

改动清单：
  A web_template.html                       A1 删 4 行 / A2 版本号 +1 / A3 三处注释
  B tests/test_fund_xsection.py             版本断言 29 → 30
  C tests/test_multi_logic_relocation.py    版本断言 29 → 30
  D tests/test_page_tools_variety_state.py  D1 反转断言 / D2 守卫断言 / D3 div 配平护栏
  E probe/_verify_inventory_migration.py + probe/_verify_trim3.py  同步
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv

report: list[str] = []


# ────────────────────────────────────────────────────────────
# A. web_template.html（CRLF）
# ────────────────────────────────────────────────────────────
A1_OLD = (
    ' <div style="font-size:0.8125rem;font-weight:500;margin:16px 0 8px">\n'
    ' 价格-库存关联分析 <span class="badge badge-info" id="f-analysis-badge"></span>\n'
    ' </div>\n'
    ' <div style="font-size:0.8125rem;color:var(--text-secondary);line-height:1.8" id="f-analysis-body">加载中...</div>\n'
)
A1_NEW = (
    ' <!-- 【2026-10-16 用户要求删除】此处原为「价格-库存关联分析」小节\n'
    ' (标题 #f-analysis-badge + 正文 #f-analysis-body: 三张统计卡 = 价格-库存 Pearson R /\n'
    '  库存趋势(近5日均 vs 更早5日均) / 最新基差率, 外加"背离检测"文字)。\n'
    ' 删除判据: ① Pearson R 与"背离检测"在左侧「库存周期四象限」已有更完整的同源表述;\n'
    '   ② 库存趋势/基差率在 ② 估值+驱动 与 ③ 利润 里各有更完整的视图 ⇒ 属重复展示。\n'
    ' ⚠ loadFundAnalysis 的取值点**保留**(body/badge 均带 `if (...)` 守卫, 无落点时零副作用);\n'
    '   该函数**同时渲染 #f-research 研报基本面指标卡** ⇒ 绝不能整删函数(删了整个函数\n'
    '   会连带把研报卡一起弄没)。保留取值点也便于将来回退。\n'
    ' ⚠ 本备注与上方「价格 vs 仓单库存」那条删除备注各自独立, 勿混为一条。 -->\n'
)

A3_1_OLD = ' · 价格-库存关联分析 → 基本面页 ① 三指标系统「库存维度明细」\n'
A3_1_NEW = (' · 价格-库存关联分析 → 基本面页 ① 三指标系统「库存维度明细」\n'
            '    【2026-10-16】用户要求删除该小节(与「库存周期四象限」重复), 已删。\n')

A3_2_OLD = ' // 【渲染】#f-analysis-body / #f-analysis-badge / #f-research\n'
A3_2_NEW = (' // 【渲染】#f-research（研报基本面指标卡）。\n'
            ' // 【2026-10-16】**「价格-库存关联分析」小节已按用户要求删除** ⇒ #f-analysis-body /\n'
            ' //   #f-analysis-badge 无落点; 两个取值点**保留且均有 `if (...)` 守卫**(零副作用、便于回退)。\n')

A3_3_OLD = ' // · loadFundAnalysis : 价格-库存关联 / 价格-仓单双轴 / 研报基本面指标(→ #f-research)\n'
A3_3_NEW = (' // · loadFundAnalysis : 研报基本面指标(→ #f-research)。\n'
            ' //   【2026-10-16】其"价格-库存关联"小节与 2026-10-14 的"价格-仓单双轴"均已删,\n'
            ' //   本函数现实际只服务 #f-research(取值点保留带守卫)。\n')

A2_OLD = "_SEC_IDX_VER: '29',"
A2_NEW = "_SEC_IDX_VER: '30',"


def patch_a() -> None:
    p = ROOT / "web_template.html"
    raw = p.read_bytes()
    crlf, lf = raw.count(b"\r\n"), raw.count(b"\n")
    assert crlf > 0 and crlf == lf, "web_template.html 不是纯 CRLF（crlf=%d lf=%d）" % (crlf, lf)
    t = raw.decode("utf-8")

    for tag, old, new in (
        ("A1 删「价格-库存关联分析」4 行", A1_OLD, A1_NEW),
        ("A3.1 迁出清单加删除标注", A3_1_OLD, A3_1_NEW),
        ("A3.2 loadFundAnalysis 渲染清单", A3_2_OLD, A3_2_NEW),
        ("A3.3 方法总览", A3_3_OLD, A3_3_NEW),
        ("A2 _SEC_IDX_VER 29 → 30", A2_OLD, A2_NEW),
    ):
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        c = t.count(o)
        assert c == 1, "[%s] 锚点命中 %d 次（应为 1）" % (tag, c)
        t = t.replace(o, n, 1)
        report.append("  ✓ A %s" % tag)

    out = t.encode("utf-8")
    b = out.count(b"\r\n")
    assert b > 0 and out.count(b"\n") == b, "写回会破坏 EOL"
    # 结构自检：删掉的 DOM 必须真的不在了（且不许只删一半）
    assert 'id="f-analysis-body"' not in t, "A1 没删干净：#f-analysis-body 仍在"
    assert 'id="f-analysis-badge"' not in t, "A1 没删干净：#f-analysis-badge 仍在"
    assert '价格-库存关联分析' in t, "删除说明注释没写进去"
    if WRITE:
        p.write_bytes(out)
    report.append("  ✓ A 写回尺寸 %d → %d 字节" % (len(raw), len(out)))


# ────────────────────────────────────────────────────────────
# B / C. 两处版本号断言（LF）
# ────────────────────────────────────────────────────────────
B_OLD = ('    assert "_SEC_IDX_VER: \'29\'," in h, "_SEC_IDX_VER 未随卡片增删 +1"\n'
         '    assert "_SEC_IDX_VER: \'28\'," not in h\n')
B_NEW = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30。\n'
         '    assert "_SEC_IDX_VER: \'30\'," in h, "_SEC_IDX_VER 未随卡片增删 +1"\n'
         '    assert "_SEC_IDX_VER: \'29\'," not in h\n')

C_OLD = ('    assert "_SEC_IDX_VER: \'29\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
         '    assert "_SEC_IDX_VER: \'28\'," not in h, "旧版本号残留"\n')
C_NEW = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30。\n'
         '    assert "_SEC_IDX_VER: \'30\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
         '    assert "_SEC_IDX_VER: \'29\'," not in h, "旧版本号残留"\n')

C_DOC_OLD = ('    【2026-10-16】新增「全品种基本面截面表」并把基本面页拆成两视图 ⇒ 27 升 28。\n')
C_DOC_NEW = ('    【2026-10-16】新增「全品种基本面截面表」并把基本面页拆成两视图 ⇒ 27 升 28；\n'
             '    同日删「价格-库存关联分析」小模块 ⇒ 29 升 30。\n')


def patch_file(rel: str, edits: list[tuple[str, str, str]]) -> None:
    p = ROOT / rel
    raw = p.read_bytes()
    assert raw.count(b"\r\n") == 0, "%s 应为 LF" % rel
    t = raw.decode("utf-8")
    for tag, old, new in edits:
        c = t.count(old)
        assert c == 1, "[%s %s] 锚点命中 %d 次" % (rel, tag, c)
        t = t.replace(old, new, 1)
        report.append("  ✓ %s %s" % (rel, tag))
    if WRITE:
        p.write_bytes(t.encode("utf-8"))


# ────────────────────────────────────────────────────────────
# D. 护栏
# ────────────────────────────────────────────────────────────
D1_OLD = '    assert \'id="f-analysis-badge"\' in live, "「价格-库存关联分析」被误删（只该删双轴图）"\n'
D1_NEW = (
    '    # 【2026-10-16 反转】原断言是"只该删双轴图、这个小节要留"。当天用户又明确要求\n'
    '    #   **连「价格-库存关联分析」一起去掉** ⇒ 判据反转，并补上"删 DOM 后不许连带伤害"的守卫。\n'
    '    assert \'id="f-analysis-badge"\' not in live, (\n'
    '        "「价格-库存关联分析」小模块仍在（用户 2026-10-16 要求去掉该小模块）"\n'
    '    )\n'
    '    assert \'id="f-analysis-body"\' not in live, "#f-analysis-body 容器未删"\n'
    '    # ★ 删这个 DOM 后**最容易踩的连带伤害**：loadFundAnalysis 在同一个 try 里既写这个\n'
    '    #   body、又渲染 #f-research（研报基本面指标卡）。取值点若没有 null 守卫，\n'
    '    #   `body.innerHTML` 会抛 TypeError ⇒ 被同一个 catch 吞掉 ⇒ **#f-research 静默变空**。\n'
    '    #   ⇒ 判据必须落到"取值点带守卫"，而不是只看"DOM 已删"。\n'
    '    assert "if (body) body.innerHTML" in src and "if (badge) badge.textContent" in src, (\n'
    '        "#f-analysis-body/-badge 已删，但 loadFundAnalysis 的取值点缺 null 守卫 ⇒ "\n'
    '        "#f-research 会被连带打空（两者共用同一个 try/catch）"\n'
    '    )\n'
    '    assert "this._renderResearchSection(d, \'f\')" in src, (\n'
    '        "删小模块不该动 #f-research 的渲染（两者在同一个函数里）"\n'
    '    )\n'
)

D3_APPEND = '''

def test_fund_tab_div_balance():
    """★ 删/加基本面页的卡片后, #tab-fund 的 div 配对必须平衡。

    【背景】2026-10-16 删「价格-库存关联分析」小模块：它是「标题 div + 正文 div」两对，
    而其后紧邻的那个 ` </div>` 是**外层「库存维度明细」区块的闭合** —— 一旦顺手多删它，
    ① 三指标策略系统卡会提前闭合，后面的「板块相关性四栏」「③ 利润」等会被挤出容器
    （本项目 2026-10-08 在国君页踩过同型事故：用户看到「模块平铺到了所有大模块下面」）。
    静态文本断言看不见这种结构错位，只有 div 深度配平能抓。
    """
    import re as _re
    src = _src()
    i = src.find('<div id="tab-fund" class="tab">')
    j = src.find('</main>', i)
    assert i != -1 and j > i, "找不到 #tab-fund 区间"
    seg = _re.sub(r"<!--.*?-->", "", src[i:j], flags=_re.S)
    depth = 0
    k = 0
    while k < len(seg):
        no = seg.find("<div", k)
        nc = seg.find("</div>", k)
        if no == -1 and nc == -1:
            break
        if no != -1 and (nc == -1 or no < nc):
            depth += 1
            k = no + 4
        else:
            depth -= 1
            k = nc + 6
            if depth < 0:
                break
    assert depth == 0, (
        "#tab-fund 内 div 配对不平衡(深度 %d) —— 删/加卡片时漏了或多删了闭合 </div>, "
        "卡片会被挤出 tab 容器" % depth
    )


def test_fund_tab_div_balance_mutation():
    """【变异】给 #tab-fund 多塞一个悬空 </div>, 配平护栏必须红。"""
    import re as _re
    src = _src()
    i = src.find('<div id="tab-fund" class="tab">')
    j = src.find('</main>', i)
    assert i != -1 and j > i, "找不到 #tab-fund 区间"
    seg = _re.sub(r"<!--.*?-->", "", src[i:j], flags=_re.S)
    cut = i + (j - i) // 2
    mutated = src[:cut] + "</div>" + src[cut:]

    def _balanced(t: str) -> bool:
        s = _re.sub(r"<!--.*?-->", "", t[t.find('<div id="tab-fund" class="tab">'):t.find("</main>", t.find('<div id="tab-fund" class="tab">'))], flags=_re.S)
        d = 0
        k = 0
        while k < len(s):
            no = s.find("<div", k)
            nc = s.find("</div>", k)
            if no == -1 and nc == -1:
                break
            if no != -1 and (nc == -1 or no < nc):
                d += 1
                k = no + 4
            else:
                d -= 1
                k = nc + 6
                if d < 0:
                    break
        return d == 0

    assert mutated != src, "变异未生效"
    assert not _balanced(mutated), (
        "多塞一个悬空 </div> 后配平判据仍为 True —— 护栏没起作用"
    )
'''


def patch_d() -> None:
    rel = "tests/test_page_tools_variety_state.py"
    p = ROOT / rel
    raw = p.read_bytes()
    assert raw.count(b"\r\n") == 0, "%s 应为 LF" % rel
    t = raw.decode("utf-8")
    c = t.count(D1_OLD)
    assert c == 1, "[D1] 锚点命中 %d 次" % c
    t = t.replace(D1_OLD, D1_NEW, 1)
    report.append("  ✓ D1 反转断言 + 守卫断言")
    assert "def test_fund_tab_div_balance():" not in t, "D3 护栏已存在（重复追加）"
    t = t.rstrip("\n") + "\n" + D3_APPEND
    report.append("  ✓ D3 #tab-fund div 配平护栏（+ 变异自检）")
    if WRITE:
        p.write_bytes(t.encode("utf-8"))


# ────────────────────────────────────────────────────────────
# E. probe 两条验收脚本（LF）
# ────────────────────────────────────────────────────────────
E1_OLD = ('    "chart-f-pi": ("价格vs仓单双轴", "chart"),\n'
          '    "f-analysis-body": ("价格-库存关联分析", "text"),\n'
          '    "f-inv-badge": ("仓单徽章", "text"),\n'
          '    "f-pi-badge": ("双轴徽章", "text"),\n'
          '    "f-analysis-badge": ("关联徽章", "text"),\n'
          '}\n')
E1_NEW = ('    "f-inv-badge": ("仓单徽章", "text"),\n'
          '}\n'
          '# 【2026-10-16 同步】下面四个容器已按用户要求**先后删除**, 不再属于"应存在"清单:\n'
          '#   · chart-f-pi / f-pi-badge  —— 2026-10-14 删「价格 vs 仓单库存」双轴图\n'
          '#   · f-analysis-body / f-analysis-badge —— 2026-10-16 删「价格-库存关联分析」小节\n'
          '# 保留在注释里是为了说明"它们确实不在了", 而不是漏列。\n')
E2_OLD = '    ck(\'id="f-analysis-badge"\' in clean, "② 「价格-库存关联分析」保留(只删了双轴图)")\n'
E2_NEW = ('    # 【2026-10-16 反转】用户当天追加要求：连「价格-库存关联分析」小模块一起去掉。\n'
          '    ck(\'id="f-analysis-badge"\' not in clean, "② 「价格-库存关联分析」小模块已删(2026-10-16)")\n'
          '    ck(\'id="f-analysis-body"\' not in clean, "② #f-analysis-body 容器已删")\n')


def patch_e() -> None:
    patch_file("probe/_verify_inventory_migration.py", [("E1 迁移清单同步", E1_OLD, E1_NEW)])
    patch_file("probe/_verify_trim3.py", [("E2 判据反转", E2_OLD, E2_NEW)])


if __name__ == "__main__":
    print("=== %s ===" % ("落盘" if WRITE else "空跑（--write 才写）"))
    patch_a()
    patch_file("tests/test_fund_xsection.py", [("B 版本断言", B_OLD, B_NEW)])
    patch_file("tests/test_multi_logic_relocation.py",
               [("C 版本断言", C_OLD, C_NEW), ("C docstring", C_DOC_OLD, C_DOC_NEW)])
    patch_d()
    patch_e()
    print("\n".join(report))
    print("\n✓ 共 %d 处" % len(report))
