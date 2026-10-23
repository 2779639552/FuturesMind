# -*- coding: utf-8 -*-
"""把技术面量化的「⑫ 数据计算面板（含相关系数矩阵）」整卡搬到基本面量化的 A 区，编号 A9。

【为什么放 A9】
A 区是「数据底座（原料/核验工具）」，A8 是「板块相关性」（跨品种价格-库存 R 的横向对比）。
数据计算面板做的是「任意多维度两两相关 + 曲线叠加」，同属相关性核验工具，
且它不产出方向结论（B 区才产出）⇒ 紧跟 A8 之后作为 A9，主题连续。

【为什么整卡搬、不拆】
卡内「多维度曲线 + 相关系数矩阵 + 所选维度明细」共用同一份 /api/quant/compute 响应，
拆开会让两个区块各自请求一次、且 n 的口径说明（逐对标注）只在卡尾图例里。
用户说的「数据计算面板与相关系数矩阵」是同一张卡的两个部分。

【不动的东西】
· 容器 id 一律沿用（`q-compute` / `chart-q-cmp` / `q-cmp-corr` …）——
  JS 与 tests/test_chart_half_width.py 多处按 id 取，改名会静默断链。
· 后端 /api/quant/compute 不动。
"""
from __future__ import annotations

import io
import os
import re

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def close_of(x: str, i: int) -> int:
    depth, started = 0, False
    for m in re.finditer(r"<div\b|</div>", x[i:]):
        if m.group(0).startswith("<div"):
            depth += 1
            started = True
        else:
            depth -= 1
        if started and depth == 0:
            return i + m.end()
    return -1


# ── ① 切出整卡（含卡前那段口径注释） ─────────────────────────────────────
CARD = '<div class="card" id="q-compute">'
ci = s.index(CARD)
ce = close_of(s, ci)
assert ce > ci

cm = s.rindex("<!-- ── ⑫ 数据计算面板", 0, ci)
block = s[cm:ce]
# 卡后若有换行统一吃掉一个，避免留下连续空行
after = ce
while after < len(s) and s[after] in "\r\n":
    after += 1
removed = s[cm:after]
print("整卡字符数:", len(block))

# 原位置留一句迁移说明（技术面页的编号从 ① 到 ⑫，搬走 ⑫ 后要让人知道去哪了）
note = (
    "<!-- ── ⑫ 数据计算面板已于 2026-10-21 整体迁移至「基本面量化 · A9 数据计算面板」──" + CRLF
    + "     原因: 它做的是「任意多维度的两两相关 + 曲线叠加」，属于相关性核验工具，" + CRLF
    + "     与技术面的 K 线/指标/策略回测不是一类；而基本面 A 区已有一块「A8 板块相关性」，" + CRLF
    + "     同主题相邻更好找。容器 id 与 /api/quant/compute 全部沿用，未改后端。 -->" + CRLF
)
s = s[:cm] + note + s[after:]

# ── ② 插到基本面 A 区 A8 之后（f-zone-b 之前） ───────────────────────────
A8 = '<div class="card f-a-block"><h3>A8 板块相关性</h3>'
a8i = s.index(A8)
a8e = close_of(s, a8i)
assert a8e > a8i, "A8 卡闭合定位失败"

# 卡片：加 f-a-block（跟着 A 区整区折叠），标题补编号
newblock = block.replace(
    '<div class="card" id="q-compute">',
    '<div class="card f-a-block" id="q-compute">', 1)
newblock = newblock.replace("<span>数据计算面板</span>",
                            "<span>A9 数据计算面板</span>", 1)
assert "A9 数据计算面板" in newblock
# 卡内注释里的 ⑫ 改成 A9，避免页面上出现第二套编号
newblock = newblock.replace("<!-- ── ⑫ 数据计算面板(2026-09-30 新增) ──",
                            "<!-- ── A9 数据计算面板(2026-09-30 新增; 2026-10-21 由技术面 ⑫ 迁入) ──", 1)

s = s[:a8e] + CRLF + newblock + s[a8e:]

# ── ③ 计数文案：A 区 8 块 → 9 块 ─────────────────────────────────────────
pairs = [
    ('<span class="f-jump" data-zone="a" style="font-size:0.75rem;color:var(--brand,#0071e3);cursor:pointer">A 数据底座 8</span>',
     '<span class="f-jump" data-zone="a" style="font-size:0.75rem;color:var(--brand,#0071e3);cursor:pointer">A 数据底座 9</span>'),
    ('<div class="f-subnav" id="f-subnav"><button type="button" data-zone="a" class="on">A · 数据底座 8</button>',
     '<div class="f-subnav" id="f-subnav"><button type="button" data-zone="a" class="on">A · 数据底座 9</button>'),
    ('<div class="f-zone-n">8 块 · 仓单含 3 个子块</div>',
     '<div class="f-zone-n">9 块 · 仓单含 3 个子块</div>'),
]
for old, new in pairs:
    o = old.replace("\n", CRLF)
    n2 = new.replace("\n", CRLF)
    assert s.count(o) == 1, "计数锚点命中 %d: %s" % (s.count(o), old[:50])
    s = s.replace(o, n2, 1)

# ── ④ 技术面页的两处提示文案 ─────────────────────────────────────────────
pairs2 = [
    ("K 线 / 技术指标 / 策略回测 / 方法论研判（技术面体系） / 数据计算 · 截面排行已移至上方的折叠模块",
     "K 线 / 技术指标 / 策略回测 / 方法论研判（技术面体系） · 截面排行已移至上方折叠模块 · 数据计算面板已移至基本面 A9"),
    ("截面排行 / 方法论研判 / 量化信号 / 数据计算面板。",
     "截面排行 / 方法论研判 / 量化信号（数据计算面板已于 2026-10-21 移至基本面 A9）。"),
]
for old, new in pairs2:
    o = old.replace("\n", CRLF)
    n2 = new.replace("\n", CRLF)
    if s.count(o) == 1:
        s = s.replace(o, n2, 1)
    else:
        print("  (文案未命中，跳过) %s" % old[:40])

# ── ⑤ 索引版本 +1（卡片归属变了：quant −1 / fund +1） ────────────────────
old_ver = s[s.index("_SEC_IDX_VER:"):s.index("_SEC_IDX_VER:") + 30]
m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", s)
assert m, "找不到 _SEC_IDX_VER"
ver = int(m.group(1))
s = s[:m.start()] + "_SEC_IDX_VER: '%d'" % (ver + 1) + s[m.end():]
print("_SEC_IDX_VER: %d -> %d" % (ver, ver + 1))

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok, len", len(s))
