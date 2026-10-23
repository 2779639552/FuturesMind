"""基本面多品种截面表（⑨ 表）护栏 —— 2026-10-16。

【背景】技术面有「全品种技术面总览」，基本面此前**只有单品种视图**。本次新增
  ⑨ 全品种基本面截面表（列 = 库存双口径 / 基差 / 基差率 / 仓单5日 / 社会库存4期 /
  虚实盘比 / 利润 + 《期货多空逻辑》系 4 票 + 三指标综合结论），并把基本面页
  拆成「全品种总览 → 点进单品种明细」两视图。

本文件钉住五件事：
  A. **口径不新增**：截面表必须复用既有函数（库存双口径 / 虚实比唯一定义处 / 4 票同源）
  B. 月差别名修复（FU/P/PG/PX 曾因新浪命名表用别名而**取数失败**）
  C. 接口契约（字段齐全 + 分位是 0~1 比率 + 库存档位方向序）
  D. 前端两视图结构 + 交互绑定（点行跳明细 / 下拉不得冒泡 / 返回按钮）
  E. ★ 顺带修掉的 ① 卡「基差率」100× 显示错误
"""

import re
import threading
import time
from pathlib import Path

import pytest

import fund_module as fm
import quant_module as qm

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _h() -> str:
    return TPL.read_bytes().decode("utf-8")


def _src(p) -> str:
    return Path(p).read_text(encoding="utf-8")


# ===========================================================================
# A. 口径不新增（本表与各卡片必然一致）
# ===========================================================================
@pytest.mark.unit
def test_vr_ratio_single_definition():
    """虚实盘比的算术只能有一处 —— ④ 卡路由必须调 _vr_ratio_of。

    【为什么钉这条】④ 卡与 ⑨ 表都要这个比值。若哪次重构把路由改回内联
    `one_side_oi / lots`, 而 ⑨ 表走另一条路径, 同一品种在两个页面就会出现
    **不同虚实比**(本项目已有"利润档位两处各写一遍"的同类教训)。
    """
    src = _src(ROOT / "fund_module.py")
    route = src[src.index('@fund_bp.route("/api/fund/virtual-real")'):]
    assert "_vr_ratio_of(" in route, "④ 卡路由未调用 _vr_ratio_of（比值又出现第二处算术了）"
    assert "/ float(inv_out[\"lots\"])" not in route, "④ 卡路由里仍有内联的比值算术"
    # 全文件里比值的除法只应出现在 _vr_ratio_of 内部
    assert src.count("oi_f / lots_f") == 1, "出现第二处比值除法"


@pytest.mark.unit
def test_vr_ratio_of_edge_cases():
    assert fm._vr_ratio_of(100, 200) == 2.0
    assert fm._vr_ratio_of(3, 1) == 0.3333
    # ★ 分母缺失/为 0 必须返回 None, **不是 0** —— 0 会被下游读成"极端空逼多"
    assert fm._vr_ratio_of(0, 100) is None
    assert fm._vr_ratio_of(None, 100) is None
    assert fm._vr_ratio_of("x", 100) is None


@pytest.mark.unit
def test_vote_book_names_match_quant_verdict():
    """4 票的 book **字面量**必须与 quant_verdict 里写的一致。

    【为什么钉这条】截面表按 book 名从 build_verdict 的 votes 里挑票。若哪天
    quant_verdict 把 "多空逻辑(期限结构)" 改个名, 而这里没跟着改, 结果**不是报错**
    而是那一列**静默变空**(看着像"该品种无期限结构数据")。这是最危险的一类漂移。
    """
    qv = _src(ROOT / "quant_verdict.py")
    for _key, book in fm._FUND_VOTE_BOOKS:
        assert f'"book": "{book}"' in qv, f"quant_verdict 里找不到 book={book!r}（改名了？）"


@pytest.mark.unit
def test_gap_prefixes_match_quant_verdict():
    """缺口文案前缀同理 —— 它是"该列为何为空"的唯一解释来源。"""
    qv = _src(ROOT / "quant_verdict.py")
    for key, prefix in fm._FUND_GAP_PREFIX.items():
        core = prefix.rstrip(":")
        assert core in qv, f"quant_verdict 里找不到缺口前缀 {core!r}（列 {key} 的空白原因会丢失）"


@pytest.mark.unit
def test_inv_rank_computed_in_row_builder():
    """★ inv_rank 必须在**行生成时**算好, 不能只靠路由事后补。

    【变异自检实测(2026-10-16)】把 `row["inv_rank"] = ...` 从 _fund_xsection_one 删掉后,
    护栏**照样全绿** —— 因为 api_fund_ranking 里有一句**防御性重算**, 走 HTTP 的用例
    拿到的仍是正确值。但直接调 _fund_rank_all(或日后别处复用缓存行)拿到的行**没有**该字段,
    按"库存档位"排序会整体失效。⇒ 判据必须落到"行生成时就带上"。
    """
    src = _src(ROOT / "fund_module.py")
    body = src[src.index("def _fund_xsection_one("):src.index("def _fund_rank_all(")]
    assert 'row["inv_rank"] = _INV_GRADE_RANK.get(' in body, (
        "截面行没在生成时算 inv_rank（缓存里存的行会缺该字段, 按库存档位排序会静默失效）"
    )
    # 档位序的唯一定义处（不得出现第二套映射）
    assert src.count('_INV_GRADE_RANK = {') == 1, "库存档位序出现了第二处定义"


@pytest.mark.unit
def test_xsection_reuses_existing_sources():
    """截面行必须调既有函数, 不得自己重算库存档位/利润档位/策略结论。"""
    src = _src(ROOT / "fund_module.py")
    body = src[src.index("def _fund_xsection_one("):src.index("def _fund_rank_all(")]
    for need in ("q._inv_dual(", "_vr_ratio(", "_profit_summary(", "_strategy_verdict(",
                 "_fund_skill_votes("):
        assert need in body, f"截面行没有复用 {need}"
    # 不得在截面行里自己写档位阈值
    assert "_grade(" not in body, "截面行自己算了档位（应由 _inv_dual/_profit_summary 给）"


@pytest.mark.unit
def test_skill_votes_call_build_verdict():
    """4 票必须调 quant_verdict.build_verdict —— 不得另写一套判定。"""
    src = _src(ROOT / "fund_module.py")
    body = src[src.index("def _fund_skill_votes("):src.index("def _fund_xsection_one(")]
    assert "qv.build_verdict(" in body, "没有调 build_verdict（四票判定被重写了一套）"
    # 利润票用的是 0.3/0.7 阈值(与 fund._grade 的 0.35/0.65 **不同**) ⇒ 不得在此重算。
    # ⚠ 断言别用裸子串 "0.3" —— 它是 "0.35" 的子串, 会误报(第一版就这么写错了)。
    for pat in (r"pct <= 0\.3", r"pct >= 0\.7", r"value_drive_matrix\(", r"term_structure_regime\(",
                r"inventory_cycle_phase\("):
        assert not re.search(pat, body), f"截面表自己重算了 /{pat}/（应与 ⑥ 卡同源）"


# ===========================================================================
# B. 月差别名修复
# ===========================================================================
@pytest.mark.unit
def test_sina_alias_table_present():
    assert isinstance(qm._SINA_NAME_ALIASES, dict)
    # 实测 2026-10-16 因新浪命名表用别名而失败的四/五个品种
    for code in ("PX", "FU", "P", "PG", "LU"):
        assert code in qm._SINA_NAME_ALIASES, f"{code} 不在别名表里"


@pytest.mark.unit
def test_term_structure_uses_alias_retry():
    """_fetch_term_structure 必须走逐候选名重试, 不得直接传项目名。

    【事故原型】原先写 `ak.futures_zh_realtime(symbol=name)`(项目名) ⇒ KeyError
    ⇒ 该品种期限结构/虚实比**全为空**, 界面上表现为"无跨月合约报价",
    看着像**数据源无覆盖**, 实为**取名失败**(三态被混)。
    """
    src = _src(ROOT / "quant_module.py")
    assert "_term_realtime_df(" in src
    body = src[src.index("def _fetch_term_structure("):]
    body = body[:body.index("\ndef ", 10)]
    assert "_term_realtime_df(" in body, "_fetch_term_structure 未使用候选名重试"
    assert "ak.futures_zh_realtime(symbol=name)" not in body, "仍在直接传项目名"


@pytest.mark.unit
def test_term_realtime_df_tries_candidates(monkeypatch):
    """逐候选名重试的行为：第一个失败要换下一个, 全失败要如实带回"试过哪些"。"""
    class _FakeAk:
        def __init__(self, ok_name):
            self.ok_name = ok_name
            self.tried = []

        def futures_zh_realtime(self, symbol):
            self.tried.append(symbol)
            if symbol != self.ok_name:
                raise KeyError(symbol)
            class _DF:
                empty = False
            return _DF()

    ak = _FakeAk("二甲苯")
    df, used, tried = qm._term_realtime_df(ak, "PX", "对二甲苯")
    assert df is not None and used == "二甲苯", (used, tried)
    assert tried == ["对二甲苯", "二甲苯"], tried
    # 项目名优先(多数品种命中), 命中即停
    ak2 = _FakeAk("PTA")
    _df2, used2, tried2 = qm._term_realtime_df(ak2, "TA", "PTA")
    assert used2 == "PTA" and tried2 == ["PTA"], (used2, tried2)
    # 全失败 → 返回 None 且 tried 齐全(供"取名失败"与"无覆盖"区分)
    ak3 = _FakeAk("不存在的名字")
    df3, used3, tried3 = qm._term_realtime_df(ak3, "PX", "对二甲苯")
    assert df3 is None and used3 is None and len(tried3) == 2, tried3


@pytest.mark.unit
def test_empty_df_moves_to_next_candidate():
    """★ 空 DataFrame 也要换下一个候选 —— 只捕异常会漏掉"名字存在但查不到合约"。"""
    class _Empty:
        empty = True

    class _FakeAk:
        def __init__(self):
            self.n = 0

        def futures_zh_realtime(self, symbol):
            self.n += 1
            if self.n == 1:
                return _Empty()

            class _DF:
                empty = False
            return _DF()

    df, used, tried = qm._term_realtime_df(_FakeAk(), "PX", "对二甲苯")
    assert df is not None and len(tried) == 2, (used, tried)


# ===========================================================================
# C. 接口契约（离线: 直调视图函数, 用 test_client；不依赖外网数据是否齐）
# ===========================================================================
@pytest.fixture(scope="module")
def client():
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    import web_app
    return web_app.app.test_client()


@pytest.fixture()
def _no_bg_ranking(monkeypatch):
    """★ 禁止本文件经 `/api/fund/ranking` 起**真实的后台重算**。

    【为什么必须掐】2026-10-16 把该接口从"同步阻塞"改成"后台线程 + 渐进发布"之后，
      HTTP 用例**返回时线程仍在跑**（这正是一个接口从同步改后台必然引入的测试卫生问题）：
        ① 留下取数副作用与 pending 任务 ⇒ 解释器关闭时刷屏
           `Task was destroyed but it is pending!`（看着像真故障，实为测试污染）；
        ② 更要紧：该线程会停在别人"临时替换全局状态"的窗口内 ⇒ **顺序污染**
           （本项目 2026-10-14 在 test_quant_overview 上踩过同型事故：单跑全绿、组合变红）。
    【★ 为什么不直接换掉 `threading.Thread`】`_fund_rank_all` / `_fund_xsection_one` 内部
      用 `ThreadPoolExecutor`，而它依赖**真实的** `threading.Thread` —— 换掉会让任务池
      **永远不启动**（`fut.result()` 挂死）。故只把"本轮要算的东西"换成空操作：
      线程照常创建、立刻结束 ⇒ 既无悬挂，也不影响任务池。
    """
    import fund_module as _fm

    monkeypatch.setattr(_fm, "_frk_worker", lambda key, codes, carry=None: None)
    yield
    _fm._FRK_STATE.clear()      # 不留状态给别的测试（同 _no_tqsdk_bars 的纪律）


@pytest.fixture(autouse=True)
def _no_tqsdk_bars(monkeypatch):
    """★ 禁止本文件经 `_load_daily_bars` 去碰天勤(tqsdk)。

    【为什么必须掐】该函数会先试天勤日K(本机不可达, 走 SSL 失败), 失败的副作用是
      **留下 pending 的 asyncio 任务**, 解释器关闭时刷一整屏
      `Task was destroyed but it is pending!` —— 看着像真故障, 其实是测试卫生问题;
      更要紧的是这类"活在测试结束之后"的任务可能停在别人"临时替换全局状态"的窗口内,
      造成**顺序污染**(本项目 2026-10-14 在 test_quant_overview 上踩过同型事故,
      单跑全绿、组合变红)。本文件的接口用例只验**字段契约**, 不依赖真实日线,
      故用合成日线替换 ⇒ 既快、又确定、又不制造跨测试副作用。
    """
    import quant_module as _qm

    def _fake_bars(code, days):
        out = []
        for i in range(max(40, min(days, 120))):
            px = 5000.0 + (i % 12) * 3.0
            out.append({"date": "2026-01-01", "open": px, "high": px * 1.01,
                        "low": px * 0.99, "close": px, "volume": 1000.0,
                        "open_interest": 20000.0})
        return out

    monkeypatch.setattr(_qm, "_load_daily_bars", _fake_bars)
    # ★ 还要堵住**天勤客户端本身**：`_load_daily_bars` 之外，基差/仓单/合约报价等路径
    #   也会经 `tq_source.fetch_*` 创建 TqApi —— 实测残留的 pending 任务正是
    #   `TqBaseApi._windows_patch()`（不是日线那条）。故把 tq_source 的取数入口一并返回 None。
    #   ⚠ quant_module 里用的是 `tq_source.xxx(...)`（动态属性）⇒ patch 模块属性即生效；
    #     若哪天有人改成 `from ... import xxx`，这条会静默失效（那时噪声会回来提醒）。
    import tradingagents.dataflows.tq_source as _tqs

    monkeypatch.setattr(_tqs, "fetch_daily_bars", lambda *a, **k: None)
    monkeypatch.setattr(_tqs, "fetch_minute_bars", lambda *a, **k: None)
    monkeypatch.setattr(_tqs, "fetch_realtime", lambda *a, **k: None)
    yield


@pytest.mark.unit
def test_routes_registered(client, _no_bg_ranking):
    """路由真的注册（401 ≠ 路由不存在, 故关鉴权后再打）。

    ★ 需要 `_no_bg_ranking`：`varieties=NOPE` 会被过滤 ⇒ **回退全池 21 品种** ⇒
    起后台真取数（实测不留 fixture 时产生 21 条天勤失败日志 + pending 任务）。
    """
    r = client.get("/api/fund/ranking?varieties=NOPE")
    assert r.status_code == 200, r.status_code
    d = r.get_json()
    assert d["ok"] and isinstance(d["rows"], list) and "metric" in d
    r2 = client.get("/api/fund/contracts?variety=TA")
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert "months" in d2 or "error" in d2


@pytest.mark.unit
def test_ranking_field_contract():
    """⑨ 表**行**的字段契约。

    【为什么改为直接调同步版】该接口已从"同步阻塞"改成"**后台线程 + 渐进发布**"：
      HTTP 用例返回时可能**一行都还没算出来**（本文件另有 fixture 掐掉后台重算）。
      而"行该有哪些字段"本就是 `_fund_xsection_one` 的契约 ⇒ 直接调同步版更贴切、也更快。
      （接口装配由 `test_frk_response_exposes_progress_fields` 用受控 state 覆盖。）
    """
    rows = fm._fund_rank_all(["TA", "CF"])
    assert len(rows) == 2, "同步版应返回两行，实测 %d" % len(rows)
    row = rows[0]
    for k in ("variety", "name", "inv_grade", "inv_mode", "ck_pct", "soc_pct",
              "vr_ratio", "vr_months", "prof_grade", "skill_vd", "skill_term",
              "skill_cyc", "skill_prof", "skill_net", "skill_n", "strategy",
              "inv_rank", "close"):
        assert k in row, f"截面行缺字段 {k}"
    # 分位必须是 0~1 的**比率**(前端乘 100 展示) —— 后端若改成百分数, 前端会显示 3100%
    for k in ("ck_pct", "soc_pct", "basis_pct"):
        v = row.get(k)
        if v is not None:
            assert 0 <= v <= 1, f"{k}={v} 不在 0~1(是比率不是百分数)"
    # 口径关系必须给出(共振/背离/分歧/仅X口径/无口径)
    assert row["inv_mode"], "未给出口径关系"
    # 票的取值域: ★ 用「同源校验」而不是写死白名单 —— 二维四象的 advice 有
    #   5 个取值(单边做多/单边做空/套利-正套/套利-反套/观望), 白名单会漏(第一版就漏了)。
    #   判据改为: 值必须是方法论纯函数或研判文件里的**字面量**, 防"上游改了措辞、
    #   这里贴了旧硬编码"这类静默漂移。
    srcs = (_src(ROOT / "quant_methodology.py"), _src(ROOT / "quant_verdict.py"))
    for k in ("skill_vd", "skill_term", "skill_cyc", "skill_prof"):
        v = row.get(k)
        if v is None:
            continue
        assert isinstance(v, str) and v.strip(), (k, v)
        assert not v.lower().startswith("none"), f"{k} 泄漏了字面量 None: {v!r}"
        assert any((f'"{v}"' in s) or (f"'{v}'" in s) for s in srcs), (
            f"{k}={v!r} 不是方法论/研判文件里的取值（口径漂移？）")


@pytest.mark.unit
def test_ranking_inv_rank_direction():
    """库存档位排序序: 低=+1(偏多) / 中=0 / 高=-1 —— 与"净票(做多=+)"同向。

    【为什么钉】若把低写成 -1, 用户按"库存档位"降序排会看到**高库存排最前**,
    与"低库存偏多"的语义完全相反(排序看着没坏, 结论却反了)。
    """
    # 同上：直接调同步版（接口已后台化，HTTP 用例可能一行都拿不到）
    rows = fm._fund_rank_all(["TA", "CF", "M"])
    m = {r["variety"]: r.get("inv_rank") for r in rows}
    r = {x["variety"]: x.get("inv_grade") for x in rows}
    for code, grade in r.items():
        if grade == "低":
            assert m[code] == 1, (code, grade, m[code])
        elif grade == "高":
            assert m[code] == -1, (code, grade, m[code])
        elif grade == "中":
            assert m[code] == 0, (code, grade, m[code])


def client_get(url: str) -> dict:
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    import web_app
    return web_app.app.test_client().get(url).get_json()


@pytest.mark.unit
def test_contracts_field_contract():
    d = client_get("/api/fund/contracts?variety=TA")
    assert d.get("ok"), d
    assert d.get("count") and d["count"] >= 2
    for k in ("month", "contract", "price", "oi", "volume", "months_to_delivery", "is_main"):
        assert k in d["months"][0], f"合约行缺字段 {k}"
    assert sum(1 for m in d["months"] if m["is_main"]) <= 1, "主力合约不唯一"
    assert d.get("shape") and d.get("spread") is not None


@pytest.mark.unit
def test_contracts_bad_params(_no_bg_ranking):
    """★ 需要 `_no_bg_ranking`：池外码回退**全池 21 品种** ⇒ 会起后台真取数
    （实测不加 fixture 时刷 14 条 `Task was destroyed but it is pending!`）。"""
    d = client_get("/api/fund/contracts?variety=")
    assert d.get("ok") is False
    d2 = client_get("/api/fund/ranking?varieties=ZZZZ")
    # 池外码被过滤 ⇒ 回退全池(与 quant/ranking 同一约定), 不该 500。
    # ⚠ 判据用 `total`（按 codes 算、立刻可用）而**不是** `count`：该接口现在是
    #   "后台计算 + 渐进发布"，首个请求返回时可能一行都还没算出来（count=0 是合法的）。
    assert d2["ok"] and d2["total"] >= 1, d2


# ===========================================================================
# D. 前端结构 + 交互绑定
# ===========================================================================
@pytest.mark.unit
def test_frontend_two_views():
    h = _h()
    assert 'id="f-overview-view"' in h, "缺全品种总览视图容器"
    assert 'id="f-detail-view"' in h, "缺单品种明细视图容器"
    # 明细默认隐藏(否则首屏会同时看到总览表和一长串单品种卡)
    i = h.index('id="f-detail-view"')
    assert 'style="display:none"' in h[i:i + 60], "明细视图未默认隐藏"
    # 两个视图各恰好一个
    assert h.count('id="f-overview-view"') == 1 and h.count('id="f-detail-view"') == 1


@pytest.mark.unit
def test_frontend_detail_view_wraps_price_tools_and_warrant():
    """原有内容必须**在**明细视图内 —— 否则"仓单也进明细"没做到。"""
    h = _h().replace("\r\n", "\n")
    ov = h.index('id="f-overview-view"')
    dv = h.index('id="f-detail-view"')
    assert ov < dv, "视图顺序反了"
    end = h.index("#f-detail-view" if False else "/#f-detail-view")
    seg = h[dv:end]
    for need in ('id="tab-fund-tools"', 'id="f-variety"', 'id="f-strategy-body"',
                 'id="f-votes-body"', 'id="tab-warrant-tools"', 'id="wt-table"'):
        assert need in seg, f"{need} 不在明细视图内"


@pytest.mark.unit
def test_frontend_new_table_card():
    h = _h()
    for i in ("f-rk-table", "f-rk-rows", "f-rk-metric", "f-rk-order", "f-rk-note",
              "f-rk-badge", "f-rk-stats"):
        assert f'id="{i}"' in h, f"新表缺容器 #{i}"
    # 19 列(表头 + 占位行 colspan 必须一致, 否则表格错位)
    assert 'id="f-rk-rows"' in h
    assert h.count('colspan="19"') >= 1, "缺 19 列占位行"


@pytest.mark.unit
def test_frontend_row_click_opens_detail():
    """★ 交互必须真的绑上 —— 本项目已踩过"函数写了、id 有了、就是没绑事件"。"""
    h = _h()
    assert "onclick=\"App.fOpenDetail(" in h, "行没有绑定 fOpenDetail"
    assert "fOpenDetail(code) {" in h, "fOpenDetail 未定义"
    assert "fBackOverview() {" in h and "App.fBackOverview()" in h, "缺返回总览的绑定/实现"


@pytest.mark.unit
def test_frontend_month_select_stops_propagation():
    """★ 月份下拉必须阻止冒泡，否则"选月份"会同时触发"跳详情"。

    【变异自检实测】判据不能只查"附近窗口里有没有 stopPropagation" —— 该 `<select>` 的
    **外层 `<td>` 上也有一个**同名 onclick（双保险） ⇒ 把 select 自己那个删掉, 窗口里
    照样能搜到 td 的那个, 护栏**假过**(M14 存活)。⇒ 必须精确取 select 标签自身。
    """
    h = _h()
    start = h.rindex("<select", 0, h.index('class="f-rk-mon"'))
    tag = h[start:h.index(">", start)]
    assert "stopPropagation" in tag, (
        "月份下拉的 <select> 自身没阻止冒泡（只有外层 td 有, 内部点击仍会冒泡触发跳页）"
    )


@pytest.mark.unit
def test_frontend_switchtab_dispatches_by_view():
    h = _h()
    i = h.index("if (t === 'fund') {")
    seg = h[i:i + 620]
    assert "f-overview-view" in seg, "switchTab 未按视图态分派"
    assert "loadFundRanking()" in seg and "loadFund()" in seg, "两个分支缺一"


@pytest.mark.unit
def test_frontend_pending_sym_adopted():
    """默认落在总览 ⇒ #f-variety 未初始化; 点行的品种必须交给 pending 槽采用。"""
    h = _h()
    assert "_fPendingSym: null" in h, "缺 pending 槽"
    i = h.index("this._fPendingSym = null;")
    assert "sel.value = _fWant ||" in h[i:i + 400], "meta 未采用 pending 品种（点行会被静默换成默认品种）"
    assert "this._fPendingSym = code;" in h, "fOpenDetail 未写入 pending 槽"


@pytest.mark.unit
def test_frontend_sec_index_version_bumped():
    h = _h()
    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30；
    #   同日新增「采集通道状态巡检条」(#res-ingest-status) ⇒ 30 升 31。
    # 【2026-10-21】基本面页重构为 A/B 两层（拆 5 张卡 + 新增 1 张）⇒ 再 +1
    # 【2026-10-21】数据计算面板从技术面迁入基本面 A9（两页卡片归属都变了）⇒ 再 +1
    assert "_SEC_IDX_VER: '33'," in h, "_SEC_IDX_VER 未随卡片增删 +1"
    assert "_SEC_IDX_VER: '32'," not in h


# ===========================================================================
# E. 顺带修掉的 ① 卡基差率 100× 显示错误
# ===========================================================================
# ===========================================================================
# F. 按指定合约月重算该行（2026-10-16 用户要求）
# ===========================================================================
@pytest.mark.unit
def test_vote_inputs_shared_by_both_paths():
    """★ 4 票的**取数输入**必须由同一个助手提供（默认口径 与 按合约月重算 共用）。

    各写一遍的话，哪天某处换了窗口（如基差率 180→120 天）另一处不会跟着变
    ⇒ 同一品种在两个入口**四票不一致**。判定口径本身仍在 _fund_skill_votes。
    """
    src = _src(ROOT / "fund_module.py")
    assert "\ndef _fund_vote_inputs(" in src, "缺共享助手 _fund_vote_inputs"
    for fn in ("_fund_xsection_one", "_fund_month_row"):
        body = src[src.index("def %s(" % fn):]
        body = body[:body.index("\ndef ", 10)]
        assert "_fund_vote_inputs(" in body, f"{fn} 没用共享助手装 4 票输入"


@pytest.mark.unit
def test_month_row_reuses_existing_caliber():
    """按合约月重算也不得引入新口径。"""
    src = _src(ROOT / "fund_module.py")
    body = src[src.index("def _fund_month_row("):]
    body = body[:body.index("\n@fund_bp.route")]
    for need in ("_vr_ratio_of(", "_strategy_verdict(", "_fund_skill_votes(", "_lot_factor("):
        assert need in body, f"_fund_month_row 没有复用 {need}"
    assert "oi_f / lots_f" not in body, "月度行里出现了第二处比值除法"


@pytest.mark.unit
def test_month_row_blank_month_returns_default_row():
    """month 为空 ⇒ 返回**默认口径整行**（与拉表同一函数，数值必然一致）。"""
    d = client_get("/api/fund/xsection?variety=TA")
    assert d.get("ok"), d
    assert d.get("month") == ""
    row = d.get("row") or {}
    assert row.get("vr_month"), "默认行应带主力月"
    assert row.get("basis_pct") is not None, "默认行的基差分位应由 \u6210\u5f53\u7ed9\u51fa(不是空)"
    assert not row.get("month_mode"), "默认行不该带月份口径标记"


@pytest.mark.unit
def test_month_row_bad_month_reports_available():
    """无效月份要**如实报错并列出可用月份**（不要静默返回默认行）。"""
    d = client_get("/api/fund/xsection?variety=TA&month=9999")
    assert d.get("ok") is False
    assert "9999" in d.get("error", "") and "可用" in d.get("error", ""), d.get("error")


@pytest.mark.unit
def test_month_row_arithmetic_matches_contracts_endpoint():
    """★ 跨接口核对：基差必须 == 现货 − **该月合约价**（价取自已有的合约报价接口）。

    这条是本功能的核心正确性判据 —— 只断言"字段存在"是测不到"换月真的生效"的。
    """
    d = client_get("/api/fund/xsection?variety=TA&month=2705")
    if not d.get("ok"):
        pytest.skip("该品种无 2705 合约（取数边界）")
    row = d["row"]
    spot = row.get("basis_spot")
    if spot is None:
        pytest.skip("该品种无现货口径（基差本不可得）")
    c = client_get("/api/fund/contracts?variety=TA")
    px = next((m.get("price") for m in (c.get("months") or [])
               if str(m.get("month")) == "2705"), None)
    assert px is not None, "合约接口里找不到 2705 的报价"
    assert abs(row["basis"] - (spot - px)) < 0.01, (
        f"基差 != 现货 − 该月价：{row['basis']} vs {spot} − {px}"
    )
    assert abs(row["basis_rate"] - row["basis"] / px) < 1e-3, "基差率未按该月价归一"
    assert row["vr_month"] == "2705" and row["vr_contract"].endswith("2705"), "虚实比没换到该月"


@pytest.mark.unit
def test_month_row_percentile_blanked_with_note():
    """★ 基差分位**必须置空并注明**（不许用主力序列冒充）—— 用户 2026-10-16 拍板。"""
    d = client_get("/api/fund/xsection?variety=TA&month=2705")
    if not d.get("ok"):
        pytest.skip("取数边界")
    row = d["row"]
    assert row.get("basis_pct") is None, "选定月的基差分位必须是 None"
    note = row.get("basis_pct_note") or ""
    assert "历史序列" in note and "2705" in note, f"分位置空的原因没写清: {note}"


@pytest.mark.unit
def test_month_row_keeps_contract_independent_votes():
    """与合约无关的票必须**保持**（库存周期/利润/期限结构），二维四象如实置空 + 注明。"""
    d = client_get("/api/fund/xsection?variety=TA&month=2705")
    if not d.get("ok"):
        pytest.skip("取数边界")
    row = d["row"]
    assert row.get("skill_vd") is None and row.get("skill_vd_gap"), "二维四象应置空并注明原因"
    assert "历史序列" in row["skill_vd_gap"], row["skill_vd_gap"]
    # ★ 判据用「与默认行**逐票相等**」而不是「非空」——
    #   本文件的 autouse fixture 会把日线桩成合成序列(避免 tqsdk 噪声), 而合成价格会让
    #   库存周期判为"阶段不明"⇒ 该票**本来就不投**(非空断言会误报, 第一版就这么错了)。
    #   真正要守的不变量是: **与合约无关的票, 在两行里必须一致**。
    d0 = client_get("/api/fund/xsection?variety=TA")
    base = d0.get("row") or {}
    for k in ("skill_term", "skill_cyc", "skill_prof"):
        assert row.get(k) == base.get(k), (
            f"与合约无关的票 {k} 在月度行与默认行不一致：{row.get(k)!r} vs {base.get(k)!r}"
        )
    # 净票/票数只统计**本行真正有效的票**（四象已置空 ⇒ 3 票上限）
    assert row.get("skill_n") in (0, 1, 2, 3), row.get("skill_n")
    assert isinstance(row.get("skill_net"), int)
    keys = ("term", "cyc", "prof")
    # 【2026-10-19】★ 不许再抄一份映射：此前这条测试复制了生产代码的 dirmap，
    #   生产代码漏「偏多/偏空」时测试跟着一起错 ⇒ 恒绿，属于自指陷阱。
    #   现改为引用 vote_semantics 同一份表（独立于本测试的期望值构造）。
    from vote_semantics import sign as _sign
    expect = sum(_sign(row.get("skill_" + k)) for k in keys)
    assert row.get("skill_net") == expect, "净票未按本行有效票重算"
    assert row.get("skill_n") == sum(1 for k in keys if row.get("skill_" + k))


@pytest.mark.unit
def test_month_row_keeps_inventory_and_profit_untouched():
    """库存族/利润与合约无关 ⇒ **不得**出现在被替换的字段里（前端按字段合并）。"""
    d = client_get("/api/fund/xsection?variety=TA&month=2705")
    if not d.get("ok"):
        pytest.skip("取数边界")
    row = d["row"]
    for k in ("inv_grade", "inv_mode", "ck_pct", "ck_chg5", "soc_pct", "soc_chg4", "prof_grade"):
        assert k not in row, f"{k} 不该被月度重算覆盖（它不依赖合约）"


@pytest.mark.unit
def test_month_row_provides_term_anchor_without_overwriting_vote():
    """★ 期限结构：**原票保留** + 另起一个「自该月看」的读数（用户拍板）。"""
    d = client_get("/api/fund/xsection?variety=TA&month=2705")
    if not d.get("ok"):
        pytest.skip("取数边界")
    row = d["row"]
    a = row.get("term_anchor")
    assert a, "缺「自该月看」的读数"
    assert a.get("from_month") == "2705" and a.get("shape") and a.get("spread") is not None
    assert "未改变" in (a.get("note") or ""), "锚定读数的说明必须讲明原票未被覆盖"
    # 原票仍在（且与默认口径一致 —— 它不依赖合约）
    assert row.get("skill_term"), "期限结构原票被清掉了"


@pytest.mark.unit
def test_frontend_month_switch_recomputes_row():
    """前端：选月份 → 调 /api/fund/xsection 重算该行；选「默认(主力)」→ 还原。"""
    h = _h()
    assert "async _fRecomputeRow(code, month)" in h, "缺行重算实现"
    assert "/api/fund/xsection?variety=" in h, "没调重算接口"
    assert "_fRestoreRow(code)" in h, "缺恢复默认口径"
    # _fExpandContracts 必须**真的**串上这两条（写在别处等于没接线）
    i = h.index("async _fExpandContracts(code, month, el)")
    body = h[i:i + 1600]
    assert "await this._fRecomputeRow(code, month);" in body, "选月份没有触发重算"
    assert "this._fRestoreRow(code);" in body, "选空值没有触发还原"
    # 重渲染会清掉展开的明细 ⇒ 必须补回来
    assert "await this._fRenderContracts(code, month);" in body, "重算后没有把合约明细补回来"


@pytest.mark.unit
def test_frontend_month_mode_is_marked():
    """切到月份口径的行必须**看得出来**（否则用户会以为表格口径统一）。"""
    h = _h()
    assert "r.month_mode" in h, "行内没读月份口径标记"
    assert "inset 3px 0 0 var(--amber)" in h, "月份口径行没有视觉标记"
    assert "已切换到 " in h and "恢复默认" in h, "缺切换/恢复的提示文案"
    # 空选项改名 ⇒ 它现在的语义是"恢复默认"
    assert '<option value="">默认(主力)</option>' in h, "空选项未改名为「默认(主力)」"


@pytest.mark.unit
def test_frontend_baseline_snapshot_for_restore():
    """恢复默认口径必须用**基线快照**零请求还原（重算不可逆，算不回平台的默认值）。"""
    h = _h()
    assert "this._fRkBase = JSON.parse(JSON.stringify(d));" in h, "拉表时没留基线快照"
    i = h.index("_fRestoreRow(code) {")
    body = h[i:i + 900]
    assert "_fRkBase" in body and "_fRkData.rows[i] = " in body, "还原没走基线快照"


@pytest.mark.unit
def test_basis_rate_display_is_percent():
    """① 卡的基差率必须 ×100 再显示。

    【事故】`basis_rate` 字段是**比率**(实测 TA basis=545/fut=6488 → 0.084 = 8.4%),
    原写 `Number(bs.rate).toFixed(2) + '%'` ⇒ 显示 **0.08%**, 与真值差 **100 倍**。
    同文件 11310 行的「基差率%」图表一直是对的(`r.basis_rate * 100`) ⇒ 两处口径不一致。
    """
    h = _h()
    assert "(Number(bs.rate) * 100).toFixed(2) + '%'" in h, "① 卡基差率仍未 ×100（差 100 倍）"
    assert "Number(bs.rate).toFixed(2) + '%'" not in h, "仍存在未 ×100 的基差率显示"
    # 与图表那处的口径一致
    assert "(r.basis_rate * 100).toFixed(2)" in h, "图表侧的 ×100 口径丢了（两处又不同源）"


# ===========================================================================
# G. 筛选 / 排序 / 列显隐（2026-10-16 用户要求：与技术面同款的"选择分类与排序"）
#    ⚠ 行为级验证在 probe/_verify_ffilter_rules.mjs（抽源码喂合成行，确定性跑）
#      与 probe/_e2e_fund_filter.mjs（真浏览器）。这里守**结构契约与口径同源**。
# ===========================================================================
@pytest.mark.unit
def test_filter_controls_present():
    """① 控制区必须齐备（少一个用户就没法用某类筛选）。"""
    h = _h()
    for i in ("f-rk-search", "f-rk-pools", "f-rk-dims", "f-rk-colspanel", "f-rk-fcount"):
        assert 'id="%s"' % i in h, "缺控制区 #%s" % i
    assert 'placeholder="搜索代码 / 名称…"' in h, "缺搜索框"
    assert "App._fRkToggleColsPanel()" in h and "App._fRkClearFilters()" in h, "缺「列」或「清除筛选」按钮"


@pytest.mark.unit
def test_th_order_matches_columns_constant():
    """★★ 19 个 th 的 `data-col` 顺序必须与 `_FRK_COLS` **严格一致**。

    【为什么是硬判据】列显隐靠 `nth-child(序位)` 实现 —— 表头顺序与常量表**错一格**，
    隐藏的就是**另一列**（用户点"隐藏仓单分位"，消失的却是"仓单5日"）。
    这类错位静态看不出来、也不会报错。
    """
    h = _h()
    # ⚠ 列名含数字（ck_chg5 / soc_chg4）⇒ 字符类必须带 0-9，否则静默漏列（实测漏 2 个）
    th_cols = re.findall(r'<th data-col="([a-z_0-9]+)"', h)
    m = re.search(r"_FRK_COLS: \[(.*?)\n\],", h, re.S)
    assert m, "未找到 _FRK_COLS"
    # ⚠ 同一坑：常量里的列名同样含数字（ck_chg5 / soc_chg4）
    const_cols = re.findall(r"key: '([a-z_0-9]+)'", m.group(1))
    assert len(th_cols) == 19, "th 的 data-col 应有 19 个，实测 %d" % len(th_cols)
    assert th_cols == const_cols, "th 顺序与 _FRK_COLS 不一致：\n  th   = %s\n  const= %s" % (th_cols, const_cols)


@pytest.mark.unit
def test_sortable_columns_contract():
    """可排列 = 非 fixed、非控件列（合约）；且排序下拉只列**表里看得到**的指标。

    【为什么钉这条】旧下拉有一项 `chg_pct`（当日涨跌%）而**表里没有这一列** ⇒
    用户能选、能排，但看不到在按什么排（本次已移除并改为只列可见列）。
    """
    h = _h()
    ths = re.findall(r'<th data-col="([a-z_0-9]+)"([^>]*)>', h)
    sortable = [c for c, attrs in ths if 'data-metric' in attrs]
    assert "contract" not in sortable, "「合约」是控件列，不该可排"
    assert "idx" not in sortable, "「#」不该可排"
    assert len(sortable) == 17, "可排列应 17 个，实测 %d" % len(sortable)
    # 下拉里的每个 value（除 net 这个非列排序键）都必须能在表头 data-metric 里找到
    m = re.search(r'<select id="f-rk-metric".*?</select>', h, re.S)
    assert m, "未找到排序指标下拉"
    vals = re.findall(r'<option value="([a-z_]+)"', m.group(0))
    extra = [v for v in vals if v not in sortable and v != "net"]
    assert not extra, "下拉里有表里看不到的排序项：%s（用户会困惑「在按什么排」）" % extra
    assert "chg_pct" not in vals, "旧的 chg_pct（表里无该列）又回来了"


@pytest.mark.unit
def test_filter_dims_and_short_uses_App():
    """8 个筛选维度：key 必须是行字段（或按规则派生）；标签精简不得用 `this.`。

    【★ 本次踩过的 bug】`_FRK_DIMS` 里写 `short: v => this._fRkShortVd(v)` ——
    对象字面量的**属性初始化器**求值时 `this` **不是该对象**（是外层词法 this）⇒
    调用即抛 "is not a function"，异常冒到 loadFundRanking 的 catch ⇒ **整张表显示"加载失败"**。
    浏览器 E2E 抓到的。这里加静态兜底。
    """
    h = _h()
    m = re.search(r"_FRK_DIMS: \[(.*?)\n\],", h, re.S)
    assert m, "未找到 _FRK_DIMS"
    keys = re.findall(r"key: '([a-z_]+)'", m.group(1))
    assert len(keys) == 8, "应有 8 个筛选维度，实测 %d: %s" % (len(keys), keys)
    assert "inv_mode_kind" in keys, "缺「口径关系」维度"
    for k in ("basis_side", "inv_grade", "skill_term", "skill_cyc", "skill_vd",
              "prof_grade", "strategy"):
        assert k in keys, "缺筛选维度 %s" % k
    assert "this._fRkShort" not in h, "★ short 里又用了 `this.`（对象字面量属性初始化器里 this 不是 App）"
    assert h.count("App._fRkShort") == 2, "short 应显式引用 App（实测 %d 处）" % h.count("App._fRkShort")


@pytest.mark.unit
def test_frontend_sort_does_not_request():
    """★ 排序/筛选必须走**纯前端**（字符串列后端排不了）。

    判据：两个下拉的 onchange 指向 `_fRkSetSort`（前端重排），且 `_fRkSetSort` 体内**没有 fetch**。
    「拉取/刷新」按钮仍保留 `loadFundRanking(1)`（那是真要重新取数）。
    """
    h = _h()
    assert h.count('onchange="App._fRkSetSort()"') == 2, "两个下拉都该指向 _fRkSetSort"
    assert "onchange=\"App.loadFundRanking()\"" not in h, "下拉仍走接口（点文本列会「没反应」）"
    i = h.index("_fRkSetSort() {")
    body = h[i:i + 700]
    # ⚠ 方法结尾是 `\n},`（本块由补丁插入，行首无缩进）—— 写成 `\n },` 会 ValueError
    end = body.index("\n},")
    assert "fetch(" not in body[:end], "_fRkSetSort 里有 fetch（白花一次请求）"
    assert "_renderFundRanking()" in body[:end], "_fRkSetSort 没有触发重渲染"
    # 「拉取/刷新」按钮仍在
    assert "App.loadFundRanking(1)" in h, "「拉取/刷新」按钮丢了"


@pytest.mark.unit
def test_stats_use_all_rows_not_filtered():
    """统计条按**全量**算（否则数字随筛选乱跳）—— 与技术面总览同一纪律。"""
    h = _h()
    i = h.index("_renderFundRanking() {")
    body = h[i:i + 6000]
    assert "const all = d.rows || [];" in body, "没区分 all 与 rows"
    assert "const rows = this._fRkVisibleRows();" in body, "行集没走筛选+排序"
    seg = body[body.index("const st = document.getElementById('f-rk-stats')"):]
    seg = seg[:seg.index("// 【2026-10-16") if "// 【2026-10-16" in seg else 900]
    assert "all.filter(f)" in seg or "c = f => all.filter" in seg, "统计条改回按筛选后的 rows 算了"
    assert "['品种', all.length" in seg, "「品种」数应取全量"


@pytest.mark.unit
def test_unknown_values_sort_last():
    """★ 排序"未知恒最后"必须显式实现（与后端 sort_key 的 `not ok` 同纪律）。

    没有它的话：升序时 `null` 会排到最前面（用户以为"最差/最好"），且升降序表现不一致。
    """
    h = _h()
    assert "_fRkNum(v) {" in h, "缺「未知恒最后」的取值函数"
    i = h.index("_fRkSortVal(r, key, freq) {")
    body = h[i:i + 1800]
    assert "g: 1, v: 0" in body, "没有把未知值标成 g=1"
    assert "ka.g !== kb.g" in h, "排序比较里没用分组优先（未知恒最后失效）"
    # 数值比较用排序后的比较器（空值不参与正负反号）
    j = h.index("arr.sort((a, b) => {")
    cmp_body = h[j:j + 400]
    assert "ka.g !== kb.g" in cmp_body, "比较器缺分组判断"


@pytest.mark.unit
def test_cols_css_has_expand_row_guard():
    """列显隐用注入 CSS 的 nth-child；行内展开行要显式兜底。"""
    h = _h()
    assert "_fRkApplyCols() {" in h, "缺列显隐实现"
    i = h.index("_fRkApplyCols() {")
    body = h[i:i + 1200]
    assert "nth-child(" in body, "没用按序位的 nth-child 规则"
    assert 'f-rk-exp-' in body and "table-cell !important" in body, \
        "缺行内展开行的兜底规则（第 1 列隐藏时会连带把展开行藏掉）"
    assert "agentsense.fundRanking.cols" in h, "列偏好没存 localStorage（刷新就丢）"
    # fixed 列永不隐藏
    assert "if (col && col.fixed) return false;" in h, "fixed 列没有豁免（# 或 品种 会被藏掉）"


# ── 后端：口径同源（本表**不许**新造分类口径）───────────────────────────────
@pytest.mark.unit
def test_sector_bucket_same_source_as_quant():
    """★★ 板块归并必须**复用 quant 的同一个函数**（不另写一份映射表）。

    【为什么重要】板块是与技术面共用的筛选维度：若本表自己 `split('(')` 取前缀，
    「能化(燃料)→能化」就会与技术面的「能化(燃料)→能源」**不一致** ⇒ 同一品种在两页
    归到不同板块，用户看到两个"能化"数量不同。

    【为什么这条不取数】本文件的 autouse fixture 已掐掉后台重算（见 `_no_bg_ranking`），
    走 HTTP 拿不到行。而"归并是否同源"**本就不需要真实行情** —— 它只依赖元数据：
      · 静态：行构造函数里调的就是 `q._sector_bucket(...)`；
      · 动态：`fund_module` 与 `quant_module` 拿到的是**同一个模块对象**（防 fm 内另有同名函数遮蔽），
        且对全池逐个品种的归并结果与 quant 逐字相同（含"精确表"特例）。
    ★ 真正的端到端（真数据 → 真渲染 → 筛出的行确实同属一个板块）在
      `probe/_e2e_fund_filter.mjs`（"筛出的行确实都属于「能化」（按 sector_bucket）"）。
    """
    s = _src(ROOT / "fund_module.py")
    assert '"sector_bucket": q._sector_bucket(_sector_cn)' in s, \
        "行里没有复用 quant 的归并函数（可能自己写了一份映射）"
    assert fm._q() is qm, "fund_module 用的不是同一个 quant_module 对象（可能被遮蔽）"
    # 逐品种：本表行的 sector 就是 meta 的原文 ⇒ 期望值由同一函数给出
    for code in fm._pool():
        raw = fm._meta(code).get("sector_cn") or ""
        assert fm._q()._sector_bucket(raw) == qm._sector_bucket(raw), \
            "%s 的归并在两个模块里不一致" % code
    # 「精确表」特例：能化(燃料) 归「能源」而不是「能化」（与技术面一致）
    assert qm._sector_bucket("能化(燃料)") == "能源"
    assert qm._sector_bucket("能化(化工)") == "能化", "前缀兜底失效（扩池新增子板块会漏）"


@pytest.mark.unit
def test_pools_derived_and_consistent(_no_bg_ranking):
    """接口下发的 `pools` 必须由**池内品种**派生，且与技术面同一归并函数。"""
    d = client_get("/api/fund/ranking?varieties=TA,FU,SC,BR,CF,PX")
    pools = d.get("pools") or []
    assert pools and pools[0] == "全部品种", "pools 首项应是「全部品种」（与技术面同形）"
    rows = d.get("rows") or []
    shown = {r.get("sector_bucket") for r in rows if r.get("sector_bucket")}
    assert shown <= set(pools), "出现了不在 pools 里的板块：%s" % (shown - set(pools))
    # 顺序固定（sorted）—— 否则胶囊位置会随请求抖动
    assert pools[1:] == sorted(pools[1:]), "pools 未按固定顺序下发（胶囊会跳位）"


@pytest.mark.unit
def test_inv_grade_sort_reads_backend_rank():
    """★ 库存档位排序必须**读后端算好的 `inv_rank`**，前端不得另写一套档位→数值映射。

    【为什么必须钉】前端写 `{'低':1,'中':0,'高':-1}` 与后端 `_INV_GRADE_RANK` **当前行为等价**
    ⇒ 行为级测试（含规则复核）**杀不掉**这种重复实现。但只要后端改口径（如把"中"拆成中低/中高），
    前端那份不会跟着变 ⇒ 两页排序结果不同。这类"重复口径"只能靠静态判据守。
    """
    h = _h()
    i = h.index("_fRkSortVal(r, key, freq) {")
    body = h[i:i + 2200]
    j = body.index("key === 'inv_grade'")
    seg = body[j:j + 420]
    assert "r.inv_rank" in seg, "库存档位排序没读后端 inv_rank（前端另写了一份档位映射）"
    assert "低': 1" not in seg and "'低':1" not in seg, "前端出现了第二套档位→数值映射"


@pytest.mark.unit
def test_pools_derived_from_pool_codes():
    """★ `pools` 必须由**池内品种**派生（扩池自动跟随），不得硬编码。

    ⚠ 用 `codes` 而不是 `rows`：渐进阶段/个别源失败时 rows 可能不全，
    用 rows 会让可选板块**越筛越少**（技术面 api_quant_overview 同一处已有此纪律）。
    """
    s = _src(ROOT / "fund_module.py")
    i = s.index("_pools = sorted(")
    seg = s[i:i + 420]
    assert "_sector_bucket(" in seg, "pools 没有走 _sector_bucket（与技术面不同源）"
    assert "for c in codes" in seg, "pools 不是按池内品种 codes 派生（用 rows 会让板块越筛越少）"
    assert s.count("_pools = sorted(") == 1, "pools 出现了第二处定义"


# ===========================================================================
# H. 缓存 + 定期刷新（2026-10-16 用户要求：更新模式对齐技术面量化）
#    ⚠ 行为级验证在 probe/_e2e_fund_refresh.mjs（真浏览器测时序/停表/旧表保留）；
#      这里守**跨文件对齐**、**状态机语义**与**前端结构契约**。
# ===========================================================================
@pytest.mark.unit
def test_refresh_period_aligned_across_files():
    """★★ 前端刷新周期必须与后端 TTL **数值对齐**，否则必然出问题：

    · 前端周期 **短于** TTL ⇒ 每次到点重取都命中同一份缓存，永远看不到更新（白轮询）；
    · 前端周期 **长于** TTL ⇒ 每次重取时缓存已过期，每次都白重算一整轮。
    两处分散在不同文件（py / html），只能靠这条判据钉住 —— 改一处忘另一处必红。
    """
    s = _src(ROOT / "fund_module.py")
    m = re.search(r"_TTL_FUND_RANK\s*=\s*(\d+)", s)
    assert m, "未找到 _TTL_FUND_RANK"
    ttl = int(m.group(1))
    h = _h()
    m2 = re.search(r"_FRK_REFRESH_MS:\s*(\d+)\s*\*\s*60\s*\*\s*1000", h)
    assert m2, "未找到 _FRK_REFRESH_MS（或写的不是 `N * 60 * 1000`）"
    ms = int(m2.group(1))
    assert ttl == ms * 60, (
        "前端刷新周期 %d 分钟与后端 TTL %d 秒**不对齐**（会白轮询或白重算）" % (ms, ttl))
    assert ttl == 900 and ms == 15, "本项目的口径是 15 分钟（与技术面总览一致）"


@pytest.mark.unit
def test_frk_worker_consumes_carry(monkeypatch):
    """★ carry 必须被 worker **真正消费**（不是只留在 state 里）。

    【为什么单列一条】`_frk_start` 把旧 rows 留在 state 里，但那**不保证** worker 会用 ——
    技术面那轮实测：改成 `rows = {}` 后护栏照样全绿（旧 state 仍在，只是第一次发布就把它
    盖成空表）。判据：carry 里放一个**不在 codes 里**的键，跑完仍应在结果里 —— 它只可能来自 carry。
    """
    key = "fund_rank:__carry_test__"
    fm._FRK_STATE[key] = {"rows": {}, "done": True, "refreshing": True, "stale": True,
                          "refreshed": 0, "err": "", "ts": 0.0}
    monkeypatch.setattr(fm, "_fund_xsection_one", lambda c: {"variety": c, "name": c})
    try:
        fm._frk_worker(key, ["TA"], carry={"XX": {"variety": "XX", "name": "旧行"}})
        st = fm._FRK_STATE[key]
        assert "XX" in (st.get("rows") or {}), (
            "worker 没消费 carry —— 旧结果会被第一轮发布抹掉（用户看到表格瞬间变空）")
        assert st["done"] is True
    finally:
        fm._FRK_STATE.pop(key, None)


@pytest.mark.unit
def test_frk_publish_keeps_done_true_during_refresh(monkeypatch):
    """★ 刷新**过程中** done 必须全程为 True（否则前端把已显示的表换成占位行，"秒开"毁掉）。

    【为什么用 `_fund_xsection_one` 当观察点】它是 worker 每完成一个品种都会调的钩子 ⇒
    在那里读 state 就能看到**刷新途中**的 done。这是唯一能区分
    `done = done or had_old` 与 `done = done` 的观察点（看最终值区分不出来，最后都会 True）。
    """
    key = "fund_rank:__done_refresh_test__"
    fm._FRK_STATE[key] = {"rows": {"XX": {"variety": "XX"}}, "done": True, "refreshing": True,
                          "stale": True, "refreshed": 0, "err": "", "ts": 0.0}
    seen: list = []
    lock = threading.Lock()

    def _one(_c):
        # 【★ 为什么要有"串行化 + 等前序 publish"这一段】worker 是**并发**提交的
        #   （`ThreadPoolExecutor(_RANK_PARALLEL)`），于是所有 `_one` 可能在**任何 publish
        #   发生之前**就都读完了 state ⇒ 读到的全是测试摆的初始值 ⇒
        #   把 `done = done or had_old` 改成 `done = done` 也**照样通过**（变异自检实测存活）。
        #   正解：用一把锁把 `_one` 串行化，并让第 n 个（n≥1）**等到 `refreshed >= n`**
        #   —— 那意味着"前 n 个品种已完成并 publish 过"，此时读到的才是**刷新途中**的真实值。
        with lock:
            for _ in range(400):
                st0 = fm._FRK_STATE.get(key) or {}
                if int(st0.get("refreshed") or 0) >= len(seen):
                    break
                time.sleep(0.01)
            seen.append(bool((fm._FRK_STATE.get(key) or {}).get("done")))
        return {"variety": _c, "name": _c}

    monkeypatch.setattr(fm, "_fund_xsection_one", _one)
    try:
        fm._frk_worker(key, ["TA", "CF", "M"], carry={"XX": {"variety": "XX"}})
        assert len(seen) == 3, "观察点触发次数不对：%d" % len(seen)
        # 第 1 个读的是初始值（必 True）；第 2、3 个读的是**首次 publish 之后**的值 ⇒
        # 只有它俩能区分 `done or had_old` 与 `done`。
        assert all(x is True for x in seen), (
            "刷新途中 done 变成了 False —— 前端会把已显示的表换成占位行：%s" % seen)
        assert fm._FRK_STATE[key]["done"] is True
    finally:
        fm._FRK_STATE.pop(key, None)


@pytest.mark.unit
def test_frk_start_is_idempotent_and_ttl_aware(monkeypatch):
    """★ 三态语义（与量化总览同一条纪律，必须测**行为**而不是只查常量）：
    ① 已有后台刷新在跑 ⇒ 不再起第二个线程；
    ② TTL 内且新鲜 ⇒ 不起（命中缓存）；
    ③ 超 TTL ⇒ 才起，且**保留旧结果**（carry 非空、done 先为 True）。
    """
    started: list = []

    class _T:
        def __init__(self, *a, **k):
            started.append(k.get("name") or (a[0] if a else "?"))

        def start(self):
            return None

    monkeypatch.setattr(fm.threading, "Thread", _T)
    key = fm._frk_state_key(["TA", "CF"])
    try:
        # ① 在跑中 ⇒ 不起新线程
        fm._FRK_STATE[key] = {"rows": {"TA": {}}, "done": True, "refreshing": True,
                              "stale": True, "refreshed": 0, "err": "", "ts": 0.0}
        n0 = len(started)
        fm._frk_start(["TA", "CF"])
        assert len(started) == n0, "已有刷新在跑却又起了一个线程"

        # ② 新鲜 ⇒ 不起
        fm._FRK_STATE[key] = {"rows": {"TA": {}, "CF": {}}, "done": True, "refreshing": False,
                              "stale": False, "refreshed": 2, "err": "", "ts": time.time()}
        n1 = len(started)
        fm._frk_start(["TA", "CF"])
        assert len(started) == n1, "TTL 内仍起了线程（会白重算一轮）"

        # ③ 超 TTL ⇒ 起，且**保留旧结果**
        fm._FRK_STATE[key]["ts"] = time.time() - fm._TTL_FUND_RANK - 10
        n2 = len(started)
        fm._frk_start(["TA", "CF"])
        assert len(started) == n2 + 1, "超 TTL 却不起线程（永远不更新）"
        st = fm._FRK_STATE[key]
        assert st["refreshing"] is True and st["stale"] is True
        assert st["done"] is True, "重算时把 done 清成 False ⇒ 前端会回到占位行（秒开被毁）"
        assert set(st["rows"].keys()) == {"TA", "CF"}, "旧结果没被保留（carry 丢了）"

        # ④ force ⇒ 即使新鲜也起
        fm._FRK_STATE[key] = {"rows": {"TA": {}}, "done": True, "refreshing": False,
                              "stale": False, "refreshed": 1, "err": "", "ts": time.time()}
        n3 = len(started)
        fm._frk_start(["TA", "CF"], force=True)
        assert len(started) == n3 + 1, "force 未生效（用户点「拉取/刷新」不会重算）"
    finally:
        fm._FRK_STATE.pop(key, None)


@pytest.mark.unit
def test_frk_start_seeds_carry_from_persistent_cache(monkeypatch):
    """★ 进程内无旧结果时，carry 应来自**持久缓存** ⇒ 进程重启后第一次访问也能秒开。"""
    started: list = []

    class _T:
        def __init__(self, *a, **k):
            started.append(1)

        def start(self):
            return None

    monkeypatch.setattr(fm.threading, "Thread", _T)
    key = fm._frk_state_key(["TA"])
    fm._FRK_STATE.pop(key, None)
    monkeypatch.setattr(fm._q(), "_cache_get",
                        lambda k, ttl: [{"variety": "TA", "name": "缓存里的旧行"}])
    try:
        fm._frk_start(["TA"])
        st = fm._FRK_STATE[key]
        assert st["done"] is True, "没把持久缓存当 carry ⇒ 重启后第一次访问仍要干等一整轮"
        assert st["rows"]["TA"]["name"] == "缓存里的旧行"
    finally:
        fm._FRK_STATE.pop(key, None)


@pytest.mark.unit
def test_frk_cached_flag_reflects_refreshing(monkeypatch):
    """★ `cached` 必须反映"本次拿到的是一份**没在刷新**的新鲜结果"。

    原实现是 `not refresh` —— 只要不是显式刷新就报"命中缓存"，哪怕就是**刚现算出来**的
    （技术面实测第一轮就 cached=true，是假的）。这里用受控 meta 直接验路由组装。
    """
    monkeypatch.setattr(fm, "_frk_start", lambda codes, force=False: "k")
    monkeypatch.setattr(fm, "_frk_snapshot",
                        lambda key, codes: ([{"variety": "TA", "name": "PTA"}], True, "",
                                            {"refreshing": True, "stale": True,
                                             "refreshed": 0, "ts": 0.0}))
    d = client_get("/api/fund/ranking?varieties=TA")
    assert d["refreshing"] is True and d["stale"] is True
    assert d["cached"] is False, "★ 刷新中不得报 cached=true（原实现恒真）"
    assert d["done"] is True, "有旧结果时 done 应保持 true"
    monkeypatch.setattr(fm, "_frk_snapshot",
                        lambda key, codes: ([{"variety": "TA", "name": "PTA"}], True, "",
                                            {"refreshing": False, "stale": False,
                                             "refreshed": 21, "ts": time.time()}))
    d2 = client_get("/api/fund/ranking?varieties=TA")
    assert d2["cached"] is True and d2["refreshing"] is False
    assert d2["refreshed"] == 21 and d2["ready"] == 1 and d2["total"] == 1, "渐进字段未透出"


@pytest.mark.unit
def test_frk_response_exposes_progress_fields(monkeypatch):
    """响应必须带渐进渲染所需字段（与技术面总览同形，前端判据可复用）。

    【用受控 state】不依赖后台线程是否真跑 —— 本文件已全局掐掉重算（`_no_bg_ranking`），
      所以这里直接把 state 摆好、再走 HTTP 验**装配**。
    """
    key = fm._frk_state_key(["TA"])
    fm._FRK_STATE[key] = {"rows": {"TA": {"variety": "TA", "name": "PTA"}}, "done": True,
                          "refreshing": False, "stale": False, "refreshed": 1,
                          "err": "", "ts": time.time()}
    monkeypatch.setattr(fm, "_frk_start", lambda codes, force=False: key)
    try:
        d = client_get("/api/fund/ranking?varieties=TA")
        for k in ("ok", "done", "refreshing", "stale", "refreshed", "ready",
                  "total", "cached", "ts"):
            assert k in d, "响应缺字段 %s" % k
        assert d["total"] == 1 and d["ready"] == 1, "ready/total 未按快照透出"
        assert d["cached"] is True and d["refreshing"] is False
        assert d["rows"][0]["variety"] == "TA"
    finally:
        fm._FRK_STATE.pop(key, None)


@pytest.mark.unit
def test_frontend_asynch_ranking_keeps_old_table():
    """★ 前端只有在"真的什么都没有"时才写占位行；有旧数据必须保留（防闪白）。"""
    h = _h()
    i = h.index("async loadFundRanking(refresh) {")
    body = h[i:i + 6000]
    assert "const had = !!(this._fRkData && (this._fRkData.rows || []).length);" in body, \
        "没区分「首次」与「已有数据」"
    # ⚠ 判据不能只查「`if (!had) {` 存在」—— 同一方法里 catch 分支**也有一个**同形守卫
    #   （"加载失败"那条），于是「把占位行那处的守卫删掉」照样能过（变异自检实测存活）。
    #   必须钉「**占位行紧跟在这个守卫之后**」这个组合。
    i = body.index("if (!had) {")
    assert "正在计算全池基本面指标" in body[i:i + 240], \
        "占位行没有 had 守卫（有旧数据时会被替换成占位行 ⇒ 闪白）"
    # 基线快照只在"非刷新中"重建
    assert "if (!d.refreshing || !this._fRkBase)" in body, \
        "基线快照在刷新中也被重建（会把「上一轮的数」当成可还原的默认值）"


@pytest.mark.unit
def test_frontend_three_way_scheduling():
    """★ 三支排期必须齐备（与技术面总览同一套）：
    首次 !done → 3s；有旧表 + refreshing → 短轮询；完成且空闲 → 长周期。
    """
    h = _h()
    i = h.index("async loadFundRanking(refresh) {")
    body = h[i:i + 6000]
    assert "if (!d.done) {" in body and "3000)" in body, "缺「首次计算」的 3s 渐进轮询"
    assert "} else if (d.refreshing) {" in body, "缺「有旧表 + 后台刷新中」分支"
    assert "_FRK_REFRESH_POLL_MS" in body, "刷新中没用短轮询（新数据落地前看不到更新）"
    assert "this._frkNextAt = Date.now() + this._FRK_REFRESH_MS;" in body, "缺「完成且空闲」的长周期排期"
    # 刷新中不得设"下次时刻"（那一刻还没确定，是过期信息）
    j = body.index("} else if (d.refreshing) {")
    seg = body[j:body.index("} else {", j)]
    assert "this._frkNextAt = 0;" in seg, "刷新中仍设了「下次刷新时刻」（过期信息）"
    assert "this._frkNextAt = Date.now()" not in seg, "刷新中不该算下次时刻"
    # 失败也要排重试，否则后台算好了前端再也不来看
    assert "加载失败" in body and "this._frkPollTimer = setTimeout(" in body, "失败后没有排重试"


@pytest.mark.unit
def test_frontend_stops_timer_on_leave():
    """★ 离开本页必须停表（三处：切 tab / 进明细 / 停表方法本身）。

    【为什么要三处都钉】技术面那轮的事故就是"只在进详情时停了，切 tab 没停" ⇒
    人不在页面也周期性触发，而那一刻 TTL 恰好到期 ⇒ **没人在看也白算一轮**。
    """
    h = _h()
    assert "_stopFundRanking() {" in h, "缺停表方法"
    i = h.index("_stopFundRanking() {")
    b = h[i:i + 400]
    assert "clearTimeout(this._frkPollTimer)" in b and "this._frkNextAt = 0;" in b, \
        "停表没有同时清定时器与「下次时刻」"
    # ① 切 tab
    assert "if (t !== 'fund') this._stopFundRanking();" in h, "切走 tab 没停表"
    # ② 进明细
    i2 = h.index("fOpenDetail(code) {")
    assert "this._stopFundRanking();" in h[i2:i2 + 400], "进单品种明细没停表"
    # ③ 返回总览要重启
    i3 = h.index("fBackOverview() {")
    assert "this.loadFundRanking();" in h[i3:i3 + 600], "返回总览没有重启拉取（定时器不会重排）"


@pytest.mark.unit
def test_frontend_progress_bar_three_states():
    """进度条三态文案（与技术面同形），且刷新中明说"表格显示上一轮、无需等待"。"""
    h = _h()
    assert 'id="f-rk-progress"' in h, "控制区没有进度条容器"
    i = h.index("const pg = document.getElementById('f-rk-progress');")
    seg = h[i:i + 1600]
    assert "'计算中 ' + (d.ready || 0) + '/' + (d.total || 0)" in seg, "缺「计算中 n/N」文案"
    assert "后台刷新中 " in seg and "表格显示上一轮结果" in seg, \
        "缺「后台刷新中 + 表格显示上一轮结果」文案"
    assert "每 15 分钟自动刷新（下次 " in seg, "缺「下次刷新时刻」文案"
    assert "d.done && !d.refreshing && this._frkNextAt" in seg, \
        "刷新中仍会显示「下次刷新时刻」（过期信息）"


@pytest.mark.unit
def test_frontend_dims_are_pills_not_select():
    """★ 指标筛选必须是**气泡**（用户 2026-10-16 要求：与品种筛选同形），不再是 `<select>`。"""
    h = _h()
    i = h.index("_fRkBuildDims() {")
    body = h[i:i + 2600]
    assert "f-rk-pill" in body, "维度筛选没做成气泡"
    # ⚠ 判据要钉**元素类型**（button）—— 只查 `f-rk-pill` 这个类名的话，
    #   把气泡改成 `<option class="f-rk-pill">` 照样能过（变异自检实测存活）。
    assert '<button class="btn btn-sm btn-outline f-rk-pill"' in body, \
        "气泡不是 <button>（用户要求「气泡形式」，与技术面品种筛选同形）"
    assert 'data-dim="' in body and 'data-val="' in body, "气泡缺 data-dim/data-val（无法定位与测试）"
    assert "<select class=\"f-rk-dim\"" not in h, "旧的维度下拉残留（两套控件并存）"
    assert h.count("f-rk-dim\"") == 0, "残留旧的 f-rk-dim 类名"
    # 每次渲染只更新选中态，不能在轮询里重建 DOM（否则点击会闪掉）
    assert "box.dataset.built" in body and "box.dataset.sig" in body, \
        "气泡没有「签名守卫」⇒ 周期性重渲染会把正点着的按钮重建掉"


@pytest.mark.unit
def test_frontend_pill_guard_skips_rebuild_while_computing():
    """★ 渐进计算期间**不重建**气泡（那 40s 里数据不全，建了也残缺，还会闪）。"""
    h = _h()
    i = h.index("_fRkBuildDims() {")
    body = h[i:i + 2600]
    assert "const settled = !!(dd.done && !dd.refreshing);" in body, "没有「数据已稳定」判据"
    assert "if (!built || (settled && (box.dataset.sig !== sig || box.dataset.settled !== '1'))) {" in body, \
        "重建条件不对（要么每次都建=闪，要么只建一次=取值域/计数残缺）"
    # 计数在未稳定时不显示（宁可留空也不给不准的数）
    assert "settled ? rows.length : null" in body, "未稳定时仍显示了计数"


@pytest.mark.unit
def test_frontend_pill_settled_state_recorded():
    """★ 气泡重建条件必须**同时**依赖「取值签名」与「上次是否已稳定」。

    计数是**建 DOM 时**烘焙的：若首次构建发生在"数据未稳定"（计算中/刷新中）时计数为空，
      此后取值签名不变 ⇒ 只靠签名判断就**永不重建 ⇒ 计数永久为空**
      （实测踩到：程序化核对时一组数字都没有 —— 只在"进页面时后端正好在 refresh"这条路径复现）。
    反之，若只加条件、忘了**记录**状态（`dataset.settled` 永远 undefined），
      条件恒真 ⇒ 稳定后每次渲染都重建 ⇒ **把"防闪"守卫废掉**（比不改更糟）。
    两者是一对，缺任一都不行 —— 故一条断言同时钉住。
    """
    h = _h()
    i = h.index("_fRkBuildDims() {")
    body = h[i:i + 2600]
    assert "box.dataset.settled !== '1'" in body, "重建条件没带「上次是否已稳定」"
    assert "box.dataset.settled = settled ? '1' : '0';" in body, \
        "记了判定却没记录状态 ⇒ 条件恒真 ⇒ 每次渲染都重建（防闪守卫失效）"
    assert "const settled = !!(dd.done && !dd.refreshing);" in body, "缺 settled 判定"
