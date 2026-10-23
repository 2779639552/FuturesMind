# -*- coding: utf-8 -*-
"""公众号采集通道状态巡检的护栏（2026-10-16 新增）。

【背景】两条公众号通道（合集 / 微信读书）的运行结果此前**只写进 alerts 表**，
  界面完全看不出来 —— 用户是靠肉眼看"今天没有公众号来源的"才发现，
  而那条 `wechat_weread_collect_failed` 告警已经躺了 3 天（典型**告警疲劳**）。

【本文件守什么】
  ① 后端字段契约（前端判据可依赖）；
  ② ★ 两个**核心判据的语义** —— 它们被单抽成纯函数，正是为了让护栏咬得住：
     · 「最近一轮」锚点：告警表里**只有失败才写记录**（成功不写）⇒
       "有没有告警"不能当状态，必须判"是不是本轮的"。
     · 三态不可混：empty(内容源没新文) ≠ failed(通道坏了) —— 混了用户会修错东西。
  ③ **只读纪律**：巡检绝不能起子进程 / 发网络请求（否则"看一眼"就污染生产）；
  ④ 前端结构：容器、方法、接线、三态渲染齐备。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _h() -> str:
    return TPL.read_bytes().decode("utf-8")


@pytest.fixture(autouse=True)
def _no_tq_side_effects():
    """本文件不碰天勤（tqsdk），避免 pending 任务污染其它用例。"""
    import tradingagents.dataflows.tq_source as _tqs
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_tqs, "fetch_daily_bars", lambda *a, **k: None)
        mp.setattr(_tqs, "fetch_minute_bars", lambda *a, **k: None)
        mp.setattr(_tqs, "fetch_realtime", lambda *a, **k: None)
        yield


@pytest.fixture
def client():
    """关鉴权后的测试客户端（/api/* 默认需登录）。"""
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    import web_app
    web_app.app.config["TESTING"] = True
    return web_app.app.test_client()


# ════════════════════════════════════════════════════════════════════
# A. 后端字段契约
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
def test_channel_status_contract():
    """响应结构必须稳定：前端的三态渲染直接依赖这些键。"""
    import web_app
    d = web_app._wechat_channels_status()

    assert d["ok"] is True
    assert isinstance(d["channels"], list)
    assert len(d["channels"]) == 2, "两条通道：合集 + 微信读书（少一条说明漏了某条链路）"

    keys = {c["key"] for c in d["channels"]}
    assert keys == {"album", "weread"}, "通道 key 变了（前端/文档都按它索引）"

    for c in d["channels"]:
        for k in ("key", "name", "accounts", "unit_name", "state", "new", "error", "hint"):
            assert k in c, "通道条目缺字段 %s" % k
        assert c["state"] in ("ok", "empty", "failed"), "state 取值越界: %r" % c["state"]
        assert isinstance(c["accounts"], int) and c["accounts"] >= 0
        # hint 只在 failed 时给（否则前端会对着正常通道显示"修复动作"）
        if c["state"] != "failed":
            assert not c["hint"], "非 failed 却给了修复命令: %r" % c["hint"]

    assert isinstance(d["summary"], str) and d["summary"]
    for k in ("inbox", "pool_block", "failed", "empty_channels"):
        assert k in d, "响应缺字段 %s" % k
    # failed / empty_channels 必须与 channels 的 state 一致（两处口径打架会让前端显示矛盾）
    assert set(d["failed"]) == {c["key"] for c in d["channels"] if c["state"] == "failed"}
    assert set(d["empty_channels"]) == {c["key"] for c in d["channels"] if c["state"] == "empty"}


@pytest.mark.unit
def test_route_registered(client):
    """路由真的注册（401 ≠ 路由不存在 ⇒ 关鉴权后再打）。"""
    r = client.get("/api/wechat/channels")
    assert r.status_code == 200
    d = r.get_json()
    assert d.get("ok") is True
    assert len(d.get("channels") or []) == 2


# ════════════════════════════════════════════════════════════════════
# B. ★ 核心判据（纯函数，可精确注入）
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
def test_tri_state_never_mixes_empty_with_failed():
    """★ 三态不可混：failed(通道坏了) 与 empty(内容源没新文)**修法完全相反**。

    混成一个"没数据"会让用户修错东西：
      · 把 empty 当 failed ⇒ 去重登一个没坏的账号（白折腾）；
      · 把 failed 当 empty ⇒ 以为"等等就会好"，而它永远不会自己好（本次事故就是这样）。
    """
    import web_app
    f = web_app._wechat_channel_tri_state
    assert f(True, 5) == "failed", "有失败告警时必须是 failed（哪怕本轮也采到了东西）"
    assert f(False, 0) == "empty"
    assert f(False, 7) == "ok"
    # 没读到结果文件（None）时不该误报空转 —— 宁可显示 ok
    assert f(False, None) == "ok"


@pytest.mark.unit
def test_alert_in_round_anchor_semantics():
    """★★ 「最近一轮」锚点的语义 —— 这是本巡检最关键的一条。

    【为什么重要】告警表里**只有失败才写记录**（成功不写），所以"存在告警"不能当状态：
    一次历史失败若不判时间，会把该通道**永久**标成"中断"（用户会以为一直没修好）。
    反之 `base_ts` 为空（还没跑过一轮）时必须**算本轮**，宁可多提醒也别漏报真问题。
    """
    import web_app
    g = web_app._wechat_alert_in_round
    base = "2026-10-16 00:30:00"   # 最近一轮的 wechat_collect_done

    assert g(None, base) is False, "没有告警 ⇒ 不是本轮失败"
    assert g({"created_at": base}, base) is True, "同一时刻的失败要算本轮（>= 而非 >）"
    assert g({"created_at": "2026-10-16 01:00:00"}, base) is True
    # ★ 反面：上一轮的失败**不能**算本轮
    assert g({"created_at": "2026-10-14 10:30:00"}, base) is False, \
        "上一轮的失败被算成了本轮 ⇒ 通道会被永久标红（修好了也不消失）"
    # 没有基准时：只要存在就算本轮
    assert g({"created_at": base}, "") is True, "没有时间锚时漏报了真失败"
    # 空 created_at 不能当成本轮（否则脏数据会造出假告警）
    assert g({"created_at": ""}, base) is False


# ════════════════════════════════════════════════════════════════════
# C. ★ 只读纪律
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
def test_status_check_is_read_only(monkeypatch):
    """★ 巡检**绝不能**起子进程或发网络请求 —— 否则"打开页面看一眼"就污染生产。

    【为什么单列】这个接口会在每次打开研报页时被调用（高频）。一旦它顺手触发一次采集，
    就成了"用户越看、系统越忙"的放大器，还可能撞上平台风控（本项目为此踩过坑）。
    """
    import subprocess
    import urllib.request
    import web_app

    hits: list = []

    def _boom(*a, **k):
        hits.append(a)
        raise AssertionError("巡检触发了子进程/网络请求 —— 违反只读纪律")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    d = web_app._wechat_channels_status()
    assert not hits, "只读纪律被破坏: %r" % (hits[:2],)
    assert d["ok"] is True


# ════════════════════════════════════════════════════════════════════
# D. 前端结构
# ════════════════════════════════════════════════════════════════════
@pytest.mark.unit
def test_frontend_container_and_wiring():
    """容器 + 方法 + 接线三件套齐备（缺一处该提示就永远不会出现）。"""
    h = _h()
    assert 'id="res-ingest-status"' in h, "研报库卡内没有状态巡检容器"
    assert "async _loadIngestStatus() {" in h, "缺 _loadIngestStatus 方法"
    assert "this._loadIngestStatus();" in h, "方法定义了但**没接线** ⇒ 永远不会执行"

    # ★ 接线点必须在 **loadWarehouse** 里 —— `#res-ingest-status` 与 `#res-list` 都住 tab-warehouse。
    #   ⚠ 这条是浏览器 E2E 抓出来的：第一版把它挂在 `loadResearch()`（**研报问答页**），
    #     而那个页面没有这个容器 ⇒ `getElementById` 返回 null 直接 return ⇒ **永不执行**；
    #     而"方法定义了 + 存在一行 `this._loadIngestStatus();`"的静态断言**照样全绿**。
    i = h.index("async loadWarehouse() {")
    assert "this._loadIngestStatus();" in h[i:i + 900], \
        "没挂在 loadWarehouse 上（拥有该容器的 tab）"
    j = h.index("async loadResearch() {")
    assert "this._loadIngestStatus();" not in h[j:j + 900], \
        "挂到了 loadResearch（研报问答页）—— 那个页面没有该容器，等于永不执行"

    # 容器要落在研报库卡内（列表之上），否则和"研报从哪来"割裂
    j = h.index("<h3>研报库(上传 + 自动接入)")
    k = h.index('id="res-list"')
    assert j < h.index('id="res-ingest-status"') < k, "巡检条不在研报库卡内、列表之上"


@pytest.mark.unit
def test_frontend_renders_three_states_and_fix_hint():
    """三态渲染齐备，且 failed 必须把**修复命令**显示出来（否则用户知道坏了也没法修）。"""
    h = _h()
    i = h.index("async _loadIngestStatus() {")
    body = h[i:i + 6000]
    assert "state === 'failed'" in body, "缺 failed 分支"
    assert "state === 'empty'" in body, "缺 empty 分支（会被误并成 failed）"
    assert "c.hint" in body, "缺修复命令渲染（用户看到失败却不知道怎么办）"
    # ★ 只读容错：巡检失败不能影响研报库本体
    assert "box.style.display = 'none'" in body, "巡检异常时没有降级路径"
    # ★ 「采到却未入库」的口径解释（品种池边界 ≠ 通道故障）
    # ⚠ 判据必须钉**"真的被用作渲染条件"**，不能只查 `pool_block` 这个词存在 ——
    #   变量声明行 `const pb = d.pool_block || {}` 也含它，于是把 `if (pb.count)` 改成
    #   `if (false && pb.count)`（等于不渲染）时断言照样绿（变异自检实测存活）。
    assert "const pb = d.pool_block || {};" in body, "没读池拦截数据"
    assert "if (pb.count) {" in body, \
        "「品种池拦截」解释没有真的参与渲染 ⇒ 用户会把口径边界当成通道故障"


@pytest.mark.unit
def test_frontend_empty_state_is_not_alarming():
    """★ empty（跑了但无新文）不能渲染成红色告警 —— 那是**正常状态**，不是故障。

    若把 empty 也标红，用户会去重登一个没坏的账号（白折腾），
    而且真正的 failed 会被淹没在红色里（告警疲劳的成因）。
    """
    h = _h()
    i = h.index("async _loadIngestStatus() {")
    body = h[i:i + 6000]
    # empty 分支用的是琥珀色系，不是红 (239,68,68)
    e = body.index("if (empty.length) {")
    seg = body[e:e + 700]
    assert "239,68,68" not in seg, "empty 用了红色告警底 ⇒ 正常状态被渲染成故障"


# ===========================================================================
# F. 陈旧失败告警必须被「更晚的成功记录」推翻
#    （2026-10-16 实测：修好链路后界面仍显示「中断」，用户以为还坏着）
# ===========================================================================
@pytest.mark.unit
def test_stale_alert_superseded_time_alignment():
    """★★ 时间口径：`alerts.created_at` 是 **UTC**，result 的 `ts` 是**本地时间**。

    【为什么单列一条钉它】SQLite 的 `CURRENT_TIMESTAMP` 写的是 UTC，
      而采集结果文件的 `ts` 来自 `datetime.now().isoformat()`（本地）⇒
      不做 +8 对齐的话，「本地 17:03 vs UTC 16:15」会被算成**更早** ⇒ 判定恒为假
      ⇒ **这条修复静默失效**（症状：代码明明在，状态却永远 failed，很难看出是时区）。
      本项目在时间口径上已踩过一次（DB 时间列多为 UTC），故用一条断言钉死语义。
    """
    import importlib
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    wa = importlib.import_module("web_app")
    f = wa._wechat_alert_superseded
    # 告警 UTC 08:15 = 本地 16:15；本地 17:03 更晚 ⇒ 推翻（本次真实场景）
    assert f({"created_at": "2026-10-16 08:15:00"}, "2026-10-16T17:03:47") is True
    # 同一刻 ⇒ **不**推翻（保守：宁可多提醒一次，也别漏真问题）
    assert f({"created_at": "2026-10-16 08:15:00"}, "2026-10-16T16:15:00") is False
    # 告警比成功记录更晚 ⇒ 不推翻（说明之后又失败过）
    assert f({"created_at": "2026-10-16 10:15:00"}, "2026-10-16T17:03:47") is False
    # 边界：空告警 / 空 ts / 坏格式 都不能抛，也不能误判为"已恢复"
    assert f(None, "2026-10-16T17:03:47") is False
    assert f({"created_at": "2026-10-16 08:15:00"}, "") is False
    assert f({"created_at": "坏格式"}, "2026-10-16T17:03:47") is False
    assert f({"created_at": ""}, "2026-10-16T17:03:47") is False


@pytest.mark.unit
def test_both_channels_apply_supersede_check():
    """★ 两个通道都要接上推翻判定 —— 只修一个的话，另一个仍会被历史告警钉红。

    【为什么钉到调用点】定义了纯函数但没接到状态组装里，等于没修
      （本项目同型坑：方法定义了、接线错/漏接线，静态断言照样绿）。
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert "def _wechat_alert_superseded(" in src, "缺推翻判定函数"
    assert "if wr_bad and _wechat_alert_superseded(" in src, "读书通道没接推翻判定"
    assert "if alb_bad and _wechat_alert_superseded(" in src, "合集通道没接推翻判定"
    # 合集通道的结果是纯文本、没有 ts 字段 ⇒ 必须另有 mtime 兜底
    assert "def _file_mtime_iso(" in src, "缺「文本结果用 mtime 当时间」的兜底"
    assert "_file_mtime_iso(_os.path.join(W, \"album_collect_result.txt\"))" in src, \
        "合集通道没用 mtime 兜底（它的 result 没有 ts）"


@pytest.mark.unit
def test_superseded_turns_failed_into_ok_when_new_data():
    """★ 端到端语义：告警被推翻后，状态应按「本轮拿到的新文数」落到 ok / empty，而不是 failed。

    【为什么】"历史上失败过"与"现在好不好"是两件事。修好之后界面必须**自己转向**，
      否则用户每次开页都看到红色块，久之就不看了（又回到告警疲劳）。
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    i = src.index("if wr_bad and _wechat_alert_superseded(")
    seg = src[i:i + 700]
    assert "wr_bad = False" in seg, "推翻后没有把 wr_bad 置回 False"
    # state 必须仍由（更新后的）wr_bad 参与三态判定 ⇒ 自然落到 ok/empty
    assert '"state": _wechat_channel_tri_state(wr_bad, mp_rep.get("new"))' in src, \
        "state 没用更新后的 wr_bad（推翻白做）"
