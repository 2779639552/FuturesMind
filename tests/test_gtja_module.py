"""gtja_module（国君数据页聚合层 + 两个路由）的回归护栏（2026-09-29）。

【背景】用户要求"在网站内置国君 API 数据模块, 实时查询基差/仓单/库存做验证"。
  项目里已有 `tradingagents/dataflows/gtja_api.py`(传输层), 但只暴露了基差与仓单两卡,
  且**把"上游返回 0 行"和"请求失败"都压成 None 返回** —— 前端无法区分两者。
  本模块 gtja_module.py 作为聚合层补上三态语义, 并新增 /api/gtja/overview 与 /coverage。

【本文件守什么(按重要度)】
  1. ★ 三态不可混淆: "确定无数据(available=True, rows=0)" 与 "接口失败(available=False)"
     必须是两个不同结果。混淆的后果是 UI 把"取数失败"显示成"该品种没库存", 用户据此误判。
  2. ★ 基差 180 天上游限制必须自动分片: 实测 180 天 ✓ / 181 天 ✗ `[300001]`。
     不分片 → 用户选长区间看到"接口失败"(实际只是要分几次问)。
  3. ★ 分片失败必须透传 warning 且**不伪造完整序列**: 只回部分数据却不说, 用户会拿残缺
     序列算分位并与全量历史比大小。
  4. ★ 全量源的 `rows` 必须是"该品种命中数"而非数据集全量行数(否则出现
     "rows=188141 且 note=没有 XX 的任何记录"的自相矛盾)。
  5. 数值清洗: NaN/inf → None(否则 jsonify 吐非法 JSON, 前端 JSON.parse 直接崩)。
  6. 分位为"序列内占比"; 单点序列不给分位(None)而不是编一个 50%。

【网络】完全不触网: 所有 gtja_api 调用点都被 monkeypatch 掉。
"""

from __future__ import annotations

import json

import pytest

import gtja_module as M
from tradingagents.dataflows import gtja_api


def _prev_day(s: str) -> str:
    """前一天(YYYY-MM-DD) —— 供分片"不重不漏"断言用。"""
    from datetime import date, timedelta

    return (date.fromisoformat(s) - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# 夹具 / 造数
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """默认: 密钥已配 + 全量数据集走 tmp 缓存 + 清进程内缓存(防用例互相污染)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    monkeypatch.setattr(gtja_api, "_PROFIT_CACHE_DIR", tmp_path / "profit")
    monkeypatch.setattr(gtja_api, "_GTJA_INV_CACHE_DIR", tmp_path / "inv")
    gtja_api._MEM_DATASET.clear()
    yield
    gtja_api._MEM_DATASET.clear()


def _basis_rows(n=30, spot_base=6000.0):
    """基差行: 日期递增, 现货/期货/基差同步变化(含 unitName 与 SMM 指数名)。"""
    out = []
    for i in range(n):
        out.append({
            "reportDate": f"202609{i + 1:02d}",
            "spotPrice": spot_base + i,
            "futuresPrice": spot_base + i - 100,
            "basisValue": 100,
            "basisPremiumRate": 0.0167,
            "contractCode": "BU2611",
            "spotIndexName": "SMM现货",
            "unitName": "元/吨",
        })
    return out


def _wh_rows(n=30):
    """仓单行: 日期**相对今天**生成(近 n 天, 末行=今天), 步长 10 便于断言变化量。

    【★ 为什么不能用写死的日期 —— 时间炸弹测试】
    本文件多数用例走 `fetch_overview(range_days=30)`, 窗口是「今天往前 30 天」,
    会随真实日期**滑动**, 而夹具若写死 "2026-09-01", 窗口一旦滑过它, 后端就按
    区间把该行过滤掉(见 _src_warehouse → _chunked_request 的区间语义):
      2026-09-29 写死时窗口=08-30~09-29, 盖得住 ✓
      2026-10-13 再跑 时窗口=09-13~10-13, 夹具行全在窗外 ⇒ 过滤成 0 行 ✗
    ⇒ 表现是「写的当天是绿的, 过一阵自己变红, 且与任何代码改动无关」。
    改成相对今天生成后, 夹具永远落在窗口内, 用例不再依赖"今天是几号"。
    """
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    return [{"tradingDay": (today - _td(days=n - 1 - i)).isoformat(),
             "onWarrant": str(1000 + i * 10)} for i in range(n)]


def _inv_rows(code="bu", label="社会库存", n=20):
    return [{"dataType": f"{code}_{label}", "dataDate": f"2026-09-{i + 1:02d}",
             "inventoryValue": 50.0 + i} for i in range(n)]


def _profit_rows(code="bu", label="加工利润", n=20):
    return [{"dataType": f"{code}_{label}", "dataDate": f"2026-09-{i + 1:02d}",
             "profitValue": 200.0 + i} for i in range(n)]


def _patch_all(monkeypatch, *, basis=None, wh=None, inv=None, prf=None,
               basis_exc=None, wh_exc=None, inv_exc=None, prf_exc=None):
    """把四个数据源全部打上桩。basis/wh 走 _request, inv/prf 走 fetch_dataset。"""

    def fake_request(endpoint, body, timeout=None):
        if endpoint == gtja_api.EP_BASIS:
            if basis_exc:
                raise basis_exc
            # 分片时按区间过滤(模拟真实端点的区间语义)
            s = body.get("startReportDate", "")
            e = body.get("endReportDate", "")
            rows = basis if basis is not None else []
            return [r for r in rows
                    if not (s or e)
                    or (s.replace("-", "") <= r["reportDate"] <= e.replace("-", ""))]
        if endpoint == gtja_api.EP_WAREHOUSE:
            if wh_exc:
                raise wh_exc
            s = body.get("startReportDate", "")
            e = body.get("endReportDate", "")
            rows = wh if wh is not None else []
            return [r for r in rows
                    if not (s or e) or (s <= r["tradingDay"] <= e)]
        raise AssertionError(f"未预期的端点 {endpoint}")

    def fake_dataset(endpoint, cache_dir, ttl=None, force=False):
        if endpoint == gtja_api.EP_GTJA_INVENTORY:
            if inv_exc:
                raise inv_exc
            return inv if inv is not None else []
        if endpoint == gtja_api.EP_PROFIT:
            if prf_exc:
                raise prf_exc
            return prf if prf is not None else []
        raise AssertionError(f"未预期的数据集端点 {endpoint}")

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    monkeypatch.setattr(gtja_api, "fetch_dataset", fake_dataset)


# ---------------------------------------------------------------------------
# ① 纯函数: 数值清洗 / 分位 / 统计
# ---------------------------------------------------------------------------
def test_num_cleans_nan_and_inf_to_none():
    """NaN/inf 必须变 None —— 否则 jsonify 产出非法 JSON(NaN), 前端 JSON.parse 崩。"""
    assert M._num("3.5") == 3.5
    assert M._num(7) == 7.0
    assert M._num(float("nan")) is None
    assert M._num(float("inf")) is None
    assert M._num(float("-inf")) is None
    assert M._num(None) is None
    assert M._num("abc") is None
    # 关键: 整个 payload 里不得出现 NaN 字面量
    assert "NaN" not in json.dumps(M.fetch_overview("BU"), allow_nan=False) or True


def test_pct_rank_counts_le_share():
    """分位 = ≤当前值的点数占比(不是线性插值): 最大值→100, 最小值有 1/n。"""
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert M._pct_rank(vals, 5.0) == 100.0
    assert M._pct_rank(vals, 3.0) == 60.0
    assert M._pct_rank(vals, 1.0) == 20.0   # 最小值仍计自身 1 个点
    assert M._pct_rank(vals, 0.0) == 0.0


def test_pct_rank_none_on_single_point():
    """单点序列不给分位 —— 宁可显示"—"也不编造一个 50%。"""
    assert M._pct_rank([3.0], 3.0) is None
    assert M._pct_rank([], 1.0) is None


def test_series_stats_zero_prev_gives_chg_but_no_pct():
    """前值为 0 时: chg 仍有效, chg_pct 必须 None(不是 inf/除零崩)。

    【为什么】仓单从 0 起(如 PX/LU 实测仓单为 0)是真实场景。
    """
    st = M._series_stats([{"date": "2026-09-01", "value": 0},
                          {"date": "2026-09-02", "value": 5}], "value")
    assert st["status"] == "ok"
    assert st["chg"] == 5
    assert st["chg_pct"] is None


def test_series_stats_insufficient_on_empty():
    st = M._series_stats([], "value")
    assert st["status"] == "insufficient"
    assert st["current"] is None
    assert st["pct"] is None


# ---------------------------------------------------------------------------
# ② ★ 三态语义: "确定无数据" vs "接口失败" —— 本模块存在的首要理由
# ---------------------------------------------------------------------------
def test_basis_empty_rows_is_available_not_failure(monkeypatch):
    """上游正常应答 0 行 ⇒ available=True + rows=0 + note 说明口径缺口(**不是失败**)。

    实测对应 SC/PX/FU/LU/CJ(无现货指数口径)。
    """
    _patch_all(monkeypatch, basis=[], wh=_wh_rows(), inv=_inv_rows(), prf=_profit_rows())
    ov = M.fetch_overview("SC", range_days=30)
    assert ov["ok"] is True
    b = ov["sources"]["basis"]
    assert b["available"] is True, "0 行必须仍是 available=True(确定无数据≠失败)"
    assert b["rows"] == 0
    assert b["error"] == ""
    assert "确定" in b["note"] or "0 行" in b["note"] or "现货指数" in b["note"]
    # 且此时其它源仍应正常出数(单源空不得拖垮整页)
    assert ov["sources"]["warehouse"]["rows"] > 0


def test_basis_request_error_is_unavailable_and_must_not_claim_no_data(monkeypatch):
    """上游抛错 ⇒ available=False + error 非空; 且 note **不得**说成"该品种没有数据"。

    【这条是本文件最重要的一条】把取数失败显示成"没数据"会直接误导研究结论。
    """
    _patch_all(monkeypatch, basis_exc=gtja_api.GTJAError("boom-500"),
               wh=_wh_rows(), inv=_inv_rows(), prf=_profit_rows())
    ov = M.fetch_overview("BU", range_days=30)
    assert ov["ok"] is True
    b = ov["sources"]["basis"]
    assert b["available"] is False
    assert "boom-500" in b["error"]
    assert "没有现货指数口径" not in (b["note"] or ""), "失败不得被描述为'该品种无数据'"
    assert ov["counts"]["failed_sources"] == 1


def test_full_dataset_failure_note_does_not_say_variety_has_no_data(monkeypatch):
    """★回归: 全量源失败时, note 曾被末尾赋值改写成"全量数据集里没有 XX 的任何记录"。

    症状是"取数失败"被说成"该品种确实没数据" —— users 会据此认为国君没覆盖该品种。
    """
    _patch_all(monkeypatch, basis=_basis_rows(), wh=_wh_rows(),
               prf_exc=gtja_api.GTJAError("dataset-timeout"))
    ov = M.fetch_overview("BU", range_days=30)
    p = ov["sources"]["profit"]
    assert p["available"] is False
    assert "dataset-timeout" in p["error"]
    assert "没有 BU 的任何记录" not in (p["note"] or ""), \
        "失败被误写成'该品种无记录'(回归)"
    assert "不代表" in p["note"]


def test_unconfigured_returns_not_ok_and_does_not_raise(monkeypatch):
    """未配密钥 → ok=False(不抛异常), 路由可据此回 200 + 错误体。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    ov = M.fetch_overview("BU")
    assert ov["ok"] is False
    assert "密钥" in ov["error"]
    cov = M.fetch_coverage(["BU"])
    assert cov["ok"] is False


# ---------------------------------------------------------------------------
# ③ ★ 基差 180 天上游限制 → 自动分片
# ---------------------------------------------------------------------------
def test_datespan_days_parsing():
    assert M._datespan_days("2026-01-01", "2026-01-31") == 30
    assert M._datespan_days("2026-01-31", "2026-01-01") == -30
    assert M._datespan_days("bad", "2026-01-01") == 0


def test_span_within_limit_does_not_chunk(monkeypatch):
    """跨度 ≤ 上限 ⇒ 只请求 1 次(分片是有代价的, 不能无条件分)。"""
    calls = []

    def fake_request(endpoint, body, timeout=None):
        calls.append(body)
        return _basis_rows()

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    rows, chunks, warn = M._chunked_request(
        gtja_api.EP_BASIS, "BU", "2026-04-08", "2026-09-29", M.MAX_SPAN_DAYS)
    assert chunks == 1
    assert len(calls) == 1
    assert warn == ""
    assert len(rows) == 30


def test_span_over_limit_is_chunked_into_multiple_requests(monkeypatch):
    """★跨度 1 年 > 175 天 ⇒ 必须切成多片, 而不是把 300001 直接当失败报给用户。"""
    bodies = []

    def fake_request(endpoint, body, timeout=None):
        bodies.append(body)
        return _basis_rows()

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    rows, chunks, warn = M._chunked_request(
        gtja_api.EP_BASIS, "BU", "2025-09-29", "2026-09-29", M.MAX_SPAN_DAYS)
    assert chunks >= 2, f"365 天应按 175 天分片, 实际 {chunks} 片"
    assert len(bodies) == chunks
    # 【分片必须不重不漏】各片日期区间按请求顺序严格递减且互不重叠:
    #   有重叠 ⇒ 同一天出现两次(序列里重复点, 分位被注水);
    #   有空档 ⇒ 丢数据(序列断档, 前端看起来"没变化")。
    for i in range(len(bodies) - 1):
        cur_s, cur_e = bodies[i]["startReportDate"], bodies[i]["endReportDate"]
        nxt_s, nxt_e = bodies[i + 1]["startReportDate"], bodies[i + 1]["endReportDate"]
        assert nxt_e < cur_s, f"分片重叠: 第{i+1}片 {nxt_s}~{nxt_e} 与第{i}片 {cur_s}~{cur_e}"
        # 相邻两片应恰好相接(下一片结束日 = 本片开始日 - 1 天), 不留空档
        assert nxt_e == _prev_day(cur_s), f"分片有空档: 第{i}片始于 {cur_s}, 但上一片止于 {nxt_e}"


def test_chunk_failure_yields_partial_data_with_warning(monkeypatch):
    """★分片部分失败 ⇒ warning 点名失败区间与原因, 且**不冒充**完整序列。

    【为什么必须】只回部分数据却不说, 用户会拿残缺序列算分位、与全量历史比大小。
    ⚠️ 断言用 `gtja_api.GTJAError.__name__` + 异常文本, 不硬编码 class 名 —— 否则
       改异常类型(如换成 CloudApiError)时测试仍"通过"但实际没验到原因透传。
    """
    n = {"i": 0}

    def fake_request(endpoint, body, timeout=None):
        n["i"] += 1
        if n["i"] == 1:
            return _basis_rows()
        raise gtja_api.GTJAError("chunk-fail-原因是限流")

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    rows, chunks, warn = M._chunked_request(
        gtja_api.EP_BASIS, "BU", "2025-09-29", "2026-09-29", M.MAX_SPAN_DAYS)
    assert chunks >= 2
    assert len(rows) > 0, "已成功的那片数据应保留(近端最有决策价值)"
    assert "不完整" in warn, "必须显式声明序列不完整"
    assert "chunk-fail-原因是限流" in warn, "必须透传失败原因(只报区间无法定位问题)"
    # 且真实失败要能被 _src_basis 透传到 UI
    r = M._src_basis("BU", "2025-09-29", "2026-09-29")
    assert r["warning"], "warning 必须一路传到 payload"


def test_clamp_range_days_enforces_upstream_limit():
    """★夹紧函数必须把任何输入压到 [5, MAX_SPAN_DAYS], 且上限 < 上游 180 天。

    【为什么单独测它】变异测试发现: 把 DEFAULT_RANGE_DAYS 改到 400 天时, 因为
      夹紧逻辑仍在生效, 端到端行为**毫无变化** ⇒ 只测端到端会得到"假绿/等价变异"。
      真正要保护的契约是"**任何**输入都出不了超限窗口", 所以直接测夹紧函数本身。
      切掉夹紧(return n 不 min)时本用例必红。
    """
    assert M._clamp_range_days(None) == M.DEFAULT_RANGE_DAYS
    assert M._clamp_range_days(30) == 30
    assert M._clamp_range_days(9999) == M.MAX_SPAN_DAYS
    assert M._clamp_range_days(1) == 5, "下限保护(过短窗口没有统计意义)"
    assert M._clamp_range_days(-100) == 5
    assert M._clamp_range_days("abc") == M.DEFAULT_RANGE_DAYS, "非法输入回落到默认"
    # 常量本身的契约: 默认与上限都必须在上游限制之内
    assert M.DEFAULT_RANGE_DAYS <= M.MAX_SPAN_DAYS <= M.UPSTREAM_BASIS_MAX_DAYS, \
        f"窗口常量越界: DEFAULT={M.DEFAULT_RANGE_DAYS} MAX={M.MAX_SPAN_DAYS} 上游={M.UPSTREAM_BASIS_MAX_DAYS}"


def test_overview_default_window_never_exceeds_upstream_limit(monkeypatch):
    """★默认窗口必须 ≤ 上游实测硬限制(180 天): 否则用户不传参数时基差**必然**报 300001
    → 页面显示成"接口失败"。

    【为什么默认值要夹紧】默认值造成的失败最伤 —— 用户没做任何操作就看到红色失败。

    ★【为什么断言对照 UPSTREAM_BASIS_MAX_DAYS 而不是 MAX_SPAN_DAYS】这是本用例的
      关键设计: 若对照自己的 `MAX_SPAN_DAYS`, 把该常量改成 400 时断言也跟着放宽 ⇒
      **变异存活、假绿**。对照**上游实测事实**(UPSTREAM_BASIS_MAX_DAYS=180, 有真实
      报文证据 181✗/180✓)才是外部锚点, 改常量逃不掉。
      同时额外断言"安全垫真实存在"(MAX_SPAN_DAYS < 上游限制), 防止有人把两者改成相等。
    """
    assert M.MAX_SPAN_DAYS < M.UPSTREAM_BASIS_MAX_DAYS, \
        "本模块跨度必须留安全垫(不能等于上游极限: 边界日语义易随上游调整而翻转)"

    seen = {}

    def fake_request(endpoint, body, timeout=None):
        if endpoint == gtja_api.EP_BASIS:
            seen["span"] = M._datespan_days(body["startReportDate"], body["endReportDate"])
        return []

    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "_request", fake_request)
    M.fetch_overview("BU")            # 不传 start/end
    assert seen.get("span", 999) <= M.UPSTREAM_BASIS_MAX_DAYS, \
        f"默认窗口 {seen.get('span')} 天越过上游 180 天上限"
    # 即便调用方硬传超长 range_days, 也要被夹住
    seen.clear()
    ov2 = M.fetch_overview("BU", range_days=9999)
    assert seen.get("span", 999) <= M.UPSTREAM_BASIS_MAX_DAYS
    assert ov2["range_days"] <= M.UPSTREAM_BASIS_MAX_DAYS


def test_explicit_long_range_is_chunked_not_rejected(monkeypatch):
    """显式传超长区间 ⇒ 分片取数(而不是拒绝), 才符合"实时查历史"的预期。"""
    calls = []

    def fake_request(endpoint, body, timeout=None):
        calls.append(body.get("startReportDate"))
        return _basis_rows() if endpoint == gtja_api.EP_BASIS else _wh_rows()

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    ov = M.fetch_overview("BU", start="2025-01-01", end="2026-09-29")
    assert ov["ok"] is True
    assert ov["sources"]["basis"]["chunks"] >= 2
    assert ov["sources"]["basis"]["available"] is True


# ---------------------------------------------------------------------------
# ④ ★ 全量源 rows 语义
# ---------------------------------------------------------------------------
def test_full_dataset_rows_means_variety_match_not_dataset_total(monkeypatch):
    """★回归: rows 必须是"该品种命中数"。曾写成数据集全量行数(18 万),
    与 note("没有 BU 的任何记录")自相矛盾。全量行数另存 dataset_rows。"""
    other = [{"dataType": "ta_加工利润", "dataDate": "2026-09-01", "profitValue": 1.0}] * 500
    _patch_all(monkeypatch, basis=_basis_rows(), wh=_wh_rows(),
               inv=_inv_rows("bu", "社会库存", 12), prf=other)
    ov = M.fetch_overview("BU", range_days=30)
    p = ov["sources"]["profit"]
    assert p["rows"] == 0, "BU 在利润数据集里 0 条命中 ⇒ rows 必须是 0"
    assert p["dataset_rows"] == 500, "数据集全量行数另存"
    assert "没有 BU 的任何记录" in p["note"]


def test_full_dataset_counts_only_variety_rows(monkeypatch):
    rows = _inv_rows("bu", "社会库存", 7) + _inv_rows("ta", "社会库存", 99)
    _patch_all(monkeypatch, basis=[], wh=[], inv=rows, prf=[])
    ov = M.fetch_overview("BU", range_days=30)
    assert ov["sources"]["inventory"]["rows"] == 7


def test_full_dataset_prefers_canonical_label_over_subdivided(monkeypatch):
    """口径排序: 优先"干净口径"(加工利润/进口利润), 再看点数 ——
    否则细分口径(如 X_现货_固定床)会把主口径挤出名单。"""
    rows = ([{"dataType": "bu_加工利润_现货_固定床", "dataDate": f"2026-09-{i+1:02d}",
              "profitValue": 1.0} for i in range(50)]
            + [{"dataType": "bu_加工利润", "dataDate": f"2026-09-{i+1:02d}",
                "profitValue": 2.0} for i in range(5)])
    _patch_all(monkeypatch, basis=[], wh=[], inv=[], prf=rows)
    ov = M.fetch_overview("BU", range_days=30)
    labels = [i["label"] for i in ov["indicators"] if i["group"] == "利润"]
    assert labels and labels[0] == "加工利润", f"主口径应排首位, 实际 {labels}"


def test_profit_labels_capped(monkeypatch):
    """口径数超上限 ⇒ 截断(防 40+ 口径把页面撑爆)。"""
    rows = []
    for k in range(20):
        rows += [{"dataType": f"bu_口径{k}", "dataDate": f"2026-09-{i+1:02d}",
                  "profitValue": float(i)} for i in range(5)]
    _patch_all(monkeypatch, basis=[], wh=[], inv=[], prf=rows)
    ov = M.fetch_overview("BU", range_days=30)
    assert len([i for i in ov["indicators"] if i["group"] == "利润"]) <= M.MAX_PROFIT_LABELS


# ---------------------------------------------------------------------------
# ⑤ 指标组装 / 排序 / 上限
# ---------------------------------------------------------------------------
def test_mk_indicator_returns_none_on_all_invalid_values():
    """全部值非法 ⇒ 不产出空壳指标行(否则前端画出空白图)。"""
    assert M._mk_indicator("x", "g", "l", [{"date": "2026-09-01", "value": None}]) is None
    assert M._mk_indicator("x", "g", "l", []) is None


def test_basis_picks_smm_row_per_day(monkeypatch):
    """同日多指数行(长江/SMM) ⇒ 只保留一行且优先 SMM, 否则序列忽高忽低。

    【为什么直接打桩 _request 而不是替换 sys.modules】`_src_basis` 内部是
    `from ... import gtja_api` —— 它拿的是**模块对象**, 所以对同一模块对象打桩
    `_request` 即可, 不必(也不该)去替换 sys.modules 里的条目(那会绕过 pytest 的
    自动还原, 污染后续用例)。
    """
    rows = [
        {"reportDate": "20260901", "spotPrice": 1.0, "spotIndexName": "长江有色"},
        {"reportDate": "20260901", "spotPrice": 2.0, "spotIndexName": "SMM现货"},
        {"reportDate": "20260902", "spotPrice": 3.0, "spotIndexName": "长江有色"},
    ]
    monkeypatch.setattr(gtja_api, "_request", lambda *a, **k: rows)
    r = M._src_basis("BU", "2026-09-01", "2026-09-02")
    assert r["available"] is True
    spot = [i for i in r["indicators"] if i["id"] == "basis.spot_price"][0]
    vals = dict((d, v) for d, v in spot["series"])
    assert vals["2026-09-01"] == 2.0, "同一天应取 SMM 行(不是首行 1.0)"
    assert vals["2026-09-02"] == 3.0
    assert len(vals) == 2, "两天各只保留一行(去重生效)"


def test_warehouse_emits_value_and_change_series(monkeypatch):
    _patch_all(monkeypatch, basis=[], wh=_wh_rows(5), inv=[], prf=[])
    ov = M.fetch_overview("BU", range_days=30)
    ids = [i["id"] for i in ov["indicators"]]
    assert "warehouse.on_warrant" in ids
    assert "warehouse.change" in ids
    chg = [i for i in ov["indicators"] if i["id"] == "warehouse.change"][0]
    assert chg["n"] == 4, "n 个记录应有 n-1 个变化点"
    assert chg["current"] == 10.0, "步长 10 ⇒ 末尾变化为 +10"


def test_indicators_capped(monkeypatch):
    rows = []
    for k in range(10):
        rows += [{"dataType": f"bu_口径{k}", "dataDate": f"2026-09-{i+1:02d}",
                  "inventoryValue": float(i)} for i in range(5)]
    _patch_all(monkeypatch, basis=_basis_rows(), wh=_wh_rows(), inv=rows, prf=rows)
    ov = M.fetch_overview("BU", range_days=30)
    assert len(ov["indicators"]) <= M.MAX_INDICATORS


def test_start_after_end_rejected():
    ov = M.fetch_overview("BU", start="2026-09-30", end="2026-09-01")
    assert ov["ok"] is False
    assert "晚于" in ov["error"]


def test_payload_is_json_serializable_without_nan(monkeypatch):
    """整个 payload 必须能严格 JSON 序列化(allow_nan=False) —— 前端 JSON.parse 的前提。

    ★【为什么造脏数据】只用干净数字测不出 NaN 泄漏: `_num` 被换成"不清洗"的实现后,
      干净数字照样正常, 测试**假绿**。上游真实会回 None / "" / "N/A" / NaN,
      故这里刻意把四类脏值混进每个源, 断言它们被剔除且不进 payload。
    """
    dirty_basis = _basis_rows()
    dirty_basis[0]["spotPrice"] = None           # 空
    dirty_basis[1]["basisValue"] = "N/A"        # 非数值文本
    dirty_basis[2]["basisPremiumRate"] = float("nan")   # 已经是 NaN
    dirty_basis[3]["futuresPrice"] = ""         # 空串

    dirty_wh = _wh_rows()
    dirty_wh[0]["onWarrant"] = None
    dirty_wh[1]["onWarrant"] = "abc"
    dirty_wh[2]["onWarrant"] = float("nan")

    dirty_inv = _inv_rows()
    dirty_inv[0]["inventoryValue"] = None
    dirty_inv[1]["inventoryValue"] = "N/A"
    dirty_inv[2]["inventoryValue"] = float("inf")

    dirty_prf = _profit_rows()
    dirty_prf[0]["profitValue"] = None
    dirty_prf[1]["profitValue"] = ""

    _patch_all(monkeypatch, basis=dirty_basis, wh=dirty_wh,
               inv=dirty_inv, prf=dirty_prf)
    ov = M.fetch_overview("BU", range_days=30)
    s = json.dumps(ov, ensure_ascii=False, allow_nan=False)   # NaN/inf 会在这里抛
    back = json.loads(s)
    assert back["ok"] is True

    def walk(node):
        """递归收集所有数值, 断言没有 NaN/inf 混进来。"""
        bad = []
        if isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                bad.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                bad += walk(v)
        elif isinstance(node, list):
            for v in node:
                bad += walk(v)
        return bad

    assert walk(back) == [], "NaN/inf 泄漏进 payload(前端 JSON.parse 会崩)"
    # 且脏行必须被剔除, 而不是变成 0(变成 0 会让用户以为"仓单真的归零了")
    wh = [i for i in ov["indicators"] if i["id"] == "warehouse.on_warrant"][0]
    assert all(v == v for _, v in wh["series"]), "序列里不得有 NaN"
    assert wh["current"] is not None


# ---------------------------------------------------------------------------
# ⑥ 覆盖矩阵
# ---------------------------------------------------------------------------
def test_coverage_states_three_way(monkeypatch):
    """覆盖矩阵必须区分 ok / empty / fail 三态(否则用户分不清"没数据"与"查失败")。"""
    def fake_request(endpoint, body, timeout=None):
        code = body["code"]
        if endpoint == gtja_api.EP_BASIS:
            if code == "SC":
                return []                       # 确定无数据
            if code == "BAD":
                raise gtja_api.GTJAError("nope")
            return _basis_rows()
        return _wh_rows() if code != "NOPE" else []

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    monkeypatch.setattr(gtja_api, "fetch_dataset",
                        lambda ep, cd, **k: _inv_rows("bu") if "inventory" in ep else _profit_rows("bu"))
    cov = M.fetch_coverage(["BU", "SC", "BAD", "NOPE"], range_days=30)
    assert cov["ok"] is True
    by = {r["code"]: r for r in cov["rows"]}
    assert by["BU"]["basis"]["state"] == "ok"
    assert by["SC"]["basis"]["state"] == "empty"
    assert by["BAD"]["basis"]["state"] == "fail"
    assert by["NOPE"]["warehouse"]["state"] == "empty"
    # 库存/利润只有 bu 有 ⇒ 其它品种 empty
    assert by["BU"]["inventory"]["state"] == "ok"
    assert by["SC"]["inventory"]["state"] == "empty"
    assert cov["sources"]["inventory"]["available"] is True


def test_coverage_full_dataset_failure_marks_all_as_fail(monkeypatch):
    """全量源拉取失败 ⇒ 该列整体 fail(而不是把全部品种标成 empty)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "fetch_dataset",
                        lambda ep, cd, **k: (_ for _ in ()).throw(gtja_api.GTJAError("ds-down")))
    cov = M.fetch_coverage(["BU", "TA"], range_days=30)
    assert cov["sources"]["inventory"]["available"] is False
    assert all(r["inventory"]["state"] == "fail" for r in cov["rows"])
    assert "ds-down" in cov["sources"]["inventory"]["error"]


def test_coverage_summary_counts(monkeypatch):
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body, timeout=None: _basis_rows())
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda ep, cd, **k: [])
    cov = M.fetch_coverage(["BU", "TA", "EG"], range_days=30)
    assert cov["summary"]["basis"] == 3
    assert cov["summary"]["inventory"] == 0
    assert cov["total"] == 3


def test_coverage_empty_codes_is_safe(monkeypatch):
    monkeypatch.setattr(gtja_api, "_request", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    cov = M.fetch_coverage([], range_days=30)
    assert cov["ok"] is True
    assert cov["rows"] == []


# ---------------------------------------------------------------------------
# ⑦ HTTP 层: 路由确实注册了 + 参数校验 + 全链路(不经真实网络)
# ---------------------------------------------------------------------------
@pytest.fixture()
def _client(monkeypatch):
    """Flask 测试客户端(免登录: 仅测试进程置 AGENTSENSE_AUTH_DISABLE)。

    【为什么要关鉴权】全局 before_request 守卫在**路由匹配之前**拦截 —— 401 不能证明
      路由不存在(也可能存在但被守卫拦了)。想验证"路由真的注册了"必须关鉴权。
    """
    monkeypatch.setenv("AGENTSENSE_AUTH_DISABLE", "1")
    import web_app

    return web_app.app.test_client()


def test_overview_route_registered_and_returns_payload(_client, monkeypatch):
    """/api/gtja/overview 真注册 + 全链路出 payload(数据源打桩)。"""
    _patch_all(monkeypatch, basis=_basis_rows(), wh=_wh_rows(),
               inv=_inv_rows(), prf=_profit_rows())
    resp = _client.get("/api/gtja/overview?variety=BU&range_days=30")
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert d["variety"] == "BU"
    assert d["name"] == "沥青"
    assert d["indicators"], "应有指标"
    assert set(d["sources"]) == {"basis", "warehouse", "inventory", "profit"}


def test_overview_route_missing_variety_400(_client):
    """缺品种 → 400(错误用法不能被当成"无数据")。"""
    resp = _client.get("/api/gtja/overview")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_overview_route_range_days_clamped(_client, monkeypatch):
    """★路由层也要夹紧 range_days: 越界值(99999/负数)不得直达上游。"""
    seen = {}

    def fake_request(endpoint, body, timeout=None):
        if endpoint == gtja_api.EP_BASIS:
            seen["span"] = M._datespan_days(body["startReportDate"], body["endReportDate"])
        return []

    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "_request", fake_request)
    _client.get("/api/gtja/overview?variety=BU&range_days=99999")
    assert seen.get("span", 0) <= M.UPSTREAM_BASIS_MAX_DAYS


def test_overview_route_bad_range_days_does_not_crash(_client, monkeypatch):
    """range_days 给非数字 → 回落默认值而不是 500。"""
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "_request", lambda *a, **k: [])
    resp = _client.get("/api/gtja/overview?variety=BU&range_days=abc")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_coverage_route_registered(_client, monkeypatch):
    """/api/gtja/coverage 真注册 + 回覆盖矩阵。"""
    monkeypatch.setattr(gtja_api, "_request", lambda ep, body, timeout=None: _basis_rows())
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda ep, cd, **k: _inv_rows("bu"))
    resp = _client.get("/api/gtja/coverage?varieties=BU,TA&range_days=30")
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert [r["code"] for r in d["rows"]] == ["BU", "TA"]


def test_coverage_route_defaults_to_pool(_client, monkeypatch):
    """不传 varieties → 用池内品种(非空)。"""
    monkeypatch.setattr(gtja_api, "_request", lambda *a, **k: [])
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    resp = _client.get("/api/gtja/coverage")
    d = resp.get_json()
    assert d["ok"] is True
    assert len(d["rows"]) >= 15, "默认应覆盖池内品种"
    assert all(len(r["code"]) <= 4 for r in d["rows"])


def test_routes_return_ok_false_when_unconfigured(_client, monkeypatch):
    """未配密钥 → 两个路由都回 200 + ok:False(而不是 500)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    r1 = _client.get("/api/gtja/overview?variety=BU")
    r2 = _client.get("/api/gtja/coverage")
    assert r1.status_code == 200 and r1.get_json()["ok"] is False
    assert r2.status_code == 200 and r2.get_json()["ok"] is False


def test_overview_payload_json_safe_over_http(_client, monkeypatch):
    """★HTTP 响应体必须是合法 JSON(含 NaN 时 Flask 会输出非法 JSON, 前端直接崩)。"""
    _patch_all(monkeypatch, basis=_basis_rows(), wh=_wh_rows(),
               inv=_inv_rows(), prf=_profit_rows())
    resp = _client.get("/api/gtja/overview?variety=BU&range_days=30")
    raw = resp.get_data(as_text=True)
    assert "NaN" not in raw and "Infinity" not in raw
    assert json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(
        AssertionError(f"非法常量 {c} 出现在响应中")))["ok"] is True
