# -*- coding: utf-8 -*-
"""2026-10-14 技术面量化两项改动（一次落盘）。

A. 全品种总览：**每 15 分钟自动刷新 + 缓存**
   · 后端 `_TTL_OVERVIEW` 300s → 900s（缓存 15 分钟）
   · 前端计算完成后不再只停轮询，而是**排一个 15 分钟后的自动重取**；
     到点后请求会让后端发现缓存过期 → 后台重算 → done=False → 前端回到 3s 渐进轮询。
   · 进度条显示「每 15 分钟自动刷新（下次 HH:MM）」。

B. 单品种详情排版：**K 线缩到一半宽（左半边）+ 右半边技术指标**
   · 实测原状：K 线卡 1600×570（chart 520）、技术指标卡 1600×661（body 294+RSI150+MACD150）
     ⇒ 纵向合计 1243px。
   · 改为 flex 并排（各占约一半宽）：chart 宽 1572 → 766 = **正好一半**；
     行高由较高者决定 ⇒ 纵向 ~1243 → ~584，**省 53%**。
   · 收窄的 K 线反而更好读：原 1572×520 是 3:1 的极扁比例，766×520 接近 1.5:1。
   · RSI/MACD 迷你图 150 → 130，让两列高度接近（570 vs 584），视觉更整。
   · 窄屏靠 `flex-wrap` + `min-width` 自动回落为上下排列（无需媒体查询）。
   · **容器 id 一个不改**（`chart-q-kline` / `q-tech-body` / `chart-q-rsi` / `chart-q-macd`）
     —— `tests/test_quant_overview.py` 按 id 断言它们存在。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QM = ROOT / "quant_module.py"
TPL = ROOT / "web_template.html"

# ── A. 后端 TTL ────────────────────────────────────────────────────
QM_EDITS = [
    ("_TTL_OVERVIEW 300→900",
     "_TTL_OVERVIEW = 300     # 【变量】总览整体缓存 5 min(与 _TTL_RANK 同档)",
     "_TTL_OVERVIEW = 900     # 【变量】总览整体缓存 15 min"
     "\n# 【2026-10-14 用户要求】「每隔 15 分钟刷新一次并缓存」⇒ 与前端自动刷新周期对齐。"
     "\n#   前端到点重取 ⇒ 此时缓存刚过期 ⇒ 后台重算(约 105s) ⇒ done=False ⇒ 前端回到 3s 渐进轮询。",
     1),
]

# ── B1. 详情页：两卡并排 ──────────────────────────────────────────
FLEX_OPEN = [
    " <!-- 【2026-10-14 用户要求】K 线 + 技术指标 改为**左右并排**(各占约一半宽), 压缩纵向空间。",
    " 实测收益: 原来上下排列 => 570 + 12 + 661 = 1243px 纵向; 并排后行高由较高者决定,",
    " 约 584px ⇒ **省 53%**(用户诉求「压缩空间」)。且 K 线由 1572×520(3:1 极扁)",
    " 变成 766×520(约 1.5:1), 影线更易读。",
    " ⚠ 容器 id 一个不改(chart-q-kline / q-tech-body / chart-q-rsi / chart-q-macd)",
    "   —— tests/test_quant_overview.py 按 id 断言它们存在。",
    " ⚠ 窄屏靠 flex-wrap + min-width 自动回落上下排列(无需媒体查询): 768px 视口即会换行。",
    " ⚠ ECharts 在容器**可见时**才 init(见 qOpenDetail 先 display 再 loadQuant), ",
    "   改成 flex 不改变「可见时才 init」这一点 ⇒ 不需要额外 resize 兜底。 -->",
    " <div style=\"display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px\">",
]

KLINE_CARD = [
    " <div class=\"card\" style=\"flex:1 1 calc(50% - 6px);min-width:420px;margin-top:0\">",
]
TECH_CARD = [
    " <div class=\"card\" style=\"flex:1 1 calc(50% - 6px);min-width:340px;margin-top:0\">",
]

TPL_LINE_EDITS = [
    # ① K 线卡：插入 flex 容器 + 给卡片加 flex 参数
    ("K线卡外层加 flex 容器",
     " <!-- ── ① K 线图(主图, 由看板迁入并增强) ── -->",
     FLEX_OPEN),
    ("K线卡加 flex 参数",
     " <div class=\"card\" >",
     KLINE_CARD),
    # ② 技术指标卡：加 flex 参数
    ("技术指标卡加 flex 参数",
     " <h3>技术指标 <span class=\"badge badge-info\" id=\"q-tech-badge\"></span></h3>",
     None),  # 占位，实际改上一行（card 开标签），见下方 special
]

# ── 前端 JS：15 分钟自动刷新 ──────────────────────────────────────
OLD_POLL = """  this._qovData = d;
  this._renderQuantOverview();
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  if (!d.done) {
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);
  }
 },"""

NEW_POLL = """  this._qovData = d;
  this._renderQuantOverview();
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  if (!d.done) {
   // 后台还在算 ⇒ 3s 渐进轮询(每完成一个品种 ready+1)
   this._qovNextAt = 0;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);
  } else {
   // 【2026-10-14 用户要求】「每隔 15 分钟刷新一次并缓存」。
   //   到点重取 ⇒ 后端缓存刚过期 ⇒ 自动重算 ⇒ done=False ⇒ 上面那条 3s 渐进轮询接管,
   //   用户看到的是"又算了一遍"的进度, 而不是一个卡死的旧表。
   this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);
  }
  this._renderQuantOverview();   // 重画一次: 让"下次刷新 HH:MM"立刻反映 _qovNextAt
 },"""

OLD_STOP = """ _stopQuantOverview() {
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
 },"""
NEW_STOP = """ _stopQuantOverview() {
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  this._qovNextAt = 0;   // 离开总览 ⇒ 不再显示"下次刷新"时刻(否则是过期信息)
 },"""

OLD_PROGRESS = """   pg.textContent = d.done
    ? ('计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时'))
    : ('计算中 ' + d.ready + '/' + d.total + ' 个品种…');
   pg.style.color = d.done ? 'var(--text-muted)' : 'var(--amber)';"""
NEW_PROGRESS = """   let _next = '';
   if (d.done && this._qovNextAt) {
    const _t = new Date(this._qovNextAt);
    _next = ' · 每 15 分钟自动刷新（下次 '
     + String(_t.getHours()).padStart(2, '0') + ':'
     + String(_t.getMinutes()).padStart(2, '0') + '）';
   }
   pg.textContent = d.done
    ? ('计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时') + _next)
    : ('计算中 ' + d.ready + '/' + d.total + ' 个品种…');
   pg.style.color = d.done ? 'var(--text-muted)' : 'var(--amber)';"""

OLD_VARS = """ _qovPollTimer: null,
 _qovPendingSym: null,   // 【变量】"点行后要选中的品种"，交给 _loadQuantMeta 首次填充时采用"""
NEW_VARS = """ _qovPollTimer: null,
 _qovNextAt: 0,          // 【变量】下次自动刷新时刻(Date.now() 基准); 0 = 未排期
 _QOV_REFRESH_MS: 15 * 60 * 1000,   // 【变量】总览自动刷新周期 = 15 min（与后端 _TTL_OVERVIEW 对齐）
 _qovPendingSym: null,   // 【变量】"点行后要选中的品种"，交给 _loadQuantMeta 首次填充时采用"""

OLD_HINT = "边算边填；之后走 5 分钟缓存秒开）"
NEW_HINT = "边算边填；之后走 15 分钟缓存，每 15 分钟自动刷新）"

JS_EDITS = [
    ("轮询→15min 自动刷新", OLD_POLL, NEW_POLL, 1),
    ("_stop 清下次时刻", OLD_STOP, NEW_STOP, 1),
    ("进度条显示下次刷新", OLD_PROGRESS, NEW_PROGRESS, 1),
    ("新增 _qovNextAt/_QOV_REFRESH_MS", OLD_VARS, NEW_VARS, 1),
    ("占位文案 5→15 分钟", OLD_HINT, NEW_HINT, 1),
]


def main() -> int:
    # ── A. 后端 ────────────────────────────────────────────────────
    raw = QM.read_bytes()
    assert raw.count(b"\r\n") == 0, "quant_module.py 应为 LF"
    t = raw.decode("utf-8")
    print("quant_module.py md5(前) =", hashlib.md5(raw).hexdigest()[:12])
    for tag, old, new, n in QM_EDITS:
        if new and new.split("\n")[0] in t and old not in t:
            print("  = %s（已应用，跳过）" % tag)   # 幂等：重跑补丁不会二次修改
            continue
        c = t.count(old)
        assert c == n, "%s 锚点命中 %d（期望 %d）" % (tag, c, n)
        t = t.replace(old, new, 1)
        print("  ✔ %s" % tag)
    QM.write_text(t, encoding="utf-8", newline="")
    assert QM.read_bytes().count(b"\r\n") == 0
    print("  md5(后) =", hashlib.md5(QM.read_bytes()).hexdigest()[:12])
    print()

    # ── B. 前端 ────────────────────────────────────────────────────
    raw = TPL.read_bytes()
    assert raw.count(b"\r\n") > 0, "web_template.html 应为 CRLF"
    lines = raw.decode("utf-8").split("\r\n")
    h0 = hashlib.md5(raw).hexdigest()
    print("web_template.html md5(前) =", h0[:12])

    # ★★ 本脚本是**一次性补丁，禁止重跑**（2026-10-14 实测踩到）：
    #   HTML 段是「在锚点前插入若干行」⇒ 重跑会**重复插入**（再插一个 flex 容器）；
    #   JS 段是 replace ⇒ 重跑会因锚点已消失而断言失败。
    #   两者叠加的结果是"跑一半炸掉"，因此**必须在开头就拦住**，并给出清晰指引，
    #   而不是依赖下游断言碰巧拦住（那是运气不是设计）。
    _MARK = "display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px"
    if _MARK in raw.decode("utf-8"):
        print("★ 改动已应用过 —— 本脚本不幂等，**请勿重跑**。")
        print("  要重做：先从 backup/web_template_pre_refresh_*.html 还原，再跑本脚本。")
        print("  要复核：跑 probe/_verify_quant_refresh_layout.py（只读）。")
        return 2

    def find(key: str, nth: int = 1) -> int:
        hits = [i for i, l in enumerate(lines) if key in l]
        assert len(hits) >= nth, "关键字 %r 命中 %d（需第 %d）" % (key, len(hits), nth)
        return hits[nth - 1]

    log = []

    # B1. K 线卡：插 flex 容器（在注释行之前）+ 卡片加 flex 参数
    i = find("<!-- ── ① K 线图(主图, 由看板迁入并增强) ── -->")
    lines[i:i] = FLEX_OPEN
    log.append(("插入 flex 容器", "K线卡之前", len(FLEX_OPEN)))

    # 重新定位（插过之后下标变了）
    j = find("<div class=\"chart-container\" id=\"chart-q-kline\"")
    ci = j - 5   # 卡开标签
    assert lines[ci].strip() == '<div class="card" >', repr(lines[ci])
    lines[ci] = KLINE_CARD[0]
    log.append(("K线卡加 flex 参数", lines[ci].strip()[:60], 1))

    # B2. 技术指标卡：卡开标签加 flex 参数 + RSI/MACD 高 150→130
    k = find("id=\"q-tech-badge\"")
    ti = k - 1
    assert lines[ti].strip() == '<div class="card" >', repr(lines[ti])
    lines[ti] = TECH_CARD[0]
    log.append(("技术指标卡加 flex 参数", lines[ti].strip()[:60], 1))

    # B3. RSI/MACD 高度
    for cid in ("chart-q-rsi", "chart-q-macd"):
        m = find("id=\"%s\"" % cid)
        before = lines[m]
        assert 'height:150px' in before, before
        lines[m] = before.replace('height:150px', 'height:130px')
        log.append(("缩高 %s" % cid, "150px -> 130px", 1))

    # B4. 关闭 flex 容器：技术指标卡之后（MACD 行之后首个 ` </div>`）
    mm = find("id=\"chart-q-macd\"")
    x = mm + 1
    while x < len(lines) and lines[x].strip() != "</div>":
        x += 1
    assert x < len(lines), "找不到技术指标卡的闭合 </div>"
    lines[x + 1:x + 1] = [" </div>"]   # 关闭 flex 容器
    log.append(("关闭 flex 容器", "after 技术指标卡", 1))

    txt = "\r\n".join(lines)

    # B5. JS 改动
    # ⚠⚠【必踩的坑】本脚本的常量用 `\n` 书写（源码是 LF），而模板是 **CRLF** ⇒
    #   多行锚点直接 count 会**永远命中 0**（后端 quant_module.py 是 LF 所以那半边能过）。
    #   必须先把锚点的换行统一成目标文件的 EOL 再匹配。
    print()
    print("[前端 JS]")
    _EOL = "\r\n"
    for tag, old, new, n in JS_EDITS:
        old_e = old.replace("\n", _EOL)
        new_e = new.replace("\n", _EOL)
        c = txt.count(old_e)
        assert c == n, "%s 锚点命中 %d（期望 %d）" % (tag, c, n)
        txt = txt.replace(old_e, new_e, 1)
        print("  ✔ %s" % tag)

    TPL.write_bytes(txt.encode("utf-8"))
    raw2 = TPL.read_bytes()
    assert raw2.count(b"\r\n") > 0 and (raw2.count(b"\n") - raw2.count(b"\r\n")) == 0, "EOL 被破坏"

    print()
    print("[HTML 改动明细]")
    for a, b, c in log:
        print("    %-22s %s" % (a, b))

    print()
    print("[自检]")
    t2 = raw2.decode("utf-8")
    checks = [
        ("后端 TTL = 900", "_TTL_OVERVIEW = 900" in QM.read_text(encoding="utf-8")),
        ("前端有 15min 周期常量", "_QOV_REFRESH_MS: 15 * 60 * 1000," in t2),
        ("完成态排期自动刷新", "this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;" in t2),
        ("3s 渐进轮询仍在", "setTimeout(() => this.loadQuantOverview(), 3000);" in t2),
        ("离开总览清下次时刻", "this._qovNextAt = 0;   // 离开总览" in t2),
        ("进度条显示下次刷新", "每 15 分钟自动刷新（下次 " in t2),
        ("占位文案已改", "之后走 15 分钟缓存" in t2 and "之后走 5 分钟缓存" not in t2),
        ("flex 容器已插", 'display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:12px' in t2),
        ("K线卡 flex=50%", 'flex:1 1 calc(50% - 6px);min-width:420px' in t2),
        ("技术指标卡 flex=50%", 'flex:1 1 calc(50% - 6px);min-width:340px' in t2),
        ("K线高度未变(520)", 'id="chart-q-kline" style="height:520px"' in t2),
        ("RSI/MACD 已缩到 130", t2.count("height:130px;margin-top:") == 2),
        ("四个容器 id 都在", all(('id="%s"' % i) in t2 for i in
                             ("chart-q-kline", "q-tech-body", "chart-q-rsi", "chart-q-macd"))),
    ]
    ok = True
    for tag, v in checks:
        print("    %-30s %s" % (tag, "✔" if v else "✘"))
        ok = ok and v

    # div 配平（剥注释）
    clean = re.sub(r"<!--.*?-->", "", t2, flags=re.S)
    mi, me = t2.index("<main>"), t2.index("</main>")
    seg = re.sub(r"<!--.*?-->", "", t2[mi:me], flags=re.S)
    no, nc = len(re.findall(r"<div\b", seg)), len(re.findall(r"</div>", seg))
    print("    %-30s %s" % ("<main> div 配平", "✔" if no == nc else "✘ %d/%d" % (no, nc)))
    ok = ok and no == nc
    # q-detail-view 内配平
    a = clean.index('<div id="q-detail-view"')
    dd, k2 = 0, a
    pat = re.compile(r"<div\b|</div>")
    while k2 < len(clean):
        m = pat.search(clean, k2)
        dd += 1 if m.group(0) == "<div" else -1
        if dd == 0:
            print("    %-30s %s" % ("q-detail-view div 配平", "✔"))
            break
        k2 = m.end()
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
