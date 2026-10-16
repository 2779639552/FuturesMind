# -*- coding: utf-8 -*-
"""前端补丁：基本面截面表 —— 后台/定期刷新 + 气泡式指标筛选（2026-10-16）。

【改动 8 处】
  W0 状态变量：_frkPollTimer / _frkNextAt / _FRK_REFRESH_MS / _FRK_REFRESH_POLL_MS
  W1 `loadFundRanking` 整段替换（三支排期 + 旧表保留 + 月份口径保留）+ 新增 `_stopFundRanking`
  W2 `_fRkBuildDims` 整段替换（下拉 → 气泡 + 签名守卫防闪）
  W3 `_fRkBuildPools` 整段替换（加签名守卫防闪）
  W4 `switchTab` 离开 fund 时停表（与技术面 `_stopQuantOverview` 同一纪律）
  W5 `fOpenDetail` 进明细时停表（总览已不可见，轮询无意义）
  W6 `fBackOverview` 返回总览时**重启**拉取（含重排定时器）
  W7 控制区加 `#f-rk-progress`（三态进度/下次刷新时刻）
  W8 `_renderFundRanking` 加进度条三态渲染

【不幂等】重跑会重复插入 ⇒ 开头检测标记并拒绝。要从 backup 还原后再跑。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
P = ROOT / "probe"

MARK = "_stopFundRanking"


def _eol_of(raw: bytes) -> str:
    return "\r\n" if raw.count(b"\r\n") else "\n"


def method_span(txt: str, name: str) -> tuple[int, int]:
    """按**花括号配平**取一个方法的 [start,end) —— 不依赖方法体内容。

    ★ 上一件事的教训：用"下一个方法的开头"当边界，一旦别处改动就会漂；
      配平只依赖结构，且与方法体怎么改无关。
    ⚠ 定位有两个坑（本次都踩到）：
      ① 方法头的**前导空格不固定**（同一对象里有的 1 空格、有的由补丁插入而无缩进）
         ⇒ 不能写死 " name() {"；
      ② 异步方法头是 " async name(...)" —— **`async ` 也是名字的一部分前缀**，
         漏掉它 `[ \\t]*name` 就匹配不到（实测命中 0）。
      故用：换行 + 可选缩进 + 可选 async + 方法名 + 左括号。
    """
    import re as _re
    m = _re.search(r"\n[ \t]*(?:async[ \t]+)?" + _re.escape(name) + r"\s*\(", txt)
    if not m:
        raise AssertionError("找不到方法: " + name)
    i = m.start() + 1
    b = txt.index("{", m.end())
    d = 0
    k = b
    while k < len(txt):
        ch = txt[k]
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return i, k + 1
        k += 1
    raise AssertionError("未配平: " + header)


# ── W1：loadFundRanking 整体 + _stopFundRanking ──────────────────────────────
NEW_LOAD = (P / "_frk_refresh_load.js.txt").read_text(encoding="utf-8").rstrip("\n")
NEW_DIMS = (P / "_frk_pills_dims.js.txt").read_text(encoding="utf-8").rstrip("\n")
NEW_POOLS = (P / "_frk_pills_pools.js.txt").read_text(encoding="utf-8").rstrip("\n")

W0_OLD = ''' _fRkData: null,         // 【变量】基本面截面表最近一次响应(切排序维度/返回总览时复用)'''
W0_NEW = ''' _fRkData: null,         // 【变量】基本面截面表最近一次响应(切排序维度/返回总览时复用)
// 【变量】⑨ 表的**定期刷新**（2026-10-16 与技术面总览同一套）
//   _frkPollTimer 轮询定时器 / _frkNextAt 下次自动刷新时刻(Date.now() 基准, 0=未排期)
_frkPollTimer: null,
_frkNextAt: 0,
// 【变量】自动刷新周期 = 15 min —— **必须与后端 `_TTL_FUND_RANK` 对齐**，
//   否则要么永远命中旧缓存（前端周期短于 TTL）、要么每次都白重算（长于 TTL）。
_FRK_REFRESH_MS: 15 * 60 * 1000,
// 【变量】"已有旧结果、后台正在刷新"时的短轮询间隔(5s) —— 见 loadFundRanking 的 refreshing 分支
_FRK_REFRESH_POLL_MS: 5000,'''


def trim_trailing_comma(seg: str) -> str:
    """去掉"对象方法段"末尾的 `},` 里的那个逗号，只留 `}`。

    【为什么】`method_span` 的结束点是配平到的 `}` **本身**，而方法在对象字面量里
      实际写作 `},\n`（逗号在 span **之外**）。所以替换段若自带 `},` 结尾，
      原文件那个逗号会补上 ⇒ 变成 `},,` ⇒ **整个内联 JS 语法崩**
      （本次实测：`node --check` 报 `Unexpected token ','`）。
    """
    t = seg.rstrip()
    return t[:-1] if t.endswith("},") else seg


def main(write: bool) -> int:
    raw = TPL.read_bytes()
    eol = _eol_of(raw)
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2

    edits: list[tuple[str, str, int, str]] = []

    # W0
    edits.append((W0_OLD, W0_NEW, 1, "W0 状态变量"))

    # W1：方法级替换（运行时取范围 → 用切片当锚点）
    a, b = method_span(txt, "loadFundRanking")
    edits.append((txt[a:b], trim_trailing_comma(NEW_LOAD), 1, "W1 loadFundRanking 整段替换"))

    # W2
    a, b = method_span(txt, "_fRkBuildDims")
    edits.append((txt[a:b], trim_trailing_comma(NEW_DIMS), 1, "W2 维度筛选改气泡"))

    # W3
    a, b = method_span(txt, "_fRkBuildPools")
    edits.append((txt[a:b], trim_trailing_comma(NEW_POOLS), 1, "W3 板块气泡加签名守卫"))

    # W4 切 tab 停表
    edits.append((
        " if (t !== 'quant') this._stopQuantOverview();",
        " if (t !== 'quant') this._stopQuantOverview();\n"
        " // 【2026-10-16】同理, 离开基本面页要停掉 ⑨ 表的定期刷新 ——\n"
        " //   否则人不在这一页时定时器仍会触发, 而那一刻后端 TTL 恰好到期 ⇒\n"
        " //   **没人在看也重算一轮**(全池约 40s), 用户切回来正好撞在重算中间。\n"
        " if (t !== 'fund') this._stopFundRanking();",
        1, "W4 switchTab 离开 fund 停表"))

    # W5 进明细停表
    edits.append((
        " fOpenDetail(code) {\n  const ov = document.getElementById('f-overview-view');",
        " fOpenDetail(code) {\n"
        "  // 总览已不可见 ⇒ 停掉它的定期刷新（切回时 fBackOverview 会重新起）\n"
        "  this._stopFundRanking();\n"
        "  const ov = document.getElementById('f-overview-view');",
        1, "W5 fOpenDetail 停表"))

    # W6 返回总览重启
    edits.append((
        " if (!this._fRkData) this.loadFundRanking();   // 首次从明细返回时补拉",
        "  // 【2026-10-16】返回总览时**一律重拉**：不只是补首拉，还要**重排定期刷新定时器**\n"
        "  //   （进明细时被 _stopFundRanking 停了）。命中缓存 ⇒ 秒回，无额外成本。\n"
        "  this.loadFundRanking();",
        1, "W6 fBackOverview 重启拉取"))

    # W7 控制区加进度条
    edits.append((
        ' <span id="f-rk-note" style="font-size:0.75rem;color:var(--text-muted);padding-bottom:8px"></span>',
        ' <span id="f-rk-progress" style="font-size:0.75rem;color:var(--text-muted);padding-bottom:8px"></span>\n'
        ' <span id="f-rk-note" style="font-size:0.75rem;color:var(--text-muted);padding-bottom:8px"></span>',
        1, "W7 控制区加进度条"))

    # W8 进度条三态渲染（插在 badge 段之前）
    PG = '''  // 【进度条三态】与技术面总览同形（见 _renderQuantOverview 的进度条注释）：
  //   ① 首次计算中 → 进度 n/N；② 有旧表+后台刷新中 → 明说"显示的是上一轮、无需等待"；
  //   ③ 完成且空闲 → 缓存/实时 + 下次刷新时刻。
  //   ⚠ `dates` 直接在这里从 `all` 现算（**不要**声明在上面的 if 块里 ——
  //     技术面那轮踩过：块内声明、块外引用 = ReferenceError，且只在重算期间才炸）。
  const pg = document.getElementById('f-rk-progress');
  if (pg) {
   const _dates = all.map(r => r.date).filter(Boolean).sort();
   let _next = '';
   if (d.done && !d.refreshing && this._frkNextAt) {
    const _t = new Date(this._frkNextAt);
    _next = ' · 每 15 分钟自动刷新（下次 '
     + String(_t.getHours()).padStart(2, '0') + ':'
     + String(_t.getMinutes()).padStart(2, '0') + '）';
   }
   if (!d.done) {
    pg.textContent = '计算中 ' + (d.ready || 0) + '/' + (d.total || 0) + ' 个品种…';
    pg.style.color = 'var(--amber)';
   } else if (d.refreshing) {
    pg.textContent = '数据截至 ' + (_dates.length ? _dates[_dates.length - 1] : '—')
     + ' · 后台刷新中 ' + (d.refreshed || 0) + '/' + (d.total || 0)
     + ' 个品种（表格显示上一轮结果, 算好的行会就地更新, 无需等待）';
    pg.style.color = 'var(--amber)';
   } else {
    pg.textContent = '计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时') + _next;
    pg.style.color = 'var(--text-muted)';
   }
  }

'''
    edits.append((
        "  // badge 用**前端排序状态**（不再读 d.metric —— 排序已归前端，且后端不认识文本列）",
        PG + "  // badge 用**前端排序状态**（不再读 d.metric —— 排序已归前端，且后端不认识文本列）",
        1, "W8 进度条三态渲染"))

    ok = True
    new = txt
    for old, rep, n, tag in edits:
        o = old if "\r\n" in old else old.replace("\n", eol)
        r = rep if "\r\n" in rep else rep.replace("\n", eol)
        got = new.count(o)
        flag = "OK " if got == n else "★  "
        print("  %s %-34s 命中 %d(期望 %d)" % (flag, tag, got, n))
        if got != n:
            ok = False
            continue
        new = new.replace(o, r, 1)
    if not ok:
        print("  ⇒ 有锚点未命中, **未写盘**")
        return 1

    # ── 写盘前硬自检（本次上一件事的教训：锚点打偏会静默吃掉内容）──────────────
    def _depth(t: str) -> int:
        import re
        d = 0
        for m in re.finditer(r"<div\b|</div>", t):
            d += 1 if m.group(0) == "<div" else -1
        return d

    for key_id in ('id="tab-fund"', 'id="f-overview-view"', 'id="f-detail-view"',
                   'id="f-rk-table"', 'id="f-rk-rows"', 'id="f-rk-stats"',
                   'id="f-rk-dims"', 'id="f-rk-pools"', 'id="tab-quant"', 'id="q-ov-rows"',
                   'id="f-variety"', 'id="wt-table"'):
        a2, b2 = txt.count(key_id), new.count(key_id)
        if a2 != b2:
            print("  ★ 写盘前自检失败: %s 计数 %d → %d ⇒ 拒绝写盘" % (key_id, a2, b2))
            return 1
    d0, d1 = _depth(txt), _depth(new)
    if d0 != d1:
        print("  ★ 写盘前自检失败: div 深度 %+d → %+d ⇒ 拒绝写盘" % (d0, d1))
        return 1
    dl = len(new) - len(txt)
    if not (-2000 <= dl <= 20000):
        print("  ★ 写盘前自检失败: 长度变化 %+d 字符 ⇒ 拒绝写盘" % dl)
        return 1
    print("  ✔ 写盘前自检: 关键 id 计数不变 / div 深度 %+d 不变 / 长度 %+d 字符" % (d0, dl))
    if not write:
        print("  ⇒ 空跑通过(未写盘)。加 --write 落盘。")
        return 0
    TPL.write_bytes(new.encode("utf-8"))
    bb = TPL.read_bytes()
    assert bb.count(b"\r\n") > 0 and (bb.count(b"\n") - bb.count(b"\r\n")) == 0, "模板 EOL 必须 CRLF"
    print("  落盘后 md5=%s (CRLF 保持)" % hashlib.md5(bb).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
