# -*- coding: utf-8 -*-
"""把「公众号采集通道失败」做成界面可见提示（用户 2026-10-16 要求）。

【背景】两条公众号通道（合集 / 微信读书）的运行结果此前**只写进 alerts 表**，
        界面完全看不出来。用户是靠肉眼看「今天没有公众号来源的」才发现，
        而 `wechat_weread_collect_failed` 告警已经躺了 3 天（典型告警疲劳）。
【目标】在「数据仓库 → 研报库」卡内加一行通道状态：哪条断了 / 断在哪 / 怎么修。

改动清单：
  A web_app.py       A1 新增 `_wechat_channels_status()`；A2 新增 `/api/wechat/channels`（纯只读）
  B web_template.html B1 插入 `#res-ingest-status` 容器；B2 新增 `_loadIngestStatus()` 并接线；
                      B3 `_SEC_IDX_VER` 30 → 31（结构有增删，铁律）
  C tests            同步两处硬钉版本号的断言（30 → 31）

★ 两条纪律（本项目血泪）：
  1. 本接口**纯只读**：不触发采集、不发网络请求，只读 alerts 表 + 本地状态文件。
  2. 「最近一轮」判据用 `wechat_collect_done` 当时间锚（两条通道同轮触发），
     **不能**用 result 文件 mtime —— 失败时文件根本不更新，会把「旧」误读成「没跑」。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRITE = "--write" in sys.argv
report: list[str] = []


# ────────────────────────────────────────────────────────────
# A. web_app.py —— 后端（CRLF）
# ────────────────────────────────────────────────────────────
A_ANCHOR = '@app.route("/api/research")\ndef api_research():'

A_NEW = '''# ============================================================================
# 公众号采集通道状态（2026-10-16 新增 · 只读巡检）
# 【为什么要有它】两条公众号通道（合集 / 微信读书）的运行结果此前**只写进 alerts 表**，
#   界面完全看不出来 —— 用户是靠肉眼看"今天没有公众号来源的"才发现，而那条
#   `wechat_weread_collect_failed` 告警已经躺了 3 天（典型的**告警疲劳**）。
#   ⇒ 本接口把"哪条通道断了 / 断在哪 / 怎么修"变成页面上看得见的一行。
# 【纪律】**纯只读**：不触发采集、不发任何网络请求；只读 alerts 表 + 本地状态文件。
# 【判据】"最近一轮"以 `wechat_collect_done` 为时间锚（两条通道同轮触发）——
#   某通道的 failed 告警若不早于该锚，即判定它**这一轮**是失败的。
#   ⚠️ **不要用 result 文件的 mtime 判**：通道失败时那个文件根本不会被更新，
#     于是"旧"会被误读成"压根没跑"（两者修法完全不同）。
# ============================================================================
def _wechat_channels_status() -> dict:
    import json as _json
    import os as _os
    import re as _re

    W = _os.path.join(_os.path.expanduser("~"), ".tradingagents", "wechat")
    R = _os.path.join(_os.path.expanduser("~"), ".tradingagents", "weread")

    def _load(p, default=None):
        try:
            with open(p, encoding="utf-8") as fh:
                return _json.load(fh)
        except Exception:
            return default

    def _text(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    db = get_db()

    def _latest(atype):
        try:
            return db.get_latest_alert(atype)
        except Exception:
            return None

    # 最近一轮的时间锚（两条通道同轮触发）
    base = str((_latest("wechat_collect_done") or {}).get("created_at") or "")

    def _this_round(rec) -> bool:
        """该告警是否属于"最近一轮"（created_at 同为 UTC 字符串，可直接比）。"""
        if not rec:
            return False
        ts = str(rec.get("created_at") or "")
        return bool(ts) and (not base or ts >= base)

    channels: list[dict] = []

    # ── 通道① 合集通道（免登录 HTTP，按"已登记合集"增量追踪）────────────────
    acc = (_load(_os.path.join(W, "wechat_accounts.json"), {}) or {}).get("accounts") or {}
    n_alb = sum(len((a or {}).get("albums") or {}) for a in acc.values())
    alb_rep = _text(_os.path.join(W, "album_collect_result.txt"))
    m = _re.search(r"本次新增\\s*(\\d+)\\s*篇", alb_rep)
    alb_new = int(m.group(1)) if m else None
    alb_fail = _latest("wechat_collect_failed")
    alb_bad = _this_round(alb_fail)
    channels.append({
        "key": "album",
        "name": "公众号 · 合集通道",
        "accounts": len(acc),
        "units": n_alb,
        "unit_name": "合集",
        "state": "failed" if alb_bad else ("empty" if alb_new == 0 else "ok"),
        "new": alb_new,
        "last_run": base[:19],
        "error": str((alb_fail or {}).get("message") or "") if alb_bad else "",
        "hint": "",
    })

    # ── 通道② 微信读书通道（需扫码登录态 cookie）──────────────────────────
    mp = (_load(_os.path.join(R, "mp_accounts.json"), {}) or {}).get("accounts") or []
    mp_rep = _load(_os.path.join(R, "mp_collect_result.json"), {}) or {}
    wr_fail = _latest("wechat_weread_collect_failed")
    wr_bad = _this_round(wr_fail)
    channels.append({
        "key": "weread",
        "name": "公众号 · 微信读书通道",
        "accounts": len(mp) if isinstance(mp, list) else 0,
        "units": len(mp) if isinstance(mp, list) else 0,
        "unit_name": "个号",
        "state": "failed" if wr_bad else ("empty" if mp_rep.get("new") == 0 else "ok"),
        "new": mp_rep.get("new"),
        "last_run": base[:19],
        "last_ok": str(mp_rep.get("ts") or "")[:19],
        "error": str((wr_fail or {}).get("message") or "") if wr_bad else "",
        "hint": "node probe/_weread_login.mjs" if wr_bad else "",
    })

    # ── 入库侧：inbox 积压（采到了但还没入库）──────────────────────────────
    inbox = {"total": 0, "latest": "", "since_days": 1}
    lines = [l for l in _text(_os.path.join(W, "wechat_inbox.jsonl")).split("\\n") if l.strip()]
    inbox["total"] = len(lines)
    for l in reversed(lines):
        try:
            dv = (_json.loads(l) or {}).get("date") or ""
        except Exception:
            continue
        if dv:
            inbox["latest"] = str(dv)
            break
    try:
        inbox["since_days"] = int(_os.getenv("WECHAT_INGEST_SINCE_DAYS") or 1)
    except Exception:
        pass

    # ── ★ 品种池拦截：解释"采到了却没进库"（这是最容易被误判成故障的一环）──
    pool_block = {"days": 7, "count": 0, "sample": ""}
    try:
        import sqlite3 as _sq
        from database import DB_PATH
        with _sq.connect(str(DB_PATH)) as c:
            row = c.execute(
                "SELECT COUNT(*) FROM research_reports WHERE source LIKE '%公众号%' "
                "AND status='skipped' AND uploaded_at >= date('now','-7 day')"
            ).fetchone()
            pool_block["count"] = int(row[0] or 0)
            row2 = c.execute(
                "SELECT error FROM research_reports WHERE source LIKE '%公众号%' "
                "AND status='skipped' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row2 and row2[0]:
                mm = _re.search(r"识别品种\\s*([A-Za-z,]+)\\s*均不在", str(row2[0]))
                pool_block["sample"] = mm.group(1) if mm else ""
    except Exception:
        pass

    n_fail = sum(1 for c in channels if c["state"] == "failed")
    n_empty = sum(1 for c in channels if c["state"] == "empty")
    return {
        "ok": True,
        "channels": channels,
        "failed": [c["key"] for c in channels if c["state"] == "failed"],
        "empty_channels": [c["key"] for c in channels if c["state"] == "empty"],
        "summary": "共 %d 条通道：%d 正常 / %d 空转(跑了但无新文) / %d 中断"
                   % (len(channels), len(channels) - n_fail - n_empty, n_empty, n_fail),
        "inbox": inbox,
        "pool_block": pool_block,
    }


@app.route("/api/wechat/channels")
def api_wechat_channels():
    """公众号采集通道状态（只读巡检）: 哪条断了 / 断在哪 / 怎么修。

    【渲染】研发库卡片 #res-ingest-status
    【关键】纯只读 —— 不触发采集、不发网络请求。任何异常都返回 ok:false 而不是 500,
            避免"巡检本身把页面搞崩"。
    """
    try:
        return jsonify(_wechat_channels_status())
    except Exception as e:
        return jsonify({"ok": False, "channels": [], "error": str(e)[:300]})


''' + A_ANCHOR


def patch_a() -> None:
    p = ROOT / "web_app.py"
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    assert crlf > 0 and raw.count(b"\n") == crlf, "web_app.py 不是纯 CRLF"
    t = raw.decode("utf-8")
    o = A_ANCHOR.replace("\n", "\r\n")
    c = t.count(o)
    assert c == 1, "[A] 锚点命中 %d 次（应为 1）" % c
    assert "_wechat_channels_status" not in t, "A 已打过了（重复）"
    n = A_NEW.replace("\n", "\r\n")
    t = t.replace(o, n, 1)
    out = t.encode("utf-8")
    b = out.count(b"\r\n")
    assert b > 0 and out.count(b"\n") == b, "写回破坏 EOL"
    assert "/api/wechat/channels" in t and "_wechat_channels_status" in t
    if WRITE:
        p.write_bytes(out)
    report.append("  ✓ A 后端：_wechat_channels_status + /api/wechat/channels（%d → %d 字节）"
                  % (len(raw), len(out)))


# ────────────────────────────────────────────────────────────
# B. web_template.html —— 前端（CRLF）
# ────────────────────────────────────────────────────────────
B1_OLD = ' <div style="font-size:0.8125rem;color:var(--text-secondary);line-height:1.8" id="res-list">加载中...</div>\n'
B1_NEW = (' <!-- 【2026-10-16 新增】采集通道状态巡检条。\n'
          ' ⚠ 为什么放在**研报库卡内、列表之上**：这里是"研报从哪来"的归口，\n'
          '   而通道失败此前只写 alerts 表 —— 用户在页面上完全看不出来，\n'
          '   是从"今天没有公众号来源的"这一现象反推出来的（告警已躺 3 天）。\n'
          ' 默认 display:none，抓到数据才显示（无数据时不占位）。 -->\n'
          ' <div id="res-ingest-status" style="display:none;margin:0 0 10px"></div>\n'
          + B1_OLD)

B2_OLD = (' this._loadRagStatus(); // 研报问答卡片:索引状态(后台, 不阻塞)\n'
          ' // 2026-09-24 历史会话下拉已隐藏, 不再预拉会话列表(_loadQaSessions 函数保留待恢复)\n'
          ' },\n')

B2_NEW = ''' this._loadRagStatus(); // 研报问答卡片:索引状态(后台, 不阻塞)
 this._loadIngestStatus(); // 【2026-10-16】采集通道巡检:通道断了要让用户**在页面上**看见
 // 2026-09-24 历史会话下拉已隐藏, 不再预拉会话列表(_loadQaSessions 函数保留待恢复)
 },

 // 【功能】采集通道状态巡检:把"哪条采集通道断了"从 alerts 表搬到页面上(2026-10-16 新增)
 // 【为什么】此前通道失败**只写 alerts 表**,界面完全看不出来 —— 用户是靠肉眼看
 //   "今天没有公众号来源的"才发现,而那条告警已经躺了 3 天(典型告警疲劳)。
 // 【输入】无 【渲染】#res-ingest-status 【后端】GET /api/wechat/channels (纯只读巡检)
 // 【三态】failed(红·中断,给修复命令) / empty(黄·跑了但无新文) / ok(淡色一行)
 //   ⚠ "empty" 与 "failed" 必须分开:前者是**内容源没新文**(换关键词才有用),
 //     后者是**通道本身坏了**(重登才有效) —— 混成一个"没数据"会让用户修错东西。
 async _loadIngestStatus() {
 const box = document.getElementById('res-ingest-status');
 if (!box) return;
 try {
 const r = await fetch('/api/wechat/channels');
 if (!r.ok) return; // 巡检失败静默:它不该影响研报库本体的可用性
 const d = await r.json();
 if (!d || d.ok === false) { box.style.display = 'none'; return; }
 const esc = s => this._escHtml(String(s == null ? '' : s));
 const chs = d.channels || [];
 const bad = chs.filter(c => c.state === 'failed');
 const empty = chs.filter(c => c.state === 'empty');
 const pb = d.pool_block || {};
 const parts = [];
 if (bad.length) {
 parts.push(bad.map(c => `
 <div style="display:flex;gap:10px;align-items:flex-start;padding:8px 12px;border-radius:8px;background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.4);margin-bottom:6px">
 <span style="color:#ef4444;font-weight:700;white-space:nowrap">⚠ 采集通道中断</span>
 <div style="font-size:0.8125rem;line-height:1.7">
 <b>${esc(c.name)}</b><span style="color:var(--text-muted)">（覆盖 ${c.accounts} ${esc(c.unit_name || '个')}）</span>
 <div style="color:var(--text-secondary)">${esc(c.error || '本轮运行失败')}</div>
 ${c.hint ? `<div style="margin-top:2px">修复：<code style="background:var(--bg-input);padding:1px 6px;border-radius:4px">${esc(c.hint)}</code></div>` : ''}
 </div></div>`).join(''));
 }
 if (empty.length) {
 parts.push(`<div style="font-size:0.8125rem;line-height:1.7;padding:6px 12px;border-radius:8px;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.35);color:var(--text-secondary)">
 <b style="color:#f59e0b">◐ 通道跑了但无新文</b>：${empty.map(c => esc(c.name)).join(' / ')}
 （通道本身正常；若持续为空，多半是内容源近期未更新，或该号有**未登记**的合集）</div>`);
 }
 if (pb.count) {
 parts.push(`<div style="font-size:0.75rem;color:var(--text-muted);margin-top:6px;line-height:1.7">
 另有 ${pb.count} 篇公众号文章采集成功但**未入库**（近 ${pb.days} 天）：识别出的品种${pb.sample ? '（如 ' + esc(pb.sample) + '）' : ''}不在当前品种池内 ⇒ 属口径边界，不是通道故障。</div>`);
 }
 box.style.display = '';
 box.innerHTML = parts.length ? parts.join('')
 : `<div style="font-size:0.75rem;color:var(--text-muted)">采集通道正常 · ${esc(d.summary || '')}</div>`;
 } catch (e) {
 box.style.display = 'none';
 }
 },
'''

B3_OLD = "_SEC_IDX_VER: '30',"
B3_NEW = "_SEC_IDX_VER: '31',"


def patch_b() -> None:
    p = ROOT / "web_template.html"
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    assert crlf > 0 and raw.count(b"\n") == crlf, "web_template.html 不是纯 CRLF"
    t = raw.decode("utf-8")
    for tag, old, new in (
        ("B1 插入 #res-ingest-status", B1_OLD, B1_NEW),
        ("B2 新增 _loadIngestStatus + 接线", B2_OLD, B2_NEW),
        ("B3 _SEC_IDX_VER 30 → 31", B3_OLD, B3_NEW),
    ):
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        c = t.count(o)
        assert c == 1, "[%s] 锚点命中 %d 次（应为 1）" % (tag, c)
        t = t.replace(o, n, 1)
        report.append("  ✓ B %s" % tag)
    out = t.encode("utf-8")
    b = out.count(b"\r\n")
    assert b > 0 and out.count(b"\n") == b, "写回破坏 EOL"
    assert 'id="res-ingest-status"' in t, "B1 没写进去"
    assert "async _loadIngestStatus() {" in t, "B2 方法没写进去"
    if WRITE:
        p.write_bytes(out)
    report.append("  ✓ B 前端写回 %d → %d 字节" % (len(raw), len(out)))


# ────────────────────────────────────────────────────────────
# C. 同步硬钉版本号的两处断言（LF）
# ────────────────────────────────────────────────────────────
C1_OLD = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30。\n'
          '    assert "_SEC_IDX_VER: \'30\'," in h, "_SEC_IDX_VER 未随卡片增删 +1"\n'
          '    assert "_SEC_IDX_VER: \'29\'," not in h\n')
C1_NEW = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30；\n'
          '    #   同日新增「采集通道状态巡检条」(#res-ingest-status) ⇒ 30 升 31。\n'
          '    assert "_SEC_IDX_VER: \'31\'," in h, "_SEC_IDX_VER 未随卡片增删 +1"\n'
          '    assert "_SEC_IDX_VER: \'30\'," not in h\n')

C2_OLD = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30。\n'
          '    assert "_SEC_IDX_VER: \'30\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
          '    assert "_SEC_IDX_VER: \'29\'," not in h, "旧版本号残留"\n')
C2_NEW = ('    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30；\n'
          '    #   同日新增「采集通道状态巡检条」⇒ 30 升 31。\n'
          '    assert "_SEC_IDX_VER: \'31\'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"\n'
          '    assert "_SEC_IDX_VER: \'30\'," not in h, "旧版本号残留"\n')


def patch_c() -> None:
    for rel, tag, old, new in (
        ("tests/test_fund_xsection.py", "C1 版本断言", C1_OLD, C1_NEW),
        ("tests/test_multi_logic_relocation.py", "C2 版本断言", C2_OLD, C2_NEW),
    ):
        p = ROOT / rel
        raw = p.read_bytes()
        assert raw.count(b"\r\n") == 0, "%s 应为 LF" % rel
        t = raw.decode("utf-8")
        c = t.count(old)
        assert c == 1, "[%s %s] 锚点命中 %d 次" % (rel, tag, c)
        t = t.replace(old, new, 1)
        if WRITE:
            p.write_bytes(t.encode("utf-8"))
        report.append("  ✓ %s %s" % (rel, tag))


if __name__ == "__main__":
    print("=== %s ===" % ("落盘" if WRITE else "空跑（--write 才写）"))
    patch_a()
    patch_b()
    patch_c()
    print("\n".join(report))
    print("\n✓ 共 %d 处" % len(report))
