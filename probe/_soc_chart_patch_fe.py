# -*- coding: utf-8 -*-
"""前端补丁：⑨→④ 卡「仓单库存 · 社会库存双口径」图（2026-10-16）。

【改动 4 处】
  F1  图表容器高度 300px → 360px（上下两栏需要更多纵向空间）
  F2  `loadFundInventory` 把 `d.social` 一并传给渲染函数（**根因就在这一行**）
  F3  `_renderFundInventory` 整段替换（单口径 → 三形态：双口径两栏 / 只有仓单 / 只有社会库存）
  F4  上方注释同步（旧注释写的是"库存折线 + 日变化柱, 双轴"，已过时）

【不幂等】重跑会重复替换 ⇒ 开头检测标记并拒绝。

★ 两个本轮学到的细节（都在下面用到了）：
  · 用**花括号配平**取方法体时结束符是 `}` 本身，逗号在 span **之外** ⇒ 替换段若自带 `},`
    结尾会变成 `},,`（整页 JS 语法崩）⇒ 统一用 `trim_trailing_comma()`。
  · 方法头**前导空格不固定**且**异步方法带 `async ` 前缀** ⇒ 正则要容这两点。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
P = ROOT / "probe"
MARK = "社会库存(阶梯)"


def method_span(txt: str, name: str) -> tuple[int, int]:
    """花括号配平取方法体 [start,end)。"""
    m = re.search(r"\n[ \t]*(?:async[ \t]+)?" + re.escape(name) + r"\s*\(", txt)
    if not m:
        raise AssertionError("找不到方法: " + name)
    i = m.start() + 1
    b = txt.index("{", m.end())
    d, k = 0, b
    while k < len(txt):
        ch = txt[k]
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return i, k + 1
        k += 1
    raise AssertionError("未配平: " + name)


def trim_trailing_comma(seg: str) -> str:
    """去掉对象方法段末尾 `},` 里的逗号（逗号在 span 之外，否则会变成 `},,`）。"""
    t = seg.rstrip()
    return t[:-1] if t.endswith("},") else seg


OLD_COMMENT = ''' // 【功能】渲染仓单库存图(库存折线 + 日变化柱, 双轴)
 // 【输入】s: {ok, rows:[{date,qty,change}], latest, chg, pct_rank, source, error}
 // 【渲染】#chart-f-inv
 // 【后端】无'''

NEW_COMMENT = '''// 【功能】渲染「仓单库存 · 社会库存双口径」图（2026-10-16 由单口径扩为双口径）
// 【输入】s  = 交易所注册仓单块；so = 国君社会(工厂)库存块（**两者都要传**，各自 rows 见下）
// 【渲染】#chart-f-inv；【后端】无（数据已随 /api/quant/inventory 取回）
// 【★ 本次修复的根因】此前 `loadFundInventory` 只调 `_renderFundInventory(d.inventory)`，
//   而 `_inv_social_block` 当时**也不回传序列** ⇒ 标题写着"双口径"、图上却只有仓单。
//   现在后端补了 `social.rows`，这里把两者一起画（形态与降级规则见方法体注释）。'''


def main(write: bool) -> int:
    raw = TPL.read_bytes()
    eol = "\r\n" if raw.count(b"\r\n") else "\n"
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2
    newjs = trim_trailing_comma((P / "_soc_chart_fe.js.txt").read_text(encoding="utf-8").rstrip("\n"))

    a, b = method_span(txt, "_renderFundInventory")
    edits = [
        ('<div class="chart-container" id="chart-f-inv" style="height:300px"></div>',
         '<div class="chart-container" id="chart-f-inv" style="height:360px"></div>',
         1, "F1 容器高度 300→360(上下两栏需要高度)"),
        (" this._renderFundInventory(d.inventory || {});",
         " // 【2026-10-16】把社会库存块一并交给渲染函数 —— 这就是「图上只有仓单」的根因所在。\n"
         " this._renderFundInventory(d.inventory || {}, d.social || {});",
         1, "F2 loadFundInventory 传 social"),
        (txt[a:b], newjs, 1, "F3 _renderFundInventory 整段替换"),
        (OLD_COMMENT, NEW_COMMENT, 1, "F4 注释同步"),
    ]
    ok = True
    new = txt
    for old, rep, n, tag in edits:
        # ⚠ 已经含 CRLF 的切片（如"从模板里现取的"方法体）**不要再替换一次** ——
        #   否则 \r\n 会被再扩成 \r\r\n，锚点永远命中 0（本轮实测踩到）。
        o = old if "\r\n" in old else old.replace("\n", eol)
        r = rep if "\r\n" in rep else rep.replace("\n", eol)
        got = new.count(o)
        print("  %s %-38s 命中 %d(期望 %d)" % ("OK " if got == n else "★  ", tag, got, n))
        if got != n:
            ok = False
            continue
        new = new.replace(o, r, 1)
    if not ok:
        print("  ⇒ 有锚点未命中, **未写盘**")
        return 1

    # ── 写盘前自检（上一件事的教训：锚点打偏会静默吃掉内容）──────────────────
    def _depth(t: str) -> int:
        d = 0
        for m in re.finditer(r"<div\b|</div>", t):
            d += 1 if m.group(0) == "<div" else -1
        return d

    for key_id in ('id="tab-fund"', 'id="f-overview-view"', 'id="f-detail-view"',
                   'id="chart-f-inv"', 'id="f-inv-badge"', 'id="f-cycle-body"',
                   'id="chart-q-kline"', 'id="tab-quant"'):
        x, y = txt.count(key_id), new.count(key_id)
        if x != y:
            print("  ★ 自检失败: %s %d → %d ⇒ 拒绝写盘" % (key_id, x, y))
            return 1
    d0, d1 = _depth(txt), _depth(new)
    if d0 != d1:
        print("  ★ 自检失败: div 深度 %+d → %+d ⇒ 拒绝写盘" % (d0, d1))
        return 1
    dl = len(new) - len(txt)
    if not (500 <= dl <= 20000):
        print("  ★ 自检失败: 长度变化 %+d ⇒ 拒绝写盘" % dl)
        return 1
    # 替换段本身必须语法自洽（尾逗号已去、括号配平）
    assert newjs.rstrip().endswith("}"), "新方法段结尾应为 `}`（尾逗号应已去除）"
    print("  ✔ 写盘前自检: 关键 id 计数不变 / div 深度 %+d 不变 / 长度 %+d" % (d0, dl))
    if not write:
        print("  ⇒ 空跑通过。加 --write 落盘。")
        return 0
    TPL.write_bytes(new.encode("utf-8"))
    bb = TPL.read_bytes()
    assert bb.count(b"\r\n") > 0 and (bb.count(b"\n") - bb.count(b"\r\n")) == 0, "模板 EOL 必须 CRLF"
    print("  落盘 md5=%s (CRLF 保持)" % hashlib.md5(bb).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
