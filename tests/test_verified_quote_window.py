"""`get_verified_quote` 指标窗口护栏（2026-10-23）。

【事故】LC 技术报告里出现"口径冲突"：`get_verified_quote` 给 SMA20 = 110,397.65，
而 `get_futures_indicators` / 方法论注入块给 113,379。模型判定"以 113,379 为准，
110,397.65 视为可疑数据缺口"。

**实测（逐日对比两个数据源）：19 个交易日收盘价完全相同，差值全部为 0。**
⇒ 数据源没有问题、复权也没有问题，差异**纯粹来自"取哪 20 根"**。

【根因】
`get_verified_quote` 拉的是 [目标日-30天, 目标日+5天] 的窗口，然后
    all_closes = 「整个窗口所有收盘价」
    sma20 = all_closes[-20:] 的算术平均
两个毛病：
  1. **会看到未来**：窗口含目标日之后 5 天，数据源若已填上那些日期，
     `[-20:]` 就是"最新 20 根"而不是"截至目标日的 20 根"；
  2. **分母可能不足 20**：35 天窗口约 22 个交易日，遇上长假只有 19 根，
     却照样按 19 根平均（不做 `len==20` 校验）。

【本文件守什么】
  1. 目标日之后的价格绝不能进 SMA / 日涨跌幅；
  2. 拉取窗口必须够宽（≥60 天），保证 ≥20 个交易日；
  3. 截断后不足 20 根时，取值仍要是"已有全部分"（不允许静默丢最后几根）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from tradingagents.dataflows import commodity_futures as cf


def _workdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _csv(rows: list[tuple[date, float]]) -> str:
    head = "# 日期,开盘价,最高价,最低价,收盘价,成交量,持仓量"
    body = []
    for d, c in rows:
        body.append("%s,%.1f,%.1f,%.1f,%.1f,1000,2000" % (
            d.isoformat(), c, c, c, c))
    return head + "\n" + "\n".join(body)


def _sma20_of(text: str) -> float:
    m = re.search(r"SMA\(20\):\s*([\d.]+)", text)
    assert m, f"输出里没有 SMA(20): {text[:300]}"
    return float(m.group(1))


@pytest.fixture
def fake_price(monkeypatch):
    """返回一个可控的价格桩，并记录它收到的窗口参数。"""
    box: dict = {"rows": [], "start": None, "end": None}

    def _fake(symbol, start_date="", end_date=""):
        box["start"], box["end"] = start_date, end_date
        return _csv(box["rows"])

    monkeypatch.setattr(cf, "get_futures_price", _fake)
    return box


def test_future_prices_do_not_leak_into_sma(fake_price):
    """目标日之后的价格若进均线，等于用未来数据算指标。"""
    days = _workdays(date(2026, 9, 1), 50)
    target = days[29]                      # 第 30 个交易日为目标日
    rows = [(d, 100000.0 + i * 1000) for i, d in enumerate(days)]
    # 目标日之后的价格异常抬高，一旦被算进均线会非常显眼
    rows = [(d, 999999.0 if d > target else c) for d, c in rows]
    fake_price["rows"] = rows

    out = cf.get_verified_quote("RB", target.isoformat())
    sma = _sma20_of(out)

    upto = [c for d, c in rows if d <= target][-20:]
    expect = sum(upto) / len(upto)
    assert sma == pytest.approx(expect, abs=0.01), (
        f"SMA20 用了目标日之后的数据：得到 {sma:.2f}，按目标日截断应为 {expect:.2f}"
    )
    assert sma < 200000, "明显把 999999 的未来价算了进去"


def test_fetch_window_is_wide_enough(fake_price):
    """窗口必须远宽于 30 天：35 天窗口遇长假不足 20 个交易日。"""
    days = _workdays(date(2026, 9, 1), 40)
    fake_price["rows"] = [(d, 100000.0) for d in days]
    target = days[29]
    cf.get_verified_quote("RB", target.isoformat())

    start = date.fromisoformat(fake_price["start"])
    assert (target - start).days >= 60, (
        f"拉取窗口只有 {(target - start).days} 天，不足以保证 20 个交易日"
    )


def test_short_history_degrades_to_available(fake_price):
    """只有 10 根时用 10 根的平均（不报错、不补 0）。"""
    days = _workdays(date(2026, 9, 1), 10)
    fake_price["rows"] = [(d, 50000.0 + i) for i, d in enumerate(days)]
    out = cf.get_verified_quote("RB", days[-1].isoformat())
    assert _sma20_of(out) == pytest.approx(50004.5, abs=0.01)


def test_day_change_uses_previous_trading_day(fake_price):
    """日涨跌幅的"前值"必须是目标日前一根，不是全表倒数第二根。"""
    days = _workdays(date(2026, 9, 1), 30)
    target = days[20]
    rows = [(d, 100000.0 + i * 100) for i, d in enumerate(days)]
    # 目标日之后放一个极端价：若 prev_close 取错，涨跌幅会离谱
    rows = [(d, 1.0 if d > target else c) for d, c in rows]
    fake_price["rows"] = rows

    out = cf.get_verified_quote("RB", target.isoformat())
    m = re.search(r"Change:\s*([+-]?[\d.]+)%", out, re.I)
    if m is None:
        m = re.search(r"日涨跌[^\d+-]*([+-]?[\d.]+)%", out)
    assert m, f"输出里找不到日涨跌幅: {out[:400]}"
    chg = float(m.group(1))
    # 正常应约 +0.1%（每天 +100，基数约 10 万）
    assert abs(chg) < 5, f"日涨跌幅 {chg}% 明显用了错误的前值（目标日之后的数据混入）"
