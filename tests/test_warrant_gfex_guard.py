# -*- coding: utf-8 -*-
"""广期所（GFEX）仓单采集的「假成功空数据」护栏测试。

【为什么】2026-09-17 实测: 广期所日报在盘后发布前, 接口照样返回
    {"code":"0","msg":"接口访问成功！","data":[{"variety":"总计",
     "lastWbillQty":null,"wbillQty":null,"diff":null}]}
—— HTTP 200 + code=0 + data 非空, 是标准的假成功空数据。旧版只判
`code=="0" and data` ⇒ 把空壳当有效数据落盘, 广期所当日变 0 个品种
（前端「四所全部品种」实际只有三所），且日志里没有任何异常。

两道护栏:
  ① `has_real_data` 下探到"排除总计行后还有没有带数量的明细行"
  ② `collect(date=...)` 当日无数据时**回退到最近有效交易日**, 而不是抛异常丢整所
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from warrant import gfex  # noqa: E402

TOTAL_ONLY = {
    "code": "0", "msg": "接口访问成功！",
    "data": [{"variety": "总计", "varietyOrder": "", "whCodeOrder": "",
              "whAbbr": "", "whType": "", "trademarkName": "",
              "lastWbillQty": None, "wbillQty": None, "diff": None}],
}

REAL = {
    "code": "0", "msg": "接口访问成功！",
    "data": [
        {"variety": "总计", "varietyOrder": "", "whCodeOrder": "", "whAbbr": "",
         "whType": "", "trademarkName": "", "lastWbillQty": 45515,
         "wbillQty": 43583, "diff": -1932},
        {"variety": "碳酸锂小计", "varietyOrder": "lc", "whCodeOrder": "wh_code_order",
         "whAbbr": "", "whType": "", "trademarkName": "", "lastWbillQty": 45515,
         "wbillQty": 43583, "diff": -1932},
        {"variety": "碳酸锂", "varietyOrder": "lc", "whCodeOrder": "WH001",
         "whAbbr": "某仓库", "whType": "1", "trademarkName": "某品牌",
         "lastWbillQty": 45515, "wbillQty": 43583, "diff": -1932},
    ],
}

# 有明细行但数量全 null（另一种空壳: 品种行发布了、数量还没出）
NULL_QTY_ROWS = {
    "code": "0", "msg": "接口访问成功！",
    "data": [
        {"variety": "总计", "varietyOrder": "", "whCodeOrder": "", "whAbbr": "",
         "whType": "", "trademarkName": "", "lastWbillQty": None,
         "wbillQty": None, "diff": None},
        {"variety": "碳酸锂小计", "varietyOrder": "lc", "whCodeOrder": "wh_code_order",
         "whAbbr": "", "whType": "", "trademarkName": "", "lastWbillQty": None,
         "wbillQty": None, "diff": None},
    ],
}


# ───────────────────────── has_real_data ─────────────────────────

def test_total_only_is_not_real_data():
    """只有「总计」占位行 → 必须判为无数据。"""
    assert gfex.has_real_data(TOTAL_ONLY) is False


def test_real_rows_pass():
    assert gfex.has_real_data(REAL) is True


def test_null_qty_rows_are_not_real_data():
    """品种行在但数量全 null → 仍是空壳。"""
    assert gfex.has_real_data(NULL_QTY_ROWS) is False


def test_bad_code_is_not_real_data():
    js = dict(REAL)
    js["code"] = "1"
    assert gfex.has_real_data(js) is False


def test_empty_and_none_are_not_real_data():
    assert gfex.has_real_data(None) is False
    assert gfex.has_real_data({}) is False
    assert gfex.has_real_data({"code": "0", "data": []}) is False


def test_variety_whitespace_total_still_recognized():
    """「 总计 」带空格也算占位行(接口偶发前导空格)。"""
    js = {"code": "0", "data": [{"variety": " 总计 ", "wbillQty": None}]}
    assert gfex.has_real_data(js) is False


# ───────────────────────── fetch ─────────────────────────

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


def test_fetch_returns_none_on_fake_success(monkeypatch):
    """fetch 层就要把假成功空数据挡掉(否则 latest_date 会认为当天有数据)。"""
    monkeypatch.setattr(gfex, "post", lambda *a, **k: _Resp(TOTAL_ONLY))
    assert gfex.fetch(dt.date(2026, 9, 17)) is None


def test_fetch_returns_payload_on_real_data(monkeypatch):
    monkeypatch.setattr(gfex, "post", lambda *a, **k: _Resp(REAL))
    js = gfex.fetch(dt.date(2026, 9, 17))
    assert js is not None and js["code"] == "0"


def test_fetch_non_200_returns_none(monkeypatch):
    class R:
        status_code = 520

    monkeypatch.setattr(gfex, "post", lambda *a, **k: R())
    assert gfex.fetch(dt.date(2026, 9, 17)) is None


# ───────────────────────── collect 回退 ─────────────────────────

def _silence(monkeypatch):
    monkeypatch.setattr(gfex, "save_raw", lambda *a, **k: "x")


def test_collect_falls_back_to_last_valid_day(monkeypatch):
    """指定交易日无数据(日报未发布) → 回退到最近有效交易日, 而不是整所丢失。"""
    _silence(monkeypatch)
    monkeypatch.setattr(gfex, "fetch", lambda d: None)

    def fake_latest(start=None, lookback=12):
        assert start == dt.date(2026, 9, 16), f"应从指定日的前一天往前找, 实际 {start}"
        return dt.date(2026, 9, 15), REAL

    monkeypatch.setattr(gfex, "latest_date", fake_latest)
    res = gfex.collect(date=dt.date(2026, 9, 17))
    assert res["date"] == "2026-09-15", res["date"]
    assert len(res["product"]) == 1        # 「碳酸锂小计」行


def test_collect_raises_when_no_valid_day_within_window(monkeypatch):
    _silence(monkeypatch)
    monkeypatch.setattr(gfex, "fetch", lambda d: None)

    def boom(start=None, lookback=12):
        raise RuntimeError("GFEX: 最近 %d 天内未找到仓单日报数据" % lookback)

    monkeypatch.setattr(gfex, "latest_date", boom)
    with pytest.raises(RuntimeError, match="均无数据"):
        gfex.collect(date=dt.date(2026, 9, 17))


def test_collect_uses_payload_when_day_has_data(monkeypatch):
    """当日有真实数据时不得走回退(反例: 别把正常路径也改成回退)。"""
    _silence(monkeypatch)
    monkeypatch.setattr(gfex, "fetch", lambda d: REAL)

    def must_not_call(*a, **k):
        raise AssertionError("当日有数据时不该调用 latest_date 回退")

    monkeypatch.setattr(gfex, "latest_date", must_not_call)
    res = gfex.collect(date=dt.date(2026, 9, 17))
    assert res["date"] == "2026-09-17"


# ───────────────────────── parse ─────────────────────────

def test_parse_splits_total_product_detail():
    """「总计」进 summary、「X小计」进 product、其余进 detail(不能重复计数)。"""
    detail, total, product = gfex.parse(REAL, dt.date(2026, 9, 17))
    assert len(total) == 1
    assert len(product) == 1 and product[0]["variety"] == "碳酸锂"
    assert len(detail) == 1 and detail[0]["warehouse_name"] == "某仓库"


def test_parse_total_only_yields_empty_product():
    detail, total, product = gfex.parse(TOTAL_ONLY, dt.date(2026, 9, 17))
    assert product == [] and detail == []
