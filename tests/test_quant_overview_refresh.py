# -*- coding: utf-8 -*-
"""技术面量化「总览 15 分钟刷新 + 缓存」与「详情 K线/技术指标 左右并排」回归护栏。

【用户 2026-10-14 拍板】
  A. 全品种技术面总览：改为**每隔 15 分钟刷新一次并缓存**。
  B. 单品种详情：K 线缩到目前一半宽、放左半边；右半边放技术指标；压缩空间。

【本文件锁死】
  A1. 后端缓存 TTL = 15 min（`_TTL_OVERVIEW`）；
  A2. ★ `_ov_start` 的新鲜度语义：**在跑中不起新线程**、**TTL 内命中缓存不起**、
      **超过 TTL 才重算** —— 这条是"并缓存"的真正含义，只查常量是测不到的；
  A3. 前端：算完排 15 min 自动刷新；**未算完仍保留 3s 渐进轮询**
      （退回成"只查 TTL 常量"会漏掉这条 —— 若把 3s 也改成 15 min，首屏要等一刻钟）；
  A4. 离开总览（进详情/切 tab）时定时器与"下次时刻"都要清掉，否则空转 + 显示过期信息。
  B1. 两卡在**同一个 flex 容器**内，各占约一半宽，`align-items:flex-start`（不用 stretch）；
  B2. 容器 id 一个不改（`chart-q-kline` / `q-tech-body` / `chart-q-rsi` / `chart-q-macd`）
      —— `tests/test_quant_overview.py` 按 id 断言它们存在；
  B3. 窄屏靠 `flex-wrap` 回落，不写媒体查询；
  B4. ★ flex 容器**恰好 1 个**（重复插入是这种"在锚点前插行"补丁最容易犯的错）。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

import quant_module as qm

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"

MARK_FLEX = ("display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;"
             "margin-top:12px")


def _tpl() -> str:
    return TPL.read_bytes().decode("utf-8")


def _strip_c(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


# ════════════════════════════════════════════════════════════════════
# A. 15 分钟刷新 + 缓存
# ════════════════════════════════════════════════════════════════════
def test_ttl_overview_is_15_minutes():
    """A1：后端总览缓存 TTL = 15 min（与原 5 min 区分）。"""
    assert qm._TTL_OVERVIEW == 900, (
        "总览缓存 TTL 不是 15 min（当前 %r）—— 用户要求每 15 分钟刷新并缓存"
        % qm._TTL_OVERVIEW
    )


def test_ov_start_freshness_semantics(monkeypatch):
    """A2：`_ov_start` 的三条新鲜度语义（"并缓存"的真正行为）。

    只断言 `_TTL_OVERVIEW == 900` 是**测不到行为**的 —— 把 `_ov_start` 里的
    `fresh` 判断删掉，常量照样是 900，页面却每次都重算（或永远不重算）。
    """
    codes = ["TA"]                       # 只用一个品种，避免真去拉数据
    key = qm._ov_state_key(codes)
    started: list = []

    class _FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started.append(args[0] if args else None)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setattr(qm, "_ov_worker", lambda *a, **k: None)
    try:
        qm._OV_STATE.pop(key, None)

        qm._ov_start(codes)
        assert len(started) == 1, "首次必须起线程"
        assert qm._OV_STATE[key]["done"] is False

        qm._ov_start(codes)
        assert len(started) == 1, "★ 正在算 ⇒ 不得重复起线程（否则并发烧取数配额）"

        st = qm._OV_STATE[key]
        # 【2026-10-16】本用例**手工伪造** state ⇒ 必须把新字段一并伪造到位:
        #   `refreshing` 现在也是一条"别起新线程"的理由(有后台刷新在跑)。
        #   不置 False 的话, 下面两条断言会因为"仍在刷新"而通过 —— 看着绿, 实际**没测到
        #   TTL 新鲜度语义**(本用例存在的唯一理由)。
        st["done"] = True
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - 60
        qm._ov_start(codes)
        assert len(started) == 1, "★ TTL 内（完成且 <900s）⇒ 命中缓存，不得重算"

        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        qm._ov_start(codes)
        assert len(started) == 2, "★ 超过 TTL ⇒ 必须重算（否则「每 15 分钟刷新」是假的）"
        # 【新增】★ stale-while-revalidate: 重算时**必须保留旧结果**, 不能清空 ——
        #   清空就是"每次进页面都得干等一整轮(~105s)"那个投诉的直接成因。
        qm._OV_STATE[key]["rows"] = {"TA": {"variety": "TA", "state": "偏多"}}
        st = qm._OV_STATE[key]
        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        st["refreshing"] = False
        st["stale"] = False
        qm._ov_start(codes)
        assert len(started) == 3, "超过 TTL 必须再起一轮"
        st = qm._OV_STATE[key]
        assert st.get("refreshing") is True, "重算中必须标 refreshing（前端据此保留旧表）"
        assert st.get("done") is True, (
            "★ 有旧结果时重算期间 done 必须为 True —— 否则前端会把已显示的表格换成进度条"
        )
        assert st.get("rows"), "★ 重算时不得清空旧 rows（stale-while-revalidate）"
    finally:
        qm._OV_STATE.pop(key, None)      # 清理，别污染其它用例


def test_frontend_schedules_15min_refresh_after_done():
    """A3：算完后排 15 min 自动刷新，且**算的过程中仍是 3s 渐进轮询**。"""
    s = _tpl()
    assert "_QOV_REFRESH_MS: 15 * 60 * 1000," in s, "缺少 15 min 周期常量"
    assert "this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;" in s, (
        "算完后没有排「15 分钟后重取」"
    )
    assert "setTimeout(() => this.loadQuantOverview(), this._QOV_REFRESH_MS);" in s, (
        "自动刷新定时器没有用 _QOV_REFRESH_MS"
    )
    assert "setTimeout(() => this.loadQuantOverview(), 3000);" in s, (
        "★ 算的过程中必须保留 3s 渐进轮询 —— 若也改成 15 min，首屏要等一刻钟"
    )
    # 定时器必须挂在「已完成」分支上（放在 if 外面会让算不完时也排 15min）
    i = s.index("this._qovNextAt = Date.now() + this._QOV_REFRESH_MS;")
    assert s.rindex("} else {", 0, i) > s.index("if (!d.done) {"), (
        "15 min 排期不在 done 分支里 —— 会与 3s 轮询互相覆盖"
    )


def test_frontend_shows_next_refresh_time():
    """A3：进度条要把"下次什么时候刷新"显示出来（否则用户不知道是否还在自动更新）。

    【★ 2026-10-16 限定范围】原断言在**全文**范围里禁止出现「之后走 5 分钟缓存」。
    新增的「全品种基本面截面表」占位文案合法地写了「之后走 5 分钟缓存」（那个表的后端
    缓存**确实是 5 min**，与技术面总览的 15 min 无关）⇒ 全局禁用会误报。
    **正解是把判据限定到技术面总览视图**，而不是把断言删掉 —— 它要守的是
    "技术面总览不得退回 5 分钟口径"这条产品要求。
    """
    s = _tpl()
    assert "每 15 分钟自动刷新（下次 " in s
    assert "之后走 15 分钟缓存" in s, "首屏占位文案没同步成 15 分钟"
    # 取 #q-overview-view 容器的范围（div 深度配平），只在其中查旧文案
    start = s.rindex("<div", 0, s.index('id="q-overview-view"'))
    depth, i = 0, start
    pat = re.compile(r"<div\b|</div>")
    ov = ""
    while i < len(s):
        m = pat.search(s, i)
        assert m, "q-overview-view 未配平"
        depth += 1 if m.group(0) == "<div" else -1
        if depth == 0:
            ov = s[start:m.end()]
            break
        i = m.end()
    assert ov, "未取到 #q-overview-view 范围"
    assert "之后走 15 分钟缓存" in ov, "技术面总览的占位文案没同步成 15 分钟"
    assert "之后走 5 分钟缓存" not in ov, "技术面总览里还留着旧的「5 分钟缓存」文案"


def test_switchtab_stops_overview_when_leaving_quant():
    """★ A5（2026-10-16 新增）：切走 tab 必须停总览的定时器/轮询。

    【为什么必须有这条】`_stopQuantOverview()` 原本**只在进单品种详情时**被调用,
    切到别的 tab 时 15 分钟定时器仍在跑 ⇒ 每次触发都请求总览, 而那一刻后端 TTL 恰好到期
    ⇒ **没人在看也重算一轮**, 且 3s 轮询空转。这正是用户"每次进这个页面都要重新加载计算"
    的直接成因。判据要落在**switchTab 真的调了它**, 而不是"函数本身写得对"。
    """
    s = _tpl()
    i = s.index("switchTab(t) {")
    seg = s[i:i + 900]
    assert "if (t !== 'quant') this._stopQuantOverview();" in seg, (
        "switchTab 离开量化页时没有停总览（后台空转 + 撞 TTL 边界重算）"
    )
    # 与 K 线定时器同一处处理（形态一致性）—— 别哪天只改了一边
    assert "if (t !== 'quant') this._clearKlineRefresh();" in seg, "K 线定时器那处被改动了"


def test_ov_start_keeps_old_rows_when_revalidating():
    """★ A6：`_ov_start` 的 stale-while-revalidate 语义（行为级）。

    只断言常量/文本是测不到的 —— 判据要证明"重算时旧 rows 还在"以及
    "刷新中不会重复起线程""新鲜时不重算"。
    """
    codes = ["TA"]
    key = qm._ov_state_key(codes)
    started: list = []

    class _FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started.append((args[0] if args else None, args[2] if len(args) > 2 else None))

        def start(self):
            pass

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(threading, "Thread", _FakeThread)
    monkeypatch.setattr(qm, "_ov_worker", lambda *a, **k: None)
    try:
        qm._OV_STATE.pop(key, None)
        qm._ov_start(codes)
        assert qm._OV_STATE[key]["refreshing"] is True, "首次计算也要标 refreshing"
        qm._ov_start(codes)
        assert len(started) == 1, "刷新中不得重复起线程"
        # 造一个"过期但有过结果"的状态
        st = qm._OV_STATE[key]
        st["rows"] = {"TA": {"variety": "TA"}}
        st["done"] = True
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - (qm._TTL_OVERVIEW + 5)
        qm._ov_start(codes)
        assert len(started) == 2, "过期必须重算"
        assert started[1][1], "★ 重算时要把旧 rows 作为 carry 传给 worker（否则旧表会被清空）"
        st = qm._OV_STATE[key]
        assert st["rows"] == {"TA": {"variety": "TA"}}, "★ 重算时旧 rows 被清空了"
        assert st["done"] is True and st["refreshing"] is True, "应处于'有数据+刷新中'"
        # 新鲜时不得重算
        st["refreshing"] = False
        st["stale"] = False
        st["ts"] = time.time() - 10
        qm._ov_start(codes)
        assert len(started) == 2, "TTL 内不得重算"
    finally:
        qm._OV_STATE.pop(key, None)
        monkeypatch.undo()


def _client():
    """本文件的 test_client（关鉴权）。"""
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    import web_app
    return web_app.app.test_client()


def test_ov_worker_consumes_carry(monkeypatch):
    """★ A8：`carry` 必须被 worker **真正消费**。

    【为什么单列一条】`_ov_start` 把旧 rows 留在 state 里（另一条用例已守），但那**不保证**
    worker 会用它们 —— 变异自检实测：把 `rows = dict(carry or {})` 改成 `rows = {}` 后，
    护栏照样全绿（旧 state 仍在，只是 worker 第一次 publish 就把它盖成空表）。
    判据：carry 里放一个**不在 codes 里**的键，跑完仍应在结果里 —— 它只可能来自 carry。
    """
    key = "overview:__carry_test__"
    qm._OV_STATE[key] = {"rows": {}, "done": True, "refreshing": True, "stale": True,
                         "refreshed": 0, "err": "", "ts": 0.0}
    monkeypatch.setattr(qm, "_overview_base", lambda c: {"variety": c, "state": "偏多"})
    monkeypatch.setattr(qm, "_overview_fill_periods", lambda c, r: None)
    monkeypatch.setattr(qm, "_liquidity_grade", lambda m: {})
    try:
        qm._ov_worker(key, ["TA"], carry={"XX": {"variety": "XX", "state": "偏空"}})
        st = qm._OV_STATE[key]
        assert "XX" in (st.get("rows") or {}), (
            "worker 没消费 carry —— 旧结果会被第一轮 publish 抹掉（用户会看到表格瞬间变空）"
        )
        assert st["done"] is True
    finally:
        qm._OV_STATE.pop(key, None)


def test_publish_keeps_done_true_during_refresh(monkeypatch):
    """★ A9：刷新**过程中** done 必须全程为 True。

    【为什么用 `_liquidity_grade` 当观察点】它是"阶段 A 全部 publish 完"之后第一个被调用的
    钩子 ⇒ 在那里读 state 就能看到**刷新途中**的 done。这是唯一能区分
    `done = done or had_old` 与 `done = done` 的观察点（看最终值区分不出来，因为最后都会 True）。
    """
    key = "overview:__done_refresh_test__"
    qm._OV_STATE[key] = {"rows": {"XX": {"variety": "XX"}}, "done": True, "refreshing": True,
                         "stale": True, "refreshed": 0, "err": "", "ts": 0.0}
    seen: list = []

    def _grade(_m):
        seen.append(bool((qm._OV_STATE.get(key) or {}).get("done")))
        return {}

    monkeypatch.setattr(qm, "_overview_base", lambda c: {"variety": c, "state": "偏多"})
    monkeypatch.setattr(qm, "_overview_fill_periods", lambda c, r: None)
    monkeypatch.setattr(qm, "_liquidity_grade", _grade)
    try:
        qm._ov_worker(key, ["TA", "CF"], carry={"XX": {"variety": "XX"}})
        assert seen, "观察点未触发（_liquidity_grade 没被调用？）"
        assert seen[0] is True, (
            "刷新途中 done 变成了 False —— 前端会把已显示的表格换成'计算中'，秒开就毁了"
        )
        assert qm._OV_STATE[key]["done"] is True
    finally:
        qm._OV_STATE.pop(key, None)


def test_overview_cached_flag_reflects_refreshing(monkeypatch):
    """★ A10：`cached` 必须反映"本次拿到的是一份**没在刷新**的新鲜结果"。

    原实现是 `done and not err` —— 只要算完就报"缓存"，哪怕就是**刚刚现算出来**的
    （实测第一轮就 cached=true，是假的）。这里用受控 meta 直接验路由的组装。
    """
    monkeypatch.setattr(qm, "_ov_start", lambda codes, force=False: "k")
    # 刷新中
    monkeypatch.setattr(qm, "_ov_snapshot",
                        lambda key, codes: ([{"variety": "TA", "name": "PTA"}], True, "",
                                            {"refreshing": True, "stale": True,
                                             "refreshed": 0, "ts": 0.0}))
    d = _client().get("/api/quant/overview?varieties=TA").get_json()
    assert d["refreshing"] is True and d["stale"] is True
    assert d["cached"] is False, "★ 刷新中不得报 cached=true（原实现恒真）"
    # 空闲且新鲜
    monkeypatch.setattr(qm, "_ov_snapshot",
                        lambda key, codes: ([{"variety": "TA", "name": "PTA"}], True, "",
                                            {"refreshing": False, "stale": False,
                                             "refreshed": 21, "ts": time.time()}))
    d2 = _client().get("/api/quant/overview?varieties=TA").get_json()
    assert d2["cached"] is True and d2["refreshing"] is False
    assert d2["refreshed"] == 21, "本轮刷新计数未透出"


def test_frontend_keeps_old_table_while_refreshing():
    """★ A11（2026-10-16）：前端必须有「有旧结果 + 后台刷新中」这两条分支。

    ① 排期分支要用**短轮询**（5s）—— 否则新数据落地前用户看不到更新；
    ② 进度条要写「后台刷新中」并说明表格是**上一轮**结果 —— 否则用户以为数是新的、
       也会以为页面卡住了。
    ③ 刷新中**不得**显示"下次刷新时刻"（那一刻还没确定，是过期信息）。
    E2E 已做行为级验证；这里补静态兜底（这两处被改坏时会立刻红）。
    """
    s = _tpl()
    i = s.index("} else if (d.refreshing) {")
    seg = s[i:i + 600]
    assert "_QOV_REFRESH_POLL_MS" in seg, "刷新中没有用短轮询（新数据落地前看不到更新）"
    j = s.index("const pg = document.getElementById('q-ov-progress');")
    pg = s[j:j + 1500]
    assert "} else if (d.refreshing) {" in pg, "进度条缺「后台刷新中」分支"
    assert "后台刷新中" in pg, "进度条没告诉用户正在后台刷新"
    assert "上一轮" in pg, "进度条没说明表格是上一轮结果"
    assert "d.done && !d.refreshing && this._qovNextAt" in pg, (
        "刷新中仍会显示「下次刷新时刻」—— 那是过期信息"
    )


def test_progress_lines_vars_are_function_scoped():
    """★ A7（2026-10-16）：进度条三态用到的变量必须在**函数作用域**声明。

    【为什么】本次给进度条加「后台刷新中」分支时，引用了一个原先声明在
    `if (up) { const dates = ... }` **块内**的变量 ⇒ 块外引用直接 `ReferenceError`。
    而它**只在重算期间那条新分支上被读到** ⇒ 平时不炸、`node --check`（只管语法）
    也查不出来 —— 是浏览器 E2E 抓到的。这里补一条静态兜底：`dates` 必须在函数作用域。
    """
    s = _tpl()
    i = s.index("const dates = all.map(r => r.date).filter(Boolean).sort();")
    prev = [ln for ln in s[:i].split("\n") if ln.strip()][-1]
    assert not prev.strip().startswith("if (up) {"), (
        "`dates` 声明在 `if (up) {` 块内 —— 进度条分支引用它会 ReferenceError"
    )
    # 进度条那段确实用了它（否则这条护栏会因为"没人用"而失去意义）
    pg = s[s.index("const pg = document.getElementById('q-ov-progress');"):]
    pg = pg[:pg.index("\n  const bd")]
    assert "dates" in pg, "进度条没用到 dates（这条护栏的前提变了，请重审）"


def test_stop_overview_clears_timer_and_next_at():
    """A4：离开总览（进详情/切 tab）必须清定时器 + 清"下次时刻"。"""
    s = _tpl()
    i = s.index("_stopQuantOverview() {")
    seg = s[i:i + 400]
    assert "clearTimeout(this._qovPollTimer)" in seg, "未清定时器 ⇒ 后台空转请求"
    assert "this._qovNextAt = 0;" in seg, (
        "未清「下次刷新时刻」⇒ 再回总览会先显示一个过期的下次时间"
    )


# ════════════════════════════════════════════════════════════════════
# B. 详情排版
# ════════════════════════════════════════════════════════════════════
def test_two_cards_share_one_flex_row():
    """B1/B4：两卡在同一个 flex 容器内，且容器**恰好 1 个**。"""
    s = _tpl()
    assert s.count(MARK_FLEX) == 1, (
        "flex 容器出现 %d 次（应恰好 1）—— 0 说明没改，>1 说明"
        "「在锚点前插行」的补丁被重复执行了" % s.count(MARK_FLEX)
    )
    clean = _strip_c(s)
    # 【2026-10-14 修】原用「下一张卡的 id」(`q-signal-body`) 当右边界 —— 而该卡已随
    #   「多空逻辑归位」迁到**基本面量化页** ⇒ 右边界会一路拉到 tab-fund，`seg` 膨胀成
    #   跨页文本，下面几条 "in seg" 断言就变成了**恒真**（等于悄悄失效）。
    #   正解：按 div 深度配平取 **flex 容器自身**的范围，不依赖任何后续卡片。
    a = clean.rindex("<div", 0, clean.index(MARK_FLEX))
    _d, _k = 0, a
    import re as _re
    while _k < len(clean):
        _m = _re.search(r"<div\b|</div>", clean[_k:])
        _d += 1 if _m.group(0) == "<div" else -1
        _k += _m.end()
        if _d == 0:
            break
    seg = clean[a:_k]
    # ⚠ 只查 `id="..."`：容器实际写作 `<div class="chart-container" id="chart-q-kline">`，
    #   `id` 并不紧跟 `<div` ⇒ 写 `<div id="..."` 会永远不命中（本次实测踩到）。
    assert 'id="chart-q-kline"' in seg and 'id="chart-q-macd"' in seg, (
        "flex 容器自身范围提取失败（K线/技术指标应都在其中）")
    assert 'id="chart-q-kline"' in seg, "K 线卡不在 flex 容器内"
    assert 'id="chart-q-macd"' in seg, "技术指标卡不在 flex 容器内"
    assert seg.index('id="chart-q-kline"') < seg.index('id="q-tech-badge"'), (
        "K 线必须排在技术指标**之前**（用户要求 K 线在左半边）"
    )


def test_both_cards_take_half_width():
    """B1：两卡各占约一半宽（`flex:1 1 calc(50% - 6px)`）。"""
    s = _tpl()
    assert s.count("flex:1 1 calc(50% - 6px);min-width:420px") == 1, "K 线卡未占一半宽"
    assert s.count("flex:1 1 calc(50% - 6px);min-width:340px") == 1, "技术指标卡未占一半宽"
    assert "align-items:flex-start" in MARK_FLEX, (
        "用了 stretch 会让固定高度的图下方留白（对齐假象）"
    )
    assert "flex-wrap:wrap" in MARK_FLEX, (
        "缺 flex-wrap ⇒ 窄屏会挤成两列过窄的图，而不是回落上下排列"
    )


def test_kline_wider_than_half_is_not_widened_back():
    """B1：K 线**只压宽度**，高度不得被顺手改小/改大（用户没要求改高度）。"""
    s = _tpl()
    assert 'id="chart-q-kline" style="height:520px"' in s, (
        "K 线高度被改动了（用户只要求缩宽度）"
    )


def test_ids_preserved_for_other_guards():
    """B2：容器 id 一个不改 —— 别的护栏按 id 断言它们存在。"""
    clean = _strip_c(_tpl())
    for cid in ("chart-q-kline", "q-kline-title", "q-kline-badge",
                "q-tech-body", "q-tech-badge", "chart-q-rsi", "chart-q-macd"):
        assert 'id="%s"' % cid in clean, "容器 id 被删/改名了: %s" % cid


def test_rsi_macd_compressed():
    """B1：RSI/MACD 迷你图缩到 130（让两列高度接近，配合"压缩空间"）。"""
    s = _tpl()
    assert s.count("height:130px;margin-top:") == 2, (
        "RSI/MACD 未缩到 130（实测 %d 处）" % s.count("height:130px;margin-top:")
    )
