"""宏观数据修复(2026-09-03)测试:AKShare 宏观接口倒序规范化 + 指标扩展(11 节) +
磁盘 last-good 回退 + 进程内新鲜度缓存。

覆盖对象: tradingagents/dataflows/commodity_futures.py 的
_macro_date_col / _macro_date_key / _macro_sort_ascending / _macro_float /
_macro_cache_* / _macro_fallback_lines / get_futures_macro;以及 web_app.py 的
_MACRO_VALUE_LABELS 取值(新指标节能取到值)。

全隔离: fake akshare(types.ModuleType)塞进 sys.modules,不打真实接口;
缓存目录 monkeypatch 到 tmp_path,不碰真实 ~/.tradingagents。
"""

import datetime
import json
import sys
import types

import pandas as pd
import pytest

import tradingagents.dataflows.commodity_futures as cf
import web_app

# ---------------------------------------------------------------------------
# 合成 DataFrame(代表 AKShare 各宏观接口真实列名;除 LPR/社融外均【最新在前】)
# ---------------------------------------------------------------------------


def _df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


def _pmi_df():  # 最新在前倒序:2026-08 49.8 … 2008-01 53.0
    return _df(
        [
            ("2026年08月份", 49.8, 50.6),
            ("2026年07月份", 49.9, 50.3),
            ("2026年06月份", 50.1, 50.8),
            ("2026年05月份", 50.2, 50.9),
            ("2008年01月份", 53.0, 56.0),
        ],
        ["月份", "制造业-指数", "非制造业-指数"],
    )


def _gdp_df():  # 最新在前:2026 H1 4.7 … 2006 Q1 12.4
    return _df(
        [
            ("2026年第1-2季度", 120000, 4.7, 3.5, 5.0, 4.2),
            ("2026年第1季度", 110000, 5.3, 3.5, 6.1, 5.0),
            ("2025年第4季度", 100000, 5.4, 3.6, 5.7, 5.6),
            ("2025年第1-3季度", 90000, 5.2, 3.4, 5.5, 5.1),
            ("2006年第1季度", 50000, 12.4, 5.0, 13.0, 12.0),
        ],
        ["季度", "国内生产总值-绝对值", "国内生产总值-同比增长",
         "第一产业-同比增长", "第二产业-同比增长", "第三产业-同比增长"],
    )


def _fai_df():  # 固定资产投资,最新在前
    return _df(
        [
            ("2026年07月份", 3000, 3.6, 70000),
            ("2026年06月份", 2500, 3.9, 60000),
            ("2026年05月份", 2000, 4.0, 50000),
            ("2012年02月份", 1200, 5.0, 10000),
        ],
        ["月份", "当月", "同比增长", "累计值"],
    )


def _re_df():  # 房地产景气指数,最新在前
    return _df(
        [
            ("2026-08-01", 100.2, 0.1),
            ("2026-07-01", 100.1, 0.2),
            ("2026-06-01", 99.9, -0.1),
            ("2012-02-01", 95.0, -0.5),
        ],
        ["日期", "指数值", "涨跌幅"],
    )


def _gy_df():  # 工业增加值,最新在前
    return _df(
        [
            ("2026年07月份", 5.1, 5.0),
            ("2026年06月份", 5.4, 5.0),
            ("2026年05月份", 5.6, 5.0),
            ("2008年01月份", 15.0, 15.0),
        ],
        ["月份", "同比增长", "累计增长"],
    )


def _ci_df():  # 建筑业指数(日度)
    return _df(
        [
            ("2026-08-28", 111.5),
            ("2026-08-27", 111.3),
            ("2026-08-26", 111.0),
        ],
        ["日期", "指数值"],
    )


def _cpi_df():  # 最新在前
    return _df(
        [
            ("2026年07月份", 100.6, 0.5, -0.2),
            ("2026年06月份", 100.5, 0.4, 0.0),
            ("2026年05月份", 100.6, 0.3, 0.1),
        ],
        ["月份", "全国-当月", "全国-同比增长", "全国-环比增长"],
    )


def _ppi_df():  # 最新在前
    return _df(
        [
            ("2026年07月份", 100.0, -1.4, -1.5),
            ("2026年06月份", 100.1, -1.2, -1.4),
            ("2026年05月份", 100.2, -1.0, -1.2),
        ],
        ["月份", "当月", "当月同比增长", "累计"],
    )


def _ms_df():  # 货币供应,最新在前
    return _df(
        [
            ("2026年07月份", 6.3, -0.3, 10.0),
            ("2026年06月份", 6.2, -0.5, 11.0),
            ("2026年05月份", 6.1, -1.0, 12.0),
        ],
        ["月份", "货币和准货币(M2)-同比增长", "货币(M1)-同比增长", "流通中的现金(M0)-同比增长"],
    )


def _lpr_df():  # LPR 本就是升序(真实如此)
    return _df(
        [
            ("2026-06-20", 3.45, 3.95),
            ("2026-07-20", 3.45, 3.85),
            ("2026-08-20", 3.35, 3.85),
        ],
        ["TRADE_DATE", "LPR1Y", "LPR5Y"],
    )


def _shr_df():  # 社会融资增量(月份为 YYYYMM 数字),最新在前喂入测排序
    return _df(
        [
            (201504, 22000, 15000),
            (201503, 21000, 14000),
            (201502, 16000, 12000),
            (201501, 20500, 13000),
        ],
        ["月份", "社会融资规模增量", "其中-人民币贷款"],
    )


def _fake_ak(overrides=None):
    """构造假的 akshare 模块:11 个宏观接口各返回上表。overrides 覆盖个别接口。"""
    ak = types.ModuleType("akshare")
    ak.macro_china_gdp = lambda: _gdp_df()
    ak.macro_china_pmi = lambda: _pmi_df()
    ak.macro_china_gdzctz = lambda: _fai_df()
    ak.macro_china_real_estate = lambda: _re_df()
    ak.macro_china_gyzjz = lambda: _gy_df()
    ak.macro_china_construction_index = lambda: _ci_df()
    ak.macro_china_cpi = lambda: _cpi_df()
    ak.macro_china_ppi = lambda: _ppi_df()
    ak.macro_china_money_supply = lambda: _ms_df()
    ak.macro_china_lpr = lambda: _lpr_df()
    ak.macro_china_shrzgm = lambda: _shr_df()
    for name, fn in (overrides or {}).items():
        setattr(ak, name, fn)
    return ak


@pytest.fixture(autouse=True)
def _clear_macro_mem_cache():
    """每个用例前后清空进程内宏观新鲜度缓存(避免用例间串扰)。"""
    cf._macro_mem_cache.clear()
    yield
    cf._macro_mem_cache.clear()


def _use_fake_ak(monkeypatch, overrides=None):
    fake = _fake_ak(overrides)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


def _use_tmp_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "_MACRO_CACHE_DIR", tmp_path)


# ---------------------------------------------------------------------------
# 1) 排序规范化 + 日期解析(纯,快,不联网)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMacroSortNormalization:
    def test_descending_pmi_becomes_ascending_and_latest_is_row_tail(self):
        df = cf._macro_sort_ascending(_pmi_df())
        assert df.iloc[-1]["月份"] == "2026年08月份"
        assert df.iloc[0]["月份"] == "2008年01月份"
        assert float(df.iloc[-1]["制造业-指数"]) == 49.8

    def test_gdp_uses_quarter_column_and_oldest_never_selected(self):
        assert cf._macro_date_col(_gdp_df()) == "季度"  # 修旧实现写死 '日期' → N/A
        df = cf._macro_sort_ascending(_gdp_df())
        latest = df.iloc[-1]
        assert latest["季度"] == "2026年第1-2季度"
        assert float(latest["国内生产总值-同比增长"]) == 4.7
        # 近 4 期趋势不含 2006 年 12.4
        assert "12.4" not in [str(x) for x in df.tail(4)["国内生产总值-同比增长"]]

    def test_lpr_already_ascending_keeps_order(self):
        df = cf._macro_sort_ascending(_lpr_df())
        assert df.iloc[-1]["TRADE_DATE"] == "2026-08-20"
        assert float(df.iloc[-1]["LPR1Y"]) == 3.35

    def test_mixed_yyyymm_numeric_month_parses(self):
        # 社融 月份 是 YYYYMM 数字:倒序喂入 → 升序后最新 = 201504
        df = cf._macro_sort_ascending(_shr_df())
        assert int(df.iloc[-1]["月份"]) == 201504
        assert int(df.iloc[0]["月份"]) == 201501
        assert int(df.iloc[-1]["社会融资规模增量"]) == 22000

    def test_date_col_falls_back_to_first_column(self):
        df = pd.DataFrame([(1, 2)], columns=["A", "B"])
        assert cf._macro_date_col(df) == "A"

    def test_unparseable_rows_leave_frame_unchanged(self):
        df = pd.DataFrame(
            [("未知", 50), ("未知", 49)], columns=["月份", "制造业-指数"]
        )
        out = cf._macro_sort_ascending(df)
        assert list(out["月份"]) == ["未知", "未知"]
        assert list(out["制造业-指数"]) == [50, 49]


# ---------------------------------------------------------------------------
# 2) get_futures_macro 全成功:当期值、11 节、剪刀差、磁盘 last-good 落盘
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFuturesMacroSuccess:
    def test_outputs_current_values_across_11_sections(self, tmp_path, monkeypatch):
        _use_fake_ak(monkeypatch)
        _use_tmp_cache_dir(monkeypatch, tmp_path)
        out = cf.get_futures_macro()

        # 陈旧值绝不再出现(修复目标)
        assert "2008" not in out and "2006" not in out and "12.4" not in out
        assert "2012" not in out and "UNAVAILABLE" not in out

        # 当期核心值
        assert "49.8" in out  # PMI 2026-08
        assert "2026年第1-2季度" in out and "4.7" in out  # GDP
        assert "2026年07月份" in out  # FAI 最新月
        assert "0.5" in out  # CPI 同比
        assert "-1.4" in out  # PPI 同比
        assert "6.3" in out  # M2 同比
        assert "3.35" in out and "较上期" in out  # LPR
        assert "2015年04月" in out and "22000" in out  # 社融展示格式化
        assert "111.5" in out  # 建筑业日度

        # 剪刀差解读
        assert "-1.9" in out  # PPI-CPI = -1.4 - 0.5
        assert "-6.6" in out  # M1-M2 = -0.3 - 6.3

        # 11 节齐全
        for name in ["GDP", "PMI", "固定资产投资", "房地产景气指数", "工业增加值",
                     "建筑业指数", "CPI", "PPI", "货币供应", "LPR", "社会融资规模增量"]:
            assert f"## {name}" in out

    def test_disk_last_good_written_with_all_sections(self, tmp_path, monkeypatch):
        _use_fake_ak(monkeypatch)
        _use_tmp_cache_dir(monkeypatch, tmp_path)
        cf.get_futures_macro()
        path = tmp_path / "macro_last_good.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        sections = data["sections"]
        assert len(sections) == 11
        assert sections["CPI"]["lines"] and sections["CPI"]["date"] == datetime.date.today().isoformat()
        # 落盘的正文行是展示行(不含 '## ' 头,含两空格缩进)
        assert any("CPI 同比: 0.5%" in ln for ln in sections["CPI"]["lines"])


# ---------------------------------------------------------------------------
# 3) 单接口失败 → 回退 last-good 快照(标注日期),不输出 UNAVAILABLE
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMacroLastGoodFallback:
    def test_single_endpoint_failure_falls_back_to_snapshot(self, tmp_path, monkeypatch):
        _use_fake_ak(monkeypatch)
        _use_tmp_cache_dir(monkeypatch, tmp_path)

        def _boom():
            raise RuntimeError("boom: 接口瞬时不可用")

        # 第一次全成功 → 写盘
        out1 = cf.get_futures_macro()
        assert "UNAVAILABLE" not in out1 and "0.5" in out1

        # 第二次让 CPI 接口失败(其余照常)→ 应出现带快照日期的回退节
        cf._macro_mem_cache.clear()
        _use_fake_ak(monkeypatch, overrides={"macro_china_cpi": _boom})
        out2 = cf.get_futures_macro()
        assert "快照" in out2 and "接口暂不可用" in out2
        assert "UNAVAILABLE" not in out2
        assert "CPI 同比: 0.5%" in out2  # 旧正文行仍在
        # 其它成功接口照常出新值
        assert "49.8" in out2

    def test_failure_with_no_snapshot_yields_unavailable(self, tmp_path, monkeypatch):
        # 无缓存、又失败 → 干净地标 UNAVAILABLE(不编造数值)
        def _boom():
            raise RuntimeError("boom")

        _use_fake_ak(monkeypatch, overrides={"macro_china_pmi": _boom})
        _use_tmp_cache_dir(monkeypatch, tmp_path)
        out = cf.get_futures_macro()
        assert "## PMI: UNAVAILABLE (boom)" in out
        # 其余接口仍成功
        assert "## CPI" in out and "0.5" in out


# ---------------------------------------------------------------------------
# 4) 进程内新鲜度缓存:重复调用不重打 11 个免费接口
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMacroFreshnessCache:
    class _Counter:
        def __init__(self, fn):
            self.fn, self.n = fn, 0

        def __call__(self):
            self.n += 1
            return self.fn()

    def test_second_call_served_from_memory(self, tmp_path, monkeypatch):
        _use_tmp_cache_dir(monkeypatch, tmp_path)
        fake = _fake_ak()
        fake.macro_china_pmi = self._Counter(_pmi_df)
        fake.macro_china_cpi = self._Counter(_cpi_df)
        monkeypatch.setitem(sys.modules, "akshare", fake)

        out1 = cf.get_futures_macro()
        n_after_first = (fake.macro_china_pmi.n, fake.macro_china_cpi.n)
        assert n_after_first == (1, 1)

        out2 = cf.get_futures_macro()  # 命中进程内缓存
        assert out2 == out1
        assert (fake.macro_china_pmi.n, fake.macro_china_cpi.n) == (1, 1)


# ---------------------------------------------------------------------------
# 5) web_app 取值:新指标节能被 _parse_macro_text 提为 item
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebappMacroValueLabels:
    def test_new_sections_pick_values(self):
        text = (
            "# head\n"
            "## CPI (居民消费价格指数)\n"
            "  CPI 同比: 0.5%\n"
            "## PPI (工业生产者出厂价格指数)\n"
            "  PPI 同比: -1.4%\n"
            "## 货币供应 (M2/M1/M0 月度)\n"
            "  M2 同比: 6.3%\n"
            "## LPR (贷款市场报价利率)\n"
            "  LPR1Y: 3.35% (较上期 -0.10)\n"
            "## 社会融资规模增量 (月度)\n"
            "  社会融资规模增量: 22000 亿元\n"
        )
        items, _ = web_app._parse_macro_text(text)
        m = {it["name"]: it["value"] for it in items}
        assert m["CPI"] == "0.5%"
        assert m["PPI"] == "-1.4%"
        assert m["货币供应"] == "6.3%"
        assert m["LPR"] == "3.35% (较上期 -0.10)"
        assert m["社会融资规模增量"] == "22000 亿元"

    def test_snapshot_header_parsed_as_available_with_values(self):
        # 回退头 "## CPI (快照 2026-09-03;接口暂不可用)" 仍按可取解析(值来自快照),
        # 节名 "CPI" 从括号前剥离,不被误判为 UNAVAILABLE
        text = "## CPI (快照 2026-09-03;接口暂不可用)\n  CPI 同比: 0.5%\n"
        items, _ = web_app._parse_macro_text(text)
        assert items[0]["name"] == "CPI"
        assert items[0]["value"] == "0.5%"
