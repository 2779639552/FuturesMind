"""数据仓库「季节性图」护栏(2026-10-19)。

【需求】用户: "数据仓库中, 走势图部分占据的范围过大, 将其缩小至原尺寸的一半,
  并基于桌面上的 季节性图绘制包 文件, 将其绘制为季节性图表"。

【本文件守什么】
  1. ★ 按年分组的口径: x 轴是**年内天数**而不是日期。分组错了图还是能画出来,
     只是把"2024 与 2026 的同一天"错开, 同比结论全废 —— 纯肉眼很难发现。
  2. ★ 闰年必须对齐: 2024-03-01 与平年 3/1 必须落在同一位置(第 59 天)。
     不对齐的后果是整条曲线从 3 月起错位一格, 且 2/29 与 3/1 重逢。
  3. ★ 多年历史不能被 250 点图表裁剪掐掉: overview 的 series 只留最近 250 点,
     季节性必须显式要全历史(series_cap=0), 否则永远只画得出一年。
  4. ★ 高度减半(340px → 170px)且**只改这一处**: 容器高度写死 320px 的兜底会把它顶回去。
  5. 配色/线宽/图例/最新点标注必须照搬绘制包, 不能自己发挥。
  6. 取数失败必须是 ok:False + error, 不得返回空 years 让前端画成"该指标无数据"。

【网络】完全不触网: 所有 gtja_api 调用点 monkeypatch。
"""

from __future__ import annotations

import datetime as _dt
import io
import pathlib
import re

import pytest

import gtja_module as M

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
APP = ROOT / "web_app.py"


# ---------------------------------------------------------------------------
# 一、纯函数: to_seasonal_years / _doy
# ---------------------------------------------------------------------------
def test_doy_aligns_leap_year_to_non_leap_axis():
    """★ 闰年 3/1 与平年 3/1 必须同为第 59 天(否则逐年对比整体错位一格)。"""
    assert M._doy(_dt.date(2024, 3, 1)) == M._doy(_dt.date(2026, 3, 1)) == 59
    # 闰年 12/31 原始为第 365 天, 对齐后同样是 364(与平年同日同位)
    assert M._doy(_dt.date(2024, 12, 31)) == 364
    assert M._doy(_dt.date(2026, 12, 31)) == 364
    assert M._doy(_dt.date(2026, 1, 1)) == 0


def test_doy_never_exceeds_365():
    """x 轴固定 366 个类目(0~365); 越界点会被 ECharts 丢掉 ⇒ 年末最后一个点凭空消失。"""
    for d in (_dt.date(2024, 2, 29), _dt.date(2025, 12, 31),
              _dt.date(2024, 12, 31), _dt.date(2020, 2, 29)):
        assert 0 <= M._doy(d) <= 365


def test_seasonal_groups_by_year():
    series = [
        {"date": "2024-01-02", "value": 10},
        {"date": "2024-06-01", "value": 20},
        {"date": "2025-01-02", "value": 30},
        {"date": "2026-06-01", "value": 40},
    ]
    out = M.to_seasonal_years(series)
    assert sorted(out["years"]) == ["2024", "2025", "2026"]
    assert out["years"]["2024"] == [[1, 10], [151, 20]]   # 闰年已对齐: 6/1 同为第 151 天
    assert out["years"]["2025"] == [[1, 30]]
    assert out["year_count"] == 3 and out["point_count"] == 4


def test_seasonal_keeps_only_recent_years():
    """最多回看 SEASONAL_YEARS 年 —— 全量库存有 2012 年起的数据, 全画上去挤成一团。"""
    series = [{"date": f"{y}-03-01", "value": y} for y in range(2010, 2027)]
    out = M.to_seasonal_years(series, max_years=5)
    assert sorted(out["years"]) == ["2022", "2023", "2024", "2025", "2026"]


def test_seasonal_latest_and_diff():
    series = [{"date": "2026-10-15", "value": 100}, {"date": "2026-10-16", "value": 133.5}]
    out = M.to_seasonal_years(series)
    assert out["latest"] == {"doy": M._doy(_dt.date(2026, 10, 16)),
                             "val": 133.5, "date": "2026-10-16", "diff": 33.5}
    assert out["first"] == "2026-10-15" and out["last"] == "2026-10-16"


def test_seasonal_same_day_last_wins():
    """接口偶发同日重复行; 后值覆盖前值(与基差"同一天多地区只留一条"同一思路)。"""
    out = M.to_seasonal_years([{"date": "2026-05-06", "value": 1},
                               {"date": "2026-05-06", "value": 2}])
    assert out["years"]["2026"] == [[125, 2]]


def test_seasonal_empty_is_empty_not_zero():
    """★ 空序列必须返回空, 不能补 0 —— 补 0 会在图上画出一条假的地板线。"""
    for bad in ([], [{"date": "", "value": 1}], [{"date": "2026-01-01", "value": None}],
                [{"date": "2026-13-45", "value": 1}]):
        out = M.to_seasonal_years(bad)
        assert out["years"] == {} and out["latest"] is None and out["point_count"] == 0


# ---------------------------------------------------------------------------
# 二、多年历史不能被 250 点裁剪掐掉
# ---------------------------------------------------------------------------
def test_tail_cap_zero_returns_full_history():
    series = [{"date": "2026-01-01", "value": i} for i in range(400)]
    assert len(M._tail(series)) == M.MAX_SERIES_POINTS          # 默认仍裁(页面快照省带宽)
    assert len(M._tail(series, 0)) == 400                        # 季节性要全历史
    assert len(M._tail(series, 10)) == 10


def test_mk_indicator_honours_series_cap():
    d0 = _dt.date(2026, 1, 1)
    series = [{"date": (d0 + _dt.timedelta(days=i)).isoformat(), "value": i}
              for i in range(1, 101)]
    assert len(M._mk_indicator("x.y", "g", "l", series)["series"]) == 100


# ---------------------------------------------------------------------------
# 三、fetch_indicator_seasonal 的取数分派与失败语义
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_gtja(monkeypatch):
    from tradingagents.dataflows import gtja_api

    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    return gtja_api


def test_seasonal_rejects_unknown_id(monkeypatch, fake_gtja):
    M._SEASONAL_CACHE.clear()
    r = M.fetch_indicator_seasonal("TA", "no_such.id")
    assert r["ok"] is False and "未知指标" in r["error"]


def test_seasonal_requires_key(monkeypatch):
    from tradingagents.dataflows import gtja_api

    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    r = M.fetch_indicator_seasonal("TA", "basis.value")
    assert r["ok"] is False and "密钥" in r["error"]


def test_seasonal_empty_id(monkeypatch, fake_gtja):
    r = M.fetch_indicator_seasonal("TA", "  ")
    assert r["ok"] is False and "id" in r["error"]


def test_seasonal_basis_path_uses_chunked_request(monkeypatch, fake_gtja):
    """基差走带参数端点的分片通道, 且**显式要全历史**(series_cap=0)。"""
    seen = {}

    def _fake_src_basis(code, start, end, series_cap=M.MAX_SERIES_POINTS):
        seen["cap"] = series_cap
        seen["start"] = start
        return {"available": True, "indicators": [
            {"id": "basis.value", "group": "基差", "label": "基差", "unit": "元/吨",
             "note": "", "series": [["2024-01-02", 1], ["2026-01-02", 2]]}]}

    monkeypatch.setattr(M, "_src_basis", _fake_src_basis)
    M._SEASONAL_CACHE.clear()
    r = M.fetch_indicator_seasonal("TA", "basis.value", 3)
    assert r["ok"] is True
    assert seen["cap"] == 0, "★ 没要全历史 ⇒ 又被裁到 250 点, 画不出多年同比"
    assert seen["start"].endswith("-01-01")
    assert r["year_count"] == 2


def test_seasonal_missing_indicator_reports_clear_error(monkeypatch, fake_gtja):
    monkeypatch.setattr(M, "_src_warehouse", lambda *a, **k: {
        "available": True, "indicators": [], "note": "该品种此区间无注册仓单记录。"})
    M._SEASONAL_CACHE.clear()
    r = M.fetch_indicator_seasonal("TA", "warehouse.on_warrant")
    assert r["ok"] is False and "无序列" in r["error"]


def test_seasonal_source_failure_is_not_empty_success(monkeypatch, fake_gtja):
    """★ 取数失败不得变成 ok:True + 空 years(前端会画成"该指标没有数据")。"""
    monkeypatch.setattr(M, "_src_basis", lambda *a, **k: {
        "available": False, "error": "GTJAError: 300001", "indicators": []})
    M._SEASONAL_CACHE.clear()
    r = M.fetch_indicator_seasonal("TA", "basis.value")
    assert r["ok"] is False and "300001" in r["error"]


# ---------------------------------------------------------------------------
# 四、模板/路由静态结构
# ---------------------------------------------------------------------------
def _tpl() -> str:
    return io.open(TPL, encoding="utf-8").read()


def test_chart_height_halved():
    """★ 340px → 170px, 且容器上不再出现 340px。

    【2026-10-21】不再写死整段属性顺序：这轮给容器加了 `class="chart-half"`
    （长条图宽度减半），写死 `id="gt-chart" style="..."` 会把"加了个 class"
    误判成"高度改回去了"。改为在 id="gt-chart" 的**标签内**找高度。
    """
    s = _tpl()
    m = re.search(r'<div[^>]*id="gt-chart"[^>]*>', s)
    assert m, "找不到 #gt-chart 容器"
    tag = m.group(0)
    assert "height:170px" in tag, "容器高度应为 170px（原来 340px 的一半）: %s" % tag
    assert "height:340px" not in tag


def test_init_chart_keeps_inline_height():
    """★ 兜底不得写死 320px: 折叠态 clientHeight=0 时会把 170px 顶掉(缩小失效)。"""
    s = _tpl()
    assert "el.style.height = el.style.height || '320px'" in s
    assert "if (el.clientHeight === 0) { el.style.height = '320px'; }" not in s


def test_seasonal_render_wired():
    s = _tpl()
    assert "_drawGtjaSeasonal(c, ind, local)" in s, "本地出图没接上"
    assert "/api/gtja/seasonal?variety=" in s, "没去取多年历史"
    for fn in ("_seasonalFromSeries(series)", "_doyOf(y, mo, da)", "_seasonColor(y)",
               "_drawGtjaSeasonal(c, ind, data)", "_gtSeasonalNote(ind, d, noteEl)"):
        assert f" {fn} {{" in s, f"缺方法 {fn}"


def test_year_colors_match_drawing_package():
    """★ 6 色年份映射必须与 season_colors.py 逐字一致(网页/周报同色是硬要求)。"""
    s = _tpl()
    for hexv in ("#FFC000", "#70449F", "#00B0F0", "#00B050", "#FF0000"):
        assert hexv in s
    m = re.search(r"const YCOL = \{(.+?)\};", s, re.S)
    assert m, "找不到年份色表; 季节性图配色必须集中在一处"
    assert "2021: '#000000'" in m.group(1) and "2024: '#00B0F0'" in m.group(1)


def test_season_black_is_theme_aware():
    """2021 年线是纯黑, 暗色底上不可见 ⇒ 必须走主题令牌, 且登记进重染键。"""
    s = _tpl()
    assert "get seasonBlack()" in s
    assert "'up','down','seasonBlack']" in s


def test_route_registered():
    s = io.open(APP, encoding="utf-8").read()
    assert '@app.route("/api/gtja/seasonal")' in s
    assert "fetch_indicator_seasonal(code, iid, years)" in s
    assert "years = max(1, min(years, 5))" in s, "years 必须夹紧, 否则可拉十年把上游拖垮"


def test_subtitle_renamed():
    assert "季节性图（按年同比）" in _tpl()

def test_chart_seq_bumped_before_any_early_return():
    """★ 在途请求防串的序号必须**在空态 return 之前**自增。

    否则: 点 A(有数据, 发请求) → 点 B(无序列点, 提前 return 不自增) → A 的响应回来时
    seq 仍相等 ⇒ 把 A 的曲线画到 B 的空态画布上(用户看到"空指标却有图")。
    """
    s = _tpl()
    i0 = s.find(" _renderGtjaChart(ind) {")
    assert i0 > 0
    body = s[i0:i0 + 4000]
    i_seq = body.find("this._gtState.chartSeq =")
    i_empty = body.find("if (!series.length) {")
    assert i_seq > 0 and i_empty > 0
    assert i_seq < i_empty, "chartSeq 自增被放到了空态 return 之后 ⇒ 旧请求会污染新图"
