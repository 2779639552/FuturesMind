# -*- coding: utf-8 -*-
"""基本面 A/B 分区改成「气泡切换」（研报页那种胶囊页签，默认展开 A）。

【用户要求】「将基本面中的 AB 两个部分做成研报页那种气泡的形式，
即不点击对应气泡的情况下不显示」；默认显示哪个 → 用户选「默认展开 A 区」。

【交互】
· 点「A · 数据底座」→ 只显示 A 区；点「B · 信号研判」→ 只显示 B 区；
· 点当前已激活的气泡 → 两区都收起（再点一次恢复）；
· 吸顶气泡、面包屑「A 数据底座 / B 信号研判」、分区条上的「跳到结论↓ / 回到数据↑」
  共用同一入口 `fJumpZone`，所以改它一处，全部入口行为一致。

【为什么复用 fJumpZone 而不新写】
事件委托早就统一成 `.f-jump, #f-subnav button` → `App.fJumpZone(zone)`
（见 DOMContentLoaded 里的 click 监听）。改实现即可，不需要动三处 DOM。

【不改】不重建 DOM、不重新请求数据 —— 只切 display，与研报页二级页签同一套做法。
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


def rep(old: str, new: str, label: str):
    global s
    old = old.replace("\n", CRLF)
    new = new.replace("\n", CRLF)
    n = s.count(old)
    assert n == 1, "%s 命中 %d 次:\n%s" % (label, n, old[:120])
    s = s.replace(old, new, 1)


# ── ① 气泡样式：放大到研报页那种胶囊，选中反色 ──────────────────────────
rep(""".f-subnav{display:inline-flex;gap:4px;padding:3px;margin:0 0 12px;
 border:1px solid var(--border-light,#e8e8ed);border-radius:999px}
.f-subnav button{border:0;background:transparent;color:var(--text-secondary);font:inherit;
 font-size:0.75rem;padding:4px 12px;border-radius:999px;cursor:pointer}
.f-subnav button.on{background:var(--bg-tile,#f5f5f7);color:var(--text)}
[data-theme=dark] .f-subnav button.on{background:#3a3a3c}""",
    """.f-subnav{display:inline-flex;gap:6px;padding:4px;margin:0 0 12px;
 border:1px solid var(--border-light,#e8e8ed);border-radius:999px}
/* 【2026-10-22】与研报页二级页签同一视觉：胶囊 + 选中反色（用户要的"气泡"）。
   原来只有 0.75rem 灰底，更像"小开关"而不是页签。 */
.f-subnav button{border:0;background:transparent;color:var(--text-secondary);font:inherit;
 font-size:0.8125rem;padding:7px 18px;border-radius:999px;cursor:pointer;
 transition:background .15s ease,color .15s ease}
.f-subnav button:hover{color:var(--text)}
.f-subnav button.on{background:var(--text);color:var(--bg);font-weight:500}
[data-theme=dark] .f-subnav button.on{background:#f5f5f7;color:#1d1d1f}""",
    "bubble css")

# ── ② B 区四张卡打 f-b-block（供显隐，和 A 区 f-a-block 对称） ──────────
zb = s.index('<div class="f-zone b" id="f-zone-b"')
zb_end = close_of(s, zb)
v_start = s.index('<div id="f-detail-view"')
v_end = close_of(s, v_start)
assert zb_end > zb and v_end > v_start

i = zb_end
marked = 0
while i < v_end:
    m = re.search(r'<div class="card([^"]*)"', s[i:v_end])
    if not m:
        break
    tag_start = i + m.start()
    tag_end = i + m.end()
    if tag_start >= v_end:
        break
    cls = m.group(1)
    if "f-b-block" not in cls:
        s = s[:tag_start] + '<div class="card%s f-b-block"' % cls + s[tag_end:]
        v_end += len(" f-b-block")
        i = tag_end + len(" f-b-block")
        marked += 1
    else:
        i = tag_end
    # 跳过这张卡，继续找下一张
    card_close = close_of(s, tag_start)
    assert card_close > tag_start, "B 区卡片闭合定位失败"
    i = card_close
print("B 区打标卡片数:", marked)
assert marked == 4, "B 区应有 4 张卡，实际标记 %d 张" % marked

# ── ③ JS：状态 + 应用函数；fJumpZone 改为切换 ───────────────────────────
rep("""// 【功能】A/B 分区跳转（2026-10-21 信息架构重构）
// 【为什么不用 href="#id"】会改地址栏并触发浏览器默认滚动，返回时残留 hash。
fJumpZone(z) {
 const el = document.getElementById('f-zone-' + z);
 if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
 document.querySelectorAll('#f-subnav button').forEach(b => {
  b.classList.toggle('on', b.dataset.zone === z);
 });
},""",
    """// 【功能】A/B 气泡切换（2026-10-22 由"跳转锚点"改成"显示/隐藏"）
// 【交互】点 A → 只显示 A 区；点 B → 只显示 B 区；点当前已选中的 → 两区都收起。
// 【为什么复用这个方法名】吸顶气泡 / 面包屑 / 分区条箭头三处入口早就在
//   `.f-jump, #f-subnav button` 的同一个委托里调 fJumpZone，改实现即可，不动 DOM。
// 【不改 DOM、不重新请求】只切 display，与研报页二级页签同一套做法。
fJumpZone(z) {
 const next = (this._fZone === z) ? '' : z;   // 再点一次 = 收起
 this._fZone = next;
 this._applyFZone();
 window.scrollTo({ top: 0, behavior: 'smooth' });
},

// 【功能】按 _fZone 应用 A/B 两区的显隐 + 气泡高亮
// 【状态】'a' 只看数据 / 'b' 只看结论 / '' 两区都收起
_applyFZone() {
 const z = this._fZone || 'a';
 const aBlocks = document.querySelectorAll('#f-detail-view .f-a-block');
 const bBlocks = document.querySelectorAll('#f-detail-view .f-b-block');
 // 切到 A 时先解除"整区折叠"：否则气泡选中了 A、里面却是被折叠的空壳。
 if (z === 'a') {
  this.fFoldA(false);
 } else {
  aBlocks.forEach(el => { el.style.display = 'none'; });
 }
 bBlocks.forEach(el => { el.style.display = (z === 'b') ? '' : 'none'; });
 // 分区条跟随（它们自身不带 f-a-block/f-b-block）
 const za = document.getElementById('f-zone-a');
 const zb = document.getElementById('f-zone-b');
 if (za) za.style.display = (z === 'a') ? '' : 'none';
 if (zb) zb.style.display = (z === 'b') ? '' : 'none';
 document.querySelectorAll('#f-subnav button').forEach(b => {
  b.classList.toggle('on', b.dataset.zone === z);
 });
 // 由隐藏变可见后必须 resize：隐藏期间容器宽高为 0，ECharts 不会自己复位
 setTimeout(() => { if (this._resizeAllCharts) this._resizeAllCharts(); }, 30);
},""",
    "fJumpZone -> switch")

# ── ④ 状态初值与进明细时应用 ────────────────────────────────────────────
rep(""" _fundSym() {
 const el = document.getElementById('f-variety');
 return (el?.value || '').toUpperCase();
 },""",
    """ _fundSym() {
 const el = document.getElementById('f-variety');
 return (el?.value || '').toUpperCase();
 },

 // 【状态】A/B 气泡当前选中：'a'（默认，用户选定）/ 'b' / ''（都收起）
 _fZone: 'a',""",
    "_fZone state")

# ⚠ fOpenDetail 内部缩进是 2 空格（与文件里其它方法不同，历史遗留），锚点必须照抄
rep("""  this.loadFund();
  // 【2026-10-21】同 qOpenDetail：仓单/利润等图在总览态（detail 隐藏）就已 init，
  //   进明细时必须补 resize，否则画布停在 100px。
  setTimeout(() => this._resizeAllCharts(), 0);""",
    """  this.loadFund();
  // 【2026-10-22】进明细即按气泡状态铺一次显隐（默认只显示 A 区）。
  //   放在 loadFund 之后：卡内数据先就位，再切显示，避免"先显示空白再填充"的闪动。
  this._applyFZone();
  // 【2026-10-21】同 qOpenDetail：仓单/利润等图在总览态（detail 隐藏）就已 init，
  //   进明细时必须补 resize，否则画布停在 100px。
  setTimeout(() => this._resizeAllCharts(), 0);""",
    "fOpenDetail apply")

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok, len", len(s))
