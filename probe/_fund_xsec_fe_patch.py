"""基本面截面表 —— 前端一次性补丁（2026-10-16）。

【改动面】
  W1 tab-fund 拆两视图：新 #f-overview-view(默认可见) + #f-detail-view(display:none)
  W2 tab-fund 收尾补 </div> 关掉 f-detail-view
  W3 switchTab('fund') 改为按视图态分派(镜像 quant 页写法)
  W4 新增 App._fPendingSym / _fRkData 两个状态槽
  W5 _loadFundMeta 采用 pending 品种(否则点行选的品种被静默换成默认品种)
  W6 _SEC_IDX_VER 27 → 28(tab 内卡片有增删)
  W7 顺带修 ① 卡基差率的 **100× 显示错误**(字段是比率, 原写 toFixed(2)+'%')
  W8 插入 ⑨ 静态 JS 方法(loadFundRanking/_renderFundRanking/_fExpandContracts/fOpenDetail/fBackOverview)

【★ EOL】web_template.html = CRLF; 本脚本归一为 LF 匹配, 写回还原 CRLF 并断言。
【★ 不幂等】"插入"类改动重跑会重复插入 ⇒ 开头检测标记即拒绝重跑。

用法: python probe/_fund_xsec_fe_patch.py [--write]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 【★ 这里曾写反】原写成「只保留**不含** CRLF 的行」⇒ 把 69 行卡片 HTML 全丢了,
#   只剩注释正文 ⇒ #f-overview-view 变成空壳(而 W1 锚点照样命中 1 次, 补丁"全绿")。
#   正解: 对**每一行**剥掉行首的 CRLF 标记(该标记是文件里用来表达换行约定的记号)。
CARD = "\n".join(
    (ln[4:] if ln.startswith("CRLF") else ln)
    for ln in (ROOT / "probe" / "_fund_xsec_card.html.txt").read_text(encoding="utf-8").split("\n")
).rstrip("\n")
assert 'id="f-rk-table"' in CARD and 'id="f-rk-rows"' in CARD, "卡片提取失败(空壳) —— 拒绝落盘"
JS = (ROOT / "probe" / "_fund_xsec_js.js.txt").read_text(encoding="utf-8").rstrip("\n")

MARKERS = {"web_template.html": "f-rk-rows"}

# ── W1 视图包裹 ────────────────────────────────────────────────────────────
W1_OLD = '''<div id="tab-fund" class="tab">
 <div id="tab-fund-tools" class="page-tools">'''

W1_NEW = '''<div id="tab-fund" class="tab">
<!-- ════════════════════════════════════════════════════════════════════════
     【2026-10-16 结构改造】基本面量化页改为**两视图**(与技术面量化页同构):
       · #f-overview-view  —— 默认可见: 全品种基本面截面表(跨品种, 全池)
       · #f-detail-view    —— 默认隐藏: **原有全部内容**(品种控制条 + 单品种卡 +
                              仓单分析子模块), 点截面表某一行进来(App.fOpenDetail)
     进入方式: 截面表点行 → App.fOpenDetail(code); 返回: 顶部「← 返回全品种总览」。
     ⚠ 两视图用 display 互斥(与 q-overview-view/q-detail-view 同一机制);
       不动任何一个既有容器 id(f-*/q-signal-*/wt-*), 也不动仓单子模块的内部结构。
     ════════════════════════════════════════════════════════════════════════ -->
<div id="f-overview-view">
''' + CARD + '''
</div>

<div id="f-detail-view" style="display:none">
<!-- 明细视图顶部: 返回按钮(进详情的唯一退路, 与技术面 #q-detail-view 顶部同形) -->
<div class="card" style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
 <button class="btn btn-sm btn-outline" onclick="App.fBackOverview()">← 返回全品种总览</button>
 <span style="font-size:0.75rem;color:var(--text-muted)">单品种明细 · 三指标策略 / 估值驱动四象 / 利润 / 虚实盘比 / 多空逻辑体系研判 / 仓单分析</span>
</div>
 <div id="tab-fund-tools" class="page-tools">'''

# ── W2 收尾 ────────────────────────────────────────────────────────────────
W2_OLD = '''<div class="chart-container" id="wt-h-wh" style="height:360px"></div>
 </div>
 </div>
 </div>
</div>'''

W2_NEW = '''<div class="chart-container" id="wt-h-wh" style="height:360px"></div>
 </div>
 </div>
 </div>
 </div><!-- /#f-detail-view -->
</div>'''

# ── W3 switchTab ───────────────────────────────────────────────────────────
W3_OLD = """ if (t === 'fund') { this.loadFund(); this.loadWarrant(); } // 基本面量化(2026-09-29 新增: 三指标策略/估值驱动四象/加工进口利润/虚实盘比); 【2026-10-14】仓单分析已并入本页 ⇒ 一并加载 loadWarrant()(原独立 tab-warrant)"""

W3_NEW = """ if (t === 'fund') {
  // 【2026-10-16 改造】基本面页改为「全品种总览 → 点进单品种明细」两视图(与技术面同构):
  //   默认落在总览(loadFundRanking); 若用户上次停在明细(#f-overview-view 被隐藏),
  //   则维持明细并载入该品种 —— 切走再切回不丢上下文(与 quant 页同一判据)。
  const fov = document.getElementById('f-overview-view');
  if (fov && fov.style.display === 'none') this.loadFund();
  else this.loadFundRanking();
  this.loadWarrant();
 } // 基本面量化(2026-09-29 新增: 三指标策略/估值驱动四象/加工进口利润/虚实盘比); 【2026-10-14】仓单分析已并入本页 ⇒ 一并加载 loadWarrant()(原独立 tab-warrant)"""

# ── W4 状态槽 ──────────────────────────────────────────────────────────────
W4_OLD = """ _qovPendingSym: null,   // 【变量】"点行后要选中的品种"，交给 _loadQuantMeta 首次填充时采用"""

W4_NEW = """ _qovPendingSym: null,   // 【变量】"点行后要选中的品种"，交给 _loadQuantMeta 首次填充时采用
 _fPendingSym: null,     // 【变量】基本面页同款槽位(默认落在总览 ⇒ #f-variety 未初始化时用)
 _fRkData: null,         // 【变量】基本面截面表最近一次响应(切排序维度/返回总览时复用)"""

# ── W5 pending 采用 ────────────────────────────────────────────────────────
W5_OLD = """ sel.value = (pref && (d.varieties || []).some(x => x.code === pref.code))
 ? pref.code : (d.varieties?.[0]?.code || '');"""

W5_NEW = """ // 【2026-10-16】基本面截面表点行进来的品种优先：本函数有 options.length 守卫 ⇒ 只跑一次,
 //   若仍按默认值选, 用户点的那个品种会被**静默替换**(与技术面 _qovPendingSym 同一坑)。
 const _fValid = c => (d.varieties || []).some(x => x.code === c);
 const _fWant = this._fPendingSym;
 this._fPendingSym = null;
 if (_fWant && !_fValid(_fWant)) {
  const o = document.createElement('option');
  o.value = _fWant; o.textContent = _fWant;
  sel.appendChild(o);
 }
 sel.value = _fWant || ((pref && _fValid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));"""

# ── W6 版本号 ──────────────────────────────────────────────────────────────
W6_OLD = "_SEC_IDX_VER: '27',"
W6_NEW = "_SEC_IDX_VER: '28',"

# ── W7 基差率 100× 显示错误 ────────────────────────────────────────────────
W7_OLD = """Number(bs.rate).toFixed(2) + '%'"""
W7_NEW = """(Number(bs.rate) * 100).toFixed(2) + '%'"""

# ── W8 JS 插入 ─────────────────────────────────────────────────────────────
W8_ANCHOR = " // 【功能】⑤ 加载量化信号(库存+基差 / 反转三信号 / 期限结构 → 综合研判; + 利润校验维度)"


def patch(name: str, ops: list, write: bool) -> bool:
    p = ROOT / name
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    bare = raw.count(b"\n") - crlf
    assert crlf == 0 or bare == 0, "%s EOL 混合" % name
    eol = "\r\n" if crlf else "\n"
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    ok = True
    print("  ── %s (%s) ──" % (name, "CRLF" if crlf else "LF"))
    for old, new, want, why in ops:
        got = txt.count(old)
        print("    %s %-44s 命中 %d (期望 %d)" % ("OK " if got == want else "★  ", why, got, want))
        if got != want:
            ok = False
            continue
        txt = txt.replace(old, new, want)
    if not ok:
        print("    ⇒ 有锚点未命中, **本文件未写入**")
        return False
    if write:
        p.write_bytes(txt.replace("\n", eol).encode("utf-8"))
        chk = p.read_bytes()
        c2, b2 = chk.count(b"\r\n"), chk.count(b"\n") - chk.count(b"\r\n")
        assert (c2 > 0 and b2 == 0) if crlf else (c2 == 0), "写回后 EOL 破坏"
        print("    ✔ 已写入 (%d 字节, EOL=%s 保持)" % (len(chk), "CRLF" if crlf else "LF"))
    else:
        print("    ✔ 空跑通过")
    return True


def main() -> int:
    write = "--write" in sys.argv
    for name, mark in MARKERS.items():
        if mark in (ROOT / name).read_bytes().decode("utf-8", "ignore"):
            print("★ %s 已含标记 %r —— 本脚本不幂等, 请勿重跑。" % (name, mark))
            return 2
    print("=== %s ===" % ("写入模式" if write else "空跑模式(加 --write 落盘)"))
    ok = patch("web_template.html", [
        (W1_OLD, W1_NEW, 1, "W1 两视图包裹 + 新表卡"),
        (W2_OLD, W2_NEW, 1, "W2 收尾关掉 f-detail-view"),
        (W3_OLD, W3_NEW, 1, "W3 switchTab 按视图态分派"),
        (W4_OLD, W4_NEW, 1, "W4 两个状态槽"),
        (W5_OLD, W5_NEW, 1, "W5 meta 采用 pending 品种"),
        (W6_OLD, W6_NEW, 1, "W6 _SEC_IDX_VER +1"),
        (W7_OLD, W7_NEW, 1, "W7 修 ①卡基差率 100× 显示错误"),
        (W8_ANCHOR, JS + "\n\n" + W8_ANCHOR, 1, "W8 插入 ⑨ 静态 JS 方法"),
    ], write)
    print()
    print("=== %s ===" % ("全部通过" if ok else "★ 有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
