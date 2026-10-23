# -*- coding: utf-8 -*-
"""基本面量化 · 按效果图重构为 A/B 两层（2026-10-21）。

用户要求：「按照之前的效果图进行改造」——`docs/fund-ia-preview-2026-10-21.html` 的
A 数据底座 / B 信号研判 两区 + 统一编号（A1–A8 / B1–B4 / A6.1–6.3）。

【只动结构与标题，不动数据与逻辑】
- 所有容器 id 原样保留（chart-f-*、f-strategy-*、q-signal-*、wt-*）；
- `① 三指标策略系统` → B1(计分) + A1(库存图) + A2(周期) + A8(板块相关)；
- `② 估值+驱动 二维四象` → B2(四象) + A3(基差/期限结构)；
- 仓单三卡的 `①②③` 改 `A6.1/6.2/6.3`，消灭**第二套编号**；
- `⑤ 量化信号研判` → `B4 逐维读数与综合研判`（整块搬，不拆 JS 生成的正文）。

⚠ CRLF：写回用 newline=""。
用法：python probe/_apply_fund_ia.py [--write]
"""
from __future__ import annotations

import io
import re
import sys

P = r"D:\AgentSense\AgentSense\web_template.html"
CRLF = "\r\n"
CLOSE = "</div>"


def close_of(s: str, i: int, limit: int | None = None) -> int:
    """从 i 处 <div 起按 div 配平，返回结束下标（exclusive，即 </div> 之后）。"""
    depth, started = 0, False
    end = limit if limit is not None else len(s)
    for m in re.finditer(r"<div\b|</div>", s[i:end]):
        if m.group(0).startswith("<div"):
            depth += 1
            started = True
        else:
            depth -= 1
        if started and depth == 0:
            return i + m.end()
    return -1


def elem_at(s: str, idx: int) -> tuple[int, int]:
    st = s.index("<div", idx)
    en = close_of(s, st)
    assert en > 0, "元素未闭合 @%d" % st
    return st, en


def inner_of(s: str, idx: int) -> str:
    """idx 起第一个 <div> 的 innerHTML（去掉自身开闭标签）。"""
    st, en = elem_at(s, idx)
    tag_end = s.index(">", st) + 1
    return s[tag_end:en - len(CLOSE)]


def kids(s: str, start: int, end: int) -> list[tuple[int, int]]:
    out, i = [], start
    while i < end:
        m = re.search(r"<div\b", s[i:end])
        if not m:
            break
        st = i + m.start()
        en = close_of(s, st, end)
        if en < 0 or en > end:
            break
        out.append((st, en))
        i = en
    return out


def bal(x: str) -> int:
    return x.count("<div") - x.count(CLOSE)


def main() -> int:
    write = "--write" in sys.argv
    src = io.open(P, "r", encoding="utf-8", newline="").read()
    before = src

    v_start = src.index('<div id="f-detail-view"')
    v_end = close_of(src, v_start)
    assert v_end > 0
    open_tag_end = src.index(">", v_start) + 1
    open_tag = src[v_start:open_tag_end]

    top = kids(src, open_tag_end, v_end)
    assert len(top) == 14, "顶层节点数变了: %d（预期 14）" % len(top)
    C = [src[a:b] for a, b in top]

    ret_bar, tools = C[0], C[1]
    strat, sig, vd = C[2], C[3], C[4]
    profit, research, vr, verdict = C[5], C[6], C[7], C[8]
    wt_tools, wt_exp, wt1, wt2, wt3 = C[9], C[10], C[11], C[12], C[13]

    # ── ① 三指标策略系统：拆 B1 头部 / A1+A2（库存 grid 两列）/ A8（板块）──
    m_inv = strat.index("<!-- ── 库存维度明细")
    m_sec = strat.index("<!-- ── 跨品种: 板块相关性总览")
    b1_head = strat[:m_inv]
    assert bal(b1_head) == 1, "B1 头部配平异常: %+d" % bal(b1_head)
    b1 = b1_head + CLOSE

    inv_inner = inner_of(strat, m_inv)
    g = inv_inner.index('<div class="grid grid-2"')
    gst, gen = elem_at(inv_inner, g)
    cols = kids(inv_inner, gst + inv_inner[gst:].index(">") + 1, gen)
    assert len(cols) == 2, "库存 grid 不是两列: %d" % len(cols)
    col_l = inner_of(inv_inner[cols[0][0]:cols[0][1]], 0)
    col_r = inner_of(inv_inner[cols[1][0]:cols[1][1]], 0)
    assert "chart-f-soc" in col_l and "f-cycle-body" in col_r

    def drop_title(x: str) -> str:
        return re.sub(r'<div style="font-size:0\.8125rem;font-weight:500;margin-bottom:8px">.*?</div>',
                      "", x, count=1, flags=re.S)

    col_l, col_r = drop_title(col_l), drop_title(col_r)
    assert bal(col_l) == 0 and bal(col_r) == 0

    sector_body = drop_title(inner_of(strat, m_sec))
    assert "f-sector-panel" in sector_body and bal(sector_body) == 0

    # ── ② 四象：拆 B2（头部+矩阵）/ A3（估值轴明细）──
    m_matrix = vd.index("<!-- 全池矩阵气泡图")
    m_basis = vd.index("<!-- ── 估值轴明细")
    b2_head = vd[:m_matrix]
    assert bal(b2_head) == 1, "B2 头部配平异常: %+d" % bal(b2_head)
    b2_matrix = vd[m_matrix:m_basis]
    assert bal(b2_matrix) == 0
    b2 = b2_head + b2_matrix + CLOSE

    a3_inner = inner_of(vd, m_basis)
    assert "chart-f-basis" in a3_inner and "chart-f-term" in a3_inner and bal(a3_inner) == 0

    # ── 改名 ──
    b1 = b1.replace("<h3>① 三指标策略系统 ", "<h3>B1 三指标计分 ")
    b2 = b2.replace("<h3>② 估值 + 驱动 二维四象 ", "<h3>B2 估值 × 驱动 四象定位 ")
    b3 = verdict.replace("<h3>⑥ 多空逻辑体系研判 ", "<h3>B3 体系方向票 ")
    b4 = sig.replace("<h3>⑤ 量化信号研判 ", "<h3>B4 逐维读数与综合研判 ")
    a4 = profit.replace("<h3>③ 加工 / 进口利润口径 ", "<h3>A4 加工 / 进口利润口径 ")
    a5 = vr.replace("<h3>④ 虚实盘比与逼仓识别 ", "<h3>A5 虚实盘比与逼仓识别 ")
    wt1 = wt1.replace("<h3>① 仓单总览 ", "<h3>A6.1 仓单总览 ")
    wt2 = wt2.replace("<h3>② 品种仓单细则", "<h3>A6.2 品种仓单细则")
    wt3 = wt3.replace("<h3>③ 仓单历史走势 ", "<h3>A6.3 仓单历史走势 ")
    for t, n in ((b1, "B1 三指标计分"), (b2, "B2 估值 × 驱动 四象定位"), (b3, "B3 体系方向票"),
                 (b4, "B4 逐维读数与综合研判"), (a4, "A4 加工"), (a5, "A5 虚实"),
                 (wt1, "A6.1"), (wt2, "A6.2"), (wt3, "A6.3")):
        assert n in t, "标题未替换: " + n

    # ── 面包屑：纯文本 → 可点分区锚点 ──
    m_crumb = re.search(r'<span style="font-size:0\.75rem;color:var\(--text-muted\)">单品种明细 · [^<]*</span>',
                        ret_bar)
    assert m_crumb, "面包屑锚点未命中"
    new_crumb = (
        '<span style="font-size:0.75rem;color:var(--text-muted)">单品种明细</span>'
        '<span style="font-size:0.75rem;color:var(--border)">·</span>'
        '<span class="f-jump" data-zone="a" style="font-size:0.75rem;color:var(--brand,#0071e3);cursor:pointer">A 数据底座 8</span>'
        '<span style="font-size:0.75rem;color:var(--border)">·</span>'
        '<span class="f-jump" data-zone="b" style="font-size:0.75rem;color:var(--purple,#ff375f);cursor:pointer">B 信号研判 4</span>'
    )
    ret_bar = ret_bar[:m_crumb.start()] + new_crumb + ret_bar[m_crumb.end():]

    def band(kind: str, letter: str, title: str, desc: str, cnt: str,
             jump_to: str, jump_txt: str) -> str:
        return (
            f'<div class="f-zone {kind}" id="f-zone-{kind}">'
            f'<div class="f-zone-l">{letter}</div>'
            f'<div class="f-zone-t"><b>{title}</b><span>{desc}</span></div>'
            f'<div class="f-zone-n">{cnt}</div>'
            f'<div class="f-jump" data-zone="{jump_to}" '
            f'style="font-size:0.75rem;color:var(--brand,#0071e3);cursor:pointer;white-space:nowrap">'
            f'{jump_txt}</div></div>')

    band_a = band("a", "A", "A · 数据底座",
                  "原始观测与计算值，用来核验结论；本区不含方向判断。", "8 块 · 仓单含 3 个子块",
                  "b", "跳到结论 ↓")
    band_a6 = ('<div class="f-zone sub" id="f-zone-a6">'
               '<div class="f-zone-t"><b>A6 · 仓单</b>'
               '<span>本块的选择器（品种范围 / 单位 / 日期）只作用于仓单，与上方页面级「品种」不同。</span>'
               '</div></div>')
    band_b = band("b", "B", "B · 信号与研判",
                  "由 A 区数据推导，供决策；同一结论只出现一次。", "4 块 · 全部默认展开",
                  "a", "回到数据 ↑")

    subnav = ('<div class="f-subnav" id="f-subnav">'
              '<button type="button" data-zone="a" class="on">A · 数据底座 8</button>'
              '<button type="button" data-zone="b">B · 信号研判 4</button></div>')

    a1 = ('<div class="card">'
          '<h3>A1 库存：社会库存季节性 · 交易所仓单走势 '
          '<span class="badge badge-info" id="f-inv-badge"></span></h3>'
          + col_l + CLOSE)
    a2 = '<div class="card"><h3>A2 库存周期四象限</h3>' + col_r + CLOSE
    a3 = ('<div class="card"><h3>A3 基差与期限结构</h3>'
          '<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:10px;line-height:1.7">'
          '估值轴的原料：基差率走势、现货/期货价对照、跨月合约曲线。B2 的四象定位读的就是这一轴。</div>'
          + a3_inner + CLOSE)
    a8 = '<div class="card"><h3>A8 板块相关性</h3>' + sector_body + CLOSE

    parts = [ret_bar, tools, subnav, band_a,
             a1, a2, a3, a4, a5,
             band_a6, wt_tools, wt_exp, wt1, wt2, wt3,
             research,
             a8,
             band_b, b1, b2, b3, b4]
    for i, x in enumerate(parts):
        assert x.strip(), "第 %d 段为空" % i
    new_inner = CRLF.join(parts)
    assert bal(new_inner) == 0, "新结构 div 不配平: %+d" % bal(new_inner)

    new_view = open_tag + new_inner + CLOSE
    assert bal(new_view) == 0
    out = src[:v_start] + new_view + src[v_end:]

    # ── 配套：索引版本 / 研报卡编号 / 跳转方法 / 事件绑定 / CSS ──
    assert out.count("_SEC_IDX_VER: '31'") == 1
    out = out.replace("_SEC_IDX_VER: '31'", "_SEC_IDX_VER: '32'", 1)

    old_res = '<h3 style="margin:0">研报基本面指标 '
    assert out.count(old_res) == 1
    out = out.replace(old_res, '<h3 style="margin:0">A7 研报基本面指标 ', 1)

    anchor = "// 【功能】渲染库存周期四象限文字卡"
    assert out.count(anchor) == 1
    jump = (
        "// 【功能】A/B 分区跳转（2026-10-21 信息架构重构）\n"
        "// 【为什么不用 href=\"#id\"】会改地址栏并触发浏览器默认滚动，返回时残留 hash。\n"
        "fJumpZone(z) {\n"
        " const el = document.getElementById('f-zone-' + z);\n"
        " if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });\n"
        " document.querySelectorAll('#f-subnav button').forEach(b => {\n"
        "  b.classList.toggle('on', b.dataset.zone === z);\n"
        " });\n"
        "},\n\n")
    out = out.replace(anchor, jump + anchor, 1)

    hook = "document.addEventListener('DOMContentLoaded', () => {"
    assert out.count(hook) == 1, "DOMContentLoaded 钩子命中 %d" % out.count(hook)
    bind = (hook + "\n // 【2026-10-21】A/B 分区跳转（吸顶切换 + 面包屑锚点 + 分区条箭头）\n"
            " document.addEventListener('click', (e) => {\n"
            "  const t = e.target.closest && e.target.closest('.f-jump, #f-subnav button');\n"
            "  if (!t || !t.dataset || !t.dataset.zone) return;\n"
            "  e.preventDefault();\n"
            "  if (window.App && App.fJumpZone) App.fJumpZone(t.dataset.zone);\n"
            " });")
    out = out.replace(hook, bind, 1)

    # 主样式表的收尾（模板里有两个 </style>，第一个是主表）
    css_anchor = "\n</style>"
    assert out.count(css_anchor) >= 1, "CSS 锚点未命中"
    _first_style = out.index(css_anchor)
    css = (
        "/* 【2026-10-21 信息架构 A/B 分区】 */\n"
        ".f-zone{display:flex;align-items:center;gap:14px;padding:13px 16px;margin:22px 0 12px;\n"
        " border:1px solid var(--border-light,#e8e8ed);border-radius:10px;background:var(--bg-tile,#f5f5f7)}\n"
        ".f-zone.sub{margin:14px 0 10px;padding:9px 14px}\n"
        ".f-zone-l{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;font-weight:700;\n"
        " font-size:0.9375rem;background:rgba(0,113,227,.10);color:var(--brand,#0071e3);\n"
        " border:1px solid rgba(0,113,227,.30);flex:0 0 auto}\n"
        ".f-zone.b .f-zone-l{background:rgba(255,55,95,.10);color:var(--purple,#ff375f);\n"
        " border-color:rgba(255,55,95,.30)}\n"
        ".f-zone-t{flex:1;min-width:0}\n"
        ".f-zone-t b{display:block;font-size:0.875rem;letter-spacing:-.005em}\n"
        ".f-zone-t span{display:block;font-size:0.75rem;color:var(--text-muted);margin-top:1px;line-height:1.6}\n"
        ".f-zone-n{font-size:0.75rem;color:var(--text-muted);white-space:nowrap}\n"
        ".f-subnav{display:inline-flex;gap:4px;padding:3px;margin:0 0 12px;\n"
        " border:1px solid var(--border-light,#e8e8ed);border-radius:999px}\n"
        ".f-subnav button{border:0;background:transparent;color:var(--text-secondary);font:inherit;\n"
        " font-size:0.75rem;padding:4px 12px;border-radius:999px;cursor:pointer}\n"
        ".f-subnav button.on{background:var(--bg-tile,#f5f5f7);color:var(--text)}\n"
        "[data-theme=dark] .f-subnav button.on{background:#3a3a3c}\n"
    )
    out = out[:_first_style] + "\n" + css + out[_first_style:]

    # 注入片段用 \n 书写 ⇒ 统一归一为 CRLF
    out = out.replace("\r\n", "\n").replace("\n", CRLF)
    assert out != before
    assert out.count("\n") == out.count(CRLF), "CRLF 被破坏"

    if not write:
        nv_start = out.index('<div id="f-detail-view"')
        nv_end = close_of(out, nv_start)
        nv_open = out.index(">", nv_start) + 1
        print("新视图 span:", nv_start, nv_end, " 配平:", bal(out[nv_start:nv_end]))
        print("── 目标结构 ──")
        for a, b in kids(out, nv_open, nv_end):
            seg = out[a:b]
            h = re.search(r"<h3[^>]*>(.*?)</h3>", seg, re.S)
            if h:
                title = re.sub(r"<[^>]+>", "", h.group(1)).strip()[:40]
            elif "f-zone" in seg[:60]:
                title = "【分区条】" + re.sub(r"<[^>]+>", " ", seg)[:34].strip()
            else:
                title = seg[:38].replace("\n", " ")
            print("   ", title)
        print("\n(dry-run：未写盘；加 --write 落盘)")
        return 0

    io.open(P, "w", encoding="utf-8", newline="").write(out)
    print("written ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
